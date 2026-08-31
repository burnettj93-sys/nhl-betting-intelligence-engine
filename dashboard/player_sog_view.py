"""
View logic for the Player SOG Research dashboard page (Part 41/42/43 of
PLAYER_SOG_FOUNDATION_REPORT.md).

STATUS: RESEARCH -- NOT YET A BETTING RECOMMENDATION. No sportsbook odds
are read or shown here (Part 36/37). Every probability is reconstructed
from real, PIT-safe historical data as of a chosen past date -- never a
claim about a live future lineup. Lineup status is always labeled
PROJECTED ACTIVE, never CONFIRMED ACTIVE (Part 43) -- this module has no
code path that could produce that label, since it never reads
target-game appearance.
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely
from research.player_sog.live_projection import project_player_sog  # noqa: F401  (re-exported)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "player_sog_results.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_PATH)
