"""
Part 3: PIT-safe marginal probability provenance for the three frozen,
accepted marginal engines (Player SOG, Team SOG, Goalie Saves). This
module deliberately BREAKS this project's usual "don't cross-import
between sibling prop packages" convention -- that convention exists to
keep independent marginal models from silently coupling; this slice's
entire purpose is to sit ABOVE those marginals, so it must import their
real, frozen, already-validated code paths directly rather than
reimplementing a second, parallel (and possibly divergent) copy of each.

CRITICAL: every function here LOADS frozen weights from the already-
written results JSON (headline_stage / winner / full_game_winner) and
calls each marginal's OWN unmodified build_example()/compute_candidates()
(or, for Player SOG, the existing shared research.player_sog.
live_projection.project_player_sog helper) -- there is no re-fitting
anywhere in this file. A marginal probability for a joint row's own
`game_date` uses only information strictly before that date, exactly as
each marginal's original walk-forward evaluation did.
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
from research.team_sog import features as tf
from research.team_sog import hierarchy as th
from research.run_team_sog_model import build_example as team_sog_build_example
from research.run_team_sog_model import compute_candidates as team_sog_compute_candidates
from research.run_team_sog_model import threshold_prob as team_sog_threshold_prob
from research.goalie_saves import features as gf
from research.goalie_saves import hierarchy as gh
from research.run_goalie_saves_model import build_example as goalie_saves_build_example
from research.run_goalie_saves_model import compute_candidates as goalie_saves_compute_candidates
from research.run_goalie_saves_model import threshold_prob as goalie_saves_threshold_prob

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYER_SOG_RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"
TEAM_SOG_RESULTS_PATH = REPO_ROOT / "research" / "team_sog_results.json"
GOALIE_SAVES_RESULTS_PATH = REPO_ROOT / "research" / "goalie_saves_results.json"


class PlayerSogMarginal:
    """Reuses research.player_sog.live_projection.project_player_sog
    UNCHANGED -- the same shared function the dashboard's Player SOG
    Research page and live_sog_pricing already call."""

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
        return {"mu": result["expected_sog"], "probs": result["probs"], "history_len": result.get("history_len")}


class TeamSogMarginal:
    """Reuses research.run_team_sog_model's OWN build_example()/
    compute_candidates() with the frozen glm_weights/offset_weights."""

    def __init__(self):
        with open(TEAM_SOG_RESULTS_PATH) as f:
            r = json.load(f)
        self.glm_weights = r["glm_weights"]
        self.offset_weights = r["offset_weights"]
        self.winner = r["winner"]
        self.glm_alpha = r["glm_alpha"]

        rows = tf.load_team_sog_corpus()
        self.index = tf.TeamHistoryIndex(rows)
        tuning_season = r["config"]["tuning_season"]
        tuning_rows = [x for x in rows if x["season"] == tuning_season]
        self.rates = th.TeamSogRates(tuning_rows)
        self.league_avg_sog = statistics.fmean(x["actual_team_sog"] for x in tuning_rows)

    def predict(self, team: str, opponent: str, home_away: str, game_id: int, game_date: str,
                season: int) -> dict | None:
        row = {
            "game_id": game_id, "game_date": game_date, "season": season, "team": team, "opponent": opponent,
            "home_away": home_away, "actual_team_sog": 0, "actual_opponent_sog": 0,
            "P1_team_sog": 0, "P2_team_sog": 0, "P3_team_sog": 0,
        }
        ex = team_sog_build_example(row, self.index, self.rates, self.league_avg_sog, None, None)
        if ex is None:
            return None
        candidates = team_sog_compute_candidates(ex, self.glm_weights, self.offset_weights, self.league_avg_sog)
        mu = candidates[self.winner]
        return {"mu": mu, "history_games": ex["history_games"],
                "threshold_prob": lambda t: team_sog_threshold_prob(mu, None, t)}


class GoalieSavesMarginal:
    """Reuses research.run_goalie_saves_model's OWN build_example()/
    compute_candidates() with the frozen glm_weights/offset_weights.
    Player-SOG-roster-aggregation is intentionally never invoked here
    (agg_ctx=None) -- the Goalie Saves slice already found it does not
    improve on the frozen model's own winner, so it plays no role in the
    frozen marginal being reused."""

    def __init__(self):
        with open(GOALIE_SAVES_RESULTS_PATH) as f:
            r = json.load(f)
        self.glm_weights = r["glm_weights"]
        self.offset_weights = r["offset_weights"]
        self.winner = r["full_game_winner"]

        goalie_rows = gf.load_goalie_corpus()
        team_rows = gf.load_team_sog_corpus()
        self.goalie_index = gf.GoalieHistoryIndex(goalie_rows)
        self.team_index = gf.TeamSogHistoryIndex(team_rows)
        tuning_season = r["config"]["tuning_season"]
        start_rows = [x for x in goalie_rows if x["actual_started"]]
        tuning_starts = [x for x in start_rows if x["season"] == tuning_season]
        tuning_teams = [x for x in team_rows if x["season"] == tuning_season]
        self.save_rates = gh.GoalieSavePctRates(tuning_starts)
        self.workload_rates = gh.GoalieWorkloadRates(tuning_starts, field="actual_saves")
        self.league_avg_team_sog = statistics.fmean(x["full_game_sog"] for x in tuning_teams)

    def predict(self, goalie_id: int, team: str, opponent: str, home_away: str, game_id: int,
                game_date: str, season: int) -> dict | None:
        row = {
            "game_id": game_id, "game_date": game_date, "season": season, "goalie_id": goalie_id,
            "team": team, "opponent": opponent, "home_away": home_away,
            "actual_saves": 0, "actual_shots_faced": 0, "actual_goals_allowed": 0,
            "period_1_saves": 0, "period_2_saves": 0, "period_3_saves": 0,
            "period_1_shots_faced": 0, "period_2_shots_faced": 0, "period_3_shots_faced": 0,
        }
        ex = goalie_saves_build_example(row, self.goalie_index, self.team_index, self.save_rates,
                                         self.workload_rates, self.league_avg_team_sog, None, None)
        if ex is None:
            return None
        candidates = goalie_saves_compute_candidates(ex, self.glm_weights, self.offset_weights, self.workload_rates)
        mu = candidates[self.winner]
        return {"mu": mu, "history_games": ex["history_games"],
                "threshold_prob": lambda t: goalie_saves_threshold_prob(mu, None, t)}


class MarginalContext:
    """One shared context holding all three frozen marginal engines --
    built once, reused across every joint-corpus row."""

    def __init__(self):
        self.player_sog = PlayerSogMarginal()
        self.team_sog = TeamSogMarginal()
        self.goalie_saves = GoalieSavesMarginal()

    def predict_row(self, row: dict) -> dict | None:
        player_pred = self.player_sog.predict(
            row["player_id"], row["player_team"], row["opponent_team"], row["game_date"], row["season"])
        if player_pred is None:
            return None

        team_home_away = "home" if row["home_or_away"] == "HOME" else "away"
        team_pred = self.team_sog.predict(
            row["player_team"], row["opponent_team"], team_home_away, row["game_id"], row["game_date"],
            row["season"])
        if team_pred is None:
            return None

        goalie_home_away = "away" if row["home_or_away"] == "HOME" else "home"
        goalie_pred = self.goalie_saves.predict(
            row["opposing_goalie_id"], row["opponent_team"], row["player_team"], goalie_home_away,
            row["game_id"], row["game_date"], row["season"])
        if goalie_pred is None:
            return None

        return {"player": player_pred, "team": team_pred, "goalie": goalie_pred}
