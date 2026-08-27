"""
Model E-lite — goalie performance (spec sec.22, simplified to save% since
the demo data doesn't carry shot-danger location; upgrade path in README).

Regresses toward league-average save% by sample size, per the spec's
explicit instruction (sec.22/50): small samples must not be trusted, and
an unconfirmed starter should widen uncertainty rather than being ignored.
"""
from __future__ import annotations

import config


class GoalieRatingModel:
    def __init__(self):
        self.saves: dict[str, int] = {}
        self.shots_against: dict[str, int] = {}
        self.league_save_pct = 0.905   # seed guess; not re-estimated live in v1

    def starts(self, player_id: str) -> int:
        return 1 if player_id in self.shots_against and self.shots_against[player_id] else 0

    def save_pct(self, player_id: str) -> float:
        sa = self.shots_against.get(player_id, 0)
        if sa == 0:
            return self.league_save_pct
        return self.saves[player_id] / sa

    def sample_size(self, player_id: str) -> int:
        return self.shots_against.get(player_id, 0)

    def rating_adjustment_elo(self, player_id: str | None, confirmed: bool) -> tuple[float, float]:
        """Returns (elo_delta, uncertainty_multiplier). If no goalie is
        known yet, contributes nothing and widens uncertainty."""
        if player_id is None:
            return 0.0, config.UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING
        sample = self.sample_size(player_id)
        shrink = sample / (sample + config.GOALIE_SHRINKAGE_STARTS * 25)  # 25 shots/start-ish
        sv = self.save_pct(player_id)
        delta = (sv - self.league_save_pct) * shrink * config.SAVE_PCT_TO_ELO
        widening = 1.0 if confirmed else config.UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING
        return delta, widening

    def update(self, player_id: str, saves: int, shots_against: int) -> None:
        self.saves[player_id] = self.saves.get(player_id, 0) + saves
        self.shots_against[player_id] = self.shots_against.get(player_id, 0) + shots_against
