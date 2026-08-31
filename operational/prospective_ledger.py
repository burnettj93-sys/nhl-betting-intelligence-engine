"""
Append-only prospective observation store (Preseason Operationalization
sprint, Sections 1-25). A SEPARATE SQLite database from nhl.db (the
production demo DB) -- new operational infrastructure, never mixed with
the production schema, never a JSON-only volatile store.

Immutability is enforced in TWO layers:
1. Database: `predictions_immutability` trigger in
   operational/prospective_schema.sql aborts any UPDATE that touches a
   prediction-time column.
2. API: `settle_prediction()` is the only function that ever issues an
   UPDATE, and it only ever writes to the eight settlement columns.

Idempotency (Section 7): a deterministic `idempotency_key` (game_id,
player_id, market_id, threshold, side, model_version,
prediction_cutoff_utc) means a Streamlit rerun that calls
`insert_prediction()` again with the same real-world inputs gets back
the EXISTING prediction_id rather than creating a duplicate row.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "operational" / "prospective_observations.db"
SCHEMA_PATH = REPO_ROOT / "operational" / "prospective_schema.sql"
SCHEMA_VERSION = 3  # v3 (Live Special-Teams Role Shadow sprint): added SOG PP-role shadow columns

RECORD_TYPES = ("MODEL_OBSERVATION", "SHADOW_POLICY_OBSERVATION", "REAL_BET", "HISTORICAL_RESEARCH")
RESULT_STATES = ("PENDING", "WIN", "LOSS", "PUSH", "VOID", "UNRESOLVED")


class DuplicatePredictionError(Exception):
    pass


class InvalidPredictionError(Exception):
    pass


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Explicit, additive migrations only -- never a schema rebuild.
    v1 -> v2 (Preseason Closing sprint): add prediction_checkpoint.
    v2 -> v3 (Live Special-Teams Role Shadow sprint): add the SOG
    PP-role shadow columns -- deliberately NOT reusing the existing
    shadow_context_policy_probability/shadow_policy_status columns,
    which belong to the separate Goals/Points context overlay (a
    different overlay entirely; conflating the two would make it
    impossible to tell which shadow mechanism produced a given value)."""
    if from_version < 2:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        if "prediction_checkpoint" not in cols:
            conn.execute("ALTER TABLE predictions ADD COLUMN prediction_checkpoint TEXT")
    if from_version < 3:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        new_columns = (
            ("sog_shadow_raw_probability", "REAL"), ("sog_shadow_conservative_probability", "REAL"),
            ("pp_role_state", "TEXT"), ("pp_role_certainty", "REAL"), ("pp_transition_state", "TEXT"),
            ("pp_games_since_transition", "INTEGER"), ("role_overlay_version", "TEXT"),
        )
        for col, col_type in new_columns:
            if col not in cols:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {col_type}")
        # SQLite has no ALTER TRIGGER -- the immutability trigger must be
        # dropped and recreated (from the current schema.sql, the single
        # source of truth) so the new columns are actually protected on
        # an already-existing database, not just a freshly-created one.
        conn.execute("DROP TRIGGER IF EXISTS predictions_immutability")
        with open(SCHEMA_PATH) as f:
            schema_sql = f.read()
        trigger_start = schema_sql.index("CREATE TRIGGER IF NOT EXISTS predictions_immutability")
        trigger_end = schema_sql.index("END;", trigger_start) + len("END;")
        conn.executescript(schema_sql[trigger_start:trigger_end])


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Explicit schema initialization (Section 24) -- never creates
    tables ad hoc inside another function. Idempotent: safe to call on
    every process start (CREATE TABLE/TRIGGER IF NOT EXISTS)."""
    conn = get_conn(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    elif row["version"] < SCHEMA_VERSION:
        _migrate(conn, row["version"])
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        conn.commit()
    return conn


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_idempotency_key(game_id, player_id, market_id, threshold, side, model_version,
                             prediction_cutoff_utc) -> str:
    raw = "|".join(str(x) for x in
                    (game_id, player_id, market_id, threshold, side, model_version, prediction_cutoff_utc))
    return hashlib.sha256(raw.encode()).hexdigest()


def new_prediction_id() -> str:
    return str(uuid.uuid4())


_COLUMNS = [
    "prediction_id", "idempotency_key", "record_type", "created_at_utc", "prediction_cutoff_utc",
    "event_start_utc", "game_id", "game_date", "player_id", "player_name_snapshot", "team", "opponent",
    "market_id", "market_family", "threshold", "side", "prediction_checkpoint",
    "raw_probability", "context_adjusted_probability", "coherent_probability", "conservative_probability",
    "confidence", "context_state", "context_overlay_status", "model_status", "prospective_status",
    "decision_policy_version", "current_policy_status", "shadow_policy_status",
    "raw_policy_input_probability", "shadow_context_policy_probability", "future_policy_candidate",
    "model_version", "model_hash", "context_overlay_version", "context_overlay_hash",
    "registry_version", "registry_hash", "data_snapshot_references",
    "sportsbook", "book_event_id", "market_key", "line", "odds_american", "odds_decimal",
    "market_implied_probability", "market_no_vig_probability", "odds_captured_at_utc",
    "odds_received_at_utc", "data_freshness_status", "stake", "placed_odds", "placed_at_utc",
    "sog_shadow_raw_probability", "sog_shadow_conservative_probability", "pp_role_state",
    "pp_role_certainty", "pp_transition_state", "pp_games_since_transition", "role_overlay_version",
]


def insert_prediction(conn: sqlite3.Connection, fields: dict) -> dict:
    """Section 6/7: inserts a new immutable prediction row, or returns
    the EXISTING row's id if an identical (idempotency-key-equal)
    observation was already recorded -- never a silent UPSERT, never a
    duplicate row from a dashboard rerun.

    Returns {"status": "INSERTED"|"DUPLICATE", "prediction_id": ...}.
    """
    record_type = fields.get("record_type")
    if record_type not in RECORD_TYPES:
        raise InvalidPredictionError(f"unknown record_type {record_type!r}")

    event_start_utc = fields.get("event_start_utc")
    if not event_start_utc:
        raise InvalidPredictionError("event_start_utc is required")
    created_at_utc = fields.get("created_at_utc") or _utcnow_iso()

    if record_type != "HISTORICAL_RESEARCH" and created_at_utc >= event_start_utc:
        raise InvalidPredictionError(
            "prediction created_at_utc must be strictly before event_start_utc "
            "(Section 10 pre-game temporal guard) unless record_type=HISTORICAL_RESEARCH")

    odds_captured_at_utc = fields.get("odds_captured_at_utc")
    if odds_captured_at_utc and odds_captured_at_utc >= event_start_utc:
        raise InvalidPredictionError(
            "odds_captured_at_utc must be strictly before event_start_utc (Section 11 odds temporal guard)")

    if record_type == "REAL_BET":
        for required in ("stake", "placed_odds", "placed_at_utc", "sportsbook"):
            if not fields.get(required):
                raise InvalidPredictionError(f"REAL_BET requires explicit {required}")

    idempotency_key = fields.get("idempotency_key") or compute_idempotency_key(
        fields.get("game_id"), fields.get("player_id"), fields["market_id"], fields.get("threshold"),
        fields.get("side"), fields.get("model_version"), fields.get("prediction_cutoff_utc"))

    existing = conn.execute("SELECT prediction_id FROM predictions WHERE idempotency_key = ?",
                             (idempotency_key,)).fetchone()
    if existing:
        return {"status": "DUPLICATE", "prediction_id": existing["prediction_id"]}

    prediction_id = fields.get("prediction_id") or new_prediction_id()
    data_snapshot_references = fields.get("data_snapshot_references")
    if isinstance(data_snapshot_references, dict):
        data_snapshot_references = json.dumps(data_snapshot_references)

    row = {**fields, "prediction_id": prediction_id, "idempotency_key": idempotency_key,
           "created_at_utc": created_at_utc, "data_snapshot_references": data_snapshot_references,
           "prediction_checkpoint": fields.get("prediction_checkpoint") or "PRIMARY_DAILY"}
    values = [row.get(c) for c in _COLUMNS]
    placeholders = ", ".join("?" for _ in _COLUMNS)
    try:
        conn.execute(f"INSERT INTO predictions ({', '.join(_COLUMNS)}) VALUES ({placeholders})", values)
        conn.execute("INSERT INTO audit_log (timestamp_utc, prediction_id, action) VALUES (?, ?, 'INSERT')",
                     (_utcnow_iso(), prediction_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise DuplicatePredictionError(f"prediction_id {prediction_id} already exists") from e

    return {"status": "INSERTED", "prediction_id": prediction_id}


def record_model_observation(conn: sqlite3.Connection, **fields) -> dict:
    return insert_prediction(conn, {**fields, "record_type": "MODEL_OBSERVATION"})


def record_shadow_observation(conn: sqlite3.Connection, **fields) -> dict:
    return insert_prediction(conn, {**fields, "record_type": "SHADOW_POLICY_OBSERVATION"})


def record_real_bet(conn: sqlite3.Connection, **fields) -> dict:
    return insert_prediction(conn, {**fields, "record_type": "REAL_BET"})


def record_historical_research(conn: sqlite3.Connection, **fields) -> dict:
    return insert_prediction(conn, {**fields, "record_type": "HISTORICAL_RESEARCH"})


def get_observation(conn: sqlite3.Connection, prediction_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
    return dict(row) if row else None


def query_observations(conn: sqlite3.Connection, record_type: str | None = None, game_date: str | None = None,
                        player_id: str | None = None, market_id: str | None = None,
                        context_state: str | None = None, confidence: str | None = None,
                        season: int | None = None) -> list[dict]:
    clauses, params = [], []
    for col, val in (("record_type", record_type), ("game_date", game_date), ("player_id", player_id),
                      ("market_id", market_id), ("context_state", context_state), ("confidence", confidence)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    if season is not None:
        clauses.append("game_date LIKE ?")
        params.append(f"{season}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM predictions {where} ORDER BY created_at_utc DESC", params).fetchall()
    return [dict(r) for r in rows]


def settle_prediction(conn: sqlite3.Connection, prediction_id: str, result_status: str,
                       actual_outcome: str | None = None, profit_loss: float | None = None,
                       closing_odds: float | None = None, closing_captured_at_utc: str | None = None,
                       clv: float | None = None, notes: str | None = None) -> dict:
    """Section 8: may update ONLY result-related fields. Never mutates
    an original prediction field -- enforced redundantly by the DB
    trigger even if this function's own SQL is ever edited carelessly."""
    if result_status not in RESULT_STATES:
        raise InvalidPredictionError(f"unknown result_status {result_status!r}")
    if get_observation(conn, prediction_id) is None:
        raise InvalidPredictionError(f"no prediction with id {prediction_id}")
    settled_at_utc = _utcnow_iso()
    conn.execute(
        """UPDATE predictions SET result_status=?, actual_outcome=?, settled_at_utc=?, profit_loss=?,
           closing_odds=?, closing_captured_at_utc=?, clv=?, notes=? WHERE prediction_id=?""",
        (result_status, actual_outcome, settled_at_utc, profit_loss, closing_odds, closing_captured_at_utc,
         clv, notes, prediction_id))
    conn.execute("INSERT INTO audit_log (timestamp_utc, prediction_id, action) VALUES (?, ?, ?)",
                 (settled_at_utc, prediction_id, "VOID" if result_status == "VOID" else "SETTLE"))
    conn.commit()
    return get_observation(conn, prediction_id)


def summary_metrics(conn: sqlite3.Connection) -> dict:
    """Section 20: NEVER combines MODEL_OBSERVATION with REAL_BET for
    actual P&L -- one section per record type, structurally separate."""
    out = {}
    for rt in RECORD_TYPES:
        rows = query_observations(conn, record_type=rt)
        section = {"n": len(rows), "n_settled": sum(1 for r in rows if r["result_status"] != "PENDING")}
        if rt == "REAL_BET":
            settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "PUSH")]
            pnl_values = [r["profit_loss"] for r in settled if r["profit_loss"] is not None]
            section["total_profit_loss"] = sum(pnl_values) if pnl_values else None
            if len(rows) == 0:
                section["message"] = "NO REAL BETS RECORDED"
        else:
            section["n_with_outcome"] = sum(1 for r in rows if r["actual_outcome"] is not None)
        out[rt] = section
    return out


def operational_summary(conn: sqlite3.Connection) -> dict:
    """Preseason Closing sprint, Track 11: real operational visibility
    into the ledger for Today/Ledger widgets -- today's recorded count,
    pending-past-event settlements, last recorded timestamp, and a
    checkpoint breakdown. Never fabricates a count; an empty ledger
    reports honest zeros."""
    rows = conn.execute("SELECT * FROM predictions").fetchall()
    rows = [dict(r) for r in rows]
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    now_iso = _utcnow_iso()
    recorded_today = [r for r in rows if (r.get("created_at_utc") or "").startswith(today)]
    pending_past_event = [r for r in rows
                           if r.get("result_status") == "PENDING" and (r.get("event_start_utc") or "") < now_iso]
    checkpoints = {}
    for r in rows:
        cp = r.get("prediction_checkpoint") or "UNKNOWN"
        checkpoints[cp] = checkpoints.get(cp, 0) + 1
    last_recorded = max((r["created_at_utc"] for r in rows if r.get("created_at_utc")), default=None)
    return {
        "total": len(rows),
        "recorded_today": len(recorded_today),
        "pending_settlement": len(pending_past_event),
        "last_recorded_at_utc": last_recorded,
        "by_checkpoint": checkpoints,
    }


def export_observations_csv(conn: sqlite3.Connection, out_path: str, record_type: str | None = None) -> str:
    rows = query_observations(conn, record_type=record_type)
    if not rows:
        fieldnames = _COLUMNS + ["result_status", "actual_outcome", "settled_at_utc", "profit_loss",
                                  "closing_odds", "closing_captured_at_utc", "clv", "notes"]
    else:
        fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return out_path


def backup_db(dest_path: str, db_path: Path = DB_PATH) -> str:
    shutil.copy2(db_path, dest_path)
    return dest_path


# ---- prospective evaluation query helpers (Section 22) ----

def context_cohort(conn: sqlite3.Connection, market_family: str, context_state: str = "COLD_AND_TOI_DECLINE",
                    season: int | None = None) -> list[dict]:
    rows = query_observations(conn, season=season)
    return [r for r in rows if r.get("market_family") == market_family and r.get("context_state") == context_state]


def raw_vs_adjusted_summary(conn: sqlite3.Connection, market_family: str, season: int | None = None) -> dict:
    rows = [r for r in query_observations(conn, season=season) if r.get("market_family") == market_family]
    with_outcome = [r for r in rows if r.get("actual_outcome") is not None]
    if not with_outcome:
        return {"n": len(rows), "n_with_outcome": 0}
    raw_err = [(float(r["actual_outcome"]) - r["raw_probability"]) ** 2 for r in with_outcome
               if r["raw_probability"] is not None]
    adj_err = [(float(r["actual_outcome"]) - r["context_adjusted_probability"]) ** 2 for r in with_outcome
               if r["context_adjusted_probability"] is not None]
    return {
        "n": len(rows), "n_with_outcome": len(with_outcome),
        "raw_brier": (sum(raw_err) / len(raw_err)) if raw_err else None,
        "adjusted_brier": (sum(adj_err) / len(adj_err)) if adj_err else None,
    }
