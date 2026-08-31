"""
MoneyPuck team game-by-game ingestion: parse -> validate -> normalize ->
idempotent, revision-aware write into the SEPARATE research storage layer
(schema.sql::research_moneypuck_team_game_stats). This is the "minimum
MoneyPuck ingestion foundation" this slice builds -- no derived features
(rolling xG, xG%, team-strength scores) and no production model changes
happen anywhere in this module.

PROVENANCE: every normalized row is tagged with provenance_type
('ARCHIVAL_RESEARCH' for a file downloaded today describing past games,
or 'LIVE_OBSERVED' for a file collected going forward whose
downloaded_at_utc genuinely is close to the events it describes -- see
this slice's PROVENANCE MODEL section and
MONEYPUCK_DATA_CONTRACT_REVIEW.md Sections R/S) and the real
downloaded_at_utc of the raw file it came from -- NEVER a fabricated
historical timestamp backdated to the game's own date.

XG VERSION SEMANTICS: every row also carries
xg_model_version_semantics='UNKNOWN' (schema.sql's default) -- the prior
review could not determine whether MoneyPuck's xG model was retrained
and old shots rescored, so any xG value ingested here must be read as
ARCHIVAL HISTORICAL MONEYPUCK XG, MODEL VERSION SEMANTICS: UNKNOWN, not
necessarily the exact value a real-time observer would have seen at the
time.

NHL GAME JOIN: game_id here is used exactly as MoneyPuck supplies it,
which the prior review verified is the NHL's own native game_id (same ID
space, same gameType/season encoding) -- see derive_game_type() below,
which derives regular-season-only filtering from the id itself (digits
5-6 == '02'), the same convention research/real_nhl_results/
build_research_corpus.py uses, rather than trusting MoneyPuck's
`playoffGame` flag alone (both are read and cross-checked; see
validate_against_nhl_corpus.py for the full cross-check against the real
NHL corpus).
"""
from __future__ import annotations

import csv
import datetime as dt
import sqlite3
from dataclasses import dataclass, field

from research.moneypuck_ingestion.raw_archive import RawFileProvenance

REGULAR_SEASON_GAME_TYPE = 2  # matches research/real_nhl_results's TARGET_GAME_TYPE

# Source columns this ingester actually reads. If any of these disappear
# from a future MoneyPuck file, ingestion must fail loudly rather than
# silently ingest nulls/zeros for a field that used to exist.
REQUIRED_SOURCE_COLUMNS = [
    "team", "season", "gameId", "opposingTeam", "home_or_away", "gameDate",
    "situation", "goalsFor", "goalsAgainst", "shotsOnGoalFor", "shotsOnGoalAgainst",
    "xGoalsFor", "xGoalsAgainst", "playoffGame",
]

# Source columns that are read if present but not required to exist --
# missing ones simply normalize to NULL rather than failing the file.
OPTIONAL_SOURCE_COLUMNS = [
    "shotAttemptsFor", "shotAttemptsAgainst",
    "unblockedShotAttemptsFor", "unblockedShotAttemptsAgainst",
    "highDangerShotsFor", "highDangerShotsAgainst",
    "mediumDangerShotsFor", "mediumDangerShotsAgainst",
    "lowDangerShotsFor", "lowDangerShotsAgainst",
    "highDangerxGoalsFor", "highDangerxGoalsAgainst",
    "mediumDangerxGoalsFor", "mediumDangerxGoalsAgainst",
    "lowDangerxGoalsFor", "lowDangerxGoalsAgainst",
    "reboundsFor", "reboundsAgainst",
    "scoreAdjustedShotsAttemptsFor", "scoreAdjustedShotsAttemptsAgainst",
    "scoreVenueAdjustedxGoalsFor", "scoreVenueAdjustedxGoalsAgainst",
    "iceTime",
]

# source-field -> normalized-column mapping (Section F of the report)
_FLOAT_TO_INT_FIELDS = {
    "goalsFor": "goals_for", "goalsAgainst": "goals_against",
    "shotsOnGoalFor": "shots_for", "shotsOnGoalAgainst": "shots_against",
    "shotAttemptsFor": "shot_attempts_for", "shotAttemptsAgainst": "shot_attempts_against",
    "unblockedShotAttemptsFor": "unblocked_shot_attempts_for",
    "unblockedShotAttemptsAgainst": "unblocked_shot_attempts_against",
    "highDangerShotsFor": "high_danger_shots_for", "highDangerShotsAgainst": "high_danger_shots_against",
    "mediumDangerShotsFor": "medium_danger_shots_for", "mediumDangerShotsAgainst": "medium_danger_shots_against",
    "lowDangerShotsFor": "low_danger_shots_for", "lowDangerShotsAgainst": "low_danger_shots_against",
    "reboundsFor": "rebounds_for", "reboundsAgainst": "rebounds_against",
}
_FLOAT_FIELDS = {
    "xGoalsFor": "xg_for", "xGoalsAgainst": "xg_against",
    "highDangerxGoalsFor": "high_danger_xg_for", "highDangerxGoalsAgainst": "high_danger_xg_against",
    "mediumDangerxGoalsFor": "medium_danger_xg_for", "mediumDangerxGoalsAgainst": "medium_danger_xg_against",
    "lowDangerxGoalsFor": "low_danger_xg_for", "lowDangerxGoalsAgainst": "low_danger_xg_against",
    "scoreAdjustedShotsAttemptsFor": "score_adjusted_shot_attempts_for",
    "scoreAdjustedShotsAttemptsAgainst": "score_adjusted_shot_attempts_against",
    "scoreVenueAdjustedxGoalsFor": "score_venue_adjusted_xg_for",
    "scoreVenueAdjustedxGoalsAgainst": "score_venue_adjusted_xg_against",
    "iceTime": "ice_time_seconds",
}

KNOWN_TARGET_SEASON_TEAMS = {
    "ANA", "ARI", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI",
    "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
}


class MoneyPuckSchemaError(RuntimeError):
    """Raised when the source file's structure doesn't match what this
    ingester requires -- a missing required column, a corrupt/non-CSV
    file (including an HTML page returned instead of data, which fails
    here because it has none of REQUIRED_SOURCE_COLUMNS as a header),
    or an unparseable row. Never partially promoted -- see ingest_file()."""


class MoneyPuckRowError(RuntimeError):
    """Raised for a single malformed row (missing game_id/team, invalid
    numeric field, etc). Collected per-file so one bad row is reported
    clearly rather than raising on the first one and hiding the rest."""


@dataclass
class IngestReport:
    source_file: str
    source_sha256: str
    rows_read: int = 0
    rows_regular_season_target: int = 0
    rows_new: int = 0
    rows_revised: int = 0
    rows_unchanged: int = 0
    rows_rejected: int = 0
    rejected_reasons: list[str] = field(default_factory=list)


def derive_nhl_season(mp_season: int) -> int:
    """MoneyPuck's `season` column is the season's START year as a bare
    4-digit int (e.g. 2022 for the 2022-23 season) -- verified against
    the real corpus's YYYYZZZZ convention (e.g. 20222023) this turn by
    cross-checking every sampled game_id/date against
    research/real_nhl_results/. This is a purely mechanical reformatting,
    not an assumption -- see validate_against_nhl_corpus.py for the
    empirical cross-check that confirms it lines up."""
    return mp_season * 10000 + (mp_season + 1)


def derive_game_type(game_id: int) -> int:
    """NHL game_id encoding: season(4) + gameType(2) + sequence(4) --
    the same convention research/real_nhl_results/build_research_corpus.py
    uses. Digits 5-6 (0-indexed [4:6] of the 10-digit id) are the game
    type; 2 == regular season."""
    return int(str(game_id)[4:6])


def validate_schema(header: list[str]) -> None:
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in header]
    if missing:
        raise MoneyPuckSchemaError(
            f"required MoneyPuck columns missing from source file: {missing}. "
            f"Refusing to ingest -- this may mean the file is corrupt, is not "
            f"the expected CSV (e.g. an HTML page was returned instead of "
            f"data), or MoneyPuck changed its schema."
        )


def _parse_float(raw_value: str, field_name: str, row_context: str) -> float:
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        raise MoneyPuckRowError(f"{row_context}: field {field_name!r} is not numeric: {raw_value!r}")


def _reformat_game_date(mp_game_date: str, row_context: str) -> str:
    if not (isinstance(mp_game_date, str) and len(mp_game_date) == 8 and mp_game_date.isdigit()):
        raise MoneyPuckRowError(f"{row_context}: gameDate is not YYYYMMDD: {mp_game_date!r}")
    return f"{mp_game_date[0:4]}-{mp_game_date[4:6]}-{mp_game_date[6:8]}"


def normalize_row(raw: dict, *, provenance_type: str, source_file: str,
                   source_sha256: str, downloaded_at_utc: str, ingested_at_utc: str
                   ) -> dict:
    """Pure function: one raw MoneyPuck CSV row (as a dict) -> one
    normalized dict matching research_moneypuck_team_game_stats' columns.
    Raises MoneyPuckRowError on a structurally invalid row. Does not
    filter by season/game-type -- that's ingest_file()'s job, so this
    function stays a simple, independently-testable 1-row transform."""
    try:
        game_id = int(raw["gameId"])
    except (KeyError, TypeError, ValueError):
        raise MoneyPuckRowError(f"row missing/invalid gameId: {raw.get('gameId')!r}")
    team = raw.get("team") or ""
    if not team:
        raise MoneyPuckRowError(f"row for gameId {game_id} missing team")
    row_context = f"gameId={game_id} team={team}"

    try:
        mp_season = int(raw["season"])
    except (KeyError, TypeError, ValueError):
        raise MoneyPuckRowError(f"{row_context}: missing/invalid season: {raw.get('season')!r}")

    normalized = {
        "game_id": game_id,
        "season": derive_nhl_season(mp_season),
        "game_date": _reformat_game_date(raw.get("gameDate", ""), row_context),
        "team": team,
        "opponent": raw.get("opposingTeam") or "",
        "situation": raw.get("situation") or "",
        "home_or_away": raw.get("home_or_away") or "",
        "provenance_type": provenance_type,
        "source": "MoneyPuck",
        "source_file": source_file,
        "source_sha256": source_sha256,
        "downloaded_at_utc": downloaded_at_utc,
        "ingested_at_utc": ingested_at_utc,
        "xg_model_version_semantics": "UNKNOWN",
    }
    if not normalized["opponent"]:
        raise MoneyPuckRowError(f"{row_context}: missing opposingTeam")
    if not normalized["situation"]:
        raise MoneyPuckRowError(f"{row_context}: missing situation")

    for src_field, dest_field in _FLOAT_TO_INT_FIELDS.items():
        raw_value = raw.get(src_field)
        if raw_value in (None, ""):
            if src_field in REQUIRED_SOURCE_COLUMNS:
                raise MoneyPuckRowError(f"{row_context}: required field {src_field!r} is empty")
            normalized[dest_field] = None
            continue
        normalized[dest_field] = int(round(_parse_float(raw_value, src_field, row_context)))

    for src_field, dest_field in _FLOAT_FIELDS.items():
        raw_value = raw.get(src_field)
        if raw_value in (None, ""):
            if src_field in REQUIRED_SOURCE_COLUMNS:
                raise MoneyPuckRowError(f"{row_context}: required field {src_field!r} is empty")
            normalized[dest_field] = None
            continue
        normalized[dest_field] = _parse_float(raw_value, src_field, row_context)

    return normalized


_INSERT_COLUMNS = [
    "game_id", "season", "game_date", "team", "opponent", "situation", "home_or_away",
    "ice_time_seconds",
    "goals_for", "goals_against", "shots_for", "shots_against",
    "xg_for", "xg_against",
    "shot_attempts_for", "shot_attempts_against",
    "unblocked_shot_attempts_for", "unblocked_shot_attempts_against",
    "high_danger_shots_for", "high_danger_shots_against",
    "medium_danger_shots_for", "medium_danger_shots_against",
    "low_danger_shots_for", "low_danger_shots_against",
    "high_danger_xg_for", "high_danger_xg_against",
    "medium_danger_xg_for", "medium_danger_xg_against",
    "low_danger_xg_for", "low_danger_xg_against",
    "rebounds_for", "rebounds_against",
    "score_adjusted_shot_attempts_for", "score_adjusted_shot_attempts_against",
    "score_venue_adjusted_xg_for", "score_venue_adjusted_xg_against",
    "provenance_type", "source", "source_file", "source_sha256",
    "downloaded_at_utc", "ingested_at_utc", "xg_model_version_semantics",
]

_COMPARISON_COLUMNS = [c for c in _INSERT_COLUMNS if c not in (
    "provenance_type", "source", "source_file", "source_sha256",
    "downloaded_at_utc", "ingested_at_utc", "xg_model_version_semantics",
)]


def classify_and_ingest_row(conn: sqlite3.Connection, normalized: dict) -> str:
    """Idempotency + revision logic (Sections: IDEMPOTENCY, R). Natural
    key = (game_id, team, situation). Looks up the latest existing row
    (by ingested_at_utc) for that key:
      - none exists                          -> NEW,     insert
      - exists, all comparison fields equal  -> UNCHANGED, no insert
      - exists, some comparison field differs -> REVISED, insert a new row
    Never UPDATEs a row in place -- see schema.sql's module docstring."""
    cur = conn.execute(
        "SELECT * FROM research_moneypuck_team_game_stats "
        "WHERE game_id=? AND team=? AND situation=? "
        "ORDER BY ingested_at_utc DESC LIMIT 1",
        (normalized["game_id"], normalized["team"], normalized["situation"]),
    )
    existing = cur.fetchone()

    if existing is not None:
        existing_keys = existing.keys()
        unchanged = all(
            existing[c] == normalized[c] for c in _COMPARISON_COLUMNS if c in existing_keys
        )
        if unchanged:
            return "UNCHANGED"
        classification = "REVISED"
    else:
        classification = "NEW"

    placeholders = ",".join("?" * len(_INSERT_COLUMNS))
    values = [normalized[c] for c in _INSERT_COLUMNS]
    conn.execute(
        "INSERT OR IGNORE INTO research_moneypuck_team_game_stats "
        f"({','.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
        values,
    )
    return classification


def ingest_file(conn: sqlite3.Connection, archived_path: str, provenance: RawFileProvenance,
                 *, provenance_type: str, target_seasons: set[int] | None = None,
                 regular_season_only: bool = True) -> IngestReport:
    """Parses `archived_path` (a raw MoneyPuck team game-by-game CSV
    already archived by raw_archive.archive_raw_file), validates its
    schema, normalizes every row, filters to `target_seasons` (NHL
    YYYYZZZZ form) and regular-season games only (per derive_game_type,
    cross-checked against the file's own playoffGame flag), and writes
    each surviving row via classify_and_ingest_row(). Fails loudly and
    promotes NOTHING if the file's schema is invalid (Section: SCHEMA
    VALIDATION) -- schema validation happens before any row is written.

    `provenance_type` must be 'ARCHIVAL_RESEARCH' or 'LIVE_OBSERVED',
    matching the CLI's --provenance flag -- kept as an explicit parameter
    here (not inferred from `provenance`, which describes the raw FILE's
    own archival metadata) so the exact same normalizer/ingester can
    later serve a LIVE_OBSERVED daily-sync path without modification."""
    assert provenance_type in ("ARCHIVAL_RESEARCH", "LIVE_OBSERVED"), provenance_type
    report = IngestReport(source_file=archived_path, source_sha256=provenance.sha256)
    ingested_at_utc = dt.datetime.now(dt.timezone.utc).isoformat()

    with open(archived_path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise MoneyPuckSchemaError(f"{archived_path}: could not read a header row -- corrupt or empty file")
        validate_schema(reader.fieldnames)

        for raw in reader:
            report.rows_read += 1
            try:
                game_id = int(raw["gameId"])
                mp_season = int(raw["season"])
            except (KeyError, TypeError, ValueError) as exc:
                report.rows_rejected += 1
                report.rejected_reasons.append(f"row {report.rows_read}: {exc}")
                continue

            nhl_season = derive_nhl_season(mp_season)
            if target_seasons is not None and nhl_season not in target_seasons:
                continue
            if regular_season_only:
                game_type_by_id = derive_game_type(game_id)
                playoff_flag = raw.get("playoffGame") == "1"
                game_type_by_flag = 3 if playoff_flag else 2
                if game_type_by_id != game_type_by_flag:
                    report.rows_rejected += 1
                    report.rejected_reasons.append(
                        f"row {report.rows_read}: gameId-derived type {game_type_by_id} "
                        f"disagrees with playoffGame flag ({raw.get('playoffGame')}) for game {game_id}"
                    )
                    continue
                if game_type_by_id != REGULAR_SEASON_GAME_TYPE:
                    continue

            report.rows_regular_season_target += 1
            try:
                normalized = normalize_row(
                    raw, provenance_type=provenance_type,
                    source_file=archived_path, source_sha256=provenance.sha256,
                    downloaded_at_utc=provenance.downloaded_at_utc,
                    ingested_at_utc=ingested_at_utc,
                )
            except MoneyPuckRowError as exc:
                report.rows_rejected += 1
                report.rejected_reasons.append(str(exc))
                continue

            classification = classify_and_ingest_row(conn, normalized)
            if classification == "NEW":
                report.rows_new += 1
            elif classification == "REVISED":
                report.rows_revised += 1
            else:
                report.rows_unchanged += 1

    conn.commit()
    return report
