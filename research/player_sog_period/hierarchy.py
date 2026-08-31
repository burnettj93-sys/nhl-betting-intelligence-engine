"""
Part 6/9/10: PLAYER -> POSITION -> LEAGUE partial-pooling for period SOG
shares and period SOG counts -- same structure as
research/player_goals/hierarchy.py's RoleLeagueRates (rebuilt here rather
than imported, matching this project's established per-package
convention). PP role uses the per-shot situationCode proxy tracked in the
period corpus (period_k_pp_sog), not true PP-TOI (Part 14's disclosed
limitation).
"""
from __future__ import annotations

import statistics
from collections import defaultdict

FORWARD_POSITIONS = {"C", "L", "R"}
PERIODS = (1, 2, 3)


def role_tag(row: dict) -> str:
    """Population-level tag for a COMPLETED historical row -- used only
    to bucket TUNING-season rows when fitting PeriodRoleLeagueRates'
    league/role priors (retrospective, population statistics; never
    called on a target row -- see history_role_tag for that, Part 4/15's
    explicit ban on target-game realized PP usage)."""
    base = "F" if row["position"] in FORWARD_POSITIONS else "D"
    has_pp = (row["period_1_pp_sog"] + row["period_2_pp_sog"] + row["period_3_pp_sog"]) > 0
    return f"{base}_{'PP' if has_pp else 'NONPP'}"


def history_role_tag(position: str, history: list[dict]) -> str:
    """PIT-safe role tag for a TARGET prediction row: PP status is
    determined from the player's OWN PRIOR games only (any real PP shot
    recorded in their pregame history), never from the target game's own
    realized PP shots (Part 4/15's explicit target-game-leakage ban --
    this fixes a real bug caught during initial testing, where the target
    row's own pp_sog fields were used instead of history)."""
    base = "F" if position in FORWARD_POSITIONS else "D"
    has_pp = any((r["period_1_pp_sog"] + r["period_2_pp_sog"] + r["period_3_pp_sog"]) > 0 for r in history)
    return f"{base}_{'PP' if has_pp else 'NONPP'}"


class PeriodRoleLeagueRates:
    """League- and role-level priors for period SOG COUNTS and SHARES,
    fit ONLY on the rows passed in (must be TUNING-season-scoped by the
    caller for PIT safety, exactly as every other prop model in this
    project does for its hierarchical priors)."""

    def __init__(self, train_rows: list[dict]):
        self.league_n = len(train_rows)
        self.league_mean = {
            k: statistics.fmean(r[f"period_{k}_sog"] for r in train_rows) if train_rows else 0.5
            for k in PERIODS
        }
        full_game_mean = statistics.fmean(r["full_game_sog"] for r in train_rows) if train_rows else 1.5
        self.league_share = {
            k: (self.league_mean[k] / full_game_mean if full_game_mean else 1.0 / 3.0)
            for k in PERIODS
        }

        by_role = defaultdict(list)
        for r in train_rows:
            by_role[role_tag(r)].append(r)
        self.role_n = {tag: len(rows) for tag, rows in by_role.items()}
        self.role_mean: dict[str, dict[int, float]] = {}
        self.role_share: dict[str, dict[int, float]] = {}
        for tag, rows in by_role.items():
            role_full_game_mean = statistics.fmean(r["full_game_sog"] for r in rows) if rows else full_game_mean
            self.role_mean[tag] = {k: statistics.fmean(r[f"period_{k}_sog"] for r in rows) for k in PERIODS}
            self.role_share[tag] = {
                k: (self.role_mean[tag][k] / role_full_game_mean if role_full_game_mean else 1.0 / 3.0)
                for k in PERIODS
            }

    def role_mean_shrunk(self, tag: str, k_period: int, k_role: int = 300) -> float:
        n = self.role_n.get(tag, 0)
        if n == 0 or tag not in self.role_mean:
            return self.league_mean[k_period]
        w = n / (n + k_role)
        return self.league_mean[k_period] + w * (self.role_mean[tag][k_period] - self.league_mean[k_period])

    def role_share_shrunk(self, tag: str, k_period: int, k_role: int = 300) -> float:
        n = self.role_n.get(tag, 0)
        if n == 0 or tag not in self.role_share:
            return self.league_share[k_period]
        w = n / (n + k_role)
        return self.league_share[k_period] + w * (self.role_share[tag][k_period] - self.league_share[k_period])


def player_period_share_hierarchical(history: list[dict], tag: str, rates: PeriodRoleLeagueRates,
                                      k_period: int, k_player: int = 40) -> float:
    """Shrinks the player's OWN historical period-k share of full-game SOG
    toward the role-shrunk prior, weighted by the player's own game count
    -- Part 9's persistence question answered via the same n/(n+k)
    shrinkage convention used everywhere else in this project."""
    role_prior = rates.role_share_shrunk(tag, k_period)
    n = len(history)
    if n == 0:
        return role_prior
    total_full_game = sum(r["full_game_sog"] for r in history)
    if total_full_game <= 0:
        return role_prior
    player_share = sum(r[f"period_{k_period}_sog"] for r in history) / total_full_game
    w = n / (n + k_player)
    return role_prior + w * (player_share - role_prior)


def player_period_mean_hierarchical(history: list[dict], tag: str, rates: PeriodRoleLeagueRates,
                                     k_period: int, k_player: int = 40) -> float:
    """Direct shrunk period-count mean (Candidate A / Baseline D+shrinkage) --
    distinct from the share-based estimator above: this shrinks the
    player's own period-k SOG COUNT directly toward the role-shrunk period
    COUNT prior, never routing through a full-game total at all."""
    role_prior = rates.role_mean_shrunk(tag, k_period)
    n = len(history)
    if n == 0:
        return role_prior
    player_mean = statistics.fmean(r[f"period_{k_period}_sog"] for r in history)
    w = n / (n + k_player)
    return role_prior + w * (player_mean - role_prior)
