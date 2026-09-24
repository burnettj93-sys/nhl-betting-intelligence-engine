"""
Real Recommendation Pipeline block (2026-09-24), Parts 14-16: tests for
the automated closing-price lookup wired into
operational/settle_daily_observations.py -- closing CLOSED_LOOP_
CERTIFICATION's previously-PARTIAL CLV gap for MONEYLINE, the one market
with real archived DraftKings price history in odds_snapshots.

Writes NO new CLV math -- these tests exercise the real, unmodified
operational.clv_resolver.find_closing_price/compute_clv through the new
operational.closing_price_lookup bridge and the settlement wiring itself.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import db
from operational import closing_price_lookup
from operational import prospective_ledger as pl
from operational import settle_daily_observations as sdo
from pricing import odds_math


def _fresh_official_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for team in ("EDM", "CHI"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (team, team))
    conn.commit()
    return conn


def _insert_game(conn, game_id, game_state="FINAL", home_score=4, away_score=2):
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state,
           home_score, away_score, final_period_type, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "20262027", "2026-10-15", "EDM", "CHI", game_state, home_score, away_score,
         "REG", "test_fixture"))
    conn.commit()


def _insert_real_odds_snapshot(conn, game_id, selection, price, captured_at_utc,
                                data_provider="the-odds-api"):
    conn.execute(
        """INSERT INTO odds_snapshots (game_id, sportsbook, data_provider, market, selection,
           event_start_utc, price_american, status, captured_at_utc, received_at_utc)
           VALUES (?, 'DraftKings', ?, 'MONEYLINE', ?, ?, ?, 'ACTIVE', ?, ?)""",
        (game_id, data_provider, selection, PAST_EVENT, price, captured_at_utc, captured_at_utc))
    conn.commit()


_YESTERDAY = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
PAST_EVENT = _YESTERDAY.strftime("%Y-%m-%dT23:00:00.000000Z")
PAST_CUTOFF = _YESTERDAY.strftime("%Y-%m-%dT18:00:00.000000Z")
BEFORE_CLOSE = _YESTERDAY.strftime("%Y-%m-%dT22:00:00.000000Z")
AT_OR_AFTER_START = _YESTERDAY.strftime("%Y-%m-%dT23:30:00.000000Z")


def _record_moneyline_observation(ledger_conn, prediction_id, side="EDM", entry_odds=-150, game_id=1):
    return pl.record_model_observation(
        ledger_conn, prediction_id=prediction_id, event_start_utc=PAST_EVENT,
        created_at_utc=PAST_CUTOFF, prediction_cutoff_utc=PAST_CUTOFF, game_id=game_id,
        game_date="2026-10-15", team=side, opponent="CHI" if side == "EDM" else "EDM",
        market_id="MONEYLINE", side=side, raw_probability=0.6, conservative_probability=0.55,
        odds_american=entry_odds, sportsbook="DraftKings")


class TestClosingPriceLookupPureFunction(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_official_db()
        _insert_game(self.conn, 1)

    def tearDown(self):
        self.conn.close()

    def test_resolves_the_real_latest_snapshot_strictly_before_event_start(self):
        _insert_real_odds_snapshot(self.conn, 1, "EDM", -140, PAST_CUTOFF)
        _insert_real_odds_snapshot(self.conn, 1, "EDM", -160, BEFORE_CLOSE)
        result = closing_price_lookup.resolve_real_moneyline_closing_price(
            self.conn, game_id=1, selection="EDM", event_start_utc=PAST_EVENT)
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["closing_odds"], -160)  # the LATER of the two valid snapshots
        self.assertEqual(result["closing_captured_at_utc"], BEFORE_CLOSE)

    def test_a_snapshot_at_or_after_event_start_is_never_used_as_the_close(self):
        _insert_real_odds_snapshot(self.conn, 1, "EDM", -140, PAST_CUTOFF)
        _insert_real_odds_snapshot(self.conn, 1, "EDM", -200, AT_OR_AFTER_START)  # post-start, invalid
        result = closing_price_lookup.resolve_real_moneyline_closing_price(
            self.conn, game_id=1, selection="EDM", event_start_utc=PAST_EVENT)
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["closing_odds"], -140)

    def test_no_real_snapshot_at_all_is_clv_not_available(self):
        result = closing_price_lookup.resolve_real_moneyline_closing_price(
            self.conn, game_id=1, selection="EDM", event_start_utc=PAST_EVENT)
        self.assertEqual(result["status"], "CLV_NOT_AVAILABLE")


class TestSettlementCLVIntegration(unittest.TestCase):
    def setUp(self):
        self.ledger = pl.init_db(db_path=":memory:")
        self.official = _fresh_official_db()

    def test_win_settlement_populates_real_closing_odds_and_clv(self):
        _insert_game(self.official, 1, home_score=4, away_score=2)  # EDM (home) wins
        _insert_real_odds_snapshot(self.official, 1, "EDM", -160, BEFORE_CLOSE)
        _record_moneyline_observation(self.ledger, "pred-1", side="EDM", entry_odds=-150)

        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_win"], 1)
        row = pl.get_observation(self.ledger, "pred-1")
        self.assertEqual(row["closing_odds"], -160)
        self.assertEqual(row["closing_captured_at_utc"], BEFORE_CLOSE)
        expected_clv = odds_math.american_to_prob(-160) - odds_math.american_to_prob(-150)
        self.assertAlmostEqual(row["clv"], expected_clv, places=9)

    def test_no_valid_close_settles_with_clv_left_unavailable_not_zero(self):
        _insert_game(self.official, 1, home_score=4, away_score=2)
        _record_moneyline_observation(self.ledger, "pred-1", side="EDM", entry_odds=-150)
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_win"], 1)
        row = pl.get_observation(self.ledger, "pred-1")
        self.assertIsNone(row["closing_odds"])
        self.assertIsNone(row["clv"])  # never fabricated as 0.0

    def test_loss_settlement_also_gets_real_clv(self):
        _insert_game(self.official, 1, home_score=1, away_score=5)  # EDM loses
        _insert_real_odds_snapshot(self.official, 1, "EDM", -140, BEFORE_CLOSE)
        _record_moneyline_observation(self.ledger, "pred-1", side="EDM", entry_odds=-150)
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_loss"], 1)
        row = pl.get_observation(self.ledger, "pred-1")
        self.assertEqual(row["closing_odds"], -140)
        self.assertIsNotNone(row["clv"])

    def test_repeated_settlement_never_recomputes_or_double_settles(self):
        _insert_game(self.official, 1, home_score=4, away_score=2)
        _insert_real_odds_snapshot(self.official, 1, "EDM", -160, BEFORE_CLOSE)
        _record_moneyline_observation(self.ledger, "pred-1", side="EDM", entry_odds=-150)
        first = sdo.run_settlement_batch(self.ledger, self.official)
        second = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(first["settled_win"], 1)
        self.assertEqual(second["total_candidates"], 0)

    def test_non_moneyline_observation_is_unaffected_by_closing_price_lookup(self):
        # A PLAYER_SOG observation must never be routed through the
        # MONEYLINE-only closing-price lookup.
        _insert_game(self.official, 1, home_score=4, away_score=2)
        self.official.execute(
            """INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals,
               assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (1,'P1','EDM',18.0,0,0,5,1,1,?,?,'test')""", (PAST_CUTOFF, PAST_CUTOFF))
        self.official.commit()
        pl.record_model_observation(
            self.ledger, prediction_id="pred-sog", event_start_utc=PAST_EVENT, created_at_utc=PAST_CUTOFF,
            prediction_cutoff_utc=PAST_CUTOFF, game_id=1, game_date="2026-10-15", player_id="P1",
            team="EDM", opponent="CHI", market_id="PLAYER_SOG", threshold="3+", raw_probability=0.5)
        summary = sdo.run_settlement_batch(self.ledger, self.official)
        self.assertEqual(summary["settled_win"], 1)
        row = pl.get_observation(self.ledger, "pred-sog")
        self.assertIsNone(row["closing_odds"])


if __name__ == "__main__":
    unittest.main()
