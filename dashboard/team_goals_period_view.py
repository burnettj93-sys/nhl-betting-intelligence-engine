"""
Team Goals by Period — live view helper. Reuses the SAME frozen functions
research/run_team_goals_period_model.py itself uses for evaluation
(build_example, compute_candidates, confidence_for_example) rather than a
second, parallel formula.

STATUS: RESEARCH -- NOT VALIDATED. Unlike Player SOG by Period, this
model did not clear this project's adoption bar (see
TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md) -- every number here is a real,
PIT-safe re-derivation, but should be read as an informational research
output, not a validated probability.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.player_sog import count_models as cm
from research.team_goals_period import features as tf
from research.team_goals_period import hierarchy as hi
from research.run_team_goals_period_model import (
    PERIODS,
    RESULTS_PATH,
    build_example,
    compute_candidates,
    confidence_for_example,
    threshold_prob,
)

RESULTS_JSON_PATH = str(RESULTS_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


class TeamGoalsPeriodEngine:
    def __init__(self, results: dict):
        self.rows = tf.load_team_period_corpus()
        self.index = tf.TeamPeriodHistoryIndex(self.rows)

        tuning_season = results["config"]["tuning_season"]
        tuning_rows = [r for r in self.rows if r["season"] == tuning_season]
        self.rates = hi.PeriodTeamRates(tuning_rows)
        import statistics
        self.league_avg_opponent_period = {
            k: statistics.fmean(r[f"opponent_period_{k}_goals"] for r in tuning_rows) for k in PERIODS
        }

        self.glm_weights = {int(k): v for k, v in results["glm_weights"].items()}
        self.offset_weights = {int(k): v for k, v in results["offset_weights"].items()}
        self.winner_by_period = {int(k): v for k, v in results["winner_by_period"].items()}

    def project(self, team: str, opponent: str, home_away: str, game_id: int, game_date: str,
                season: int) -> dict | None:
        row = {
            "game_id": game_id, "game_date": game_date, "season": season, "team": team, "opponent": opponent,
            "home_away": home_away, "period_1_goals": 0, "period_2_goals": 0, "period_3_goals": 0,
            "period_1_pp_goals": 0, "period_2_pp_goals": 0, "period_3_pp_goals": 0,
            "opponent_period_1_goals": 0, "opponent_period_2_goals": 0, "opponent_period_3_goals": 0,
            "ot_goals": 0, "full_game_team_goals": 0,
        }
        ex = build_example(row, self.index, self.rates, self.league_avg_opponent_period)
        if ex is None:
            return None
        ex["candidates"] = compute_candidates(ex, self.glm_weights, self.offset_weights, self.rates)

        periods_out = {}
        for k in PERIODS:
            winner = self.winner_by_period[k]
            mu = ex["candidates"][k][winner]
            cons_mu = cm.conservative_mu(mu, min(ex["history_games"], 20))
            label, drivers, risks = confidence_for_example(ex, k)
            periods_out[k] = {
                "expected_goals": mu, "conservative_goals": cons_mu,
                "prob_1plus": threshold_prob(mu, None, 1), "prob_2plus": threshold_prob(mu, None, 2),
                "prob_3plus": threshold_prob(mu, None, 3), "confidence": label,
                "confidence_drivers": drivers, "confidence_risks": risks, "model": winner,
            }
        return {"full_game_expected": ex["upstream_expected"], "history_games": ex["history_games"],
                "periods": periods_out}
