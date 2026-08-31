"""
Part 9/10 candidate A: PLAYER -> ROLE -> LEAGUE partial-pooling
aggregates for the GOALS target -- same structure as
research/player_points/hierarchy.py (built for a different target field
and rebuilt here rather than shared, matching this project's existing
per-prop features.py convention of not cross-importing between sibling
prop packages).

PIT safety at the FOLD/season level: real NHL season date ranges never
overlap (already verified directly for this exact corpus in
tests/test_player_points_redesign.py::test_real_season_date_ranges_never_overlap,
which reads game dates generically, not points-specific) -- an aggregate
built once from a fixed TUNING-season row pool is PIT-safe for every row
scored afterward.
"""
from __future__ import annotations

import statistics
from collections import defaultdict

FORWARD_POSITIONS = {"C", "L", "R"}
ROLE_TAGS = ("F_PP", "F_NONPP", "D_PP", "D_NONPP")
THRESHOLDS = (1, 2)


def role_tag(row: dict) -> str:
    base = "F" if row["position"] in FORWARD_POSITIONS else "D"
    pp = "PP" if row.get("pp") is not None else "NONPP"
    return f"{base}_{pp}"


def target_role_tag(is_forward: bool, recent_pp_icetime: float | None) -> str:
    base = "F" if is_forward else "D"
    pp = "PP" if (recent_pp_icetime or 0.0) > 0.0 else "NONPP"
    return f"{base}_{pp}"


class RoleLeagueRates:
    def __init__(self, train_rows: list[dict]):
        self.league_n = len(train_rows)
        self.league_mean = statistics.fmean(r["goals"] for r in train_rows) if train_rows else 0.15
        self.league_threshold_rate = {
            t: (sum(1 for r in train_rows if r["goals"] >= t) / self.league_n if self.league_n else 0.0)
            for t in THRESHOLDS
        }
        self.league_shooting_pct = (sum(r["goals"] for r in train_rows) / sum(r["sog"] for r in train_rows)
                                     if train_rows and sum(r["sog"] for r in train_rows) > 0 else 0.09)

        by_role = defaultdict(list)
        for r in train_rows:
            by_role[role_tag(r)].append(r)

        self.role_n = {tag: len(rows) for tag, rows in by_role.items()}
        self.role_mean = {tag: (statistics.fmean(r["goals"] for r in rows) if rows else self.league_mean)
                           for tag, rows in by_role.items()}
        self.role_threshold_rate = {
            tag: {t: (sum(1 for r in rows if r["goals"] >= t) / len(rows) if rows else self.league_threshold_rate[t])
                  for t in THRESHOLDS}
            for tag, rows in by_role.items()
        }
        for tag in ROLE_TAGS:
            self.role_n.setdefault(tag, 0)
            self.role_mean.setdefault(tag, self.league_mean)
            self.role_threshold_rate.setdefault(tag, dict(self.league_threshold_rate))

    def role_mean_shrunk(self, tag: str, k_role: int = 200) -> float:
        n = self.role_n.get(tag, 0)
        if n == 0:
            return self.league_mean
        w = n / (n + k_role)
        return self.league_mean + w * (self.role_mean[tag] - self.league_mean)

    def role_threshold_rate_shrunk(self, tag: str, t: int, k_role: int = 200) -> float:
        n = self.role_n.get(tag, 0)
        if n == 0:
            return self.league_threshold_rate[t]
        w = n / (n + k_role)
        return self.league_threshold_rate[t] + w * (self.role_threshold_rate[tag][t] - self.league_threshold_rate[t])


def player_role_hierarchical_mean(history: list[dict], role: str, rates: RoleLeagueRates, k_player: int) -> float:
    role_prior = rates.role_mean_shrunk(role)
    n = len(history)
    if n == 0:
        return role_prior
    player_mean = statistics.fmean(r["goals"] for r in history)
    w = n / (n + k_player)
    return role_prior + w * (player_mean - role_prior)


def player_role_hierarchical_threshold_rate(history: list[dict], role: str, t: int,
                                             rates: RoleLeagueRates, k_player: int) -> float:
    role_prior = rates.role_threshold_rate_shrunk(role, t)
    n = len(history)
    if n == 0:
        return role_prior
    player_rate = sum(1 for r in history if r["goals"] >= t) / n
    w = n / (n + k_player)
    return role_prior + w * (player_rate - role_prior)
