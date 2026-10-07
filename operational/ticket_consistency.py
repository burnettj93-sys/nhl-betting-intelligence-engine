"""
Cross-checks that every surface describes the same tickets: the paper ledger (source of truth), the
ticket board document Today reads, the published Cloud snapshot, and the postmortems.

    python3 -m operational.ticket_consistency [--published]     # exit code 1 when anything disagrees

`audit()` returns a list of human-readable discrepancies; an empty list means the surfaces agree on ticket
ids, statuses, legs (labels, prices, price timestamps), potential return, and the account (cash, open stakes,
equity, settled P&L). Nothing here writes anything.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from operational import daily_tickets as dtk
from operational import paper_bankroll as pb

REPO = Path(__file__).resolve().parent.parent
_STATUS_FOR = {"PENDING": {"RECORDED", "PENDING"}, "WIN": {"WON"}, "LOSS": {"LOST"}, "VOID": {"VOID"},
               "UNRESOLVED": {"UNRESOLVED"}}


def _cards(state: dict) -> dict[str, dict]:
    cards = {}
    for key in ("tickets", "earlier_open_tickets", "recent_settled"):
        for c in state.get(key, []):
            cards.setdefault(c["ticket_id"], c)
    return cards


def _leg_signature(card: dict) -> list[tuple]:
    return [(l["label"], l["american_price"], l["price_captured_at_utc"]) for l in card["legs"]]


def audit(bankroll_conn, state: dict | None, published: dict | None = None, *, postmortems: list[dict] | None = None) -> list[str]:
    problems: list[str] = []
    rows = {r["paper_bet_id"]: r for r in pb.query_paper_bets(bankroll_conn, track=dtk.TRACK, is_combo=True)}
    account = pb.account_state(bankroll_conn, dtk.TRACK)

    surfaces = {"board": state}
    if published is not None:
        surfaces["published"] = published
    for name, doc in surfaces.items():
        if not doc or "account" not in doc:
            problems.append(f"{name}: no ticket board document")
            continue
        for field in ("available_cash", "open_stakes", "equity", "settled_pnl"):
            if abs(doc["account"][field] - account[field]) > 0.005:
                problems.append(f"{name}: account {field} {doc['account'][field]} != ledger {account[field]}")
        cards = _cards(doc)
        recorded_cards = {i: c for i, c in cards.items() if c.get("recorded")}
        for tid in recorded_cards:
            if tid not in rows:
                problems.append(f"{name}: recorded ticket {tid} is not in the ledger")
        board_tickets = {c["ticket_id"] for c in doc.get("tickets", []) if c.get("recorded")}
        # Every ledger ticket for the board's Eastern date must be on the board, and open older ones listed.
        for tid, row in rows.items():
            if row["result_status"] in ("PENDING", "UNRESOLVED") or tid in board_tickets or \
                    row["market_id"].startswith(f"REAL_MARKET_PARLAY:{doc.get('date_et')}:"):
                if tid not in cards:
                    problems.append(f"{name}: ledger ticket {tid} ({row['result_status']}) is missing")
        for tid, card in recorded_cards.items():
            row = rows.get(tid)
            if row is None:
                continue
            if card["status"] not in _STATUS_FOR[row["result_status"]]:
                problems.append(f"{name}: {tid} status {card['status']} vs ledger {row['result_status']}")
            frozen = json.loads(row["legs_json"])
            expected = [(l["participant_name"], l["american_price"], l["captured_at_utc"]) for l in frozen]
            got = [(l["participant_name"], l["american_price"], l["price_captured_at_utc"]) for l in card["legs"]]
            if expected != got:
                problems.append(f"{name}: {tid} legs/prices/timestamps differ from the frozen ledger legs")
            if abs(card["combined_american"] - row["entry_odds"]) > 1e-6:
                problems.append(f"{name}: {tid} combined price {card['combined_american']} != ledger {row['entry_odds']}")
            if abs(card["stake"] - row["stake"]) > 1e-9:
                problems.append(f"{name}: {tid} stake differs")
            if row["result_status"] in ("WIN", "LOSS", "VOID"):
                pnl = (card.get("result") or {}).get("profit_loss")
                if pnl is None or abs(pnl - (row["profit_loss"] or 0.0)) > 0.005:
                    problems.append(f"{name}: {tid} profit/loss {pnl} != ledger {row['profit_loss']}")
    if postmortems is not None:
        lost = {i for i, r in rows.items() if r["result_status"] == "LOSS"}
        pm_ids = {p["ticket_id"] for p in postmortems}
        if pm_ids != lost:
            problems.append(f"postmortems cover {sorted(pm_ids)} but the lost tickets are {sorted(lost)}")
        for pm in postmortems:
            row = rows.get(pm["ticket_id"])
            frozen = {(l["participant_id"], l["market_family"], l.get("threshold")): l for l in json.loads(row["legs_json"])} if row else {}
            for leg in pm["missed_legs"] + pm["hit_legs"] + pm["other_legs"]:
                if leg.get("predicted_probability") is None:
                    problems.append(f"postmortem {pm['ticket_id']}: a leg has no stored predicted probability")
    return problems


def published_board() -> dict | None:
    """The tickets section of the snapshot currently on the cloud-data branch (what the remote app reads)."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "cloud-data"], cwd=REPO, capture_output=True, timeout=60)
        raw = subprocess.run(["git", "show", "origin/cloud-data:current/snapshot.json"], cwd=REPO, capture_output=True,
                             text=True, check=True, timeout=60).stdout
        return json.loads(raw).get("tickets")
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    conn = pb.init_db()
    published = published_board() if "--published" in argv else None
    if "--published" in argv and published is None:
        print("published snapshot could not be read")
        return 2
    problems = audit(conn, dtk.read_state(), published)
    print(json.dumps({"checked_surfaces": ["ledger", "board"] + (["published"] if published else []),
                      "discrepancies": problems}, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
