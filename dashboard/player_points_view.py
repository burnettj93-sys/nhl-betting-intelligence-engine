"""
View logic for the Player Points Research dashboard page.

STATUS: PARTIAL -- see research/player_props/registry.py's POINTS entry
and PLAYER_POINTS_VALIDATION_REPORT.md. Not yet VALIDATED: the locked
model beats 3 of 4 naive baselines but loses to the simple per-player
empirical-distribution baseline at every threshold, a real, replicated
finding surfaced on this page, not hidden.
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely
from research.player_points.live_projection import project_player_points  # noqa: F401 (re-exported)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "player_points_results.json"
MANIFEST_PATH = REPO_ROOT / "research" / "player_points_freeze_manifest.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_PATH)


def load_manifest() -> dict | None:
    return load_json_safely(MANIFEST_PATH)
