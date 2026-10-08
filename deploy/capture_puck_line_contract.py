#!/usr/bin/env python3
"""Owner-run, ONE paid credit: captures one real DraftKings `spreads` payload (all listed NHL games) so the puck-line price contract can be certified.

    python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit

The call is the league-wide /odds endpoint with a single market (the same cost model as the moneyline pull: markets x regions = 1). The raw response is archived with
the provider's headers, validated by research/generic_prop_pricing/puck_line_contract.py, and the first three events are written to
tests/fixtures/draftkings_puck_line_real_payload.json with their provenance. Nothing is certified automatically: a person reads the fixture, then adds the
contract entry and a parity test (see how PLAYER_GOALS was done in tests/test_goals_market.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm-spend-1-credit", action="store_true")
    args = ap.parse_args()
    if not args.confirm_spend_1_credit:
        print("Refusing to spend a credit without --confirm-spend-1-credit.")
        return 2
    from operational import credit_planner as cp
    from research.generic_prop_pricing import puck_line_contract as plc
    from research.live_sog_pricing import archive, client
    r = client.get_sport_odds(markets="spreads", bookmakers="draftkings")
    if not r.ok:
        print("API error:", r.error)
        return 1
    archive.archive_result(r, event_id=None, market_filter="spreads", bookmaker_filter="draftkings")
    cp.record("CONTRACT_CERTIFICATION", int(r.requests_last or 0), dt.datetime.now(dt.timezone.utc), market="spreads")
    checks = [plc.validate_spreads_event(e) for e in r.data]
    ok = [e for e, c in zip(r.data, checks) if c["status"] == "SHAPE_OK"]
    print(f"{len(r.data)} events; {len(ok)} with a valid draftkings spreads shape; credits charged {r.requests_last}, remaining {r.requests_remaining}")
    if ok:
        out = REPO / "tests" / "fixtures" / "draftkings_puck_line_real_payload.json"
        out.write_text(json.dumps({"events": ok[:3], "_provenance": {"retrieved_at_utc": r.retrieved_at_utc, "requests_last": r.requests_last,
                                                                      "note": "Real DraftKings payload via The Odds API, spreads only, first 3 events; nothing altered."}}, indent=1))
        print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
