"""
Real Parlay Paper Trader (Platform Recovery block, 2026-09-29). The
missing link between "a real, qualifying cross-game parlay exists" and "a
real $10 paper bet is actually staked, tracked, and later settled."

Before this block, operational/paper_bankroll.py::create_real_market_combo_paper_bet()
and operational/paper_bet_settlement_driver.py::settle_due_bets() both
existed, real and already tested, but neither was ever called against the
real operational/paper_bankroll.db by any scheduled job -- the only caller
of the former was research/real_market_parlay/manual_real_slate_exercise.py,
an explicit certification-only exercise that writes to an isolated temp-file
database by design and must never touch production. A real, correct
paper-betting engine that nothing in production ever calls produces exactly
zero real rows in Paper Performance -- this module is the fix.

Sourcing legs: the SAME real_slate_adapter.py + research.real_market_parlay.engine
calls dashboard/real_today_view.py::build_real_today_state() itself makes
(never re-derived, never a second eligibility system) -- what the Today page
shows a viewer and what this module stakes a real $10 paper bet against are
always the same real, qualifying parlay.

Idempotent per Eastern calendar day (operational/eastern_time.py, matching
the real NHL hockey-day semantics every other real-data section on Today
already uses): if a REAL_MARKET_PAPER combo bet already exists for today's
Eastern date, staking is skipped this run -- restart-safe and duplicate-safe,
matching every other daily job in this project's established pattern.
record_paper_bet()'s own idempotency_key additionally guarantees the exact
same qualifying leg set is never staked twice even across a day boundary.

Settlement runs every invocation regardless of whether a new bet was staked
this run, across every track (operational/paper_bet_settlement_driver.py's
own generic, already-correct WIN/LOSS/VOID/UNRESOLVED logic) -- so DEMO_PAPER
and GAME_PARLAY_PAPER bets that were ALSO never settled by any scheduled job
get the same fix as a direct consequence of finally wiring this driver in,
not a separate, invented settlement rule.

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


def _already_staked_today(bankroll_conn, today_et: str) -> bool:
    for row in pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True):
        if et.eastern_today(dt.datetime.fromisoformat(row["created_at_utc"].replace("Z", "+00:00"))) == today_et:
            return True
    return False


def _build_todays_real_parlay(nhl_conn, now: dt.datetime) -> dict:
    moneyline_legs, _ = adapter.moneyline_candidate_legs(nhl_conn, now=now)
    sog_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0, now=now)
    sog_legs, _ = adapter.sog_alternate_candidate_legs(nhl_conn, sog_payloads, now=now)
    return rmp.build_real_market_parlay(moneyline_legs + sog_legs)


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
        if _already_staked_today(bankroll_conn, today_et):
            stake_result = {"status": "ALREADY_STAKED_TODAY", "eastern_date": today_et}
        else:
            parlay_result = _build_todays_real_parlay(nhl_conn, now)
            if parlay_result["status"] == "QUALIFIED":
                game_ids = {l.game_id for l in parlay_result["combo"].legs}
                event_start_utc = _earliest_scheduled_start(nhl_conn, game_ids)
                bet = pb.create_real_market_combo_paper_bet(bankroll_conn, parlay_result,
                                                             event_start_utc=event_start_utc)
                stake_result = {"status": bet["status"], "paper_bet_id": bet.get("paper_bet_id"),
                                 "eastern_date": today_et, "recommended_legs": parlay_result["recommended_legs"]}
            else:
                stake_result = {"status": "NO_QUALIFYING_PARLAY", "eastern_date": today_et,
                                 "reason": parlay_result.get("reason")}
        settlement_summary = settlement.settle_due_bets(bankroll_conn, nhl_conn)
    finally:
        nhl_conn.close()
        bankroll_conn.close()
    return {"stake_result": stake_result, "settlement_summary": settlement_summary}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
