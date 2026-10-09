"""
Settlement audit: re-derive every recorded ticket's result from the NHL's own box scores, WITHOUT the engine's resolver or its nhl.db, and compare with
what the ledgers say. Covers the model book (operational/paper_bankroll.db) and every personal log (operational/runtime/personal_logs.db) separately.

For each bet and leg it reports: official game state, the leg's actual value, whether it hit, the ticket outcome and profit/loss recomputed from the
stated odds and stake, and whether each of those equals the stored value. The same fetch also catches a CORRECTION (the official number now differs from
the value saved at settlement) and an unsettled bet whose game is already final (a backlog).

Public NHL API only, read-only, no purchase. Run: python3 deploy/audit_settlement.py --out docs/validation/settlement_audit.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BOX = "https://api-web.nhle.com/v1/gamecenter/{}/boxscore"
PLAYER_STAT = {"PLAYER_SOG": "sog", "PLAYER_SOG_ALTERNATE": "sog", "PLAYER_POINTS": "points", "PLAYER_GOALS": "goals"}
_cache: dict[str, dict] = {}


def boxscore(game_id) -> dict:
    gid = str(game_id)
    if gid not in _cache:
        import requests   # carries its own CA bundle; the python.org build's urllib has none on this machine
        r = requests.get(BOX.format(gid), headers={"User-Agent": "nhl-engine-settlement-audit"}, timeout=30)
        r.raise_for_status()
        _cache[gid] = r.json()
    return _cache[gid]


def _players(box: dict):
    for side in ("homeTeam", "awayTeam"):
        for grp in ("forwards", "defense", "goalies"):
            for p in box["playerByGameStats"][side][grp]:
                yield p


def official_leg(leg: dict, box: dict) -> dict:
    """{'state', 'actual', 'hit', 'status'} straight from the box score. status: RESOLVED | NOT_FINAL | DID_NOT_PLAY | UNSUPPORTED."""
    state = box.get("gameState")
    if state not in ("FINAL", "OFF"):
        return {"state": state, "status": "NOT_FINAL", "actual": None, "hit": None}
    fam = leg.get("market_family")
    if fam == "MONEYLINE":
        h, a = box["homeTeam"], box["awayTeam"]
        winner = h["abbrev"] if h["score"] > a["score"] else a["abbrev"]
        return {"state": state, "status": "RESOLVED", "actual": winner, "hit": winner == (leg.get("team") or leg.get("side_team"))}
    pid = int(leg["participant_id"])
    rows = [p for p in _players(box) if p["playerId"] == pid]
    if fam in PLAYER_STAT:
        if not rows:
            return {"state": state, "status": "DID_NOT_PLAY", "actual": None, "hit": None}
        v = rows[0][PLAYER_STAT[fam]]
        return {"state": state, "status": "RESOLVED", "actual": v, "hit": v >= int(leg["threshold"])}
    if fam == "GOALIE_SAVES":
        g = [p for p in rows if "saves" in p]
        if not g or not g[0].get("toi") or g[0]["toi"] in ("00:00", "0:00"):
            return {"state": state, "status": "DID_NOT_PLAY", "actual": None, "hit": None}
        return {"state": state, "status": "RESOLVED", "actual": g[0]["saves"], "hit": g[0]["saves"] >= int(leg["threshold"])}
    return {"state": state, "status": "UNSUPPORTED", "actual": None, "hit": None}


def ticket_outcome(legs: list[dict], stake: float, decimal: float) -> tuple[str, float | None]:
    """Stated convention (docs/PAPER_SETTLEMENT_RULES.md): any lost leg loses; all won pays stake*(decimal-1); a not-played leg is UNRESOLVED
    while the void rule is unverified; anything not final is PENDING."""
    if any(l["status"] == "RESOLVED" and l["hit"] is False for l in legs):
        return "LOSS", -stake
    if any(l["status"] == "NOT_FINAL" for l in legs):
        return "PENDING", None
    if any(l["status"] in ("DID_NOT_PLAY", "UNSUPPORTED") for l in legs):
        return "UNRESOLVED", None
    return "WIN", round(stake * (decimal - 1.0), 2)


def decimal_of(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def audit_row(book: str, row: dict) -> dict:
    legs = json.loads(row["legs_json"]) if row.get("legs_json") else []
    if not legs:       # a single moneyline bet in the model ledger carries its fields on the row
        legs = [{"market_family": row["market_family"], "team": row["team"], "game_id": row["event_id"], "threshold": row.get("threshold"),
                 "participant_id": row.get("player_id")}]
    checked, problems = [], []
    stored_legs = (json.loads(row["settlement_json"]).get("leg_results") if row.get("settlement_json") and "leg_results" in (row["settlement_json"] or "") else None) or []
    for i, leg in enumerate(legs):
        off = official_leg(leg, boxscore(leg["game_id"]))
        stored = stored_legs[i] if i < len(stored_legs) else None
        if stored and stored.get("status") == "RESOLVED" and off["status"] == "RESOLVED":
            if stored.get("actual_value") != off["actual"]:
                problems.append(f"leg {i + 1}: saved actual {stored.get('actual_value')} but the box score now says {off['actual']} (correction?)")
            if stored.get("outcome_hit") != off["hit"]:
                problems.append(f"leg {i + 1}: saved hit={stored.get('outcome_hit')} but official hit={off['hit']}")
        checked.append({"leg": f"{leg.get('participant_name') or leg.get('team')} {leg['market_family']} {leg.get('threshold') or ''}".strip(), **off})
    # entry_odds is the ticket's American price (the product of the legs' prices for a parlay), the figure the payout was computed from.
    dec = decimal_of(float(row["entry_odds"]))
    expected, pnl = ticket_outcome(checked, float(row["stake"]), dec)
    stored_status, stored_pnl = row["result_status"], row.get("profit_loss")
    if stored_status != expected:
        problems.append(f"status stored {stored_status} but the box scores give {expected}")
    elif pnl is not None and stored_pnl is not None and abs(stored_pnl - pnl) > 0.02 + 0.005 * abs(pnl):
        problems.append(f"profit/loss stored {stored_pnl:+.2f} but recomputed {pnl:+.2f} from the leg prices")
    return {"book": book, "bet_id": row["bet_id"], "legs": checked, "stored": {"status": stored_status, "profit_loss": stored_pnl},
            "recomputed": {"status": expected, "profit_loss": pnl}, "agrees": not problems, "problems": problems}


def _rows(path: Path, sql: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def run() -> dict:
    ledger = REPO / "operational" / "paper_bankroll.db"
    personal = REPO / "operational" / "runtime" / "personal_logs.db"
    model_rows = _rows(ledger, "SELECT paper_bet_id AS bet_id, origin, market_family, team, event_id, player_id, threshold, entry_odds, stake, legs_json, "
                               "result_status, profit_loss, settlement_json FROM paper_bets ORDER BY created_at_utc")
    results = []
    for r in model_rows:
        book = "model" if r["origin"] == "AUTOMATIC" else "earlier manual ticket (kept in the ledger for audit)"
        results.append(audit_row(book, r))
    for r in _rows(personal, "SELECT bet_id, log_hash, legs_json, entry_odds, stake, result_status, profit_loss, settlement_json FROM bets"):
        results.append(audit_row("personal log " + (r["log_hash"][:8] if r["log_hash"] else "?"), r))
    games = {}
    for res in results:
        for l in res["legs"]:
            games[l["state"]] = games.get(l["state"], 0) + 1
    return {"as_of_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": "api-web.nhle.com box scores, fetched now",
            "bets_checked": len(results), "agree": sum(1 for r in results if r["agrees"]), "leg_game_states": games, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = run()
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    print(f"{rep['agree']}/{rep['bets_checked']} bets agree with the official box scores")
    for r in rep["results"]:
        print(f"  {'ok  ' if r['agrees'] else 'DIFF'} {r['book'][:22]:<22} {r['bet_id'][:16]:<16} stored {r['stored']['status']:<10} recomputed {r['recomputed']['status']:<10}" + ("" if r["agrees"] else " -- " + "; ".join(r["problems"])))
    return 0 if rep["agree"] == rep["bets_checked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
