"""Replay a past day's CAPTURE PLAN under the current planner, from the credit ledger as it stood when the day's plan was made. No outcomes, no network, no credits; scratch state only.

Usage: python3 deploy/replay_capture_plan.py DAY plan_time_utc remaining_header 'gid=start_utc,...' [--ahead 'YYYY-MM-DD=n,...']
Example: python3 deploy/replay_capture_plan.py 2026-10-30 2026-10-30T04:08:15Z 49 '2026020214=2026-10-30T22:00:00Z,2026020215=2026-10-30T23:00:00Z,2026020216=2026-10-31T00:00:00Z' --ahead 2026-10-31=10
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("plan_time")
    ap.add_argument("remaining", type=int)
    ap.add_argument("games")
    ap.add_argument("--ahead", default="")
    ap.add_argument("--ledger", default=str(Path.home() / "nhl_engine_release" / "operational" / "runtime" / "credit_plan_ledger.jsonl"))
    a = ap.parse_args()
    scratch = tempfile.mkdtemp(prefix="replay_plan_")
    os.environ["NHL_ENGINE_STATE_DIR"] = scratch
    from operational import credit_planner as cp, state_paths
    now = utc(a.plan_time)
    rows = [ln for ln in Path(a.ledger).read_text().splitlines() if json.loads(ln)["at"] < a.plan_time]
    state_paths.path(cp.LEDGER_NAME).parent.mkdir(parents=True, exist_ok=True)
    state_paths.path(cp.LEDGER_NAME).write_text("\n".join(rows) + "\n")
    starts = {g: utc(t) for g, t in (x.split("=") for x in a.games.split(","))}
    ahead = {d: int(n) for d, n in (x.split("=") for x in a.ahead.split(",") if x)}
    plan = cp.build_day_plan(now, a.day, starts, remaining=a.remaining, counts_ahead=ahead)
    print(json.dumps({k: plan[k] for k in ("budget", "coverage", "games_priced", "games_not_priced", "game_markets", "allowance", "surplus_credits", "notes")}, indent=1, default=str))
