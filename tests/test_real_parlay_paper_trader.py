"""
Platform Recovery block (2026-09-29), extended to several independent
parlays (Owner Escalation block, 2026-09-30): proves EVERY independent
qualifying parlay the day's real eligible legs support is staked as its
own real $10 paper bet -- not just the single best one -- staking is
idempotent per Eastern day AND per exact leg combination (a real day never
gets silently blocked from staking a fresh set of parlays just because one
already went in), and settlement (previously "manual invocation only") now
runs and correctly resolves a real WIN, feeding bankroll_summary exactly as
Paper Performance and the daily postmortem already read it.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import paper_bankroll as pb
from operational import real_parlay_paper_trader as trader
from research.real_market_parlay.engine import ParlayLeg

_REAL_GET_CONN = db.get_conn
_REAL_PB_INIT_DB = pb.init_db


def _leg(game_id, market_family="MONEYLINE", threshold=None, participant_id="TOR",
         conservative_probability=0.90, american_price=-150, side="HOME"):
    return ParlayLeg(
        game_id=game_id, event_id=f"evt-{game_id}", market_family=market_family,
        participant_id=participant_id, participant_name=participant_id, side=side, threshold=threshold,
        american_price=american_price, conservative_probability=conservative_probability,
        sportsbook="draftkings", captured_at_utc="2026-09-29T18:00:00Z",
        provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
        price_fresh=True, event_not_started=True)


def _fresh_nhl_db_with_games(games: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for g in games:
        for t in (g["home"], g["away"]):
            conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
        conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, home_score, away_score, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (g["game_id"], "20262027", g["date"], g["start"], g["home"], g["away"], g["start"],
             g.get("state", "SCHEDULED"), g.get("home_score"), g.get("away_score"), "test"))
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _run_with(nhl_path, bankroll_path, legs, now):
    with mock.patch.object(db, "get_conn", lambda: _REAL_GET_CONN(nhl_path)), \
         mock.patch.object(pb, "init_db", lambda: _REAL_PB_INIT_DB(Path(bankroll_path))), \
         mock.patch("research.real_market_parlay.real_slate_adapter.moneyline_candidate_legs",
                    return_value=(legs, [])), \
         mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                    return_value=([], [])), \
         mock.patch.object(trader, "_recent_archive_payloads", return_value=[]):
        return trader.run(now=now)


class TestQualifyingParlayIsStaked(unittest.TestCase):
    def test_a_qualifying_parlay_stakes_a_real_ten_dollar_bet(self):
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR"},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN"},
        ])
        legs = [_leg("1", conservative_probability=0.90), _leg("2", conservative_probability=0.90),
                _leg("3", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        result = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["qualifying_parlays_found"], 1)
        self.assertEqual(result["stake_result"]["newly_staked"], 1)
        self.assertEqual(result["stake_result"]["results"][0]["status"], "INSERTED")
        self.assertEqual(result["stake_result"]["results"][0]["recommended_legs"], 3)

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stake"], 10.00)
        self.assertEqual(rows[0]["event_start_utc"], "2026-09-29T23:00:00")  # earliest of the 3 games

    def test_rerunning_the_same_eastern_day_never_double_stakes(self):
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR"},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN"},
        ])
        legs = [_leg("1", conservative_probability=0.90), _leg("2", conservative_probability=0.90),
                _leg("3", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        first = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        # 8:30 PM EDT later the same Eastern day -- UTC has already rolled to Sept 30.
        second = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 30, 0, 30, tzinfo=dt.timezone.utc))

        self.assertEqual(first["stake_result"]["newly_staked"], 1)
        # Production Gap Closure sprint (2026-09-30): the second run's candidate
        # legs are now filtered against what run 1 already persisted BEFORE the
        # engine ever runs (todays_real_parlay_usage) -- the same 3 games are
        # already used today, so nothing eligible remains to even attempt a
        # (now-impossible) duplicate stake. qualifying_parlays_found is 0
        # rather than "found 1, but it was a DUPLICATE," which is the more
        # correct fact: run 2 never rebuilds a ticket it has no legs left for.
        self.assertEqual(second["stake_result"]["newly_staked"], 0)
        self.assertEqual(second["stake_result"]["qualifying_parlays_found"], 0)
        self.assertEqual(second["stake_result"]["status"], "NO_QUALIFYING_PARLAY")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 1, "must never stake a second $10 bet for the same Eastern day")

    def test_no_qualifying_parlay_stakes_nothing(self):
        nhl_path = _fresh_nhl_db_with_games([])
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        result = _run_with(nhl_path, bankroll_tmp.name, [], dt.datetime.now(dt.timezone.utc))

        self.assertEqual(result["stake_result"]["status"], "NO_QUALIFYING_PARLAY")
        self.assertEqual(result["stake_result"]["qualifying_parlays_found"], 0)
        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        self.assertEqual(pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER"), [])


class TestSeveralIndependentParlaysAreAllStaked(unittest.TestCase):
    """Owner Escalation block (2026-09-30): the actual ask -- several
    parlay tickets a day, each its own $10 stake, not one ticket."""

    def test_every_independent_qualifying_parlay_gets_its_own_ten_dollar_stake(self):
        games = [{"game_id": i, "date": "2026-09-29", "start": f"2026-09-29T2{i % 4}:00:00",
                  "home": f"H{i}", "away": f"A{i}"} for i in range(1, 10)]
        nhl_path = _fresh_nhl_db_with_games(games)
        # Unique participant_id per leg -- real MONEYLINE legs on different
        # real games always have different teams; a shared identity would
        # only happen with unrealistic test data (the same team can't play
        # two real games the same real day).
        legs = [_leg(str(i), participant_id=f"T{i}", conservative_probability=0.90) for i in range(1, 10)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        result = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["qualifying_parlays_found"], 3)
        self.assertEqual(result["stake_result"]["newly_staked"], 3)

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 3, "each independent qualifying parlay must be its own real $10 bet")
        self.assertTrue(all(r["stake"] == 10.00 for r in rows))
        # Three genuinely different tickets, not the same combo three times.
        self.assertEqual(len({r["market_id"] for r in rows}), 3)

    def test_never_stakes_more_than_the_real_pool_supports(self):
        # Only 4 real legs -- enough for exactly one 3-leg parlay, never
        # padded up toward the 5-parlay cap.
        games = [{"game_id": i, "date": "2026-09-29", "start": "2026-09-29T23:00:00",
                  "home": f"H{i}", "away": f"A{i}"} for i in range(1, 5)]
        nhl_path = _fresh_nhl_db_with_games(games)
        legs = [_leg(str(i), conservative_probability=0.90) for i in range(1, 5)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        result = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["qualifying_parlays_found"], 1)

    def test_a_new_eastern_day_can_stake_fresh_parlays_even_if_yesterdays_are_identical(self):
        # The date-scoped idempotency key must never permanently block a
        # combo that happens to look best again on a genuinely later day.
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL"},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR"},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN"},
        ])
        legs = [_leg("1", conservative_probability=0.90), _leg("2", conservative_probability=0.90),
                _leg("3", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        day1 = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        day2 = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 10, 1, 18, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(day1["stake_result"]["newly_staked"], 1)
        self.assertEqual(day2["stake_result"]["newly_staked"], 1, "a genuinely new day must not be blocked")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 2)


class TestFullLifecycleThroughSettlement(unittest.TestCase):
    def test_a_staked_parlay_settles_win_and_feeds_bankroll_summary(self):
        # Two already-FINAL games: TOR beat MTL (leg picks TOR -> WIN), BOS beat NYR (leg picks BOS -> WIN).
        nhl_path = _fresh_nhl_db_with_games([
            {"game_id": 1, "date": "2026-09-29", "start": "2026-09-29T23:00:00", "home": "TOR", "away": "MTL",
             "state": "FINAL", "home_score": 4, "away_score": 2},
            {"game_id": 2, "date": "2026-09-29", "start": "2026-09-29T23:30:00", "home": "BOS", "away": "NYR",
             "state": "FINAL", "home_score": 3, "away_score": 1},
            {"game_id": 3, "date": "2026-09-29", "start": "2026-09-30T00:00:00", "home": "EDM", "away": "VAN",
             "state": "FINAL", "home_score": 5, "away_score": 2},
        ])
        legs = [_leg("1", participant_id="TOR", side="HOME", conservative_probability=0.90),
                _leg("2", participant_id="BOS", side="HOME", conservative_probability=0.90),
                _leg("3", participant_id="EDM", side="HOME", conservative_probability=0.90)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        result = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 30, 3, 0, tzinfo=dt.timezone.utc))

        self.assertEqual(result["stake_result"]["newly_staked"], 1)
        self.assertEqual(result["settlement_summary"]["settled"], 1)
        self.assertEqual(result["settlement_summary"]["results"][0]["status"], "WIN")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        summary = pb.bankroll_summary(bankroll_conn, "REAL_MARKET_PAPER")
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 0)
        self.assertGreater(summary["net_profit"], 0.0)
        self.assertEqual(summary["hit_rate"], 1.0)


class TestCrossRunDailyCapAndExclusivity(unittest.TestCase):
    """Production Gap Closure sprint (2026-09-30): the exact audited
    reproduction -- 3 tickets on one run, then 3 more later the same ET
    day, must never exceed MAX_PARLAYS_PER_DAY=5 total, and a later run
    must never reuse a game or leg an earlier run already staked."""

    def test_a_second_run_the_same_day_is_capped_by_what_the_first_run_already_staked(self):
        # 9 real games/legs -- enough for 3 fresh tickets on their own, but
        # run 1 has already staked 3 tickets (9 legs) today, leaving a
        # budget of only 2 more (MAX_PARLAYS_PER_DAY=5 - 3 already staked).
        first_games = [{"game_id": i, "date": "2026-09-29", "start": f"2026-09-29T2{i % 4}:00:00",
                        "home": f"H{i}", "away": f"A{i}"} for i in range(1, 10)]
        second_games = [{"game_id": i, "date": "2026-09-29", "start": f"2026-09-29T2{i % 4}:00:00",
                         "home": f"H{i}", "away": f"A{i}"} for i in range(10, 19)]
        nhl_path = _fresh_nhl_db_with_games(first_games + second_games)
        first_legs = [_leg(str(i), participant_id=f"T{i}", conservative_probability=0.90) for i in range(1, 10)]
        second_legs = [_leg(str(i), participant_id=f"T{i}", conservative_probability=0.90) for i in range(10, 19)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        first = _run_with(nhl_path, bankroll_tmp.name, first_legs,
                           dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(first["stake_result"]["newly_staked"], 3)

        # Second run later the SAME ET day: a fresh, fully independent pool
        # of 9 NEW legs (different games) could support 3 more tickets on
        # its own -- but only 2 remain in today's budget (5 - 3).
        second = _run_with(nhl_path, bankroll_tmp.name, second_legs,
                            dt.datetime(2026, 9, 29, 22, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(second["stake_result"]["newly_staked"], 2,
                          "must be capped at the REMAINING daily budget, not the pool's own full capacity")

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 5, "3 (run 1) + 2 (run 2, capped) = exactly MAX_PARLAYS_PER_DAY, never 6")

    def test_a_second_run_never_reuses_a_game_the_first_run_already_staked(self):
        games = [{"game_id": i, "date": "2026-09-29", "start": "2026-09-29T23:00:00",
                  "home": f"H{i}", "away": f"A{i}"} for i in range(1, 4)]
        nhl_path = _fresh_nhl_db_with_games(games)
        legs = [_leg(str(i), conservative_probability=0.90) for i in range(1, 4)]
        bankroll_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        bankroll_tmp.close()

        first = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 18, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(first["stake_result"]["newly_staked"], 1)

        # Same 3 games offered again (as a fresh, independently-priced pool
        # would look if odds were re-quoted) -- must not be restaked.
        second = _run_with(nhl_path, bankroll_tmp.name, legs, dt.datetime(2026, 9, 29, 22, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(second["stake_result"]["newly_staked"], 0)
        self.assertEqual(second["stake_result"]["qualifying_parlays_found"], 0)

        bankroll_conn = pb.init_db(Path(bankroll_tmp.name))
        rows = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
