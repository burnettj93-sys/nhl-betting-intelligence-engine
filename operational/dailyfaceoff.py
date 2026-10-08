"""
Daily Faceoff (dailyfaceoff.com) starting goalies and line combinations -- the one public source found that carries both.

ACCESS AND TERMS (checked 2026-10-08):
  * Technically reachable: plain HTTP GETs of https://www.dailyfaceoff.com/starting-goalies/ and /teams/<slug>/line-combinations answer 200 with
    server-rendered JSON in a Next.js `__NEXT_DATA__` block. robots.txt (`User-agent: *`) is `Allow: /`, disallowing only /api/ and /cms/.
  * Contractually restricted: the site belongs to The Nation Network, whose Terms of Service (https://oilersnation.com/terms-of-service, linked
    from the network's sites; they state they govern "any and all of its subsidiaries, affiliates, brands") say a user may not "use any robot,
    spider, rover, scraper or any other data-mining technology or automatic or manual process to monitor, cache, frame, mask, extract data from,
    copy or distribute any data from the Services", and may not make commercial use of the content, other than keeping and sharing information
    "for your own non-commercial purposes". Daily Faceoff's own footer links only a privacy policy; its /terms-of-use path redirects to a 404.
    robots.txt permission and a low request rate do NOT establish permission under those terms.
  * Therefore this reader is OFF unless the owner opts in (`NHL_ENGINE_DAILYFACEOFF=ON` in the environment or `.env`), which records that the owner
    has read the terms above and accepts the use. When off it makes no request at all, and everything else works (manual confirmations, estimated
    usage). When on it still requests politely: starters at most every 20 minutes, line combinations every 3 hours, a descriptive User-Agent.

CONFIRMATION POLICY (owner-set, 2026-10-08). A goalie row is CONFIRMED only when ALL hold:
  1. Daily Faceoff's status word is "Confirmed";
  2. the cited source is identifiable: the team itself (TEAM_POST) or a reporter on the recognized list (RECOGNIZED_REPORTER,
     `operational/recognized_starter_sources.json`, matched by the account handle in the cited link) with a link and a name -- never a blank or
     unlisted source (UNRECOGNIZED_SOURCE / NO_SOURCE stay EXPECTED);
  3. the item is fresh: created no more than CONFIRMATION_MAX_AGE_H hours ago and not in the future;
  4. the item's text names the goalie.
"Likely", blank and unsupported entries are EXPECTED. Source name, link, time and basis are stored on every row. A later report that names a different
goalie, from any source, makes the earlier confirmation a CONFLICT (the gate stays closed). A recognized reporter whose past confirmations were wrong is
suspended automatically (`source_record`). The lineup and injury information from the same source is held to the same standard (`lineup_basis`).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from pathlib import Path

from operational import state_paths

BASE = "https://www.dailyfaceoff.com"
USER_AGENT = "nhl-engine-paper-research/1.0 (personal, non-commercial research; low volume; contact via repository owner)"
STATE_NAME = "dailyfaceoff_state.json"
MIN_INTERVAL_GOALIES_MIN = 20.0
MIN_INTERVAL_LINES_MIN = 180.0
SWITCH_ENV = "NHL_ENGINE_DAILYFACEOFF"
SOURCE_PREFIX = "dailyfaceoff:"
RECOGNIZED_PATH = Path(__file__).resolve().parent / "recognized_starter_sources.json"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

CONFIRMATION_MAX_AGE_H = 30.0     # a confirmation older than this is stale
LINEUP_MAX_AGE_H = 48.0           # a reported lineup older than this is stale
FEED_MAX_AGE_MIN = 90.0           # DF-sourced confirmations count only while the last good fetch is this recent
FUTURE_TOLERANCE_MIN = 5.0
SUSPEND_WRONG_OF_LAST = (2, 10)   # a recognized reporter wrong 2+ times in their last 10 resolved confirmations is suspended

TEAM_POST, RECOGNIZED_REPORTER, UNRECOGNIZED_SOURCE, NO_SOURCE = "TEAM_POST", "RECOGNIZED_REPORTER", "UNRECOGNIZED_SOURCE", "NO_SOURCE"
ACCEPTED_BASES = (TEAM_POST, RECOGNIZED_REPORTER)


def _env_value(name: str) -> str | None:
    import os
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    env = state_paths.REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, val = line.strip().partition("=")
            if sep and k.strip() == name:
                return val.strip().strip("\"'")
    return None


def enabled() -> bool:
    """OFF unless the owner opted in (see ACCESS AND TERMS)."""
    return (_env_value(SWITCH_ENV) or "OFF").upper() == "ON"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str | None) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ").replace(".", "")).strip()


# ------------------------------------------------------------------ source standards ----

def handle_of(url: str | None) -> str | None:
    """The account handle in a cited social link (x.com/<handle>/status/..), lower-cased."""
    m = re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)/", url or "")
    return m.group(1).lower() if m else None


def load_recognized(path: Path | None = None) -> dict:
    try:
        return json.loads((path or RECOGNIZED_PATH).read_text())
    except (OSError, json.JSONDecodeError):
        return {"x_handles": {}, "suspended": {}}


def classify_source(team_name: str | None, source_name: str | None, source_url: str | None, *, recognized: dict | None = None) -> str:
    name = (source_name or "").strip()
    if not name or not (source_url or "").strip():
        return NO_SOURCE
    if norm(name) == norm(team_name or ""):
        return TEAM_POST
    rec = recognized if recognized is not None else load_recognized()
    h = handle_of(source_url)
    if h and h in {k.lower() for k in rec.get("x_handles", {})} and h not in {k.lower() for k in rec.get("suspended", {})}:
        return RECOGNIZED_REPORTER
    return UNRECOGNIZED_SOURCE


def _names_goalie(details: str, goalie: str) -> bool:
    surname = norm(goalie).split(" ")[-1] if goalie else ""
    return bool(surname) and surname in norm(details)


def judge_goalie_item(side: dict, now: dt.datetime | None) -> tuple[str, str | None]:
    """(status, rejection reason or None) for one parsed goalie slot, applying the confirmation policy."""
    if side["status_word"].lower() != "confirmed":
        return "EXPECTED", None
    basis = side["basis"]
    if basis not in ACCEPTED_BASES:
        return "EXPECTED", basis
    if not _names_goalie(side.get("details", ""), side["goalie"]):
        return "EXPECTED", "TEXT_DOES_NOT_NAME_GOALIE"
    if now is not None:
        at = _parse(side.get("news_at_utc"))
        if at is None:
            return "EXPECTED", "NO_TIMESTAMP"
        age_h = (now - at).total_seconds() / 3600.0
        if age_h > CONFIRMATION_MAX_AGE_H:
            return "EXPECTED", "STALE"
        if age_h < -FUTURE_TOLERANCE_MIN / 60.0:
            return "EXPECTED", "TIMESTAMP_IN_FUTURE"
    return "CONFIRMED", None


# ------------------------------------------------------------------ parsing (pure) ----

def next_data(html: str) -> dict:
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("page has no __NEXT_DATA__ block (the site's layout changed)")
    return json.loads(m.group(1))["props"]["pageProps"]


def parse_starting_goalies(page: dict, now: dt.datetime | None = None, *, recognized: dict | None = None) -> dict:
    games = []
    rec = recognized if recognized is not None else load_recognized()
    for g in page.get("data") or []:
        sides = {}
        for side in ("home", "away"):
            name = g.get(f"{side}GoalieName")
            if not name:
                continue
            team_name = g.get(f"{side}TeamName")
            source_name = (g.get(f"{side}NewsSourceName") or "").strip()
            url = g.get(f"{side}NewsSourceUrl")
            s = {"team_name": team_name, "goalie": name, "status_word": (g.get(f"{side}NewsStrengthName") or "").strip(),
                 "details": (g.get(f"{side}NewsDetails") or "").strip(), "source_name": source_name, "source_url": url,
                 "news_at_utc": g.get(f"{side}NewsCreatedAt"), "basis": classify_source(team_name, source_name, url, recognized=rec)}
            s["status"], s["rejected_because"] = judge_goalie_item(s, now)
            sides[side] = s
        games.append({"start_utc": g.get("dateGmt"), "sides": sides})
    return {"page_date": page.get("date"), "games": games}


def rejudge(state: dict, now: dt.datetime) -> dict:
    """Re-apply the policy to a saved parse (a confirmation that was fresh when fetched can age out before the next fetch)."""
    rec = load_recognized()
    for g in (state.get("goalies") or {}).get("games", []):
        for s in g["sides"].values():
            s["basis"] = classify_source(s["team_name"], s["source_name"], s["source_url"], recognized=rec)
            s["status"], s["rejected_because"] = judge_goalie_item(s, now)
    return state


def parse_line_combinations(page: dict) -> dict:
    c = page["combinations"]
    groups: dict[str, list[dict]] = {}
    for p in c.get("players", []):
        key = p["groupIdentifier"]
        groups.setdefault(key, []).append({"name": p["name"], "position": p.get("positionIdentifier"), "category": p.get("categoryIdentifier"),
                                           "injury_status": p.get("injuryStatus"), "game_time_decision": bool(p.get("gameTimeDecision"))})
    return {"team": c["teamAbbreviation"], "team_name": c["teamName"], "slug": c["teamSlug"], "reported_by": c.get("sourceName"),
            "source_url": c.get("source"), "updated_at_utc": c.get("updatedAt"), "groups": groups}


def lineup_basis(team_report: dict, now: dt.datetime, *, recognized: dict | None = None) -> dict:
    """The same source standard for a team's reported lineup (and the injury flags on it): who reported it, with a link, how old.
    status: REPORTED (identifiable source, fresh) | STALE | UNSOURCED."""
    basis = classify_source(team_report.get("team_name"), team_report.get("reported_by"), team_report.get("source_url"), recognized=recognized)
    at = _parse(team_report.get("updated_at_utc"))
    age_h = None if at is None else (now - at).total_seconds() / 3600.0
    if basis not in ACCEPTED_BASES:
        status = "UNSOURCED"
    elif age_h is None or age_h > LINEUP_MAX_AGE_H or age_h < -FUTURE_TOLERANCE_MIN / 60.0:
        status = "STALE"
    else:
        status = "REPORTED"
    return {"status": status, "basis": basis, "age_hours": None if age_h is None else round(age_h, 1)}


def team_directory(page: dict) -> list[dict]:
    return [{"name": t["name"], "abbrev": t["shortName"], "slug": t["slug"]} for t in page.get("sortedTeams") or []]


# ------------------------------------------------------------------ state ----

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


def _age_min(stamp: str | None, now: dt.datetime) -> float | None:
    t = _parse(stamp)
    return None if t is None else (now - t).total_seconds() / 60.0


def feed_is_fresh(state: dict | None, now: dt.datetime) -> bool:
    """True while the last successful starters fetch is recent enough for a DF-sourced confirmation to be believed."""
    state = state if state is not None else load_state()
    age = _age_min((state.get("goalies") or {}).get("fetched_at_utc"), now)
    return age is not None and age <= FEED_MAX_AGE_MIN and state.get("goalies_ok", True)


def _get(path: str, session) -> str:
    import requests
    resp = (session or requests).get(BASE + path, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    return resp.text


def refresh(now: dt.datetime | None = None, *, session=None, force: bool = False, fetch=None) -> dict:
    """Fetches what is due. Never raises: a failure keeps the last good data (cached, with its own fetch time) and records the cause.
    Off (no request at all) unless the owner opted in."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = load_state()
    if not enabled():
        state["status"] = "DISABLED"
        state["disabled_reason"] = "Owner has not opted in (NHL_ENGINE_DAILYFACEOFF=ON): the site's terms restrict automated access."
        if not state_paths.under_test():
            _save(state)                                   # so the product shows WHY nothing was read, not "never run"
        return state
    if fetch is None and session is None and state_paths.under_test():
        state["status"] = "DISABLED"                    # a test run never reaches the public site
        return state
    get = fetch or (lambda path: _get(path, session))
    errors = []
    try:
        due = force or (_age_min((state.get("goalies") or {}).get("fetched_at_utc"), now) or 1e9) >= MIN_INTERVAL_GOALIES_MIN
        if due:
            state["goalies"] = {**parse_starting_goalies(next_data(get("/starting-goalies/")), now), "fetched_at_utc": _iso(now)}
            state["goalies_ok"] = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"starting-goalies: {type(exc).__name__}: {str(exc)[:120]}")
        state["goalies_ok"] = False
    try:
        lines = state.get("lines") or {}
        due = force or (_age_min(lines.get("fetched_at_utc"), now) or 1e9) >= MIN_INTERVAL_LINES_MIN
        if due:
            first = next_data(get("/teams/ottawa-senators/line-combinations"))
            teams = team_directory(first)
            out = {}
            for t in teams:
                page = first if t["slug"] == "ottawa-senators" else next_data(get(f"/teams/{t['slug']}/line-combinations"))
                out[t["abbrev"]] = parse_line_combinations(page)
            state["lines"] = {"teams": out, "fetched_at_utc": _iso(now)}
            state["teams"] = {t["name"]: t["abbrev"] for t in teams}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"line-combinations: {type(exc).__name__}: {str(exc)[:120]}")
    state["last_error"] = "; ".join(errors) or None
    state["status"] = "ERROR" if errors else "OK"
    state["last_attempt_utc"] = _iso(now)
    if not errors:
        state["last_ok_utc"] = _iso(now)
    _save(state)
    return state


# ------------------------------------------------------------------ into the engine ----

def ingest_goalie_status(nhl, state: dict, now: dt.datetime) -> dict:
    """Appends one goalie_status_events row per team per upcoming game when the source's word, goalie, basis or news changed.
    The row's source is `dailyfaceoff:<word>|<basis>|<link>|<source name>`; its effective time is the item's own time."""
    from ingest.nhl_api import record_goalie_status
    g = state.get("goalies") or {}
    if not g.get("games") or not feed_is_fresh(state, now):
        return {"written": 0, "unmatched": 0, "skipped": "FEED_NOT_FRESH" if g.get("games") else "NO_DATA"}
    rejudge(state, now)
    abbr = state.get("teams") or {}
    written = unmatched = 0
    goalies = nhl.execute("SELECT player_id, full_name FROM players WHERE position = 'G'").fetchall()
    for game in g["games"]:
        start = game.get("start_utc")
        for side, s in game["sides"].items():
            team = abbr.get(s["team_name"])
            if team is None:
                unmatched += 1
                continue
            row = nhl.execute("SELECT game_id, game_state FROM games WHERE scheduled_start_utc LIKE ? AND (home_team = ? OR away_team = ?)",
                              ((start or "")[:16] + "%", team, team)).fetchone()
            if row is None or row["game_state"] != "SCHEDULED":
                unmatched += 1
                continue
            hits = [p for p in goalies if norm(p["full_name"]) == norm(s["goalie"])]
            if len(hits) != 1:
                unmatched += 1
                continue
            source = f"{SOURCE_PREFIX}{s['status_word'] or 'unspecified'}|{s['basis']}|{s.get('source_url') or ''}|{s.get('source_name') or ''}"[:300]
            latest = nhl.execute("SELECT player_id, status FROM goalie_status_events WHERE game_id = ? AND team_id = ? ORDER BY observed_at_utc DESC, id DESC LIMIT 1",
                                 (row["game_id"], team)).fetchone()
            if s["status"] != "CONFIRMED" and latest and latest["status"] == "CONFIRMED" and latest["player_id"] == hits[0]["player_id"]:
                continue        # an expectation naming the same goalie never walks back a confirmation
            last = nhl.execute("SELECT player_id, status, source FROM goalie_status_events WHERE game_id = ? AND team_id = ? AND source LIKE 'dailyfaceoff:%' "
                               "ORDER BY id DESC LIMIT 1", (row["game_id"], team)).fetchone()
            if last and (last["player_id"], last["status"], last["source"]) == (hits[0]["player_id"], s["status"], source):
                continue
            record_goalie_status(nhl, int(row["game_id"]), team, hits[0]["player_id"], s["status"], s.get("news_at_utc") or _iso(now), _iso(now), source)
            written += 1
    nhl.commit()
    return {"written": written, "unmatched": unmatched}


def observed_lineup(state: dict, team: str, players: dict[str, dict], now: dt.datetime | None = None) -> dict:
    """{player_id: {"line", "pp", "pk", "injury_status", ..., "report": basis/status}} for one team, matched by name. Entries carry the
    team report's source standard; callers show line/PP/injury only when status is REPORTED."""
    t = ((state.get("lines") or {}).get("teams") or {}).get(team)
    if not t:
        return {}
    quality = lineup_basis(t, now or dt.datetime.now(dt.timezone.utc))
    by_name = {norm(p["name"]): pid for pid, p in players.items() if p["team"] == team}
    out: dict[str, dict] = {}
    for group, plist in t["groups"].items():
        for p in plist:
            pid = by_name.get(norm(p["name"]))
            if pid is None:
                continue
            rec = out.setdefault(pid, {"line": None, "pp": None, "pk": None, "injury_status": p.get("injury_status"),
                                       "game_time_decision": p["game_time_decision"], "report": quality})
            if re.fullmatch(r"[fd][1-4]", group):
                rec["line"] = group.upper()
            elif group in ("pp1", "pp2"):
                rec["pp"] = group.upper()
            elif group in ("pk1", "pk2"):
                rec["pk"] = group.upper()
    return out


# ------------------------------------------------------------------ track record ----

def source_record(nhl) -> dict:
    """Per cited account: how many of its CONFIRMED items named the goalie who actually started (resolved games only)."""
    rows = nhl.execute("SELECT game_id, team_id, player_id, source, observed_at_utc FROM goalie_status_events "
                       "WHERE source LIKE 'dailyfaceoff:%' AND status = 'CONFIRMED' ORDER BY observed_at_utc").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        started = nhl.execute("SELECT player_id FROM goalie_game_stats WHERE game_id = ? AND team_id = ? AND started = 1", (r["game_id"], r["team_id"])).fetchall()
        if not started:
            continue
        parts = r["source"].split("|") + ["", "", "", ""]
        handle = handle_of(parts[2]) or parts[3].lower()
        d = out.setdefault(handle, {"resolved": 0, "right": 0, "results": []})
        ok = str(r["player_id"]) in {str(x["player_id"]) for x in started}
        d["resolved"] += 1
        d["right"] += int(ok)
        d["results"].append(int(ok))
    wrong_n, of_n = SUSPEND_WRONG_OF_LAST
    for d in out.values():
        last = d["results"][-of_n:]
        d["wrong_in_last"] = len(last) - sum(last)
        d["suspend"] = d["wrong_in_last"] >= wrong_n
    return out
