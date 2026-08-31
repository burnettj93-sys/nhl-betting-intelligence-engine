"""
Part 22: confidence-label reuse for the Context Overlay slice.

Do NOT redesign confidence. This module computes the exact same
ingredients (history length, recent TOI CV, recent stat-rate CV,
opponent-window game count, appearance rate) that
research/player_goals/live_projection.py::project_player_goals and
research/player_points/live_projection.py::project_player_points already
compute internally, then calls the SAME shared
research.player_sog.count_models.confidence_score(...) function those
two modules call -- never a second confidence formula.

CRITICAL: this module is used ONLY to obtain the confidence label. It
must NEVER be used to obtain a probability. In particular,
project_player_points internally uses the REJECTED GLM mu (Part 3 of
this slice's own instructions: "Do NOT substitute the rejected Points
GLM") -- its confidence-scoring ingredients are independent of which mu
the probability comes from, so reusing them for confidence only is safe,
but this module intentionally does not import or expose
project_player_points's probability output at all.
"""
from __future__ import annotations

from research.player_goals import features as gf
from research.player_points import features as ptf
from research.player_sog import count_models as cm

OPPONENT_WINDOW_TARGET = 20


def goals_confidence_label(engine, player_id: str, team: str, opponent: str, prediction_game_date: str) -> str | None:
    history = engine.index.history_as_of(player_id, prediction_game_date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in engine.team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
    toi_window = history[-10:]
    goals_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_goals_cv = cm.coefficient_of_variation([r["goals"] for r in goals_window]) if goals_window else None
    opp_hist = gf.opponent_history_as_of(engine.opponent_env, opponent, prediction_game_date)
    recent_team_games = team_sched_prior[-gf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)
    label, _drivers, _risks = cm.confidence_score(
        len(history), recent_toi_cv, recent_goals_cv, len(opp_hist), OPPONENT_WINDOW_TARGET, appearance_rate)
    return label


def points_confidence_label(engine, opponent_env: dict, player_id: str, team: str, opponent: str,
                             prediction_game_date: str) -> str | None:
    # PointsMarginal (research.player_context_state.marginal_provenance) does not build its own
    # opponent_env -- it doesn't need one for the empirical-baseline probability. The confidence
    # ingredients below mirror project_player_points's own internal computation exactly, so
    # opponent_env is built once by the driver (same ptf builder functions, no new formula) and
    # passed in explicitly rather than assumed to live on the frozen engine object.
    history = engine.index.history_as_of(player_id, prediction_game_date)
    if len(history) < 3:
        return None
    team_sched_prior = [g for g in engine.team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
    toi_window = history[-10:]
    points_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_points_cv = cm.coefficient_of_variation([r["points"] for r in points_window]) if points_window else None
    opp_hist = ptf.opponent_history_as_of(opponent_env, opponent, prediction_game_date)
    recent_team_games = team_sched_prior[-ptf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)
    label, _drivers, _risks = cm.confidence_score(
        len(history), recent_toi_cv, recent_points_cv, len(opp_hist), OPPONENT_WINDOW_TARGET, appearance_rate)
    return label
