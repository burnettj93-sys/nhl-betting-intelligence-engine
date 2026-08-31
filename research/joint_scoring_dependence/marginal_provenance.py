"""
Part 3: PIT-safe marginal probability provenance for the four frozen,
accepted marginal engines (Player SOG, Goals, Assists, Points). Same
deliberate exception to the "don't cross-import sibling packages"
convention as research/joint_shot_workload/marginal_provenance.py --
this slice's purpose is to sit ABOVE these marginals, so it imports their
real, frozen code paths directly. No re-fitting anywhere in this file.

POINTS (Part 4): the accepted status is EMPIRICAL_BASELINE_REMAINS_
CHAMPION -- the fancier GLM candidate (locked_stage M6_plus_h2h) does
NOT beat the simple shrunk-empirical baseline (D_empirical_distribution,
Brier 0.2077 vs. 0.2096 in the real true-evaluation). This module
therefore uses the EMPIRICAL BASELINE as the Points marginal, never the
GLM -- using the GLM here would silently promote a model this project's
own prior slice found inferior.
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYER_SOG_RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"
GOALS_RESULTS_PATH = REPO_ROOT / "research" / "player_goals_results.json"
ASSISTS_RESULTS_PATH = REPO_ROOT / "research" / "player_assists_results.json"
POINTS_RESULTS_PATH = REPO_ROOT / "research" / "player_points_results.json"


class PlayerSogMarginal:
    """Identical to research/joint_shot_workload/marginal_provenance.py's
    own PlayerSogMarginal -- reimplemented here (not imported) per this
    project's per-package convention."""

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
    """Reuses research.player_goals.live_projection.project_player_goals
    UNCHANGED, with the frozen locked candidate-E state (context_weights_e
    / locked_context_idx_for_candidate_e / best_k_player / alpha_e) --
    the SAME state the dashboard's live "project a player" view uses
    (dashboard/pages/12_Player_Goals_Research.py)."""

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
        # "frozen, full-corpus aggregate for live use" -- the SAME choice the
        # accepted dashboard live-projection view makes (not TUNING-only),
        # since RoleLeagueRates is a role/league prior, not a per-row fit.
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
        # research.player_goals.live_projection returns string-keyed probs
        # ({"1": p, "2": p}) -- normalized to int keys here so every marginal
        # in this package exposes the SAME {int: float} probs shape.
        probs = {int(k): v for k, v in result["probs"].items()}
        return {"mu": result["expected_goals"], "probs": probs}


class AssistsMarginal:
    """Reuses research.run_player_assists_model.build_example() with the
    frozen M4_plus_h2h weights -- no live_projection.py exists for
    Assists, so this mirrors that module's own build_example() exactly,
    the same reuse discipline as every other marginal wrapper."""

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
    """Uses the shrunk EMPIRICAL baseline (D_empirical_distribution) as
    the Points marginal, per Part 4's explicit instruction -- the fancier
    GLM candidate does not beat this baseline in the real true-evaluation
    (research/player_points_results.json: baseline Brier 0.2077 vs.
    headline GLM Brier 0.2096 at 1+), so using the GLM here would
    silently promote a model this project already found inferior."""

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


class ScoringMarginalContext:
    """One shared context holding all four frozen scoring marginal
    engines -- built once, reused across every joint-corpus row."""

    def __init__(self):
        self.player_sog = PlayerSogMarginal()
        self.goals = GoalsMarginal()
        self.assists = AssistsMarginal()
        self.points = PointsMarginal()

    def predict_row(self, row: dict) -> dict | None:
        sog_pred = self.player_sog.predict(row["player_id"], row["team"], row["opponent"],
                                            row["game_date"], row["season"])
        if sog_pred is None:
            return None
        goals_pred = self.goals.predict(row["player_id"], row["team"], row["opponent"],
                                         row["game_date"], row["season"])
        if goals_pred is None:
            return None
        assists_pred = self.assists.predict(row["player_id"], row["team"], row["opponent"],
                                             row["game_date"], row["season"])
        if assists_pred is None:
            return None
        points_pred = self.points.predict(row["player_id"], row["team"], row["opponent"],
                                           row["game_date"], row["season"])
        if points_pred is None:
            return None
        return {"sog": sog_pred, "goals": goals_pred, "assists": assists_pred, "points": points_pred}
