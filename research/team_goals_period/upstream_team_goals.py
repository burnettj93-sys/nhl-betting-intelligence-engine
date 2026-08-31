"""
Part 5 / Section J: PIT-safe full-game TEAM GOAL expectation.

Unlike Player SOG by Period (which had an existing VALIDATED full-game
model to reuse), no validated full-game team-scoring MODEL exists in this
project (research/moneypuck_team_features.py exposes real, PIT-safe xG
rolling features, but no fitted goals-expectation model; models/elo_model.py
outputs only a single win-probability scalar, not decomposable into goals
-- confirmed by direct audit this slice). This module therefore builds a
NEW, honestly-disclosed, simple PIT-safe full-game team-goal prior from
REAL data (this project's own PBP-derived team-goal corpus, exact and
already reconciled against official final scores) -- a shrunk rolling mean
of the team's own full-game goals, home/away-aware, window=20, shrunk
toward the league mean by game count (same shrinkage convention used
throughout this project). It is NOT presented as "the existing validated
model recomputed" (Part J of the SOG-by-period report) because no such
model exists here -- it is presented as exactly what it is.
"""
from __future__ import annotations

import statistics


def rolling_full_game_mean(history: list[dict], window: int = 20) -> float | None:
    recent = history[-window:]
    if not recent:
        return None
    return statistics.fmean(r["full_game_team_goals"] for r in recent)


def shrunk_full_game_expectation(history: list[dict], home_away: str, rates, k_team: int = 60) -> float:
    """rates: a hierarchy.PeriodTeamRates instance (reused for its
    home/away full-game league prior, derived the same way its period
    priors are -- one source of truth for shrinkage targets)."""
    league_full_game_mean = sum(rates.league_mean.values())
    ha_rows = None
    prior = league_full_game_mean
    if home_away in rates.ha_mean:
        prior = sum(rates.ha_mean[home_away].values())
    n = len(history)
    if n == 0:
        return prior
    team_mean = statistics.fmean(r["full_game_team_goals"] for r in history)
    w = n / (n + k_team)
    return prior + w * (team_mean - prior)
