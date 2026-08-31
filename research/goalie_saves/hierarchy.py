"""
Part 9/10: GOALIE -> TEAM -> LEAGUE partial-pooling for goalie save
percentage (a RATIO, shrunk via pooled shots/saves counts, never a naive
mean-of-per-game-percentages) and goalie shots-faced workload (a COUNT
MEAN). Mirrors research/team_goals_period/hierarchy.py's structure, with
TEAM standing in for that package's HOME/AWAY "role" tier -- for a
goalie, "which team's defensive system" is the natural middle rung
between individual sample size and the league (Part 10's own wording:
"shrinkage toward: goalie history, team/system, league").

Fit ONLY on the rows passed in -- the caller must TUNING-season-scope
them for PIT safety, same discipline as every other hierarchy.py in this
project.
"""
from __future__ import annotations

import statistics
from collections import defaultdict


class GoalieSavePctRates:
    def __init__(self, train_rows: list[dict]):
        total_saves = sum(r["actual_saves"] for r in train_rows)
        total_shots = sum(r["actual_shots_faced"] for r in train_rows)
        self.league_save_pct = total_saves / total_shots if total_shots else 0.9

        by_team = defaultdict(list)
        for r in train_rows:
            by_team[r["team"]].append(r)
        self.team_shots: dict[str, int] = {}
        self.team_save_pct: dict[str, float] = {}
        for team, rows in by_team.items():
            t_shots = sum(r["actual_shots_faced"] for r in rows)
            t_saves = sum(r["actual_saves"] for r in rows)
            self.team_shots[team] = t_shots
            self.team_save_pct[team] = (t_saves / t_shots) if t_shots else self.league_save_pct

    def team_shrunk_save_pct(self, team: str, k_team: int = 600) -> float:
        n = self.team_shots.get(team, 0)
        if n == 0 or team not in self.team_save_pct:
            return self.league_save_pct
        w = n / (n + k_team)
        return self.league_save_pct + w * (self.team_save_pct[team] - self.league_save_pct)

    def goalie_shrunk_save_pct(self, goalie_history: list[dict], team: str,
                                k_goalie: int = 400) -> float:
        prior = self.team_shrunk_save_pct(team)
        total_shots = sum(r["actual_shots_faced"] for r in goalie_history)
        if total_shots == 0:
            return prior
        total_saves = sum(r["actual_saves"] for r in goalie_history)
        goalie_pct = total_saves / total_shots
        w = total_shots / (total_shots + k_goalie)
        return prior + w * (goalie_pct - prior)


class GoalieWorkloadRates:
    def __init__(self, train_rows: list[dict], field: str = "actual_shots_faced"):
        self.field = field
        self.league_mean = statistics.fmean(r[field] for r in train_rows) if train_rows else 28.0

        by_team = defaultdict(list)
        for r in train_rows:
            by_team[r["team"]].append(r)
        self.team_n: dict[str, int] = {t: len(rows) for t, rows in by_team.items()}
        self.team_mean: dict[str, float] = {
            t: statistics.fmean(r[self.field] for r in rows) for t, rows in by_team.items()
        }

    def team_shrunk_mean(self, team: str, k_team: int = 60) -> float:
        n = self.team_n.get(team, 0)
        if n == 0 or team not in self.team_mean:
            return self.league_mean
        w = n / (n + k_team)
        return self.league_mean + w * (self.team_mean[team] - self.league_mean)

    def goalie_shrunk_mean(self, goalie_history: list[dict], team: str, k_goalie: int = 20) -> float:
        prior = self.team_shrunk_mean(team)
        n = len(goalie_history)
        if n == 0:
            return prior
        goalie_mean = statistics.fmean(r[self.field] for r in goalie_history)
        w = n / (n + k_goalie)
        return prior + w * (goalie_mean - prior)
