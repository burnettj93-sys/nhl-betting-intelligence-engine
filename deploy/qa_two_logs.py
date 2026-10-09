"""
Isolated QA of personal accounts against a COPY of the real model ledger (nothing live is touched): two separate last-name accounts (a shared surname gets a number), each with its
own $500, a Paper Parlay Builder slip and a model-option bet each, an overdraft refusal, a forged cross-account order, settlement with both outcomes, then proof the model book is
byte-identical and each account reconciles on its own. Prints a JSON report.

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
    from tests.product_fixture import builder_pool_doc
    tmp = Path(tempfile.mkdtemp())
    ledger_copy = tmp / "paper_bankroll_copy.db"
    shutil.copy(REPO / "operational" / "paper_bankroll.db", ledger_copy)
    led = pb.init_db(ledger_copy)
    before_hash, before_acct = fingerprint(led), pb.account_state(led, "REAL_MARKET_PAPER")
    logs = pl.connect(tmp / "personal_logs.db")
    legs = board(4, price=-105, p=0.62)
    options = po.build_options(legs, "2026-10-15")["options"]
    pool = builder_pool_doc()
    bnow = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)
    people = {"A": ("Alvarez", 1, "AB2D-EF3G"), "B": ("Alvarez", 2, "HJ4K-LM5N")}                    # two people with one surname: the second is "Alvarez 2"
    acct = {}
    for tag, (surname, n, passcode) in people.items():
        slug = pl.surname_slug(surname, n)
        acct[tag] = {"slug": slug, "hash": pl.account_key(slug), "pass": passcode, "name": pl.display_for(surname, n), "surname": surname}
    answers = []

    def send(tag, doc, *, now=bnow, current=None):
        a = acct[tag]
        res = pl.process_order(logs, log_signing.sign(doc, a["pass"], a["hash"]), current_legs=current or [], now=now, source="qa", builder_pool=pool)
        return res

    for tag, a in acct.items():
        create = {"creation_id": f"crt_{tag}" + "z" * 8, "display_name": a["surname"], "slug": a["slug"], "write_pub": log_signing.public_key_hex(a["pass"], a["hash"])}
        doc = order_client.build_personal_order(None, order_id=f"ord_{tag}create1", log_hash=a["hash"], page_generated_at=None, stake=0, create=create, kind="PERSONAL_LOG_CREATE")
        answers.append({"account": a["name"], "step": "create", "status": send(tag, doc)["status"]})
    pool_leg = lambda pid, price, name, k=2: {"game_id": "2026020900", "participant_id": pid, "participant_name": name, "market_family": "PLAYER_SOG_ALTERNATE", "threshold": k, "side": "OVER", "american_price": price}
    for tag, price in (("A", -110.0), ("B", -110.0)):
        doc = order_client.build_builder_order([pool_leg("P1", price, "Test Skater One")], order_id=f"ord_{tag}builder1", log_hash=acct[tag]["hash"], page_generated_at=None, stake=40, same_game_ack=False, combined_american=price)
        answers.append({"account": acct[tag]["name"], "step": "builder slip", "status": send(tag, doc)["status"]})
    doc = order_client.build_builder_order([pool_leg("P1", -110.0, "Test Skater One"), pool_leg("P2", 120.0, "Test Skater Two")], order_id="ord_Abuilder2", log_hash=acct["A"]["hash"], page_generated_at=None,
                                           stake=15, same_game_ack=True, combined_american=None)
    answers.append({"account": acct["A"]["name"], "step": "same-game slip (acknowledged)", "status": send("A", doc)["status"]})
    doc = order_client.build_personal_order(options[0], order_id="ord_Amodel0001", log_hash=acct["A"]["hash"], page_generated_at=None, stake=10)
    answers.append({"account": acct["A"]["name"], "step": "model option", "status": send("A", doc, now=NOW, current=legs)["status"]})
    over = order_client.build_builder_order([pool_leg("P1", -300.0, "Test Skater One", k=1)], order_id="ord_Boverdraw1", log_hash=acct["B"]["hash"], page_generated_at=None, stake=900, same_game_ack=False, combined_american=-300)
    overdraft = send("B", over)
    forged = []
    for tag, other in (("A", "B"), ("B", "A")):                 # each person tries to add to the OTHER person's account, signing with their own passcode
        h = acct[tag]["hash"]
        doc = order_client.build_builder_order([pool_leg("P1", -110.0, "Test Skater One", k=3)], order_id=f"ord_x{tag}forged1", log_hash=h, page_generated_at=None, stake=10, same_game_ack=False, combined_american=None)
        forged.append(pl.process_order(logs, log_signing.sign(doc, acct[other]["pass"], h), current_legs=[], now=bnow, source="qa", builder_pool=pool)["status"])
    outcomes = iter([{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}] * 6)
    with mock.patch.object(drv, "resolve_combo_bet", side_effect=lambda *a, **k: next(outcomes)):
        settled = pl.settle_open(logs, None, NOW + dt.timedelta(days=1))
    per_log = {}
    for lg in logs.execute("SELECT * FROM logs"):
        bets = [dict(b) for b in logs.execute("SELECT * FROM bets WHERE log_hash = ?", (lg["log_hash"],))]
        summ = pl.summarize(bets)
        st = pl.account_state(logs, lg["log_hash"])
        per_log[lg["display_name"]] = {"bets": summ["bets"], "wins": summ["wins"], "losses": summ["losses"], "settled_pnl": summ["settled_pnl"],
                                       "independent_pnl": round(sum(b["profit_loss"] for b in bets), 2), "open_stake": summ["open_stake"],
                                       "starting_balance": st["starting_balance"], "available_cash": st["available_cash"],
                                       "cash_rederived": round(500.0 + sum(b["profit_loss"] for b in bets) - summ["open_stake"], 2)}
    after_hash, after_acct = fingerprint(led), pb.account_state(led, "REAL_MARKET_PAPER")
    reconcile = pl.reconcile(logs)
    report = {"answers": answers, "overdraft_attempt": {"status": overdraft["status"], "reason": (overdraft["reason"] or "")[:80]}, "forged_cross_account_orders": forged,
              "settlement": settled, "per_account": per_log, "reconcile": [{"name": r["name"], "agrees": r["agrees"]} for r in reconcile],
              "model_ledger_hash_before": before_hash[:16], "model_ledger_hash_after": after_hash[:16], "model_ledger_unchanged": before_hash == after_hash,
              "model_account_before": before_acct, "model_account_after": after_acct, "model_account_unchanged": before_acct == after_acct,
              "accounts_reconcile": all(v["settled_pnl"] == v["independent_pnl"] and v["available_cash"] == v["cash_rederived"] for v in per_log.values()) and all(r["agrees"] for r in reconcile),
              "note": "Isolated: a copy of the model ledger and a temporary personal database; a synthetic price list and legs; the resolver is stubbed with alternating WIN and LOSS."}
    report["pass"] = (report["model_ledger_unchanged"] and report["model_account_unchanged"] and report["accounts_reconcile"] and all(a["status"] in ("CREATED", "RECORDED") for a in answers)
                      and overdraft["status"] == "REJECTED" and "INSUFFICIENT_FUNDS" in (overdraft["reason"] or "") and forged == ["REJECTED", "REJECTED"] and set(per_log) == {"Alvarez", "Alvarez 2"})
    shutil.rmtree(tmp, ignore_errors=True)
    return report


if __name__ == "__main__":
    out = main()
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["pass"] else 1)
