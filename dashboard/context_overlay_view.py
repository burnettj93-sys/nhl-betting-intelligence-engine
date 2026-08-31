"""
Context-State Probability Overlay -- view helper. Reads the frozen
driver output and registry from research/run_context_overlay_model.py
and research/context_overlay/registry.py. Never recomputes a
probability or adjustment live.

STATUS: RESEARCH CONTEXT OVERLAY -- NOT YET A BETTING ADJUSTMENT. No
decision_policy change was made this slice; nothing here feeds
run_slate.py. No sportsbook odds are read or shown here.
See CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md.
"""
from __future__ import annotations

from dashboard.data_access import load_json_safely
from research.context_overlay.registry import RESULTS_PATH, REGISTRY_PATH

RESEARCH_OVERLAY_DISCLAIMER = (
    "RESEARCH CONTEXT OVERLAY -- NOT YET A BETTING ADJUSTMENT. Adjusted probabilities are shown "
    "alongside raw frozen probabilities for research review only; decision_policy v3 is unchanged "
    "and existing LOW-confidence WATCH_ONLY restrictions still apply regardless of this overlay. "
    "No sportsbook odds are read or shown here."
)

RESULTS_JSON_PATH = str(RESULTS_PATH)
REGISTRY_JSON_PATH = str(REGISTRY_PATH)


def load_results() -> dict | None:
    return load_json_safely(RESULTS_JSON_PATH)


def load_registry() -> list[dict] | None:
    return load_json_safely(REGISTRY_JSON_PATH)
