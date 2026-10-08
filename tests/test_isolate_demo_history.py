"""operational/isolate_demo_history.py: simulated history leaves the production database, real rows stay."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from operational import isolate_demo_history as iso


def _seed(path: Path) -> None:
    conn = db.init_db(db_path=path, wipe=True)
    for t in ("TOR", "MTL"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    for gid, source, season in ((1, "demo_generator", "2024-2025-DEMO"), (2, "demo_generator", "2024-2025-DEMO"),
                                (3, "nhl_api", "20262027")):
        conn.execute("INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state, home_score, away_score, "
                     "final_period_type, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (gid, season, "2026-10-0%d" % gid, "TOR", "MTL", "FINAL", 3, 2, "REG", source))
        for pid in ("8470001", "TOR_demo1"):
            conn.execute("INSERT OR IGNORE INTO players (player_id, full_name, position) VALUES (?,?,?)", (pid, pid, "C"))
        conn.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, "
                     "revision_number, effective_at_utc, observed_at_utc, source) VALUES (?,?,?,?,?,?,?,?,1,?,?,?)",
                     (gid, "8470001" if source == "nhl_api" else "TOR_demo1", "TOR", 18.0, 0, 1, 3, 1,
                      "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", source))
    conn.commit()
    conn.close()


class TestIsolateDemoHistory(unittest.TestCase):
    def test_demo_rows_leave_production_and_a_full_backup_remains(self):
        tmp = Path(tempfile.mkdtemp())
        prod = tmp / "nhl.db"
        _seed(prod)
        c = sqlite3.connect(prod)
        before = iso.plan(c)
        c.close()
        self.assertEqual((before["demo_games"], before["real_games"]), (2, 1))
        result = iso.apply(db_path=prod, backup_dir=tmp / "backups")
        self.assertEqual(result["status"], "ISOLATED", result)
        after = sqlite3.connect(prod)
        self.assertEqual([r[0] for r in after.execute("SELECT game_id FROM games")], [3])
        self.assertEqual(after.execute("SELECT COUNT(*) FROM player_game_stats").fetchone()[0], 1)
        self.assertEqual([r[0] for r in after.execute("SELECT player_id FROM players")], ["8470001"])
        after.close()
        backup = sqlite3.connect(result["backup"])
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM games").fetchone()[0], 3)        # everything is preserved there
        backup.close()

    def test_running_it_twice_changes_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        prod = tmp / "nhl.db"
        _seed(prod)
        iso.apply(db_path=prod, backup_dir=tmp / "b")
        second = iso.apply(db_path=prod, backup_dir=tmp / "b")
        self.assertEqual(second["status"], "ISOLATED")
        self.assertEqual(second["before"]["demo_games"], 0)


if __name__ == "__main__":
    unittest.main()
