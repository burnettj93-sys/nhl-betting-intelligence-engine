"""
Builds the ARCHIVAL player-game GOALS research corpus, from the SAME
already-archived real MoneyPuck skater game-by-game files used for
SOG/blocks/assists/points (research/player_sog/raw/*.csv -- no new
download).

PART 1 DATA AUDIT (real fields, MoneyPuck skater CSV, "all"-situation
rows unless noted -- read directly from the raw CSV header, not
inferred):
  goals                     -> I_F_goals
  SOG                       -> I_F_shotsOnGoal
  shot attempts             -> I_F_shotAttempts
  unblocked attempts        -> I_F_unblockedShotAttempts
  individual xG             -> I_F_xGoals
  high-danger attempts      -> I_F_highDangerShots
  medium-danger attempts    -> I_F_mediumDangerShots
  low-danger attempts       -> I_F_lowDangerShots
  high/medium/low-danger xG -> I_F_highDangerxGoals / I_F_mediumDangerxGoals / I_F_lowDangerxGoals
  rebounds                  -> I_F_rebounds
  rebound goals             -> I_F_reboundGoals
  total TOI                 -> icetime (situation="all")
  5v5 TOI                   -> icetime (situation="5on5")
  PP TOI / PP goals/SOG/xG  -> icetime, I_F_goals/I_F_shotsOnGoal/I_F_xGoals (situation="5on4")
  player/team/opponent/game/date/season/home-away/position -> playerId/playerTeam/opposingTeam/
                              gameId/gameDate/season/home_or_away/position

No "rush attempt" field exists in this MoneyPuck skater export (checked
directly against the raw header) -- NOT built, not inferred, reported
honestly rather than invented.
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
OUT_PATH = Path(__file__).resolve().parent / "player_game_goals.jsonl"
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

                key = (row["playerId"], game_id)
                pp_row = pp_by_key.get(key)
                pp_block = None
                if pp_row is not None and _f(pp_row, "icetime") > 0:
                    stats["rows_with_pp_data"] += 1
                    pp_block = {
                        "icetime_seconds": _f(pp_row, "icetime"), "goals": _f(pp_row, "I_F_goals"),
                        "sog": _f(pp_row, "I_F_shotsOnGoal"), "individual_xg": _f(pp_row, "I_F_xGoals"),
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
                    "goals": _f(row, "I_F_goals"), "sog": _f(row, "I_F_shotsOnGoal"),
                    "shot_attempts": _f(row, "I_F_shotAttempts"),
                    "unblocked_attempts": _f(row, "I_F_unblockedShotAttempts"),
                    "individual_xg": _f(row, "I_F_xGoals"),
                    "high_danger_shots": _f(row, "I_F_highDangerShots"),
                    "medium_danger_shots": _f(row, "I_F_mediumDangerShots"),
                    "low_danger_shots": _f(row, "I_F_lowDangerShots"),
                    "high_danger_xg": _f(row, "I_F_highDangerxGoals"),
                    "rebounds": _f(row, "I_F_rebounds"), "rebound_goals": _f(row, "I_F_reboundGoals"),
                    "points": _f(row, "I_F_points"), "assists": _f(row, "I_F_primaryAssists") + _f(row, "I_F_secondaryAssists"),
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
