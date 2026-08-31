"""
Builds the ARCHIVAL player-game TOTAL POINTS research corpus, from the
SAME already-archived real MoneyPuck skater game-by-game files used for
SOG/blocks/assists (research/player_sog/raw/*.csv -- no new download).

PART 1 DATA AUDIT (real fields, MoneyPuck skater CSV, "all"-situation
rows unless noted):
  goals                -> I_F_goals
  assists               -> I_F_primaryAssists + I_F_secondaryAssists (no
                           combined column exists; same documented sum
                           used by research/player_assists/)
  points (direct field) -> I_F_points
  primary assists       -> I_F_primaryAssists
  secondary assists     -> I_F_secondaryAssists
  SOG                    -> I_F_shotsOnGoal
  shot attempts          -> I_F_shotAttempts
  individual xG           -> I_F_xGoals
  on-ice xGF               -> OnIce_F_xGoals
  on-ice xGA               -> OnIce_A_xGoals
  total TOI                 -> icetime (situation="all")
  5v5 TOI                    -> icetime (situation="5on5")
  PP TOI / PP production       -> icetime, I_F_points/goals/assists (situation="5on4")
  player/team/opponent/game/date/season -> playerId/playerTeam/opposingTeam/gameId/gameDate/season

PART 1 CROSS-CHECK (run directly against the real 2024 season file
before writing this module): I_F_points == I_F_goals +
I_F_primaryAssists + I_F_secondaryAssists for all 47,224 "all"-situation
rows, zero mismatches. actual_points below is computed as I_F_points
directly (the provided field), with the sum cross-check re-run and
recorded in build stats for every season, not assumed once.
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
OUT_PATH = Path(__file__).resolve().parent / "player_game_points.jsonl"
SEASONS = [2022, 2023, 2024, 2025]


def _iso_date(raw: str) -> str:
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _f(row: dict, key: str) -> float:
    return float(row[key])


def build_corpus() -> dict:
    real_corpus_games = {g["game_id"]: g for g in ec.load_corpus(str(
        REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))}

    stats = {"raw_all_situation_rows_read": 0, "regular_season_rows": 0,
             "excluded_not_in_real_corpus": 0, "rows_written": 0, "rows_with_pp_data": 0,
             "points_goals_assists_cross_check_mismatches": 0,
             "toi_5v5_missing_rows": 0}

    written = []
    for season in SEASONS:
        path = RAW_DIR / f"{season}.csv"

        pp_by_key: dict[tuple[str, int], dict] = {}
        toi5v5_by_key: dict[tuple[str, int], float] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row["situation"] == "5on4":
                    pp_by_key[(row["playerId"], int(row["gameId"]))] = row
                elif row["situation"] == "5on5":
                    toi5v5_by_key[(row["playerId"], int(row["gameId"]))] = _f(row, "icetime")

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

                goals = _f(row, "I_F_goals")
                primary_assists = _f(row, "I_F_primaryAssists")
                secondary_assists = _f(row, "I_F_secondaryAssists")
                points_direct = _f(row, "I_F_points")
                points_summed = goals + primary_assists + secondary_assists
                if points_direct != points_summed:
                    stats["points_goals_assists_cross_check_mismatches"] += 1

                key = (row["playerId"], game_id)
                pp_row = pp_by_key.get(key)
                pp_block = None
                if pp_row is not None and _f(pp_row, "icetime") > 0:
                    stats["rows_with_pp_data"] += 1
                    pp_block = {
                        "icetime_seconds": _f(pp_row, "icetime"),
                        "points": _f(pp_row, "I_F_points"),
                        "goals": _f(pp_row, "I_F_goals"),
                        "assists": _f(pp_row, "I_F_primaryAssists") + _f(pp_row, "I_F_secondaryAssists"),
                    }

                toi_5v5 = toi5v5_by_key.get(key)
                if toi_5v5 is None:
                    stats["toi_5v5_missing_rows"] += 1

                written.append({
                    "player_id": row["playerId"], "player_name": row["name"], "game_id": game_id,
                    "season": derive_nhl_season(int(row["season"])), "game_date": _iso_date(row["gameDate"]),
                    "team": row["playerTeam"], "opponent": row["opposingTeam"],
                    "home_or_away": row["home_or_away"], "position": row["position"],
                    "icetime_seconds": _f(row, "icetime"),
                    "toi_5v5_seconds": toi_5v5 if toi_5v5 is not None else 0.0,
                    "goals": goals, "primary_assists": primary_assists, "secondary_assists": secondary_assists,
                    "assists": primary_assists + secondary_assists,
                    "points": points_direct,
                    "sog": _f(row, "I_F_shotsOnGoal"), "shot_attempts": _f(row, "I_F_shotAttempts"),
                    "individual_xg": _f(row, "I_F_xGoals"),
                    "on_ice_xgf": _f(row, "OnIce_F_xGoals"), "on_ice_xga": _f(row, "OnIce_A_xGoals"),
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
