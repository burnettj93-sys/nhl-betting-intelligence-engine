"""
Real Parlay Paper Trader (Platform Recovery block, 2026-09-29; extended to
several independent parlays, Owner Escalation block, 2026-09-30). The
missing link between "a real, qualifying cross-game parlay exists" and "a
real $10 paper bet is actually staked, tracked, and later settled."

Before the first version of this block, operational/paper_bankroll.py::
create_real_market_combo_paper_bet() and operational/paper_bet_settlement_driver.py::
settle_due_bets() both existed, real and already tested, but neither was
ever called against the real operational/paper_bankroll.db by any scheduled
job. That first version then only ever staked the SINGLE best parlay of the
day -- correct as a first step, but not what was actually being asked for:
several independent parlay tickets a day, each getting its own $10 stake,
not one ticket using up to 4 legs. This version stakes EVERY independent
qualifying parlay the day's real eligible legs support
(research/real_market_parlay/engine.py::build_top_real_market_parlays(),
capped at MAX_PARLAYS_PER_DAY), never just the first one found and never
more than the real pool genuinely supports.

Sourcing legs: the SAME real_slate_adapter.py + research.real_market_parlay.engine
calls dashboard/real_today_view.py::build_real_today_state() itself makes
(never re-derived, never a second eligibility system) -- what the Today page
shows a viewer and what this module stakes real $10 paper bets against are
always the same real, qualifying parlays.

Idempotent per Eastern calendar day AND per exact leg combination
(operational/paper_bankroll.py::create_real_market_combo_paper_bet()'s own
date-scoped idempotency key) -- re-running this same day never double-stakes
any of the day's parlays, restart-safe, but a genuinely new day is never
blocked just because the same players/thresholds happened to look best
again.

Settlement runs every invocation regardless of how many new bets were
staked this run, across every track (operational/paper_bet_settlement_driver.py's
own generic, already-correct WIN/LOSS/VOID/UNRESOLVED logic).

Run: python3 -m operational.real_parlay_paper_trader
"""
from __future__ import annotations

import datetime as dt
import fcntl

import db
from operational import bet_revalidation
from operational import eastern_time as et
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as settlement
from operational import state_paths as _sp
from operational.real_prop_orchestrator import _recent_archive_payloads
from research.live_sog_pricing import market_parser
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import real_slate_adapter as adapter

MAX_PARLAYS_PER_DAY = 5

LOCK_PATH = _sp.path("real_parlay_paper_trader.lock", area="operational")


def _build_todays_real_parlays(nhl_conn, bankroll_conn, now: dt.datetime) -> dict:
    """Production Gap Closure sprint (2026-09-30): the daily cap and leg/
    game exclusivity must hold against what's ALREADY PERSISTED, not just
    what this one call happens to build in memory -- otherwise a second
    run() later the same ET day (the exact audited reproduction) can stake
    past MAX_PARLAYS_PER_DAY and reuse legs/games a prior run already
    committed. today's_usage reads the real bankroll DB for exactly that."""
    today_et = et.eastern_today(now)
    usage = pb.todays_real_parlay_usage(bankroll_conn, today_et)
    remaining_budget = max(0, MAX_PARLAYS_PER_DAY - usage["count"])
    if remaining_budget == 0:
        return {"status": "NO_QUALIFYING_PARLAY",
                "reason": f"daily cap of {MAX_PARLAYS_PER_DAY} parlay(s) already reached for {today_et}"}

    moneyline_legs, _ = adapter.moneyline_candidate_legs(nhl_conn, now=now)
    sog_alt_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0, now=now)
    sog_alt_legs, _ = adapter.sog_alternate_candidate_legs(nhl_conn, sog_alt_payloads, now=now)
    # Standard SOG/Saves Certification block (2026-10-01): the real,
    # certified standard (two-sided) PLAYER_SOG shape is now also a real
    # leg source -- unlike the one-sided alternate ladder above, it can
    # clear a genuine two-sided no-vig edge (see real_slate_adapter.py::
    # sog_standard_candidate_legs()'s own docstring). GOALIE_SAVES is
    # wired in too for architectural completeness, but structurally
    # produces zero real legs today: every real Saves quote is excluded
    # upstream (STARTER_NOT_CONFIRMED) by the real, deliberately-preserved
    # starter-certainty gate -- see goalie_saves_candidate_legs()'s own
    # docstring for why that is correct, not a bug to route around.
    sog_std_payloads = _recent_archive_payloads(market_parser.STANDARD_MARKET_KEY, max_age_hours=24.0, now=now)
    sog_std_legs, _ = adapter.sog_standard_candidate_legs(nhl_conn, sog_std_payloads, now=now)
    saves_payloads = _recent_archive_payloads(market_parser.SAVES_MARKET_KEY, max_age_hours=24.0, now=now)
    saves_legs, _ = adapter.goalie_saves_candidate_legs(nhl_conn, saves_payloads, now=now)
    all_legs = moneyline_legs + sog_alt_legs + sog_std_legs + saves_legs

    # Cross-RUN exclusivity (the engine's own dedup/game-exclusivity in
    # research/real_market_parlay/engine.py only sees legs WITHIN this one
    # call): never offer a leg whose game or exact economic identity a
    # prior run today already committed to a persisted ticket.
    candidates = [l for l in all_legs if l.game_id not in usage["used_game_ids"]
                  and (l.game_id, l.participant_id, l.market_family, l.threshold) not in usage["used_leg_keys"]]
    return rmp.build_top_real_market_parlays(candidates, max_parlays=remaining_budget)


def _earliest_scheduled_start(nhl_conn, game_ids: set[str]) -> str | None:
    starts = []
    for game_id in game_ids:
        row = nhl_conn.execute("SELECT scheduled_start_utc FROM games WHERE game_id = ?", (game_id,)).fetchone()
        if row and row["scheduled_start_utc"]:
            starts.append(row["scheduled_start_utc"])
    return min(starts) if starts else None


def run(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    today_et = et.eastern_today(now)
    nhl_conn = db.get_conn()
    bankroll_conn = pb.init_db()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        # Production Gap Closure sprint (2026-09-30): read-usage -> build ->
        # stake is a single bounded critical section. Without this lock, two
        # concurrent runs could both read "0 staked today" before either
        # writes anything, and each independently stake up to
        # MAX_PARLAYS_PER_DAY -- the exact TOCTOU version of the audited
        # 3+3=6 defect, just from concurrency instead of two sequential runs.
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"stake_result": {"eastern_date": today_et, "status": "SKIPPED",
                                      "reason": "ANOTHER_TRADER_RUN_IN_PROGRESS"},
                    "settlement_summary": None}

        # Bet Re-Validation block (2026-10-01): "I want bets to be
        # reevaluated at every pull" -- re-check every already-staked,
        # not-yet-started real-market bet against the CURRENT real state
        # (schedule/injury/goalie/trade/suspension) BEFORE building today's
        # new parlays, so a genuinely invalidated bet is voided rather than
        # left to settle on stale information.
        revalidation_summary = bet_revalidation.revalidate_pending_real_market_bets(
            bankroll_conn, nhl_conn, now)

        parlays_result = _build_todays_real_parlays(nhl_conn, bankroll_conn, now)
        stake_results = []
        if parlays_result["status"] == "QUALIFIED":
            for entry in parlays_result["parlays"]:
                single = {"status": "QUALIFIED", "recommended_legs": entry["recommended_legs"],
                          "combo": entry["combo"]}
                game_ids = {l.game_id for l in entry["combo"].legs}
                event_start_utc = _earliest_scheduled_start(nhl_conn, game_ids)
                bet = pb.create_real_market_combo_paper_bet(bankroll_conn, single,
                                                             event_start_utc=event_start_utc,
                                                             created_at_utc=now.isoformat())
                stake_results.append({"status": bet["status"], "paper_bet_id": bet.get("paper_bet_id"),
                                       "recommended_legs": entry["recommended_legs"]})
        stake_summary = {
            "eastern_date": today_et,
            "qualifying_parlays_found": len(parlays_result.get("parlays", [])),
            "newly_staked": sum(1 for r in stake_results if r["status"] == "INSERTED"),
            "already_staked": sum(1 for r in stake_results if r["status"] == "DUPLICATE"),
            "results": stake_results,
            # Precise independence claim (Production Gap Closure sprint,
            # 2026-09-30): "no identical legs reused" is not the same fact
            # as "these tickets are independent." Every ticket's own legs
            # are cross-game by construction, AND research/real_market_parlay/
            # engine.py::build_top_real_market_parlays() now also excludes
            # any game already used by an earlier ticket THE SAME DAY (both
            # within this run and against prior runs' persisted tickets via
            # todays_real_parlay_usage above) -- so today's tickets, plural,
            # never share a game with each other either.
            "independence_note": "Each ticket's legs are drawn from different games (cross-game only). "
                                  "No game is shared across two of today's tickets, so today's tickets "
                                  "are mutually independent, not merely free of duplicate legs.",
        }
        if parlays_result["status"] != "QUALIFIED":
            stake_summary["status"] = "NO_QUALIFYING_PARLAY"
            stake_summary["reason"] = parlays_result.get("reason")
        settlement_summary = settlement.settle_due_bets(bankroll_conn, nhl_conn)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()
        nhl_conn.close()
        bankroll_conn.close()

    result = {"stake_result": stake_summary, "settlement_summary": settlement_summary,
              "revalidation_summary": revalidation_summary}
    # Production Gap Closure sprint (2026-09-30): this job runs every 15
    # minutes via launchd (deploy/launchd/com.nhlengine.real-parlay-paper-
    # trader.plist) but, unlike every other scheduled job in this project,
    # never recorded its own health or published changed state downstream
    # -- a cloud viewer had to wait on an UNRELATED job (odds pull,
    # settlement, postmortem) to happen to publish before a real parlay
    # stake or settlement outcome this job just produced became visible.
    # Mirrors settle_daily_observations.py's own established
    # record_run()-then-publish_after() pattern exactly; reaching this
    # line at all means the run completed without raising, so it is
    # always a real SUCCESS to record.
    from operational import ingestion_health
    ingestion_health.record_run("real_parlay_paper_trader", {**stake_summary, "status": "SUCCESS"})
    newly_staked = stake_summary.get("newly_staked", 0)
    settled_count = (settlement_summary or {}).get("settled", 0)
    if newly_staked > 0 or settled_count > 0:
        from operational import cloud_publish_hook
        result["cloud_publish"] = cloud_publish_hook.publish_after("real_parlay_paper_trader")
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
