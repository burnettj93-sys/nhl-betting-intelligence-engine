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

import db
from operational import eastern_time as et
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as settlement
from operational.real_prop_orchestrator import _recent_archive_payloads
from research.live_sog_pricing import market_parser
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import real_slate_adapter as adapter

MAX_PARLAYS_PER_DAY = 5


def _build_todays_real_parlays(nhl_conn, now: dt.datetime) -> dict:
    moneyline_legs, _ = adapter.moneyline_candidate_legs(nhl_conn, now=now)
    sog_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0, now=now)
    sog_legs, _ = adapter.sog_alternate_candidate_legs(nhl_conn, sog_payloads, now=now)
    return rmp.build_top_real_market_parlays(moneyline_legs + sog_legs, max_parlays=MAX_PARLAYS_PER_DAY)


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
    try:
        parlays_result = _build_todays_real_parlays(nhl_conn, now)
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
        }
        if parlays_result["status"] != "QUALIFIED":
            stake_summary["status"] = "NO_QUALIFYING_PARLAY"
            stake_summary["reason"] = parlays_result.get("reason")
        settlement_summary = settlement.settle_due_bets(bankroll_conn, nhl_conn)
    finally:
        nhl_conn.close()
        bankroll_conn.close()
    return {"stake_result": stake_summary, "settlement_summary": settlement_summary}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
