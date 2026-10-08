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

Void/push policy: see docs/PAPER_SETTLEMENT_RULES.md. In short, a player or
team who did not appear in an otherwise-FINAL game (PLAYER_DID_NOT_DRESS /
GOALIE_DID_NOT_PLAY / TEAM_DID_NOT_PLAY) makes THAT LEG a push. A single
ticket with a push leg is VOID (stake refunded). A parlay with a push leg is
settled on its remaining legs at their original prices (the usual sportsbook
"reduced parlay" treatment): any remaining LOSS -> LOSS, all remaining WIN ->
WIN at the repriced odds, every leg void -> VOID. The ticket is never voided
as a whole just because one leg pushed. Every OTHER fail-closed resolver
status (a data/coverage gap such as TEAM_SOG_NOT_INGESTED or
UNSUPPORTED_SETTLEMENT_MARKET) leaves the ticket UNRESOLVED: a real answer may
exist but this system cannot determine it, which is a different and more
concerning case than a genuine did-not-play push, and no outcome is guessed.
UNRESOLVED tickets are re-examined on later runs and keep their stake in open
exposure until they resolve.

Never runs on a game that has not gone FINAL: resolve_prediction() itself
returns GAME_NOT_FINAL in that case, and this driver treats it as "not yet
settleable this run" -- it is skipped, not forced to any terminal state,
and will be reconsidered on the next run once the real game concludes.
"""
from __future__ import annotations

import json

from operational import outcome_resolver as resolver

PENDING_STILL_WAITING = "PENDING_STILL_WAITING"

# The did-not-play void and parlay-reduction conventions below are modelled on common sportsbook practice but have
# NOT been checked against DraftKings Ontario's published house rules (the official page was unreachable: HTTP 403;
# secondary sources only). Until a person verifies them and flips this flag, any ticket whose result depends on a
# void/reduction stays UNRESOLVED with its stake open, and the provisional outcome is only recorded, not applied.
VOID_RULES_VERIFIED = False

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
    underlying stat, never which raw market key priced it.

    Standard SOG/Saves Certification block (2026-10-01): PLAYER_SOG and
    GOALIE_SAVES added -- real bug caught while wiring real_slate_adapter.py::
    sog_standard_candidate_legs()/goalie_saves_candidate_legs() into the
    paper trader: without these two cases, any real-market parlay containing
    one of these legs would return None here, fall into
    resolver.UNSUPPORTED_SETTLEMENT_MARKET for that leg, and the whole combo
    would be stuck UNRESOLVED forever -- never reaching a real WIN/LOSS,
    directly defeating the daily postmortem's "find out why when something
    doesn't hit" purpose. Both resolve_prediction()'s own dispatch
    (operational/outcome_resolver.py) already recognizes these exact prefixes
    -- this was purely a missing translation here, not a missing resolver."""
    family = leg.get("market_family")
    if family == "MONEYLINE":
        return "MONEYLINE"
    if family == "PLAYER_SOG_ALTERNATE":
        threshold = leg.get("threshold")
        return f"PLAYER_SOG_{threshold}PLUS" if threshold is not None else None
    if family == "PLAYER_SOG":
        threshold = leg.get("threshold")
        return f"PLAYER_SOG_{threshold}PLUS" if threshold is not None else None
    if family == "GOALIE_SAVES":
        threshold = leg.get("threshold")
        return f"GOALIE_SAVES_{threshold}PLUS" if threshold is not None else None
    if family == "PLAYER_POINTS":
        threshold = leg.get("threshold")
        return f"PLAYER_POINTS_{threshold}PLUS" if threshold is not None else None
    if family == "PLAYER_GOALS":
        threshold = leg.get("threshold")
        return f"PLAYER_GOALS_{threshold}PLUS" if threshold is not None else None
    return None


def resolve_straight_bet(nhl_conn, bet: dict) -> dict:
    """One non-combo paper_bets row. `bet["event_id"]` carries the internal
    nhl.db game_id (the real, established convention -- see
    operational/real_recommendation_orchestrator.py::_process_report())."""
    prediction = _prediction_dict(
        market_id=bet.get("market_id"), threshold=bet.get("threshold"), side=bet.get("side"),
        game_id=bet.get("event_id"), player_id=bet.get("player_id"), team=bet.get("team"))
    return resolver.resolve_prediction(nhl_conn, prediction)


def _decimal_from_american(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def _american_from_decimal(decimal_odds: float) -> float:
    if decimal_odds >= 2.0:
        return round((decimal_odds - 1.0) * 100.0, 2)
    return round(-100.0 / (decimal_odds - 1.0), 2)


def _leg_outcome(result: dict) -> str:
    status = result["status"]
    if status == resolver.RESOLVED:
        return "WIN" if result["outcome_hit"] else "LOSS"
    if status == resolver.GAME_NOT_FINAL:
        return "PENDING"
    if status in _PUSH_STATUSES:
        return "VOID"
    return "UNRESOLVED"


def resolve_combo_bet(nhl_conn, bet: dict) -> dict:
    """One combo (is_combo=1) paper_bets row. Every leg in legs_json is
    resolved through the same resolve_prediction() dispatch, then the ticket
    is settled as a parlay (rules in the module docstring and
    docs/PAPER_SETTLEMENT_RULES.md):
      any LOSS leg              -> LOSS (even if other games are still live)
      any leg not yet final     -> PENDING_STILL_WAITING
      any leg UNRESOLVED        -> UNRESOLVED (no guess; the ticket stays open)
      every leg VOID            -> VOID (refund)
      some legs VOID, rest WIN  -> WIN, repriced on the remaining legs
      all legs WIN              -> WIN at the recorded entry price
    A WIN with voided legs carries `settled_odds` (the repriced American
    odds from each remaining leg's own recorded price) and `voided_legs`."""
    legs = json.loads(bet.get("legs_json") or "[]")
    if not legs:
        return {"status": resolver.UNSUPPORTED_SETTLEMENT_MARKET, "leg_results": []}

    leg_results = []
    for leg in legs:
        market_id = _leg_settlement_market_id(leg)
        if market_id is None:
            leg_results.append({"status": resolver.UNSUPPORTED_SETTLEMENT_MARKET, "leg": leg,
                                "outcome": "UNRESOLVED"})
            continue
        player_id = leg.get("participant_id") if leg.get("market_family") != "MONEYLINE" else None
        team = leg.get("participant_id") if leg.get("market_family") == "MONEYLINE" else None
        prediction = _prediction_dict(market_id=market_id, threshold=leg.get("threshold"),
                                       side=leg.get("side"), game_id=leg.get("game_id"),
                                       player_id=player_id, team=team)
        result = resolver.resolve_prediction(nhl_conn, prediction)
        leg_results.append({**result, "leg": leg, "outcome": _leg_outcome(result)})

    outcomes = [r["outcome"] for r in leg_results]
    detail = {"leg_results": leg_results, "leg_outcomes": outcomes}
    if "LOSS" in outcomes:
        return {"status": "LOSS", **detail}
    if "PENDING" in outcomes:
        return {"status": PENDING_STILL_WAITING, **detail}
    if "UNRESOLVED" in outcomes:
        return {"status": "UNRESOLVED", **detail}
    if all(o == "VOID" for o in outcomes):
        if not VOID_RULES_VERIFIED:
            return {"status": "UNRESOLVED", "reason": "VOID_RULE_UNVERIFIED",
                    "provisional": {"status": "VOID"}, **detail}
        return {"status": "VOID", **detail}
    voided = [r["leg"] for r in leg_results if r["outcome"] == "VOID"]
    if voided:
        decimal_odds = 1.0
        for r in leg_results:
            if r["outcome"] == "WIN":
                decimal_odds *= _decimal_from_american(r["leg"]["american_price"])
        repriced = _american_from_decimal(decimal_odds)
        if not VOID_RULES_VERIFIED:
            return {"status": "UNRESOLVED", "reason": "VOID_RULE_UNVERIFIED",
                    "provisional": {"status": "WIN", "settled_odds": repriced, "voided_legs": voided}, **detail}
        return {"status": "WIN", "settled_odds": repriced, "voided_legs": voided, **detail}
    return {"status": "WIN", **detail}


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
    candidates = pb.find_unresolved_past_event_bets(bankroll_conn, track=track, include_unresolved=True)
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
        elif status in _PUSH_STATUSES:  # a single ticket whose player/team did not play: refund
            final_status = "VOID" if VOID_RULES_VERIFIED else "UNRESOLVED"
        else:
            final_status = "UNRESOLVED"
        if final_status == "UNRESOLVED" and bet["result_status"] == "UNRESOLVED":
            summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": "STILL_UNRESOLVED"})
            continue
        notes = None
        if result.get("reason") == "VOID_RULE_UNVERIFIED" or (status in _PUSH_STATUSES and not VOID_RULES_VERIFIED):
            notes = ("A leg did not play. DraftKings Ontario's void/parlay-reduction rule is unverified, so the ticket "
                     "stays open (UNRESOLVED); provisional outcome: "
                     + json.dumps(result.get("provisional") or {"status": "VOID"}, default=str))
        elif result.get("voided_legs"):
            names = ", ".join(str(l.get("participant_name")) for l in result["voided_legs"])
            notes = f"Parlay repriced: voided leg(s) {names} removed; settled at {result['settled_odds']:+.0f}"
        detail = ({"leg_results": result["leg_results"], "settled_odds": result.get("settled_odds"),
                   "provisional": result.get("provisional")}
                  if "leg_results" in result else {"resolver": result})
        pb.settle_paper_bet(bankroll_conn, bet["paper_bet_id"], final_status,
                            settled_odds=result.get("settled_odds"), notes=notes,
                            settlement_json=json.dumps(detail, default=str))
        summary["settled"] += 1
        summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": final_status,
                                    "resolver_detail": result})
    return summary
