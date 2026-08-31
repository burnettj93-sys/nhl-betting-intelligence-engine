"""
Part 5 / Part J: upstream full-game expected-SOG feature, using the
EXISTING VALIDATED full-game Player SOG model's exact locked spec
(research/player_sog/{features,count_models,live_projection}.py,
research/player_sog_results.json) -- never a second, parallel formula.

No pre-materialized out-of-fold prediction archive exists for the
full-game SOG model (confirmed: research/player_sog_results.json holds
only aggregate metrics, not a per-game prediction file). This module
creates LEGITIMATE rolling upstream predictions instead of leaking: for
every period-corpus row (game_id, player_id, prediction_game_date), it
calls `live_projection.project_player_sog()` with history gated strictly
to `game_date < prediction_game_date` via `PlayerHistoryIndex` -- the same
gate the full-game model itself was built and evaluated with. This is
"ELIGIBLE" (Part 5's required disclosure), not leakage, because the
history available to the projection is identical in kind to what the
frozen model saw during its own tuning/eval walk-forward, just re-run
per period-corpus row instead of per full-game-corpus row.
"""
from __future__ import annotations

import json
from pathlib import Path

from research import elo_comparison as ec
from research.player_sog import count_models as cm
from research.player_sog import features as pf
from research.player_sog import live_projection as lp

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOG_RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"
NHL_CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"


class UpstreamSogModel:
    """Loads the frozen full-game SOG spec once; `.expected_sog(...)` is
    then a cheap, PIT-safe re-derivation per call."""

    def __init__(self):
        with open(SOG_RESULTS_PATH) as f:
            results = json.load(f)
        stage = results["headline_stage"]
        self.weights = [results["stage_weights"][stage][name] for name in results["config"]["feature_names"]]
        self.alpha = results["negbinom_alpha_fitted"]  # reported as an alternative; Poisson (alpha=None) is headline
        self.distribution = "poisson"  # confirmed headline distribution (research/player_sog_results.json has no
                                        # separate "distribution" key; NB is reported only as an alternative)

        self.rows = pf.load_sog_corpus()
        self.index = pf.PlayerHistoryIndex(self.rows)
        team_totals = pf.build_team_game_totals(self.rows)
        self.opponent_allowed_history = pf.build_opponent_allowed_history(team_totals)
        self.league_avg_sog_allowed = sum(r["sog_for"] for r in team_totals.values()) / len(team_totals)

        games = ec.load_corpus(str(NHL_CORPUS_PATH))
        self.team_schedules = _build_team_schedules(games)

    def expected_sog(self, player_id: str, team: str, opponent: str, prediction_game_date: str,
                      season: int) -> dict:
        """Returns the full live_projection.project_player_sog() dict --
        callers use `result["expected_sog"]` when `result["status"] ==
        "PROJECTED_ACTIVE"`, otherwise the feature is unavailable for this
        row (a real, disclosed non-eligibility, not a fabricated number)."""
        return lp.project_player_sog(
            self.rows, self.index, self.team_schedules, self.opponent_allowed_history,
            self.league_avg_sog_allowed, self.weights, None,  # alpha=None -> Poisson, the headline distribution
            player_id, team, opponent, prediction_game_date, season,
        )


def _build_team_schedules(games: list[dict]) -> dict[str, list[dict]]:
    from collections import defaultdict
    by_team = defaultdict(list)
    for g in games:
        by_team[g["home_team"]].append(g)
        by_team[g["away_team"]].append(g)
    for team in by_team:
        by_team[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return by_team
