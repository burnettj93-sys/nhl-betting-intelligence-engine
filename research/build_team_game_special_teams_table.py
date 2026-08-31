"""
Builds and persists the FULL (game_id, team_id) special-teams table (all
10,496 rows -- the Part 7 corpus itself, not just a summary), needed as
the real per-team-game PP/PK-seconds denominator for player PP/PK role
-share calculations in special_teams_roles.py. The summary-only
research/special_teams_corpus_results.json intentionally did not embed
every row (its own stated reason: bloat) -- this is the separate, full
table that role analysis actually needs to join against.

Run manually:
    python3 -m research.build_team_game_special_teams_table
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.period_event_timing import special_teams_corpus as stc
from research.period_event_timing.team_ids import TEAM_ID_TO_ABBREV
from research.real_nhl_pbp.store import DB_PATH

OUT_PATH = REPO_ROOT / "research" / "team_game_special_teams_table.jsonl"


def build(db_path: str = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT game_id, home_team_id, away_team_id, game_date, season FROM pbp_games ORDER BY game_id")
    games = cur.fetchall()

    n = 0
    with open(OUT_PATH, "w") as f:
        for game_id, home_id, away_id, game_date, season in games:
            rows = stc.build_team_game_special_teams(conn, game_id, home_id, away_id)
            for team_id, r in rows.items():
                row = dict(r)
                row.update({
                    "game_id": game_id, "team_id": team_id, "team": TEAM_ID_TO_ABBREV.get(team_id),
                    "game_date": game_date, "season": season,
                })
                f.write(json.dumps(row, sort_keys=True) + "\n")
                n += 1
    return n


if __name__ == "__main__":
    n = build()
    print(f"wrote {n} rows to {OUT_PATH}")
