"""
Builds the ARCHIVAL player-game ASSISTS research corpus, from the SAME
already-archived real MoneyPuck skater files used for SOG/blocks (no new
download). Target label: `I_F_primaryAssists + I_F_secondaryAssists`
(MoneyPuck's own split-out assist columns, summed to total assists per
MoneyPuck's data dictionary -- no combined "assists" column exists, so
this is a documented sum, not an assumption).
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

RAW_DIR = REPO_ROOT / "research" / "player_sog" / "raw"
OUT_PATH = Path(__file__).resolve().parent / "player_game_assists.jsonl"
SEASONS = [2022, 2023, 2024, 2025]


def _iso_date(raw: str) -> str:
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
        pp_by_key: dict[tuple[str, int], dict] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row["situation"] != "5on4":
                    continue
                pp_by_key[(row["playerId"], int(row["gameId"]))] = row

        with open(path, newline="") as f:
            for row in csv.DictReader(f):
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
                    assists_pp = _f(pp_row, "I_F_primaryAssists") + _f(pp_row, "I_F_secondaryAssists")
                    pp_block = {"icetime_seconds": _f(pp_row, "icetime"), "assists": assists_pp}

                written.append({
                    "player_id": row["playerId"], "player_name": row["name"], "game_id": game_id,
                    "season": derive_nhl_season(int(row["season"])), "game_date": _iso_date(row["gameDate"]),
                    "team": row["playerTeam"], "opponent": row["opposingTeam"],
                    "home_or_away": row["home_or_away"], "position": row["position"],
                    "icetime_seconds": _f(row, "icetime"),
                    "assists": _f(row, "I_F_primaryAssists") + _f(row, "I_F_secondaryAssists"),
                    "points": _f(row, "I_F_points"), "on_ice_xgf": _f(row, "OnIce_F_xGoals"),
                    "individual_xg": _f(row, "I_F_xGoals"), "pp": pp_block,
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
