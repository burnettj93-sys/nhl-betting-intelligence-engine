"""
Builds the ARCHIVAL goalie-quality APPEARANCE corpus: one row per real
goalie appearance (any icetime > 0, starter OR reliever), from the SAME
already-archived raw MoneyPuck goalie files used to build the starter
corpus (research/goalie_intelligence/raw/*.csv -- see that file's
provenance.json; no new download needed for this slice).

DIFFERENT FROM THE STARTER CORPUS (build_starter_corpus.py) ON PURPOSE:
that corpus is team-scoped (one row per team-game, the STARTER only) and
feeds who-plays inference; this corpus is GOALIE-scoped (one row per
appearance, any goalie who saw the ice) and feeds how-good-is-he
inference (Part 17: "goalie quality should follow the goalie identity,
not reset because he changes teams" -- so this file is keyed by
goalie_id first, not team). A goalie can appear in both corpora for the
same game (as the starter) or only here (as a reliever who wasn't the
starter).

Fields kept (Part 13's audit): icetime_seconds, shots_against (MoneyPuck's
`ongoal` -- shots that reached the goalie), goals_against (`goals`),
xg_against (`xGoals` -- expected goals against, from this goalie's own
faced-shots). No danger-tier or rebound fields are carried into this
corpus -- Part 14 asks for only a FEW transparent formulas, and neither
candidate quality metric this slice uses those.
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
OUT_PATH = Path(__file__).resolve().parent / "goalie_appearances.jsonl"
SEASONS = [2022, 2023, 2024, 2025]


def build_corpus() -> dict:
    real_corpus_games = {g["game_id"]: g for g in ec.load_corpus(str(
        REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))}

    stats = {"raw_goalie_rows_read": 0, "regular_season_appearance_rows": 0,
             "excluded_not_in_real_corpus": 0, "excluded_zero_icetime": 0, "rows_written": 0}

    written = []
    for season in SEASONS:
        path = RAW_DIR / f"{season}.csv"
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["situation"] != "all" or row["position"] != "G":
                    continue
                stats["raw_goalie_rows_read"] += 1
                game_id = int(row["gameId"])
                if derive_game_type(game_id) != REGULAR_SEASON_GAME_TYPE:
                    continue
                stats["regular_season_appearance_rows"] += 1
                if game_id not in real_corpus_games:
                    stats["excluded_not_in_real_corpus"] += 1
                    continue
                icetime = float(row["icetime"])
                if icetime <= 0:
                    stats["excluded_zero_icetime"] += 1
                    continue

                game = real_corpus_games[game_id]
                shots_against = float(row["ongoal"])
                goals_against = float(row["goals"])
                written.append({
                    "goalie_id": row["playerId"],
                    "goalie_name": row["name"],
                    "game_id": game_id,
                    "season": derive_nhl_season(int(row["season"])),
                    "game_date": game["game_date"],
                    "team": row["playerTeam"],
                    "opponent": row["opposingTeam"],
                    "icetime_seconds": icetime,
                    "shots_against": shots_against,
                    "saves": shots_against - goals_against,
                    "goals_against": goals_against,
                    "xg_against": float(row["xGoals"]),
                    "provenance_type": "ARCHIVAL_RESEARCH",
                })
                stats["rows_written"] += 1

    written.sort(key=lambda r: (r["game_date"], r["game_id"], r["goalie_id"]))
    with open(OUT_PATH, "w") as f:
        for row in written:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    stats["unique_goalies"] = len({r["goalie_id"] for r in written})
    return stats


if __name__ == "__main__":
    stats = build_corpus()
    print(json.dumps(stats, indent=2))
    print(f"wrote {OUT_PATH}")
