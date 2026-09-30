"""
Real Player Props view (Platform Recovery block, 2026-09-29): the ONE
canonical real-data structure dashboard/pages/26_Player_Props.py's
primary/default view reads from. Every real, currently-eligible
PLAYER_SOG_ALTERNATE leg -- real DraftKings price, real captured/freshness
state, real model probability -- REAL OR EMPTY, never backfilled with a
simulated substitute.

The only real production prop family today is PLAYER_SOG_ALTERNATE
(research/real_market_parlay/real_slate_adapter.py's own certified real
path, the same one dashboard/real_today_view.py's Today bridge already
uses) -- other market families (Goals, Assists, Points, Blocked Shots)
stay demo-only until they independently earn their own real payload
contract, exactly like the real parlay engine's own ALLOWED_MARKET_FAMILIES
allowlist. This module never imports the demo data module.

Unlike real_today_view.py (which surfaces only each game's single
STRONGEST leg), this module returns EVERY real eligible leg -- Player
Props is a listing page, not a summary card.
"""
from __future__ import annotations

import datetime as dt


def _team_and_opponent(conn, game_id: str, player_id: str) -> tuple[str | None, str | None]:
    """Real team/opponent for a real SOG leg, derived the same way
    research/real_market_parlay/real_slate_adapter.py's own
    sog_alternate_candidate_legs() determines a player's current real team
    internally (most_recent_team from the real player-history index) --
    never re-invented, just re-run here for display since ParlayLeg itself
    deliberately carries no team/opponent field (the parlay engine's own
    minimal eligibility interface)."""
    from research.live_sog_pricing import player_mapping
    from research.player_sog import features as pf

    row = conn.execute("SELECT home_team, away_team FROM games WHERE game_id = ?", (game_id,)).fetchone()
    if row is None:
        return None, None
    home, away = row["home_team"], row["away_team"]
    sog_rows = pf.load_sog_corpus()
    player_index = player_mapping.build_player_index(sog_rows)
    for candidates in player_index.values():
        for c in candidates:
            if c["player_id"] == player_id:
                team = c["most_recent_team"]
                if team == home:
                    return home, away
                if team == away:
                    return away, home
    return None, None


def _real_prop_row(conn, leg) -> dict:
    from pricing import odds_math

    implied_probability = odds_math.american_to_prob(leg.american_price)
    fair_odds = odds_math.prob_to_american(leg.conservative_probability)
    team, opponent = _team_and_opponent(conn, leg.game_id, leg.participant_id)
    return {
        "player_id": leg.participant_id, "player": leg.participant_name, "team": team, "opponent": opponent,
        "market": leg.market_family, "threshold": leg.threshold, "side": leg.side,
        "dk_price": leg.american_price, "conservative_probability": round(leg.conservative_probability, 4),
        "fair_odds": round(fair_odds, 1), "implied_probability": round(implied_probability, 4),
        "edge": round(leg.conservative_probability - implied_probability, 4),
        "captured_at_utc": leg.captured_at_utc, "sportsbook": leg.sportsbook,
        "game_id": leg.game_id, "status": "ELIGIBLE", "data_label": "REAL MARKET DATA",
    }


def build_real_player_props_state(nhl_conn, *, now: dt.datetime | None = None,
                                   sog_archive_payloads: list[dict] | None = None) -> dict:
    """The one real state dashboard/pages/26_Player_Props.py's default
    view reads from. `sog_archive_payloads` is injectable for tests;
    defaults to the caller's own already-retained real archives, exactly
    like real_today_view.py's identical pattern."""
    from operational.real_prop_orchestrator import _recent_archive_payloads
    from research.live_sog_pricing import market_parser
    from research.real_market_parlay import real_slate_adapter as adapter

    now = now or dt.datetime.now(dt.timezone.utc)
    if sog_archive_payloads is None:
        sog_archive_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0,
                                                        now=now)
    legs, excluded = adapter.sog_alternate_candidate_legs(nhl_conn, sog_archive_payloads, now=now)

    from collections import Counter
    exclusion_reasons = Counter(e["reason"].split("(")[0].split(":")[0].strip() for e in excluded)

    return {
        "generated_at_utc": now.isoformat(),
        "provenance": "REAL MARKET DATA",
        "rows": [_real_prop_row(nhl_conn, l) for l in legs],
        "excluded_count": len(excluded),
        "excluded_by_reason": dict(exclusion_reasons),
    }
