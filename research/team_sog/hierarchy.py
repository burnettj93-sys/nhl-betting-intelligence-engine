"""
Part 9/10: TEAM -> HOME/AWAY -> LEAGUE partial-pooling for team SOG counts
(offense: SOG generated) and opponent-allowed SOG counts (defense: SOG
suppressed) -- mirrors research/team_goals_period/hierarchy.py's
PeriodTeamRates structure (HOME/AWAY as the natural "role" tier), adapted
to full-game, non-period SOG. Fit ONLY on the rows passed in -- the
caller must TUNING-season-scope them for PIT safety.
"""
from __future__ import annotations

import statistics
from collections import defaultdict


class TeamSogRates:
    def __init__(self, train_rows: list[dict]):
        self.league_n = len(train_rows)
        self.league_mean_for = statistics.fmean(r["actual_team_sog"] for r in train_rows) if train_rows else 29.0
        self.league_mean_against = statistics.fmean(
            r["actual_opponent_sog"] for r in train_rows) if train_rows else 29.0

        by_ha = defaultdict(list)
        for r in train_rows:
            by_ha[r["home_away"]].append(r)
        self.ha_n = {tag: len(rows) for tag, rows in by_ha.items()}
        self.ha_mean_for: dict[str, float] = {
            tag: statistics.fmean(r["actual_team_sog"] for r in rows) for tag, rows in by_ha.items()
        }
        self.ha_mean_against: dict[str, float] = {
            tag: statistics.fmean(r["actual_opponent_sog"] for r in rows) for tag, rows in by_ha.items()
        }

        by_team = defaultdict(list)
        for r in train_rows:
            by_team[r["team"]].append(r)
        self.team_n: dict[str, int] = {t: len(rows) for t, rows in by_team.items()}
        self.team_mean_for: dict[str, float] = {
            t: statistics.fmean(r["actual_team_sog"] for r in rows) for t, rows in by_team.items()
        }
        self.team_mean_against: dict[str, float] = {
            t: statistics.fmean(r["actual_opponent_sog"] for r in rows) for t, rows in by_team.items()
        }

    def ha_mean_for_shrunk(self, tag: str, k_ha: int = 300) -> float:
        n = self.ha_n.get(tag, 0)
        if n == 0 or tag not in self.ha_mean_for:
            return self.league_mean_for
        w = n / (n + k_ha)
        return self.league_mean_for + w * (self.ha_mean_for[tag] - self.league_mean_for)

    def ha_mean_against_shrunk(self, tag: str, k_ha: int = 300) -> float:
        n = self.ha_n.get(tag, 0)
        if n == 0 or tag not in self.ha_mean_against:
            return self.league_mean_against
        w = n / (n + k_ha)
        return self.league_mean_against + w * (self.ha_mean_against[tag] - self.league_mean_against)

    def team_offensive_factor_shrunk(self, team: str, k_team: int = 60) -> float:
        n = self.team_n.get(team, 0)
        if n == 0 or team not in self.team_mean_for:
            return 1.0
        w = n / (n + k_team)
        team_factor = self.team_mean_for[team] / self.league_mean_for if self.league_mean_for else 1.0
        return 1.0 + w * (team_factor - 1.0)

    def team_defensive_factor_shrunk(self, team: str, k_team: int = 60) -> float:
        n = self.team_n.get(team, 0)
        if n == 0 or team not in self.team_mean_against:
            return 1.0
        w = n / (n + k_team)
        team_factor = self.team_mean_against[team] / self.league_mean_against if self.league_mean_against else 1.0
        return 1.0 + w * (team_factor - 1.0)


def team_sog_mean_hierarchical(history: list[dict], home_away: str, rates: TeamSogRates,
                                k_team: int = 60) -> float:
    """Shrinks the team's own historical SOG-for mean toward the
    HOME/AWAY-shrunk league prior, weighted by the team's own game count."""
    prior = rates.ha_mean_for_shrunk(home_away)
    n = len(history)
    if n == 0:
        return prior
    team_mean = statistics.fmean(r["actual_team_sog"] for r in history)
    w = n / (n + k_team)
    return prior + w * (team_mean - prior)


def opponent_sog_allowed_mean_hierarchical(opponent_history: list[dict], opponent_home_away: str,
                                            rates: TeamSogRates, k_team: int = 60) -> float:
    """Shrinks the OPPONENT's own historical SOG-allowed mean (i.e., how
    many shots the opponent typically allows) toward the HOME/AWAY-shrunk
    league prior."""
    prior = rates.ha_mean_against_shrunk(opponent_home_away)
    n = len(opponent_history)
    if n == 0:
        return prior
    opp_mean = statistics.fmean(r["actual_opponent_sog"] for r in opponent_history)
    w = n / (n + k_team)
    return prior + w * (opp_mean - prior)
