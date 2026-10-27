"""The legacy primary slip is RETIRED (operational/daily_tickets.LEGACY_PRIMARY_SLIP_RECORDING): it creates no new automatic paper bet; its history still reads and settles; the learning parlays, Eggy's flagship,
and the daily postmortem are unaffected. (The test package turns the old recording machinery ON for its own tests, tests/__init__.py; these tests turn it off, as production runs.)"""
from __future__ import annotations

import datetime as dt
import inspect
import os
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import daily_tickets as dtk
from operational import eggy_candidates as ec
from operational import learning_cohort as lc
from operational import paper_bankroll as pb
from operational import real_parlay_paper_trader as trader
from tests import test_eggy_official as eo
from tests import test_learning_parlays as tlp
from tests.test_real_parlay_paper_trader import GAMES, NOW1, _REAL_GET_CONN, _REAL_PB_INIT_DB, _bankroll, _fresh_nhl_db_with_games, _legs, _run_with


def _off():
    return mock.patch.object(dtk, "LEGACY_PRIMARY_SLIP_RECORDING", False)


def _on():
    return mock.patch.object(dtk, "LEGACY_PRIMARY_SLIP_RECORDING", True)


class TestRetiredInProduction(unittest.TestCase):
    def test_the_production_default_is_off_and_only_the_explicit_variable_turns_it_on(self):
        src = inspect.getsource(dtk)
        self.assertIn('os.environ.get("NHL_ENGINE_LEGACY_PRIMARY_RECORDING") == "1"', src)
        self.assertEqual(os.environ.get("NHL_ENGINE_LEGACY_PRIMARY_RECORDING"), "1")                      # set by tests/__init__.py only

    def test_a_trader_cycle_creates_no_new_legacy_paper_bet(self):
        nhl, bank = _fresh_nhl_db_with_games(GAMES), _bankroll()
        with _off():
            out = _run_with(nhl, bank, _legs(8), NOW1)
        self.assertEqual(out["stake_result"]["newly_recorded"], 0)
        self.assertIn("retired", out["stake_result"]["reason"])
        conn = pb.init_db(Path(bank))
        self.assertEqual(pb.query_paper_bets(conn, track="REAL_MARKET_PAPER"), [])
        self.assertEqual(pb.account_state(conn, "REAL_MARKET_PAPER")["available_cash"], 500.0)

    def test_the_recording_path_itself_refuses_when_called_directly(self):
        with _off():
            self.assertEqual(dtk.record_tickets(None, ["anything"], NOW1)[0]["status"], "RETIRED_LEGACY")


class TestHistoryStillReadsAndSettles(unittest.TestCase):
    def test_open_legacy_records_are_read_and_settled_after_the_retirement(self):
        nhl, bank = _fresh_nhl_db_with_games(GAMES), _bankroll()
        with _on():
            first = _run_with(nhl, bank, _legs(8), NOW1)
        self.assertEqual(first["stake_result"]["newly_recorded"], 5)
        c = _REAL_GET_CONN(nhl)                                                                           # the games finish; every player took 4 shots
        c.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=1")
        for i in range(1, 9):
            c.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, hits, blocked_shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)", (i, f"P{i}", "TOR", 18.0, 0, 0, 4, None, None, 1, "2026-09-29T23:30:00Z", "2026-09-29T23:30:00Z", "test"))
        c.commit()
        c.close()
        with _off():
            later = _run_with(nhl, bank, _legs(8), dt.datetime(2026, 9, 30, 5, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(later["stake_result"]["newly_recorded"], 0)
        self.assertEqual(later["settlement_summary"]["settled"], 5)
        conn = pb.init_db(Path(bank))
        rows = pb.query_paper_bets(conn, track="REAL_MARKET_PAPER", is_combo=True)
        self.assertEqual((len(rows), {r["result_status"] for r in rows}), (5, {"WIN"}))                   # history kept, none added, none rewritten
        self.assertEqual(len(dtk.recorded_today(conn, "2026-09-29")), 5)


class TestLearningParlaysStillRecord(tlp.TestTheCohortRecordsParlaysNotStraightBets):
    """The same parlay-recording tests, run with the legacy slip retired."""
    def setUp(self):
        super().setUp()
        p = _off()
        p.start()
        self.addCleanup(p.stop)

    def test_the_cohort_does_not_depend_on_the_legacy_switch(self):
        self.assertNotIn("LEGACY_PRIMARY_SLIP_RECORDING", inspect.getsource(lc))
        self.cycle(self.legs())
        self.assertTrue(self.parlays())


class TestEggyFlagshipUnchanged(eo.TestOfficialOutput):
    """Eggy's official flagship behaviour, run again with the legacy slip retired."""
    def setUp(self):
        p = _off()
        p.start()
        self.addCleanup(p.stop)

    def test_the_retired_flagship_parlay_and_primary_slip_digests_are_recorded_as_replaced_and_are_not_the_official_digest(self):
        rep = ec.OFFICIAL_POLICY["replaces_as_official_output"]
        self.assertEqual(rep["flagship_parlay_digest"], "af5ed8907d646212")
        self.assertEqual(rep["primary_slip_policy_digest"], "1fad3ba03503cf94")
        self.assertNotIn(ec.official_policy_digest(), ("af5ed8907d646212", "1fad3ba03503cf94"))


class TestPostmortemAndSettlementAreAutomatic(unittest.TestCase):
    def test_the_trader_cycle_still_runs_the_cohort_which_settles_and_refreshes_the_postmortem(self):
        src = inspect.getsource(trader)
        self.assertIn("learning_cohort.run_cycle", src)
        self.assertTrue(trader._learning_changed({"parlay_settlement": {"settled": 1}}))
        run = inspect.getsource(lc.run_cycle)
        self.assertIn("settle_parlays", run)
        self.assertIn("_refresh_postmortem", run)
        self.assertNotIn("LEGACY_PRIMARY_SLIP_RECORDING", run)


if __name__ == "__main__":
    unittest.main()
