"""
Fantasy category projections: reuses the SAME real, frozen NHL models
the betting engine's own demo layer already calls (Part 26) --
research.context_overlay.prediction_stack.ShadowContextStack for
skaters, the goalie saves engine for goaltenders -- but extracts each
model's own expected-value output (`mu` / `expected_saves`), never the
betting engine's threshold-crossing probabilities or its
BET/WATCH/WAIT/PASS decision (Part 27's own explicit rule: fantasy
value is not a betting decision).

Nothing here imports operational/, research/live_sog_pricing/, or
research/player_props/decision_policy.py -- see fantasy/__init__.py's
isolation rule, enforced by tests/test_fantasy_betting_isolation.py.
"""
from __future__ import annotations

from fantasy.league.scoring import CategoryProjection


def _confidence_for_history_length(n_games: int) -> str:
    """Same low/medium/high banding convention already used elsewhere
    in this engine for 'is there enough real history to trust this' --
    reimplemented locally (not imported) to keep fantasy/ self-contained."""
    if n_games < 5:
        return "LOW"
    if n_games < 15:
        return "MEDIUM"
    return "HIGH"


def project_skater_categories(stack, player_id: str, team: str, opponent: str, date: str,
                               season: int) -> dict[str, CategoryProjection]:
    """Returns {internal_stat: CategoryProjection} for every skater
    category this engine has a real model for (sog, blocks, goals,
    assists, points). A prop with no model output for this player
    (e.g. insufficient real history, or PROJECTED_INACTIVE) is simply
    absent from the result -- callers treat a missing key exactly like
    PROJECTION_NOT_AVAILABLE, never fabricated."""
    projections: dict[str, CategoryProjection] = {}

    for prop, internal_stat in (("sog", "sog"), ("blocks", "blocks")):
        engine = getattr(stack.ctx, prop)
        pred = engine.predict(player_id, team, opponent, date, season)
        if pred is None or pred.get("mu") is None:
            continue
        history = engine.index.history_as_of(player_id, date)
        projections[internal_stat] = CategoryProjection(
            internal_stat=internal_stat, projected_value=pred["mu"],
            confidence=_confidence_for_history_length(len(history)), status="PROJECTED",
        )

    # goals/points go through the real context-overlay stack (the same
    # PIT-safe COLD_AND_TOI_DECLINE adjustment the betting engine uses);
    # assists has no context overlay, so its own marginal engine is
    # queried directly -- identical pattern to dashboard/eligible_bets.py.
    result = stack.predict(player_id, team, opponent, date, season)
    for prop, internal_stat in (("goals", "goals"), ("points", "points")):
        stage = result.get(prop)
        if stage is None or stage.get("mu") is None:
            continue
        history = getattr(stack.ctx, prop).index.history_as_of(player_id, date)
        projections[internal_stat] = CategoryProjection(
            internal_stat=internal_stat, projected_value=stage["mu"],
            confidence=_confidence_for_history_length(len(history)), status="PROJECTED",
        )

    assists_engine = stack.ctx.assists
    assists_pred = assists_engine.predict(player_id, team, opponent, date, season)
    if assists_pred is not None and assists_pred.get("mu") is not None:
        history = assists_engine.index.history_as_of(player_id, date)
        projections["assists"] = CategoryProjection(
            internal_stat="assists", projected_value=assists_pred["mu"],
            confidence=_confidence_for_history_length(len(history)), status="PROJECTED",
        )

    return projections


def project_goalie_categories(expected_saves: float | None, confidence: str) -> dict[str, CategoryProjection]:
    """Returns {internal_stat: CategoryProjection} for goaltender
    categories. Only `goalie_saves` has a real model (Part 15/66 -- W/
    GAA/SV%/Shutouts have no validated projection in this engine; they
    are simply absent here, never fabricated)."""
    if expected_saves is None:
        return {}
    return {"goalie_saves": CategoryProjection(internal_stat="goalie_saves", projected_value=expected_saves,
                                                confidence=confidence, status="PROJECTED")}
