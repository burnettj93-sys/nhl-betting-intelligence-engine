"""
Multi-season expansion driver (Parts 1-8): runs the exact same, unmodified
build_pbp_season.ingest_season() pipeline across all 4 regular seasons the
project's other research already covers (2022-23 through 2025-26). No
season-specific parsing fork -- Part 3's explicit requirement, and
justified here by the real contract-drift audit in
NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md Section H, which found the accepted
2025-26 contract holds identically across all 3 prior seasons.

Naturally resumable (Part 6): ingest_season() already skips any game_id
already present under raw_archive.archived_game_ids(season), so re-running
this driver after an interruption continues from the accepted completed
games rather than restarting from zero -- no separate checkpoint file is
needed because the raw archive itself IS the checkpoint.
"""
from __future__ import annotations

import json
import os

from research.real_nhl_pbp.build_pbp_season import ingest_season

ALL_SEASONS = (20222023, 20232024, 20242025, 20252026)
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "four_season_ingestion_manifest.json")


def run_all(seasons: tuple[int, ...] = ALL_SEASONS) -> dict:
    per_season = {}
    for season in seasons:
        per_season[str(season)] = ingest_season(season)
    manifest = {
        "seasons": list(seasons),
        "per_season": per_season,
        "total_games_retrieved": sum(r["games_retrieved_total"] for r in per_season.values()),
        "total_missing": sum(len(r["games_missing"]) for r in per_season.values()),
        "total_failures": sum(len(r["failures"]) for r in per_season.values()),
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = run_all()
    print(json.dumps({
        "seasons": result["seasons"],
        "total_games_retrieved": result["total_games_retrieved"],
        "total_missing": result["total_missing"],
        "total_failures": result["total_failures"],
    }, indent=2))
    for season, r in result["per_season"].items():
        print(season, {k: v for k, v in r.items() if k != "failures"})
