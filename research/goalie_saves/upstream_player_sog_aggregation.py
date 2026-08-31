"""
Part 6-8: mandatory investigation -- can PIT-safe aggregation of the
EXISTING, VALIDATED Player SOG model's expected-SOG output over an
opposing team's PROJECTED_ACTIVE roster improve expected goalie shots
faced, versus the much simpler team-level historical SOG rate?

Reuses the frozen Player SOG model's own weights/alpha (research/
player_sog_results.json, headline_stage) and its own feature/eligibility
functions (research/player_sog/features.py, count_models.py) UNCHANGED --
never a second, re-fit copy of that model. "PROJECTED_ACTIVE roster" uses
the exact same `pf.projected_active()` gate the Player SOG model itself
uses to decide whether a player counts as a legitimate target (>=4 of the
team's last 10 real games) -- applied here to every skater who has
recently appeared for the opposing team, not just one named player, using
research.elo_comparison's real schedule for the team-games-prior context
exactly as research/run_player_sog_model.py::build_team_schedules does.

Every projection uses STRICT prior-date history only (player_id's own
`history_as_of`, itself gated the same way as the validated model). No
target-game roster/lineup information is ever read -- eligibility and
projections are both computed from data strictly before the prediction
date.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from research import elo_comparison as ec
from research.player_sog import count_models as cm
from research.player_sog import features as pf

SOG_RESULTS_PATH = Path(__file__).resolve().parent.parent / "player_sog_results.json"
NHL_CORPUS_PATH = Path(__file__).resolve().parent.parent / "real_nhl_results" / "normalized_regular_season_games.jsonl"
ROSTER_WINDOW = 15  # recent team games searched for "who has suited up lately"


def load_frozen_sog_model() -> tuple[list[float], float]:
    with open(SOG_RESULTS_PATH) as f:
        r = json.load(f)
    stage = r["headline_stage"]
    weights_by_name = r["stage_weights"][stage]
    feature_names = r["config"]["feature_names"]
    weights = [weights_by_name[n] for n in feature_names]
    alpha = r["negbinom_alpha_fitted"]
    return weights, alpha


class AggregationContext:
    def __init__(self):
        self.sog_rows = pf.load_sog_corpus()
        self.index = pf.PlayerHistoryIndex(self.sog_rows)
        totals = pf.build_team_game_totals(self.sog_rows)
        self.opponent_allowed_history = pf.build_opponent_allowed_history(totals)
        self.league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())

        games = ec.load_corpus(str(NHL_CORPUS_PATH))
        self.team_schedules: dict[str, list[dict]] = defaultdict(list)
        for g in games:
            self.team_schedules[g["home_team"]].append(g)
            self.team_schedules[g["away_team"]].append(g)
        for team in self.team_schedules:
            self.team_schedules[team].sort(key=lambda r: (r["game_date"], r["game_id"]))

        by_team_games: dict[str, list[tuple[str, int, set]]] = defaultdict(dict)
        for r in self.sog_rows:
            key = (r["game_date"], r["game_id"])
            by_team_games[r["team"]].setdefault(key, set()).add(r["player_id"])
        self.by_team_recent_rosters: dict[str, list[tuple[str, int, set]]] = {}
        for team, keyed in by_team_games.items():
            ordered = sorted(keyed.items(), key=lambda kv: kv[0])
            self.by_team_recent_rosters[team] = [(d, gid, ids) for (d, gid), ids in ordered]

    def roster_candidates(self, team: str, prediction_game_date: str, window: int = ROSTER_WINDOW) -> set:
        games = self.by_team_recent_rosters.get(team, [])
        prior = [g for g in games if g[0] < prediction_game_date]
        recent = prior[-window:]
        ids: set = set()
        for _d, _gid, players in recent:
            ids |= players
        return ids

    def team_schedule_as_of(self, team: str, prediction_game_date: str) -> list[dict]:
        return [g for g in self.team_schedules.get(team, []) if g["game_date"] < prediction_game_date]


def aggregate_expected_opponent_sog(ctx: AggregationContext, shooting_team: str, defending_team: str,
                                     prediction_game_date: str, weights: list[float],
                                     min_history_games: int = 3) -> dict:
    """Sums the frozen Player SOG model's expected_sog over every skater
    PROJECTED_ACTIVE for `shooting_team` -- the aggregate expected shots
    the GOALIE (playing for `defending_team`) will face. NEVER reads the
    target game's own roster -- only players recently seen for
    `shooting_team` and STRICT prior history for each. Each skater's own
    `opponent_factor` feature is `defending_team`'s rolling SOG-allowed
    rate (the real defense they face), matching the validated model's own
    feature exactly."""
    candidates = ctx.roster_candidates(shooting_team, prediction_game_date)
    team_sched_prior = ctx.team_schedule_as_of(shooting_team, prediction_game_date)
    opp_allowed = pf.rolling_opponent_sog_allowed(ctx.opponent_allowed_history, defending_team,
                                                    prediction_game_date, 20)
    opponent_factor = None if opp_allowed is None else opp_allowed / ctx.league_avg_sog_allowed

    total_mu = 0.0
    n_players = 0
    for player_id in candidates:
        history = ctx.index.history_as_of(player_id, prediction_game_date)
        if len(history) < min_history_games:
            continue
        if not pf.projected_active(history, team_sched_prior):
            continue

        baseline_rate = pf.rolling_mean(history, "sog", 20)
        if baseline_rate is None:
            continue
        if baseline_rate <= 0:
            baseline_rate = 0.5
        recent_rate5 = pf.rolling_mean(history, "sog", 5)
        recent_toi = pf.rolling_mean(history, "icetime_seconds", 10)
        baseline_toi = pf.rolling_mean(history, "icetime_seconds", 20)

        h2h_delta_raw, _h2h_n = pf.h2h_shrunk_sog_rate(history, defending_team, baseline_rate)
        h2h_delta = h2h_delta_raw - baseline_rate

        fv = cm.build_feature_vector(baseline_rate, recent_rate5, recent_toi, baseline_toi,
                                      opponent_factor, h2h_delta)
        mu = cm.predict_mu(weights, fv)
        total_mu += mu
        n_players += 1

    return {"expected_sog_sum": total_mu, "n_players": n_players, "n_candidates": len(candidates)}
