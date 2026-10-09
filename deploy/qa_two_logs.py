"""
Isolated QA of personal logs against a COPY of the real model ledger (nothing live is touched): two separate logs, several bets each, settlement with
both outcomes, then proof the model book is byte-identical and each log reconciles on its own. Prints a JSON report.

Run: python3 deploy/qa_two_logs.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dashboard import order_client  # noqa: E402
from operational import log_signing  # noqa: E402
from operational import paper_bankroll as pb  # noqa: E402
from operational import paper_bet_settlement_driver as drv  # noqa: E402
from operational import personal_logs as pl  # noqa: E402
from operational import player_options as po  # noqa: E402
from tests.test_daily_tickets import NOW, board  # noqa: E402


def fingerprint(conn) -> str:
    h = hashlib.sha256()
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        h.update(name.encode())
        h.update("\n".join(sorted(repr(tuple(r)) for r in conn.execute(f"SELECT * FROM {name}"))).encode())
    return h.hexdigest()


def main() -> dict:
    tmp = Path(tempfile.mkdtemp())
    ledger_copy = tmp / "paper_bankroll_copy.db"
    shutil.copy(REPO / "operational" / "paper_bankroll.db", ledger_copy)
    led = pb.init_db(ledger_copy)
    before_hash, before_acct = fingerprint(led), pb.account_state(led, "REAL_MARKET_PAPER")
    logs = pl.connect(tmp / "personal_logs.db")
    legs = board(4, price=-105, p=0.62)
    options = po.build_options(legs, "2026-10-15")["options"]
    codes = {"A": "qa-alpha-otter-1111", "B": "qa-bravo-maple-2222"}
    keys = {"A": "AAAA-BBBB-CCCC-DDDD-EEEE", "B": "FFFF-GGGG-HHHH-JJJJ-KKKK"}
    answers = []
    for tag, code in codes.items():
        for i, opt in enumerate(options[:2]):
            h = pl.code_hash(code)
            doc = order_client.build_personal_order(opt, order_id=f"ord_{tag}{i}" + "q" * 8, log_hash=h, page_generated_at=None, stake=10 + 5 * i,
                                                    create={"creation_id": f"crt_{tag}" + "z" * 8, "display_name": f"QA {tag}",
                                                            "write_pub": log_signing.public_key_hex(keys[tag], h)} if i == 0 else None)
            doc = log_signing.sign(doc, keys[tag], h)
            res = pl.process_order(logs, doc, current_legs=legs, now=NOW, source="qa")
            answers.append({"log": tag, "order": res["order_id"], "status": res["status"]})
    forged = []
    for tag, other in (("A", "B"), ("B", "A")):                 # each person tries to add to the OTHER person's log, knowing its code but signing with their own key
        h = pl.code_hash(codes[tag])
        doc = log_signing.sign(order_client.build_personal_order(options[0], order_id=f"ord_x{tag}" + "f" * 8, log_hash=h, page_generated_at=None, stake=10), keys[other], h)
        forged.append(pl.process_order(logs, doc, current_legs=legs, now=NOW, source="qa")["status"])
    outcomes = iter([{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}] * 4)
    with mock.patch.object(drv, "resolve_combo_bet", side_effect=lambda *a, **k: next(outcomes)):
        settled = pl.settle_open(logs, None, NOW + dt.timedelta(days=1))
    per_log = {}
    for lg in logs.execute("SELECT * FROM logs"):
        bets = [dict(b) for b in logs.execute("SELECT * FROM bets WHERE log_hash = ?", (lg["log_hash"],))]
        summ = pl.summarize(bets)
        per_log[lg["display_name"]] = {"bets": summ["bets"], "wins": summ["wins"], "losses": summ["losses"], "settled_pnl": summ["settled_pnl"],
                                       "independent_pnl": round(sum(b["profit_loss"] for b in bets), 2), "open_stake": summ["open_stake"]}
    after_hash, after_acct = fingerprint(led), pb.account_state(led, "REAL_MARKET_PAPER")
    report = {"answers": answers, "forged_cross_log_orders": forged, "settlement": settled, "per_log": per_log, "model_ledger_hash_before": before_hash[:16], "model_ledger_hash_after": after_hash[:16],
              "model_ledger_unchanged": before_hash == after_hash, "model_account_before": before_acct, "model_account_after": after_acct,
              "model_account_unchanged": before_acct == after_acct,
              "logs_reconcile": all(v["settled_pnl"] == v["independent_pnl"] for v in per_log.values()),
              "note": "Isolated: a copy of the model ledger and a temporary personal database; synthetic legs; the resolver is stubbed with one WIN and one LOSS per log."}
    report["pass"] = report["model_ledger_unchanged"] and report["model_account_unchanged"] and report["logs_reconcile"] and all(a["status"] == "RECORDED" for a in answers) and forged == ["REJECTED", "REJECTED"]
    shutil.rmtree(tmp, ignore_errors=True)
    return report


if __name__ == "__main__":
    out = main()
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["pass"] else 1)
