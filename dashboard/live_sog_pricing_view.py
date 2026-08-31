"""
View logic for the Live SOG Markets dashboard page.

STATUS: LIVE MODEL VS MARKET (research) -- NO AUTOMATIC BETTING. This
module NEVER makes a network call itself -- it only reads the cached
board snapshot research/live_sog_board_cache.json, written by the
explicit `python3 -m research.live_sog_pricing.refresh` action (Part:
"dashboard must read cached normalized market state... a separate
explicit refresh action... should control API retrieval").
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely

REPO_ROOT = Path(__file__).resolve().parent.parent
BOARD_CACHE_PATH = REPO_ROOT / "research" / "live_sog_board_cache.json"


def load_board_cache() -> dict | None:
    return load_json_safely(BOARD_CACHE_PATH)
