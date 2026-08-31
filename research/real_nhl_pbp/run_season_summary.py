"""
Parts 25-27: post-ingestion season-scale summary. Reads every archived raw
payload for a season and reports real counts -- no modeling, no new
network calls (all data is already archived).
"""
from __future__ import annotations

import collections
import json
import os

from research.real_nhl_pbp import normalize, raw_archive

SEASON = "20252026"
SUMMARY_PATH = os.path.join(os.path.dirname(__file__), "season_summary.json")


def dir_size_bytes(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


def run(season: str = SEASON) -> dict:
    game_ids = raw_archive.archived_game_ids(season)
    event_type_counts = collections.Counter()
    total_events = 0

    player_field_present = collections.Counter()
    player_field_total = collections.Counter()

    for gid in game_ids:
        raw = raw_archive.load_raw_pbp(season, gid)
        events = normalize.normalize_game_events(raw)
        total_events += len(events)
        for e in events:
            event_type_counts[e.event_type] += 1
            expected_roles = {
                "goal": ("scorer",), "shot-on-goal": ("shooter",), "missed-shot": ("shooter",),
                "blocked-shot": ("shooter", "blocker"), "hit": ("hitter", "hittee"),
                "penalty": ("committed_by",), "faceoff": ("winner", "loser"),
            }.get(e.event_type, ())
            for role in expected_roles:
                player_field_total[f"{e.event_type}.{role}"] += 1
                if role in e.players:
                    player_field_present[f"{e.event_type}.{role}"] += 1

    coverage = {
        key: {"present": player_field_present[key], "total": total,
              "pct": round(100.0 * player_field_present[key] / total, 2) if total else None}
        for key, total in player_field_total.items()
    }

    raw_dir = os.path.join(raw_archive.RAW_ROOT, season)
    summary = {
        "season": season,
        "games_archived": len(game_ids),
        "total_normalized_events": total_events,
        "avg_events_per_game": round(total_events / len(game_ids), 1) if game_ids else None,
        "event_type_counts": dict(event_type_counts.most_common()),
        "player_id_coverage": coverage,
        "raw_storage_bytes": dir_size_bytes(raw_dir),
        "raw_storage_mb": round(dir_size_bytes(raw_dir) / (1024 * 1024), 1),
    }
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    return summary


if __name__ == "__main__":
    result = run()
    print(json.dumps({k: v for k, v in result.items()
                       if k not in ("event_type_counts", "player_id_coverage")}, indent=2))
    print(json.dumps(result["event_type_counts"], indent=2))
    print(json.dumps(result["player_id_coverage"], indent=2))
