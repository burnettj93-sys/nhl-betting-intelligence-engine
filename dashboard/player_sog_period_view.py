"""
Player SOG by Period — live view helper. Reuses the SAME frozen functions
research/run_player_sog_period_model.py itself uses for evaluation
(build_example, compute_candidates, confidence_for_example) rather than a
second, parallel formula -- moved here so dashboard/pages/14_*.py stays
thin, mirroring dashboard/player_sog_view.py's own precedent.

STATUS: research re-derivation only, same guarantee as the full-game SOG
page -- every number is reconstructed from real, PIT-safe historical data
as of the chosen date, never a claim about a live future lineup.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.player_sog import count_models as cm
from research.player_sog import features as sog_pf
from research.player_sog_period import features as pf
from research.player_sog_period import hierarchy as hi
from research.player_sog_period.upstream_sog import UpstreamSogModel
from research.run_player_sog_period_model import (
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


class PeriodSogEngine:
    """Loads every frozen artifact once (period corpus, indices, TUNING-
    fit priors/weights, upstream model) -- callers reuse ONE instance
    across a dashboard session (cached via st.cache_resource)."""

    def __init__(self, results: dict):
        self.rows = pf.load_period_corpus()
        self.period_index = pf.PeriodHistoryIndex(self.rows)
        self.sog_rows = sog_pf.load_sog_corpus()
        self.sog_index = sog_pf.PlayerHistoryIndex(self.sog_rows)

        from research import elo_comparison as ec
        from research.run_player_sog_period_model import REPO_ROOT
        games = ec.load_corpus(str(REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))
        from collections import defaultdict
        team_schedules = defaultdict(list)
        for g in games:
            team_schedules[g["home_team"]].append(g)
            team_schedules[g["away_team"]].append(g)
        for t in team_schedules:
            team_schedules[t].sort(key=lambda r: (r["game_date"], r["game_id"]))
        self.team_schedules = dict(team_schedules)

        team_totals = pf.build_team_game_period_totals(self.rows)
        self.opp_period_allowed = pf.build_opponent_period_allowed_history(team_totals)
        self.team_period_hist = pf.build_team_period_history(team_totals)

        tuning_season = results["config"]["tuning_season"]
        tuning_rows = [r for r in self.rows if r["season"] == tuning_season]
        self.rates = hi.PeriodRoleLeagueRates(tuning_rows)
        import statistics
        tuning_team_totals = {k: v for k, v in team_totals.items() if v["season"] == tuning_season}
        self.league_avg_team_period = {
            k: statistics.fmean(v[f"period_{k}_sog"] for v in tuning_team_totals.values()) for k in PERIODS
        }

        self.glm_weights = {int(k): v for k, v in results["glm_weights"].items()}
        self.glm_alpha = {int(k): v for k, v in results["glm_alpha"].items()}
        self.offset_weights = {int(k): v for k, v in results["offset_weights"].items()}
        self.winner_by_period = {int(k): v for k, v in results["winner_by_period"].items()}
        self.upstream_model = UpstreamSogModel()

    def project(self, player_id: str, team: str, opponent: str, position: str, home_away: str,
                game_id: int, game_date: str, season: int) -> dict | None:
        # period_k_sog/full_game_sog/ot_sog are placeholders ONLY -- build_example()
        # reads them purely to populate the "actual" (label) field on its output,
        # which this live view never displays (Part 3/4: never use target-game
        # outcomes as a pregame input, and this row IS the pregame target, not
        # historical data -- history comes entirely from period_index/sog_index).
        row = {"player_id": player_id, "team": team, "opponent": opponent, "position": position,
               "home_away": home_away, "game_id": game_id, "game_date": game_date, "season": season,
               "went_to_ot": False, "period_1_sog": 0, "period_2_sog": 0, "period_3_sog": 0,
               "period_1_pp_sog": 0, "period_2_pp_sog": 0, "period_3_pp_sog": 0,
               "ot_sog": 0, "full_game_sog": 0}
        ex = build_example(self.rows, row, self.period_index, self.sog_index, self.team_schedules,
                            None, self.team_period_hist, self.opp_period_allowed, self.rates,
                            self.league_avg_team_period)
        if ex is None:
            return {"status": "INSUFFICIENT_HISTORY_OR_INACTIVE"}

        upstream = self.upstream_model.expected_sog(player_id, team, opponent, game_date, season)
        ex["upstream_status"] = upstream["status"]
        ex["upstream_expected_sog"] = upstream.get("expected_sog")

        cand = compute_candidates(ex, self.glm_weights, self.glm_alpha, self.offset_weights,
                                   ex["upstream_expected_sog"], self.rates)
        ex["candidates"] = cand

        periods_out = {}
        for k in PERIODS:
            winner = self.winner_by_period[k]
            mu = cand[k][winner]
            if mu is None:
                continue
            cons_mu = cm.conservative_mu(mu, min(ex["history_games"], 20))
            label, drivers, risks = confidence_for_example(ex, k)
            periods_out[k] = {
                "expected_sog": mu, "conservative_sog": cons_mu,
                "prob_1plus": threshold_prob(mu, None, 1), "prob_2plus": threshold_prob(mu, None, 2),
                "prob_3plus": threshold_prob(mu, None, 3), "prob_4plus": threshold_prob(mu, None, 4),
                "confidence": label, "confidence_drivers": drivers, "confidence_risks": risks,
                "model": winner,
            }
        return {
            "status": "PROJECTED_ACTIVE", "full_game_expected_sog": ex["upstream_expected_sog"],
            "role_tag": ex["role_tag"], "history_games": ex["history_games"], "periods": periods_out,
        }
