"""
TEMPORARY verification harness -- NOT production code, NOT committed to the
production module tree. Lives entirely under tmp_live_contract/.

Purpose (per user's BROWSER-ASSISTED LIVE NHL API CONTRACT REPLAY plan,
Step 5): replay genuine, unmodified NHL API JSON responses (captured via
browser navigation in tmp_live_contract/*.json) through the EXISTING,
UNMODIFIED production ingestion functions in ingest/nhl_api.py, against a
fresh temporary SQLite database, and report what happens. Do NOT edit
production code in response to any failure surfaced here -- that is the
whole point of this exercise.

Fake session/response adapter: `.get(url, timeout=...)` returns an object
whose `.raise_for_status()` is a no-op and `.json()` returns the EXACT
parsed JSON captured from the real NHL API for that URL. No payload
transformation of any kind.
"""
import datetime as dt
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from ingest import nhl_api

HERE = Path(__file__).resolve().parent
BASE_URL = "https://api-web.nhle.com/v1"

URL_TO_FILE = {
    f"{BASE_URL}/schedule/2026-06-03": HERE / "schedule.json",
    f"{BASE_URL}/gamecenter/2025030412/boxscore": HERE / "boxscore_2025030412.json",
    f"{BASE_URL}/gamecenter/2025030413/boxscore": HERE / "boxscore_2025030413.json",
    f"{BASE_URL}/gamecenter/2025030414/boxscore": HERE / "boxscore_2025030414.json",
    f"{BASE_URL}/roster/CAR/current": HERE / "roster_CAR_current.json",
    f"{BASE_URL}/roster/VGK/current": HERE / "roster_VGK_current.json",
}


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeSession:
    """.get() returns the EXACT captured real-NHL JSON for that URL,
    unmodified. Any URL not in URL_TO_FILE is a hard error -- this harness
    must never silently fall back to a synthetic payload."""

    def get(self, url, timeout=15):
        if url not in URL_TO_FILE:
            raise RuntimeError(f"FakeSession: no captured payload for URL {url!r} "
                                f"-- refusing to fabricate one")
        with open(URL_TO_FILE[url]) as f:
            data = json.load(f)
        return FakeResponse(data)


def fresh_temp_db():
    fd_path = Path(tempfile.mkstemp(suffix=".db")[1])
    fd_path.unlink()
    conn = db.init_db(db_path=fd_path, wipe=False)
    return conn, fd_path


def counts(conn):
    tables = ("games", "game_schedule_events", "game_result_events",
              "player_game_stats", "goalie_game_stats", "players",
              "team_membership_events")
    return {name: conn.execute(f"SELECT COUNT(*) c FROM {name}").fetchone()["c"]
            for name in tables}


def main():
    session = FakeSession()
    conn, db_path = fresh_temp_db()
    print(f"Fresh temp DB: {db_path}")

    report = {"phase": None, "error": None}

    try:
        report["phase"] = "ingest_range (schedule/result/boxscore) -- PASS 1"
        result1 = nhl_api.ingest_range(
            conn, dt.date(2026, 6, 3), dt.date(2026, 6, 9), session=session)
        print("ingest_range pass 1 result:", result1)
        print("counts after pass 1:", counts(conn))

        report["phase"] = "ingest_current_roster_identities -- PASS 1"
        roster_result1 = nhl_api.ingest_current_roster_identities(
            conn, session, ["CAR", "VGK"])
        print("roster pass 1 result:", roster_result1)
        print("counts after roster pass 1:", counts(conn))

        before = counts(conn)

        report["phase"] = "ingest_range -- PASS 2 (idempotency)"
        result2 = nhl_api.ingest_range(
            conn, dt.date(2026, 6, 3), dt.date(2026, 6, 9), session=session)
        print("ingest_range pass 2 result:", result2)

        report["phase"] = "ingest_current_roster_identities -- PASS 2 (idempotency)"
        roster_result2 = nhl_api.ingest_current_roster_identities(
            conn, session, ["CAR", "VGK"])
        print("roster pass 2 result:", roster_result2)

        after = counts(conn)
        print("counts before pass 2:", before)
        print("counts after pass 2:", after)
        print("IDEMPOTENT (no count changes):", before == after)

        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        print("foreign_key_check:", fk)

        report["phase"] = "DONE"

    except Exception as e:
        report["error"] = {
            "phase": report["phase"],
            "exception_type": type(e).__name__,
            "exception_str": str(e),
            "traceback": traceback.format_exc(),
        }
        print("=" * 70)
        print(f"STOPPED -- exception during phase: {report['phase']}")
        print(f"{type(e).__name__}: {e}")
        print("-" * 70)
        print(traceback.format_exc())
        print("=" * 70)

    conn.close()
    return report


if __name__ == "__main__":
    main()
