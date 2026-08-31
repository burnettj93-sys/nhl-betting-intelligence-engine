"""
Single-player/game SOG projection, using the SAME feature functions and
the SAME fitted headline-model weights as
research/run_player_sog_model.py's build_example() / headline stage --
not a second, parallel formula. This is the shared core reused by both
the dashboard's Player SOG Research page (a chosen historical date) and
research/live_sog_pricing/ (a real live/near-future game date) -- moved
here (out of dashboard/player_sog_view.py, which now just re-exports it)
so the dependency direction stays research -> dashboard, never the
reverse, and research/live_sog_pricing/ never has to import a
dashboard-layer module.

STATUS: research re-derivation only. Every probability is reconstructed
from real, PIT-safe historical data as of `prediction_game_date` -- never
a claim about a live future lineup. Returns "PROJECTED_ACTIVE",
"PROJECTED_INACTIVE", or "INSUFFICIENT_HISTORY" -- never "CONFIRMED
ACTIVE" (no code path here reads target-game appearance).
"""
from __future__ import annotations

from research.player_sog import features as pf
from research.player_sog import count_models as cm


def project_player_sog(rows: list[dict], index: pf.PlayerHistoryIndex, team_schedules: dict,
                        opponent_allowed_history: dict, league_avg_sog_allowed: float,
                        weights: list[float], alpha: float | None,
                        player_id: str, team: str, opponent: str, prediction_game_date: str,
                        season: int, opponent_window_target: int = 20) -> dict:
    history = index.history_as_of(player_id, prediction_game_date)
    if len(history) < 3:
        return {"status": "INSUFFICIENT_HISTORY"}

    team_sched_prior = [g for g in team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
    if not pf.projected_active(history, team_sched_prior):
        return {"status": "PROJECTED_INACTIVE",
                "note": "This player has appeared in fewer than the minimum recent team games -- "
                        "not enough recent evidence to treat as a projected active skater."}

    baseline_rate = pf.rolling_mean(history, "sog", 20) or pf.season_to_date_mean(history, "sog", season) or 0.5
    recent_rate5 = pf.rolling_mean(history, "sog", 5)
    recent_toi = pf.rolling_mean(history, "icetime_seconds", 10)
    baseline_toi = pf.rolling_mean(history, "icetime_seconds", 20)

    opp_hist = pf.opponent_history_as_of(opponent_allowed_history, opponent, prediction_game_date)
    opp_allowed = pf.rolling_opponent_sog_allowed(opponent_allowed_history, opponent, prediction_game_date, 20)
    opponent_factor = None if opp_allowed is None else opp_allowed / league_avg_sog_allowed

    h2h_rate, h2h_games = pf.h2h_shrunk_sog_rate(history, opponent, baseline_rate)
    h2h_delta = h2h_rate - baseline_rate

    fv = cm.build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi, opponent_factor, h2h_delta)
    mu = cm.predict_mu(weights, fv)

    toi_window = history[-10:]
    sog_window = history[-10:]
    recent_toi_cv = cm.coefficient_of_variation([r["icetime_seconds"] for r in toi_window]) if toi_window else None
    recent_sog_cv = cm.coefficient_of_variation([r["sog"] for r in sog_window]) if sog_window else None
    recent_team_games = team_sched_prior[-pf.ELIGIBILITY_WINDOW_TEAM_GAMES:]
    appearance_rate = 1.0
    if recent_team_games:
        played_dates = {r["game_date"] for r in history}
        appearance_rate = sum(1 for g in recent_team_games if g["game_date"] in played_dates) / len(recent_team_games)

    label, drivers, risks = cm.confidence_score(
        len(history), recent_toi_cv, recent_sog_cv, len(opp_hist), opponent_window_target, appearance_rate)

    eff_n = min(len(history), 20)
    conservative_mu = cm.conservative_mu(mu, eff_n)

    probs = cm.threshold_probabilities(mu, alpha)
    conservative_probs = cm.threshold_probabilities(conservative_mu, alpha)

    return {
        "status": "PROJECTED_ACTIVE",
        "expected_sog": mu, "conservative_sog": conservative_mu,
        "probs": probs, "conservative_probs": conservative_probs,
        "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
        "baseline_rate": baseline_rate, "recent_rate5": recent_rate5,
        "recent_toi_minutes": (recent_toi / 60.0) if recent_toi else None,
        "opponent_factor": opponent_factor, "h2h_games": h2h_games, "h2h_rate": h2h_rate,
        "history_games": len(history), "distribution": "negative_binomial" if alpha else "poisson",
    }
