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
SCHEMA_VERSION = 1

PAPER_STARTING_BANKROLL = 500.00
PAPER_BET_STAKE = 10.00  # 2% of the default starting bankroll -- fixed, never dynamic/Kelly

TRACKS = ("REAL_MARKET_PAPER", "DEMO_PAPER")
PRICE_SOURCES = ("LIVE_DRAFTKINGS", "SIMULATED_DEMO")
RESULT_STATES = ("PENDING", "WIN", "LOSS", "VOID", "UNRESOLVED")

ODDS_RANGE_BUCKETS_ORDER = (
    "shorter than -500", "-500 to -400", "-399 to -300", "-299 to -200",
    "-199 to -110", "-109 to +100", "+101 to +200", "+201 or longer",
)


class InvalidPaperBetError(Exception):
    pass


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = get_conn(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    return conn


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
    "stake", "created_at_utc", "event_start_utc",
]


def record_paper_bet(conn: sqlite3.Connection, *, track: str, price_source: str, market_id: str,
                      entry_odds: float, event_id=None, game_date=None, player_id=None,
                      player_name_snapshot=None, team=None, opponent=None, market_family=None,
                      threshold=None, side=None, is_combo: bool = False, top_conviction: bool = False,
                      legs_json: str | None = None, model_probability=None, conservative_probability=None,
                      market_no_vig_probability=None, edge=None, ev=None, confidence=None,
                      model_version=None, prediction_checkpoint=None, stake: float = PAPER_BET_STAKE,
                      created_at_utc: str | None = None, event_start_utc=None,
                      idempotency_key: str | None = None) -> dict:
    """Part 28-30: FIRST ACTIONABLE BET CHECKPOINT entry only -- returns
    {"status": "DUPLICATE", "paper_bet_id": ...} on any later re-call
    with the same idempotency key, never a second $10 stake (Part 29)."""
    if track not in TRACKS:
        raise InvalidPaperBetError(f"unknown track {track!r}")
    if price_source not in PRICE_SOURCES:
        raise InvalidPaperBetError(f"unknown price_source {price_source!r}")
    if track == "REAL_MARKET_PAPER" and price_source != "LIVE_DRAFTKINGS":
        raise InvalidPaperBetError("REAL_MARKET_PAPER bets must be priced with LIVE_DRAFTKINGS -- "
                                    "never mix a simulated price into the real-market track")
    if track == "DEMO_PAPER" and price_source != "SIMULATED_DEMO":
        raise InvalidPaperBetError("DEMO_PAPER bets must be priced with SIMULATED_DEMO -- "
                                    "never mix a real price into the demo track")

    idempotency_key = idempotency_key or compute_paper_idempotency_key(
        track=track, event_id=event_id, participant_id=player_id or team, market_id=market_id,
        threshold=threshold, side=side, price_source=price_source)
    existing = conn.execute("SELECT paper_bet_id FROM paper_bets WHERE idempotency_key = ?",
                             (idempotency_key,)).fetchone()
    if existing:
        return {"status": "DUPLICATE", "paper_bet_id": existing["paper_bet_id"]}

    paper_bet_id = str(uuid.uuid4())
    created_at_utc = created_at_utc or _utcnow_iso()
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
        "created_at_utc": created_at_utc, "event_start_utc": event_start_utc,
    }
    values = [row.get(c) for c in _COLUMNS]
    placeholders = ", ".join("?" for _ in _COLUMNS)
    conn.execute(f"INSERT INTO paper_bets ({', '.join(_COLUMNS)}) VALUES ({placeholders})", values)
    conn.execute("INSERT INTO paper_audit_log (timestamp_utc, paper_bet_id, action) VALUES (?, ?, 'INSERT')",
                 (_utcnow_iso(), paper_bet_id))
    conn.commit()
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


def settle_paper_bet(conn: sqlite3.Connection, paper_bet_id: str, result_status: str, *,
                      closing_odds: float | None = None, closing_captured_at_utc: str | None = None,
                      clv: float | None = None, notes: str | None = None) -> dict:
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
    profit_loss = compute_payout(row["stake"], row["entry_odds"], result_status)
    computed_clv = clv
    if computed_clv is None and closing_odds is not None:
        from pricing import odds_math as pm
        computed_clv = pm.american_to_prob(row["entry_odds"]) - pm.american_to_prob(closing_odds)
    settled_at_utc = _utcnow_iso()
    conn.execute(
        """UPDATE paper_bets SET result_status=?, settled_at_utc=?, profit_loss=?, closing_odds=?,
           closing_captured_at_utc=?, clv=?, notes=? WHERE paper_bet_id=?""",
        (result_status, settled_at_utc, profit_loss, closing_odds, closing_captured_at_utc,
         computed_clv, notes, paper_bet_id))
    conn.execute("INSERT INTO paper_audit_log (timestamp_utc, paper_bet_id, action) VALUES (?, ?, ?)",
                 (settled_at_utc, paper_bet_id, "VOID" if result_status == "VOID" else "SETTLE"))
    conn.commit()
    return dict(conn.execute("SELECT * FROM paper_bets WHERE paper_bet_id = ?", (paper_bet_id,)).fetchone())


def find_unresolved_past_event_bets(conn: sqlite3.Connection, track: str | None = None) -> list[dict]:
    """Part 33's batch-scanner concept: PENDING bets whose event has
    already started (or a nhl.db game_id doesn't yet exist to resolve
    against) become UNRESOLVED rather than silently staying PENDING
    forever with no visible signal that something needs attention --
    this NEVER guesses a WIN/LOSS outcome (Part 43/44)."""
    now_iso = _utcnow_iso()
    clauses = ["result_status = 'PENDING'", "event_start_utc IS NOT NULL", "event_start_utc < ?"]
    params: list = [now_iso]
    if track is not None:
        clauses.append("track = ?")
        params.append(track)
    rows = conn.execute(f"SELECT * FROM paper_bets WHERE {' AND '.join(clauses)}", params).fetchall()
    return [dict(r) for r in rows]


def query_paper_bets(conn: sqlite3.Connection, track: str | None = None, is_combo: bool | None = None,
                      result_status: str | None = None) -> list[dict]:
    clauses, params = [], []
    if track is not None:
        clauses.append("track = ?"); params.append(track)
    if is_combo is not None:
        clauses.append("is_combo = ?"); params.append(int(is_combo))
    if result_status is not None:
        clauses.append("result_status = ?"); params.append(result_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM paper_bets {where} ORDER BY created_at_utc", params).fetchall()
    return [dict(r) for r in rows]


def bankroll_summary(conn: sqlite3.Connection, track: str) -> dict:
    """Parts 34-40, 49: the theoretical bankroll for one track, computed
    ONLY from immutable stored rows -- never recomputed from today's
    current odds (Part 49). Straight and combo bets are tracked
    separately within the same starting bankroll (Part 38's "straight vs
    combo" breakdown) but share one running P&L line per track, since
    Part 26 only requires the THREE top-level tracks to stay separate,
    not a fourth split within a track."""
    rows = query_paper_bets(conn, track=track)
    settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "VOID")]
    settled_ordered = sorted(settled, key=lambda r: r["settled_at_utc"] or "")

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
    current_bankroll = PAPER_STARTING_BANKROLL + net_profit

    # Bankroll history / peak / trough / drawdown / streaks -- replay
    # settled bets in settlement order (Part 37).
    running = PAPER_STARTING_BANKROLL
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
            "cumulative_pnl": running - PAPER_STARTING_BANKROLL,
            "cumulative_roi": ((running - PAPER_STARTING_BANKROLL) / run_staked) if run_staked > 0 else None,
            "cumulative_hit_rate": (run_wins / run_decided) if run_decided > 0 else None,
        })

    return {
        "track": track, "starting_bankroll": PAPER_STARTING_BANKROLL, "current_bankroll": current_bankroll,
        "peak_bankroll": peak, "lowest_bankroll": min(h["bankroll"] for h in history),
        "total_staked": total_staked, "total_return": total_return, "net_profit": net_profit, "roi": roi,
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


def performance_breakdowns(conn: sqlite3.Connection, track: str) -> dict:
    """Part 38: results by market family, confidence, edge bucket, odds
    range, Top Conviction status, straight vs combo."""
    rows = query_paper_bets(conn, track=track)

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


def answer_theoretical_bankroll_question(conn: sqlite3.Connection, track: str) -> str:
    """Part 49: the exact owner question, answered in one sentence from
    immutable stored data."""
    s = bankroll_summary(conn, track)
    if s["bets"] == 0:
        return f"No {track} paper bets have been recorded yet -- WAITING FOR SETTLED REAL RECOMMENDATIONS."
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


def windowed_performance(conn: sqlite3.Connection, track: str, now_utc: str | None = None) -> dict:
    """Completion sprint Part 47: yesterday / 7-day / 30-day / season
    windows, for operational/daily_model_review.py's daily report to
    read (additively -- this function only reads paper_bets, it never
    writes to or is called by anything in the real prospective ledger).
    Season = all settled bets on this track, no date filter."""
    now_utc = now_utc or _utcnow_iso()
    now_dt = dt.datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    rows = query_paper_bets(conn, track=track)
    yesterday = (now_dt - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    d7 = (now_dt - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    d30 = (now_dt - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    summary = bankroll_summary(conn, track)
    return {
        "track": track, "current_bankroll": summary["current_bankroll"],
        "max_drawdown": summary["max_drawdown"],
        "yesterday": _window_stats(rows, yesterday),
        "last_7_days": _window_stats(rows, d7),
        "last_30_days": _window_stats(rows, d30),
        "season_to_date": _window_stats(rows, None),
    }
