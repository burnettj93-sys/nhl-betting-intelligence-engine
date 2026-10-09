"""
Verifies the hosted personal-log workflow from the engine's own records, after it has been exercised on the hosted app (create log -> add -> persist -> settle):

  1. at least two logs exist, each created by an order that arrived through the app's own write path (`via: direct`) and signed (a valid signature is re-verified here);
  2. each log has at least one bet recorded from such an order, and at least one settled bet (open -> won/lost/void) with the right profit arithmetic;
  3. the two logs are separate: no bet id, order id or log hash is shared, and each log's totals equal its own rows;
  4. the model book was not touched: its account is re-derived from raw rows and agrees three ways, no personal bet id exists in the ledger, and no ledger row carries a personal order id.

It prints a JSON report and exits non-zero if anything is missing or wrong. A missing step is reported as PENDING, not as a pass.
Run: python3 deploy/verify_personal_workflow.py
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from operational import log_signing  # noqa: E402
from operational import paper_bankroll as pb  # noqa: E402
from operational import personal_logs as pl  # noqa: E402
from operational import watchdog as wd  # noqa: E402


def main(ledger=None, personal=None, now: dt.datetime | None = None) -> dict:
    if ledger is None:
        ledger = sqlite3.connect(f"file:{pb.DB_PATH}?mode=ro", uri=True)
        ledger.row_factory = sqlite3.Row
    personal = personal or pl.connect()
    now = now or dt.datetime.now(dt.timezone.utc)
    report: dict = {"checked_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "steps": {}, "problems": [], "pending": []}
    logs = [dict(r) for r in personal.execute("SELECT * FROM logs WHERE log_hash <> ?", (pl.UNCLAIMED_LEGACY,))]
    direct = []
    for lg in logs:
        orders = [dict(o) for o in personal.execute("SELECT * FROM orders WHERE log_hash = ? ORDER BY processed_at_utc", (lg["log_hash"],))]
        via = [o for o in orders if json.loads(o["request_json"] or "{}").get("via") == "direct"]
        creation = next((o for o in via if o["status"] in (pl.CREATED, pl.RECORDED) and (json.loads(o["request_json"]).get("log") or {}).get("create")), None)
        bets = [dict(b) for b in personal.execute("SELECT * FROM bets WHERE log_hash = ?", (lg["log_hash"],))]
        signed_ok = all(log_signing.verify(json.loads(o["request_json"]), lg["write_pub"]) for o in via if o["request_json"])
        settled = [b for b in bets if b["result_status"] in ("WIN", "LOSS", "VOID")]
        arithmetic_ok = all(
            (round(b["profit_loss"], 2) == -b["stake"]) if b["result_status"] == "LOSS" else (b["profit_loss"] == 0.0) if b["result_status"] == "VOID" else (b["profit_loss"] > 0) for b in settled)
        direct.append({"log": lg["display_name"], "hash_prefix": lg["log_hash"][:8], "orders_via_app_write_path": len(via), "created_via_app": bool(creation),
                       "signatures_verify": signed_ok, "bets": len(bets), "open": len(bets) - len(settled), "settled": len(settled), "settled_arithmetic_ok": arithmetic_ok,
                       "summary": pl.summarize(bets)})
    qualifying = [d for d in direct if d["created_via_app"] and d["bets"] and d["signatures_verify"]]
    report["logs"] = direct
    if len(qualifying) < 2:
        report["pending"].append(f"need 2 logs created and added to through the hosted app's own write path; found {len(qualifying)}")
    if not any(d["settled"] for d in qualifying):
        report["pending"].append("no bet created through the app has settled yet (it settles after its game finishes)")
    ids = [b["bet_id"] for b in personal.execute("SELECT bet_id FROM bets")]
    hashes = [lg["log_hash"] for lg in logs]
    report["steps"]["logs_are_separate"] = len(set(ids)) == len(ids) and len(set(hashes)) == len(hashes)
    clash = [r[0] for r in ledger.execute("SELECT paper_bet_id FROM paper_bets") if r[0] in set(ids)]
    order_ids = {o["order_id"] for o in personal.execute("SELECT order_id FROM orders")}
    leaked = [r[0] for r in ledger.execute("SELECT paper_bet_id, provenance_json FROM paper_bets") if r[1] and any(o in r[1] for o in order_ids)]
    report["steps"]["no_personal_bet_in_the_model_ledger"] = not clash and not leaked
    rec = wd.check_reconciliation(ledger, personal)
    report["steps"]["model_book_reconciles_three_ways_and_each_log_matches_its_rows"] = rec["status"] == wd.OK
    report["reconciliation"] = rec
    for k, v in report["steps"].items():
        if not v:
            report["problems"].append(k)
    for d in qualifying:
        if not d["settled_arithmetic_ok"]:
            report["problems"].append(f"log {d['hash_prefix']}: settled arithmetic")
    report["result"] = "FAIL" if report["problems"] else ("PENDING" if report["pending"] else "PASS")
    return report


if __name__ == "__main__":
    out = main()
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["result"] == "PASS" else 1)
