"""Starting-goalie status from two free public pages, read politely, with the status semantics kept exact.

PRIMARY    GoaliePost (https://goaliepost.com/): the server-rendered page carries each game's predicted goalies as data: NHL game id, NHL
           player id, a label (CONFIRMED / LIKELY / PROBABLE), the cited reporter and link, the supporting text and the entry's own update time.
SECONDARY  GameDayTweets (https://www.gamedaytweets.com/goalies): plain HTML of reporter posts per matchup. Only DIRECT start language counts
           ("will start", "is starting", "in the starter's net", "confirmed starter"); hedged posts ("will see how", "projecting") and the site's
           "Our Guess" are never evidence of a start.

Exact states (never blurred): CONFIRMED, PROBABLE (GoaliePost's LIKELY and PROBABLE; the raw label is kept), UNKNOWN, and CONFLICT (two sources
name different goalies and the newer one cannot be shown to supersede). An inferred or projected starter is never CONFIRMED. Source update time
(the source's own clock) is stored separately from the engine's retrieval time; GameDayTweets gives a date only, so it is never treated as newer
than a same-day GoaliePost entry.

Access: ordinary HTTP GETs of the two public pages, no login, no headless browser, no private endpoint; a descriptive User-Agent; at most one
fetch per page every 20 minutes (10 when a game is within three hours); robots.txt of both sites permits it and neither publishes a term
against automated reading, but neither grants permission either, so under docs/SOURCE_POLICY.md the feed is OFF by default and makes no request until the owner sets NHL_ENGINE_GOALIE_SOURCES=ON.
A test run never reaches either site. This module never writes goalie_status_events, so it cannot open the saves gate by itself.
"""
from __future__ import annotations

import datetime as dt
import html as htmllib
import json
import os
import re
from typing import Callable

from operational import state_paths

GP_URL = "https://goaliepost.com/"
GDT_URL = "https://www.gamedaytweets.com/goalies"
USER_AGENT = "nhl-engine-paper-research/1.0 (personal, non-commercial research; low volume; contact via repository owner)"
SWITCH_ENV = "NHL_ENGINE_GOALIE_SOURCES"
STATE_NAME = "goalie_feed_state.json"
HISTORY_NAME = "goalie_feed_history.jsonl"
MIN_INTERVAL_MIN = 20.0
NEAR_INTERVAL_MIN = 10.0                 # a game starts within NEAR_WINDOW_H
NEAR_WINDOW_H = 3.0
FEED_MAX_AGE_MIN = 90.0                  # automated statuses count only while the last good fetch is this recent
CONFIRMED_MAX_AGE_H = 30.0               # an explicit confirmation older than this is stale
CURRENT_H, RECENT_H = 24.0, 48.0         # source-age bands for a PROBABLE entry
FUTURE_TOLERANCE_MIN = 5.0

CONFIRMED, PROBABLE, UNKNOWN, CONFLICT = "CONFIRMED", "PROBABLE", "UNKNOWN", "CONFLICT"
CURRENT, RECENT, STALE, NO_TIME = "CURRENT", "RECENT", "STALE", "UNKNOWN"
LABELS = {"CONFIRMED": CONFIRMED, "LIKELY": PROBABLE, "PROBABLE": PROBABLE}

# GameDayTweets logo file stems that are not the NHL abbreviation
GDT_ALIASES = {"nj": "NJD", "tb": "TBL", "sj": "SJS", "la": "LAK", "ut": "UTA", "utah": "UTA"}

DIRECT = re.compile(r"(will\s+start|is\s+starting|are\s+starting|starts\s+tonight|start\s+tonight|starting\s+tonight|starter[’']?s?\s+(?:net|crease)|in\s+the\s+starting\s+net|"
                    r"co?n?firmed\s+starter|confirm(?:s|ed)?\s+(?:that\s+)?\w+\s+(?:will\s+)?start|gets\s+the\s+start|getting\s+the\s+start|will\s+be\s+in\s+net|is\s+in\s+net)", re.I)
HEDGE = re.compile(r"(will\s+see|see\s+how|make\s+a\s+decision|decision|projecting|projected|expect|likely|should|could|might|\bmay\b|\bif\b|game-?time|gtd|wait|not\s+sure|probabl|warmups?|unsure|undetermined)", re.I)
AS_EXPECTED = re.compile(r",?\s*as\s+expected", re.I)


# ------------------------------------------------------------------ small helpers ----

def enabled() -> bool:
    """OFF unless the owner opted in (NHL_ENGINE_GOALIE_SOURCES=ON): public + robots-allowed + silent terms is AMBIGUOUS under the project's source rule (docs/SOURCE_POLICY.md), and ambiguous is OFF."""
    return os.environ.get(SWITCH_ENV, "OFF").strip().upper() == "ON"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str | None) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _epoch(sec) -> str | None:
    try:
        return _iso(dt.datetime.fromtimestamp(int(sec), dt.timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _age_min(stamp: str | None, now: dt.datetime) -> float | None:
    t = _parse(stamp)
    return None if t is None else (now - t).total_seconds() / 60.0


def norm(name: str) -> str:
    return re.sub(r"[^a-z ]", "", re.sub(r"\s+", " ", (name or "").lower().replace("’", "'"))).strip()


def surname(name: str) -> str:
    parts = norm(name).split(" ")
    return parts[-1] if parts and parts[-1] else ""


# ------------------------------------------------------------------ GoaliePost (pure parsing) ----

_GAME_START = re.compile(r'\{"id":"[0-9a-f]{24}","externalId":(\d{10}),"sportsNetGameId"')


def _flight_text(page: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', page)
    out = []
    for c in chunks:
        try:
            out.append(json.loads('"' + c + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def parse_goaliepost(page: str) -> dict:
    """{games: [...]} from the page's own server-rendered data. Raises ValueError when the shape is not what this parser knows (the caller keeps the last good data)."""
    blob = _flight_text(page)
    dec = json.JSONDecoder()
    games, seen = [], set()
    for m in _GAME_START.finditer(blob):
        if m.group(1) in seen:
            continue
        try:
            g, _ = dec.raw_decode(blob, m.start())
        except json.JSONDecodeError:
            continue
        seen.add(m.group(1))
        pg = g.get("predictedGoalies")
        teams = g.get("teams") or {}
        if not isinstance(pg, dict) or "HOME" not in teams or "AWAY" not in teams:
            continue
        sides = {}
        for side in ("HOME", "AWAY"):
            ents = []
            for e in pg.get(side) or []:
                gl = e.get("goalie") or {}
                link = (e.get("links") or [{}])[0] if e.get("links") else {}
                ents.append({"player_id": str(gl.get("externalId") or ""), "name": gl.get("fullName") or "", "label": str(e.get("likeliness") or "").upper(),
                             "comment": e.get("comments") or "", "source_name": link.get("text") or "", "source_url": link.get("link") or "",
                             "source_at_utc": _epoch(e.get("timestamp"))})
            sides[side] = ents
        games.append({"game_id": int(g["externalId"]), "start_utc": _epoch(g.get("gameEpochDate")), "state": (g.get("status") or {}).get("abstractGameState"),
                      "home": teams["HOME"]["team"]["abbreviation"], "away": teams["AWAY"]["team"]["abbreviation"], "updated_at_utc": _epoch(g.get("updatedAt")), "sides": sides})
    if not games:
        raise ValueError("no GoaliePost game data found in the page")
    return {"games": games}


# ------------------------------------------------------------------ GameDayTweets (pure parsing) ----

def parse_gamedaytweets(page: str) -> dict:
    """{date, matchups: [{away, home, away_tweets, home_tweets}]}. Each tweet: handle, text, url, tweet_date. 'Our Guess' is discarded here."""
    m = re.search(r'/goalies\?date=(\d{4}-\d\d-\d\d)">Prev', page)
    prev = _parse(m.group(1) + "T00:00:00Z") if m else None
    page_date = (prev + dt.timedelta(days=1)).strftime("%Y-%m-%d") if prev else None
    blocks = re.split(r'<h1 class="text-3xl', page)[1:]
    matchups = []
    for b in blocks:
        logos = re.findall(r'/assets/logos/([a-z]+)-[0-9a-f]{20,}\.svg', b)
        if len(logos) < 2:
            continue
        away, home = (GDT_ALIASES.get(x, x.upper()) for x in logos[:2])
        body = b.split('<div class="invisible">', 1)
        left, right = (body[0], "") if len(body) == 1 else (body[0].split("</h1>", 1)[-1], body[1])
        out = {"away": away, "home": home, "away_tweets": [], "home_tweets": []}
        for col, key in ((left, "away_tweets"), (right, "home_tweets")):
            for q in re.findall(r'<blockquote class="tweet">(.*?)</blockquote>', col, re.S):
                hm = re.search(r'class="handle" href="([^"]*)"[^>]*>([^<]*)</a>', q)
                pm = re.search(r"<p[^>]*>(.*?)</p>", q, re.S)
                if not pm:
                    continue
                text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", re.sub(r'<a class="handle".*?</a>', "", pm.group(1), flags=re.S)))).strip()
                dm = re.search(r'<a href="([^"]*)">([A-Z][a-z]{2} \d{1,2}, \d{4})</a>', q)
                tdate = None
                if dm:
                    try:
                        tdate = dt.datetime.strptime(dm.group(2), "%b %d, %Y").strftime("%Y-%m-%d")
                    except ValueError:
                        tdate = None
                out[key].append({"handle": (hm.group(2) if hm else "").strip(), "handle_url": hm.group(1) if hm else "", "text": text, "tweet_url": dm.group(1) if dm else "", "tweet_date": tdate})
        matchups.append(out)
    if not matchups:
        raise ValueError("no GameDayTweets matchups found in the page")
    return {"date": page_date, "matchups": matchups}


def direct_start(text: str, candidates: list[dict]) -> dict | None:
    """The goalie a post DIRECTLY says is starting, or None. Hedged language and posts naming no known goalie of the two teams are not evidence."""
    cleaned = AS_EXPECTED.sub("", text)
    m = DIRECT.search(cleaned)
    if not m:
        return None
    lo = max(cleaned.rfind(". ", 0, m.start()), cleaned.rfind("! ", 0, m.start()), cleaned.rfind("? ", 0, m.start()))
    hi = min([i for i in (cleaned.find(". ", m.end()), cleaned.find("! ", m.end()), cleaned.find("? ", m.end())) if i >= 0] or [len(cleaned)])
    if HEDGE.search(cleaned[lo + 1:hi]):                                       # the sentence that makes the claim must itself be direct; a hedge in an unrelated sentence does not void it
        return None
    before = cleaned[:m.start()].lower().replace("’", "'")
    best, best_pos = None, -1
    for c in candidates:
        s = surname(c["name"])
        pos = before.rfind(s) if s else -1
        if pos > best_pos:
            best, best_pos = c, pos
    return best


# ------------------------------------------------------------------ resolving one side ----

def _entry_status(e: dict, now: dt.datetime) -> dict:
    """GoaliePost's label under the confirmation rules: an explicit CONFIRMED needs a named source and a recent, non-future time; LIKELY and PROBABLE are PROBABLE;
    an entry older than 48 hours is STALE and carries no status."""
    status = LABELS.get(e["label"], UNKNOWN)
    age_h = None if _age_min(e["source_at_utc"], now) is None else _age_min(e["source_at_utc"], now) / 60.0
    note = None
    if age_h is None:
        fresh = NO_TIME
    elif age_h < -FUTURE_TOLERANCE_MIN / 60.0:
        fresh, status, note = NO_TIME, UNKNOWN, "TIMESTAMP_IN_FUTURE"
    else:
        fresh = CURRENT if age_h <= CURRENT_H else RECENT if age_h <= RECENT_H else STALE
    if status == CONFIRMED:
        if not (e["source_name"] and e["source_url"]):
            status, note = PROBABLE, "NO_SOURCE"
        elif age_h is None:
            status, note = PROBABLE, "NO_TIMESTAMP"
        elif age_h > CONFIRMED_MAX_AGE_H:
            status, note = PROBABLE, "CONFIRMATION_STALE"
    if fresh == STALE:
        status, note = UNKNOWN, (note or "STALE")
    return {"status": status, "freshness": fresh, "note": note}


def resolve_side(entries: list[dict], gdt_posts: list[dict], candidates: list[dict], now: dt.datetime, slate_date: str | None) -> dict:
    """One team's resolved starter: status, goalie, freshness, source and, when sources disagree, CONFLICT. Never raises."""
    judged = [(e, _entry_status(e, now)) for e in entries]
    rank = {CONFIRMED: 0, PROBABLE: 1, UNKNOWN: 2}
    judged.sort(key=lambda p: (rank[p[1]["status"]], -(_parse(p[0]["source_at_utc"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)).timestamp()))
    primary = judged[0] if judged else None
    res = {"goalie_id": None, "goalie": None, "status": UNKNOWN, "label": None, "freshness": NO_TIME, "source": None, "source_url": None, "source_name": None, "source_at_utc": None,
           "basis": "NO_SOURCE_ENTRY", "text": None, "note": None, "conflict": None, "corroborated_by": None}
    if primary:
        e, st = primary
        res.update({"goalie_id": e["player_id"], "goalie": e["name"], "status": st["status"], "label": e["label"], "freshness": st["freshness"], "source": "goaliepost", "source_url": e["source_url"],
                    "source_name": e["source_name"], "source_at_utc": e["source_at_utc"], "basis": "GOALIEPOST_" + (e["label"] or "NO_LABEL"), "text": e["comment"], "note": st["note"]})
        confirmed = [x for x in judged if x[1]["status"] == CONFIRMED]
        if len({x[0]["player_id"] for x in confirmed}) > 1:
            res.update({"status": CONFLICT, "conflict": "GoaliePost names two different CONFIRMED goalies: " + " / ".join(x[0]["name"] for x in confirmed)})
    direct = []
    for p in gdt_posts:
        who = direct_start(p["text"], candidates)
        if who:
            direct.append((who, p))
    if direct and res["status"] != CONFLICT:
        who, post = direct[-1]
        if len({w["player_id"] for w, _ in direct}) > 1:
            res.update({"status": CONFLICT, "conflict": "GameDayTweets posts name different starters: " + " / ".join(sorted({w['name'] for w, _ in direct}))})
        elif res["goalie_id"] == who["player_id"]:
            if res["status"] == PROBABLE:
                res.update({"status": CONFIRMED, "basis": "GAMEDAYTWEETS_DIRECT_SAME_GOALIE", "freshness": CURRENT})
            res["corroborated_by"] = {"source": "gamedaytweets", "handle": post["handle"], "url": post["tweet_url"], "text": post["text"], "tweet_date": post["tweet_date"]}
        elif res["status"] == CONFIRMED:
            res.update({"status": CONFLICT, "conflict": f"GoaliePost CONFIRMED {res['goalie']}; GameDayTweets direct post ({post['handle']}) names {who['name']}"})
        else:
            gp_day = (_parse(res["source_at_utc"]) or now).strftime("%Y-%m-%d")
            if res["goalie_id"] is None or (post["tweet_date"] and post["tweet_date"] > gp_day):      # a later calendar day than the old listing: the explicit post supersedes it
                prior = {"goalie": res["goalie"], "status": res["status"], "label": res["label"], "source_at_utc": res["source_at_utc"]}
                res.update({"goalie_id": who["player_id"], "goalie": who["name"], "status": CONFIRMED, "label": "DIRECT_POST", "freshness": CURRENT, "source": "gamedaytweets", "source_url": post["tweet_url"],
                            "source_name": post["handle"], "source_at_utc": None, "basis": "GAMEDAYTWEETS_DIRECT_SUPERSEDES_OLDER_LISTING" if prior["goalie"] else "GAMEDAYTWEETS_DIRECT_ONLY",
                            "text": post["text"], "note": None, "superseded": prior})
            else:
                res.update({"status": CONFLICT, "conflict": f"GoaliePost lists {res['goalie']} ({res['label']}); a GameDayTweets direct post ({post['handle']}) names {who['name']} the same day, so neither is shown to be newer"})
    return res


def _state_of(sides: dict[str, dict]) -> str:
    sts = [s["status"] for s in sides.values()]
    if CONFLICT in sts:
        return "SOURCE_CONFLICT"
    if all(s == CONFIRMED for s in sts):
        return "BOTH_CONFIRMED"
    if UNKNOWN in sts:
        return "UNKNOWN_GOALIE"
    return "ONE_PROBABLE" if CONFIRMED in sts else "BOTH_PROBABLE"


# ------------------------------------------------------------------ state and history ----

def load_state() -> dict:
    p = state_paths.path(STATE_NAME)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    p = state_paths.path(STATE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(p)


def read_history() -> list[dict]:
    p = state_paths.path(HISTORY_NAME)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _append_history(rows: list[dict]) -> None:
    if not rows:
        return
    p = state_paths.path(HISTORY_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def feed_is_fresh(state: dict, now: dt.datetime) -> bool:
    age = _age_min(state.get("fetched_at_utc"), now)
    return bool(state.get("ok")) and age is not None and age <= FEED_MAX_AGE_MIN


def _candidates(nhl, abbrs: list[str], gp_entries: list[dict]) -> list[dict]:
    """The goalies that could be named for a game: GoaliePost's entries plus every goalie who has played for either team."""
    by_id = {e["player_id"]: {"player_id": e["player_id"], "name": e["name"]} for e in gp_entries if e["player_id"]}
    if nhl is not None:
        try:
            q = ",".join("?" * len(abbrs))
            for r in nhl.execute(f"SELECT DISTINCT p.player_id, p.full_name FROM goalie_game_stats g JOIN players p ON p.player_id = g.player_id WHERE g.team_id IN ({q})", abbrs):
                by_id.setdefault(str(r["player_id"]), {"player_id": str(r["player_id"]), "name": r["full_name"]})
        except Exception:                                                  # noqa: BLE001 - candidates are an aid; GoaliePost's own entries still work
            pass
    return list(by_id.values())


def resolve_all(gp: dict, gdt: dict | None, nhl, now: dt.datetime, retrieved_at: str) -> dict:
    posts = {(m["away"], m["home"]): m for m in (gdt or {}).get("matchups", [])}
    slate = (gdt or {}).get("date")
    out = {}
    for g in gp["games"]:
        if g.get("state") not in (None, "Preview"):
            continue
        m = posts.get((g["away"], g["home"]), {})
        allents = g["sides"]["HOME"] + g["sides"]["AWAY"]
        cands = _candidates(nhl, [g["home"], g["away"]], allents)
        sides = {"HOME": resolve_side(g["sides"]["HOME"], m.get("home_tweets", []), cands, now, slate), "AWAY": resolve_side(g["sides"]["AWAY"], m.get("away_tweets", []), cands, now, slate)}
        out[str(g["game_id"])] = {"game_id": g["game_id"], "home": g["home"], "away": g["away"], "start_utc": g["start_utc"], "retrieved_at_utc": retrieved_at, "sides": sides, "state": _state_of(sides),
                                  "gdt_available": bool(m)}
    return out


def _history_rows(resolved: dict, prior: dict, retrieved_at: str) -> list[dict]:
    rows = []
    for gid, g in resolved.items():
        for side, r in g["sides"].items():
            team = g[side.lower()]
            key = (r["goalie_id"], r["status"], r["conflict"] is not None)
            before = ((prior.get(gid) or {}).get("sides") or {}).get(side)
            if before is not None and (before["goalie_id"], before["status"], before["conflict"] is not None) == key:
                continue
            rows.append({"observed_at_utc": retrieved_at, "game_id": int(gid), "team": team, "side": side, "goalie_id": r["goalie_id"], "goalie": r["goalie"], "status": r["status"], "label": r["label"],
                         "freshness": r["freshness"], "source": r["source"], "source_name": r["source_name"], "source_url": r["source_url"], "source_at_utc": r["source_at_utc"], "basis": r["basis"],
                         "text": r["text"], "conflict": r["conflict"], "start_utc": g["start_utc"],
                         "previous": None if before is None else {"goalie_id": before["goalie_id"], "goalie": before["goalie"], "status": before["status"], "source_at_utc": before["source_at_utc"]}})
    return rows


# ------------------------------------------------------------------ fetching ----

def _get(url: str, session=None) -> str:
    import requests
    resp = (session or requests).get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    return resp.text


def _due(state: dict, now: dt.datetime, force: bool) -> bool:
    if force:
        return True
    age = _age_min(state.get("last_attempt_utc"), now)
    if age is None:
        return True
    near = any((_age_min(g.get("start_utc"), now) or 1e9) > -NEAR_WINDOW_H * 60 and (_age_min(g.get("start_utc"), now) or 1e9) < 0 for g in (state.get("games") or {}).values())
    return age >= (NEAR_INTERVAL_MIN if near else MIN_INTERVAL_MIN)


def refresh(nhl, now: dt.datetime | None = None, *, force: bool = False, fetch: Callable[[str], str] | None = None) -> dict:
    """Fetches what is due and resolves it. Never raises: a failure keeps the last good data (with its own fetch time) and records the cause. Off with NHL_ENGINE_GOALIE_SOURCES=OFF."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = load_state()
    if not enabled():
        state["status"] = "DISABLED"
        return state
    if fetch is None and state_paths.under_test():
        state["status"] = "DISABLED"                                        # a test run never reaches the public sites
        return state
    if not _due(state, now, force):
        state["status"] = state.get("status") or "OK"
        return state
    get = fetch or _get
    state["last_attempt_utc"] = _iso(now)
    errors = []
    try:
        gp = parse_goaliepost(get(GP_URL))
    except Exception as exc:                                                # noqa: BLE001
        gp = None
        errors.append(f"goaliepost: {type(exc).__name__}: {str(exc)[:120]}")
    gdt = None
    try:
        gdt = parse_gamedaytweets(get(GDT_URL))
        state["gdt_ok"] = True
    except Exception as exc:                                                # noqa: BLE001
        state["gdt_ok"] = False
        errors.append(f"gamedaytweets: {type(exc).__name__}: {str(exc)[:120]}")
    if gp is not None:
        retrieved = _iso(now)
        resolved = resolve_all(gp, gdt, nhl, now, retrieved)
        _append_history(_history_rows(resolved, state.get("games") or {}, retrieved))
        state.update({"games": resolved, "fetched_at_utc": retrieved, "ok": True, "slate_date": (gdt or {}).get("date")})
    else:
        state["ok"] = False
    state["last_error"] = "; ".join(errors) or None
    state["status"] = "ERROR" if gp is None else ("PARTIAL" if errors else "OK")
    _save(state)
    return state


def run_cycle(nhl, now: dt.datetime | None = None, *, force: bool = False, fetch=None) -> dict:
    st = refresh(nhl, now, force=force, fetch=fetch)
    return {"status": st.get("status"), "last_error": st.get("last_error"), "games": len(st.get("games") or {}), "fetched_at_utc": st.get("fetched_at_utc")}


# ------------------------------------------------------------------ what Eggy sees ----

def game_view(state: dict | None, game_id, home: str, away: str, now: dt.datetime, history: list[dict] | None = None) -> dict:
    """The decision-time goalie picture for one game. An unfetched or stale feed is UNKNOWN for both teams (stale data never passes for current).
    late_change is True when the listed goalie for a team changed after it was first observed."""
    state = state if state is not None else load_state()
    g = (state.get("games") or {}).get(str(game_id))
    fresh = feed_is_fresh(state, now)
    blank = {"goalie_id": None, "goalie": None, "status": UNKNOWN, "label": None, "freshness": NO_TIME, "source": None, "source_name": None, "source_url": None, "source_at_utc": None, "basis": "NO_FRESH_FEED",
             "text": None, "note": None, "conflict": None, "corroborated_by": None}
    if g is None or not fresh:
        sides = {"HOME": dict(blank), "AWAY": dict(blank)}
        if g is not None and not fresh:
            for k in sides:
                sides[k]["note"] = "FEED_STALE"
        return {"state": "UNKNOWN_GOALIE", "feed_fresh": fresh, "retrieved_at_utc": state.get("fetched_at_utc"), "late_change": False, "home": home, "away": away, "sides": sides}
    hist = history if history is not None else read_history()
    changed = False
    for side in ("HOME", "AWAY"):
        ids = [h["goalie_id"] for h in hist if h["game_id"] == int(game_id) and h["side"] == side and h["goalie_id"]]
        changed = changed or len(set(ids)) > 1
    return {"state": g["state"], "feed_fresh": True, "retrieved_at_utc": g["retrieved_at_utc"], "late_change": changed, "home": home, "away": away, "sides": g["sides"]}


def describe(view: dict) -> dict:
    """The card text: {team: 'Joseph Woll - CONFIRMED'} for the STARTING GOALIES section."""
    out = {}
    for side, team in (("AWAY", view["away"]), ("HOME", view["home"])):
        s = view["sides"][side]
        label = s["status"] if s["status"] != CONFLICT else "SOURCE CONFLICT"
        out[team] = f"{s['goalie'] or 'no goalie reported'} - {label}" + ("" if s["freshness"] in (CURRENT, NO_TIME) or s["status"] == UNKNOWN else f" ({s['freshness'].lower()})")
    return out
