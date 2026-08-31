"""
Redesign Cycle 2, Part 4/6: PLAYER -> ROLE -> LEAGUE partial-pooling
aggregates for the points target.

ROLE tag is a static per-row attribute (position + whether that specific
game had any 5-on-4 icetime), used only to pool HISTORICAL rows into a
role-level reference rate -- never a claim about the target game's own
role, which is decided separately (and PIT-safely, from the player's own
prior rolling PP icetime) by the caller.

PIT safety at the FOLD level: real NHL season date ranges never overlap
(verified directly -- e.g. 2022-23 ends 2023-04-14, 2023-23-24 begins
2023-10-10), so for a rolling fold "train through season X, validate
season Y" (Y strictly after X), every TRAIN-season row's game_date is
already < every VALIDATION-season row's game_date by construction. A
role/league aggregate built once from the fold's train-season rows is
therefore PIT-safe for every row scored in that fold's validation season,
with no further per-row date bisection required. This is checked
directly, not assumed (tests/test_player_points_redesign.py).
"""
from __future__ import annotations

import statistics
from collections import defaultdict

FORWARD_POSITIONS = {"C", "L", "R"}
ROLE_TAGS = ("F_PP", "F_NONPP", "D_PP", "D_NONPP")
THRESHOLDS = (1, 2, 3)


def role_tag(row: dict) -> str:
    base = "F" if row["position"] in FORWARD_POSITIONS else "D"
    pp = "PP" if row.get("pp") is not None else "NONPP"
    return f"{base}_{pp}"


def target_role_tag(is_forward: bool, recent_pp_icetime: float | None) -> str:
    """The TARGET row's own role bucket, decided PIT-safely from the
    player's own prior rolling PP icetime (never that game's own pp
    block, which would be a future/label-side read)."""
    base = "F" if is_forward else "D"
    pp = "PP" if (recent_pp_icetime or 0.0) > 0.0 else "NONPP"
    return f"{base}_{pp}"


class RoleLeagueRates:
    """Built ONCE from a fold's fixed train-season row pool. Exposes:
      - league_mean, role_mean[tag]
      - league_threshold_rate[t], role_threshold_rate[tag][t]
      - league_n, role_n[tag]
    """

    def __init__(self, train_rows: list[dict]):
        self.league_n = len(train_rows)
        self.league_mean = statistics.fmean(r["points"] for r in train_rows) if train_rows else 0.3
        self.league_threshold_rate = {
            t: (sum(1 for r in train_rows if r["points"] >= t) / self.league_n if self.league_n else 0.0)
            for t in THRESHOLDS
        }

        by_role = defaultdict(list)
        for r in train_rows:
            by_role[role_tag(r)].append(r)

        self.role_n = {tag: len(rows) for tag, rows in by_role.items()}
        self.role_mean = {tag: (statistics.fmean(r["points"] for r in rows) if rows else self.league_mean)
                           for tag, rows in by_role.items()}
        self.role_threshold_rate = {
            tag: {t: (sum(1 for r in rows if r["points"] >= t) / len(rows) if rows else self.league_threshold_rate[t])
                  for t in THRESHOLDS}
            for tag, rows in by_role.items()
        }
        for tag in ROLE_TAGS:
            self.role_n.setdefault(tag, 0)
            self.role_mean.setdefault(tag, self.league_mean)
            self.role_threshold_rate.setdefault(tag, dict(self.league_threshold_rate))

    def role_mean_shrunk(self, tag: str, k_role: int = 200) -> float:
        """Role -> league shrinkage (Part 6 level 1). Role samples are
        always large (thousands of rows) in this corpus, so this level
        rarely binds -- verified directly, not assumed (K chosen well
        below the smallest real role_n encountered)."""
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
    """PLAYER -> ROLE -> LEAGUE partial pooling (Part 4/6), for the MEAN."""
    role_prior = rates.role_mean_shrunk(role)
    n = len(history)
    if n == 0:
        return role_prior
    player_mean = statistics.fmean(r["points"] for r in history)
    w = n / (n + k_player)
    return role_prior + w * (player_mean - role_prior)


def player_role_hierarchical_threshold_rate(history: list[dict], role: str, t: int,
                                             rates: RoleLeagueRates, k_player: int) -> float:
    role_prior = rates.role_threshold_rate_shrunk(role, t)
    n = len(history)
    if n == 0:
        return role_prior
    player_rate = sum(1 for r in history if r["points"] >= t) / n
    w = n / (n + k_player)
    return role_prior + w * (player_rate - role_prior)
