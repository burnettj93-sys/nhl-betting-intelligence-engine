"""
Part 4/6/7: TEAM -> HOME/AWAY -> LEAGUE partial-pooling for team-period
goal counts and shares -- same structure as
research/player_sog_period/hierarchy.py's PLAYER->POSITION->LEAGUE
pattern, adapted to team granularity: the natural "role" split at team
level is home/away (Part 7 explicitly asks this be tested), not a
position/PP tag (there is no equivalent concept for a team).
"""
from __future__ import annotations

import statistics
from collections import defaultdict

PERIODS = (1, 2, 3)


class PeriodTeamRates:
    """League- and home/away-level priors for team-period goal COUNTS and
    SHARES, fit ONLY on the rows passed in (must be TUNING-season-scoped
    by the caller for PIT safety)."""

    def __init__(self, train_rows: list[dict]):
        self.league_n = len(train_rows)
        self.league_mean = {
            k: statistics.fmean(r[f"period_{k}_goals"] for r in train_rows) if train_rows else 0.9
            for k in PERIODS
        }
        full_game_mean = statistics.fmean(r["full_game_team_goals"] for r in train_rows) if train_rows else 3.0
        self.league_share = {
            k: (self.league_mean[k] / full_game_mean if full_game_mean else 1.0 / 3.0)
            for k in PERIODS
        }

        by_ha = defaultdict(list)
        for r in train_rows:
            by_ha[r["home_away"]].append(r)
        self.ha_n = {tag: len(rows) for tag, rows in by_ha.items()}
        self.ha_mean: dict[str, dict[int, float]] = {}
        self.ha_share: dict[str, dict[int, float]] = {}
        for tag, rows in by_ha.items():
            ha_full_game_mean = statistics.fmean(r["full_game_team_goals"] for r in rows) if rows else full_game_mean
            self.ha_mean[tag] = {k: statistics.fmean(r[f"period_{k}_goals"] for r in rows) for k in PERIODS}
            self.ha_share[tag] = {
                k: (self.ha_mean[tag][k] / ha_full_game_mean if ha_full_game_mean else 1.0 / 3.0)
                for k in PERIODS
            }

    def ha_mean_shrunk(self, tag: str, k_period: int, k_ha: int = 300) -> float:
        n = self.ha_n.get(tag, 0)
        if n == 0 or tag not in self.ha_mean:
            return self.league_mean[k_period]
        w = n / (n + k_ha)
        return self.league_mean[k_period] + w * (self.ha_mean[tag][k_period] - self.league_mean[k_period])

    def ha_share_shrunk(self, tag: str, k_period: int, k_ha: int = 300) -> float:
        n = self.ha_n.get(tag, 0)
        if n == 0 or tag not in self.ha_share:
            return self.league_share[k_period]
        w = n / (n + k_ha)
        return self.league_share[k_period] + w * (self.ha_share[tag][k_period] - self.league_share[k_period])


def team_period_share_hierarchical(history: list[dict], home_away: str, rates: PeriodTeamRates,
                                    k_period: int, k_team: int = 60) -> float:
    """Shrinks the TEAM's own historical period-k share of full-game
    goals toward the home/away-shrunk league prior, weighted by the
    team's own game count."""
    prior = rates.ha_share_shrunk(home_away, k_period)
    n = len(history)
    if n == 0:
        return prior
    total_full_game = sum(r["full_game_team_goals"] for r in history)
    if total_full_game <= 0:
        return prior
    team_share = sum(r[f"period_{k_period}_goals"] for r in history) / total_full_game
    w = n / (n + k_team)
    return prior + w * (team_share - prior)


def team_period_mean_hierarchical(history: list[dict], home_away: str, rates: PeriodTeamRates,
                                   k_period: int, k_team: int = 60) -> float:
    """Direct shrunk period-count mean -- shrinks the team's own
    period-k GOAL COUNT directly toward the home/away-shrunk period-count
    prior, never routing through a full-game total."""
    prior = rates.ha_mean_shrunk(home_away, k_period)
    n = len(history)
    if n == 0:
        return prior
    team_mean = statistics.fmean(r[f"period_{k_period}_goals"] for r in history)
    w = n / (n + k_team)
    return prior + w * (team_mean - prior)
