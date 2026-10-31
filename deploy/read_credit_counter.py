#!/usr/bin/env python3
"""Read The Odds API credit counter WITHOUT spending a credit (free GET /v4/sports/). Prints UTC time, remaining and used; never prints the key.

    python3 deploy/read_credit_counter.py

Run it once shortly before 00:00 UTC on the 1st and once shortly after: a drop of `used` to ~0 with `remaining` back near 500 brackets the reset. A single reading is an observation, not proof of the provider's schedule.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    import requests
    from research.live_sog_pricing.env_config import get_the_odds_api_key
    key = get_the_odds_api_key()
    if not key:
        print("no configured key")
        return 1
    resp = requests.get("https://api.the-odds-api.com/v4/sports/", params={"apiKey": key}, timeout=20)
    print(f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} http={resp.status_code} remaining={resp.headers.get('x-requests-remaining')} used={resp.headers.get('x-requests-used')}")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
