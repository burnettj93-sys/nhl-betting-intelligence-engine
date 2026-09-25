"""
Read-only analysis: can the CURRENT moneyline polling schedule keep pregame prices CURRENT under the
market-freshness rules (operational/cloud_snapshot_schema.classify_market_freshness)?

Uses the game start times already in nhl.db and the schedule in the loaded launchd plists (or the
documented default). Makes NO network call and spends NO Odds API credits; never writes.

    python3 -m operational.odds_freshness_analysis            # human report
    python3 -m operational.odds_freshness_analysis --json
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

from operational import cloud_snapshot_schema as schema

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PULLS_UTC = ("12:00", "17:00", "21:00", "00:00")     # launchd 08/13/17/20 local (EDT)
CURRENT_MIN, NEAR_MIN, NEAR_H = schema.MARKET_CURRENT_MAX_MINUTES, schema.NEAR_GAME_CURRENT_MAX_MINUTES, schema.NEAR_GAME_WINDOW_HOURS


def upcoming_starts(db_path: Path | None = None, days: int = 14, now: dt.datetime | None = None) -> list[dt.datetime]:
    now = now or dt.datetime.now(dt.timezone.utc)
    path = db_path or (REPO_ROOT / "operational" / "runtime" / "nhl.db")
    if not path.exists():
        path = REPO_ROOT / "nhl.db"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT scheduled_start_utc FROM games WHERE scheduled_start_utc >= ? "
                            "AND scheduled_start_utc < ? ORDER BY 1",
                            (now.strftime("%Y-%m-%dT%H:%M"), (now + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M"))).fetchall()
    finally:
        conn.close()
    return [schema.parse_utc(r[0]) for r in rows if schema.parse_utc(r[0])]


def pulls_for(day_range: list[dt.date], times=BASELINE_PULLS_UTC) -> list[dt.datetime]:
    out = []
    for day in day_range:
        for hhmm in times:
            h, m = (int(x) for x in hhmm.split(":"))
            out.append(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=dt.timezone.utc))
    return sorted(out)


def _state(pulls: list[dt.datetime], start: dt.datetime, t: dt.datetime) -> str:
    prior = [p for p in pulls if p <= t]
    if not prior:
        return "UNAVAILABLE"
    return schema.classify_market_freshness(prior[-1].isoformat(), start.isoformat(), t)["state"]


def decision_policy_eligible(pulls: list[dt.datetime], start: dt.datetime) -> bool:
    """The REAL moneyline pipeline prices every game at anchor = puck drop - 30 min and accepts only a
    DraftKings quote captured at most 10 min before that anchor (config.ODDS_STALENESS_TIERS, tier
    0.5-2 h out; pricing/engine.py). So a recommendation is possible only if a pull landed in
    [anchor - 10 min, anchor]. This is the decision policy (unchanged here), stricter than the display rule."""
    anchor = start - dt.timedelta(minutes=30)
    return any(anchor - dt.timedelta(minutes=10) <= p <= anchor for p in pulls)


def evaluate(starts: list[dt.datetime], pulls: list[dt.datetime]) -> dict:
    per_game, cur_30, cur_60, window_current_min, window_total_min = [], 0, 0, 0, 0
    longest_stale = []
    for s in starts:
        c30 = _state(pulls, s, s - dt.timedelta(minutes=30)) == "CURRENT"
        c60 = _state(pulls, s, s - dt.timedelta(minutes=60)) == "CURRENT"
        cur_30 += c30
        cur_60 += c60
        cur = tot = run = worst = 0
        for minute in range(int(NEAR_H * 60), 0, -5):                # last 4 h before puck drop
            tot += 5
            if _state(pulls, s, s - dt.timedelta(minutes=minute)) == "CURRENT":
                cur += 5
                run = 0
            else:
                run += 5
                worst = max(worst, run)
        window_current_min += cur
        window_total_min += tot
        longest_stale.append(worst)
        per_game.append({"start": s.isoformat(), "current_at_T-60": c60, "current_at_T-30": c30,
                         "pct_of_last_4h_current": round(100 * cur / tot, 1), "longest_stale_run_min": worst})
    n = max(len(starts), 1)
    policy_ok = sum(decision_policy_eligible(pulls, s) for s in starts)
    return {"games": len(starts), "decision_policy_quote_available_pct": round(100 * policy_ok / n, 1), "current_at_T-60_pct": round(100 * cur_60 / n, 1),
            "current_at_T-30_pct": round(100 * cur_30 / n, 1),
            "pct_of_last_4h_current": round(100 * window_current_min / max(window_total_min, 1), 1),
            "median_longest_stale_run_min": sorted(longest_stale)[len(longest_stale) // 2] if longest_stale else None,
            "per_game": per_game}


def add_targeted_pulls(starts: list[dt.datetime], base: list[dt.datetime], *, interval_s: int = 120,
                       offset_s: int = 0, latency_s: int = 5) -> list[dt.datetime]:
    """The ACTUAL pregame architecture (operational/moneyline_pregame.py) simulated: games are clustered
    (start times within CLUSTER_SPREAD_MIN), launchd fires every `interval_s` starting at `offset_s` past the
    hour, and the first firing inside a cluster's due window makes one pull that lands `latency_s` later.
    A cluster whose due window contains no firing is a real miss and is left uncovered."""
    from operational import moneyline_pregame as mp
    pulls = list(base)
    for c in mp.plan_clusters(starts):
        lo, hi = c.due_window
        if lo > hi:
            continue
        # first firing time >= lo on the grid  offset_s + k * interval_s (seconds past the hour)
        hour = lo.replace(minute=0, second=0, microsecond=0)
        t = hour + dt.timedelta(seconds=offset_s)
        while t < lo:
            t += dt.timedelta(seconds=interval_s)
        if t <= hi:
            pulls.append(t + dt.timedelta(seconds=latency_s))
    return sorted(pulls)


def coverage_report(starts: list[dt.datetime], base: list[dt.datetime]) -> dict:
    """BEFORE (fixed 4x/day) vs AFTER (+ pregame clusters), worst case over launchd phase offsets."""
    before = evaluate(starts, base)
    afters = [evaluate(starts, add_targeted_pulls(starts, base, offset_s=o)) for o in range(0, 120, 20)]
    worst = min(afters, key=lambda r: r["decision_policy_quote_available_pct"])
    missed = [g["start"] for g in worst["per_game"] if not decision_policy_eligible(
        add_targeted_pulls(starts, base, offset_s=0), dt.datetime.fromisoformat(g["start"]))]
    from operational import moneyline_pregame as mp
    clusters = mp.plan_clusters(starts)
    return {"games": len(starts), "clusters": len(clusters),
            "before_pct": before["decision_policy_quote_available_pct"],
            "before_games_covered": round(before["decision_policy_quote_available_pct"] * len(starts) / 100),
            "after_pct_worst_phase": worst["decision_policy_quote_available_pct"],
            "after_games_covered": round(worst["decision_policy_quote_available_pct"] * len(starts) / 100),
            "games_missed_after": missed, "extra_pulls": len(clusters)}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    now = dt.datetime.now(dt.timezone.utc)
    starts = upcoming_starts(now=now)
    days = sorted({(now + dt.timedelta(days=i)).date() for i in range(-1, 16)})
    base = pulls_for(days)
    targeted = add_targeted_pulls(starts, base)
    cov = coverage_report(starts, base)
    game_days = len({(s - dt.timedelta(hours=8)).date() for s in starts}) or 1
    report = {
        "generated_at_utc": now.isoformat(), "games_analyzed": len(starts), "game_days": game_days,
        "baseline_pulls_utc": list(BASELINE_PULLS_UTC),
        "baseline": {k: v for k, v in evaluate(starts, base).items() if k != "per_game"},
        "with_targeted_pregame_pulls": {k: v for k, v in evaluate(starts, targeted).items() if k != "per_game"},
        "extra_credits_per_game_day": round((len([p for p in targeted if p not in base and p > now])) / game_days, 2),
        "coverage": cov,
    }
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
