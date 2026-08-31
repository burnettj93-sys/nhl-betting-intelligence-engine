"""
Goalie Saves -- live view helper. Reuses the SAME frozen functions
research/run_goalie_saves_model.py itself uses for evaluation
(build_example, compute_candidates, confidence_for_example) rather than a
second, parallel formula. Starter probability reuses the existing,
separately-audited PIT-safe projected-starter model (research/
goalie_intelligence/) UNCHANGED -- never re-fit here.

STATUS: full-game 20+/25+ saves and period-2 saves are VALIDATED; 30+ and
periods 1/3 are PARTIAL (season-inconsistent); 35+ is REJECTED; 40+ is
INSUFFICIENT_DATA. See GOALIE_SAVES_VALIDATION_REPORT.md. Every saves
projection here is CONDITIONAL_ON_ACTUAL_START -- the starter-probability
panel is a separate, disclosed source of uncertainty, never folded into
the saves numbers themselves.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.goalie_intelligence import features as gif
from research.goalie_intelligence import model as gim
from research.player_sog import count_models as cm
from research.goalie_saves import features as gf
from research.goalie_saves import hierarchy as gh
from research.run_goalie_saves_model import (
    PERIODS,
    RESULTS_PATH,
    build_example,
    compute_candidates,
    confidence_for_example,
    threshold_prob,
)

RESULTS_JSON_PATH = str(RESULTS_PATH)
STARTER_RESULTS_PATH = "research/goalie_intelligence_results.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


def load_starter_results() -> dict | None:
    return load_json_safely(STARTER_RESULTS_PATH)


class StarterProbabilityEngine:
    """Live re-derivation using the EXISTING, separately-audited
    projected-starter model's own frozen weights -- true-holdout accuracy
    ~67-69%, Brier ~0.44-0.45 (research/goalie_intelligence_results.json).
    Never re-fit; disclosed as a genuinely separate source of uncertainty
    from the saves count model."""

    def __init__(self, starter_results: dict):
        weights_by_name = starter_results["fitted_weights"]
        self.weights = [weights_by_name[n] for n in gim.FEATURE_NAMES]
        self.rows = gif.load_starter_corpus()

    def project(self, team: str, prediction_game_date: str) -> dict | None:
        history = gif.team_history_as_of(self.rows, team, prediction_game_date)
        if len(history) < 5:
            return None
        candidates = gif.eligible_goalies(history)
        if not candidates:
            return None
        team_games = [r for r in self.rows if r["team"] == team]
        is_b2b = gif.team_back_to_back(team_games, prediction_game_date)
        year, month = int(prediction_game_date[:4]), int(prediction_game_date[5:7])
        season = (year * 10000 + year + 1) if month >= 8 else ((year - 1) * 10000 + year)
        fvs = [gim.build_feature_vector(history, g, season, prediction_game_date, is_b2b) for g in candidates]
        probs = gim.score_candidates(self.weights, fvs)
        return {"candidates": list(zip(candidates, probs)), "is_back_to_back": is_b2b}


class GoalieSavesEngine:
    def __init__(self, results: dict):
        self.goalie_rows = gf.load_goalie_corpus()
        self.team_rows = gf.load_team_sog_corpus()
        self.goalie_index = gf.GoalieHistoryIndex(self.goalie_rows)
        self.team_index = gf.TeamSogHistoryIndex(self.team_rows)

        tuning_season = results["config"]["tuning_season"]
        start_rows = [r for r in self.goalie_rows if r["actual_started"]]
        tuning_starts = [r for r in start_rows if r["season"] == tuning_season]
        tuning_teams = [r for r in self.team_rows if r["season"] == tuning_season]
        self.save_rates = gh.GoalieSavePctRates(tuning_starts)
        self.workload_rates = gh.GoalieWorkloadRates(tuning_starts, field="actual_saves")
        import statistics
        self.league_avg_team_sog = statistics.fmean(r["full_game_sog"] for r in tuning_teams)

        self.glm_weights = results["glm_weights"]
        self.offset_weights = results["offset_weights"]
        self.full_game_winner = results["full_game_winner"]
        self.period_league_share = {int(k): v for k, v in results["period_league_share"].items()}

    def project(self, goalie_id: int, team: str, opponent: str, home_away: str, game_id: int,
                game_date: str, season: int) -> dict | None:
        row = {
            "game_id": game_id, "game_date": game_date, "season": season, "goalie_id": goalie_id,
            "team": team, "opponent": opponent, "home_away": home_away,
            "actual_saves": 0, "actual_shots_faced": 0, "actual_goals_allowed": 0,
            "period_1_saves": 0, "period_2_saves": 0, "period_3_saves": 0,
            "period_1_shots_faced": 0, "period_2_shots_faced": 0, "period_3_shots_faced": 0,
        }
        ex = build_example(row, self.goalie_index, self.team_index, self.save_rates, self.workload_rates,
                            self.league_avg_team_sog, None, None)
        if ex is None:
            return None
        candidates = compute_candidates(ex, self.glm_weights, self.offset_weights, self.workload_rates)
        mu = candidates[self.full_game_winner]
        label, drivers, risks = confidence_for_example(ex)
        cons_mu = cm.conservative_mu(mu, min(ex["history_games"], 20))

        periods_out = {}
        for k in PERIODS:
            period_mu = mu * self.period_league_share[k]
            periods_out[k] = {"expected_saves": period_mu, "prob_5plus": threshold_prob(period_mu, None, 5),
                               "prob_8plus": threshold_prob(period_mu, None, 8)}

        return {
            "expected_shots_faced": ex["opp_sog_rolling"], "expected_saves": mu, "conservative_saves": cons_mu,
            "shrunk_save_pct": ex["shrunk_save_pct"], "history_games": ex["history_games"],
            "prob_20plus": threshold_prob(mu, None, 20), "prob_25plus": threshold_prob(mu, None, 25),
            "prob_30plus": threshold_prob(mu, None, 30), "prob_35plus": threshold_prob(mu, None, 35),
            "prob_40plus": threshold_prob(mu, None, 40),
            "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
            "periods": periods_out,
        }
