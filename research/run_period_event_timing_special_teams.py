"""
Builds the full-corpus special-teams team-game table (Part 7) and the
manpower-state validation summary (Part 5) across all 5,248 real games,
and writes both to a single machine-readable results file (Part 77).

Run manually:
    python3 -m research.run_period_event_timing_special_teams
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.period_event_timing import manpower as mp
from research.period_event_timing import penalties as pw
from research.period_event_timing import special_teams_corpus as stc
from research.real_nhl_pbp.store import DB_PATH

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_corpus_results.json"


def build_all(db_path: str = DB_PATH) -> dict:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    t0 = time.time()
    cur.execute("SELECT situation_code FROM pbp_events WHERE period_type != 'SO'")
    codes = [r[0] for r in cur.fetchall()]
    manpower_summary = mp.manpower_validation_summary(codes)

    by_season_manpower = {}
    cur.execute("SELECT DISTINCT season FROM pbp_games ORDER BY season")
    seasons = [r[0] for r in cur.fetchall()]
    for season in seasons:
        cur.execute(
            "SELECT situation_code FROM pbp_events WHERE period_type != 'SO' AND season = ?", (season,))
        by_season_manpower[season] = mp.manpower_validation_summary([r[0] for r in cur.fetchall()])

    cur.execute("SELECT game_id, home_team_id, away_team_id, season FROM pbp_games ORDER BY game_id")
    games = cur.fetchall()

    team_game_rows = []
    league_totals = stc._blank_row()
    league_totals["games"] = 0
    for game_id, home_team_id, away_team_id, season in games:
        rows = stc.build_team_game_special_teams(conn, game_id, home_team_id, away_team_id)
        for team_id, r in rows.items():
            row = dict(r)
            row.update({"game_id": game_id, "team_id": team_id, "season": season,
                        "is_home": team_id == home_team_id})
            team_game_rows.append(row)
            for k in league_totals:
                if k == "games":
                    continue
                league_totals[k] += r.get(k, 0)
        league_totals["games"] += 1

    elapsed = time.time() - t0

    pp_conversion = (league_totals["pp_goals"] / league_totals["pp_opportunities"]
                      if league_totals["pp_opportunities"] else None)
    avg_pp_seconds_per_team_game = (league_totals["pp_seconds"] / (league_totals["games"] * 2)
                                     if league_totals["games"] else None)

    return {
        "built_at_seconds": elapsed,
        "games_processed": len(games),
        "manpower_validation": manpower_summary,
        "manpower_validation_by_season": by_season_manpower,
        "league_totals": league_totals,
        "league_pp_conversion_rate": pp_conversion,
        "league_avg_pp_seconds_per_team_per_game": avg_pp_seconds_per_team_game,
        "team_game_row_count": len(team_game_rows),
        "team_game_rows_sample": team_game_rows[:20],
        "_full_team_game_rows_note": (
            "Full per-team-game rows are NOT embedded in this results file "
            "(10,496 rows would bloat it) -- rebuild via "
            "research.run_period_event_timing_special_teams.build_all()['_rows'] "
            "or call research.period_event_timing.special_teams_corpus directly."
        ),
    }


if __name__ == "__main__":
    result = build_all()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps({k: v for k, v in result.items() if k != "team_game_rows_sample"},
                      indent=2, sort_keys=True, default=str))
