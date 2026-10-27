"""How far does a moneyline price normally move in a day? An outcome-free profile of intraday movement, used to say what counts as UNUSUAL movement.

    python3 deploy/build_eggy_movement_profile.py --db ~/nhl_engine_release/operational/runtime/nhl.db --before 2026-10-22 --out docs/validation/eggy_movement_profile.json

For every game with at least two DraftKings moneyline captures on the day it is played (both sides present, captured within 16 hours of the start, at least 4 hours apart: the spacing of the morning and pregame
league pulls), the absolute change in the no-vig win probability between the earliest and latest such capture is recorded. Only snapshots captured BEFORE the cutoff date are read, and only prices: no score, no
result and no replayed pick is read, so the profile cannot be tuned to any outcome. The classification Eggy uses is read straight off this distribution: below the median is ordinary drift (NOISE), the median
to the upper quartile is MODEST, the upper quartile and above is NOTABLE, the 90th percentile and above is EXCEPTIONAL. The percentiles are the data's; that "upper quartile" is the line for "unusual" is a
convention (stated as such in the policy), not a tuned number. Whether such movement predicts anything is NOT claimed: Eggy measures it prospectively.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sqlite3
from pathlib import Path

MIN_SPAN_HOURS = 4.0
WITHIN_HOURS = 16.0


def _implied(a: float) -> float:
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--before", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    c = sqlite3.connect(f"file:{Path(a.db).expanduser()}?mode=ro", uri=True)
    rows = c.execute("SELECT game_id, selection, price_american, captured_at_utc, event_start_utc FROM odds_snapshots WHERE market = 'MONEYLINE' AND sportsbook = 'DraftKings' AND captured_at_utc < ? "
                     "ORDER BY captured_at_utc", (a.before,)).fetchall()
    by, start = collections.defaultdict(lambda: collections.defaultdict(dict)), {}
    for g, sel, price, cap, st in rows:
        by[g][cap][sel] = price
        start[g] = st
    P = dt.datetime.fromisoformat
    changes = []
    for g, caps in by.items():
        s0 = P(start[g])
        ts = sorted(t for t, s in caps.items() if len(s) == 2 and 0 < (s0 - P(t)).total_seconds() <= WITHIN_HOURS * 3600)
        if len(ts) < 2 or (P(ts[-1]) - P(ts[0])).total_seconds() / 3600 < MIN_SPAN_HOURS:
            continue
        def nv(s):
            k = sorted(s)
            ia, ib = _implied(s[k[0]]), _implied(s[k[1]])
            return ia / (ia + ib)
        changes.append(round(abs(nv(caps[ts[-1]]) - nv(caps[ts[0]])), 5))
    changes.sort()
    pct = lambda q: changes[min(len(changes) - 1, int(round(q * (len(changes) - 1))))]
    out = {"built_from": "odds_snapshots (DraftKings MONEYLINE prices only; no scores or results are read)", "snapshots_before": a.before, "games": len(changes),
           "rules": {"within_hours_of_start": WITHIN_HOURS, "min_span_hours_between_first_and_last": MIN_SPAN_HOURS}, "abs_changes": changes,
           "percentiles": {"p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90)},
           "classification": {"NOISE": "below the median |change|: ordinary intraday drift", "MODEST": "median up to the upper quartile", "NOTABLE": "upper quartile and above",
                              "EXCEPTIONAL": "90th percentile and above", "status": "percentiles are measured; treating the upper quartile as 'unusual' is a convention. Predictive value is NOT claimed: measured prospectively."}}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(out["games"], out["percentiles"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
