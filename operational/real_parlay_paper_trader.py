"""
Real Parlay Paper Trader: the scheduled job (every 10 minutes) that runs the
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
    try:
        from operational import run_heartbeat
        run_heartbeat.beat("real_parlay_paper_trader", now)           # when a cycle STARTED: the evidence that tells an outage from sleep from a quiet window
    except Exception:  # noqa: BLE001
        pass
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

        # The 07:45 refresh (or, on an early day or after a sleep, as soon as it is due): statistics, results, rosters, participation evidence, before any capture.
        try:
            from operational import credit_planner, eastern_time, morning_refresh
            def _parse(t):
                return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
            open_wave = any(_parse(w["target_utc"]) <= now <= _parse(w["last_chance_utc"]) for w in (credit_planner.load_plan(eastern_time.eastern_today(now)) or {}).get("waves") or [])
            if open_wave:                                           # a capture window is open: the statistics refresh can wait, a price window cannot
                refresh_summary = {"status": "DEFERRED", "reason": "A_CAPTURE_WINDOW_IS_OPEN"}
            else:
                refresh_summary = {k: v for k, v in morning_refresh.run_if_due(now).items() if k in ("status", "reason", "steps", "due_at_utc")}
        except Exception as exc:  # noqa: BLE001
            refresh_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}

        # Keep the Mac awake for the day's capture timeline (prep 07:45/08:00 and every wave): an idle-sleep assertion inside each window, and on AC power across the gap to the next one. The Mac cannot be
        # woken by software; this only stops it from going to sleep while this job can still run. Never raises, never touches a credit.
        try:
            from operational import keep_awake
            keep_awake_summary = {**{k: v for k, v in keep_awake.ensure_holding(now).items() if k in ("action", "reason", "why", "until", "holding")}, "guard": keep_awake.ensure_guard(now).get("guard")}
        except Exception as exc:  # noqa: BLE001
            keep_awake_summary = {"action": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}

        # Official NHL rosters for the teams playing today and tomorrow (free, no credits): the identity check for every card, price and slip. At most every 3 hours per team; a failure keeps the last good copy.
        try:
            from operational import eastern_time as _et2, player_integrity
            _d0 = _et2.eastern_today(now)
            _d1 = (dt.date.fromisoformat(_d0) + dt.timedelta(days=1)).isoformat()
            _teams = {t for r in nhl_conn.execute("SELECT home_team, away_team FROM games WHERE game_date IN (?, ?)", (_d0, _d1)).fetchall() for t in (r[0], r[1])}
            roster_summary = player_integrity.refresh_official(_teams, now)
        except Exception as exc:  # noqa: BLE001
            roster_summary = {"error": f"{exc.__class__.__name__}: {exc}"}

        revalidation_summary = bet_revalidation.revalidate_pending_real_market_bets(
            bankroll_conn, nhl_conn, now)

        # Prices first, so selection sees what was just pulled. A capture
        # failure never blocks recording from prices already archived, nor settlement.
        try:
            from operational import best_bets
            price_refresh = best_bets.refresh(now, conn=nhl_conn)
        except Exception as exc:  # noqa: BLE001
            price_refresh = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "changed": False}

        # Grade the pregame-frozen board cards of games that are now FINAL against the official results (read-only; operational/board_scorecard.py).
        try:
            from operational import board_scorecard
            scorecard_summary = board_scorecard.safe_score(nhl_conn) if not _sp.under_test() else {"status": "SKIPPED_UNDER_TEST"}
        except Exception as exc:  # noqa: BLE001 - grading must never stop a trader cycle
            scorecard_summary = {"status": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}

        # The league-wide moneyline + standard-spread pull is on the day's timeline (08:00 morning pull, one pregame pull shared by every wave): it runs from this 10-minute cycle, self-gated
        # by operational/capture_timeline.py and by credit reservations, so a late cycle after a sleep or an outage still takes the pull that is due.
        try:
            from operational import moneyline_freshness
            _league = moneyline_freshness.run_if_due(now, label=None)
            league_summary = {k: v for k, v in _league.items() if k in ("action", "reason", "ran", "credits_spent_this_run", "api_error", "due_detail")}
            if _league.get("real_odds_bridge") is not None:         # the pull's prices were carried into odds_snapshots (and the recommendations evaluated) by the same workflow
                _b = _league["real_odds_bridge"]
                league_summary["bridge"] = {k: _b.get(k) for k in ("status", "rows_seen", "rows_written", "rows_skipped_unmatched", "error")}
                league_summary["recommendations"] = {k: (_league.get("real_recommendation_orchestrator") or {}).get(k) for k in ("status", "games_evaluated", "recommendations_recorded", "error")}
        except Exception as exc:  # noqa: BLE001
            league_summary = {"action": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}

        try:                                           # the day's timeline document, from the plan, the ledger, the wave state and the availability record
            from operational import day_status
            day_status.write(now)
        except Exception:  # noqa: BLE001
            pass

        # The Paper Parlay Builder's price list (every priced line for today's games, for people to pick from), written before the order queue is answered.
        try:
            from operational import builder_pool
            builder_summary = builder_pool.refresh(nhl_conn, now)
        except Exception as exc:  # noqa: BLE001 - the builder is informational; the model book never waits on it
            builder_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}

        # Starting-goalie reports and reported lineups (Daily Faceoff, rate-limited inside the module). A failure never blocks the cycle.
        try:
            from operational import dailyfaceoff
            df_state = dailyfaceoff.refresh(now)
            df_summary = {"status": df_state.get("status"), "last_error": df_state.get("last_error"),
                          **dailyfaceoff.ingest_goalie_status(nhl_conn, df_state, now)} if df_state.get("goalies") else \
                         {"status": df_state.get("status"), "last_error": df_state.get("last_error"), "written": 0}
        except Exception as exc:  # noqa: BLE001
            df_summary = {"status": "ERROR", "last_error": f"{exc.__class__.__name__}: {exc}", "written": 0}

        # One candidate-leg pool serves the automatic selector (the only writer of model tickets).
        collected = daily_tickets.collect_candidate_legs(nhl_conn, now)
        try:                                       # evidence queue only (goalie confirmations, Ontario checks, path checks): nothing here can stake anything
            from operational import manual_orders
            manual_summary = manual_orders.poll_and_process(bankroll_conn, nhl_conn, now)
        except Exception as exc:  # noqa: BLE001 - the queue must never stop automatic tickets
            manual_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "processed": 0}
        stake_summary = daily_tickets.run_cycle(nhl_conn, bankroll_conn, now, collected=collected)
        settlement_summary = settlement.settle_due_bets(bankroll_conn, nhl_conn)
        try:                                       # the temporary 14-day paper learning cohort (its own ledger; a no-op until the owner's approval is on file, and after 2026-11-02 Eastern)
            from operational import learning_cohort
            learning_summary = learning_cohort.run_cycle(nhl_conn, now, collected, collector=lambda: daily_tickets.collect_candidate_legs(nhl_conn, now, advisory_price_age=True))
        except Exception as exc:  # noqa: BLE001 - never stops the model book
            learning_summary = {"status": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}
        try:                                       # Eggy's own daily side pick (moneyline or puck line, or PASS): its own ledger and postmortem, independent of the learning parlays
            from operational import eggy_pick_ledger
            eggy_summary = eggy_pick_ledger.run_cycle(nhl_conn, now)
        except Exception as exc:  # noqa: BLE001 - never stops the model book or the learning cohort
            eggy_summary = {"status": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}
        if settlement_summary.get("settled"):
            daily_tickets.refresh_state_only(bankroll_conn, now)
        try:
            from operational import product_data
            product_summary = product_data.refresh_state(now, nhl=nhl_conn, tickets_state=daily_tickets.read_state())
        except Exception as exc:  # noqa: BLE001 - the product pages keep the last good document
            product_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
        try:                                       # the per-game board and the primary slip (informational; the model book never waits on it)
            from operational import slate_board
            board_summary = slate_board.refresh(now, tickets=daily_tickets.read_state())
        except Exception as exc:  # noqa: BLE001
            board_summary = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}"}
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        nhl_conn.close()
        bankroll_conn.close()

    result = {"stake_result": stake_summary, "settlement_summary": settlement_summary,
              "revalidation_summary": revalidation_summary, "price_refresh": price_refresh, "board_scorecard": scorecard_summary,
              "evidence_queue": manual_summary, "league_pull": league_summary, "morning_refresh": refresh_summary, "keep_awake": keep_awake_summary, "official_rosters": {k: v for k, v in roster_summary.items() if v not in ("CURRENT", "FETCHED")} or {"checked": len(roster_summary)}, "builder_pool": builder_summary, "product_state": product_summary, "slate_board": board_summary, "starter_feed": df_summary, "learning_cohort": learning_summary, "eggy_pick": eggy_summary}
    from operational import ingestion_health
    ingestion_health.record_run("real_parlay_paper_trader", {
        "eastern_date": today_et, "newly_staked": stake_summary["newly_recorded"],
        "qualifying_parlays_found": stake_summary["qualifying_tickets_found"],
        "insufficient_funds": stake_summary["insufficient_funds"], "status": "SUCCESS"})
    settled_count = (settlement_summary or {}).get("settled", 0)
    if (stake_summary["newly_recorded"] > 0 or settled_count > 0 or stake_summary["state_changed"]
            or revalidation_summary.get("alerts_recorded") or manual_summary.get("processed") or df_summary.get("written") or _publish_heartbeat_due(now)
            or _learning_changed(learning_summary)):
        from operational import cloud_publish_hook
        result["cloud_publish"] = cloud_publish_hook.publish_after("real_parlay_paper_trader")
    return result


def _learning_changed(summary) -> bool:
    """True when the learning cohort recorded or settled something this cycle (defensive: the summary's shape never matters to the model book's publish decision)."""
    try:
        rec, st, ps = summary.get("recording"), summary.get("settlement"), summary.get("parlay_settlement")
        return bool((isinstance(rec, dict) and rec.get("recorded_this_cycle")) or (isinstance(st, dict) and st.get("settled")) or (isinstance(ps, dict) and ps.get("settled")))
    except AttributeError:
        return False


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
