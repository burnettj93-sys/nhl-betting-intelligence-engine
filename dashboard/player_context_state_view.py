"""
Player Context State -- view helper. Reads the frozen driver output and
registry produced by research/run_player_context_state_model.py and
research/player_context_state/registry.py. Never recomputes a marginal
or a context classification in the dashboard layer.

STATUS: RESEARCH -- NOT YET A BETTING ADJUSTMENT. No decision_policy
change was made this slice; nothing here feeds run_slate.py. See
PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.player_context_state.registry import RESULTS_PATH, REGISTRY_PATH

RESEARCH_DISCLAIMER = (
    "RESEARCH -- NOT YET A BETTING ADJUSTMENT. This page reports frozen, out-of-sample "
    "residual measurements against the existing SOG/Goals/Assists/Points/Blocks marginals. "
    "No decision_policy change has been made. No sportsbook odds are read or shown here. "
    "MEDIA_SENTIMENT_STATE is NOT BUILT -- no legitimate historical media/news corpus exists "
    "in this project."
)

RESULTS_JSON_PATH = str(RESULTS_PATH)
REGISTRY_JSON_PATH = str(REGISTRY_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


def load_registry() -> list[dict] | None:
    return load_json_safely(REGISTRY_JSON_PATH)
