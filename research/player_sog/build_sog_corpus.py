"""
Builds the ARCHIVAL player-game SHOTS-ON-GOAL research corpus: one row
per real skater-game (situation == "all", i.e. full-game totals), from
research/player_sog/raw/*.csv (real MoneyPuck skater game-by-game data,
peter-tanner.com CDN -- see raw/provenance.json). Cross-validates every
gameId against research/real_nhl_results/ (already proven: 100% match,
zero playoff contamination -- see provenance.json's cross_validation
note).

Each row also carries a nested "pp" block pulled from the SAME
skater-game's "5on4" situation row (power-play-specific icetime/shot
volume) when one exists -- a player with zero 5on4 rows that game simply
gets pp=None (means "did not play any 5-on-4," not "unknown").

Actual SOG (`sog`) is the TARGET LABEL for this corpus. It must never be
used as a PREGAME feature for its own game -- see
research/player_sog/features.py's `player_history_as_of()` gate, which
is the only place downstream code is allowed to read this file through.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.moneypuck_ingestion.ingest import derive_game_type, derive_nhl_season, REGULAR_SEASON_GAME_TYPE

RAW_DIR = Path(__file__).resolve().parent / "raw"
OUT_PATH = Path(__file__).resolve().parent / "player_game_sog.jsonl"
SEASONS = [2022, 2023, 2024, 2025]


def _iso_date(raw: str) -> str:
    """MoneyPuck's gameDate is YYYYMMDD -- convert to the same YYYY-MM-DD
    string format every other real corpus in this project uses, so PIT
    string comparisons (`<`) behave identically everywhere."""
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _f(row: dict, key: str) -> float:
    return float(row[key])


def build_corpus() -> dict:
    real_corpus_games = {g["game_id"]: g for g in ec.load_corpus(str(
        REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))}

    stats = {"raw_all_situation_rows_read": 0, "regular_season_rows": 0,
             "excluded_not_in_real_corpus": 0, "rows_written": 0, "rows_with_pp_data": 0}

    written = []
    for season in SEASONS:
        path = RAW_DIR / f"{season}.csv"
        # Pass 1: index every 5on4 (power-play) row for this season by
        # (playerId, gameId), so the "all"-situation pass below can attach
        # PP-specific icetime/shot volume without a second file read.
        pp_by_key: dict[tuple[str, int], dict] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row["situation"] != "5on4":
                    continue
                pp_by_key[(row["playerId"], int(row["gameId"]))] = row

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["situation"] != "all":
                    continue
                stats["raw_all_situation_rows_read"] += 1
                game_id = int(row["gameId"])
                if derive_game_type(game_id) != REGULAR_SEASON_GAME_TYPE:
                    continue
                stats["regular_season_rows"] += 1
                if game_id not in real_corpus_games:
                    stats["excluded_not_in_real_corpus"] += 1
                    continue

                game = real_corpus_games[game_id]
                pp_row = pp_by_key.get((row["playerId"], game_id))
                pp_block = None
                if pp_row is not None and _f(pp_row, "icetime") > 0:
                    stats["rows_with_pp_data"] += 1
                    pp_block = {
                        "icetime_seconds": _f(pp_row, "icetime"),
                        "shots_on_goal": _f(pp_row, "I_F_shotsOnGoal"),
                        "shot_attempts": _f(pp_row, "I_F_shotAttempts"),
                        "x_on_goal": _f(pp_row, "I_F_xOnGoal"),
                    }

                written.append({
                    "player_id": row["playerId"],
                    "player_name": row["name"],
                    "game_id": game_id,
                    "season": derive_nhl_season(int(row["season"])),
                    "game_date": _iso_date(row["gameDate"]),
                    "team": row["playerTeam"],
                    "opponent": row["opposingTeam"],
                    "home_or_away": row["home_or_away"],
                    "position": row["position"],
                    "icetime_seconds": _f(row, "icetime"),
                    "sog": _f(row, "I_F_shotsOnGoal"),
                    "shot_attempts": _f(row, "I_F_shotAttempts"),
                    "unblocked_shot_attempts": _f(row, "I_F_unblockedShotAttempts"),
                    "x_on_goal": _f(row, "I_F_xOnGoal"),
                    "x_goals": _f(row, "I_F_xGoals"),
                    "rebounds": _f(row, "I_F_rebounds"),
                    "low_danger_shots": _f(row, "I_F_lowDangerShots"),
                    "medium_danger_shots": _f(row, "I_F_mediumDangerShots"),
                    "high_danger_shots": _f(row, "I_F_highDangerShots"),
                    "on_ice_xgf": _f(row, "OnIce_F_xGoals"),
                    "on_ice_xga": _f(row, "OnIce_A_xGoals"),
                    "pp": pp_block,
                    "provenance_type": "ARCHIVAL_RESEARCH",
                })
                stats["rows_written"] += 1

    written.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    with open(OUT_PATH, "w") as f:
        for row in written:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    stats["unique_players"] = len({r["player_id"] for r in written})
    return stats


if __name__ == "__main__":
    stats = build_corpus()
    print(json.dumps(stats, indent=2))
    print(f"wrote {OUT_PATH}")
