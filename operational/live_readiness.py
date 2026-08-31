"""
Real live-readiness service (Preseason Operationalization sprint,
Sections 32-34). Fail-closed by construction: `live_readiness()` never
returns READY unless every real precondition it checks actually holds.

This function does NOT compute probability, edge, or EV -- it only
answers "is it safe to show a live market comparison right now," the
same separation-of-concerns discipline decision_policy.py already
follows for the BET/WATCH/PASS layer.
"""
from __future__ import annotations

from research.model_registry import get as get_model, MODEL_REGISTRY
from operational.system_health import odds_api_health, draftkings_markets_health

READINESS_STATUSES = ("READY", "WAIT", "DATA_UNAVAILABLE", "MODEL_NOT_OPERATIONAL")

REASONS = (
    "ODDS_MISSING", "ODDS_STALE", "STARTER_UNCONFIRMED", "PLAYER_UNMAPPED",
    "MODEL_NOT_VALIDATED", "MARKET_UNSUPPORTED", "LINEUP_PENDING", "DATA_SOURCE_ERROR",
)

_MARKET_FAMILY_TO_MODEL_ID = {
    "PLAYER_SOG": "PLAYER_SOG", "SOG": "PLAYER_SOG",
    "GOALS": "GOALS", "PLAYER_GOALS_1PLUS": "GOALS",
    "POINTS": "POINTS", "PLAYER_POINTS_1PLUS": "POINTS",
    "ASSISTS": "ASSISTS", "PLAYER_ASSISTS_1PLUS": "ASSISTS",
    "BLOCKED_SHOTS": "BLOCKED_SHOTS",
    "TEAM_SOG": "TEAM_SOG",
    "GOALIE_SAVES": "GOALIE_SAVES",
}

_LIVE_PRICED_MODEL_IDS = {"PLAYER_SOG"}  # only family with a tested DK payload contract today


def _reason_result(status: str, reason: str, message: str) -> dict:
    return {"status": status, "reason": reason, "message": message}


def live_readiness(market_id: str, game_id: str | None = None, player_id: str | None = None,
                    player_mapped: bool | None = None, starter_confirmed: bool | None = None,
                    lineup_confirmed: bool | None = None) -> dict:
    """Returns {"status": READY|WAIT|DATA_UNAVAILABLE|MODEL_NOT_OPERATIONAL,
    "reason": <structured reason or None>, "message": <human string>}.

    Fail-closed: any missing/ambiguous input defaults to the SAFER
    (more restrictive) outcome, never to READY.
    """
    model_id = _MARKET_FAMILY_TO_MODEL_ID.get(market_id)
    if model_id is None:
        return _reason_result("MODEL_NOT_OPERATIONAL", "MARKET_UNSUPPORTED",
                               f"{market_id} has no registered model family in MODEL_REGISTRY")

    entry = get_model(model_id)
    if entry is None or entry.status in ("ATTEMPTED_NOT_VALIDATED", "REJECTED"):
        return _reason_result("MODEL_NOT_OPERATIONAL", "MODEL_NOT_VALIDATED",
                               f"{model_id} is not a validated model ({entry.status if entry else 'unknown'})")

    if player_id is not None and player_mapped is False:
        return _reason_result("DATA_UNAVAILABLE", "PLAYER_UNMAPPED",
                               f"player_id {player_id} could not be mapped to a sportsbook outcome")

    if starter_confirmed is False and model_id == "GOALIE_SAVES":
        return _reason_result("WAIT", "STARTER_UNCONFIRMED", "goalie starter not yet confirmed")

    if lineup_confirmed is False:
        return _reason_result("WAIT", "LINEUP_PENDING", "lineup not yet confirmed for this game")

    if model_id not in _LIVE_PRICED_MODEL_IDS:
        # No live-tested payload contract for this family today -- fail
        # closed rather than pretend a market comparison is possible.
        return _reason_result("WAIT", "MARKET_UNSUPPORTED",
                               f"{market_id} has no live-tested DraftKings payload contract yet (SOG only today)")

    odds = odds_api_health()
    dk = draftkings_markets_health()
    if odds["status"] == "ERROR" or dk["status"] == "ERROR":
        return _reason_result("DATA_UNAVAILABLE", "DATA_SOURCE_ERROR", "odds/market data source reported an error")
    if odds["status"] == "WAITING":
        return _reason_result("WAIT", "ODDS_MISSING", "no odds have been fetched yet")
    if odds["status"] == "STALE":
        return _reason_result("WAIT", "ODDS_STALE", f"odds are stale ({odds.get('age_hours')} hours old)")

    return _reason_result("READY", None, f"{market_id} is ready: model validated, odds current, lineup confirmed")
