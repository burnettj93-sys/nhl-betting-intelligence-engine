"""Injury / player-availability intelligence for Eggy, from ATTRIBUTABLE reports only.

No injury page is read by the engine: NHL.com, CBS Sports and FOX Sports each prohibit automated reading in their written terms (docs/SOURCE_POLICY.md,
docs/PERSONNEL_SOURCES.md), and ESPN, Daily Faceoff and RotoWire stay off. What this module provides is the structure that a permitted acquisition step fills: a person (or an agent
session working like a person) records a specific, attributable status -- named player, named team, explicit status, a named source with its link and its own timestamp -- with
`python -m operational.personnel record ...`, and records a sweep ("checked team X at time T, no material absence") the same way. Nothing vague, anonymous or unsourced is accepted, and a
status is never inferred from a roster absence.

States: OUT, IR, DAY_TO_DAY, QUESTIONABLE, GAME_TIME_DECISION, EXPECTED_TO_PLAY, EXPECTED_TO_RETURN, ACTIVE_CLEARED, and UNKNOWN (no usable report). Each report keeps the player id and name, team, position,
source, source link, the source's own time and the engine's recording time (separate clocks), the designation and expected return, the previous status, whether it is new today and its freshness
(CURRENT / RECENT / STALE; stale reports are kept for history and never drive Eggy). Two sources that disagree and cannot be ordered by time and authority are a SOURCE CONFLICT, never resolved toward a
preferred bet.

Impact is measured from the engine's own data (time on ice and points per game over the team's last ten games, position-aware) and classed MATERIAL / MODERATE / DEPTH / UNRATED -- no probability penalty
is invented. Goalies belong to the goalie feed, not here. This module never writes roster_status_events, so no other part of the product reads these reports.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys

from operational import state_paths

REPORTS_NAME = "personnel_reports.jsonl"
STATUSES = ("OUT", "IR", "DAY_TO_DAY", "QUESTIONABLE", "GAME_TIME_DECISION", "EXPECTED_TO_PLAY", "EXPECTED_TO_RETURN", "ACTIVE_CLEARED")
ABSENT, UNCERTAIN, AVAILABLE = "ABSENT", "UNCERTAIN", "AVAILABLE"
CLASS_OF = {"OUT": ABSENT, "IR": ABSENT, "DAY_TO_DAY": UNCERTAIN, "QUESTIONABLE": UNCERTAIN, "GAME_TIME_DECISION": UNCERTAIN,
            "EXPECTED_TO_PLAY": AVAILABLE, "EXPECTED_TO_RETURN": AVAILABLE, "ACTIVE_CLEARED": AVAILABLE}
AUTHORITY = {"OFFICIAL": 3, "REPORTER": 2, "AGGREGATOR": 1}          # team / league statement > a named beat reporter > a secondary aggregation
CURRENT, RECENT, STALE = "CURRENT", "RECENT", "STALE"
# (CURRENT up to, RECENT up to) in hours, by the kind of status: a long-term absence stays current for days; a game-time decision is current only hours.
FRESHNESS_H = {"LONG_TERM": (168.0, 504.0), "OUT": (36.0, 72.0), "UNCERTAIN": (24.0, 48.0), "GAME_TIME_DECISION": (6.0, 12.0), "AVAILABLE": (24.0, 48.0), "SWEEP": (12.0, 24.0)}
LONG_TERM_DAYS = 14
FUTURE_TOLERANCE_MIN = 5.0
SAME_TIME_MIN = 15.0                                                 # reports this close in source time are "the same moment" for ordering
WINDOW_GAMES = 10
MATERIAL, MODERATE, DEPTH, UNRATED = "MATERIAL", "MODERATE", "DEPTH", "UNRATED"
TEAMS = {"ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WSH", "WPG"}


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp) -> dt.datetime | None:
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _hours(stamp, now: dt.datetime) -> float | None:
    t = _parse(stamp)
    return None if t is None else (now - t).total_seconds() / 3600.0


# ------------------------------------------------------------------ the report log ----

def read_reports() -> list[dict]:
    p = state_paths.path(REPORTS_NAME)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _append(row: dict) -> None:
    p = state_paths.path(REPORTS_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _check_source(name, url, at, now: dt.datetime) -> str | None:
    """A report is attributable or it is not accepted."""
    if not (name or "").strip():
        return "a named source is required"
    if not re.match(r"https?://\S+\.\S+", url or ""):
        return "a link to the source is required"
    t = _parse(at)
    if t is None:
        return "the source's own timestamp is required (ISO, UTC)"
    if t > now + dt.timedelta(minutes=FUTURE_TOLERANCE_MIN):
        return "the source timestamp is in the future"
    return None


def record_report(*, player_id, player_name, team, position, status, source_name, source_url, source_at_utc, authority="REPORTER", designation=None,
                  expected_return=None, now: dt.datetime | None = None) -> dict:
    """Validates and appends one attributable status. Raises ValueError (nothing is stored) for anything vague, unsourced, unsupported or about a goalie."""
    now = now or dt.datetime.now(dt.timezone.utc)
    status = (status or "").strip().upper().replace("-", "_").replace(" ", "_")
    team = (team or "").strip().upper()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if team not in TEAMS:
        raise ValueError("a valid NHL team abbreviation is required")
    if not str(player_id or "").strip() or not (player_name or "").strip():
        raise ValueError("a named player with an NHL player id is required")
    if (position or "").upper() == "G":
        raise ValueError("goalies are covered by the goalie feed, not by personnel reports")
    authority = (authority or "").strip().upper()
    if authority not in AUTHORITY:
        raise ValueError(f"authority must be one of {', '.join(AUTHORITY)}")
    bad = _check_source(source_name, source_url, source_at_utc, now)
    if bad:
        raise ValueError(bad)
    row = {"type": "REPORT", "player_id": str(player_id), "player_name": player_name.strip(), "team": team, "position": (position or "").upper() or None, "status": status,
           "designation": (designation or None), "expected_return": (expected_return or None), "source_name": source_name.strip(), "source_url": source_url.strip(),
           "source_at_utc": _iso(_parse(source_at_utc)), "authority": authority, "recorded_at_utc": _iso(now)}
    _append(row)
    return row


def record_sweep(*, team, source_name, source_url, checked_at_utc, now: dt.datetime | None = None) -> dict:
    """'Checked this team against this source at this time and found no material absence beyond the recorded reports.' Without a fresh sweep, silence is NOT read as health."""
    now = now or dt.datetime.now(dt.timezone.utc)
    team = (team or "").strip().upper()
    if team not in TEAMS:
        raise ValueError("a valid NHL team abbreviation is required")
    bad = _check_source(source_name, source_url, checked_at_utc, now)
    if bad:
        raise ValueError(bad)
    row = {"type": "SWEEP", "team": team, "source_name": source_name.strip(), "source_url": source_url.strip(), "source_at_utc": _iso(_parse(checked_at_utc)), "recorded_at_utc": _iso(now)}
    _append(row)
    return row


# ------------------------------------------------------------------ freshness, ordering, conflicts ----

def _long_term(r: dict) -> bool:
    if r["status"] == "IR":
        return True
    ret = (r.get("expected_return") or "").lower()
    if re.search(r"(ltir|long[- ]term|season|months?|indefinite)", ret):
        return True
    m = re.search(r"(\d+)\s*(week|wk)", ret)
    return bool(m and int(m.group(1)) * 7 >= LONG_TERM_DAYS) or bool(re.search(r"(\d+)\s*day", ret) and int(re.search(r"(\d+)\s*day", ret).group(1)) >= LONG_TERM_DAYS)


def freshness(r: dict, now: dt.datetime) -> str:
    h = _hours(r.get("source_at_utc"), now)
    if h is None or h < -FUTURE_TOLERANCE_MIN / 60.0:
        return STALE
    kind = "SWEEP" if r["type"] == "SWEEP" else ("LONG_TERM" if _long_term(r) else ("GAME_TIME_DECISION" if r["status"] == "GAME_TIME_DECISION" else
                                                                                     ("OUT" if r["status"] == "OUT" else ("UNCERTAIN" if CLASS_OF[r["status"]] == UNCERTAIN else "AVAILABLE"))))
    cur, rec = FRESHNESS_H[kind]
    return CURRENT if h <= cur else (RECENT if h <= rec else STALE)


def _order(a: dict, b: dict) -> tuple[dict, dict | None, str | None]:
    """Of two reports on one player returns (winner, loser, conflict reason). Same source: the newer wins. Different sources with different availability classes: the newer wins only if it is at least as
    authoritative; reports at (nearly) the same moment go to the strictly more authoritative; anything else stays a CONFLICT (uncertainty kept)."""
    if CLASS_OF[a["status"]] == CLASS_OF[b["status"]]:                     # same class: the newer detail replaces the older
        new, old = (a, b) if a["source_at_utc"] >= b["source_at_utc"] else (b, a)
        return new, old, None
    new, old = (a, b) if a["source_at_utc"] >= b["source_at_utc"] else (b, a)
    gap_min = abs((_parse(a["source_at_utc"]) - _parse(b["source_at_utc"])).total_seconds()) / 60.0
    if a["source_name"] == b["source_name"]:
        return new, old, None
    if gap_min <= SAME_TIME_MIN:
        if AUTHORITY[a["authority"]] != AUTHORITY[b["authority"]]:
            win, lose = (a, b) if AUTHORITY[a["authority"]] > AUTHORITY[b["authority"]] else (b, a)
            return win, lose, None
        return new, old, f"{a['source_name']} says {a['status']} and {b['source_name']} says {b['status']} at the same time"
    if AUTHORITY[new["authority"]] >= AUTHORITY[old["authority"]]:
        return new, old, None
    return new, old, f"{new['source_name']} ({new['authority'].lower()}) says {new['status']} later, but {old['source_name']} ({old['authority'].lower()}) said {old['status']}"


def resolve_player(rows: list[dict], now: dt.datetime) -> dict | None:
    """One player's current state from his reports (stale ones are history only)."""
    rows = sorted((r for r in rows if r["type"] == "REPORT"), key=lambda r: r["source_at_utc"])
    if not rows:
        return None
    live = [r for r in rows if freshness(r, now) != STALE]
    prev_of = lambda r: next((x["status"] for x in reversed(rows) if x["source_at_utc"] < r["source_at_utc"]), None)
    if not live:
        last = rows[-1]
        return {**last, "freshness": STALE, "current_status": "UNKNOWN", "class": "UNKNOWN", "previous_status": prev_of(last), "conflict": None}
    win, conflict = live[0], None
    for r in live[1:]:
        win, _l, conflict = _order(win, r)                                  # a later, clean ordering clears an earlier disagreement; one still standing at the end is the conflict
    if conflict:                                                            # unresolved: the player is neither shown as out nor as fine
        return {**win, "freshness": freshness(win, now), "current_status": "CONFLICT", "class": UNCERTAIN, "previous_status": prev_of(win), "conflict": conflict}
    return {**win, "freshness": freshness(win, now), "current_status": win["status"], "class": CLASS_OF[win["status"]], "previous_status": prev_of(win), "conflict": None}


# ------------------------------------------------------------------ player importance (the engine's own data) ----

def impact_of(nhl, player_id: str, team: str, position: str | None) -> dict:
    """MATERIAL / MODERATE / DEPTH / UNRATED from the team's last WINDOW_GAMES: time-on-ice rank within the position group and points per game rank on the team. Honest about what is missing (no power-play
    ice time is stored, so a power-play role is not measured)."""
    if nhl is None:
        return {"impact": UNRATED, "basis": "no engine data"}
    try:
        games = [r[0] for r in nhl.execute("SELECT DISTINCT game_id FROM player_game_stats WHERE team_id = ? ORDER BY game_id DESC LIMIT ?", (team, WINDOW_GAMES))]
        if not games:
            return {"impact": UNRATED, "basis": "no recent games for the team"}
        q = ",".join("?" * len(games))
        rows = nhl.execute(f"SELECT s.player_id, p.position, AVG(s.toi_minutes) AS toi, COUNT(*) AS gp, SUM(COALESCE(s.goals,0) + COALESCE(s.assists,0)) AS pts FROM player_game_stats s LEFT JOIN players p ON p.player_id = s.player_id "
                           f"WHERE s.team_id = ? AND s.game_id IN ({q}) AND COALESCE(s.played, 1) = 1 AND s.toi_minutes IS NOT NULL AND COALESCE(p.position, '') <> 'G' GROUP BY s.player_id", (team, *games)).fetchall()
    except Exception:                                                      # noqa: BLE001 - unrated is honest when the data cannot be read
        return {"impact": UNRATED, "basis": "engine data unavailable"}
    me = next((r for r in rows if str(r["player_id"]) == str(player_id)), None)
    if me is None or me["gp"] < 3:
        return {"impact": UNRATED, "basis": f"fewer than 3 recent games of data for the player"}
    pos = (me["position"] or position or "F").upper()
    group = [r for r in rows if ((r["position"] or "F").upper() == "D") == (pos == "D") and r["gp"] >= 3]
    toi_rank = 1 + sum(1 for r in group if r["toi"] > me["toi"])
    ppg = lambda r: r["pts"] / r["gp"]
    pts_rank = 1 + sum(1 for r in rows if r["gp"] >= 5 and ppg(r) > ppg(me)) if me["gp"] >= 5 and ppg(me) > 0 else None
    top, mid = (4, 6) if pos == "D" else (6, 9)
    if toi_rank <= top or (pts_rank is not None and pts_rank <= 3):
        cls = MATERIAL
    elif toi_rank <= mid or (pts_rank is not None and pts_rank <= 6):
        cls = MODERATE
    else:
        cls = DEPTH
    return {"impact": cls, "toi_rank": toi_rank, "toi_avg": round(me["toi"], 1), "points_per_game": round(ppg(me), 2), "points_rank": pts_rank, "games": me["gp"],
            "basis": f"{pos} #{toi_rank} in time on ice ({me['toi']:.1f} min) and {ppg(me):.2f} points/game over the last {me['gp']} games; power-play role not measured (no power-play ice time stored)"}


# ------------------------------------------------------------------ the team picture Eggy sees ----

def resolve_all(nhl, now: dt.datetime, reports: list[dict] | None = None) -> dict:
    """{team: {"coverage", "swept_at_utc", "players": [...]}} at decision time. coverage: SWEPT (a fresh sweep or report exists, so silence means no material absence), REPORTS_ONLY (some fresh reports but
    no sweep: absence of a report is not health) or NONE (no usable report: the team's injuries are unknown)."""
    reports = read_reports() if reports is None else reports
    today_et = _et_date(now)
    by_team: dict[str, dict] = {}
    for r in reports:
        by_team.setdefault(r["team"], {"reports": [], "sweeps": []})["sweeps" if r["type"] == "SWEEP" else "reports"].append(r)
    out = {}
    for team, d in by_team.items():
        by_player: dict[str, list[dict]] = {}
        for r in d["reports"]:
            by_player.setdefault(r["player_id"], []).append(r)
        players = []
        for pid, rows in by_player.items():
            st = resolve_player(rows, now)
            if st is None:
                continue
            imp = impact_of(nhl, pid, team, st.get("position"))
            first_today = [x for x in rows if _et_date(_parse(x["source_at_utc"])) == today_et]
            players.append({"player_id": pid, "name": st["player_name"], "team": team, "position": st.get("position"), "status": st["current_status"], "class": st["class"], "freshness": st["freshness"],
                            "designation": st.get("designation"), "expected_return": st.get("expected_return"), "source": st["source_name"], "source_url": st["source_url"], "authority": st["authority"],
                            "source_at_utc": st["source_at_utc"], "recorded_at_utc": st["recorded_at_utc"], "previous_status": st["previous_status"], "new_today": bool(first_today) and st["previous_status"] is None,
                            "conflict": st["conflict"], **imp})
        fresh_sweeps = [s for s in d["sweeps"] if freshness(s, now) != STALE]
        swept = max((s["source_at_utc"] for s in fresh_sweeps), default=None)
        live = [p for p in players if p["freshness"] != STALE]
        out[team] = {"coverage": "SWEPT" if swept else ("REPORTS_ONLY" if live else "NONE"), "swept_at_utc": swept, "players": players}
    return out


def _et_date(t: dt.datetime) -> str:
    try:
        from zoneinfo import ZoneInfo
        return t.astimezone(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except Exception:                                                      # noqa: BLE001
        return (t - dt.timedelta(hours=4)).strftime("%Y-%m-%d")


def decision_relevant(p: dict) -> bool:
    """Only items that bear on the bet: a fresh report, on a player whose absence or doubt matters (MATERIAL or MODERATE), that is not simply 'active'. A depth player is never put on the card."""
    return p["freshness"] != STALE and p["impact"] in (MATERIAL, MODERATE) and p["class"] in (ABSENT, UNCERTAIN)


def team_view(state: dict | None, team: str) -> dict:
    t = (state or {}).get(team) or {"coverage": "NONE", "swept_at_utc": None, "players": []}
    rel = [p for p in t["players"] if decision_relevant(p)]
    material = [p for p in rel if p["impact"] == MATERIAL]
    return {"team": team, "coverage": t["coverage"], "swept_at_utc": t["swept_at_utc"], "relevant": rel,
            "material_absent": [p for p in material if p["class"] == ABSENT], "material_uncertain": [p for p in material if p["class"] == UNCERTAIN],
            "moderate_absent": [p for p in rel if p["impact"] == MODERATE and p["class"] == ABSENT], "conflicts": [p for p in rel if p["conflict"]],
            "material_returns": [p for p in t["players"] if p["freshness"] != STALE and p["impact"] == MATERIAL and p["class"] == AVAILABLE and p["previous_status"] in ("OUT", "IR")]}


def describe(v: dict) -> str:
    """The card line for one team."""
    if v["coverage"] == "NONE":
        return f"{v['team']} - injury information NOT AVAILABLE (no attributable report recorded)"
    if not v["relevant"]:
        return f"{v['team']} - no material current absence identified" + ("" if v["coverage"] == "SWEPT" else " in the reports recorded (no full sweep)")
    bits = []
    for p in v["relevant"]:
        why = "SOURCE CONFLICT" if p["conflict"] else p["status"].replace("_", " ")
        bits.append(f"{p['name']} ({p['position'] or '?'}) {why} - {p['impact']}" + (f", {p['freshness'].lower()}" if p["freshness"] != CURRENT else ""))
    return f"{v['team']} - " + "; ".join(bits)


# ------------------------------------------------------------------ command line (the permitted acquisition step) ----

def _find(nhl, team: str, name: str) -> tuple[str, str | None]:
    rows = nhl.execute("SELECT DISTINCT p.player_id, p.position FROM players p JOIN player_game_stats s ON s.player_id = p.player_id WHERE s.team_id = ? AND LOWER(p.full_name) = LOWER(?)", (team, name)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"could not identify '{name}' on {team} from the engine's player data ({len(rows)} matches); pass --player-id")
    return str(rows[0][0]), rows[0][1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record one attributable status")
    for a in ("team", "status", "source", "url", "source-at"):
        r.add_argument("--" + a, required=True)
    r.add_argument("--player"), r.add_argument("--player-id"), r.add_argument("--position")
    r.add_argument("--authority", default="REPORTER"), r.add_argument("--designation"), r.add_argument("--expected-return")
    s = sub.add_parser("sweep", help="record that a team was checked against a source and no further material absence was found")
    for a in ("team", "source", "url", "checked-at"):
        s.add_argument("--" + a, required=True)
    sub.add_parser("show", help="the resolved picture at this moment")
    args = ap.parse_args(argv)
    if args.cmd == "show":
        import db
        state = resolve_all(db.get_conn(), dt.datetime.now(dt.timezone.utc))
        for team in sorted(state):
            print(describe(team_view(state, team)))
        return 0
    try:
        if args.cmd == "sweep":
            row = record_sweep(team=args.team, source_name=args.source, source_url=args.url, checked_at_utc=args.checked_at)
        else:
            pid, pos = args.player_id, args.position
            if not pid:
                import db
                pid, found = _find(db.get_conn(), args.team.upper(), args.player or "")
                pos = pos or found
            row = record_report(player_id=pid, player_name=args.player or "", team=args.team, position=pos, status=args.status, source_name=args.source, source_url=args.url,
                                source_at_utc=args.source_at, authority=args.authority, designation=args.designation, expected_return=args.expected_return)
    except ValueError as exc:
        print(f"NOT RECORDED: {exc}", file=sys.stderr)
        return 2
    print("recorded", json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
