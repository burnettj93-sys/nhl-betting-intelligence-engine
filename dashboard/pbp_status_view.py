"""
Part 36: small read-only view helper for the Play-by-Play Data Status
dashboard page. Reads the real manifests already written by
research/real_nhl_pbp/{build_pbp_pilot,run_pilot_validation,build_pbp_season}.py
-- never makes a network call itself, matching the existing Data Status
page's own "reads a cached snapshot only" convention.
"""
from __future__ import annotations

import os

from dashboard.data_access import load_json_safely

PBP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "research", "real_nhl_pbp")


def _load_json(name: str) -> dict | None:
    return load_json_safely(os.path.join(PBP_DIR, name))


ALL_CORPUS_SEASONS = ("20222023", "20232024", "20242025", "20252026")


def load_status() -> dict:
    pilot_manifest = _load_json("pilot_manifest.json")
    pilot_validation = _load_json("pilot_validation_results.json")
    season_manifest = _load_json("season_ingestion_manifest.json")
    four_season_manifest = _load_json("four_season_ingestion_manifest.json")

    from research.real_nhl_pbp import raw_archive

    archived = {s: len(raw_archive.archived_game_ids(s)) for s in ALL_CORPUS_SEASONS}
    total_games = sum(archived.values())

    return {
        "pilot_manifest": pilot_manifest,
        "pilot_validation": pilot_validation,
        "season_manifest": season_manifest,
        "four_season_manifest": four_season_manifest,
        "archived_games_by_season": archived,
        "total_games": total_games,
        "expected_total_games": 5248,
        "coverage_pct": round(100.0 * total_games / 5248, 1) if total_games else 0.0,
    }


def load_corpus_manifest() -> dict | None:
    """The Part 34 corpus manifest, if it has been built yet."""
    return _load_json("corpus_manifest.json")
