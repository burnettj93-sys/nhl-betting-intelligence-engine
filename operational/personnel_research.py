"""Automatic pre-pick injury / availability research for the teams playing today.

The engine's own code reads no injury page (NHL.com, CBS Sports and FOX Sports prohibit automated reading; ESPN, Daily Faceoff and RotoWire stay off: docs/SOURCE_POLICY.md). The research is done by a
scheduled research agent that uses GENERAL WEB SEARCH, never a fetch of a prohibited site, in three steps this module supports:

  plan    what is due now (INITIAL / MAIN / RECHECK / SKIP), which teams to research, what is already known, the topics to search, the domains to exclude
  (search) the agent searches the web for each listed team and writes ONLY explicit, attributable statements to a JSON file
  ingest  every statement is validated (named player resolved in the engine's own player data, named team on tonight's slate, explicit status, named source and link not on the prohibited list, the source's own time,
          an exact supporting statement that names the player, current enough for its status) and stored; a team that was searched and produced nothing is recorded as RESEARCHED with no report
          ("no material absence found in the research run", never "healthy"); anything rejected is listed with its reason and stored nowhere

Phases follow the day's decision time (the first game's start minus the 90-minute capture target): INITIAL (one early sweep of the slate), MAIN (about 5:15 PM ET, or 45 minutes before the decision when that is earlier),
RECHECK (the last 25 minutes before the decision, only the teams with an unresolved MATERIAL / MODERATE player: in doubt, only expected, or in conflict). After the decision window nothing is researched.
Nothing here changes a probability, a price or a ranking rule: Eggy reads the stored result through operational/personnel.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from operational import capture_timeline as ct
from operational import personnel as pe

BLOCKED_DOMAINS = ["nhl.com", "espn.com", "dailyfaceoff.com", "rotowire.com", "cbssports.com", "foxsports.com"]
TOPICS = ["injury update", "ruled out", "game-time decision", "questionable", "expected to play", "expected to return", "activated from IR", "placed on IR", "missed morning skate",
          "unavailable tonight", "late scratch", "lineup change", "coach says he will play", "coach says he will not play"]
MAIN_ET_HOUR, MAIN_ET_MINUTE = 17, 15
MAIN_BEFORE_DECISION_MIN = 45
RECHECK_BEFORE_DECISION_MIN = 25
DECISION_GRACE_MIN = 10
INITIAL, MAIN, RECHECK, SKIP = "INITIAL", "MAIN", "RECHECK", "SKIP"


def _et(t: dt.datetime) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo("America/Toronto"))


def slate(nhl, now: dt.datetime) -> list[dict]:
    """Today's (Eastern) NHL games from the engine's own schedule."""
    day = _et(now).strftime("%Y-%m-%d")
    out = []
    for r in nhl.execute("SELECT game_id, away_team, home_team, scheduled_start_utc FROM games WHERE game_date = ? ORDER BY scheduled_start_utc", (day,)):
        start = pe._parse(r["scheduled_start_utc"])
        if start is not None:
            out.append({"game_id": r["game_id"], "away": r["away_team"], "home": r["home_team"], "start_utc": pe._iso(start), "start": start})
    return out


def _runs_today(reports: list[dict], now: dt.datetime) -> list[dict]:
    day = _et(now).strftime("%Y-%m-%d")
    return [r for r in reports if r["type"] == "RESEARCH" and _et(pe._parse(r["recorded_at_utc"])).strftime("%Y-%m-%d") == day]


def plan(nhl, now: dt.datetime, reports: list[dict] | None = None, force_phase: str | None = None) -> dict:
    """What the research agent should do right now. Deterministic: the same clock and the same stored runs give the same plan."""
    reports = pe.read_reports() if reports is None else reports
    games = slate(nhl, now)
    base = {"now_utc": pe._iso(now), "action": SKIP, "reason": None, "teams": [], "games": [{k: g[k] for k in ("game_id", "away", "home", "start_utc")} for g in games], "known": {},
            "unresolved": [], "topics": TOPICS, "blocked_domains": BLOCKED_DOMAINS}
    if not games:
        return {**base, "reason": "NO_NHL_GAMES_TODAY"}
    first = min(g["start"] for g in games)
    decision = first - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN)
    main_due = min(_et(now).replace(hour=MAIN_ET_HOUR, minute=MAIN_ET_MINUTE, second=0, microsecond=0).astimezone(dt.timezone.utc), decision - dt.timedelta(minutes=MAIN_BEFORE_DECISION_MIN))
    base.update({"decision_due_utc": pe._iso(decision), "main_due_utc": pe._iso(main_due)})
    upcoming = [g for g in games if g["start"] > now]
    teams = sorted({t for g in upcoming for t in (g["home"], g["away"])})
    state = pe.resolve_all(nhl, now, reports)
    unresolved = pe.unresolved({t: state[t] for t in teams if t in state})
    base.update({"known": {t: pe.describe(pe.team_view(state, t)) for t in teams}, "unresolved": [{k: p.get(k) for k in ("player_id", "name", "team", "position", "status", "impact", "source", "source_at_utc")} for p in unresolved]})
    if not upcoming:
        return {**base, "reason": "EVERY_GAME_HAS_STARTED"}
    phases_done = {r.get("phase") for r in _runs_today(reports, now)}
    phase = force_phase
    if phase is None:
        if now > decision + dt.timedelta(minutes=DECISION_GRACE_MIN):
            return {**base, "reason": "PAST_THE_DECISION_WINDOW: research after the pick is frozen cannot reach it"}
        if now >= main_due:
            if MAIN not in phases_done:
                phase = MAIN
            elif unresolved and now >= decision - dt.timedelta(minutes=RECHECK_BEFORE_DECISION_MIN) and RECHECK not in phases_done:
                phase = RECHECK
            else:
                return {**base, "reason": "NOTHING_DUE: " + ("the pregame re-check is not due yet" if unresolved and RECHECK not in phases_done else "main research done; no unresolved material player" if not unresolved else "re-check done")}
        elif not phases_done:
            phase = INITIAL
        else:
            return {**base, "reason": "NOTHING_DUE: the initial sweep is done; the main research is not due yet"}
    if phase == RECHECK:
        teams = sorted({p["team"] for p in unresolved})
        if not teams and force_phase is None:
            return {**base, "reason": "NOTHING_DUE: no unresolved material player"}
    return {**base, "action": phase, "teams": teams, "reason": None}


def ingest(nhl, payload: dict, now: dt.datetime) -> dict:
    """Validates and stores a research result: {"phase", "teams": [searched], "search_failed": [teams], "reports": [{player, team, status, designation?, expected_return?, source_name, source_url, source_at_utc, statement,
    authority?}]}. Never raises for a bad item: it is listed as rejected with its reason and stored nowhere."""
    phase = payload.get("phase")
    if phase not in (INITIAL, MAIN, RECHECK):
        raise ValueError("phase must be INITIAL, MAIN or RECHECK")
    on_slate = {t for g in slate(nhl, now) for t in (g["home"], g["away"])}
    searched = sorted({t.upper() for t in payload.get("teams") or []} & on_slate)
    failed = {t.upper() for t in payload.get("search_failed") or []}
    accepted, rejected = [], []
    for item in payload.get("reports") or []:
        label = f"{item.get('player')} ({item.get('team')})"
        try:
            team = str(item.get("team") or "").upper()
            if team not in on_slate:
                raise ValueError("the team is not playing today")
            pid, pos = item.get("player_id"), item.get("position")
            if not pid:
                pid, found = pe._find(nhl, team, item.get("player") or "")
                pos = pos or found
            else:
                row = nhl.execute("SELECT position FROM players WHERE player_id = ?", (str(pid),)).fetchone()
                if row is None:
                    raise ValueError("the player id is not in the engine's player data")
                pos = row["position"]
            at, precision = item.get("source_at_utc"), "TIME"
            if isinstance(at, str) and len(at) == 10:                       # the source gives a date and no time: the earliest instant of that Eastern day, so freshness is never flattered
                from zoneinfo import ZoneInfo
                at = pe._iso(dt.datetime.strptime(at, "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/Toronto"))), "DATE"
                at, precision = at
            rec = pe.record_report(player_id=pid, player_name=item.get("player") or "", team=team, position=pos, status=item.get("status"), source_name=item.get("source_name"),
                                   source_url=item.get("source_url"), source_at_utc=at, time_precision=precision, authority=item.get("authority") or "REPORTER", designation=item.get("designation"),
                                   expected_return=item.get("expected_return"), statement=item.get("statement"), origin="RESEARCH", phase=phase, now=now)
            accepted.append({"player": rec["player_name"], "team": team, "status": rec["status"]})
        except ValueError as exc:
            rejected.append({"item": label, "reason": str(exc)})
    for team in searched:
        if team in failed:
            continue
        pe._append({"type": "RESEARCH", "team": team, "phase": phase, "source_name": "general web search", "source_url": "web-search", "source_at_utc": pe._iso(now), "recorded_at_utc": pe._iso(now),
                    "reports_accepted": sum(1 for a in accepted if a["team"] == team)})
    return {"phase": phase, "teams_searched": [t for t in searched if t not in failed], "teams_failed": sorted(failed & set(searched)), "accepted": accepted, "rejected": rejected}


def main(argv=None) -> int:
    import db
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--force-phase", choices=[INITIAL, MAIN, RECHECK])
    i = sub.add_parser("ingest")
    i.add_argument("--file", required=True)
    args = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    nhl = db.get_conn()
    if args.cmd == "plan":
        print(json.dumps(plan(nhl, now, force_phase=args.force_phase), indent=1, default=str))
        return 0
    with open(args.file) as fh:
        out = ingest(nhl, json.load(fh), now)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
