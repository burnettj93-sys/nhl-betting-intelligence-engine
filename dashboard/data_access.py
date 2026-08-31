"""
Data access layer for the NHL Model Research + Intelligence dashboard.

This module is READ-ONLY with respect to every production and research
data source it touches:
  - research/real_nhl_results/normalized_regular_season_games.jsonl (real
    NHL schedule/results -- the official outcome-truth corpus)
  - research/moneypuck_ingestion/research_moneypuck.db (MoneyPuck research
    data, opened via research.moneypuck_ingestion's own connection
    helper -- never a hand-rolled sqlite3.connect())
  - research/*_comparison_results.json (the four completed experiment
    result files)

It never opens, reads, or writes nhl.db or any production PIT table --
see tests/test_dashboard.py::TestNoProductionDatabaseAccess, which
AST-scans this module for exactly that.

MODEL REUSE, NOT REIMPLEMENTATION: "current model" predictions shown by
this dashboard are produced by directly reusing
research.elo_comparison.run_walkforward(), which itself calls
models/elo_model.py's real, unmodified EloModel.win_probability() /
update() at every step (proven byte-identical to production in
tests/test_elo_comparison_research.py::TestProductionEloEquivalence).
This dashboard's own code never recomputes a win probability itself.

WHY ELO-ONLY, NOT THE FULL COMBINED MODEL: every one of the four
completed research experiments (see MONEYPUCK_*_REPORT.md /
ELO_REAL_DATA_COMPARISON_REPORT.md) established the same fact: the real
NHL corpus has no real player, goalie, or roster data, so
`compute_probability_from_features()` collapses to exactly
`EloModel.win_probability()` when player/goalie/rest terms are zero --
which is the only honest value for those terms absent real data for
these real games. This dashboard shows that Elo-only production formula
plainly, and labels player/goalie/rest as NOT AVAILABLE in this data
mode rather than fabricating them -- see components.py's
MODEL_INPUT_STATUS.

DATA MODE: this dashboard operates in HISTORICAL / RESEARCH MODE only
(Part: CURRENT-DATE DATA LIMITATION). There is no live current-season
game feed wired up in this project yet, so "today's games" would either
be fabricated or require new live-ingestion work explicitly out of
scope for this slice -- v1 lets the user browse any real historical date
in the corpus instead, clearly labeled as such throughout the UI.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NHL_CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"
MONEYPUCK_DB_PATH = REPO_ROOT / "research" / "moneypuck_ingestion" / "research_moneypuck.db"
EXPERIMENT_RESULT_FILES = {
    "Result-Quality / MOV Elo": REPO_ROOT / "research" / "elo_comparison_results.json",
    "Simple Team xG": REPO_ROOT / "research" / "xg_comparison_results.json",
    "Special Teams": REPO_ROOT / "research" / "special_teams_comparison_results.json",
    "Offense / Defense Shot Quality": REPO_ROOT / "research" / "shot_quality_comparison_results.json",
}

DATA_MODE = "HISTORICAL RESEARCH"
MODEL_STATUS = "RESEARCH / VALIDATION"


class DataAvailabilityError(RuntimeError):
    """Raised with a clear, actionable message when required local
    research data is missing -- callers show this message instead of a
    raw traceback (Part: ERROR HANDLING)."""


def check_data_availability() -> dict:
    """Never raises -- returns a status dict the UI renders directly, so
    a missing-data page is always legible rather than a stack trace."""
    return {
        "nhl_corpus": {
            "path": str(NHL_CORPUS_PATH),
            "found": NHL_CORPUS_PATH.exists(),
        },
        "moneypuck_db": {
            "path": str(MONEYPUCK_DB_PATH),
            "found": MONEYPUCK_DB_PATH.exists(),
        },
        "experiment_results": {
            name: {"path": str(path), "found": path.exists()}
            for name, path in EXPERIMENT_RESULT_FILES.items()
        },
    }


def require_nhl_corpus() -> None:
    if not NHL_CORPUS_PATH.exists():
        raise DataAvailabilityError(
            f"REAL NHL CORPUS: NOT FOUND\n\n"
            f"Expected at:\n{NHL_CORPUS_PATH}\n\n"
            f"This file is built by research/real_nhl_results/build_research_corpus.py "
            f"from the raw weekly NHL API captures under research/real_nhl_results/raw/. "
            f"See REAL_NHL_RESULTS_CORPUS_REPORT.md for how it was produced."
        )


def require_moneypuck_db() -> None:
    if not MONEYPUCK_DB_PATH.exists():
        raise DataAvailabilityError(
            f"MONEYPUCK RESEARCH DB: NOT FOUND\n\n"
            f"Expected at:\n{MONEYPUCK_DB_PATH}\n\n"
            f"Build it with:\n"
            f"  python3 research/moneypuck_ingestion/ingest_moneypuck_team.py "
            f"--file <path-to-all_teams.csv> --provenance archival_research\n\n"
            f"See MONEYPUCK_TEAM_INGESTION_REPORT.md for the full setup."
        )


def load_nhl_corpus() -> list[dict]:
    require_nhl_corpus()
    from research import elo_comparison as ec
    return ec.load_corpus(str(NHL_CORPUS_PATH))


def compute_baseline_predictions() -> list[dict]:
    """Every real game's production (Elo-only) prediction -- reuses
    research.elo_comparison.run_walkforward() unchanged. See module
    docstring for why this IS the production formula for this data."""
    from research import elo_comparison as ec
    games = load_nhl_corpus()
    records, _state = ec.run_walkforward(games, weight_fn=None)
    return records


def get_moneypuck_connection() -> sqlite3.Connection:
    """Opens research_moneypuck.db via the SAME connection helper the
    ingestion pipeline itself uses (applies the schema, sets row_factory)
    -- never a hand-rolled sqlite3.connect() with different pragmas."""
    require_moneypuck_db()
    from research.moneypuck_ingestion.ingest_moneypuck_team import get_connection
    return get_connection(str(MONEYPUCK_DB_PATH))


def load_json_safely(path) -> dict | None:
    """BUG-202 fix (found during the preseason product audit's malformed-
    cache-file check): every dashboard results/cache loader used a bare
    `json.load(f)`, so a truncated write, a manual edit gone wrong, or a
    future incompatible format would crash the ENTIRE page with an
    unhandled JSONDecodeError instead of the graceful "not found" state
    every caller already handles. Returns None (the SAME sentinel already
    used for "file does not exist") for a missing file OR malformed JSON
    -- callers don't need to distinguish the two, since both mean "no
    usable result available right now." Shared by every dashboard module
    that loads a results/cache JSON file (data_access, goalie_view,
    goalie_quality_view, player_sog_view, live_sog_pricing_view,
    data_status_view) — see tests/test_dashboard.py's regression test."""
    path = Path(path) if not isinstance(path, Path) else path
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def load_experiment_results() -> dict[str, dict | None]:
    """{experiment display name: parsed JSON dict, or None if the file
    is missing or malformed}. Never raises -- Research Lab page renders
    per-experiment missing-data notices instead of failing the whole page."""
    return {name: load_json_safely(path) for name, path in EXPERIMENT_RESULT_FILES.items()}


def available_dates(records: list[dict]) -> list[str]:
    return sorted({r["game_date"] for r in records})


def games_on_date(records: list[dict], game_date: str) -> list[dict]:
    return sorted(
        [r for r in records if r["game_date"] == game_date],
        key=lambda r: (r["home_team"], r["away_team"]),
    )


def game_by_id(records: list[dict], game_id: int) -> dict | None:
    for r in records:
        if r["game_id"] == game_id:
            return r
    return None


def all_teams(records: list[dict]) -> list[str]:
    return sorted({r["home_team"] for r in records} | {r["away_team"] for r in records})


def games_for_team(records: list[dict], team: str) -> list[dict]:
    return sorted(
        [r for r in records if r["home_team"] == team or r["away_team"] == team],
        key=lambda r: r["game_date"],
    )


def available_seasons(records: list[dict]) -> list[int]:
    return sorted({r["season"] for r in records})


def format_season(season: int) -> str:
    """20252026 -> '2025-26' -- display-only formatting of the corpus's
    YYYYZZZZ season convention."""
    s = str(season)
    return f"{s[0:4]}-{s[6:8]}"
