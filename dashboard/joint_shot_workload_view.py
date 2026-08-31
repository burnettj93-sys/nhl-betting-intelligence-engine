"""
Joint Shot/Workload -- live view helper. Reuses the SAME frozen
marginal-provenance and joint-model functions research/
run_joint_shot_workload_model.py itself uses for evaluation, never a
second, parallel formula.

STATUS: RESEARCH -- JOINT PROBABILITY ESTIMATION ONLY. Not sportsbook
pricing, not a parlay optimizer. See
JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.joint_shot_workload import joint_models as jm
from research.joint_shot_workload import marginal_provenance as mp
from research.run_joint_shot_workload_model import RESULTS_PATH

RESULTS_JSON_PATH = str(RESULTS_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


class JointShotWorkloadEngine:
    def __init__(self, results: dict):
        self.ctx = mp.MarginalContext()
        self.params = jm.StructuralParams.__new__(jm.StructuralParams)
        self.params.empty_net_dist = {int(k): v for k, v in results["empty_net_dist"].items()}
        self.params.league_save_pct = results["league_save_pct"]
        self.league_avg_player_share = results["league_avg_player_share"]

    def project(self, player_id: str, player_team: str, opponent_team: str, home_or_away: str,
                goalie_id: int, game_id: int, game_date: str, season: int, player_share: float,
                x_player: int, y_team: int, z_saves: int) -> dict | None:
        row = {"player_id": player_id, "player_team": player_team, "opponent_team": opponent_team,
               "home_or_away": home_or_away, "opposing_goalie_id": goalie_id, "game_id": game_id,
               "game_date": game_date, "season": season}
        pred = self.ctx.predict_row(row)
        if pred is None:
            return None
        mu_team = pred["team"]["mu"]
        p_player = pred["player"]["probs"].get(x_player,
                                                jm.structural_marginal_player_sf(mu_team, player_share, x_player))
        p_team = jm.structural_marginal_team_sf(mu_team, y_team)
        p_goalie = jm.structural_marginal_goalie_sf(mu_team, self.params, z_saves)

        pt_naive = p_player * p_team
        pt_structural = jm.structural_joint_player_team(mu_team, player_share, x_player, y_team)
        tg_naive = p_team * p_goalie
        tg_structural = jm.structural_joint_team_goalie(mu_team, self.params, y_team, z_saves)
        pg_naive = p_player * p_goalie
        pg_structural = jm.structural_joint_player_goalie(mu_team, player_share, self.params, x_player, z_saves)
        three_naive = p_player * p_team * p_goalie
        three_structural = jm.structural_joint_three_way(mu_team, player_share, self.params,
                                                           x_player, y_team, z_saves)

        return {
            "mu_player": pred["player"]["mu"], "mu_team": mu_team, "mu_goalie": pred["goalie"]["mu"],
            "p_player": p_player, "p_team": p_team, "p_goalie": p_goalie,
            "player_team": {"naive": pt_naive, "structural": pt_structural,
                             "lift": (pt_structural / pt_naive) if pt_naive > 0 else None},
            "team_goalie": {"naive": tg_naive, "structural": tg_structural,
                             "lift": (tg_structural / tg_naive) if tg_naive > 0 else None},
            "player_goalie": {"naive": pg_naive, "structural": pg_structural,
                               "lift": (pg_structural / pg_naive) if pg_naive > 0 else None},
            "three_way": {"naive": three_naive, "structural": three_structural,
                           "lift": (three_structural / three_naive) if three_naive > 0 else None},
        }
