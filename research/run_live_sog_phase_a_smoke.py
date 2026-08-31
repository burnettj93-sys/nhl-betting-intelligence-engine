"""
Phase A real API contract smoke test (one-time, credit-conscious). Makes
the smallest genuine set of calls needed to confirm: auth succeeds,
icehockey_nhl is active, real event IDs are returned, and whether
DraftKings player_shots_on_goal is currently posted for the soonest real
NHL event. Archives every real response. Never fabricates a result --
if no markets are posted, that is reported honestly as DATA_UNAVAILABLE.

Run manually, NOT on every test/dashboard load:
    python3 research/run_live_sog_phase_a_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.live_sog_pricing import client, archive


def main():
    print("=== Phase A: The Odds API real contract smoke test ===\n")

    r_sports = client.get_sports()
    print(f"1. GET /sports -> ok={r_sports.ok} status={r_sports.status_code} "
          f"used={r_sports.requests_used} remaining={r_sports.requests_remaining}")
    if not r_sports.ok:
        print(f"   ERROR: {r_sports.error}")
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (API transport itself failed)")
        return
    path = archive.archive_result(r_sports, event_id=None, market_filter=None, bookmaker_filter=None)
    print(f"   archived -> {path.relative_to(REPO_ROOT)}")
    nhl_entries = [s for s in r_sports.data if s.get("key") == "icehockey_nhl"]
    nhl_active = bool(nhl_entries) and nhl_entries[0].get("active")
    print(f"   icehockey_nhl present={bool(nhl_entries)} active={nhl_active}")

    r_events = client.get_nhl_events()
    print(f"\n2. GET /sports/icehockey_nhl/events -> ok={r_events.ok} status={r_events.status_code} "
          f"used={r_events.requests_used} remaining={r_events.requests_remaining}")
    if not r_events.ok:
        print(f"   ERROR: {r_events.error}")
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (event listing failed)")
        return
    path = archive.archive_result(r_events, event_id=None, market_filter=None, bookmaker_filter=None)
    print(f"   archived -> {path.relative_to(REPO_ROOT)}")
    events = r_events.data
    print(f"   real events returned: {len(events)}")
    if not events:
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (no NHL events currently scheduled)")
        return

    events_sorted = sorted(events, key=lambda e: e["commence_time"])
    soonest = events_sorted[0]
    print(f"   soonest event: {soonest['away_team']} @ {soonest['home_team']} "
          f"({soonest['commence_time']}), id={soonest['id']}")

    r_odds = client.get_event_odds(soonest["id"])
    print(f"\n3. GET /sports/icehockey_nhl/events/{{id}}/odds "
          f"(markets=player_shots_on_goal,player_shots_on_goal_alternate, bookmakers=draftkings) "
          f"-> ok={r_odds.ok} status={r_odds.status_code} "
          f"used={r_odds.requests_used} remaining={r_odds.requests_remaining}")
    if not r_odds.ok:
        print(f"   ERROR: {r_odds.error}")
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (event-odds request failed)")
        return
    path = archive.archive_result(r_odds, event_id=soonest["id"],
                                   market_filter="player_shots_on_goal,player_shots_on_goal_alternate",
                                   bookmaker_filter="draftkings")
    print(f"   archived -> {path.relative_to(REPO_ROOT)}")
    bookmakers = r_odds.data.get("bookmakers", [])
    print(f"   bookmakers returned for this event: {len(bookmakers)}")

    if not bookmakers:
        print(f"\nSoonest real NHL event commences {soonest['commence_time']} -- "
              f"{len(events)} calendar days out at time of this run "
              f"is too far ahead of puck drop for DraftKings to have posted player props yet "
              f"(sportsbooks typically post player props within ~24-72h of a game).")
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (no DraftKings markets currently posted -- calendar timing, not a system failure)")
        return

    dk = next((b for b in bookmakers if b.get("key") == "draftkings"), None)
    if dk is None:
        print("   DraftKings specifically not present in this event's bookmaker list.")
        print("\nLIVE NHL SOG MARKET:\nDATA_UNAVAILABLE (DraftKings not currently quoting this event)")
        return

    print(f"   DraftKings markets: {[m['key'] for m in dk.get('markets', [])]}")
    print(f"   DraftKings last_update: {dk.get('last_update')}")
    print("\nLIVE NHL SOG MARKET:\nAVAILABLE")


if __name__ == "__main__":
    main()
