"""
Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 24-49:
the theoretical "$10 on every BET recommendation" bankroll. Answers
exactly the owner's own question (Part 49): "IF THE PROGRAM HAD PUT $10
ON EVERY BET IT RECOMMENDED, WHAT WOULD THE BANKROLL BE RIGHT NOW?" --
from immutable stored paper-bet entries and settlements, never
recomputed from today's current odds (Part 49's own requirement).

PAPER_BET is a distinct concept from REAL_BET and from
MODEL_OBSERVATION (Part 25) -- this is a SEPARATE SQLite database from
both nhl.db and operational/prospective_observations.db, never mixing
real-money P&L, paper P&L, or the two paper tracks with each other
(Part 26):
  - REAL_MARKET_PAPER: paper bets priced with real, verified DraftKings
    odds only (dashboard/live_dk.py's LIVE_SOURCE_LABEL rows).
  - DEMO_PAPER: paper bets priced with the deterministic simulated demo
    prices (dashboard/demo_data.py / eligible_bets.py).
  - REAL_BET stays exactly what it already was in
    operational/prospective_ledger.py -- untouched by this module,
    currently and correctly empty (Part 26/"Safety/Integrity").

SETTLEMENT (Part 33): reuses the real, already-built
operational/outcome_resolver.py's resolution CONCEPT (a batch scanner
that finds PENDING bets whose event has started and tries to resolve a
real outcome) -- but that resolver's actual per-stat functions require
a real nhl.db game_id, which does not exist yet for the 2026-27 schedule
(see LIVE_DK_PAPER_BANKROLL_COMPLETION_REPORT.md's honest disclosure).
So today, every paper bet this module creates correctly stays PENDING --
this module never fabricates a settlement outcome (Part 43/44). The
scanner is still real and wired: the day nhl.db has real 2026-27
results, resolvable REAL_MARKET_PAPER bets will settle through it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "operational" / "paper_bankroll.db"
SCHEMA_PATH = REPO_ROOT / "operational" / "paper_bankroll_schema.sql"
SCHEMA_VERSION = 5  # v5: experiments (paper_experiments, experiment_id on every ticket); additive, no row rewritten.
                    # v4: origin (AUTOMATIC / MANUALLY_ADDED) + provenance_json; additive, no row rewritten.
                    # v3: settlement_json column + ticket_alerts table (alerts-only revalidation).
                    # v2: Live Odds/Parlay/Post-Mortem activation sprint,
                    # Part 49 -- added the GAME_PARLAY_PAPER track.

PAPER_STARTING_BANKROLL = 500.00           # the opening balance of a NEW experiment (an existing experiment keeps its own starting_bankroll)
FIRST_EXPERIMENT_ID = "EXP-1"
PAPER_BET_STAKE = 10.00  # 2% of the default starting bankroll -- fixed, never dynamic/Kelly

TRACKS = ("REAL_MARKET_PAPER", "DEMO_PAPER", "GAME_PARLAY_PAPER")
PRICE_SOURCES = ("LIVE_DRAFTKINGS", "SIMULATED_DEMO")
ORIGINS = ("AUTOMATIC", "MANUALLY_ADDED")
# The model book (the $500 experiment) is the AUTOMATIC tickets and nothing else. A MANUALLY_ADDED row that exists in this ledger is a
# pre-separation legacy record: it is kept untouched for audit, but no account, exposure, slot, performance or settlement query counts it.
# Hand-added bets now live in operational/personal_logs.py, a different database file.
MODEL_BOOK_ORIGIN = "AUTOMATIC"
RESULT_STATES = ("PENDING", "WIN", "LOSS", "VOID", "UNRESOLVED")

ODDS_RANGE_BUCKETS_ORDER = (
    "shorter than -500", "-500 to -400", "-399 to -300", "-299 to -200",
    "-199 to -110", "-109 to +100", "+101 to +200", "+201 or longer",
)


class InvalidPaperBetError(Exception):
    pass


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    from operational import state_paths
    db_path = state_paths.guard_database(db_path)                  # a test never opens the production file
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_for_dashboard(db_path: Path | None = None) -> sqlite3.Connection:
    """Connection for dashboard pages (Community Cloud memory sprint, Part
    10). LOCAL/PRODUCTION: exactly init_db() as before. COMMUNITY_CLOUD_MODE:
    strictly read-only -- an existing file is opened `mode=ro` (no DDL, no
    migration, no bet creation); a missing one yields an empty in-memory
    schema so pages show honest zeros, and no file is ever created."""
    from operational import runtime_mode
    path = db_path if db_path is not None else DB_PATH
    if not runtime_mode.is_community_cloud():
        return init_db(path)
    if Path(path).exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    return init_db(Path(":memory:"))


_V2_CREATE_PAPER_BETS = """
CREATE TABLE paper_bets (
    paper_bet_id            TEXT PRIMARY KEY,
    idempotency_key         TEXT NOT NULL UNIQUE,
    track                   TEXT NOT NULL CHECK (track IN ('REAL_MARKET_PAPER', 'DEMO_PAPER', 'GAME_PARLAY_PAPER')),
    is_combo                INTEGER NOT NULL DEFAULT 0,
    top_conviction          INTEGER NOT NULL DEFAULT 0,
    event_id                TEXT,
    game_date               TEXT,
    player_id               TEXT,
    player_name_snapshot    TEXT,
    team                    TEXT,
    opponent                TEXT,
    market_id               TEXT NOT NULL,
    market_family           TEXT,
    threshold                TEXT,
    side                     TEXT,
    price_source             TEXT NOT NULL CHECK (price_source IN ('LIVE_DRAFTKINGS', 'SIMULATED_DEMO')),
    legs_json                 TEXT,
    entry_odds                REAL NOT NULL,
    model_probability          REAL,
    conservative_probability    REAL,
    market_no_vig_probability    REAL,
    edge                          REAL,
    ev                             REAL,
    confidence                      TEXT,
    model_version                    TEXT,
    prediction_checkpoint             TEXT,
    stake                              REAL NOT NULL,
    created_at_utc                      TEXT NOT NULL,
    event_start_utc                      TEXT,
    result_status                         TEXT NOT NULL DEFAULT 'PENDING'
                                           CHECK (result_status IN ('PENDING', 'WIN', 'LOSS', 'VOID', 'UNRESOLVED')),
    settled_at_utc                        TEXT,
    profit_loss                           REAL,
    closing_odds                          REAL,
    closing_captured_at_utc               REAL,
    clv                                   REAL,
    notes                                 TEXT
);
"""

_V2_CREATE_TRIGGER = """
CREATE TRIGGER paper_bets_immutability
BEFORE UPDATE ON paper_bets
FOR EACH ROW
WHEN
    NEW.track IS NOT OLD.track OR
    NEW.is_combo IS NOT OLD.is_combo OR
    NEW.top_conviction IS NOT OLD.top_conviction OR
    NEW.market_id IS NOT OLD.market_id OR
    NEW.threshold IS NOT OLD.threshold OR
    NEW.side IS NOT OLD.side OR
    NEW.price_source IS NOT OLD.price_source OR
    NEW.entry_odds IS NOT OLD.entry_odds OR
    NEW.model_probability IS NOT OLD.model_probability OR
    NEW.conservative_probability IS NOT OLD.conservative_probability OR
    NEW.stake IS NOT OLD.stake OR
    NEW.created_at_utc IS NOT OLD.created_at_utc OR
    NEW.idempotency_key IS NOT OLD.idempotency_key
BEGIN
    SELECT RAISE(ABORT, 'paper_bets: entry fields are immutable after creation -- only settlement columns may change');
END;
"""


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """SQLite can't ALTER a CHECK constraint in place -- recreate the
    table with the widened `track` constraint (adding GAME_PARLAY_PAPER,
    Part 49) and copy every existing row across unchanged. Real,
    reversible-by-backup, run once per database (guarded by
    schema_version in init_db() below); this project's paper_bankroll.db
    had exactly 6 real rows (all DEMO_PAPER, all PENDING) when this
    migration was written."""
    conn.execute("ALTER TABLE paper_bets RENAME TO paper_bets_v1")
    conn.executescript(_V2_CREATE_PAPER_BETS)
    conn.execute("INSERT INTO paper_bets SELECT * FROM paper_bets_v1")
    conn.execute("DROP TABLE paper_bets_v1")
    conn.executescript(_V2_CREATE_TRIGGER)
    conn.commit()


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = get_conn(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    if not {"origin", "experiment_id"} <= {r["name"] for r in conn.execute("PRAGMA table_info(paper_bets)")}:
        # An older table: the schema file's triggers name columns it does not have yet, which breaks the table
        # rebuilds below. They are dropped here and re-created by the schema file once the columns exist.
        conn.execute("DROP TRIGGER IF EXISTS paper_bets_immutability")
        conn.execute("DROP TRIGGER IF EXISTS paper_bets_experiment_must_be_active")
        conn.commit()
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    elif row["version"] < 2:
        _migrate_v1_to_v2(conn)
    if row is not None and row["version"] < SCHEMA_VERSION:
        _migrate_to_v3(conn)
        _migrate_to_v4(conn)
        _migrate_to_v5(conn)
        with open(SCHEMA_PATH) as f:     # re-create any trigger that a table rebuild dropped (all are IF NOT EXISTS)
            conn.executescript(f.read())
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        conn.commit()
    _ensure_first_experiment(conn)
    return conn


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Additive only: nothing already stored is rewritten. (ticket_alerts is
    created by the schema file's CREATE IF NOT EXISTS before this runs.)"""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(paper_bets)")}
    if "settlement_json" not in columns:
        conn.execute("ALTER TABLE paper_bets ADD COLUMN settlement_json TEXT")


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Additive only: every existing ticket becomes AUTOMATIC through the column default (SQLite does not rewrite
    the rows). The immutability trigger is dropped here and re-created by the schema file with the new columns."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(paper_bets)")}
    if "origin" not in columns:
        conn.execute("ALTER TABLE paper_bets ADD COLUMN origin TEXT NOT NULL DEFAULT 'AUTOMATIC' "
                     "CHECK (origin IN ('AUTOMATIC', 'MANUALLY_ADDED'))")
    if "provenance_json" not in columns:
        conn.execute("ALTER TABLE paper_bets ADD COLUMN provenance_json TEXT")
    conn.execute("DROP TRIGGER IF EXISTS paper_bets_immutability")
    conn.commit()


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Additive only: every existing ticket becomes EXP-1 through the column default (SQLite does not rewrite the rows). The immutability trigger is dropped here and re-created by the
    schema file with the new column."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(paper_bets)")}
    if "experiment_id" not in columns:
        conn.execute(f"ALTER TABLE paper_bets ADD COLUMN experiment_id TEXT NOT NULL DEFAULT '{FIRST_EXPERIMENT_ID}'")
    conn.execute("DROP TRIGGER IF EXISTS paper_bets_immutability")
    conn.execute("DROP TRIGGER IF EXISTS paper_bets_experiment_must_be_active")
    conn.commit()


def _ensure_first_experiment(conn: sqlite3.Connection) -> None:
    """A database with no experiment record gets EXP-1, ACTIVE, from its first ticket (or now). It never creates a second one: only start_new_experiment() does."""
    if conn.execute("SELECT 1 FROM paper_experiments LIMIT 1").fetchone():
        return
    first = conn.execute("SELECT MIN(created_at_utc) m FROM paper_bets").fetchone()["m"]
    conn.execute("INSERT OR IGNORE INTO paper_experiments (experiment_id, number, label, status, starting_bankroll, started_at_utc) VALUES (?, 1, ?, 'ACTIVE', ?, ?)",
                 (FIRST_EXPERIMENT_ID, "Experiment 1: the original model book", PAPER_STARTING_BANKROLL, first or _utcnow_iso()))
    conn.commit()


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def odds_range_bucket(odds: float) -> str:
    """Part 39's exact 8 buckets. American odds never fall strictly
    between -99 and +99, so these cumulative <= checks (in ascending
    numeric order) never leave a gap for any valid price."""
    if odds <= -500:
        return "shorter than -500"
    if odds <= -400:
        return "-500 to -400"
    if odds <= -300:
        return "-399 to -300"
    if odds <= -200:
        return "-299 to -200"
    if odds <= -110:
        return "-199 to -110"
    if odds <= 100:
        return "-109 to +100"
    if odds <= 200:
        return "+101 to +200"
    return "+201 or longer"


def compute_payout(stake: float, odds: float, result_status: str) -> float:
    """Part 32's exact formulas -- profit only, never including the
    returned stake (WIN's total return is stake + this profit)."""
    if result_status == "WIN":
        return stake * (odds / 100.0) if odds > 0 else stake * (100.0 / abs(odds))
    if result_status == "LOSS":
        return -stake
    return 0.0  # VOID / UNRESOLVED / PENDING


def compute_paper_idempotency_key(*, track: str, event_id, participant_id, market_id, threshold, side,
                                   price_source: str) -> str:
    """Part 29: keyed by event + participant/team/goalie + canonical
    market + threshold + side + price source -- a PRE_GAME_UPDATE or
    MARKET_REFRESH recomputing the same real-world opportunity produces
    the SAME key, so record_paper_bet() returns the existing row instead
    of placing a second $10 bet (Part 29's explicit requirement)."""
    raw = "|".join(str(x) for x in (track, event_id, participant_id, market_id, threshold, side, price_source))
    return hashlib.sha256(raw.encode()).hexdigest()


_COLUMNS = [
    "paper_bet_id", "idempotency_key", "track", "is_combo", "top_conviction",
    "event_id", "game_date", "player_id", "player_name_snapshot", "team", "opponent",
    "market_id", "market_family", "threshold", "side", "price_source", "legs_json",
    "entry_odds", "model_probability", "conservative_probability", "market_no_vig_probability",
    "edge", "ev", "confidence", "model_version", "prediction_checkpoint",
    "stake", "created_at_utc", "event_start_utc", "origin", "provenance_json", "experiment_id",
]


# ----------------------------------------------------------------------------------------------------------------- experiments
# One bankroll experiment is ACTIVE; every read below defaults to it (experiment="ACTIVE"), so an archived experiment can never leak into the current account, performance, reconciliation or
# slot counts. experiment="EXP-1" (or any id) reads that experiment on its own; experiment="ALL" (or None) reads every row (audit exports only).

def _has_experiments(conn: sqlite3.Connection) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_experiments'").fetchone():
        return False
    return "experiment_id" in {r["name"] for r in conn.execute("PRAGMA table_info(paper_bets)")}


def experiments(conn: sqlite3.Connection) -> list[dict]:
    if not _has_experiments(conn):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM paper_experiments ORDER BY number")]


def active_experiment(conn: sqlite3.Connection) -> dict | None:
    if not _has_experiments(conn):
        return None
    row = conn.execute("SELECT * FROM paper_experiments WHERE status = 'ACTIVE'").fetchone()
    return dict(row) if row else None


def resolve_experiment(conn: sqlite3.Connection, experiment) -> dict | None:
    """The experiment record an `experiment` argument names, or None when it means 'every row' (or the database predates experiments)."""
    if experiment in (None, "ALL") or not _has_experiments(conn):
        return None
    if experiment == "ACTIVE":
        active = active_experiment(conn)
        if active is None:
            raise InvalidPaperBetError("this database has experiments but none is ACTIVE: refusing to read every experiment as the current account")      # fail closed, never fail open
        return active
    row = conn.execute("SELECT * FROM paper_experiments WHERE experiment_id = ?", (experiment,)).fetchone()
    if row is None:
        raise InvalidPaperBetError(f"no experiment {experiment!r}")
    return dict(row)


def _scope(conn: sqlite3.Connection, experiment) -> tuple[str, list, float]:
    """(extra WHERE clause starting with ' AND', its parameters, starting bankroll) for one experiment argument."""
    exp = resolve_experiment(conn, experiment)
    if exp is None:
        return "", [], PAPER_STARTING_BANKROLL
    return " AND experiment_id = ?", [exp["experiment_id"]], float(exp["starting_bankroll"])


def experiment_filter(conn: sqlite3.Connection, experiment="ACTIVE") -> tuple[str, list]:
    """(" AND experiment_id = ?", [id]) for callers that write their own SQL against paper_bets; ("", []) when the experiment means 'every row' or the database predates experiments."""
    clause, params, _ = _scope(conn, experiment)
    return clause, params


def experiment_brief(exp: dict | None) -> dict | None:
    if exp is None:
        return None
    return {k: exp.get(k) for k in ("experiment_id", "number", "label", "status", "starting_bankroll", "started_at_utc", "ended_at_utc")}


def account_state(conn: sqlite3.Connection, track: str, experiment="ACTIVE") -> dict:
    """The paper account for one track, derived only from stored rows.
    cash = starting bankroll + realized P&L - stakes still open
    (PENDING or UNRESOLVED: money is committed until a ticket settles).
    equity = cash + open stakes at cost (nothing is marked to market).
    Only the named experiment's tickets count (the ACTIVE one by default) and its own starting bankroll."""
    clause, params, start = _scope(conn, experiment)
    row = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN result_status IN ('WIN','LOSS','VOID') THEN COALESCE(profit_loss, 0) END), 0) AS realized,
             COALESCE(SUM(CASE WHEN result_status IN ('PENDING','UNRESOLVED') THEN stake END), 0) AS open_stakes,
             COALESCE(SUM(CASE WHEN result_status IN ('PENDING','UNRESOLVED') THEN 1 END), 0) AS open_tickets,
             COUNT(*) AS tickets
           FROM paper_bets WHERE track = ? AND origin = 'AUTOMATIC'""" + clause, [track] + params).fetchone()
    realized, open_stakes = float(row["realized"]), float(row["open_stakes"])
    cash = start + realized - open_stakes
    return {"track": track, "starting_bankroll": start, "available_cash": round(cash, 2),
            "open_stakes": round(open_stakes, 2), "open_tickets": int(row["open_tickets"]),
            "equity": round(cash + open_stakes, 2), "settled_pnl": round(realized, 2),
            "tickets": int(row["tickets"]), "experiment": experiment_brief(resolve_experiment(conn, experiment))}


def _validate_stake_and_odds(track: str, stake, entry_odds) -> None:
    """Rejects, before anything is written, a stake or price that could corrupt the account: a stake must be a
    real number (not a bool/string), finite and > 0; the official ticket track stakes exactly PAPER_BET_STAKE;
    American odds must be finite with |odds| >= 100 (a value between -100 and +100 is not a price)."""
    import math
    for name, value in (("stake", stake), ("entry_odds", entry_odds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InvalidPaperBetError(f"{name} must be a finite number, got {value!r}")
    if stake <= 0:
        raise InvalidPaperBetError(f"stake must be positive, got {stake!r}")
    if track == "REAL_MARKET_PAPER" and abs(stake - PAPER_BET_STAKE) > 1e-9:
        raise InvalidPaperBetError(f"official tickets stake exactly ${PAPER_BET_STAKE:.2f}, got {stake!r}")
    if abs(entry_odds) < 100:
        raise InvalidPaperBetError(f"entry_odds must be American odds (|odds| >= 100), got {entry_odds!r}")


PARLAY_EXPERIMENT_ONLY = True      # automatic single bets (moneyline, props) stay out of the model book; the owner's decision of 2026-10-10 (docs/POSTMORTEM_2026-10-10.md)
ENV_ALLOW_SINGLES = "NHL_ENGINE_ALLOW_SINGLES"


def singles_excluded() -> bool:
    """True in production. Only inside a unit-test run may NHL_ENGINE_ALLOW_SINGLES=1 let the older machinery tests (settlement, accounting) keep recording single bets; it has no effect anywhere else."""
    import os
    from operational import state_paths
    if os.environ.get(ENV_ALLOW_SINGLES, "").strip() == "1" and state_paths.under_test():
        return False
    return PARLAY_EXPERIMENT_ONLY


def record_paper_bet(conn: sqlite3.Connection, *, track: str, price_source: str, market_id: str,
                      entry_odds: float, event_id=None, game_date=None, player_id=None,
                      player_name_snapshot=None, team=None, opponent=None, market_family=None,
                      threshold=None, side=None, is_combo: bool = False, top_conviction: bool = False,
                      legs_json: str | None = None, model_probability=None, conservative_probability=None,
                      market_no_vig_probability=None, edge=None, ev=None, confidence=None,
                      model_version=None, prediction_checkpoint=None, stake: float = PAPER_BET_STAKE,
                      created_at_utc: str | None = None, event_start_utc=None,
                      idempotency_key: str | None = None, paper_bet_id: str | None = None,
                      origin: str = "AUTOMATIC", provenance_json: str | None = None) -> dict:
    """Part 28-30: FIRST ACTIONABLE BET CHECKPOINT entry only -- returns
    {"status": "DUPLICATE", "paper_bet_id": ...} on any later re-call
    with the same idempotency key, never a second $10 stake (Part 29).

    Account integrity: the duplicate check, the available-funds check and
    the INSERT happen inside one BEGIN IMMEDIATE transaction, so two
    concurrent writers cannot both spend the last $10. If the track's
    available cash is below `stake` nothing is written and the result is
    {"status": "INSUFFICIENT_FUNDS", "available_cash": ...}. There is no
    automatic top-up: cash only grows through settled winnings/refunds."""
    if track not in TRACKS:
        raise InvalidPaperBetError(f"unknown track {track!r}")
    if price_source not in PRICE_SOURCES:
        raise InvalidPaperBetError(f"unknown price_source {price_source!r}")
    if track == "REAL_MARKET_PAPER" and origin == "AUTOMATIC" and not is_combo and singles_excluded():
        return {"status": "EXCLUDED_SINGLE", "paper_bet_id": None,
                "reason": "The model book is a parlay experiment: automatic moneyline and prop single bets are not recorded in it (their observations are still logged). Earlier single bets stay on the record, reported separately."}
    if track == "REAL_MARKET_PAPER" and origin == "AUTOMATIC":
        from operational import recording_pause          # the owner pause covers EVERY automatic writer to the model book (tickets, moneyline, props), not just the ticket selector
        if recording_pause.is_paused():
            return {"status": "PAUSED", "paper_bet_id": None, "reason": recording_pause.notice()}
    if track == "REAL_MARKET_PAPER" and price_source != "LIVE_DRAFTKINGS":
        raise InvalidPaperBetError("REAL_MARKET_PAPER bets must be priced with LIVE_DRAFTKINGS -- "
                                    "never mix a simulated price into the real-market track")
    if track == "DEMO_PAPER" and price_source != "SIMULATED_DEMO":
        raise InvalidPaperBetError("DEMO_PAPER bets must be priced with SIMULATED_DEMO -- "
                                    "never mix a real price into the demo track")
    _validate_stake_and_odds(track, stake, entry_odds)
    if origin not in ORIGINS:
        raise InvalidPaperBetError(f"unknown origin {origin!r}")

    idempotency_key = idempotency_key or compute_paper_idempotency_key(
        track=track, event_id=event_id, participant_id=player_id or team, market_id=market_id,
        threshold=threshold, side=side, price_source=price_source)
    paper_bet_id = paper_bet_id or str(uuid.uuid4())
    row = {
        "paper_bet_id": paper_bet_id, "idempotency_key": idempotency_key, "track": track,
        "is_combo": int(is_combo), "top_conviction": int(top_conviction),
        "event_id": event_id, "game_date": game_date, "player_id": player_id,
        "player_name_snapshot": player_name_snapshot, "team": team, "opponent": opponent,
        "market_id": market_id, "market_family": market_family, "threshold": threshold, "side": side,
        "price_source": price_source, "legs_json": legs_json,
        "entry_odds": entry_odds, "model_probability": model_probability,
        "conservative_probability": conservative_probability,
        "market_no_vig_probability": market_no_vig_probability, "edge": edge, "ev": ev,
        "confidence": confidence, "model_version": model_version,
        "prediction_checkpoint": prediction_checkpoint, "stake": stake,
        "created_at_utc": created_at_utc or _utcnow_iso(), "event_start_utc": event_start_utc,
        "origin": origin, "provenance_json": provenance_json,
    }
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT paper_bet_id, experiment_id FROM paper_bets WHERE idempotency_key = ? OR paper_bet_id = ?",
                                 (idempotency_key, paper_bet_id)).fetchone()
        active = active_experiment(conn)
        if existing:
            conn.rollback()
            if active and existing["experiment_id"] != active["experiment_id"]:
                # the same ticket (same date and legs) exists in an ARCHIVED experiment: it is not "already recorded" in this one, and it cannot be recorded twice under the same id
                return {"status": "ARCHIVED_EXPERIMENT_DUPLICATE", "paper_bet_id": existing["paper_bet_id"], "experiment_id": existing["experiment_id"]}
            return {"status": "DUPLICATE", "paper_bet_id": existing["paper_bet_id"]}
        row["experiment_id"] = active["experiment_id"] if active else FIRST_EXPERIMENT_ID
        account = account_state(conn, track)
        if account["available_cash"] + 1e-9 < stake:
            conn.rollback()
            return {"status": "INSUFFICIENT_FUNDS", "available_cash": account["available_cash"],
                    "required": stake}
        placeholders = ", ".join("?" for _ in _COLUMNS)
        conn.execute(f"INSERT INTO paper_bets ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                     [row.get(c) for c in _COLUMNS])
        conn.execute("INSERT INTO paper_audit_log (timestamp_utc, paper_bet_id, action) VALUES (?, ?, 'INSERT')",
                     (_utcnow_iso(), paper_bet_id))
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"status": "INSERTED", "paper_bet_id": paper_bet_id}


def auto_create_paper_bets_from_opportunities(conn: sqlite3.Connection, opportunities: list[dict], *,
                                                track: str, price_source: str) -> list[dict]:
    """Part 28: exactly one $10 PAPER_BET per actionable BET-grade
    opportunity -- WATCH/WAIT/PASS/DATA_UNAVAILABLE/CONTRACT_NOT_VERIFIED
    and model-ineligible markets are never paper-bet."""
    results = []
    for o in opportunities:
        if o.get("decision") != "BET":
            continue
        odds = o.get("current_odds")
        if odds is None:
            continue
        result = record_paper_bet(
            conn, track=track, price_source=price_source, market_id=o.get("market_id") or o.get("market"),
            entry_odds=odds, event_id=o.get("event_id"), game_date=o.get("game_date"),
            player_id=o.get("player_id"), player_name_snapshot=o.get("player"), team=o.get("team"),
            opponent=o.get("opponent"), market_family=o.get("market"), threshold=o.get("threshold"),
            side=o.get("side"), top_conviction=bool(o.get("_top_conviction")),
            model_probability=o.get("coherent_probability"),
            conservative_probability=o.get("conservative_probability"),
            market_no_vig_probability=o.get("market_no_vig_probability"), edge=o.get("conservative_edge"),
            ev=o.get("ev"), confidence=o.get("confidence"), prediction_checkpoint="FIRST_ACTIONABLE",
            event_start_utc=o.get("event_start_utc"))
        results.append(result)
    return results


def create_demo_combo_paper_bet(conn: sqlite3.Connection, combo: dict) -> dict:
    """Part 45: DEMO_COMBO_PAPER_BET, straight and combo bankrolls kept
    separate via is_combo=True (never counted alongside straight bets in
    the same P&L line -- see bankroll_summary()'s own split). Only ever
    called on a HIGH_CONFIDENCE combo (Part 5's bar) -- never an
    estimated product of live leg prices presented as a real DK parlay
    price (Part 45's explicit warning); the entry price is this engine's
    own SIMULATED combo price, honestly labeled as such."""
    legs = combo["legs"]
    market_id = "COMBO:" + "+".join(sorted(f"{l['player_id']}:{l['market_id']}" for l in legs))
    legs_snapshot = json.dumps([
        {"player": l["player"], "player_id": l["player_id"], "market": l["market"],
         "threshold": l["threshold"], "current_odds": l["current_odds"],
         "conservative_probability": l["conservative_probability"]}
        for l in legs
    ])
    return record_paper_bet(
        conn, track="DEMO_PAPER", price_source="SIMULATED_DEMO", market_id=market_id,
        entry_odds=combo["simulated_combo_price"], is_combo=True, top_conviction=True,
        legs_json=legs_snapshot, model_probability=combo["joint_probability"],
        conservative_probability=combo["joint_probability"], edge=combo["combo_edge"],
        prediction_checkpoint="FIRST_ACTIONABLE")


def create_game_edge_parlay_paper_bet(conn: sqlite3.Connection, parlay_result: dict, *,
                                       event_id: str, price_source: str = "SIMULATED_DEMO") -> dict:
    """Part 49: GAME_PARLAY_PAPER track, kept fully separate from
    DEMO_PAPER/REAL_MARKET_PAPER (Part 51). Part 50's hard rule is
    enforced here, not just trusted of the caller: a non-"QUALIFIED"
    result (research.game_edge_parlay.engine.build_game_edge_parlay's
    own NO_QUALIFYING_GAME_EDGE_PARLAY output) raises rather than
    silently placing a bet. `price_source` defaults to SIMULATED_DEMO
    since the legs' own current_odds may themselves be simulated demo
    prices or real DraftKings prices depending on what fed the parlay
    engine -- the caller must say which, matching the honest labeling
    already required by Part 47 (never an unlabeled "live" price)."""
    if parlay_result.get("status") != "QUALIFIED":
        raise InvalidPaperBetError(
            "refusing to paper-bet a non-qualifying Game Edge Parlay result "
            f"(status={parlay_result.get('status')!r}) -- Part 50: never paper-bet a non-qualifier")

    combo = parlay_result["combo"]
    legs = combo.legs
    market_id = "GAME_EDGE_PARLAY:" + "+".join(
        sorted(f"{l['player_id']}:{l.get('market_id') or l.get('market')}:{l.get('threshold')}" for l in legs))
    legs_snapshot = json.dumps([
        {"player_id": l.get("player_id"), "player": l.get("player"), "market": l.get("market"),
         "threshold": l.get("threshold"), "current_odds": l.get("current_odds"),
         "conservative_probability": l.get("conservative_probability")}
        for l in legs
    ])
    return record_paper_bet(
        conn, track="GAME_PARLAY_PAPER", price_source=price_source, market_id=market_id,
        entry_odds=combo.estimated_combo_price, event_id=event_id, is_combo=True, top_conviction=False,
        legs_json=legs_snapshot, model_probability=combo.joint_probability,
        conservative_probability=combo.joint_probability, edge=combo.combo_edge,
        prediction_checkpoint="FIRST_ACTIONABLE")


def create_real_market_combo_paper_bet(conn: sqlite3.Connection, parlay_result: dict, *,
                                        event_start_utc: str | None = None,
                                        created_at_utc: str | None = None,
                                        code_version: str | None = None) -> dict:
    """Real-Market Paper Parlay engine V1 (Production Hardening + Parlay Build
    block, 2026-09-29): REAL_MARKET_PAPER track, is_combo=True -- distinct
    from create_demo_combo_paper_bet (DEMO_PAPER, simulated prices) and
    create_game_edge_parlay_paper_bet (GAME_PARLAY_PAPER, single-game).
    This is the first CROSS-GAME real-priced combo; it reuses the
    REAL_MARKET_PAPER track rather than inventing a fourth one because the
    schema's track/price_source split only encodes pricing provenance
    (real DraftKings vs simulated demo), never single-game-vs-cross-game
    scope, and every leg here is required (research/real_market_parlay/
    engine.py::leg_is_eligible) to be a real, contract-verified, real-
    market-priced quote. A non-"QUALIFIED" result is refused, mirroring
    create_game_edge_parlay_paper_bet's own hard rule -- never paper-bet a
    non-qualifier.

    entry_odds is the combo's estimated_combo_price (the product of each
    leg's OWN real, verified American price) -- never a fabricated combined
    DraftKings price (research/real_market_parlay/engine.py's own
    ParlayResult.offered_parlay_price stays None always); this mirrors
    create_game_edge_parlay_paper_bet's identical, already-established
    choice for its own single-game combos.

    Platform Recovery block (2026-09-29): `event_start_utc` should be the
    EARLIEST scheduled start among the combo's own games -- a ParlayLeg
    carries only its own game_id, never nhl.db's scheduled_start_utc, so
    this bankroll-only module (which deliberately never takes an nhl.db
    connection) cannot derive it itself; the caller (which does hold an
    nhl.db connection) must supply it. Without it,
    find_unresolved_past_event_bets()'s own `event_start_utc IS NOT NULL
    AND event_start_utc < ?` filter never matches this row, and the bet
    sits PENDING forever, invisible to settlement. The earliest leg's
    start (not the latest) is used so the combo becomes a settlement
    CANDIDATE as soon as any leg's game could plausibly be final --
    resolve_combo_bet() itself still correctly reports
    PENDING_STILL_WAITING until every leg's own game has actually gone
    FINAL.

    `created_at_utc` defaults to the real current wall-clock time
    (record_paper_bet()'s own default) -- correct for real production use,
    where the caller never passes a fictional `now`. A caller that DOES
    pass its own reference time (e.g. operational/real_parlay_paper_trader.py's
    own day-level idempotency check, or a test) should also pass that same
    time here, or its own "already staked today" comparison against this
    row's stored created_at_utc will never line up."""
    if parlay_result.get("status") != "QUALIFIED":
        raise InvalidPaperBetError(
            "refusing to paper-bet a non-qualifying Real-Market Parlay result "
            f"(status={parlay_result.get('status')!r}) -- never paper-bet a non-qualifier")

    combo = parlay_result["combo"]
    legs = combo.legs
    # Owner Escalation block (2026-09-30): date-scoped so the SAME exact leg
    # combination is blocked from being staked twice on the SAME real day
    # (the actual duplicate-bet risk), but is never permanently blocked if
    # the identical players/thresholds genuinely line up again on a later
    # real day -- a plain, date-free key would silently treat "the same
    # legs happened to look best again three weeks later" as a duplicate
    # of the first bet forever, which is wrong.
    from operational import eastern_time as et
    _as_of = dt.datetime.fromisoformat(created_at_utc) if created_at_utc else None
    _stake_date = et.eastern_today(_as_of)
    # Production Gap Closure sprint (2026-09-30): game_id is now part of the
    # key (previously only participant_id:market_family:threshold) -- a real
    # audit found the old key had no event/game identity at all, so two
    # legs on the same participant/market/threshold from two DIFFERENT
    # games on the same real day (a corrupted/duplicate game row, or a
    # neutral-site doubleheader) would silently collide and be treated as
    # the same bet. game_id is real production data on every leg already
    # (research/real_market_parlay/engine.py::ParlayLeg.game_id) -- this
    # costs nothing and closes the gap outright rather than merely noting it.
    market_id = f"REAL_MARKET_PARLAY:{_stake_date}:" + "+".join(
        sorted(f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}" for l in legs))
    ticket_id = compute_ticket_id(_stake_date, legs)
    frozen = [_freeze_leg(l) for l in legs]
    for f in frozen:
        f["code_version"] = code_version
        f["haircut_margin"] = getattr(combo, "leg_probability_margin", None)
    legs_snapshot = json.dumps(frozen)
    model_versions = sorted({l.model_version for l in legs if getattr(l, "model_version", "")})
    if code_version:
        model_versions.append(f"code:{code_version}")
    return record_paper_bet(
        conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
        market_id=market_id, entry_odds=combo.estimated_combo_price, is_combo=True, top_conviction=False,
        legs_json=legs_snapshot, model_probability=combo.joint_probability,
        conservative_probability=combo.joint_probability, edge=combo.combo_edge,
        ev=getattr(combo, "ev_estimated", None), model_version=",".join(model_versions) or None,
        prediction_checkpoint="FIRST_ACTIONABLE", event_start_utc=event_start_utc,
        created_at_utc=created_at_utc, idempotency_key=ticket_id, paper_bet_id=ticket_id)


def compute_manual_ticket_id(stake_date: str, legs) -> str:
    """Identity of a manually added ticket. The leg identity is the same one the automatic ticket id uses, behind a
    different prefix, so the two origins are told apart on sight while the underlying bet is still recognisable."""
    return "M" + compute_ticket_id(stake_date, legs)[1:]


def create_manual_paper_bet(conn: sqlite3.Connection, combo, *, provenance: dict, event_start_utc: str | None,
                             created_at_utc: str | None = None, code_version: str | None = None) -> dict:
    """RETIRED: no production code calls this any more (hand-added bets go to operational/personal_logs.py; a ledger row it writes is excluded from
    every model-book query). Kept only so the legacy record and its tests stay interpretable. It was the one writer for MANUALLY_ADDED tickets. Same $10 stake, same REAL_MARKET_PAPER account and the same atomic
    funds/duplicate transaction as automatic tickets; the ticket id carries an M prefix and the exact details the user
    accepted are frozen in provenance_json. Refuses a bet the automatic tickets already hold (same legs, same day):
    that would stake one bet twice."""
    from operational import eastern_time as et
    _as_of = dt.datetime.fromisoformat(created_at_utc) if created_at_utc else None
    stake_date = et.eastern_today(_as_of)
    legs = combo.legs
    automatic_id = compute_ticket_id(stake_date, legs)
    existing = conn.execute("SELECT paper_bet_id, origin FROM paper_bets WHERE paper_bet_id = ? OR idempotency_key = ?",
                            (automatic_id, automatic_id)).fetchone()
    if existing:
        return {"status": "DUPLICATE", "paper_bet_id": existing["paper_bet_id"], "duplicate_of_origin": existing["origin"]}
    ticket_id = compute_manual_ticket_id(stake_date, legs)
    market_id = f"REAL_MARKET_PARLAY:{stake_date}:" + "+".join(
        sorted(f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}" for l in legs))
    frozen = []
    for l in legs:
        f = _freeze_leg(l)
        f["code_version"] = code_version
        f["haircut_margin"] = getattr(combo, "leg_probability_margin", None)
        frozen.append(f)
    model_versions = sorted({l.model_version for l in legs if getattr(l, "model_version", "")})
    if code_version:
        model_versions.append(f"code:{code_version}")
    return record_paper_bet(
        conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id=market_id,
        entry_odds=combo.estimated_combo_price, is_combo=True, top_conviction=False, legs_json=json.dumps(frozen),
        model_probability=combo.joint_probability, conservative_probability=combo.joint_probability,
        edge=combo.combo_edge, ev=getattr(combo, "ev_estimated", None), model_version=",".join(model_versions) or None,
        prediction_checkpoint="MANUAL_ADD", event_start_utc=event_start_utc, created_at_utc=created_at_utc,
        idempotency_key=ticket_id, paper_bet_id=ticket_id, origin="MANUALLY_ADDED",
        provenance_json=json.dumps(provenance, sort_keys=True))


def compute_ticket_id(stake_date: str, legs) -> str:
    """Deterministic ticket identity: Eastern date + the sorted economic
    identity (game, participant, market, line, side) of every leg. A price
    move does not change it, so the recommendation shown, the ticket
    recorded, its settlement and its postmortem all carry the same id."""
    identities = [f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}:{l.side}" for l in legs]
    identities.sort()  # order-independent identity of the leg set (not a training or eligibility ordering)
    return "T" + hashlib.sha256(f"{stake_date}|{'|'.join(identities)}".encode()).hexdigest()[:14].upper()


def _freeze_leg(l) -> dict:
    a = l.american_price
    return {"participant_id": l.participant_id, "participant_name": l.participant_name,
            "market_family": l.market_family, "threshold": l.threshold, "side": l.side,
            "american_price": a, "decimal_price": round(1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a)), 4),
            "conservative_probability": l.conservative_probability,
            "game_id": l.game_id, "event_id": l.event_id, "sportsbook": l.sportsbook,
            "captured_at_utc": l.captured_at_utc, "team": getattr(l, "team", None),
            "opponent": getattr(l, "opponent", None), "game_start_utc": getattr(l, "game_start_utc", None),
            "provider_start_utc": getattr(l, "provider_start_utc", None),
            # price timestamps, frozen apart: retrieval vs the provider's own market update, and the quote's age
            # when the ticket was recorded (see operational/quote_freshness.py)
            "retrieved_at_utc": getattr(l, "retrieved_at_utc", None),
            "quote_updated_utc": getattr(l, "quote_updated_utc", None),
            "quote_age_min_at_entry": getattr(l, "quote_age_min", None),
            "freshness_status": getattr(l, "freshness_status", "") or None,
            "model_version": getattr(l, "model_version", ""),
            # the evidence behind the stated probability (anchor tickets): the validated model's number, the player's record, the market's no-vig chance
            "model_probability": getattr(l, "model_probability", None), "empirical_probability": getattr(l, "empirical_probability", None),
            "empirical_hits": getattr(l, "empirical_hits", None), "empirical_games": getattr(l, "empirical_games", None), "empirical_games_current": getattr(l, "empirical_games_current", None),
            "market_probability": getattr(l, "market_probability", None)}


def todays_real_parlay_usage(conn: sqlite3.Connection, stake_date: str) -> dict:
    """Production Gap Closure sprint (2026-09-30): the real, PERSISTED
    picture of what today's real-market parlay trader has already
    committed to the bankroll -- so a second run() later the same ET day
    (whether the scheduled run firing twice, a manual rerun, or the
    trigger hook retriggering settlement's cousin job) can see what a
    FIRST run already staked instead of only ever checking its own
    in-memory candidate pool. Without this, the audited defect reproduces
    exactly: run 1 stakes 3 tickets, run 2 (unaware of run 1) builds its
    own fresh 3 from possibly-changed odds and stakes those too -- 6 for
    one day against an advertised cap of 5, some of them reusing legs run
    1 already committed.

    `stake_date` must be the same 'YYYY-MM-DD' Eastern date
    create_real_market_combo_paper_bet() stamps into its own market_id
    (operational.eastern_time.eastern_today()) -- this reads that exact
    prefix rather than re-deriving "today" a second, parallel way from
    created_at_utc.

    Returns {"count": int, "used_game_ids": set[str],
             "used_leg_keys": set[(game_id, participant_id, market_family, threshold)]}."""
    clause, params, _ = _scope(conn, "ACTIVE")
    rows = conn.execute(
        "SELECT legs_json FROM paper_bets WHERE track = 'REAL_MARKET_PAPER' AND is_combo = 1 AND origin = 'AUTOMATIC' "
        "AND market_id LIKE ?" + clause, [f"REAL_MARKET_PARLAY:{stake_date}:%"] + params).fetchall()
    used_game_ids: set[str] = set()
    used_leg_keys: set[tuple] = set()
    for row in rows:
        for leg in json.loads(row["legs_json"] or "[]"):
            used_game_ids.add(leg["game_id"])
            used_leg_keys.add((leg["game_id"], leg["participant_id"], leg["market_family"], leg["threshold"]))
    return {"count": len(rows), "used_game_ids": used_game_ids, "used_leg_keys": used_leg_keys}


def settle_paper_bet(conn: sqlite3.Connection, paper_bet_id: str, result_status: str, *,
                      closing_odds: float | None = None, closing_captured_at_utc: str | None = None,
                      clv: float | None = None, notes: str | None = None,
                      settled_odds: float | None = None, settlement_json: str | None = None) -> dict:
    """Part 33: only ever writes the settlement columns -- the DB
    trigger (paper_bets_immutability) additionally guarantees entry
    columns can never change even if this function's own SQL is edited
    carelessly later, mirroring prospective_ledger.py's own two-layer
    guarantee. Part 31: closing_odds is for CLV display only -- it never
    replaces entry_odds, which stays exactly what was recorded at entry."""
    if result_status not in RESULT_STATES:
        raise InvalidPaperBetError(f"unknown result_status {result_status!r}")
    row = conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id = ?", (paper_bet_id,)).fetchone()
    if row is None:
        raise InvalidPaperBetError(f"no paper bet with id {paper_bet_id}")
    if row["result_status"] != "PENDING" and row["result_status"] != "UNRESOLVED":
        raise InvalidPaperBetError(f"paper bet {paper_bet_id} already settled as {row['result_status']} "
                                    f"-- settlement is idempotent, never re-applied")
    # settled_odds is only passed when a parlay was repriced because a leg
    # was voided (see paper_bet_settlement_driver); entry_odds stays frozen.
    payout_odds = settled_odds if settled_odds is not None else row["entry_odds"]
    profit_loss = compute_payout(row["stake"], payout_odds, result_status)
    computed_clv = clv
    if computed_clv is None and closing_odds is not None:
        from pricing import odds_math as pm
        computed_clv = pm.american_to_prob(row["entry_odds"]) - pm.american_to_prob(closing_odds)
    settled_at_utc = _utcnow_iso()
    conn.execute(
        """UPDATE paper_bets SET result_status=?, settled_at_utc=?, profit_loss=?, closing_odds=?,
           closing_captured_at_utc=?, clv=?, notes=?, settlement_json=? WHERE paper_bet_id=?""",
        (result_status, settled_at_utc, profit_loss, closing_odds, closing_captured_at_utc,
         computed_clv, notes, settlement_json, paper_bet_id))
    conn.execute("INSERT INTO paper_audit_log (timestamp_utc, paper_bet_id, action) VALUES (?, ?, ?)",
                 (settled_at_utc, paper_bet_id, "VOID" if result_status == "VOID" else "SETTLE"))
    conn.commit()
    return dict(conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id = ?", (paper_bet_id,)).fetchone())


def record_ticket_alert(conn: sqlite3.Connection, paper_bet_id: str, kind: str, detail: str) -> bool:
    """Revalidation outcome. Never touches the ticket itself; returns True
    only when this exact alert was not already on file."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO ticket_alerts (paper_bet_id, kind, detail, created_at_utc) VALUES (?, ?, ?, ?)",
        (paper_bet_id, kind, detail, _utcnow_iso()))
    conn.commit()
    return cur.rowcount == 1


RETRACTIONS_DDL = ("CREATE TABLE IF NOT EXISTS ticket_alert_retractions (alert_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, retracted_at_utc TEXT NOT NULL)")


def retract_ticket_alert(conn: sqlite3.Connection, alert_id: int, reason: str) -> bool:
    """Marks an alert as raised in error. The alert row is never edited or deleted (the history stays), the ticket is untouched, and the card shows
    the alert as retracted with this reason. Returns True when newly retracted."""
    conn.execute(RETRACTIONS_DDL)
    cur = conn.execute("INSERT OR IGNORE INTO ticket_alert_retractions (alert_id, reason, retracted_at_utc) VALUES (?, ?, ?)", (alert_id, reason, _utcnow_iso()))
    conn.commit()
    return cur.rowcount == 1


def ticket_alerts(conn: sqlite3.Connection, paper_bet_ids: list[str] | None = None) -> dict[str, list[dict]]:
    have = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_alert_retractions'").fetchone()
    rows = conn.execute("SELECT a.*, " + ("r.reason AS retracted_reason FROM ticket_alerts a LEFT JOIN ticket_alert_retractions r ON r.alert_id = a.alert_id"
                                           if have else "NULL AS retracted_reason FROM ticket_alerts a") + " ORDER BY a.alert_id").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        if paper_bet_ids is None or r["paper_bet_id"] in paper_bet_ids:
            out.setdefault(r["paper_bet_id"], []).append(dict(r))
    return out


def find_unresolved_past_event_bets(conn: sqlite3.Connection, track: str | None = None,
                                     include_unresolved: bool = False) -> list[dict]:
    """Part 33's batch-scanner concept: PENDING bets whose event has
    already started (or a nhl.db game_id doesn't yet exist to resolve
    against) become UNRESOLVED rather than silently staying PENDING
    forever with no visible signal that something needs attention --
    this NEVER guesses a WIN/LOSS outcome (Part 43/44)."""
    now_iso = _utcnow_iso()
    states = "('PENDING', 'UNRESOLVED')" if include_unresolved else "('PENDING')"
    clauses = [f"result_status IN {states}", "event_start_utc IS NOT NULL", "event_start_utc < ?", "origin = 'AUTOMATIC'"]
    params: list = [now_iso]
    if track is not None:
        clauses.append("track = ?")
        params.append(track)
    rows = conn.execute(f"SELECT * FROM paper_bets WHERE {' AND '.join(clauses)}", params).fetchall()
    return [dict(r) for r in rows]


def find_pending_future_event_bets(conn: sqlite3.Connection, track: str | None = None,
                                    is_combo: bool | None = None, now: dt.datetime | None = None) -> list[dict]:
    """Bet Re-Validation block (2026-10-01): the complement of
    find_unresolved_past_event_bets() above -- PENDING bets whose event has
    NOT started yet, i.e. still cancellable before any real money (paper or
    otherwise) would be at stake on a game already underway. Used by
    operational/bet_revalidation.py to re-check an already-staked bet
    against the CURRENT real state (schedule, roster, goalie, identity)
    before its game starts -- never to re-settle or re-price a bet whose
    event has already begun."""
    now_iso = _utcnow_iso() if now is None else now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")      # the caller's clock when it has one (revalidation passes its own `now`)
    clauses = ["result_status = 'PENDING'", "event_start_utc IS NOT NULL", "event_start_utc >= ?", "origin = 'AUTOMATIC'"]
    params: list = [now_iso]
    if track is not None:
        clauses.append("track = ?")
        params.append(track)
    if is_combo is not None:
        clauses.append("is_combo = ?")
        params.append(1 if is_combo else 0)
    rows = conn.execute(f"SELECT * FROM paper_bets WHERE {' AND '.join(clauses)}", params).fetchall()
    return [dict(r) for r in rows]


def query_paper_bets(conn: sqlite3.Connection, track: str | None = None, is_combo: bool | None = None,
                      result_status: str | None = None, origin: str | None = MODEL_BOOK_ORIGIN, experiment="ACTIVE") -> list[dict]:
    """The model book's tickets by default (origin AUTOMATIC) in the ACTIVE experiment. Pass origin=None to read every row, including a legacy MANUALLY_ADDED record (audit only);
    experiment="EXP-1" for one archived experiment, experiment="ALL" for every experiment."""
    clauses, params = [], []
    exp = resolve_experiment(conn, experiment)
    if exp is not None:
        clauses.append("experiment_id = ?"); params.append(exp["experiment_id"])
    if origin is not None:
        clauses.append("origin = ?"); params.append(origin)
    if track is not None:
        clauses.append("track = ?"); params.append(track)
    if is_combo is not None:
        clauses.append("is_combo = ?"); params.append(int(is_combo))
    if result_status is not None:
        clauses.append("result_status = ?"); params.append(result_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM paper_bets {where} ORDER BY created_at_utc", params).fetchall()
    return [dict(r) for r in rows]


def bankroll_summary(conn: sqlite3.Connection, track: str, experiment="ACTIVE") -> dict:
    """Parts 34-40, 49: the theoretical bankroll for one track, computed
    ONLY from immutable stored rows -- never recomputed from today's
    current odds (Part 49). Straight and combo bets are tracked
    separately within the same starting bankroll (Part 38's "straight vs
    combo" breakdown) but share one running P&L line per track, since
    Part 26 only requires the THREE top-level tracks to stay separate,
    not a fourth split within a track."""
    rows = query_paper_bets(conn, track=track, experiment=experiment)
    start = _scope(conn, experiment)[2]
    settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "VOID")]
    settled_ordered = sorted(settled, key=lambda r: r["settled_at_utc"] or "")
    pending_rows = [r for r in rows if r["result_status"] in ("PENDING", "UNRESOLVED")]

    # Production Gap Closure sprint (2026-09-30): "Total Staked" (below)
    # only ever counted SETTLED turnover -- correct for ROI (a pending
    # bet's eventual profit/loss is unknown, so it can't yet contribute to
    # a realized return), but a viewer reading "Total Staked" naturally
    # expects "how much have I placed," which silently read $0.00 while a
    # real $10 bet sat PENDING. placed_stakes_total is that literal figure;
    # pending_exposure is the part of it not yet resolved; available_balance
    # is what's left of current_bankroll once pending exposure is set aside
    # -- three distinct, honestly-labeled numbers instead of one overloaded
    # one.
    placed_stakes_total = sum(r["stake"] for r in rows)
    pending_exposure = sum(r["stake"] for r in pending_rows)

    total_staked = sum(r["stake"] for r in settled)
    net_profit = sum(r["profit_loss"] or 0.0 for r in settled)
    total_return = sum(
        (r["stake"] + (r["profit_loss"] or 0.0)) if r["result_status"] == "WIN"
        else (r["stake"] if r["result_status"] == "VOID" else 0.0)
        for r in settled)
    wins = sum(1 for r in settled if r["result_status"] == "WIN")
    losses = sum(1 for r in settled if r["result_status"] == "LOSS")
    voids = sum(1 for r in settled if r["result_status"] == "VOID")
    pending = sum(1 for r in rows if r["result_status"] in ("PENDING", "UNRESOLVED"))
    decided = wins + losses
    hit_rate = (wins / decided) if decided > 0 else None
    roi = (net_profit / total_staked) if total_staked > 0 else None
    current_bankroll = start + net_profit

    # Bankroll history / peak / trough / drawdown / streaks -- replay
    # settled bets in settlement order (Part 37).
    running = start
    peak = running
    history = [{"paper_bet_id": None, "settled_at_utc": None, "bankroll": running,
                "cumulative_pnl": 0.0, "cumulative_roi": None, "cumulative_hit_rate": None}]
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    current_streak_type, current_streak_len = None, 0
    longest_win_streak, longest_loss_streak = 0, 0
    run_wins, run_losses, run_staked = 0, 0, 0.0
    for r in settled_ordered:
        running += (r["profit_loss"] or 0.0)
        peak = max(peak, running)
        drawdown = peak - running
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, drawdown / peak)
        run_staked += r["stake"]
        if r["result_status"] == "WIN":
            run_wins += 1
            current_streak_type = "WIN" if current_streak_type != "WIN" else current_streak_type
            current_streak_len = current_streak_len + 1 if current_streak_type == "WIN" else 1
            current_streak_type = "WIN"
            longest_win_streak = max(longest_win_streak, current_streak_len)
        elif r["result_status"] == "LOSS":
            run_losses += 1
            current_streak_len = current_streak_len + 1 if current_streak_type == "LOSS" else 1
            current_streak_type = "LOSS"
            longest_loss_streak = max(longest_loss_streak, current_streak_len)
        else:
            current_streak_type, current_streak_len = None, 0
        run_decided = run_wins + run_losses
        history.append({
            "paper_bet_id": r["paper_bet_id"], "settled_at_utc": r["settled_at_utc"], "bankroll": running,
            "cumulative_pnl": running - start,
            "cumulative_roi": ((running - start) / run_staked) if run_staked > 0 else None,
            "cumulative_hit_rate": (run_wins / run_decided) if run_decided > 0 else None,
        })

    return {
        "track": track, "starting_bankroll": start, "current_bankroll": current_bankroll,
        "peak_bankroll": peak, "lowest_bankroll": min(h["bankroll"] for h in history),
        "total_staked": total_staked, "total_return": total_return, "net_profit": net_profit, "roi": roi,
        "placed_stakes_total": placed_stakes_total, "pending_exposure": pending_exposure,
        "available_balance": current_bankroll - pending_exposure,
        "bets": len(rows), "wins": wins, "losses": losses, "voids": voids, "pending": pending,
        "hit_rate": hit_rate, "current_drawdown": peak - current_bankroll, "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "current_streak_type": current_streak_type, "current_streak_length": current_streak_len,
        "longest_win_streak": longest_win_streak, "longest_loss_streak": longest_loss_streak,
        "bankroll_history": history,
    }


def _breakdown(rows: list[dict], key_fn) -> dict:
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        if r["result_status"] not in ("WIN", "LOSS", "VOID"):
            continue
        groups[key_fn(r)].append(r)
    out = {}
    for key, grp in groups.items():
        wins = sum(1 for r in grp if r["result_status"] == "WIN")
        losses = sum(1 for r in grp if r["result_status"] == "LOSS")
        staked = sum(r["stake"] for r in grp)
        pnl = sum(r["profit_loss"] or 0.0 for r in grp)
        decided = wins + losses
        out[key] = {
            "bets": len(grp), "wins": wins, "losses": losses,
            "hit_rate": (wins / decided) if decided > 0 else None,
            "net_profit": pnl, "roi": (pnl / staked) if staked > 0 else None,
        }
    return out


def performance_breakdowns(conn: sqlite3.Connection, track: str, origin: str | None = MODEL_BOOK_ORIGIN, experiment="ACTIVE") -> dict:
    """Part 38: results by market family, confidence, edge bucket, odds
    range, Top Conviction status, straight vs combo (optionally for one origin)."""
    rows = query_paper_bets(conn, track=track, origin=origin, experiment=experiment)

    def edge_bucket(r):
        e = r.get("edge")
        if e is None:
            return "UNKNOWN"
        if e < 0.03:
            return "< 3pp"
        if e < 0.06:
            return "3-6pp"
        if e < 0.10:
            return "6-10pp"
        return ">= 10pp"

    return {
        "by_market_family": _breakdown(rows, lambda r: r.get("market_family") or "UNKNOWN"),
        "by_confidence": _breakdown(rows, lambda r: r.get("confidence") or "UNKNOWN"),
        "by_edge_bucket": _breakdown(rows, edge_bucket),
        "by_odds_range": _breakdown(rows, lambda r: odds_range_bucket(r["entry_odds"])),
        "by_top_conviction": _breakdown(rows, lambda r: "TOP_CONVICTION" if r["top_conviction"] else "OTHER"),
        "by_straight_vs_combo": _breakdown(rows, lambda r: "COMBO" if r["is_combo"] else "STRAIGHT"),
    }


def origin_performance(conn: sqlite3.Connection, track: str = "REAL_MARKET_PAPER", experiment="ACTIVE") -> dict:
    """The model book's results, in the shape the pages already use: AUTOMATIC and ALL are the same set. Hand-added bets are not in this ledger's
    accounting at all (see MODEL_BOOK_ORIGIN); their records are in operational/personal_logs.py, one log at a time."""
    rows = query_paper_bets(conn, track=track, experiment=experiment)

    def block(subset: list[dict]) -> dict:
        settled = [r for r in subset if r["result_status"] in ("WIN", "LOSS", "VOID")]
        wins = sum(1 for r in settled if r["result_status"] == "WIN")
        losses = sum(1 for r in settled if r["result_status"] == "LOSS")
        voids = sum(1 for r in settled if r["result_status"] == "VOID")
        staked = sum(r["stake"] for r in settled)
        pnl = sum(r["profit_loss"] or 0.0 for r in settled)
        open_rows = [r for r in subset if r["result_status"] in ("PENDING", "UNRESOLVED")]
        decided = wins + losses
        return {"tickets": len(subset), "settled": len(settled), "wins": wins, "losses": losses, "voids": voids,
                "pending": sum(1 for r in subset if r["result_status"] == "PENDING"),
                "unresolved": sum(1 for r in subset if r["result_status"] == "UNRESOLVED"),
                "open_stake": round(sum(r["stake"] for r in open_rows), 2), "settled_stake": round(staked, 2),
                "settled_pnl": round(pnl, 2), "roi": (pnl / staked) if staked > 0 else None,
                "hit_rate": (wins / decided) if decided else None}

    parlays, singles = [r for r in rows if r["is_combo"]], [r for r in rows if not r["is_combo"]]
    return {"AUTOMATIC": block(rows), "ALL": block(rows), "PARLAY_TICKETS": block(parlays), "SINGLE_BETS": block(singles),
            "RECONCILIATION": book_breakdown(conn, track, experiment)["reconciliation"]}


def book_breakdown(conn: sqlite3.Connection, track: str = "REAL_MARKET_PAPER", experiment="ACTIVE") -> dict:
    """The model account read three ways, with the arithmetic that ties them together. Nothing is reset or moved: the account is the combined one (cash $500 + every settled result - open
    stakes), and its parlay tickets and its earlier single bets (moneyline, props) are reported apart so the parlay experiment's own result is visible and the two add up to the whole."""
    rows = query_paper_bets(conn, track=track, experiment=experiment)
    start = _scope(conn, experiment)[2]

    def block(subset: list[dict]) -> dict:
        settled = [r for r in subset if r["result_status"] in ("WIN", "LOSS", "VOID")]
        wins, losses = sum(1 for r in settled if r["result_status"] == "WIN"), sum(1 for r in settled if r["result_status"] == "LOSS")
        staked, pnl = sum(r["stake"] for r in settled), sum(r["profit_loss"] or 0.0 for r in settled)
        open_rows = [r for r in subset if r["result_status"] in ("PENDING", "UNRESOLVED")]
        return {"bets": len(subset), "settled": len(settled), "wins": wins, "losses": losses, "open": len(open_rows), "open_stake": round(sum(r["stake"] for r in open_rows), 2),
                "settled_stake": round(staked, 2), "settled_pnl": round(pnl, 2), "roi": (pnl / staked) if staked > 0 else None, "hit_rate": (wins / (wins + losses)) if (wins + losses) else None}

    parlays = [r for r in rows if r["is_combo"]]
    singles = [r for r in rows if not r["is_combo"]]
    money = [r for r in singles if r.get("market_family") == "MONEYLINE"]
    props = [r for r in singles if r.get("market_family") != "MONEYLINE"]
    out = {"combined": block(rows), "parlay_tickets": block(parlays), "single_bets": block(singles),
           "single_bets_by_kind": {"moneyline": block(money), "props": block(props)}}
    acct = account_state(conn, track, experiment)
    c, p, sb = out["combined"], out["parlay_tickets"], out["single_bets"]
    checks = {"pnl": round(p["settled_pnl"] + sb["settled_pnl"], 2) == c["settled_pnl"], "bets": p["bets"] + sb["bets"] == c["bets"],
              "settled_stake": round(p["settled_stake"] + sb["settled_stake"], 2) == c["settled_stake"], "open_stake": round(p["open_stake"] + sb["open_stake"], 2) == c["open_stake"],
              "cash": round(start + c["settled_pnl"] - c["open_stake"], 2) == acct["available_cash"]}
    out["reconciliation"] = {"starting_bankroll": start, "parlay_pnl": p["settled_pnl"], "single_bets_pnl": sb["settled_pnl"], "combined_pnl": c["settled_pnl"],
                             "open_stakes": c["open_stake"], "available_cash": acct["available_cash"], "checks": checks, "all_agree": all(checks.values()),
                             "sentence": (f"Cash ${acct['available_cash']:,.2f} = ${start:,.0f} start {p['settled_pnl']:+,.2f} parlay tickets {sb['settled_pnl']:+,.2f} single bets "
                                          f"- ${c['open_stake']:,.2f} open")}
    return out


def answer_theoretical_bankroll_question(conn: sqlite3.Connection, track: str, experiment="ACTIVE") -> str:
    """Part 49: the exact owner question, answered in one sentence from
    immutable stored data."""
    s = bankroll_summary(conn, track, experiment)
    if s["bets"] == 0:
        # Production Gap Closure sprint (2026-09-30): this used to say
        # "WAITING FOR SETTLED REAL RECOMMENDATIONS" for EVERY track with
        # zero bets, including DEMO_PAPER and GAME_PARLAY_PAPER -- whose
        # own bets, when they exist, are simulated/single-game demo
        # entries, never real recommendations. Track-specific wording so
        # the demo tracks never imply they're waiting on real activity.
        if track == "REAL_MARKET_PAPER":
            return f"No {track} paper bets have been recorded yet -- WAITING FOR SETTLED REAL RECOMMENDATIONS."
        return f"No {track} paper bets have been recorded yet."
    return (f"${s['current_bankroll']:.2f} (started at ${s['starting_bankroll']:.2f}, "
            f"{s['wins']}-{s['losses']}-{s['voids']} on {s['bets']} bets, "
            f"{s['pending']} still pending, net {'profit' if s['net_profit'] >= 0 else 'loss'} of "
            f"${abs(s['net_profit']):.2f}).")


def _window_stats(rows: list[dict], since_iso: str | None) -> dict:
    settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "VOID")
               and (since_iso is None or (r["settled_at_utc"] or "") >= since_iso)]
    staked = sum(r["stake"] for r in settled)
    pnl = sum(r["profit_loss"] or 0.0 for r in settled)
    wins = sum(1 for r in settled if r["result_status"] == "WIN")
    losses = sum(1 for r in settled if r["result_status"] == "LOSS")
    clv_values = [r["clv"] for r in settled if r.get("clv") is not None]
    decided = wins + losses
    return {
        "bets": len(settled), "wins": wins, "losses": losses,
        "hit_rate": (wins / decided) if decided > 0 else None,
        "net_profit": pnl, "roi": (pnl / staked) if staked > 0 else None,
        "avg_clv": (sum(clv_values) / len(clv_values)) if clv_values else "WAITING",
    }


def windowed_performance(conn: sqlite3.Connection, track: str, now_utc: str | None = None, experiment="ACTIVE") -> dict:
    """Completion sprint Part 47: yesterday / 7-day / 30-day / season
    windows, for operational/daily_model_review.py's daily report to
    read (additively -- this function only reads paper_bets, it never
    writes to or is called by anything in the real prospective ledger).
    Season = all settled bets on this track, no date filter."""
    now_utc = now_utc or _utcnow_iso()
    now_dt = dt.datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    rows = query_paper_bets(conn, track=track, experiment=experiment)
    yesterday = (now_dt - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    d7 = (now_dt - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    d30 = (now_dt - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    summary = bankroll_summary(conn, track, experiment)
    return {
        "track": track, "current_bankroll": summary["current_bankroll"],
        "max_drawdown": summary["max_drawdown"],
        "yesterday": _window_stats(rows, yesterday),
        "last_7_days": _window_stats(rows, d7),
        "last_30_days": _window_stats(rows, d30),
        "season_to_date": _window_stats(rows, None),
    }


# ----------------------------------------------------------------------------------------------------------------- starting a new experiment
MODEL_TRACK = "REAL_MARKET_PAPER"


def experiment_report(conn: sqlite3.Connection, experiment_id: str) -> dict:
    """One experiment read on its own, from its own rows (live, so a ticket that settles later is reflected)."""
    exp = resolve_experiment(conn, experiment_id)
    if exp is None:
        raise InvalidPaperBetError(f"no experiment {experiment_id!r}")
    return {"experiment": experiment_brief(exp), "account": account_state(conn, MODEL_TRACK, exp["experiment_id"]), "summary": bankroll_summary(conn, MODEL_TRACK, exp["experiment_id"]),
            "origins": origin_performance(conn, MODEL_TRACK, exp["experiment_id"]), "breakdowns": performance_breakdowns(conn, MODEL_TRACK, experiment=exp["experiment_id"]),
            "bets": query_paper_bets(conn, track=MODEL_TRACK, experiment=exp["experiment_id"]), "frozen_at_close": json.loads(exp["final_summary_json"]) if exp.get("final_summary_json") else None}


def start_new_experiment(conn: sqlite3.Connection, *, reset_id: str, label: str, reason: str, now_iso: str | None = None, archive_dir: Path | None = None,
                         backup_dir: Path | None = None, policies: dict | None = None) -> dict:
    """Close the ACTIVE experiment and open a new one with a fresh $500 (PAPER_STARTING_BANKROLL), $0 open stakes and $0 P&L. Nothing already stored is deleted or rewritten:
      * the previous experiment's tickets stay in paper_bets under their own experiment_id (they are not copied or moved), and every account / performance / reconciliation read defaults to the
        ACTIVE experiment, so they no longer count; tickets still open stay open IN THE PREVIOUS experiment and settle there without touching the new bankroll;
      * a byte-for-byte copy of the database as it was before the reset, a JSON archive (tickets, results, balance, audit log, policy versions) and a summary frozen into the experiment row are written first;
      * `reset_id` is the idempotency key: applying the same id again changes nothing (no second archive, no second reset), so a restart, a redeploy or a repeated command cannot reset the bankroll again.
    Returns {"status": "RESET" | "ALREADY_APPLIED", ...}."""
    done = conn.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (reset_id,)).fetchone()
    if done:
        return {"status": "ALREADY_APPLIED", **dict(done)}
    if active_experiment(conn) is None:
        raise InvalidPaperBetError("no active experiment to close")
    now_iso = now_iso or _utcnow_iso()
    stamp = now_iso.replace(":", "").replace("-", "").replace(".", "")[:15]
    backup_path = None
    if backup_dir is not None:                    # the database exactly as it is BEFORE anything changes (taken first, outside the write lock: it can take a moment)
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        number_now = active_experiment(conn)["number"]
        backup_path = backup_dir / f"paper_bankroll_experiment_{number_now}_final_{stamp}.db"
        if conn.in_transaction:
            conn.commit()
        dest = sqlite3.connect(backup_path)
        try:
            conn.backup(dest)
        finally:
            dest.close()
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")                # from here the write lock is held: no settlement or ticket can land between reading the old experiment and closing it
    try:
        again = conn.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (reset_id,)).fetchone()
        if again:                                  # a concurrent run applied it first
            conn.rollback()
            return {"status": "ALREADY_APPLIED", **dict(again)}
        prev = active_experiment(conn)
        if prev is None:
            raise InvalidPaperBetError("no active experiment to close")
        report = experiment_report(conn, prev["experiment_id"])
        account = report["account"]
        frozen = {"closed_at_utc": now_iso, "reason": reason, "account": account, "summary": {k: v for k, v in report["summary"].items() if k != "bankroll_history"}, "origins": report["origins"],
                  "open_tickets": account["open_tickets"], "open_stakes": account["open_stakes"]}
        archive_path = None
        if archive_dir is not None:
            archive_dir = Path(archive_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / f"model_book_experiment_{prev['number']}_{stamp}.json"
            ids = [b["paper_bet_id"] for b in query_paper_bets(conn, origin=None, experiment=prev["experiment_id"])]
            audit = [dict(r) for r in conn.execute(f"SELECT * FROM paper_audit_log WHERE paper_bet_id IN ({','.join('?' for _ in ids) or 'NULL'}) ORDER BY id", ids)]
            archive_path.write_text(json.dumps({"experiment": experiment_brief(prev), "frozen_summary": frozen, "tickets": query_paper_bets(conn, origin=None, experiment=prev["experiment_id"]),
                                                "audit_log": audit, "policy_versions": policies or {}, "bankroll_history": report["summary"].get("bankroll_history")}, indent=1, sort_keys=True, default=str))
            archive_path.chmod(0o444)              # an archive is read-only
        number = int(conn.execute("SELECT MAX(number) m FROM paper_experiments").fetchone()["m"]) + 1
        new_id = f"EXP-{number}"
        closed = conn.execute("UPDATE paper_experiments SET status = 'ARCHIVED', ended_at_utc = ?, reason = ?, final_summary_json = ?, archive_path = ? WHERE experiment_id = ? AND status = 'ACTIVE'",
                              (now_iso, reason, json.dumps(frozen, sort_keys=True, default=str), str(archive_path) if archive_path else None, prev["experiment_id"]))
        if closed.rowcount != 1:
            raise InvalidPaperBetError("the active experiment changed while resetting")
        conn.execute("INSERT INTO paper_experiments (experiment_id, number, label, status, starting_bankroll, started_at_utc, reason) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)",
                     (new_id, number, label, PAPER_STARTING_BANKROLL, now_iso, reason))
        conn.execute("INSERT INTO paper_experiment_resets (reset_id, from_experiment_id, to_experiment_id, performed_at_utc, reason, open_tickets_in_previous, previous_available_cash, archive_path, backup_path) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (reset_id, prev["experiment_id"], new_id, now_iso, reason, account["open_tickets"], account["available_cash"], str(archive_path) if archive_path else None,
                      str(backup_path) if backup_path else None))
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"status": "RESET", "reset_id": reset_id, "from_experiment_id": prev["experiment_id"], "to_experiment_id": new_id, "performed_at_utc": now_iso,
            "open_tickets_in_previous": account["open_tickets"], "previous_available_cash": account["available_cash"], "archive_path": str(archive_path) if archive_path else None,
            "backup_path": str(backup_path) if backup_path else None}
