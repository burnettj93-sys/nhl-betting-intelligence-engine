#!/usr/bin/env python3
"""
CLI entry point for the MoneyPuck team game-by-game research ingestion
pipeline. Archives the raw file unchanged, validates its schema, and
normalizes+writes it into the separate research storage layer
(research/moneypuck_ingestion/research_moneypuck.db).

    python3 research/moneypuck_ingestion/ingest_moneypuck_team.py \\
        --file /path/to/all_teams.csv \\
        --source-url https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv \\
        --provenance archival_research \\
        --downloaded-at-utc 2026-08-27T13:23:32+00:00

--provenance accepts archival_research or live_observed -- see this
slice's PROVENANCE MODEL. Automated recurring downloads are explicitly
NOT built this slice (per instruction); this entry point only accepts a
LOCAL file already on disk, so it can later be pointed at either a
one-time archival-research file (this slice) or a daily-sync-collected
file (a future slice) without changing the parser/normalizer at all.

Does not touch nhl.db or any production table.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from research.moneypuck_ingestion import ingest as ingest_mod
from research.moneypuck_ingestion.raw_archive import archive_raw_file, mark_validation_status

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "research_moneypuck.db")
DEFAULT_TARGET_SEASONS = {20222023, 20232024, 20242025, 20252026}

_PROVENANCE_MAP = {"archival_research": "ARCHIVAL_RESEARCH", "live_observed": "LIVE_OBSERVED"}


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        conn.executescript(f.read())
    return conn


def run(file_path: str, source_url: str, provenance_mode: str, downloaded_at_utc: str | None,
        db_path: str = DEFAULT_DB_PATH, target_seasons=DEFAULT_TARGET_SEASONS,
        dataset: str = "team_gamebygame") -> ingest_mod.IngestReport:
    if downloaded_at_utc is None:
        # only used when the caller has no better real timestamp -- prefer
        # passing the file's own known download time explicitly.
        downloaded_at_utc = dt.datetime.fromtimestamp(
            os.path.getmtime(file_path), tz=dt.timezone.utc
        ).isoformat()

    archived_path, provenance = archive_raw_file(
        file_path, source_url=source_url, dataset=dataset,
        downloaded_at_utc=downloaded_at_utc, season_coverage="all",
    )

    conn = get_connection(db_path)
    try:
        report = ingest_mod.ingest_file(
            conn, archived_path, provenance,
            provenance_type=_PROVENANCE_MAP[provenance_mode],
            target_seasons=target_seasons, regular_season_only=True,
        )
        mark_validation_status(archived_path, "VALID")
        return report
    except ingest_mod.MoneyPuckSchemaError as exc:
        mark_validation_status(archived_path, "REJECTED", reason=str(exc))
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Ingest a local MoneyPuck team game-by-game CSV.")
    parser.add_argument("--file", required=True, help="path to the local MoneyPuck CSV")
    parser.add_argument("--source-url", default="https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv")
    parser.add_argument("--provenance", required=True, choices=["archival_research", "live_observed"])
    parser.add_argument("--downloaded-at-utc", default=None,
                         help="ISO-8601 UTC timestamp of when the file was actually obtained; "
                              "defaults to the file's own mtime if omitted")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    report = run(args.file, args.source_url, args.provenance, args.downloaded_at_utc, db_path=args.db_path)
    print(f"rows_read={report.rows_read}")
    print(f"rows_regular_season_target={report.rows_regular_season_target}")
    print(f"rows_new={report.rows_new}")
    print(f"rows_revised={report.rows_revised}")
    print(f"rows_unchanged={report.rows_unchanged}")
    print(f"rows_rejected={report.rows_rejected}")
    if report.rejected_reasons:
        print("first rejected reasons:")
        for reason in report.rejected_reasons[:10]:
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
