"""
Model A — dynamic team strength (spec sec.38). A straightforward Elo
system with a home-ice bump. Deliberately simple: this is the layer
everything else (player, goalie, rest) adjusts around, so it should be
easy to reason about and to recalibrate (ELO_K_FACTOR, ELO_HOME_ADVANTAGE
in config.py) once you have real results to tune against.
"""
from __future__ import annotations

import config


class EloModel:
    def __init__(self, teams: list[str]):
        self.ratings = {t: config.ELO_START for t in teams}
        self._current_season: str | None = None

    def win_probability(self, home_team: str, away_team: str, extra_home_adj: float = 0.0,
                         extra_away_adj: float = 0.0) -> float:
        home_r = self.ratings[home_team] + config.ELO_HOME_ADVANTAGE + extra_home_adj
        away_r = self.ratings[away_team] + extra_away_adj
        return 1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))

    def maybe_regress_new_season(self, season_label: str) -> None:
        if self._current_season is not None and season_label != self._current_season:
            for t in self.ratings:
                self.ratings[t] += (config.ELO_START - self.ratings[t]) * config.ELO_SEASON_REGRESSION
        self._current_season = season_label

    def update(self, home_team: str, away_team: str, home_won: bool) -> None:
        """Deliberately uses the BASE Elo expectation (no extra_home_adj /
        extra_away_adj) rather than the fully player+goalie+rest-adjusted
        pregame probability the pricing engine actually bets on. See
        config.ELO_UPDATES_ON_BASE_EXPECTATION for the rationale, and
        tests/test_elo_update_rule.py for a test that pins this down."""
        assert config.ELO_UPDATES_ON_BASE_EXPECTATION, (
            "this method only implements the base-expectation update rule"
        )
        p_home = self.win_probability(home_team, away_team)
        actual = 1.0 if home_won else 0.0
        delta = config.ELO_K_FACTOR * (actual - p_home)
        self.ratings[home_team] += delta
        self.ratings[away_team] -= delta
