"""
Daily prospective-recording entry point (Preseason Closing sprint,
Section 6). Deliberately SEPARATE from sync_daily.py -- data sync and
model observation recording are conceptually distinct steps in the
pipeline (sync data -> generate predictions -> record predictions), and
this script is only the last of the three.

Usage:
    python3 -m operational.record_daily_predictions [--checkpoint PRIMARY_DAILY]

Today, the ONLY real, wired data source is the Live SOG board cache
(research/live_sog_board_cache.json, populated by
research/live_sog_pricing/refresh.py) -- the reference integration named
in Section 15. Running this script when that cache is empty (true right
now, offseason) records zero observations and says so plainly; it does
NOT fabricate rows to have something to show.

Idempotent by construction (Section 7): re-running this script for the
same board cache produces zero new rows on the second run, since every
row's idempotency key already exists in the ledger.
"""
from __future__ import annotations

import argparse
import sys

from dashboard.live_sog_pricing_view import load_board_cache
from operational import prospective_ledger as pl
from operational import prospective_recording as pr


def run(checkpoint: str = pr.DEFAULT_CHECKPOINT) -> dict:
    conn = pl.init_db()
    cache = load_board_cache()
    if cache is None:
        return {"status": "NO_CACHE", "recorded": 0, "duplicates": 0, "ineligible": 0}

    board = cache.get("board", [])
    priced = [r for r in board if r.get("status") == "PRICED"]
    recorded, duplicates, ineligible = 0, 0, 0
    for row in priced:
        result = pr.record_sog_board_row(conn, row, checkpoint=checkpoint, is_demo=False)
        if result["status"] == "INSERTED":
            recorded += 1
        elif result["status"] == "DUPLICATE":
            duplicates += 1
        else:
            ineligible += 1
    return {"status": "OK", "recorded": recorded, "duplicates": duplicates, "ineligible": ineligible,
            "total_priced_rows_seen": len(priced)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", choices=pr.CHECKPOINTS, default=pr.DEFAULT_CHECKPOINT)
    args = parser.parse_args()
    result = run(checkpoint=args.checkpoint)
    print(result)
    if result["status"] == "NO_CACHE":
        print("No live SOG board cache found -- nothing to record. This is the honest, "
              "expected state during the offseason or before the first live refresh.")


if __name__ == "__main__":
    main()
