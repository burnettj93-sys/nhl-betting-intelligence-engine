"""
Real Parlay Paper Trader: the scheduled job (every 15 minutes) that runs the
unified ticket workflow in operational/daily_tickets.py against the real
$500 / $10-per-ticket paper account.

Each run, under one lock:
  1. revalidate recorded, not-yet-started tickets against current goalie /
     roster / schedule data -- ALERTS ONLY, nothing is refunded or voided;
  2. refresh DraftKings prices (bounded credit capture, operational/best_bets.py);
  3. select up to five distinct cross-game tickets with the one selector
     (research/real_market_parlay/engine.py::select_tickets) and record each as
     a $10 paper ticket if cash allows (checked atomically in the ledger);
  4. settle every due ticket (operational/paper_bet_settlement_driver.py,
     rules in docs/PAPER_SETTLEMENT_RULES.md);
  5. rewrite the tickets_state.json document Today reads and publish the cloud
     snapshot if anything changed.

Re-running is always safe: ticket ids are deterministic, so a refresh or a
restart never stakes a ticket twice and never rewrites a recorded one.

Run: python3 -m operational.real_parlay_paper_trader
"""
from __future__ import annotations

import datetime as dt
import fcntl

import db
from operational import bet_revalidation
from operational import daily_tickets
from operational import eastern_time as et
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as settlement
from operational import state_paths as _sp
from research.real_market_parlay import engine as rmp

MAX_PARLAYS_PER_DAY = rmp.MAX_TICKETS_PER_DAY

LOCK_PATH = _sp.path("real_parlay_paper_trader.lock", area="operational")


HEARTBEAT_PUBLISH_MIN = 25.0


def _publish_heartbeat_due(now: dt.datetime) -> bool:
    """The Today board shows a 'board is N minutes old' warning after 45 minutes. A quiet stretch changes nothing,
    so without a heartbeat the published board would look stale while the engine is healthy: republish when the
    last successful publication is at least HEARTBEAT_PUBLISH_MIN old."""
    import json
    path = _sp.path("cloud_publish_state.json")
    try:
        last = json.loads(path.read_text()).get("last_success_at")
        return last is None or (now - dt.datetime.fromisoformat(last)).total_seconds() / 60.0 >= HEARTBEAT_PUBLISH_MIN
    except (OSError, ValueError):
        return True


def run(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    today_et = et.eastern_today(now)
    nhl_conn = db.get_conn()
    bankroll_conn = pb.init_db()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"stake_result": {"eastern_date": today_et, "status": "SKIPPED",
                                      "reason": "ANOTHER_TRADER_RUN_IN_PROGRESS"},
                    "settlement_summary": None}

        revalidation_summary = bet_revalidation.revalidate_pending_real_market_bets(
            bankroll_conn, nhl_conn, now)

        # Prices first, so selection sees what was just pulled. A capture
        # failure never blocks recording from prices already archived, nor settlement.
        try:
            from operational import best_bets
            price_refresh = best_bets.refresh(now, conn=nhl_conn)
        except Exception as exc:  # noqa: BLE001
            price_refresh = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "changed": False}

        # Starting-goalie reports and reported lineups (Daily Faceoff, rate-limited inside the module). A failure never blocks the cycle.
        try:
            from operational import dailyfaceoff
            df_state = dailyfaceoff.refresh(now)
            df_summary = {"status": df_state.get("status"), "last_error": df_state.get("last_error"),
                          **dailyfaceoff.ingest_goalie_status(nhl_conn, df_state, now)} if df_state.get("goalies") else \
                         {"status": df_state.get("status"), "last_error": df_state.get("last_error"), "written": 0}
        except Exception as exc:  # noqa: BLE001
            df_summary = {"status": "ERROR", "last_error": f"{exc.__class__.__name__}: {exc}", "written": 0}

        # One candidate-leg pool serves manual orders (revalidated against current prices) and the automatic selector.
        collected = daily_tickets.collect_candidate_legs(nhl_conn, now)
        try:
            from operational import manual_orders
            manual_summary = manual_orders.poll_and_process(bankroll_conn, nhl_conn, now, current_legs=collected["legs"])
        except Exception as exc:  # noqa: BLE001 - the queue must never stop automatic tickets
            manual_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "processed": 0}
        stake_summary = daily_tickets.run_cycle(nhl_conn, bankroll_conn, now, collected=collected)
        settlement_summary = settlement.settle_due_bets(bankroll_conn, nhl_conn)
        if settlement_summary.get("settled"):
            daily_tickets.refresh_state_only(bankroll_conn, now)
        try:
            from operational import product_data
            product_summary = product_data.refresh_state(now, nhl=nhl_conn, tickets_state=daily_tickets.read_state())
        except Exception as exc:  # noqa: BLE001 - the product pages keep the last good document
            product_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        nhl_conn.close()
        bankroll_conn.close()

    result = {"stake_result": stake_summary, "settlement_summary": settlement_summary,
              "revalidation_summary": revalidation_summary, "price_refresh": price_refresh,
              "manual_orders": manual_summary, "product_state": product_summary, "starter_feed": df_summary}
    from operational import ingestion_health
    ingestion_health.record_run("real_parlay_paper_trader", {
        "eastern_date": today_et, "newly_staked": stake_summary["newly_recorded"],
        "qualifying_parlays_found": stake_summary["qualifying_tickets_found"],
        "insufficient_funds": stake_summary["insufficient_funds"], "status": "SUCCESS"})
    settled_count = (settlement_summary or {}).get("settled", 0)
    if (stake_summary["newly_recorded"] > 0 or settled_count > 0 or stake_summary["state_changed"]
            or revalidation_summary.get("alerts_recorded") or manual_summary.get("processed") or df_summary.get("written") or _publish_heartbeat_due(now)):
        from operational import cloud_publish_hook
        result["cloud_publish"] = cloud_publish_hook.publish_after("real_parlay_paper_trader")
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
