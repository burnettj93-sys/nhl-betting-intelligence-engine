"""
View logic for the Goalie Intelligence (Research) dashboard page.
STARTER INTELLIGENCE here is explicitly RESEARCH / HISTORICAL INFERENCE
(Stage 1 only) -- there is no live external source integrated (see
GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md Sections A-E), so every
probability shown is PROJECTED, reconstructed from real historical
rotation data, never CONFIRMED. This module must never present a
historical actual starter as if it were a pregame confirmation -- see
tests/test_dashboard.py's goalie-intelligence checks.
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely
from research.goalie_intelligence import features as gf
from research.goalie_intelligence import model as gm

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "goalie_intelligence_results.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_PATH)


def project_starters_for_team_date(all_rows: list[dict], weights: list[float],
                                    team: str, prediction_game_date: str, season: int) -> dict:
    """Returns a PROJECTED (never CONFIRMED) starter-probability
    breakdown for `team` as of `prediction_game_date`, using ONLY
    strictly-prior real data (STRICT PRIOR-GAME-DATE, same as every
    feature in research/goalie_intelligence/features.py)."""
    history = gf.team_history_as_of(all_rows, team, prediction_game_date)
    if len(history) < 1:
        return {"status": "INSUFFICIENT_HISTORY", "candidates": []}

    candidates = gf.eligible_goalies(history, window=20)
    if not candidates:
        return {"status": "INSUFFICIENT_HISTORY", "candidates": []}

    is_b2b = gf.team_back_to_back(history, prediction_game_date)
    feature_vectors = [gm.build_feature_vector(history, g, season, prediction_game_date, is_b2b)
                        for g in candidates]
    probs = gm.score_candidates(weights, feature_vectors)

    ranked = sorted(zip(candidates, probs), key=lambda cp: -cp[1])
    top_prob = ranked[0][1]
    confidence = "HIGH" if top_prob >= 0.70 else ("MEDIUM" if top_prob >= 0.50 else "LOW")

    drivers = []
    top_goalie = ranked[0][0]
    if gf.previous_game_starter(history) == top_goalie:
        drivers.append(f"started the team's most recent prior game")
    streak = gf.consecutive_start_count(history)
    if gf.previous_game_starter(history) == top_goalie and streak > 1:
        drivers.append(f"on a {streak}-game consecutive start streak")
    share10 = gf.recent_start_share(history, top_goalie, 10)
    if share10 is not None:
        drivers.append(f"started {share10*100:.0f}% of the team's last 10 games")
    if is_b2b and not gf.goalie_played_previous_night(history, top_goalie, prediction_game_date):
        drivers.append("team is on a back-to-back; this goalie did not play last night")
    elif is_b2b:
        drivers.append("team is on a back-to-back (played previous night -- unusual to start again)")

    return {
        "status": "PROJECTED",
        "is_back_to_back": is_b2b,
        "candidates": [{"goalie_id": g, "probability": p} for g, p in ranked],
        "top_goalie_id": top_goalie,
        "top_probability": top_prob,
        "confidence": confidence,
        "drivers": drivers,
        "history_games": len(history),
    }
