"""
Paper Bet Settlement Driver (P0 block, 2026-09-29). Connects two already-
real, already-tested pieces that had never been wired together:
  - operational/outcome_resolver.py::resolve_prediction() -- the single,
    real, already-tested dispatch (MONEYLINE, per-player-stat families,
    GOALIE_SAVES, TEAM_SOG, PLAYER_BLOCKS) this project's prospective-ledger
    settlement already uses.
  - operational/paper_bankroll.py::find_unresolved_past_event_bets() /
    settle_paper_bet() -- real, tested, but (confirmed by grep across this
    entire codebase) never called from any production driver until now.

Never invents a settlement rule of its own: every WIN/LOSS determination
comes from resolve_prediction()'s own resolver functions, reused exactly as
they already are. This module's only real job is the same kind of
translation research/real_market_parlay/real_slate_adapter.py does for
sourcing legs -- here, translating one paper_bets row (or one parlay leg
inside its legs_json) into the `prediction` dict shape resolve_prediction()
already expects.

Void/push policy (documented, not invented ad hoc): a player or team who
did not appear in an otherwise-FINAL game (PLAYER_DID_NOT_DRESS /
GOALIE_DID_NOT_PLAY / TEAM_DID_NOT_PLAY) is the single, universal real
sportsbook convention for a push -- refunded, never scored as a loss. This
driver applies exactly that one rule and no other. A combo with a genuine
push leg is VOIDED IN FULL (never partially "reduced" to fewer legs) --
real sportsbooks vary widely in their reduced-multiple math, and this
project has never documented which convention it wants, so the
conservative, unambiguous choice (void the whole ticket) is used instead of
guessing a specific book's reduction formula. Every OTHER fail-closed
resolver status (a genuine data/coverage gap -- e.g. TEAM_SOG_NOT_INGESTED,
HITS_UNVERIFIED_VS_MODEL_SOURCE, UNSUPPORTED_SETTLEMENT_MARKET) settles as
UNRESOLVED, never silently voided or guessed -- that status means "a real
answer may exist but this system cannot currently determine it," which is
a materially different, more concerning case than a genuine did-not-play
push and must stay visible as such.

Never runs on a game that has not gone FINAL: resolve_prediction() itself
returns GAME_NOT_FINAL in that case, and this driver treats it as "not yet
settleable this run" -- it is skipped, not forced to any terminal state,
and will be reconsidered on the next run once the real game concludes.
"""
from __future__ import annotations

import json

from operational import outcome_resolver as resolver

PENDING_STILL_WAITING = "PENDING_STILL_WAITING"

# Fail-closed resolver statuses that mean "this specific leg's real-world
# event genuinely did not occur" -- the one, universal real push/void case.
_PUSH_STATUSES = frozenset({
    resolver.PLAYER_DID_NOT_DRESS, resolver.GOALIE_DID_NOT_PLAY, resolver.TEAM_DID_NOT_PLAY,
})


def _prediction_dict(*, market_id, threshold, side, game_id, player_id=None, team=None) -> dict:
    return {"market_id": market_id, "threshold": threshold, "side": side, "game_id": game_id,
            "player_id": player_id, "team": team}


def _leg_settlement_market_id(leg: dict) -> str | None:
    """Translates a ParlayLeg-shaped dict's own market_family (a contract-
    specific id, e.g. PLAYER_SOG_ALTERNATE) into the settlement-dispatch id
    resolve_prediction() actually recognizes (a stat-family prefix, e.g.
    PLAYER_SOG_4PLUS) -- contract verification and settlement dispatch are
    deliberately different concerns (see provider_adapter.VERIFIED_CONTRACTS's
    own comment on why PLAYER_SOG_ALTERNATE must never be conflated with the
    bare PLAYER_SOG family elsewhere); settlement only cares about the
    underlying stat, never which raw market key priced it."""
    family = leg.get("market_family")
    if family == "MONEYLINE":
        return "MONEYLINE"
    if family == "PLAYER_SOG_ALTERNATE":
        threshold = leg.get("threshold")
        return f"PLAYER_SOG_{threshold}PLUS" if threshold is not None else None
    return None


def resolve_straight_bet(nhl_conn, bet: dict) -> dict:
    """One non-combo paper_bets row. `bet["event_id"]` carries the internal
    nhl.db game_id (the real, established convention -- see
    operational/real_recommendation_orchestrator.py::_process_report())."""
    prediction = _prediction_dict(
        market_id=bet.get("market_id"), threshold=bet.get("threshold"), side=bet.get("side"),
        game_id=bet.get("event_id"), player_id=bet.get("player_id"), team=bet.get("team"))
    return resolver.resolve_prediction(nhl_conn, prediction)


def resolve_combo_bet(nhl_conn, bet: dict) -> dict:
    """One combo (is_combo=1) paper_bets row -- resolves every real leg in
    legs_json (research/real_market_parlay/engine.py's own leg snapshot
    shape) via the SAME resolve_prediction() dispatch, then aggregates per
    Step 25's rule: any real LOSS -> LOSS (checked first, short-circuits
    regardless of other legs' state); any leg still GAME_NOT_FINAL ->
    the whole combo is PENDING_STILL_WAITING (not yet settleable this run);
    any leg with a genuine data-gap fail-closed status -> UNRESOLVED; any
    leg that's a real push (see module docstring) with no LOSS -> VOID (the
    whole ticket, never partially reduced); otherwise every leg really
    won -> WIN."""
    legs = json.loads(bet.get("legs_json") or "[]")
    if not legs:
        return {"status": resolver.UNSUPPORTED_SETTLEMENT_MARKET, "leg_results": []}

    leg_results = []
    for leg in legs:
        market_id = _leg_settlement_market_id(leg)
        if market_id is None:
            leg_results.append({"status": resolver.UNSUPPORTED_SETTLEMENT_MARKET, "leg": leg})
            continue
        player_id = leg.get("participant_id") if leg.get("market_family") != "MONEYLINE" else None
        team = leg.get("participant_id") if leg.get("market_family") == "MONEYLINE" else None
        prediction = _prediction_dict(market_id=market_id, threshold=leg.get("threshold"),
                                       side=leg.get("side"), game_id=leg.get("game_id"),
                                       player_id=player_id, team=team)
        result = resolver.resolve_prediction(nhl_conn, prediction)
        leg_results.append({**result, "leg": leg})

    statuses = [r["status"] for r in leg_results]
    if any(s == resolver.RESOLVED and not r["outcome_hit"] for r, s in zip(leg_results, statuses)):
        return {"status": "LOSS", "leg_results": leg_results}
    if resolver.GAME_NOT_FINAL in statuses:
        return {"status": PENDING_STILL_WAITING, "leg_results": leg_results}
    if any(s in _PUSH_STATUSES for s in statuses):
        return {"status": "VOID", "leg_results": leg_results}
    if any(s != resolver.RESOLVED for s in statuses):
        return {"status": "UNRESOLVED", "leg_results": leg_results}
    return {"status": "WIN", "leg_results": leg_results}


def settle_due_bets(bankroll_conn, nhl_conn, *, track: str | None = None) -> dict:
    """The real, narrow driver entry point. Finds every PENDING bet whose
    event has started (paper_bankroll.find_unresolved_past_event_bets(),
    reused verbatim), resolves each via the real resolvers above, and
    calls paper_bankroll.settle_paper_bet() only for a genuinely reached
    terminal state (WIN/LOSS/VOID/UNRESOLVED) -- a bet whose game has not
    gone FINAL yet is left PENDING and simply skipped this run, never
    forced. Manual invocation only in this block -- not wired into any
    scheduler."""
    from operational import paper_bankroll as pb

    summary = {"scanned": 0, "settled": 0, "skipped_still_pending": 0, "results": []}
    candidates = pb.find_unresolved_past_event_bets(bankroll_conn, track=track)
    for bet in candidates:
        summary["scanned"] += 1
        result = (resolve_combo_bet(nhl_conn, bet) if bet.get("is_combo")
                   else resolve_straight_bet(nhl_conn, bet))
        status = result["status"]
        if status in (resolver.GAME_NOT_FINAL, PENDING_STILL_WAITING):
            summary["skipped_still_pending"] += 1
            summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": "SKIPPED_STILL_PENDING"})
            continue
        if status == resolver.RESOLVED:
            final_status = "WIN" if result["outcome_hit"] else "LOSS"
        elif status in ("WIN", "LOSS", "VOID"):
            final_status = status
        else:
            final_status = "UNRESOLVED"
        settled = pb.settle_paper_bet(bankroll_conn, bet["paper_bet_id"], final_status)
        summary["settled"] += 1
        summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": final_status,
                                    "resolver_detail": result})
    return summary
