"""
Single-player/game POINTS projection, using the SAME feature functions
and the SAME frozen headline-model weights as
research/run_player_points_model.py's build_example() / locked stage --
not a second, parallel formula. Mirrors research/player_sog/live_projection.py's
structure exactly for the dashboard's "project a player" section.

STATUS: research re-derivation only, using the LOCKED (post-freeze)
model weights read from research/player_points_results.json. Every
probability is reconstructed from real, PIT-safe historical data as of
`prediction_game_date` -- never a claim about a live future lineup.
Returns "PROJECTED_ACTIVE", "PROJECTED_INACTIVE", or
"INSUFFICIENT_HISTORY" -- never "CONFIRMED ACTIVE".
"""
from __future__ import annotations

from research.player_points import features as ptf
from research.player_sog import count_models as cm
from research.run_player_points_model import build_points_feature_vector, threshold_prob, THRESHOLDS


def project_player_points(rows: list[dict], index: ptf.PlayerHistoryIndex, team_schedules: dict,
                           team_offense_hist: dict, opponent_env: dict, league_avg_points_for: float,
                           weights: list[float], alpha: float | None, calibration_scales: dict[int, float],
                           player_id: str, team: str, opponent: str, prediction_game_date: str,
                           season: int, opponent_window_target: int = 20) -> dict:
    history = index.history_as_of(player_id, prediction_game_date)
    if len(history) < 3:
        return {"status": "INSUFFICIENT_HISTORY"}

    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
    if not ptf.projected_active(history, team_sched_prior):
        return {"status": "PROJECTED_INACTIVE",
                "note": "This player has appeared in fewer than the minimum recent team games -- "
                        "not enough recent evidence to treat as a projected active skater."}

    baseline_rate = ptf.rolling_mean(history, "points", 20) or ptf.season_to_date_mean(history, "points", season) or 0.30
    recent_rate5 = ptf.rolling_mean(history, "points", 5)
    recent_toi = ptf.rolling_mean(history, "icetime_seconds", 10)
    baseline_toi = ptf.rolling_mean(history, "icetime_seconds", 20)
    pp_rate_recent = ptf.rolling_pp_mean(history, "points", 10)

    opp_hist = ptf.opponent_history_as_of(opponent_env, opponent, prediction_game_date)
    opp_allowed = ptf.rolling_opponent_points_allowed(opponent_env, opponent, prediction_game_date, 20)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_points_for

    team_offense = ptf.rolling_team_points_for(team_offense_hist, team, prediction_game_date, 20)
    team_factor = None if team_offense is None else team_offense / league_avg_points_for

    h2h_rate, h2h_games = ptf.h2h_shrunk_points_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_rate - baseline_rate

    fv = build_points_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi,
                                      pp_rate_recent, opponent_factor, team_factor, h2h_delta)
    mu = cm.predict_mu(weights, fv)

    toi_window = history[-10:]
    points_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_points_cv = cm.coefficient_of_variation([r["points"] for r in points_window]) if points_window else None
    recent_team_games = team_sched_prior[-ptf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    label, drivers, risks = cm.confidence_score(
        len(history), recent_toi_cv, recent_points_cv, len(opp_hist), opponent_window_target, appearance_rate)

    eff_n = min(len(history), 20)
    conservative_mu = cm.conservative_mu(mu, eff_n)

    probs = {t: threshold_prob(mu, alpha, t) for t in THRESHOLDS}
    conservative_probs = {t: threshold_prob(conservative_mu, alpha, t) for t in THRESHOLDS}
    if calibration_scales:
        probs = {t: min(max(p * calibration_scales.get(t, 1.0), 1e-9), 1 - 1e-9) for t, p in probs.items()}
        conservative_probs = {t: min(max(p * calibration_scales.get(t, 1.0), 1e-9), 1 - 1e-9) for t, p in conservative_probs.items()}

    return {
        "status": "PROJECTED_ACTIVE",
        "expected_points": mu, "conservative_points": conservative_mu,
        "probs": {str(k): v for k, v in probs.items()}, "conservative_probs": {str(k): v for k, v in conservative_probs.items()},
        "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
        "baseline_rate": baseline_rate, "recent_rate5": recent_rate5,
        "recent_toi_minutes": (recent_toi / 60.0) if recent_toi else None,
        "pp_rate_recent": pp_rate_recent, "opponent_factor": opponent_factor, "team_factor": team_factor,
        "h2h_games": h2h_games, "h2h_rate": h2h_rate,
        "history_games": len(history), "distribution": "negative_binomial" if alpha else "poisson",
    }
