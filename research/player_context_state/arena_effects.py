"""
Parts 15-18: arena effects, split explicitly into two DIFFERENT
mechanisms (Part 16's own required distinction), never conflated:

1. RINK-RECORDING EFFECT: the mean (actual - frozen_mu) residual at a
   given arena, pooled across EVERY player who played a game there
   (both teams, all opponents) -- if this is large and consistent
   regardless of who is playing, it is evidence of a scorekeeper/rink-
   recording pattern (or a real, confounded team-style effect -- Part 18
   requires this caveat to be preserved honestly, not claimed as proven
   scorekeeper bias), NOT individual player skill at that venue.

2. PLAYER-ARENA PERFORMANCE EFFECT: a specific player's OWN residual at
   a specific arena, hierarchically shrunk PLAYER-ARENA -> ARENA (the
   rink-wide effect above, already a real prior) -> 0 (no effect) --
   Part 17's explicit instruction not to trust a raw 6-game player-arena
   average.

"Arena" is operationalized as the HOME TEAM identity of the game (NHL
teams play their home schedule at one consistent home arena within a
season) -- a real, disclosed proxy, not a separate venue/building
dataset (which this project does not have).
"""
from __future__ import annotations

import statistics
from collections import defaultdict


def game_arena(team: str, opponent: str, home_or_away: str) -> str:
    return team if home_or_away == "HOME" else opponent


class ArenaRates:
    """Fit ONLY on the rows passed in (TUNING-scoped by the caller for
    PIT safety in the predictive sense; see module docstring for the
    descriptive-vs-predictive framing)."""

    def __init__(self, rows_with_residuals: list[dict], k_arena: int = 300, k_player_arena: int = 20):
        self.k_arena = k_arena
        self.k_player_arena = k_player_arena
        by_arena = defaultdict(list)
        for r in rows_with_residuals:
            by_arena[r["arena"]].append(r["residual"])
        self.league_mean_residual = statistics.fmean(r["residual"] for r in rows_with_residuals) \
            if rows_with_residuals else 0.0
        self.arena_n = {a: len(v) for a, v in by_arena.items()}
        self.arena_raw_mean = {a: statistics.fmean(v) for a, v in by_arena.items()}

        by_player_arena = defaultdict(list)
        for r in rows_with_residuals:
            by_player_arena[(r["player_id"], r["arena"])].append(r["residual"])
        self.player_arena_n = {k: len(v) for k, v in by_player_arena.items()}
        self.player_arena_raw_mean = {k: statistics.fmean(v) for k, v in by_player_arena.items()}

    def arena_shrunk_residual(self, arena: str) -> float:
        n = self.arena_n.get(arena, 0)
        if n == 0:
            return self.league_mean_residual
        w = n / (n + self.k_arena)
        return self.league_mean_residual + w * (self.arena_raw_mean[arena] - self.league_mean_residual)

    def player_arena_shrunk_residual(self, player_id: str, arena: str) -> float:
        prior = self.arena_shrunk_residual(arena)
        n = self.player_arena_n.get((player_id, arena), 0)
        if n == 0:
            return prior
        w = n / (n + self.k_player_arena)
        return prior + w * (self.player_arena_raw_mean[(player_id, arena)] - prior)
