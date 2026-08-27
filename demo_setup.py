"""Builds nhl.db from scratch and loads synthetic seasons. Deterministic:
same seed => byte-identical database (see tests/test_demo_data.py)."""
import datetime as dt

import db
from ingest import demo_data

if __name__ == "__main__":
    conn = db.init_db(wipe=True)
    demo_data.generate(
        conn,
        seasons=[
            ("2022-2023-DEMO", dt.date(2022, 10, 10)),
            ("2023-2024-DEMO", dt.date(2023, 10, 9)),
            ("2024-2025-DEMO", dt.date(2024, 10, 8)),
            ("2025-2026-DEMO", dt.date(2025, 10, 7)),
        ],
        seed=42,
    )
    n_games_final = conn.execute("SELECT COUNT(*) c FROM games WHERE game_state='FINAL'").fetchone()["c"]
    n_games_sched = conn.execute("SELECT COUNT(*) c FROM games WHERE game_state='SCHEDULED'").fetchone()["c"]
    n_players = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    n_roster_events = conn.execute("SELECT COUNT(*) c FROM roster_status_events").fetchone()["c"]
    n_odds = conn.execute("SELECT COUNT(*) c FROM odds_snapshots").fetchone()["c"]
    print(f"Loaded {n_games_final} FINAL games + {n_games_sched} SCHEDULED (upcoming) games")
    print(f"Schedule shape: {demo_data.SEASON_GAMES_NOTE}")
    print(f"{n_players} players, {n_roster_events} roster status events, "
          f"{n_odds} DraftKings odds snapshots")
