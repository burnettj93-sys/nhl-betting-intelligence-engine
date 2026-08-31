"""
Single-player/game GOALS projection, using the SAME feature functions
and the SAME frozen locked-candidate-E weights as
research/run_player_goals_model.py's build_example() / locked candidate
-- not a second, parallel formula. Mirrors every other prop's
live_projection.py in this project.

STATUS: research re-derivation only, using the LOCKED (post-freeze)
weights read from research/player_goals_results.json. Every probability
is reconstructed from real, PIT-safe historical data as of
`prediction_game_date` -- never a claim about a live future lineup.
"""
from __future__ import annotations

import math

from research.player_goals import features as gf
from research.player_goals import hierarchy as gh
from research.player_sog import count_models as cm
from research.run_player_goals_model import build_feature_vector, predict_mu_with_offset, threshold_prob, THRESHOLDS


def project_player_goals(rows: list[dict], index: gf.PlayerHistoryIndex, team_schedules: dict,
                          team_offense_hist: dict, opponent_env: dict, league_avg_goals_for: float,
                          league_shooting_pct: float, context_weights: list[float], alpha: float | None,
                          rates: gh.RoleLeagueRates, k_player: int, locked_context_idx: set[int],
                          player_id: str, team: str, opponent: str, prediction_game_date: str,
                          season: int, opponent_window_target: int = 20) -> dict:
    history = index.history_as_of(player_id, prediction_game_date)
    if len(history) < 3:
        return {"status": "INSUFFICIENT_HISTORY"}

    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
    if not gf.projected_active(history, team_sched_prior):
        return {"status": "PROJECTED_INACTIVE",
                "note": "This player has appeared in fewer than the minimum recent team games -- "
                        "not enough recent evidence to treat as a projected active skater."}

    baseline_rate = gf.rolling_mean(history, "goals", 20) or gf.season_to_date_mean(history, "goals", season) or 0.10
    recent_rate5 = gf.rolling_mean(history, "goals", 5)
    recent_toi = gf.rolling_mean(history, "icetime_seconds", 10)
    baseline_toi = gf.rolling_mean(history, "icetime_seconds", 20)
    pp_rate_recent = gf.rolling_pp_mean(history, "goals", 10)
    recent_sog_rate = gf.rolling_mean(history, "sog", 10)
    baseline_sog_rate = gf.rolling_mean(history, "sog", 20)
    shrunk_shooting_pct, career_shots = gf.career_shooting_pct_shrunk(history, league_shooting_pct)

    opp_allowed = gf.rolling_opponent_goals_allowed(opponent_env, opponent, prediction_game_date, 20)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_goals_for
    team_offense = gf.rolling_team_goals_for(team_offense_hist, team, prediction_game_date, 20)
    team_factor = None if team_offense is None else team_offense / league_avg_goals_for

    h2h_sog_rate, h2h_sog_games = gf.h2h_shrunk_sog_rate(history, opponent, baseline_sog_rate or 0.10)
    h2h_sog_delta = h2h_sog_rate - (baseline_sog_rate or 0.10)
    h2h_goals_rate, h2h_goals_games = gf.h2h_shrunk_goals_rate(history, opponent, baseline_rate)
    h2h_goals_delta = h2h_goals_rate - baseline_rate

    fv = build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, pp_rate_recent,
                               recent_sog_rate, baseline_sog_rate, shrunk_shooting_pct, league_shooting_pct,
                               opponent_factor, team_factor, h2h_sog_delta, h2h_goals_delta)
    fv_masked = [v if i in locked_context_idx else 0.0 for i, v in enumerate(fv)]

    pp_icetime_recent = gf.rolling_pp_mean(history, "icetime_seconds", 10) or 0.0
    position = history[-1]["position"] if history else "C"
    role = gh.target_role_tag(position in gh.FORWARD_POSITIONS, pp_icetime_recent)

    mu_base = gh.player_role_hierarchical_mean(history, role, rates, k_player)
    mu = predict_mu_with_offset(context_weights, fv_masked, math.log(max(mu_base, 1e-6)))

    toi_window = history[-10:]
    goals_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_goals_cv = cm.coefficient_of_variation([r["goals"] for r in goals_window]) if goals_window else None
    recent_team_games = team_sched_prior[-gf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    label, drivers, risks = cm.confidence_score(
        len(history), recent_toi_cv, recent_goals_cv, len(gf.opponent_history_as_of(opponent_env, opponent, prediction_game_date)),
        opponent_window_target, appearance_rate)

    eff_n = min(len(history), 20)
    conservative_mu = cm.conservative_mu(mu, eff_n)

    probs = {t: threshold_prob(mu, alpha, t) for t in THRESHOLDS}
    conservative_probs = {t: threshold_prob(conservative_mu, alpha, t) for t in THRESHOLDS}

    return {
        "status": "PROJECTED_ACTIVE",
        "expected_goals": mu, "conservative_goals": conservative_mu,
        "probs": {str(k): v for k, v in probs.items()}, "conservative_probs": {str(k): v for k, v in conservative_probs.items()},
        "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
        "baseline_rate": baseline_rate, "recent_sog_rate": recent_sog_rate,
        "raw_shooting_pct": (sum(r["goals"] for r in history) / sum(r["sog"] for r in history)
                              if sum(r["sog"] for r in history) > 0 else None),
        "shrunk_shooting_pct": shrunk_shooting_pct, "career_shots": career_shots,
        "opponent_factor": opponent_factor, "team_factor": team_factor,
        "h2h_goals_games": h2h_goals_games, "h2h_sog_games": h2h_sog_games,
        "history_games": len(history), "distribution": "negative_binomial" if alpha else "poisson",
    }
