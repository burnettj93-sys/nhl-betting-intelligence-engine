"""
Scheduled job (every 2 minutes): answers manual "Add to paper book" orders without waiting for the 15-minute trader.

It asks the order queue (open `paper-order` issues from the repository owner) for work first -- a cheap call -- and only
builds the current price pool when there is an order to revalidate. After any order is answered it rewrites the tickets
document and republishes the cloud snapshot so the hosted page shows the outcome. It takes the trader's lock without
waiting: if a trader run is in progress that run answers the queue itself.

Run: python3 -m operational.manual_order_job
"""
from __future__ import annotations

import datetime as dt
import fcntl

import db
from operational import daily_tickets, manual_orders
from operational import paper_bankroll as pb
from operational import real_parlay_paper_trader as trader


def run(now: dt.datetime | None = None, *, fetch=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    trader.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(trader.LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "SKIPPED", "reason": "TRADER_RUN_IN_PROGRESS", "processed": 0}
        nhl_conn, bankroll_conn = db.get_conn(), pb.init_db()
        try:
            summary = manual_orders.poll_and_process(bankroll_conn, nhl_conn, now, fetch=fetch)
            if summary.get("processed"):
                daily_tickets.refresh_state_only(bankroll_conn, now)
        finally:
            nhl_conn.close()
            bankroll_conn.close()
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
    if summary.get("processed"):
        from operational import cloud_publish_hook
        summary["cloud_publish"] = cloud_publish_hook.publish_after("manual_order_job")
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
