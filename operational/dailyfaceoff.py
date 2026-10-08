"""
Daily Faceoff (dailyfaceoff.com) starting goalies and line combinations -- the one public source found that carries both.

Access evidence (checked 2026-10-08): the pages https://www.dailyfaceoff.com/starting-goalies/ and
https://www.dailyfaceoff.com/teams/<slug>/line-combinations answer plain HTTP GETs (200) with server-rendered JSON in a Next.js
`__NEXT_DATA__` block. robots.txt for `User-agent: *` is `Allow: /` with only /api/ and /cms/ disallowed; this module never touches
those paths, requests a page at most once per MIN_INTERVAL_*, identifies itself, and keeps a kill switch
(`NHL_ENGINE_DAILYFACEOFF=OFF`). No terms-of-use page could be located for the site (the obvious URLs return 404), so the owner should
read whatever terms apply before relying on it; switching it off needs one environment line and loses nothing else.

What the data is, and is not:
  * starting goalies: a status word (Confirmed / Likely / ...), the goalie, the newest news item with its source and time. "Confirmed"
    is Daily Faceoff's editorial label, not an NHL feed, and its source is sometimes the team's own post and sometimes a beat
    reporter's (observed 2026-10-08: PHI "Philadelphia Flyers" post vs OTT "Andrew Wilmek" post, both labelled Confirmed). Only a
    "Confirmed" whose source is the team itself is recorded as CONFIRMED; a reporter-sourced "Confirmed" and every other word are
    recorded as EXPECTED, so the confirmation gate is exactly as strict as before (the manual confirmation still opens it). On 2026-10-08
    only 1 of 11 "Confirmed" labels was team-sourced; the other 10 cited named beat reporters. Accepting those is an owner decision
    (NHL_ENGINE_ACCEPT_REPORTER_CONFIRMATIONS=ON); the source kind stays recorded either way.
  * line combinations: the lineup a beat reporter reported for the next game (forward lines, defense pairs, power-play and penalty-kill
    units) with the reporter's name, link and update time. It is a REPORTED lineup, not what the team will actually dress, and not what
    the player did last night.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import unicodedata

from operational import state_paths

BASE = "https://www.dailyfaceoff.com"
USER_AGENT = "nhl-engine-paper-research/1.0 (personal research; low volume; contact via repository owner)"
STATE_NAME = "dailyfaceoff_state.json"
MIN_INTERVAL_GOALIES_MIN = 20.0
MIN_INTERVAL_LINES_MIN = 180.0
SWITCH_ENV = "NHL_ENGINE_DAILYFACEOFF"
REPORTER_ENV = "NHL_ENGINE_ACCEPT_REPORTER_CONFIRMATIONS"   # owner decision; OFF unless set to ON
SOURCE_PREFIX = "dailyfaceoff:"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

SOURCE_TEAM, SOURCE_REPORTER = "TEAM", "REPORTER"
STATUS_MAP = {"confirmed": "CONFIRMED"}          # every other word (likely, projected, unconfirmed, ...) is only an expectation


def accept_reporters() -> bool:
    return os.environ.get(REPORTER_ENV, "OFF").strip().upper() == "ON"


def enabled() -> bool:
    return os.environ.get(SWITCH_ENV, "ON").strip().upper() != "OFF"


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ").replace(".", "")).strip()


# ------------------------------------------------------------------ parsing (pure) ----

def next_data(html: str) -> dict:
    m = NEXT_DATA.search(html)
    if not m:
        raise ValueError("page has no __NEXT_DATA__ block (the site's layout changed)")
    return json.loads(m.group(1))["props"]["pageProps"]


def parse_starting_goalies(page: dict) -> dict:
    games = []
    for g in page.get("data") or []:
        sides = {}
        for side in ("home", "away"):
            name = g.get(f"{side}GoalieName")
            if not name:
                continue
            word = (g.get(f"{side}NewsStrengthName") or "").strip()
            team_name = g.get(f"{side}TeamName")
            source_name = (g.get(f"{side}NewsSourceName") or "").strip()
            kind = SOURCE_TEAM if source_name and norm(source_name) == norm(team_name or "") else SOURCE_REPORTER
            status = STATUS_MAP.get(word.lower(), "EXPECTED") if (kind == SOURCE_TEAM or accept_reporters()) else "EXPECTED"
            sides[side] = {"team_name": team_name, "goalie": name, "status_word": word, "status": status, "source_kind": kind,
                           "details": (g.get(f"{side}NewsDetails") or "").strip(), "source_name": source_name,
                           "source_url": g.get(f"{side}NewsSourceUrl"), "news_at_utc": g.get(f"{side}NewsCreatedAt")}
        games.append({"start_utc": g.get("dateGmt"), "sides": sides})
    return {"page_date": page.get("date"), "games": games}


def parse_line_combinations(page: dict) -> dict:
    c = page["combinations"]
    groups: dict[str, list[dict]] = {}
    for p in c.get("players", []):
        key = p["groupIdentifier"]
        groups.setdefault(key, []).append({"name": p["name"], "position": p.get("positionIdentifier"), "category": p.get("categoryIdentifier"),
                                           "injury_status": p.get("injuryStatus"), "game_time_decision": bool(p.get("gameTimeDecision"))})
    return {"team": c["teamAbbreviation"], "team_name": c["teamName"], "slug": c["teamSlug"], "reported_by": c.get("sourceName"),
            "source_url": c.get("source"), "updated_at_utc": c.get("updatedAt"), "groups": groups}


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
    if not stamp:
        return None
    return (now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 60.0


def _get(path: str, session) -> str:
    import requests
    resp = (session or requests).get(BASE + path, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    return resp.text


def refresh(now: dt.datetime | None = None, *, session=None, force: bool = False, fetch=None) -> dict:
    """Fetches what is due. Never raises: a failure keeps the last good data and records the cause."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = load_state()
    if not enabled():
        state["status"] = "DISABLED"
        return state
    if fetch is None and session is None and state_paths.under_test():
        state["status"] = "DISABLED"                    # a test run never reaches the public site
        return state
    get = fetch or (lambda path: _get(path, session))
    errors = []
    try:
        due_goalies = force or (_age_min((state.get("goalies") or {}).get("fetched_at_utc"), now) or 1e9) >= MIN_INTERVAL_GOALIES_MIN
        if due_goalies:
            parsed = parse_starting_goalies(next_data(get("/starting-goalies/")))
            state["goalies"] = {**parsed, "fetched_at_utc": _iso(now)}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"starting-goalies: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        lines = state.get("lines") or {}
        due_lines = force or (_age_min(lines.get("fetched_at_utc"), now) or 1e9) >= MIN_INTERVAL_LINES_MIN
        if due_lines:
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
    """Appends one goalie_status_events row per team per upcoming game when the source's word, goalie or news changed."""
    from ingest.nhl_api import record_goalie_status
    g = state.get("goalies") or {}
    if not g.get("games"):
        return {"written": 0, "unmatched": 0}
    abbr = state.get("teams") or {}
    written = unmatched = 0
    for game in g["games"]:
        start = game.get("start_utc")
        for side, s in game["sides"].items():
            team = abbr.get(s["team_name"])
            if team is None:
                unmatched += 1
                continue
            row = nhl.execute("SELECT game_id, game_state, home_team, away_team FROM games WHERE scheduled_start_utc LIKE ? AND (home_team = ? OR away_team = ?)",
                              ((start or "")[:16] + "%", team, team)).fetchone()
            if row is None or row["game_state"] != "SCHEDULED":
                unmatched += 1
                continue
            players = nhl.execute("SELECT player_id, full_name FROM players WHERE position = 'G'").fetchall()
            hits = [p for p in players if norm(p["full_name"]) == norm(s["goalie"])]
            if len(hits) != 1:
                unmatched += 1
                continue
            source = f"{SOURCE_PREFIX}{s['status_word'] or 'unspecified'}|{s['source_kind']}|{s.get('source_url') or ''}|{s.get('source_name') or ''}"[:300]
            latest = nhl.execute("SELECT player_id, status FROM goalie_status_events WHERE game_id = ? AND team_id = ? ORDER BY observed_at_utc DESC, id DESC LIMIT 1",
                                 (row["game_id"], team)).fetchone()
            if s["status"] != "CONFIRMED" and latest and latest["status"] == "CONFIRMED" and latest["player_id"] == hits[0]["player_id"]:
                continue        # an expectation never walks back a confirmation of the same goalie
            last = nhl.execute("SELECT player_id, status, source FROM goalie_status_events WHERE game_id = ? AND team_id = ? AND source LIKE 'dailyfaceoff:%' "
                               "ORDER BY id DESC LIMIT 1", (row["game_id"], team)).fetchone()
            if last and (last["player_id"], last["status"], last["source"]) == (hits[0]["player_id"], s["status"], source):
                continue
            record_goalie_status(nhl, int(row["game_id"]), team, hits[0]["player_id"], s["status"], s.get("news_at_utc") or _iso(now), _iso(now), source)
            written += 1
    nhl.commit()
    return {"written": written, "unmatched": unmatched}


def observed_lineup(state: dict, team: str, players: dict[str, dict]) -> dict:
    """{player_id: {"line": "F1"|"D2"..., "pp": "PP1"|"PP2"|None, "pk": ..., "injury_status", ...}} for one team, matched by name."""
    t = ((state.get("lines") or {}).get("teams") or {}).get(team)
    if not t:
        return {}
    by_name = {norm(p["name"]): pid for pid, p in players.items() if p["team"] == team}
    out: dict[str, dict] = {}
    for group, plist in t["groups"].items():
        for p in plist:
            pid = by_name.get(norm(p["name"]))
            if pid is None:
                continue
            rec = out.setdefault(pid, {"line": None, "pp": None, "pk": None, "injury_status": p.get("injury_status"), "game_time_decision": p["game_time_decision"]})
            if re.fullmatch(r"[fd][1-4]", group):
                rec["line"] = group.upper()
            elif group in ("pp1", "pp2"):
                rec["pp"] = group.upper()
            elif group in ("pk1", "pk2"):
                rec["pk"] = group.upper()
    return out
