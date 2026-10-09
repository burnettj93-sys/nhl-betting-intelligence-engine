"""
Tests for operational/bet_revalidation.py (Bet Re-Validation block,
2026-10-01): "I want bets to be reevaluated at every pull" -- re-checking
an already-staked, not-yet-started REAL_MARKET_PAPER combo bet against the
CURRENT real state and voiding it if a genuine real-world change (schedule
delay, roster/goalie status change, trade) invalidates its premise.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import db
from operational import bet_revalidation as br
from operational import paper_bankroll as pb


def _fresh_nhl_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL", "BOS"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (1, '20262027', '2026-10-15', "
        "'2026-10-15T23:00:00', 'TOR', 'MTL', '2026-10-15T00:00:00', 'SCHEDULED', 'test')")
    conn.commit()
    return conn


def _fresh_bankroll_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return pb.init_db(Path(tmp.name))


def _leg(game_id="1", market_family="MONEYLINE", threshold=None, participant_id="TOR", side="HOME"):
    return {"game_id": game_id, "market_family": market_family, "threshold": threshold, "side": side,
            "participant_id": participant_id, "participant_name": participant_id}


def _stake(bankroll_conn, legs, created_at_utc="2026-10-15T18:00:00+00:00",
           event_start_utc="2026-10-15T23:00:00"):
    pb.record_paper_bet(
        bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
        market_id="REAL_MARKET_PARLAY:2026-10-15:x", entry_odds=150, is_combo=True,
        legs_json=json.dumps(legs), conservative_probability=0.75, edge=0.05,
        event_id="evt-1", created_at_utc=created_at_utc, event_start_utc=event_start_utc)
    return pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER")[0]


NOW = dt.datetime(2026, 10, 15, 20, 0, tzinfo=dt.timezone.utc)


class TestNoChangeLeavesTheBetAlone(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_untouched_real_state_leaves_the_bet_pending(self):
        _stake(self.bankroll_conn, [_leg()])
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["alerts_recorded"], 0)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")

    def test_already_started_events_are_never_touched(self):
        # event_start_utc in the past -- find_pending_future_event_bets()
        # must never pick this up (settlement's own job, not revalidation's).
        _stake(self.bankroll_conn, [_leg()], event_start_utc="2020-01-01T00:00:00")
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["checked"], 0)


class TestScheduleChangeVoids(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_a_real_schedule_revision_after_staking_alerts_without_voiding(self):
        bet = _stake(self.bankroll_conn, [_leg()], created_at_utc="2026-10-15T18:00:00+00:00")
        # A real schedule revision (e.g. a weather postponement) observed
        # AFTER the bet was staked -- puck drop genuinely moved.
        self.nhl_conn.execute(
            "INSERT INTO game_schedule_events (game_id, game_date, scheduled_start_utc, home_team, "
            "away_team, observed_at_utc, source) VALUES (1, '2026-10-16', '2026-10-16T00:00:00', "
            "'TOR', 'MTL', '2026-10-15T19:00:00', 'test')")
        self.nhl_conn.commit()
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")  # alert only, never voided/refunded
        alerts = pb.ticket_alerts(self.bankroll_conn)[row["paper_bet_id"]]
        self.assertIn("SCHEDULE_CHANGED", alerts[0]["kind"])

    def test_a_schedule_revision_before_staking_never_alerts_without_voiding(self):
        # The revision was already reflected BEFORE the bet was placed --
        # not new information, must not trigger a void.
        self.nhl_conn.execute(
            "INSERT INTO game_schedule_events (game_id, game_date, scheduled_start_utc, home_team, "
            "away_team, observed_at_utc, source) VALUES (1, '2026-10-15', '2026-10-15T23:00:00', "
            "'TOR', 'MTL', '2026-10-15T12:00:00', 'test')")
        self.nhl_conn.commit()
        _stake(self.bankroll_conn, [_leg()], created_at_utc="2026-10-15T18:00:00+00:00")
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 0)


class TestErrorsAndRepeatsNeverRefund(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_a_revalidation_error_is_an_alert_and_the_stake_stays_open(self):
        _stake(self.bankroll_conn, [_leg()])
        with mock.patch.object(br, "_invalidation_reasons_for_leg", side_effect=RuntimeError("boom")):
            summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")
        self.assertEqual(pb.account_state(self.bankroll_conn, "REAL_MARKET_PAPER")["open_stakes"], 10.0)
        self.assertEqual(pb.ticket_alerts(self.bankroll_conn)[row["paper_bet_id"]][0]["kind"], "REVALIDATION_ERROR")

    def test_repeating_the_same_finding_does_not_duplicate_alerts(self):
        _stake(self.bankroll_conn, [_leg()])
        with mock.patch.object(br, "_invalidation_reasons_for_leg", side_effect=RuntimeError("boom")):
            br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
            second = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(second["alerts_recorded"], 0)
        self.assertEqual(len(self.bankroll_conn.execute("SELECT * FROM ticket_alerts").fetchall()), 1)


class TestGoalieChangeVoidsMoneyline(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_a_real_changed_goalie_status_after_staking_alerts_without_voiding(self):
        _stake(self.bankroll_conn, [_leg(market_family="MONEYLINE", participant_id="TOR")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        self.nhl_conn.execute(
            "INSERT INTO goalie_status_events (game_id, team_id, player_id, status, effective_at_utc, "
            "observed_at_utc, source) VALUES (1, 'TOR', 'G1', 'CHANGED', '2026-10-15T19:00:00', "
            "'2026-10-15T19:00:00', 'test')")
        self.nhl_conn.commit()
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")  # alert only, never voided/refunded
        alerts = pb.ticket_alerts(self.bankroll_conn)[row["paper_bet_id"]]
        self.assertEqual(alerts[0]["kind"], "GOALIE_STATUS_CHANGED")


class TestRosterStatusVoidsPlayerLegs(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_a_real_injury_report_after_staking_alerts_without_voiding(self):
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        self.nhl_conn.execute(
            "INSERT INTO roster_status_events (player_id, team_id, status, effective_at_utc, "
            "observed_at_utc, source) VALUES ('P1', 'TOR', 'OUT', '2026-10-15T19:00:00', "
            "'2026-10-15T19:00:00', 'test')")
        self.nhl_conn.commit()
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")  # alert only, never voided/refunded
        alerts = pb.ticket_alerts(self.bankroll_conn)[row["paper_bet_id"]]
        self.assertEqual(alerts[0]["kind"], "ROSTER_STATUS_CHANGED")

    def test_a_suspended_goalie_alerts_without_voiding(self):
        _stake(self.bankroll_conn, [_leg(market_family="GOALIE_SAVES", threshold=25, participant_id="G1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        self.nhl_conn.execute(
            "INSERT INTO roster_status_events (player_id, team_id, status, effective_at_utc, "
            "observed_at_utc, source) VALUES ('G1', 'TOR', 'SUSPENDED', '2026-10-15T19:00:00', "
            "'2026-10-15T19:00:00', 'test')")
        self.nhl_conn.commit()
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)

    def test_a_status_already_known_at_staking_time_never_alerts_without_voiding(self):
        self.nhl_conn.execute(
            "INSERT INTO roster_status_events (player_id, team_id, status, effective_at_utc, "
            "observed_at_utc, source) VALUES ('P1', 'TOR', 'OUT', '2026-10-15T10:00:00', "
            "'2026-10-15T10:00:00', 'test')")
        self.nhl_conn.commit()
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 0)


class TestMultipleBetsOnlyAffectedOneVoids(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()
        self.nhl_conn.execute(
            "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
            "schedule_observed_at_utc, game_state, source) VALUES (2, '20262027', '2026-10-15', "
            "'2026-10-15T23:30:00', 'BOS', 'MTL', '2026-10-15T00:00:00', 'SCHEDULED', 'test')")
        self.nhl_conn.commit()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_only_the_bet_with_a_real_change_is_alerted(self):
        pb.record_paper_bet(
            self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
            market_id="REAL_MARKET_PARLAY:2026-10-15:a", entry_odds=150, is_combo=True,
            legs_json=json.dumps([_leg(game_id="1", participant_id="TOR")]),
            conservative_probability=0.75, edge=0.05, event_id="evt-1",
            created_at_utc="2026-10-15T18:00:00+00:00", event_start_utc="2026-10-15T23:00:00")
        pb.record_paper_bet(
            self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
            market_id="REAL_MARKET_PARLAY:2026-10-15:b", entry_odds=150, is_combo=True,
            legs_json=json.dumps([_leg(game_id="2", participant_id="BOS")]),
            conservative_probability=0.75, edge=0.05, event_id="evt-2",
            created_at_utc="2026-10-15T18:00:00+00:00", event_start_utc="2026-10-15T23:30:00")
        self.nhl_conn.execute(
            "INSERT INTO roster_status_events (player_id, team_id, status, effective_at_utc, "
            "observed_at_utc, source) VALUES ('TOR', 'TOR', 'OUT', '2026-10-15T19:00:00', "
            "'2026-10-15T19:00:00', 'test')")
        self.nhl_conn.commit()
        # MONEYLINE legs never check roster_status (participant_id is a team
        # abbrev, not a player) -- this insert is deliberately a no-op for
        # both bets; included to prove an unrelated real event never
        # cross-contaminates a different bet's revalidation.
        summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 0)
        rows = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")
        self.assertTrue(all(r["result_status"] == "PENDING" for r in rows))


class TestTradeDetectionVoids(unittest.TestCase):
    """Re-resolves the player's CURRENT most-recent team from the real SOG
    identity corpus (research.live_sog_pricing.player_mapping.build_player_index)
    -- mocked here to a controlled index so the test doesn't depend on
    whether a real trade happens to exist in the frozen corpus file."""

    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.bankroll_conn = _fresh_bankroll_conn()

    def tearDown(self):
        self.nhl_conn.close()
        self.bankroll_conn.close()

    def test_a_player_no_longer_on_either_team_in_the_game_alerts_without_voiding(self):
        from unittest import mock
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        fake_index = {"p1": [{"player_id": "P1", "player_name": "P1", "most_recent_team": "BOS",
                               "most_recent_game_date": "2026-10-15"}]}
        with mock.patch.object(br, "_load_player_index", return_value=fake_index):
            summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)
        row = pb.query_paper_bets(self.bankroll_conn, track="REAL_MARKET_PAPER")[0]
        self.assertEqual(row["result_status"], "PENDING")  # alert only, never voided/refunded
        alerts = pb.ticket_alerts(self.bankroll_conn)[row["paper_bet_id"]]
        self.assertEqual(alerts[0]["kind"], "TEAM_CHANGED")

    def test_a_player_still_on_the_home_or_away_team_never_alerts_without_voiding(self):
        from unittest import mock
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        fake_index = {"p1": [{"player_id": "P1", "player_name": "P1", "most_recent_team": "TOR",
                               "most_recent_game_date": "2026-10-15"}]}
        with mock.patch.object(br, "_load_player_index", return_value=fake_index):
            summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 0)

    def test_the_rosters_current_team_beats_a_stale_archival_corpus(self):
        """Regression (2026-10-08): the corpus said DAL, the roster sync said NSH (where he played); no 'trade' alert may be raised."""
        from unittest import mock
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        self.nhl_conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source) "
                              "VALUES ('P1', 'TOR', '2026-09-24T00:00:00', '2026-09-24T00:00:00', 'ROSTER_SYNC', 'nhl_api')")
        self.nhl_conn.commit()
        stale = {"p1": [{"player_id": "P1", "player_name": "P1", "most_recent_team": "DAL", "most_recent_game_date": "2026-04-15"}]}
        with mock.patch.object(br, "_load_player_index", return_value=stale):
            summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 0)

    def test_the_rosters_current_team_still_raises_a_real_trade_alert(self):
        from unittest import mock
        _stake(self.bankroll_conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")],
               created_at_utc="2026-10-15T18:00:00+00:00")
        self.nhl_conn.execute("INSERT INTO team_membership_events (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source) "
                              "VALUES ('P1', 'BOS', '2026-10-15T12:00:00', '2026-10-15T12:00:00', 'TRADE', 'nhl_api')")
        self.nhl_conn.commit()
        with mock.patch.object(br, "_load_player_index", return_value={}):
            summary = br.revalidate_pending_real_market_bets(self.bankroll_conn, self.nhl_conn, now=NOW)
        self.assertEqual(summary["alerts_recorded"], 1)


if __name__ == "__main__":
    unittest.main()


class TestAlertRetraction(unittest.TestCase):
    def test_a_retracted_alert_keeps_its_row_and_carries_the_reason(self):
        conn = _fresh_bankroll_conn()
        _stake(conn, [_leg(market_family="PLAYER_SOG", threshold=4, participant_id="P1")], created_at_utc="2026-10-15T18:00:00+00:00")
        tid = pb.query_paper_bets(conn, track="REAL_MARKET_PAPER")[0]["paper_bet_id"]
        self.assertTrue(pb.record_ticket_alert(conn, tid, "TEAM_CHANGED", "TEAM_CHANGED: x is now on DAL"))
        aid = pb.ticket_alerts(conn)[tid][0]["alert_id"]
        self.assertIsNone(pb.ticket_alerts(conn)[tid][0]["retracted_reason"])
        self.assertTrue(pb.retract_ticket_alert(conn, aid, "archival corpus was stale"))
        self.assertFalse(pb.retract_ticket_alert(conn, aid, "again"))
        a = pb.ticket_alerts(conn)[tid][0]
        self.assertEqual((a["detail"], a["retracted_reason"]), ("TEAM_CHANGED: x is now on DAL", "archival corpus was stale"))
        self.assertEqual(pb.query_paper_bets(conn, track="REAL_MARKET_PAPER")[0]["result_status"], "PENDING")
