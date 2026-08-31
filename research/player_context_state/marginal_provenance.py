"""
Part 19: PIT-safe marginal probability provenance for the five frozen,
accepted marginal engines (Player SOG, Goals, Assists, Points, Blocks) --
NEVER refit here. Same deliberate exception to this project's usual
"don't cross-import sibling prop packages" convention as research/
joint_shot_workload/ and research/joint_scoring_dependence/'s own
marginal_provenance.py modules: this slice's purpose is to sit ABOVE
these marginals and study CONTEXT-CONDITIONAL residuals against them,
never to change them. Reimplemented fresh here (not imported from either
prior joint slice's copy) per this project's established per-package
convention -- every joint/context slice this session has built its own
copy of these thin wrappers rather than coupling to a sibling's file.

POINTS uses the shrunk EMPIRICAL baseline (D_empirical_distribution),
never the GLM -- same real finding as the Joint Scoring Dependence slice
(Part 4 there): the empirical baseline is the actual champion.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from research import elo_comparison as ec
from research.player_sog import count_models as cm
from research.player_sog import features as pf
from research.player_sog import live_projection as plp
from research.run_player_sog_model import NHL_CORPUS_PATH as PLAYER_SOG_NHL_CORPUS_PATH
from research.run_player_sog_model import build_team_schedules

from research.player_goals import features as gof
from research.player_goals import hierarchy as goh
from research.player_goals.live_projection import project_player_goals
from research.run_player_goals_model import NHL_CORPUS_PATH as GOALS_NHL_CORPUS_PATH

from research.player_assists import features as asf
from research.run_player_assists_model import build_example as assists_build_example
from research.run_player_assists_model import NHL_CORPUS_PATH as ASSISTS_NHL_CORPUS_PATH

from research.player_points import features as ptf
from research.run_player_points_model import empirical_threshold_probs as points_empirical_threshold_probs
from research.run_player_points_model import NHL_CORPUS_PATH as POINTS_NHL_CORPUS_PATH
from research.run_player_points_model import WARMUP_SEASON as POINTS_WARMUP_SEASON
from research.run_player_points_model import TUNING_SEASON as POINTS_TUNING_SEASON
from research.run_player_points_model import TUNING_SPLIT_DATE as POINTS_TUNING_SPLIT_DATE
from research.run_player_points_model import THRESHOLDS as POINTS_THRESHOLDS

from research.player_blocks import features as blf
from research.run_player_blocks_model import build_example as blocks_build_example
from research.run_player_blocks_model import NHL_CORPUS_PATH as BLOCKS_NHL_CORPUS_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYER_SOG_RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"
GOALS_RESULTS_PATH = REPO_ROOT / "research" / "player_goals_results.json"
ASSISTS_RESULTS_PATH = REPO_ROOT / "research" / "player_assists_results.json"
POINTS_RESULTS_PATH = REPO_ROOT / "research" / "player_points_results.json"
BLOCKS_RESULTS_PATH = REPO_ROOT / "research" / "player_blocks_results.json"


class PlayerSogMarginal:
    def __init__(self):
        with open(PLAYER_SOG_RESULTS_PATH) as f:
            r = json.load(f)
        stage = r["headline_stage"]
        weights_by_name = r["stage_weights"][stage]
        feature_names = r["config"]["feature_names"]
        self.weights = [weights_by_name[n] for n in feature_names]
        self.alpha = r["negbinom_alpha_fitted"]

        self.rows = pf.load_sog_corpus()
        self.index = pf.PlayerHistoryIndex(self.rows)
        totals = pf.build_team_game_totals(self.rows)
        self.opponent_allowed_history = pf.build_opponent_allowed_history(totals)
        self.league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())
        games = ec.load_corpus(str(PLAYER_SOG_NHL_CORPUS_PATH))
        self.team_schedules = build_team_schedules(games)

    def predict(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        result = plp.project_player_sog(
            self.rows, self.index, self.team_schedules, self.opponent_allowed_history,
            self.league_avg_sog_allowed, self.weights, self.alpha,
            player_id, team, opponent, prediction_game_date, season,
        )
        if result.get("status") != "PROJECTED_ACTIVE":
            return None
        return {"mu": result["expected_sog"], "probs": result["probs"]}


class GoalsMarginal:
    def __init__(self):
        with open(GOALS_RESULTS_PATH) as f:
            r = json.load(f)
        self.results = r
        self.context_weights = [r["context_weights_e"][n] for n in r["config"]["feature_names"]]
        self.locked_context_idx = set(r["locked_context_idx_for_candidate_e"])
        self.alpha = r["alpha_e"] if r["alpha_e"] > 0.01 else None
        self.k_player = r["best_k_player"]

        self.rows = gof.load_goals_corpus()
        self.index = gof.PlayerHistoryIndex(self.rows)
        totals = gof.build_team_game_goals_totals(self.rows)
        self.team_offense_hist = gof.build_team_offense_history(totals)
        self.opponent_env = gof.build_opponent_goals_allowed(totals)
        self.league_avg_goals_for = statistics.fmean(v["goals_for"] for v in totals.values())
        all_sog = sum(row["sog"] for row in self.rows)
        self.league_shooting_pct = (sum(row["goals"] for row in self.rows) / all_sog) if all_sog > 0 else 0.09
        games = ec.load_corpus(str(GOALS_NHL_CORPUS_PATH))
        self.team_schedules = build_team_schedules(games)
        self.rates = goh.RoleLeagueRates(self.rows)

    def predict(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        result = project_player_goals(
            self.rows, self.index, self.team_schedules, self.team_offense_hist, self.opponent_env,
            self.league_avg_goals_for, self.league_shooting_pct, self.context_weights, self.alpha,
            self.rates, self.k_player, self.locked_context_idx,
            player_id, team, opponent, prediction_game_date, season,
        )
        if result.get("status") != "PROJECTED_ACTIVE":
            return None
        probs = {int(k): v for k, v in result["probs"].items()}
        return {"mu": result["expected_goals"], "probs": probs}


class AssistsMarginal:
    def __init__(self):
        with open(ASSISTS_RESULTS_PATH) as f:
            r = json.load(f)
        weights_by_name = r["stage_weights"]["M4_plus_h2h"]
        self.weights = [weights_by_name[n] for n in
                         ("intercept", "log_baseline_rate", "recent_form_log_ratio", "toi_log_ratio",
                          "opponent_log_factor", "h2h_shrunk_delta")]
        self.alpha = r["alpha"] if r["alpha"] > 0.01 else None

        self.rows = asf.load_assists_corpus()
        self.index = asf.PlayerHistoryIndex(self.rows)
        totals = asf.build_team_game_points_totals(self.rows)
        self.opponent_env = asf.build_opponent_points_allowed(totals)
        self.league_avg_points_allowed = statistics.fmean(v["points_for"] for v in totals.values())
        games = ec.load_corpus(str(ASSISTS_NHL_CORPUS_PATH))
        self.team_schedules = build_team_schedules(games)

    def predict(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        row = {"player_id": player_id, "team": team, "opponent": opponent, "game_date": prediction_game_date,
               "season": season, "game_id": 0, "assists": 0}
        ex = assists_build_example(row, self.index, self.team_schedules, self.opponent_env,
                                    self.league_avg_points_allowed)
        if ex is None:
            return None
        mu = cm.predict_mu(self.weights, ex["feature_vector"])
        return {"mu": mu, "probs": cm.threshold_probabilities(mu, self.alpha, thresholds=(1, 2, 3))}


class PointsMarginal:
    def __init__(self):
        self.rows = ptf.load_points_corpus()
        self.index = ptf.PlayerHistoryIndex(self.rows)
        pre_lock_rows = [r for r in self.rows if r["season"] in (POINTS_WARMUP_SEASON, POINTS_TUNING_SEASON)
                          and r["game_date"] < POINTS_TUNING_SPLIT_DATE]
        self.league_empirical_rates = {
            t: sum(1 for r in pre_lock_rows if r["points"] >= t) / len(pre_lock_rows) for t in POINTS_THRESHOLDS
        }
        games = ec.load_corpus(str(POINTS_NHL_CORPUS_PATH))
        self.team_schedules = build_team_schedules(games)

    def predict(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        history = self.index.history_as_of(player_id, prediction_game_date)
        if len(history) < 3:
            return None
        team_sched_prior = [g for g in self.team_schedules.get(team, []) if g["game_date"] < prediction_game_date]
        if not ptf.projected_active(history, team_sched_prior):
            return None
        probs = points_empirical_threshold_probs(history, self.league_empirical_rates, POINTS_THRESHOLDS)
        return {"probs": probs, "history_len": len(history)}


class BlocksMarginal:
    def __init__(self):
        with open(BLOCKS_RESULTS_PATH) as f:
            r = json.load(f)
        weights_by_name = r["stage_weights"][r["headline_stage"]]
        self.weights = [weights_by_name[n] for n in
                         ("intercept", "log_baseline_rate", "recent_form_log_ratio", "toi_log_ratio",
                          "opponent_log_factor", "h2h_shrunk_delta")]
        self.alpha = r["negbinom_alpha_fitted"] if r["negbinom_alpha_fitted"] > 0.01 else None

        self.rows = blf.load_blocks_corpus()
        self.index = blf.PlayerHistoryIndex(self.rows)
        totals = blf.build_team_game_shot_attempt_totals(self.rows)
        self.opponent_env = blf.build_opponent_shot_attempt_environment(totals)
        self.league_avg_opp_shot_attempts = statistics.fmean(v["shot_attempts_against_for_team"]
                                                               for v in totals.values())
        games = ec.load_corpus(str(BLOCKS_NHL_CORPUS_PATH))
        self.team_schedules = build_team_schedules(games)

    def predict(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        row = {"player_id": player_id, "player_name": "", "team": team, "opponent": opponent,
               "game_id": 0, "game_date": prediction_game_date, "season": season, "position": "",
               "home_or_away": "HOME", "blocks": 0}
        ex = blocks_build_example(self.rows, row, self.index, self.team_schedules, self.opponent_env,
                                   self.league_avg_opp_shot_attempts)
        if ex is None:
            return None
        mu = cm.predict_mu(self.weights, ex["feature_vector"])
        return {"mu": mu, "probs": cm.threshold_probabilities(mu, self.alpha, thresholds=(1, 2, 3))}


class ContextMarginalContext:
    """One shared context holding all five frozen marginal engines."""

    def __init__(self):
        self.sog = PlayerSogMarginal()
        self.goals = GoalsMarginal()
        self.assists = AssistsMarginal()
        self.points = PointsMarginal()
        self.blocks = BlocksMarginal()

    def predict(self, prop: str, player_id: str, team: str, opponent: str, prediction_game_date: str,
                season: int) -> dict | None:
        return getattr(self, prop).predict(player_id, team, opponent, prediction_game_date, season)
