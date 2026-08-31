"""
Builds the ARCHIVAL historical-starter LABEL corpus from real MoneyPuck
goalie game-by-game data (research/goalie_intelligence/raw/*.csv,
situation='all' rows -- see raw/provenance.json for exact source URLs/
checksums). This is Part 9's "actual starter" target label -- explicitly
POSTGAME truth, never usable as a pregame feature (see
research/goalie_intelligence/features.py, which is built entirely
separately and never reads this file for the SAME game it is predicting;
enforced by STRICT PRIOR-GAME-DATE, same as every other research module
in this project).

STARTER-INFERENCE HEURISTIC: MoneyPuck's goalie file has no explicit
"started the game" flag. The standard hockey-analytics convention (used
here, not invented) is: the goalie with the MOST 'all'-situation icetime
for a team in a game is that team's starter. Verified against the real
data this turn: 94.1% of team-games (9,878 / 10,495) used exactly one
goalie for the whole game (unambiguous by construction). Of the 617
multi-goalie team-games (a goalie was pulled/relieved), the top goalie's
icetime SHARE of the two (or more) goalies' combined icetime is used as
a confidence check: below AMBIGUOUS_SHARE_THRESHOLD (0.55), the game's
label is marked ambiguous and EXCLUDED from the corpus rather than
guessed at -- 74 games (0.71% of all team-games) fall below this bar.
This is reported plainly, not hidden.

REGULAR SEASON ONLY, CROSS-VALIDATED: only games whose game_id is both
(a) a regular-season id (digits 5-6 == '02', same convention as
research/real_nhl_results) and (b) present in the accepted real NHL
corpus (research/real_nhl_results/normalized_regular_season_games.jsonl)
are kept -- this guarantees every row in this corpus has a matching,
already-validated real game_date/home_team/away_team/season to build
point-in-time features against.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.moneypuck_ingestion.ingest import derive_game_type, derive_nhl_season, REGULAR_SEASON_GAME_TYPE

RAW_DIR = Path(__file__).resolve().parent / "raw"
OUT_PATH = Path(__file__).resolve().parent / "actual_starters.jsonl"
SEASONS = [2022, 2023, 2024, 2025]

AMBIGUOUS_SHARE_THRESHOLD = 0.55


def load_raw_goalie_rows() -> list[dict]:
    rows = []
    for season in SEASONS:
        path = RAW_DIR / f"{season}.csv"
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["situation"] != "all" or row["position"] != "G":
                    continue
                rows.append(row)
    return rows


def build_corpus() -> dict:
    raw_rows = load_raw_goalie_rows()

    real_corpus_games = {g["game_id"]: g for g in ec.load_corpus(str(
        REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"))}

    by_team_game: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in raw_rows:
        game_id = int(row["gameId"])
        if derive_game_type(game_id) != REGULAR_SEASON_GAME_TYPE:
            continue
        by_team_game[(game_id, row["playerTeam"])].append(row)

    stats = {"raw_goalie_appearance_rows": len(raw_rows), "team_games_total": 0,
             "team_games_single_goalie": 0, "team_games_multi_goalie": 0,
             "team_games_ambiguous_excluded": 0, "team_games_not_in_real_corpus": 0,
             "team_games_written": 0}

    written = []
    for (game_id, team), appearances in by_team_game.items():
        stats["team_games_total"] += 1
        if game_id not in real_corpus_games:
            stats["team_games_not_in_real_corpus"] += 1
            continue
        game = real_corpus_games[game_id]

        sorted_appearances = sorted(appearances, key=lambda r: -float(r["icetime"]))
        total_icetime = sum(float(r["icetime"]) for r in sorted_appearances)
        top = sorted_appearances[0]
        share = (float(top["icetime"]) / total_icetime) if total_icetime > 0 else 0.0

        if len(sorted_appearances) == 1:
            stats["team_games_single_goalie"] += 1
        else:
            stats["team_games_multi_goalie"] += 1
            if share < AMBIGUOUS_SHARE_THRESHOLD:
                stats["team_games_ambiguous_excluded"] += 1
                continue

        opponent = game["away_team"] if game["home_team"] == team else game["home_team"]
        other_appearances = [
            {"goalie_id": r["playerId"], "goalie_name": r["name"], "icetime_seconds": float(r["icetime"])}
            for r in sorted_appearances[1:]
        ]
        written.append({
            "game_id": game_id,
            "season": derive_nhl_season(int(top["season"])),
            "game_date": game["game_date"],
            "team": team,
            "opponent": opponent,
            "starter_goalie_id": top["playerId"],
            "starter_goalie_name": top["name"],
            "starter_icetime_seconds": float(top["icetime"]),
            "starter_icetime_share": round(share, 4),
            "n_goalies_used": len(sorted_appearances),
            "other_appearances": other_appearances,
            "provenance_type": "ARCHIVAL_RESEARCH",
        })
        stats["team_games_written"] += 1

    written.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    with open(OUT_PATH, "w") as f:
        for row in written:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    stats["unique_goalies"] = len({r["starter_goalie_id"] for r in written})
    stats["unique_teams"] = len({r["team"] for r in written})
    return stats


if __name__ == "__main__":
    stats = build_corpus()
    print(json.dumps(stats, indent=2))
    print(f"wrote {OUT_PATH}")
