"""
Builds the ARCHIVAL player-game BLOCKED-SHOTS research corpus, from the
SAME already-archived real MoneyPuck skater game-by-game files used for
the SOG corpus (research/player_sog/raw/*.csv -- no new download
needed). Real field audited directly against MoneyPuck's own data
dictionary (research/moneypuck_review/data_dictionaries/
MoneyPuckDataDictionaryForPlayers.csv): `shotsBlockedByPlayer` =
"Number of shot attempts blocked by the player" -- the correct target
label for a PLAYER BLOCKED SHOTS prop. NOT `I_F_blockedShotAttempts`,
which is the opposite concept (this player's OWN shot attempts that
were blocked BY THE OPPONENT).

Also carries a nested "pk" block from the same skater-game's "4on5"
situation row (penalty-kill-specific icetime/blocks) -- PK usage is a
plausible, testable blocks-specific feature (Part H: "PK TOI if
available"), mirroring the SOG corpus's "pp" block pattern exactly.
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

RAW_DIR = REPO_ROOT / "research" / "player_sog" / "raw"  # reuse the SAME already-archived skater files
OUT_PATH = Path(__file__).resolve().parent / "player_game_blocks.jsonl"
SEASONS = [2022, 2023, 2024, 2025]


def _iso_date(raw: str) -> str:
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _f(row: dict, key: str) -> float:
    return float(row[key])


def build_corpus() -> dict:
    real_corpus_games = {g["game_id"]: g for g in ec.load_corpus(str(
        REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))}

    stats = {"raw_all_situation_rows_read": 0, "regular_season_rows": 0,
             "excluded_not_in_real_corpus": 0, "rows_written": 0, "rows_with_pk_data": 0}

    written = []
    for season in SEASONS:
        path = RAW_DIR / f"{season}.csv"
        pk_by_key: dict[tuple[str, int], dict] = {}
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row["situation"] != "4on5":
                    continue
                pk_by_key[(row["playerId"], int(row["gameId"]))] = row

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
                pk_row = pk_by_key.get((row["playerId"], game_id))
                pk_block = None
                if pk_row is not None and _f(pk_row, "icetime") > 0:
                    stats["rows_with_pk_data"] += 1
                    pk_block = {
                        "icetime_seconds": _f(pk_row, "icetime"),
                        "blocks": _f(pk_row, "shotsBlockedByPlayer"),
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
                    "blocks": _f(row, "shotsBlockedByPlayer"),
                    "hits": _f(row, "I_F_hits"),
                    "shot_attempts_against_on_ice": _f(row, "OnIce_A_shotAttempts"),
                    "pk": pk_block,
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
