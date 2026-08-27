import unittest

from features import point_in_time as pit
from tests.helpers import Fixture, make_test_db, t


class TestTeamMembership(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_trade_does_not_alter_history_before_it(self):
        # TOR_F1 starts on TOR, gets traded to BOS on day 5
        self.fx.trade_player("TOR_F1", "BOS", effective_at=t(5), observed_at=t(5))

        # as of day 2 (before the trade), they were still on TOR
        self.assertEqual(pit.team_of_player(self.conn, "TOR_F1", t(2)), "TOR")
        # as of day 6 (after), they're on BOS
        self.assertEqual(pit.team_of_player(self.conn, "TOR_F1", t(6)), "BOS")

    def test_trade_observed_late_does_not_leak_backward(self):
        # the trade HAPPENED on day 5 but wasn't publicly known until day 8
        self.fx.trade_player("TOR_F1", "BOS", effective_at=t(5), observed_at=t(8))

        # a prediction made on day 6 (after it happened, before it was known)
        # must still see the OLD team — that's the whole point of observed_at
        self.assertEqual(pit.team_of_player(self.conn, "TOR_F1", t(6)), "TOR")
        self.assertEqual(pit.team_of_player(self.conn, "TOR_F1", t(9)), "BOS")

    def test_roster_ids_reflect_trade(self):
        self.fx.trade_player("TOR_F1", "BOS", effective_at=t(5))
        before = pit.roster_ids_for_team(self.conn, "TOR", t(2))
        after = pit.roster_ids_for_team(self.conn, "TOR", t(6))
        self.assertIn("TOR_F1", before)
        self.assertNotIn("TOR_F1", after)


class TestRosterStatus(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_unreported_player_defaults_active(self):
        self.assertEqual(pit.roster_status(self.conn, "TOR_F1", t(5)), "ACTIVE")

    def test_injury_then_recovery_then_reinjury(self):
        self.fx.set_roster_status("TOR_F1", "TOR", "OUT", effective_at=t(2))
        self.assertEqual(pit.roster_status(self.conn, "TOR_F1", t(3)), "OUT")

        self.fx.set_roster_status("TOR_F1", "TOR", "ACTIVE", effective_at=t(6))
        self.assertEqual(pit.roster_status(self.conn, "TOR_F1", t(7)), "ACTIVE")

        self.fx.set_roster_status("TOR_F1", "TOR", "OUT", effective_at=t(9))
        self.assertEqual(pit.roster_status(self.conn, "TOR_F1", t(10)), "OUT")
        # and before the second injury, they were still healthy
        self.assertEqual(pit.roster_status(self.conn, "TOR_F1", t(8)), "ACTIVE")

    def test_available_roster_excludes_out_player(self):
        self.fx.set_roster_status("TOR_F1", "TOR", "OUT", effective_at=t(2))
        avail = pit.available_roster(self.conn, "TOR", t(3))
        self.assertNotIn("TOR_F1", avail)
        self.assertIn("TOR_F2", avail)

    def test_boxscore_played_flag_does_not_gate_pregame_availability(self):
        # a player marked played=0 in the FINAL box score, with NO
        # roster_status_events row at all, must still show as available
        # pregame — availability comes from roster_status_events only.
        self.conn.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, played) VALUES (1,?,?,0)",
            ("TOR_F1", "TOR"),
        )
        self.conn.commit()
        avail = pit.available_roster(self.conn, "TOR", t(9))
        self.assertIn("TOR_F1", avail)


class TestGoalieStatus(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_unknown_before_any_event(self):
        status = pit.goalie_status(self.conn, 1, "TOR", t(5))
        self.assertEqual(status.status, "UNKNOWN")
        self.assertIsNone(status.player_id)

    def test_expected_then_confirmed_progression(self):
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "EXPECTED", effective_at=t(9))
        self.assertEqual(pit.goalie_status(self.conn, 1, "TOR", t(9, hour=13)).status, "EXPECTED")

        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(10, hour=17))
        self.assertEqual(
            pit.goalie_status(self.conn, 1, "TOR", t(10, hour=18)).status, "CONFIRMED")
        # but a prediction made BEFORE the confirmation still only sees EXPECTED
        self.assertEqual(
            pit.goalie_status(self.conn, 1, "TOR", t(10, hour=16)).status, "EXPECTED")

    def test_changed_after_confirmation(self):
        self.fx.set_goalie_status(1, "TOR", "TOR_G1", "CONFIRMED", effective_at=t(10, hour=17))
        self.fx.set_goalie_status(1, "TOR", "TOR_G2", "CHANGED", effective_at=t(10, hour=18, minute=30))
        late = pit.goalie_status(self.conn, 1, "TOR", t(10, hour=18, minute=45))
        self.assertEqual(late.status, "CHANGED")
        self.assertEqual(late.player_id, "TOR_G2")
        earlier = pit.goalie_status(self.conn, 1, "TOR", t(10, hour=18))
        self.assertEqual(earlier.status, "CONFIRMED")
        self.assertEqual(earlier.player_id, "TOR_G1")


class TestRestContext(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT INTO teams (team_id) VALUES ('TOR')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def _add_game(self, gid, date_offset, opp="XXX", schedule_observed=t(-90)):
        import datetime as dt
        d = (dt.date(2025, 1, 1) + dt.timedelta(days=date_offset)).isoformat()
        start = d + "T19:00:00"
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (opp,))
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, schedule_observed_at_utc, game_state, source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (gid, "2025-DEMO", d, start, "TOR", opp, schedule_observed, "SCHEDULED", "test"),
        )
        # v2.1: rest_context()/game_schedule_as_of() now read the
        # append-only schedule-history table, not the games cache above.
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (gid, d, start, "TOR", opp, None, schedule_observed, schedule_observed, "test", "test"),
        )
        self.conn.commit()

    def test_back_to_back(self):
        self._add_game(1, 10)
        self._add_game(2, 11)
        ctx = pit.rest_context(self.conn, 2, "TOR", t(30))
        self.assertEqual(ctx["rest_days"], 1)
        self.assertEqual(ctx["back_to_back"], 1)

    def test_well_rested(self):
        self._add_game(1, 10)
        self._add_game(2, 15)
        ctx = pit.rest_context(self.conn, 2, "TOR", t(30))
        self.assertEqual(ctx["rest_days"], 5)
        self.assertEqual(ctx["back_to_back"], 0)

    def test_three_in_four_and_four_in_six(self):
        self._add_game(1, 10)
        self._add_game(2, 11)
        self._add_game(3, 13)   # this + games 1,2 -> 3 games within [day9, day13)? check windows
        ctx = pit.rest_context(self.conn, 3, "TOR", t(30))
        # games on day 10 and 11 both fall within the 4 days before day 13
        self.assertEqual(ctx["games_last_4_days"], 2)
        self.assertEqual(ctx["three_in_four"], 1)

    def test_rest_features_work_for_scheduled_not_just_final_games(self):
        # game 2 is still SCHEDULED (no result) — rest features must not
        # require a result to exist
        self._add_game(1, 10)
        self._add_game(2, 12)
        ctx = pit.rest_context(self.conn, 2, "TOR", t(30))
        self.assertEqual(ctx["rest_days"], 2)

    def test_schedule_not_yet_observed_is_excluded(self):
        # game 1's schedule wasn't "known" until day 50 -- it must not
        # count as a prior game toward game 2's rest window when
        # predicting on day 30, even though game 1 is chronologically
        # earlier (date_offset 10 < 15).
        self._add_game(1, 10, schedule_observed=t(50))   # observed late (day 50)
        self._add_game(2, 15)   # observed normally (default t(-90))
        ctx = pit.rest_context(self.conn, 2, "TOR", t(30))  # predicting on day 30
        self.assertEqual(ctx["rest_days"], 5)   # game 1 invisible -> fully rested default

    def test_target_games_own_schedule_must_be_known_to_compute_rest(self):
        # a stricter, related invariant: you cannot compute rest features
        # for a game whose OWN schedule isn't known yet as of
        # prediction_time_utc -- there's no such thing as "predict a game
        # that doesn't exist yet" (v2.1 fix -- rest_context used to read
        # the target's own game_date with no time guard at all).
        self._add_game(1, 10, schedule_observed=t(50))
        with self.assertRaises(ValueError):
            pit.rest_context(self.conn, 1, "TOR", t(30))


class TestDraftKingsSnapshots(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.fx = Fixture(self.conn)

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_latest_snapshot_at_or_before_prediction_time(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8), label="MORNING")
        self.fx.add_odds(1, "TOR", -140, captured_at=t(9, hour=18), label="T-60")
        snap = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", t(9, hour=12))
        self.assertEqual(snap["price_american"], -150)   # only the morning price had happened yet

    def test_future_snapshot_excluded_from_earlier_prediction(self):
        self.fx.add_odds(1, "TOR", -200, captured_at=self.fx.scheduled_start,
                          label="CLOSE")   # the closing line
        snap = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", t(9, hour=8))
        self.assertIsNone(snap)   # closing line captured well after prediction time

    def test_suspended_price_rejected(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8), status="SUSPENDED")
        snap = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", t(9, hour=12))
        self.assertIsNone(snap)

    def test_stale_price_rejected_when_max_staleness_set(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(5, hour=8))
        snap = pit.latest_draftkings_snapshot(
            self.conn, 1, "MONEYLINE", "TOR", t(9, hour=8), max_staleness_minutes=60)
        self.assertIsNone(snap)

    def test_post_start_price_rejected(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=self.fx.scheduled_start,
                          event_start_utc=self.fx.scheduled_start)
        # captured_at_utc == event_start_utc is NOT strictly before start -> rejected
        snap = pit.latest_draftkings_snapshot(
            self.conn, 1, "MONEYLINE", "TOR", t(30))  # prediction_time well after
        self.assertIsNone(snap)

    def test_two_sided_missing_one_side_returns_none_none(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8))
        # BOS side never posted
        a, b = pit.latest_draftkings_two_sided(self.conn, 1, "MONEYLINE", "TOR", "BOS", t(9, hour=12))
        self.assertIsNone(a)
        self.assertIsNone(b)

    def test_missing_draftkings_does_not_fall_back_to_another_book(self):
        self.fx.add_odds(1, "TOR", -140, captured_at=t(9, hour=8), sportsbook="FanDuel")
        snap = pit.latest_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR", t(9, hour=12))
        self.assertIsNone(snap)   # a FanDuel price must never satisfy a DraftKings lookup

    def test_provider_and_sportsbook_identity_stay_distinct(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8),
                          sportsbook="DraftKings", data_provider="the-odds-api")
        self.fx.add_odds(1, "BOS", 130, captured_at=t(9, hour=8),
                          sportsbook="DraftKings", data_provider="another-provider")
        row_a = self.conn.execute(
            "SELECT sportsbook, data_provider FROM odds_snapshots WHERE selection='TOR'").fetchone()
        row_b = self.conn.execute(
            "SELECT sportsbook, data_provider FROM odds_snapshots WHERE selection='BOS'").fetchone()
        self.assertEqual(row_a["sportsbook"], "DraftKings")
        self.assertEqual(row_b["sportsbook"], "DraftKings")
        self.assertEqual(row_a["data_provider"], "the-odds-api")
        self.assertEqual(row_b["data_provider"], "another-provider")

    def test_closing_snapshot_ignores_prediction_time_by_design(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8), label="MORNING")
        self.fx.add_odds(1, "TOR", -175, captured_at=self.fx.scheduled_start, label="CLOSE",
                          event_start_utc=t(999))  # avoid post-start rejection for this check
        closing = pit.closing_draftkings_snapshot(self.conn, 1, "MONEYLINE", "TOR")
        self.assertEqual(closing["price_american"], -175)

    def test_repeated_insert_of_same_snapshot_does_not_duplicate(self):
        for _ in range(3):
            self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8))
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM odds_snapshots WHERE selection='TOR'").fetchone()["c"]
        self.assertEqual(n, 1)   # the unique index makes the duplicates no-ops

    def test_new_snapshot_at_new_time_does_not_overwrite_old_one(self):
        self.fx.add_odds(1, "TOR", -150, captured_at=t(9, hour=8), label="MORNING")
        self.fx.add_odds(1, "TOR", -170, captured_at=t(9, hour=18), label="T-60")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM odds_snapshots WHERE selection='TOR'").fetchone()["c"]
        self.assertEqual(n, 2)
        morning = self.conn.execute(
            "SELECT price_american FROM odds_snapshots WHERE selection='TOR' AND "
            "snapshot_label='MORNING'").fetchone()
        self.assertEqual(morning["price_american"], -150)   # untouched by the later insert


if __name__ == "__main__":
    unittest.main()
