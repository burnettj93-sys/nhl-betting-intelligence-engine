"""
Personal bet logs: friends keep their own paper bets under a code they choose, completely apart from the $500 model book.

Separation is structural, not a filter: personal bets live in their OWN database file (`personal_logs.db`), never in
`paper_bankroll.db`. Nothing in this module opens the model ledger for writing (the one exception is the explicit,
audited legacy migration, which only READS it), and nothing in the model code reads this file, so a personal bet cannot
change the model's cash, exposure, daily slots, P&L, win rate or performance evaluation. tests/test_personal_logs.py
proves it by hashing the model ledger before and after adding and settling bets in two logs.

What a code is -- and is not. A code is a NAME for your log. It is not a password and it does not make a log private: the engine's
published data lives in a public repository, so the bets in a log are readable by anyone who looks, filed under a one-way hash of the code
(not the code itself) and a display name. Do not put anything personal in a log or its name. Paper bets only; no money moves.

Who may WRITE. Knowing a code does not let anyone add to a log. Each log has a separate WRITE KEY (generated when the log is created, shown once);
only its public half is stored, and every order must carry a valid signature made with it (operational/log_signing.py). The queue is public, but
a signature in it cannot be reused to write anything else, so the public queue leaks nothing that grants access.

Flow (same durable queue as the other hosted actions, see operational/manual_orders.py):
  hosted page -> PERSONAL_BET / PERSONAL_LOG_CREATE order (a GitHub issue created with the app's own write credential)
  -> engine revalidates against current fresh prices -> one row in the log -> settlement by the same resolver the model
  book uses -> published `personal_logs` section -> "My Bets" page.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import sqlite3
from pathlib import Path

from operational import eastern_time as et
from operational import log_signing
from operational import state_paths

DB_NAME = "personal_logs.db"
SCHEMA = 1
LABEL = "personal-bet"
TYPE_BET, TYPE_CREATE = "PERSONAL_BET", "PERSONAL_LOG_CREATE"
HASH_SALT = b"nhl-engine/personal-log/v1"
MIN_CODE_LEN, MAX_CODE_LEN = 8, 64
MAX_NAME_LEN = 30
MIN_STAKE, MAX_STAKE = 1.0, 1000.0
DEFAULT_STAKE = 10.0
MAX_BET_ORDERS_PER_LOG_PER_DAY = 40
MAX_NEW_LOGS_PER_DAY = 25
UNCLAIMED_LEGACY = "legacy-unclaimed"

RECORDED, ALREADY, NEEDS_ACCEPTANCE, REJECTED, CREATED = "RECORDED", "ALREADY_RECORDED", "NEEDS_ACCEPTANCE", "REJECTED", "CREATED"
ORIGIN_LABEL = "MANUALLY_ADDED"

_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .'_-]*")
_BLOCKED_IN_TEXT = re.compile(r"(yahoo|secret|passw|token|api[_-]?key|bearer|credential|authorization|/users/|/home/)", re.I)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    log_hash TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    created_by_order TEXT,
    note TEXT,
    write_pub TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    log_hash TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    bet_id TEXT,
    source TEXT,
    request_json TEXT,
    detail_json TEXT,
    received_at_utc TEXT,
    processed_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bets (
    bet_id TEXT PRIMARY KEY,
    log_hash TEXT NOT NULL REFERENCES logs(log_hash),
    order_id TEXT UNIQUE,
    fingerprint TEXT NOT NULL,
    legs_json TEXT NOT NULL,
    entry_odds REAL NOT NULL,
    model_probability REAL,
    ev REAL,
    stake REAL NOT NULL CHECK (stake > 0),
    created_at_utc TEXT NOT NULL,
    event_start_utc TEXT,
    origin TEXT NOT NULL DEFAULT 'MANUALLY_ADDED',
    provenance_json TEXT,
    result_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (result_status IN ('PENDING','WIN','LOSS','VOID','UNRESOLVED')),
    settled_at_utc TEXT,
    profit_loss REAL,
    settlement_json TEXT,
    notes TEXT,
    UNIQUE (log_hash, fingerprint)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc TEXT NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS migrations (
    source_ref TEXT PRIMARY KEY,
    bet_id TEXT NOT NULL,
    migrated_at_utc TEXT NOT NULL,
    detail_json TEXT
);
CREATE TRIGGER IF NOT EXISTS bets_frozen_entry
BEFORE UPDATE ON bets
FOR EACH ROW
WHEN NEW.legs_json IS NOT OLD.legs_json OR NEW.entry_odds IS NOT OLD.entry_odds OR NEW.stake IS NOT OLD.stake
  OR NEW.created_at_utc IS NOT OLD.created_at_utc OR NEW.origin IS NOT OLD.origin OR NEW.provenance_json IS NOT OLD.provenance_json
  OR NEW.fingerprint IS NOT OLD.fingerprint OR NEW.model_probability IS NOT OLD.model_probability
BEGIN
    SELECT RAISE(ABORT, 'personal bets: the entry details are frozen');
END;
CREATE TRIGGER IF NOT EXISTS bets_log_move
BEFORE UPDATE OF log_hash ON bets
FOR EACH ROW
WHEN NEW.log_hash IS NOT OLD.log_hash AND OLD.log_hash <> 'legacy-unclaimed'
BEGIN
    SELECT RAISE(ABORT, 'personal bets: only an unclaimed legacy bet can change log');
END;
CREATE TRIGGER IF NOT EXISTS bets_no_delete
BEFORE DELETE ON bets
BEGIN
    SELECT RAISE(ABORT, 'personal bets are never deleted');
END;
"""


# ------------------------------------------------------------------ codes and names ----

def normalize_code(code) -> str:
    return re.sub(r"\s+", " ", str(code or "")).strip().lower()


def validate_code(code) -> str | None:
    """A reason the code cannot be used, or None. Length and variety only: a code is a name, not a password."""
    c = normalize_code(code)
    if len(c) < MIN_CODE_LEN:
        return f"Use at least {MIN_CODE_LEN} characters."
    if len(c) > MAX_CODE_LEN:
        return f"Use at most {MAX_CODE_LEN} characters."
    if len(set(c)) < 5:
        return "Use a code with more variety (for example three words and a number joined by dashes)."
    return None


def code_hash(code) -> str:
    """One-way, deliberately slow hash of the normalised code. This is what is stored and published, never the code."""
    return hashlib.scrypt(normalize_code(code).encode(), salt=HASH_SALT, n=2 ** 14, r=8, p=1, dklen=16).hex()


_WORDS = ("otter", "maple", "puck", "crease", "blue", "line", "slap", "net", "ice", "goal", "save", "check", "wrist", "tape", "stick", "glove",
          "faceoff", "zamboni", "rink", "shift", "hat", "trick", "assist", "power", "play", "kill", "icing", "boards", "pond", "skate")


def suggest_code() -> str:
    return "-".join(secrets.choice(_WORDS) for _ in range(3)) + f"-{secrets.randbelow(9000) + 1000}"


def validate_name(name) -> tuple[str | None, str | None]:
    """(clean name, error)."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not n:
        return None, "Give the log a short display name."
    if len(n) > MAX_NAME_LEN:
        return None, f"Use at most {MAX_NAME_LEN} characters."
    if not _NAME_RE.fullmatch(n) or _BLOCKED_IN_TEXT.search(n):
        return None, "Use letters, numbers, spaces, and . ' _ - only (no personal details)."
    return n, None


# ------------------------------------------------------------------ storage ----

def _stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else state_paths.path(DB_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    if "write_pub" not in {r["name"] for r in conn.execute("PRAGMA table_info(logs)")}:      # databases created before write keys existed
        conn.execute("ALTER TABLE logs ADD COLUMN write_pub TEXT")
    return conn


def _audit(conn, kind: str, ref, detail=None, at: str | None = None) -> None:
    conn.execute("INSERT INTO audit (at_utc, kind, ref, detail_json) VALUES (?,?,?,?)",
                 (at or _stamp(dt.datetime.now(dt.timezone.utc)), kind, ref, json.dumps(detail, sort_keys=True, default=str) if detail is not None else None))


def stored_order(conn, order_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def _store(conn, order_id, kind, log_hash, status, reason, bet_id, source, request, detail, received_at, processed_at) -> dict:
    conn.execute("INSERT OR IGNORE INTO orders (order_id, log_hash, kind, status, reason, bet_id, source, request_json, detail_json, received_at_utc, processed_at_utc) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (order_id, log_hash, kind, status, reason, bet_id, source, json.dumps(request, sort_keys=True, default=str)[:4000],
                  json.dumps(detail, sort_keys=True, default=str) if detail is not None else None, received_at, processed_at))
    return stored_order(conn, order_id)


def log_row(conn, log_hash: str) -> dict | None:
    r = conn.execute("SELECT * FROM logs WHERE log_hash = ?", (log_hash,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ orders ----

def _order_shape(raw) -> tuple[dict | None, str | None, str | None]:
    """(order, order_id, error)."""
    try:
        order = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except json.JSONDecodeError:
        return None, None, "The order is not valid JSON."
    if not isinstance(order, dict) or order.get("schema") != SCHEMA or order.get("type") not in (TYPE_BET, TYPE_CREATE):
        return None, None, "Not a personal-log order of the supported schema."
    oid = order.get("order_id")
    if not isinstance(oid, str) or not _ID_RE.fullmatch(oid):
        return None, None, "order_id is missing or malformed."
    log = order.get("log")
    if not isinstance(log, dict) or not isinstance(log.get("hash"), str) or not re.fullmatch(r"[0-9a-f]{32}", log["hash"]):
        return None, oid, "The destination log is missing or malformed."
    return order, oid, None


def _creation(log: dict) -> tuple[dict | None, str | None]:
    c = log.get("create")
    if c is None:
        return None, None
    if not isinstance(c, dict) or not isinstance(c.get("creation_id"), str) or not _ID_RE.fullmatch(c["creation_id"]):
        return None, "The log creation details are malformed."
    name, err = validate_name(c.get("display_name"))
    if err:
        return None, err
    if not isinstance(c.get("write_pub"), str) or not re.fullmatch(r"[0-9a-f]{64}", c["write_pub"]):
        return None, "The log creation needs the log's public write key."
    return {"creation_id": c["creation_id"], "display_name": name, "write_pub": c["write_pub"]}, None


def _same_day(stamp: str, et_date: str) -> bool:
    return et.eastern_today(_parse(stamp)) == et_date


def _ensure_log(conn, log_hash: str, creation: dict | None, now: dt.datetime) -> tuple[str, str | None]:
    """('OK'|'CREATED'|'REJECTED', reason). A creation request never takes over a log somebody else created."""
    existing = log_row(conn, log_hash)
    if existing:
        if creation and existing["created_by_order"] != creation["creation_id"]:
            return REJECTED, "CODE_IN_USE: that code already opens another log. Choose a different code."
        return "OK", None
    if not creation:
        return REJECTED, "LOG_NOT_FOUND: no log uses that code yet. Create it first."
    today = et.eastern_today(now)
    made = sum(1 for r in conn.execute("SELECT created_at_utc FROM logs WHERE created_by_order IS NOT NULL").fetchall() if _same_day(r["created_at_utc"], today))
    if made >= MAX_NEW_LOGS_PER_DAY:
        return REJECTED, "RATE_LIMIT: too many new logs today. Try again tomorrow."
    conn.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, created_by_order, write_pub) VALUES (?,?,?,?,?)",
                 (log_hash, creation["display_name"], _stamp(now), creation["creation_id"], creation["write_pub"]))
    _audit(conn, "LOG_CREATED", log_hash, {"display_name": creation["display_name"]}, _stamp(now))
    return CREATED, None


def process_order(conn, raw, *, current_legs=None, now: dt.datetime, source: str, received_at: str | None = None) -> dict:
    """Process one personal-log order to a stored answer. Safe to repeat: the first call decides, later calls read it back."""
    received_at = received_at or _stamp(now)
    done = _stamp(now)
    order, oid, error = _order_shape(raw)
    if order is None:
        oid = oid if oid else "INVALID-" + re.sub(r"\W", "", source)[:40]
        return stored_order(conn, oid) or _store(conn, oid, "INVALID", None, REJECTED, error, None, source, {"raw": str(raw)[:1500]}, None, received_at, done)
    prior = stored_order(conn, oid)
    if prior:
        return prior
    log_hash = order["log"]["hash"]
    creation, cerr = _creation(order["log"])
    if cerr:
        return _store(conn, oid, order["type"], log_hash, REJECTED, cerr, None, source, order, None, received_at, done)

    # Who may write: the signature must verify against the log's stored public key (or, for the creating order, the public key it carries).
    existing = log_row(conn, log_hash)
    pub = existing["write_pub"] if existing else (creation or {}).get("write_pub")
    if not existing and not creation:
        return _store(conn, oid, order["type"], log_hash, REJECTED, "LOG_NOT_FOUND: no log uses that code yet. Create it first.", None, source, order, None, received_at, done)
    if existing and not pub:
        return _store(conn, oid, order["type"], log_hash, REJECTED, "NO_WRITE_KEY: this log has no write key, so nothing can be added to it.", None, source, order, None, received_at, done)
    if not log_signing.verify(order, pub):
        return _store(conn, oid, order["type"], log_hash, REJECTED,
                      "BAD_SIGNATURE: this order was not signed with this log's write key, so it was not applied.", None, source, order, None, received_at, done)

    conn.execute("BEGIN IMMEDIATE")
    try:
        if order["type"] == TYPE_CREATE:
            if not creation:
                res = _store(conn, oid, TYPE_CREATE, log_hash, REJECTED, "A log creation order needs the creation details.", None, source, order, None, received_at, done)
            else:
                state, why = _ensure_log(conn, log_hash, creation, now)
                res = _store(conn, oid, TYPE_CREATE, log_hash, REJECTED if state == REJECTED else CREATED, why, None, source, order, None, received_at, done)
        else:
            res = _process_bet(conn, order, oid, log_hash, creation, current_legs or [], now, source, received_at)
        conn.execute("COMMIT")
        return res
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _process_bet(conn, order, oid, log_hash, creation, current_legs, now, source, received_at) -> dict:
    from operational import manual_orders
    done = _stamp(now)

    def reject(reason, detail=None, status=REJECTED, bet_id=None):
        return _store(conn, oid, TYPE_BET, log_hash, status, reason, bet_id, source, order, detail, received_at, done)

    accepted = order.get("accepted")
    err = manual_orders.check_accepted(accepted)
    stake = None
    if err is None:
        stake = accepted.get("stake", DEFAULT_STAKE)
        if isinstance(stake, bool) or not isinstance(stake, (int, float)) or not (MIN_STAKE <= float(stake) <= MAX_STAKE):
            err = f"The stake must be between ${MIN_STAKE:.0f} and ${MAX_STAKE:.0f}."
        else:
            stake = round(float(stake), 2)
    if err:
        return reject(err)
    state, why = _ensure_log(conn, log_hash, creation, now)
    if state == REJECTED:
        return reject(why)
    today = et.eastern_today(now)
    sent = [r["processed_at_utc"] for r in conn.execute("SELECT processed_at_utc FROM orders WHERE log_hash = ? AND kind = ? AND status IN (?,?,?)",
                                                        (log_hash, TYPE_BET, RECORDED, NEEDS_ACCEPTANCE, REJECTED)).fetchall()]
    if sum(1 for s in sent if _same_day(s, today)) >= MAX_BET_ORDERS_PER_LOG_PER_DAY:
        return reject("RATE_LIMIT: this log reached its daily limit of orders.")

    rv = manual_orders.revalidate(accepted, current_legs, now)
    if rv["status"] in ("UNAVAILABLE", "NO_LONGER_QUALIFIES"):
        return reject(rv["reason"], manual_orders.current_details(rv) or None)
    if rv["status"] == "CHANGED":
        return reject("Prices or the hit chance changed since you looked. Nothing was recorded; review the new details and add it again.",
                      manual_orders.current_details(rv), NEEDS_ACCEPTANCE)

    combo = rv["combo"]
    from operational import daily_tickets
    from operational import paper_bankroll as pb
    identities = [f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}:{l.side}" for l in combo.legs]
    identities.sort()  # order-independent identity of the leg set (not a training or eligibility ordering)
    fingerprint = hashlib.sha256(f"{log_hash}|{today}|{'|'.join(identities)}".encode()).hexdigest()[:20]
    dup = conn.execute("SELECT bet_id FROM bets WHERE log_hash = ? AND fingerprint = ?", (log_hash, fingerprint)).fetchone()
    if dup:
        return reject("This exact bet is already in this log (added earlier today).", None, ALREADY, dup["bet_id"])

    bet_id = "P" + hashlib.sha256(f"{log_hash}|{oid}".encode()).hexdigest()[:14].upper()
    frozen = []
    for l in combo.legs:
        f = pb._freeze_leg(l)
        f["code_version"] = daily_tickets.code_version()
        f["haircut_margin"] = getattr(combo, "leg_probability_margin", None)
        frozen.append(f)
    provenance = {"order_id": oid, "source": source, "received_at_utc": received_at, "revalidated_at_utc": done,
                  "page_generated_at_utc": order.get("page_generated_at_utc"), "option_id": order.get("option_id"),
                  "accepted": {"legs": accepted["legs"], "combined_american": accepted.get("combined_american"), "hit_probability": accepted.get("hit_probability")},
                  "price_basis": "SPORTSBOOK_QUOTE" if len(combo.legs) == 1 else "ESTIMATED_PRODUCT_OF_LEG_PRICES",
                  "jurisdiction_note": "Prices are DraftKings US-feed quotes (The Odds API); not verified against DraftKings Ontario."}
    conn.execute(
        "INSERT INTO bets (bet_id, log_hash, order_id, fingerprint, legs_json, entry_odds, model_probability, ev, stake, created_at_utc, event_start_utc, origin, provenance_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bet_id, log_hash, oid, fingerprint, json.dumps(frozen), combo.estimated_combo_price, combo.joint_probability, getattr(combo, "ev_estimated", None),
         stake, done, daily_tickets._earliest_start(combo), ORIGIN_LABEL, json.dumps(provenance, sort_keys=True)))
    _audit(conn, "BET_ADDED", bet_id, {"log": log_hash, "order": oid, "stake": stake}, done)
    return reject(None, None, RECORDED, bet_id)


# ------------------------------------------------------------------ settlement ----

def _decimal(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def settle_open(conn, nhl_conn, now: dt.datetime) -> dict:
    """Settles started personal bets with the SAME resolver and rules as the model book; a bet whose game is not final stays open."""
    from operational import paper_bet_settlement_driver as drv
    summary = {"scanned": 0, "settled": 0, "still_open": 0}
    rows = conn.execute("SELECT * FROM bets WHERE result_status IN ('PENDING','UNRESOLVED') ORDER BY created_at_utc").fetchall()
    for r in rows:
        if r["event_start_utc"] and _parse(r["event_start_utc"]) > now:
            continue
        summary["scanned"] += 1
        result = drv.resolve_combo_bet(nhl_conn, {"legs_json": r["legs_json"], "is_combo": 1})
        outcome = drv.terminal_outcome(result)
        if outcome is None:
            summary["still_open"] += 1
            continue
        status = outcome["status"]
        if status == "UNRESOLVED":
            if r["result_status"] != "UNRESOLVED":
                conn.execute("UPDATE bets SET result_status='UNRESOLVED', notes=?, settlement_json=? WHERE bet_id=?", (outcome["notes"], outcome["detail_json"], r["bet_id"]))
            summary["still_open"] += 1
            continue
        dec = _decimal(outcome["settled_odds"] if outcome.get("settled_odds") is not None else r["entry_odds"])
        pnl = round(r["stake"] * (dec - 1.0), 2) if status == "WIN" else (-r["stake"] if status == "LOSS" else 0.0)
        conn.execute("UPDATE bets SET result_status=?, profit_loss=?, settled_at_utc=?, notes=?, settlement_json=? WHERE bet_id=?",
                     (status, pnl, _stamp(now), outcome["notes"], outcome["detail_json"], r["bet_id"]))
        _audit(conn, "BET_SETTLED", r["bet_id"], {"status": status, "profit_loss": pnl}, _stamp(now))
        summary["settled"] += 1
    return summary


# ------------------------------------------------------------------ published view ----

def summarize(bets: list[dict]) -> dict:
    wins = sum(1 for b in bets if b["result_status"] == "WIN")
    losses = sum(1 for b in bets if b["result_status"] == "LOSS")
    voids = sum(1 for b in bets if b["result_status"] == "VOID")
    open_ = [b for b in bets if b["result_status"] in ("PENDING", "UNRESOLVED")]
    settled = [b for b in bets if b["result_status"] in ("WIN", "LOSS", "VOID")]
    pnl = round(sum(b["profit_loss"] or 0.0 for b in settled), 2)
    risked = round(sum(b["stake"] for b in settled if b["result_status"] != "VOID"), 2)
    return {"bets": len(bets), "open": len(open_), "open_stake": round(sum(b["stake"] for b in open_), 2), "settled": len(settled),
            "wins": wins, "losses": losses, "voids": voids, "settled_pnl": pnl, "staked_settled": risked,
            "win_rate": round(wins / (wins + losses), 4) if wins + losses else None,
            "roi": round(pnl / risked, 4) if risked else None}


def section(conn, now: dt.datetime) -> dict:
    """The published `personal_logs` document: every log keyed by the hash of its code (never the code), newest bets first."""
    from operational import daily_tickets
    logs = {}
    for lg in conn.execute("SELECT * FROM logs WHERE log_hash <> ? ORDER BY created_at_utc", (UNCLAIMED_LEGACY,)).fetchall():
        bets = [dict(b) for b in conn.execute("SELECT * FROM bets WHERE log_hash = ? ORDER BY created_at_utc DESC, bet_id", (lg["log_hash"],)).fetchall()]
        cards = []
        for b in bets:
            c = daily_tickets.ticket_from_row({**b, "paper_bet_id": b["bet_id"]}, now)
            c["ticket_id"], c["origin"] = b["bet_id"], ORIGIN_LABEL
            cards.append(c)
        orders = [dict(r) for r in conn.execute(
            "SELECT order_id, kind, status, reason, bet_id, processed_at_utc FROM orders WHERE log_hash = ? ORDER BY processed_at_utc DESC, order_id LIMIT 25",
            (lg["log_hash"],)).fetchall()]
        logs[lg["log_hash"]] = {"display_name": lg["display_name"], "created_at_utc": lg["created_at_utc"], "write_pub": lg["write_pub"],
                                "summary": summarize(bets), "bets": cards, "orders": orders}
    return {"schema": SCHEMA, "generated_at_utc": _stamp(now), "logs": logs,
            "rules": {"stake_min": MIN_STAKE, "stake_max": MAX_STAKE, "stake_default": DEFAULT_STAKE, "code_min_length": MIN_CODE_LEN}}


def unclaimed_legacy(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT bet_id, created_at_utc, result_status, profit_loss FROM bets WHERE log_hash = ?", (UNCLAIMED_LEGACY,)).fetchall()]


# ------------------------------------------------------------------ legacy migration ----

def migrate_legacy_manual(conn, bankroll_conn, now: dt.datetime) -> dict:
    """Copies every MANUALLY_ADDED row of the model ledger into the personal database (log 'legacy-unclaimed', claimable by its owner with
    `claim_legacy`), exactly once per source row, with the original record left untouched in the ledger. READS the ledger only."""
    rows = bankroll_conn.execute("SELECT * FROM paper_bets WHERE origin = 'MANUALLY_ADDED' ORDER BY created_at_utc").fetchall()
    moved = []
    if not rows:
        return {"migrated": 0, "bets": []}
    if not log_row(conn, UNCLAIMED_LEGACY):
        conn.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, created_by_order, note) VALUES (?,?,?,?,?)",
                     (UNCLAIMED_LEGACY, "Unclaimed earlier manual tickets", _stamp(now), None,
                      "Tickets added by hand before personal logs existed, taken out of the model book."))
    for r in rows:
        ref = f"paper_bets:{r['paper_bet_id']}"
        if conn.execute("SELECT 1 FROM migrations WHERE source_ref = ?", (ref,)).fetchone():
            continue
        bet_id = "L" + r["paper_bet_id"]
        prov = json.loads(r["provenance_json"]) if r["provenance_json"] else {}
        prov["migrated_from"] = {"ledger": "paper_bankroll.db", "paper_bet_id": r["paper_bet_id"], "track": r["track"], "migrated_at_utc": _stamp(now)}
        legs = json.loads(r["legs_json"] or "[]")
        identities = sorted(f"{l['game_id']}:{l['participant_id']}:{l['market_family']}:{l['threshold']}:{l.get('side')}" for l in legs)
        fp = hashlib.sha256(f"{UNCLAIMED_LEGACY}|{r['paper_bet_id']}|{'|'.join(identities)}".encode()).hexdigest()[:20]
        conn.execute(
            "INSERT INTO bets (bet_id, log_hash, order_id, fingerprint, legs_json, entry_odds, model_probability, ev, stake, created_at_utc, event_start_utc, origin, "
            "provenance_json, result_status, settled_at_utc, profit_loss, settlement_json, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bet_id, UNCLAIMED_LEGACY, None, fp, r["legs_json"], r["entry_odds"], r["model_probability"], r["ev"], r["stake"], r["created_at_utc"], r["event_start_utc"],
             ORIGIN_LABEL, json.dumps(prov, sort_keys=True), r["result_status"], r["settled_at_utc"], r["profit_loss"], r["settlement_json"], r["notes"]))
        conn.execute("INSERT INTO migrations (source_ref, bet_id, migrated_at_utc, detail_json) VALUES (?,?,?,?)",
                     (ref, bet_id, _stamp(now), json.dumps({"result_status": r["result_status"], "profit_loss": r["profit_loss"]})))
        _audit(conn, "LEGACY_MIGRATED", bet_id, {"source": ref}, _stamp(now))
        moved.append(bet_id)
    return {"migrated": len(moved), "bets": moved}


def claim_legacy(conn, code, display_name, now: dt.datetime, write_key: str | None = None) -> dict:
    """Moves the unclaimed legacy bets into the log opened by `code` (creating it with `display_name` and the public half of `write_key` if it does not
    exist). Local, owner-run. For an existing log the write key must match the stored public key."""
    err = validate_code(code)
    name, nerr = validate_name(display_name)
    if err or nerr:
        return {"status": "REJECTED", "reason": err or nerr}
    h = code_hash(code)
    existing = log_row(conn, h)
    if existing is None:
        if not log_signing.valid_key_shape(write_key):
            return {"status": "REJECTED", "reason": "A new log needs a write key (generate one on My Bets, or leave it blank here to have one generated)."}
        conn.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, created_by_order, note, write_pub) VALUES (?,?,?,?,?,?)",
                     (h, name, _stamp(now), "legacy-claim", "Created by the owner to claim earlier manual tickets.", log_signing.public_key_hex(write_key, h)))
    elif not (log_signing.valid_key_shape(write_key) and log_signing.public_key_hex(write_key, h) == existing["write_pub"]):
        return {"status": "REJECTED", "reason": "That log exists and the write key does not match it."}
    pending = unclaimed_legacy(conn)
    for b in pending:
        conn.execute("UPDATE bets SET log_hash = ? WHERE bet_id = ?", (h, b["bet_id"]))
        _audit(conn, "LEGACY_CLAIMED", b["bet_id"], {"log": h}, _stamp(now))
    return {"status": "CLAIMED", "moved": len(pending), "log_hash": h}


if __name__ == "__main__":
    import getpass
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "claim-legacy":
        code = getpass.getpass("Log code to claim the earlier tickets into: ")
        name = input("Display name for the log: ")
        key = getpass.getpass("Write key (press Enter to generate a new one): ").strip() or None
        generated = None
        if key is None:
            key = generated = log_signing.new_write_key()
        res = claim_legacy(connect(), code, name, dt.datetime.now(dt.timezone.utc), key)
        print(res)
        if generated and res.get("status") == "CLAIMED":
            print(f"Your write key (shown once; store it): {generated}")
    elif cmd == "migrate-legacy":
        from operational import paper_bankroll as pb
        bk = pb.init_db()
        try:
            print(json.dumps(migrate_legacy_manual(connect(), bk, dt.datetime.now(dt.timezone.utc)), indent=1))
        finally:
            bk.close()
    else:
        print("usage: python3 -m operational.personal_logs migrate-legacy | claim-legacy")
