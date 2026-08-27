"""
Model C-lite — player-level performance (spec sec.13-15, deliberately
simplified).

This is a v1 heuristic, NOT RAPM/GAR/xGAR and not a complete player-impact
model: it is a rolling exponentially-weighted average of points-per-game
per player, compared to a fixed league-average prior, converted into
Elo-equivalent rating points via config.POINTS_PER_GAME_TO_ELO. It has no
context adjustment for teammates, opponents, zone starts, or competition
quality — those are the real upgrade path (see README "what's next").

Player quality is an explicit COMPARATIVE input: team_available_quality_elo()
returns an absolute Elo-equivalent quality figure for whichever players are
actually available, so two full-strength rosters of different talent are
told apart (not just "missing player = penalty, healthy = 0" for every
team regardless of who's actually on it). The combined model takes the
difference between the home and away figures, same as it does for Elo.

Ratings update strictly after a game's own stats are known (see
CombinedMoneylineModel.learn), so a query at prediction time only ever
reflects games already learned from — no leakage.
"""
from __future__ import annotations

import config


class PlayerRatingModel:
    def __init__(self):
        self.ratings: dict[str, float] = {}   # points-per-game EWMA
        self.games_seen: dict[str, int] = {}
        self.league_avg = 0.35   # fixed prior; NOT re-estimated from data (no leakage risk)

    def rating(self, player_id: str) -> float:
        return self.ratings.get(player_id, self.league_avg)

    def games_played(self, player_id: str) -> int:
        return self.games_seen.get(player_id, 0)

    def _weighted_rating(self, player_id: str) -> float:
        gp = self.games_played(player_id)
        weight = min(gp / config.MIN_GAMES_FOR_FULL_PLAYER_WEIGHT, 1.0)
        r = self.rating(player_id)
        # shrink unproven players toward league average rather than trusting
        # a small sample (spec sec.57 overfitting controls)
        return self.league_avg + (r - self.league_avg) * weight

    def team_available_quality_elo(self, available_ids: list[str]) -> float:
        """Absolute Elo-equivalent quality of the players actually
        available tonight, relative to a replacement-level (league-average)
        lineup of the same size. This is comparative BETWEEN teams (a
        strong healthy roster scores higher than a weak healthy roster —
        not just 0 for both) and still naturally drops when key players
        are missing, since they simply aren't in available_ids."""
        if not available_ids:
            return 0.0
        excess = sum(self._weighted_rating(pid) - self.league_avg for pid in available_ids)
        return excess * config.POINTS_PER_GAME_TO_ELO

    def update(self, player_id: str, goals: int, assists: int) -> None:
        points = goals + assists
        prev = self.ratings.get(player_id, self.league_avg)
        alpha = config.PLAYER_RATING_EWMA_ALPHA
        self.ratings[player_id] = prev * (1 - alpha) + points * alpha
        self.games_seen[player_id] = self.games_seen.get(player_id, 0) + 1
