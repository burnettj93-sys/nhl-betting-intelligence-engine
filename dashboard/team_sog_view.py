"""
Team Shots on Goal -- live view helper. Reuses the SAME frozen functions
research/run_team_sog_model.py itself uses for evaluation (build_example,
compute_candidates, confidence_for_example) rather than a second,
parallel formula.

STATUS: VALIDATED at 20+/25+/30+/35+ (both eval seasons); 40+ is PARTIAL
(season-inconsistent). See TEAM_SOG_VALIDATION_REPORT.md.
"""
from __future__ import annotations

import statistics

from dashboard.data_access import load_json_safely
from research.player_sog import count_models as cm
from research.team_sog import features as tf
from research.team_sog import hierarchy as th
from research.run_team_sog_model import (
    RESULTS_PATH,
    build_example,
    compute_candidates,
    confidence_for_example,
    threshold_prob,
)

RESULTS_JSON_PATH = str(RESULTS_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


class TeamSogEngine:
    def __init__(self, results: dict):
        self.rows = tf.load_team_sog_corpus()
        self.index = tf.TeamHistoryIndex(self.rows)

        tuning_season = results["config"]["tuning_season"]
        tuning_rows = [r for r in self.rows if r["season"] == tuning_season]
        self.rates = th.TeamSogRates(tuning_rows)
        self.league_avg_sog = statistics.fmean(r["actual_team_sog"] for r in tuning_rows)

        self.glm_weights = results["glm_weights"]
        self.offset_weights = results["offset_weights"]
        self.winner = results["winner"]

    def project(self, team: str, opponent: str, home_away: str, game_id: int, game_date: str,
                season: int) -> dict | None:
        row = {
            "game_id": game_id, "game_date": game_date, "season": season, "team": team, "opponent": opponent,
            "home_away": home_away, "actual_team_sog": 0, "actual_opponent_sog": 0,
            "P1_team_sog": 0, "P2_team_sog": 0, "P3_team_sog": 0,
        }
        ex = build_example(row, self.index, self.rates, self.league_avg_sog, None, None)
        if ex is None:
            return None
        candidates = compute_candidates(ex, self.glm_weights, self.offset_weights, self.league_avg_sog)
        mu = candidates[self.winner]
        label, drivers, risks = confidence_for_example(ex)
        cons_mu = cm.conservative_mu(mu, min(ex["history_games"], 20))

        return {
            "expected_sog": mu, "conservative_sog": cons_mu, "history_games": ex["history_games"],
            "prob_20plus": threshold_prob(mu, None, 20), "prob_25plus": threshold_prob(mu, None, 25),
            "prob_30plus": threshold_prob(mu, None, 30), "prob_35plus": threshold_prob(mu, None, 35),
            "prob_40plus": threshold_prob(mu, None, 40),
            "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
            "opponent_factor": ex["opponent_factor"],
        }
