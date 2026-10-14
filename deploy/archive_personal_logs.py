"""Archive the retired personal-log database, read-only and separate from model accounting.

Bets submitted by individual people were removed from the product on 2026-10-14. The database that held them
(operational/runtime/personal_logs.db) is NOT deleted and NOT merged into the model book: this script copies it to
operational/archive/personal_logs_archive.db, marks the copy read-only, and writes a JSON export beside it so the
records stay inspectable. Nothing in the engine, the dashboard or the cloud snapshot reads either file.

Run:  python3 deploy/archive_personal_logs.py        (safe to repeat; an existing archive is never overwritten)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "operational" / "runtime" / "personal_logs.db"
ARCHIVE_DIR = REPO / "operational" / "archive"
ARCHIVE_DB = ARCHIVE_DIR / "personal_logs_archive.db"
ARCHIVE_JSON = ARCHIVE_DIR / "personal_logs_archive.json"
TABLES = ("logs", "orders", "bets", "audit", "migrations")


def archive(source: Path = SOURCE, archive_dir: Path = ARCHIVE_DIR) -> dict:
    db_out, json_out = archive_dir / ARCHIVE_DB.name, archive_dir / ARCHIVE_JSON.name
    if not source.exists():
        return {"archived": False, "reason": f"no source database at {source}"}
    if db_out.exists():
        return {"archived": False, "reason": f"archive already exists at {db_out}; left untouched"}
    archive_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)       # read-only: the original is never modified
    dst = sqlite3.connect(db_out)
    try:
        src.backup(dst)
    finally:
        dst.close()
    src.row_factory = sqlite3.Row
    export = {"archived_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "source": str(source),
              "note": "Inactive archive. Not part of the $500 model book; never added to it, never read by the product."}
    counts = {}
    for table in TABLES:
        try:
            rows = [dict(r) for r in src.execute(f"SELECT * FROM {table}")]
        except sqlite3.Error:
            rows = []
        export[table] = rows
        counts[table] = len(rows)
    src.close()
    json_out.write_text(json.dumps(export, indent=1, default=str))
    for p in (db_out, json_out):
        os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return {"archived": True, "database": str(db_out), "export": str(json_out), "rows": counts}


if __name__ == "__main__":
    print(json.dumps(archive(), indent=1))
    sys.exit(0)
