"""
View logic for the Player Goals Research dashboard page.

STATUS: GOALS 1+ VALIDATED -- see research/player_props/registry.py's
GOALS entry and PLAYER_GOALS_VALIDATION_REPORT.md. 2+ GOALS remains
INSUFFICIENT DATA (fails only the per-confidence-bucket support check).
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely
from research.player_goals.live_projection import project_player_goals  # noqa: F401 (re-exported)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "player_goals_results.json"
MANIFEST_PATH = REPO_ROOT / "research" / "player_goals_freeze_manifest.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_PATH)


def load_manifest() -> dict | None:
    return load_json_safely(MANIFEST_PATH)
