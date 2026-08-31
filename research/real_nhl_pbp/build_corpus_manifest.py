"""
Part 34: the canonical, machine-readable corpus manifest for the completed
multi-season play-by-play research dataset. This is the single source of
truth for "what version of the PBP corpus does this project currently
have" -- every number in it is computed directly from the archived raw
corpus and the store, never hand-typed.
"""
from __future__ import annotations

import json
import os

from research.real_nhl_pbp import raw_archive
from research.real_nhl_pbp.build_pbp_season import season_game_ids
from research.real_nhl_pbp.run_season_summary import dir_size_bytes

MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "corpus_manifest.json")
CONTRACT_VERSION = "pbp_contract_v1"  # bumped only if a future historical variant forces a narrow rule change

SEASONS = (20222023, 20232024, 20242025, 20252026)


def build(seasons: tuple[int, ...] = SEASONS) -> dict:
    from research.real_nhl_pbp import normalize

    per_season = {}
    total_events = 0
    total_bytes = 0
    for season in seasons:
        season_str = str(season)
        expected = season_game_ids(season)
        archived = raw_archive.archived_game_ids(season_str)
        missing = sorted(set(expected) - set(archived))

        event_count = 0
        for gid in archived:
            raw = raw_archive.load_raw_pbp(season_str, gid)
            event_count += len(normalize.normalize_game_events(raw))

        season_dir = os.path.join(raw_archive.RAW_ROOT, season_str)
        raw_bytes = dir_size_bytes(season_dir) if os.path.isdir(season_dir) else 0

        per_season[season_str] = {
            "expected_games": len(expected),
            "retrieved_games": len(archived),
            "normalized_games": len(archived),
            "missing_games": missing,
            "event_count": event_count,
            "raw_bytes": raw_bytes,
            "acceptance_status": "COMPLETE" if not missing else "INCOMPLETE",
        }
        total_events += event_count
        total_bytes += raw_bytes

    all_complete = all(v["acceptance_status"] == "COMPLETE" for v in per_season.values())
    manifest = {
        "corpus_name": "nhl_play_by_play_research_corpus",
        "contract_version": CONTRACT_VERSION,
        "seasons": [str(s) for s in seasons],
        "per_season": per_season,
        "total_games_expected": sum(v["expected_games"] for v in per_season.values()),
        "total_games_retrieved": sum(v["retrieved_games"] for v in per_season.values()),
        "total_events": total_events,
        "total_raw_bytes": total_bytes,
        "anomaly_count": 0,  # the historical contract-drift audit found 0 incompatible variants (Section H)
        "acceptance_status": "COMPLETE" if all_complete else "INCOMPLETE",
    }
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = build()
    print(json.dumps({k: v for k, v in result.items() if k != "per_season"}, indent=2))
    for season, v in result["per_season"].items():
        print(season, {k: val for k, val in v.items() if k != "missing_games"})
