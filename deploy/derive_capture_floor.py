"""Derive the DATA COVERAGE FLOOR from the engine's own history (an operations requirement, not a betting threshold). Read-only.

  distinct legs a full day needs  = the most distinct legs any day that REACHED the daily ticket target actually used (learning cohort database)
  eligible legs per shots game    = a low percentile (p10) of, per day, the best cycle's "parlay_legs_eligible" divided by the games captured at that time (trader log + credit ledger)
  floor games                     = ceil(distinct legs needed / eligible legs per game at that percentile)

Usage: python3 deploy/derive_capture_floor.py [--runtime DIR] [--log FILE] [--write]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import re
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
PERCENTILE = 0.10


def _et(stamp: str) -> str:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def percentile(values: list[float], q: float) -> float:
    xs = sorted(values)
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def distinct_legs_by_day(db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(db)
    out: dict[str, dict] = collections.defaultdict(lambda: {"tickets": 0, "keys": set()})
    for et_date, keys in conn.execute("SELECT et_date, leg_keys FROM parlays"):
        out[et_date]["tickets"] += 1
        out[et_date]["keys"].update(json.loads(keys))
    return {d: {"tickets": v["tickets"], "distinct_legs": len(v["keys"])} for d, v in out.items()}


def eligible_per_game(ledger: Path, log: Path) -> dict[str, dict]:
    captured = [(r["at"], r["game_id"]) for r in map(json.loads, ledger.read_text().splitlines()) if r.get("game_id") and r["class"] == "PROPS_PREGAME" and r.get("credits", 0) > 0]
    text = log.read_text(errors="ignore")
    marks = [(m.start(), m.group(1)) for m in re.finditer(r'"generated_at_utc": "(2026-[0-9T:\-]+Z)"', text)]
    best: dict[str, dict] = {}
    for i, (pos, stamp) in enumerate(marks):
        seg = text[pos:(marks[i + 1][0] if i + 1 < len(marks) else pos + 30000)]
        m = re.search(r'"parlay_legs_eligible": (\d+)', seg)
        if not m:
            continue
        day = _et(stamp)
        games = {g for at, g in captured if _et(at) == day and at <= stamp}
        if games and int(m.group(1)) >= best.get(day, {}).get("eligible", -1):
            best[day] = {"eligible": int(m.group(1)), "games_captured": len(games), "per_game": round(int(m.group(1)) / len(games), 2)}
    return best


def derive(runtime: Path, log: Path, target: int = 10) -> dict:
    legs = distinct_legs_by_day(runtime / "learning_cohort.db")
    full = {d: v for d, v in legs.items() if v["tickets"] >= target}
    per_game = eligible_per_game(runtime / "credit_plan_ledger.jsonl", log)
    needed = max(v["distinct_legs"] for v in full.values())
    rate = percentile([v["per_game"] for v in per_game.values()], PERCENTILE)
    return {"purpose": "operations requirement (data coverage), not a betting threshold", "ticket_target": target,
            "distinct_legs_needed": needed, "days_that_reached_the_target": full,
            "eligible_legs_per_shots_game": {"percentile": PERCENTILE, "value": round(rate, 2), "by_day": per_game},
            "floor_games": int(math.ceil(needed / rate)),
            "method": "floor_games = ceil(distinct_legs_needed / eligible_legs_per_shots_game at the stated low percentile)",
            "derived_on": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", default=str(Path.home() / "nhl_engine_release" / "operational" / "runtime"))
    ap.add_argument("--log", default=str(Path.home() / "nhl_engine_release" / "operational" / "logs" / "real_parlay_paper_trader.log"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    doc = derive(Path(a.runtime), Path(a.log))
    print(json.dumps(doc, indent=1))
    if a.write:
        (REPO / "docs" / "validation" / "capture_coverage_floor.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
