"""
Joint Scoring Dependence -- live view helper. Reuses the SAME frozen
marginal-provenance and joint-model functions research/
run_joint_scoring_dependence_model.py itself uses for evaluation, never
a second, parallel formula.

STATUS: RESEARCH -- JOINT PROBABILITY ESTIMATION ONLY. Not sportsbook
pricing, not a parlay optimizer. See
JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.joint_scoring_dependence import features as jf
from research.joint_scoring_dependence import joint_models as jm
from research.joint_scoring_dependence import marginal_provenance as mp
from research.run_joint_scoring_dependence_model import RESULTS_PATH

RESULTS_JSON_PATH = str(RESULTS_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


class ScoringDependenceEngine:
    def __init__(self, results: dict):
        self.ctx = mp.ScoringMarginalContext()
        rows = jf.load_joint_scoring_corpus()
        self.history_index = jf.JointScoringHistoryIndex(rows)
        tuning_rows = [r for r in rows if r["season"] == results["config"]["tuning_season"]]
        self.goal_rates = jm.ConversionRates(tuning_rows, "actual_goals")
        self.point_rates = jm.ConversionRates(tuning_rows, "actual_points")
        self.assist_rates = jm.ConversionRates(tuning_rows, "actual_assists")
        self.rho_by_name = results["rho_by_name"]

    def project(self, player_id: str, team: str, opponent: str, home_or_away: str,
                game_id: int, game_date: str, season: int) -> dict | None:
        row = {"player_id": player_id, "team": team, "opponent": opponent, "home_or_away": home_or_away,
               "game_id": game_id, "game_date": game_date, "season": season}
        pred = self.ctx.predict_row(row)
        if pred is None:
            return None
        history = self.history_index.history_as_of(player_id, game_date)
        mu_sog = pred["sog"]["mu"]
        p_sog = pred["sog"]["probs"]
        p_goal = pred["goals"]["probs"].get(1)
        p_assist = pred["assists"]["probs"].get(1)
        p_point = pred["points"]["probs"].get(1)
        goal_rate = self.goal_rates.shrunk_rate(history)
        point_rate = self.point_rates.shrunk_rate(history)
        assist_rate = self.assist_rates.shrunk_rate(history)

        def pair(x_sog, event_p, rate, rho_name):
            naive = p_sog.get(x_sog, 0.0) * event_p
            copula = jm.gaussian_copula_joint_upper_tail(p_sog.get(x_sog, 0.0), event_p,
                                                          self.rho_by_name.get(rho_name, 0.0))
            return {"naive": naive, "copula": copula,
                    "lift": (copula / naive) if naive > 0 else None}

        return {
            "mu_sog": mu_sog, "p_goal_1plus": p_goal, "p_assist_1plus": p_assist, "p_point_1plus": p_point,
            "sog3_goal": pair(3, p_goal, goal_rate, "SOG3_GOAL"),
            "sog3_assist": pair(3, p_assist, assist_rate, "SOG3_ASSIST"),
            "sog3_point": pair(3, p_point, point_rate, "SOG3_POINT"),
            "goal_point_exact": p_goal,
            "assist_point_exact": jm.clip_to_frechet(p_assist, p_assist, p_point) if p_point else p_assist,
        }
