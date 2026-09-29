"""
Real Today state (Real Product Bridge block, 2026-09-29): the ONE canonical
real-data structure dashboard/pages/21_Today.py's primary sections read from
-- today's actual NHL games, real Top Conviction, and the real cross-game
daily parlay result. Writes no new model math, no new decision rule, no new
eligibility system: every piece here is research/real_market_parlay/
real_slate_adapter.py + engine.py (already real, already tested) or the
already-real prospective-ledger recommendations
(dashboard/real_recommendations_view.py), read-only.

REAL OR EMPTY, never REAL OR FAKE: every accessor here returns an honest
empty result (empty list, or a named NOT_* status) when no real data
qualifies -- it never substitutes a demo/simulated row. Demo data is not
imported by this module at all.
"""
from __future__ import annotations

import datetime as dt

NO_QUALIFYING_REAL_OPPORTUNITIES = "NO_QUALIFYING_REAL_OPPORTUNITIES"
NO_QUALIFYING_REAL_PARLAY = "NO_QUALIFYING_REAL_PARLAY"


def _today_real_games(conn, now: dt.datetime) -> list[dict]:
    """Today's (UTC calendar date) real NHL games -- the canonical real
    slate, never a simulated one. `strongest_leg` is filled in by
    build_real_today_state() once real eligible legs are known, so a game
    card can show its own single best real opportunity without a second
    query."""
    today = now.date().isoformat()
    rows = conn.execute(
        "SELECT game_id, home_team, away_team, game_state, scheduled_start_utc FROM games "
        "WHERE game_date = ? ORDER BY scheduled_start_utc", (today,)).fetchall()
    return [{"game_id": str(r["game_id"]), "home_team": r["home_team"], "away_team": r["away_team"],
             "game_state": r["game_state"], "scheduled_start_utc": r["scheduled_start_utc"],
             "strongest_leg": None} for r in rows]


def _leg_summary(leg) -> dict:
    return {"market_family": leg.market_family, "participant_name": leg.participant_name,
            "threshold": leg.threshold, "side": leg.side, "american_price": leg.american_price,
            "conservative_probability": round(leg.conservative_probability, 4),
            "game_id": leg.game_id, "sportsbook": leg.sportsbook, "captured_at_utc": leg.captured_at_utc,
            "data_label": "REAL MARKET DATA"}


def real_top_conviction(eligible_legs: list, *, max_n: int = 5) -> list[dict]:
    """Ranks REAL eligible legs (from research/real_market_parlay's own
    leg_is_eligible() gate -- the same one the real parlay engine uses, so
    Top Conviction and the parlay pool are never two contradictory
    definitions of "eligible") by conservative_probability. This is a
    DIFFERENT ranking rule from dashboard/conviction.py::top_conviction()'s
    own BET-grade two-sided edge (Part 12) deliberately: PLAYER_SOG_ALTERNATE
    is a real, certified, but ONE-SIDED market, so no two-sided no-vig edge
    can ever be computed for it (research/generic_prop_pricing/evaluator.py's
    own Part 42 rule) -- ranking by the model's own real conservative
    probability is the honest, available signal, not a workaround. A real
    two-sided MONEYLINE leg is included on equal footing since
    conservative_probability is directly comparable either way."""
    ranked = sorted(eligible_legs, key=lambda l: l.conservative_probability, reverse=True)
    return [_leg_summary(l) for l in ranked[:max_n]]


def build_real_today_state(nhl_conn, *, now: dt.datetime | None = None,
                           sog_archive_payloads: list[dict] | None = None) -> dict:
    """The one real state dashboard/pages/21_Today.py's primary sections all
    read from. `sog_archive_payloads` is injectable for tests/snapshot
    building (never a new sportsbook API call is made here -- the caller
    sources them from already-retained archives, exactly like research/
    real_market_parlay/manual_real_slate_exercise.py already does)."""
    from operational.real_prop_orchestrator import _recent_archive_payloads
    from research.live_sog_pricing import market_parser
    from research.real_market_parlay import engine as rmp
    from research.real_market_parlay import real_slate_adapter as adapter

    now = now or dt.datetime.now(dt.timezone.utc)
    games = _today_real_games(nhl_conn, now)

    moneyline_legs, moneyline_excluded = adapter.moneyline_candidate_legs(nhl_conn, now=now)
    if sog_archive_payloads is None:
        sog_archive_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0,
                                                        now=now)
    sog_legs, sog_excluded = adapter.sog_alternate_candidate_legs(nhl_conn, sog_archive_payloads, now=now)

    all_legs = moneyline_legs + sog_legs
    all_excluded = moneyline_excluded + sog_excluded

    by_game: dict[str, list] = {}
    for leg in all_legs:
        by_game.setdefault(leg.game_id, []).append(leg)
    for game in games:
        legs_here = by_game.get(game["game_id"], [])
        if legs_here:
            best = max(legs_here, key=lambda l: l.conservative_probability)
            game["strongest_leg"] = _leg_summary(best)

    top_conviction = real_top_conviction(all_legs)
    parlay_result = rmp.build_real_market_parlay(all_legs)
    parlay_view = {"status": parlay_result["status"], "reason": parlay_result.get("reason")}
    if parlay_result["status"] == "QUALIFIED":
        combo = parlay_result["combo"]
        parlay_view["combo"] = {
            "recommended_legs": parlay_result["recommended_legs"],
            "legs": [_leg_summary(l) for l in combo.legs],
            "joint_probability": round(combo.joint_probability, 4),
            "fair_combo_price": round(combo.fair_combo_price, 1),
            "estimated_combo_price": round(combo.estimated_combo_price, 1),
            "offered_parlay_price": combo.offered_parlay_price,
            "data_label": "REAL MARKET DATA",
        }

    from collections import Counter
    exclusion_reasons = Counter(e["reason"].split("(")[0].split(":")[0].strip() for e in all_excluded)

    return {
        "generated_at_utc": now.isoformat(),
        "provenance": "REAL MARKET DATA",
        "games": games,
        "eligible_leg_count": len(all_legs),
        "top_conviction": top_conviction if top_conviction else NO_QUALIFYING_REAL_OPPORTUNITIES,
        "parlay": parlay_view,
        "excluded_count": len(all_excluded),
        "excluded_by_reason": dict(exclusion_reasons),
    }
