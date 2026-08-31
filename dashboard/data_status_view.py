"""
View logic for the Data Status dashboard page (Part 16). Reads ONLY the
cached readiness snapshot (operational/data_readiness_cache.json),
written by `python3 sync_daily.py` — this module makes NO network calls
and imports no ingestion/API-client code (Part 17), verified by
tests/test_operational_daily_sync.py::TestDashboardNoNetworkOnRerun.
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely

REPO_ROOT = Path(__file__).resolve().parent.parent
READINESS_CACHE_PATH = REPO_ROOT / "operational" / "data_readiness_cache.json"


def load_readiness_cache() -> dict | None:
    return load_json_safely(READINESS_CACHE_PATH)
