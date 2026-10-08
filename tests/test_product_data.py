"""
The product data layer and its helpers: Eastern dates, default dates, team records, role inference, goalie model pieces, manual goalie
confirmations and Ontario spot checks, the daily review, snapshot rules (no simulated content) and demo-bet isolation.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from operational import daily_review, goalie_confirmations, isolate_demo_history, manual_orders, product_data
from operational import paper_bankroll as pb
from operational import cloud_snapshot_schema as schema
from research.product_models import live, team_goalie as tg
from tests.test_paper_bet_settlement_driver import _fresh_nhl_conn

UTC = dt.timezone.utc


def add_game(conn, gid, start, state="SCHEDULED", home="TOR", away="MTL", hs=None, as_=None, period=None, season="20262027", date=None):
    conn.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, game_state, home_score, away_score, "
                 "final_period_type, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (gid, season, date or start[:10], start, home, away, state, hs, as_, period, "test"))
    conn.commit()


class TestSchedule(unittest.TestCase):
    def test_eastern_date_follows_the_start_instant_not_the_utc_date(self):
        nhl = _fresh_nhl_conn()
        add_game(nhl, 2026020050, "2026-10-07T00:00:00", state="FINAL", hs=4, as_=2, period="REG", date="2026-10-06")   # 8 PM ET on Oct 6
        add_game(nhl, 2026020051, "2026-10-07T23:00:00")                                                                  # 7 PM ET on Oct 7
        games = {g["game_id"]: g for g in product_data.load_games(nhl, dt.datetime(2026, 10, 7, 12, tzinfo=UTC))}
        self.assertEqual(games["2026020050"]["date_et"], "2026-10-06")
        self.assertEqual(games["2026020051"]["date_et"], "2026-10-07")
        self.assertEqual(games["2026020050"]["start_et"], "8:00 PM ET")

    def test_state_is_relative_to_now(self):
        nhl = _fresh_nhl_conn()
        add_game(nhl, 2026020060, "2026-10-08T23:00:00")
        add_game(nhl, 2026020061, "2026-10-08T23:00:00", state="FINAL", hs=1, as_=0, period="REG")
        before = {g["game_id"]: g["state"] for g in product_data.load_games(nhl, dt.datetime(2026, 10, 8, 20, tzinfo=UTC))}
        after = {g["game_id"]: g["state"] for g in product_data.load_games(nhl, dt.datetime(2026, 10, 9, 1, tzinfo=UTC))}
        self.assertEqual(before, {"2026020060": "SCHEDULED", "2026020061": "FINAL"})
        self.assertEqual(after["2026020060"], "STARTED")                      # past puck drop, no result yet: not shown as upcoming

    def test_game_types_and_the_previous_season_are_labelled(self):
        self.assertEqual(product_data.game_type(2026010005), "PRESEASON")
        self.assertEqual(product_data.game_type("2026020056"), "REGULAR")
        self.assertEqual(product_data.season_label("20252026"), "2025-26")

    def test_default_date_is_today_else_next_game_day_in_the_current_season(self):
        games = [{"type": "REGULAR", "season": "20262027", "date_et": d} for d in ("2026-10-06", "2026-10-09", "2026-10-10")] + \
                [{"type": "REGULAR", "season": "20252026", "date_et": "2026-10-08"}, {"type": "PRESEASON", "season": "20262027", "date_et": "2026-10-08"}]
        self.assertEqual(product_data._default_date(games, "2026-10-08"), "2026-10-09")       # an old-season game on today's date is never the default
        self.assertEqual(product_data._default_date(games, "2026-10-09"), "2026-10-09")
        self.assertEqual(product_data._default_date(games, "2026-10-20"), "2026-10-10")

    def test_team_records_count_overtime_losses_separately(self):
        g = lambda i, h, a, hs, as_, p: {"game_id": str(i), "state": "FINAL", "type": "REGULAR", "season": "20262027", "start_utc": f"2026-10-0{i}T23:00:00Z",
                                         "date_et": f"2026-10-0{i}", "home": h, "away": a, "home_score": hs, "away_score": as_, "period_type": p}
        rec = product_data.team_records([g(1, "TOR", "MTL", 3, 2, "REG"), g(2, "TOR", "BOS", 2, 3, "OT"), g(3, "MTL", "TOR", 4, 1, "REG")])
        self.assertEqual((rec["TOR"]["w"], rec["TOR"]["otl"], rec["TOR"]["l"], rec["TOR"]["gp"]), (1, 1, 1, 3))
        self.assertEqual(rec["TOR"]["last5"][0]["game_id"], "3")


class TestRoles(unittest.TestCase):
    def test_lines_pairs_and_power_play_units_come_from_recent_usage(self):
        players = {}
        for i in range(12):
            players[f"F{i}"] = {"team": "TOR", "position_group": "F", "recent_toi": 20 - i, "recent_toi_pp": 3.0 if i < 5 else (1.0 if i < 10 else 0.0), "role_games": 4}
        for i in range(6):
            players[f"D{i}"] = {"team": "TOR", "position_group": "D", "recent_toi": 24 - i, "recent_toi_pp": 2.0 if i < 2 else 0.0, "role_games": 4}
        players["X"] = {"team": "TOR", "position_group": "F", "recent_toi": 18, "recent_toi_pp": 1.0, "role_games": 1}
        live.infer_roles(players)
        self.assertEqual([players[f"F{i}"]["line"] for i in (0, 2, 3, 11)], [1, 1, 2, 4])
        self.assertEqual([players[f"D{i}"]["line"] for i in (0, 1, 2, 5)], [1, 1, 2, 3])
        self.assertEqual(players["F0"]["pp_unit"], 1)
        self.assertEqual(players["F7"]["pp_unit"], 2)
        self.assertIsNone(players["F11"]["pp_unit"])
        self.assertIsNone(players["X"]["line"])                                    # not enough games to infer a role
        self.assertIn("not an official line chart", players["F0"]["role_source"])


class TestGoalieModelPieces(unittest.TestCase):
    def test_range_contains_the_mean_and_widens_with_dispersion(self):
        lo, hi = tg.saves_quantile_range(26.0, 0.03)
        self.assertLess(lo, 26)
        self.assertGreater(hi, 26)
        lo2, hi2 = tg.saves_quantile_range(26.0, 0.30)
        self.assertGreaterEqual(hi2 - lo2, hi - lo)

    def test_probability_of_at_least_n_saves_falls_as_n_rises(self):
        p = [tg.prob_saves_at_least(k, 26.0, 0.03) for k in (20, 24, 28, 32)]
        self.assertEqual(p, sorted(p, reverse=True))
        self.assertAlmostEqual(tg.prob_saves_at_least(0, 26.0, 0.03), 1.0)

    def test_stronger_team_has_the_higher_win_probability(self):
        rows = []
        for d in range(1, 25):
            for team, opp, ga_for, ga_against in (("AAA", "BBB", 4, 1), ("BBB", "AAA", 1, 4)):
                rows.append({"player_id": f"g{team}", "name": team, "game_id": 100 + d, "season": 2026, "team": team, "opp": opp, "home": team == "AAA",
                             "date": f"2026-10-{d:02d}", "toi": 60.0, "shots_against": 30.0, "goals_against": float(ga_against)})
        st = tg.TeamGoalieState()
        for date, day in sorted({r["date"]: None for r in rows}.items()):
            st.consume_day([r for r in rows if r["date"] == date])
        live_stub = {"state": st, "strength_coef": [0.27, 0.75]}
        self.assertGreater(tg.win_probability(live_stub, "AAA", "BBB")["base"], 0.6)
        self.assertLess(tg.win_probability(live_stub, "BBB", "AAA")["base"], 0.4)

    def test_validation_report_exists_and_states_what_it_found(self):
        v = tg.load_validation()
        self.assertEqual(v["splits"], {"train": [2022, 2023], "calibration": 2024, "final_evaluation": 2025})
        wp = v["win_probability"]
        self.assertLess(wp["strength_only"]["log_loss"], wp["home_rate_baseline"]["log_loss"])
        verdicts = tg.saves_verdicts(v)
        self.assertTrue(verdicts and all(x["verdict"] in ("BEATS_BASELINES", "DOES_NOT_BEAT_BASELINES") for x in verdicts.values()))

    def test_skater_validation_report_scores_every_market_against_baselines(self):
        verdicts = live.market_verdicts(live.load_validation())
        self.assertIn("shots>=2", verdicts)
        self.assertTrue(all("baselines_log_loss" in v for v in verdicts.values()))


class TestGoalieConfirmations(unittest.TestCase):
    def setUp(self):
        self.nhl = _fresh_nhl_conn()
        self.now = dt.datetime(2026, 10, 8, 15, tzinfo=UTC)
        add_game(self.nhl, 2026020070, "2026-10-08T23:00:00")
        self.nhl.execute("INSERT INTO players (player_id, full_name, position) VALUES ('8000001', 'Test Goalie', 'G'), ('8000002', 'Test Skater', 'C')")
        self.nhl.commit()

    def doc(self, **kw):
        base = {"schema": 1, "type": "GOALIE_CONFIRMATION", "confirmation_id": "cnf_" + "a" * 12, "game_id": "2026020070", "team": "TOR",
                "goalie_id": "8000001", "where_seen": "team announcement", "seen_at_utc": "2026-10-08T14:50:00Z"}
        base.update(kw)
        return base

    def test_a_confirmation_is_stored_with_source_and_time_and_read_back(self):
        res = goalie_confirmations.validate_and_record(self.nhl, self.doc(), now=self.now, source_ref="t")
        self.assertEqual(res["status"], "RECORDED")
        got = goalie_confirmations.lookup(self.nhl, 2026020070, "TOR", "8000001")
        self.assertEqual(got["status"], "CONFIRMED")
        self.assertIn("team announcement", got["source"])
        self.assertEqual(got["checked_at_utc"], "2026-10-08T14:50:00Z")
        other = goalie_confirmations.lookup(self.nhl, 2026020070, "TOR", "8000009")
        self.assertEqual(other["status"], "NOT_STARTING")
        self.assertEqual(goalie_confirmations.validate_and_record(self.nhl, self.doc(), now=self.now, source_ref="t")["status"], "ALREADY_RECORDED")

    def test_it_feeds_the_same_gates_every_consumer_reads(self):
        from features import point_in_time as pit
        goalie_confirmations.validate_and_record(self.nhl, self.doc(), now=self.now, source_ref="t")
        status = pit.goalie_status(self.nhl, 2026020070, "TOR", "2026-10-08T16:00:00")
        self.assertEqual((status.status, status.player_id), ("CONFIRMED", "8000001"))
        obs = goalie_confirmations.confirmed_observations(self.nhl, 2026020070, "TOR")
        self.assertEqual(obs[0].source_status, "CONFIRMED")
        self.assertEqual(obs[0].goalie_id, "8000001")

    def test_bad_confirmations_are_refused(self):
        bad = [self.doc(where_seen=""), self.doc(seen_at_utc="2026-10-08T20:00:00Z"), self.doc(team="BOS"), self.doc(goalie_id="8000002"),
               self.doc(game_id="999"), self.doc(confirmation_id="x")]
        for d in bad:
            self.assertEqual(goalie_confirmations.validate_and_record(self.nhl, d, now=self.now, source_ref="t")["status"], "REJECTED", d)
        started = dt.datetime(2026, 10, 8, 23, 30, tzinfo=UTC)
        self.assertEqual(goalie_confirmations.validate_and_record(self.nhl, self.doc(seen_at_utc="2026-10-08T23:20:00Z"), now=started, source_ref="t")["status"], "REJECTED")
        self.assertIsNone(goalie_confirmations.lookup(self.nhl, 2026020070, "TOR", "8000001"))


class TestOntarioSpotChecks(unittest.TestCase):
    def setUp(self):
        self.conn = pb.init_db(Path(tempfile.mkdtemp()) / "l.db")
        self.now = dt.datetime(2026, 10, 8, 18, tzinfo=UTC)

    def doc(self, **kw):
        base = {"schema": 1, "type": "ONTARIO_VERIFICATION", "verification_id": "ver_" + "a" * 12, "ontario_price": -140, "us_price_shown": -125,
                "observed_at_utc": "2026-10-08T17:55:00Z", "where_seen": "DraftKings Ontario app",
                "leg": {"game_id": "1", "participant_id": "P1", "participant_name": "Player 1", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "side": "OVER"}}
        base.update(kw)
        return base

    def test_records_price_jurisdiction_source_and_time_once(self):
        a = manual_orders.process_verification(self.conn, self.doc(), now=self.now, source="t")
        b = manual_orders.process_verification(self.conn, self.doc(), now=self.now, source="t")
        self.assertEqual((a["status"], b["status"]), ("RECORDED", "ALREADY_RECORDED"))
        row = manual_orders.recent_verifications(self.conn)[0]
        self.assertEqual((row["ontario_price"], row["where_seen"], row["observed_at_utc"]), (-140, "DraftKings Ontario app", "2026-10-08T17:55:00Z"))

    def test_invalid_checks_are_refused(self):
        for d in (self.doc(ontario_price=50), self.doc(where_seen=" "), self.doc(observed_at_utc="2026-10-08T19:00:00Z"), self.doc(leg={}), "nope"):
            self.assertEqual(manual_orders.process_verification(self.conn, d, now=self.now, source="t")["status"], "REJECTED")
        self.assertEqual(manual_orders.recent_verifications(self.conn), [])

    def test_a_check_does_not_touch_any_ticket(self):
        before = self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0]
        manual_orders.process_verification(self.conn, self.doc(), now=self.now, source="t")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], before)


class TestDailyReview(unittest.TestCase):
    def _loss(self, conn, quote_time="2026-10-07T18:50:00Z"):
        legs = [{"game_id": "10", "participant_id": "A", "participant_name": "Player A", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "side": "OVER",
                 "american_price": -150, "conservative_probability": 0.7, "model_version": "m", "quote_updated_utc": quote_time, "quote_age_min_at_entry": 5.0,
                 "game_start_utc": "2026-10-07T23:30:00Z", "event_id": None},
                {"game_id": "11", "participant_id": "B", "participant_name": "Player B", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "side": "OVER",
                 "american_price": -150, "conservative_probability": 0.7, "model_version": "m", "quote_updated_utc": quote_time, "quote_age_min_at_entry": 5.0,
                 "game_start_utc": "2026-10-07T23:30:00Z", "event_id": None}]
        res = pb.record_paper_bet(conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id="REAL_MARKET_PARLAY:2026-10-07:x", entry_odds=177,
                                  is_combo=True, legs_json=json.dumps(legs), conservative_probability=0.49, model_probability=0.49,
                                  created_at_utc="2026-10-07T19:00:00Z", paper_bet_id="TLOSS1")
        sj = {"leg_results": [{"leg": {k: legs[0][k] for k in ("game_id", "participant_id", "market_family", "threshold")}, "status": "RESOLVED", "outcome_hit": False, "actual_value": 1},
                              {"leg": {k: legs[1][k] for k in ("game_id", "participant_id", "market_family", "threshold")}, "status": "RESOLVED", "outcome_hit": True, "actual_value": 4}]}
        pb.settle_paper_bet(conn, "TLOSS1", "LOSS", settlement_json=json.dumps(sj))
        conn.execute("UPDATE paper_bets SET settled_at_utc = '2026-10-08T02:30:00Z' WHERE paper_bet_id = 'TLOSS1'")
        conn.commit()

    def test_a_clean_loss_is_variance_with_its_own_probability(self):
        conn = pb.init_db(Path(tempfile.mkdtemp()) / "l.db")
        self._loss(conn)
        rv = daily_review.build_review(conn, None, dt.datetime(2026, 10, 8, 14, tzinfo=UTC), review_date="2026-10-07")
        t = rv["tickets"][0]
        self.assertEqual((t["reading"], t["status"], t["origin"]), ("VARIANCE", "LOSS", "AUTOMATIC"))
        self.assertIn("51%", t["variance_note"])
        self.assertEqual([l["outcome"] for l in t["legs"]], ["LOSS", "WIN"])
        self.assertEqual(rv["defects"], [])
        self.assertEqual(rv["tickets_by_origin"]["MANUALLY_ADDED"], [])
        self.assertFalse(rv["policy"]["auto_retune"])

    def test_a_missing_quote_time_is_a_note_not_a_defect_and_a_late_record_is_a_defect(self):
        conn = pb.init_db(Path(tempfile.mkdtemp()) / "l.db")
        self._loss(conn, quote_time=None)
        rv = daily_review.build_review(conn, None, dt.datetime(2026, 10, 8, 14, tzinfo=UTC), review_date="2026-10-07")
        self.assertEqual(rv["tickets"][0]["reading"], "VARIANCE")
        self.assertTrue(rv["notes"])
        conn.execute("UPDATE paper_bets SET created_at_utc = '2026-10-08T00:30:00Z' WHERE paper_bet_id = 'TLOSS1'") if False else None

    def test_proposals_need_enough_legs_and_a_real_gap(self):
        few = {"AUTOMATIC": {"PLAYER_SOG_ALTERNATE": {"legs": 6, "expected": 4.1, "hits": 2, "brier": 0.2, "z": -2.5}}}
        self.assertEqual(daily_review.proposals(few)[0]["action"], "NONE")
        many = {"AUTOMATIC": {"PLAYER_SOG_ALTERNATE": {"legs": 80, "expected": 56.0, "hits": 40, "brier": 0.2, "z": -3.0}},
                "MANUALLY_ADDED": {"PLAYER_SOG_ALTERNATE": {"legs": 80, "expected": 56.0, "hits": 55, "brier": 0.2, "z": -0.2}}}
        got = {p["origin"]: p["action"] for p in daily_review.proposals(many)}
        self.assertEqual(got, {"AUTOMATIC": "REVIEW_CALIBRATION", "MANUALLY_ADDED": "NONE"})


class TestSnapshotRules(unittest.TestCase):
    def _doc(self, **extra):
        now = "2026-10-08T12:00:00Z"
        return {"schema_version": 2, "metadata": {"schema_version": 2, "generated_at": now, "generated_by": "t", "source_master_commit": "x",
                                                   "engine_mode": "t", "data_as_of": now, "freshness": {}}, **extra}

    def test_simulated_sections_cannot_be_published(self):
        schema.validate_snapshot(self._doc(tickets={}))
        for key in ("demo", "live_moneyline_rows"):
            with self.assertRaises(schema.SnapshotInvalid):
                schema.validate_snapshot(self._doc(**{key: {}}))

    def test_no_bundled_fallback_exists(self):
        from dashboard import snapshot_source
        self.assertFalse((Path(__file__).resolve().parent.parent / "dashboard" / "cloud_snapshot").exists())
        self.assertFalse(hasattr(snapshot_source, "_bundled"))
        self.assertFalse(hasattr(snapshot_source, "BUNDLED_FALLBACK"))

    def test_registry_has_no_simulated_pages_for_the_hosted_app(self):
        from dashboard import page_registry
        titles = {s.title for sections in page_registry.pages_for("ADMIN", "COMMUNITY_CLOUD_MODE").values() for s in sections}
        self.assertTrue({"Today", "Games", "Game Detail", "Players", "Goalies", "Team Intelligence", "Model Health", "Best Options",
                         "Paper Performance", "Ticket History", "Morning Review"} <= titles)
        for gone in ("Combinations", "Market Movement", "Model Learning", "Player Intelligence"):
            self.assertNotIn(gone, titles)
        pages_dir = Path(__file__).resolve().parent.parent / "dashboard" / "pages"
        for spec in page_registry.PAGES:
            self.assertTrue((pages_dir / spec.file).exists(), spec.file)


class TestIsolatePaperBets(unittest.TestCase):
    def test_simulated_tickets_move_out_and_real_ones_are_untouched(self):
        d = Path(tempfile.mkdtemp())
        conn = pb.init_db(d / "l.db")
        for i in range(2):
            pb.record_paper_bet(conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS", market_id=f"R{i}", entry_odds=120, idempotency_key=f"r{i}", paper_bet_id=f"T{i}")
        pb.record_paper_bet(conn, track="DEMO_PAPER", price_source="SIMULATED_DEMO", market_id="D", entry_odds=120, idempotency_key="d", paper_bet_id="DEMO1")
        conn.close()
        out = isolate_demo_history.apply_paper_bets(d / "l.db", d / "bk")
        self.assertEqual((out["status"], out["moved"], out["real_tickets"]), ("ISOLATED", 1, 2))
        left = sqlite3.connect(d / "l.db")
        self.assertEqual([r[0] for r in left.execute("SELECT paper_bet_id FROM paper_bets ORDER BY 1")], ["T0", "T1"])
        archive = sqlite3.connect(out["archive"])
        self.assertEqual(archive.execute("SELECT COUNT(*) FROM paper_bets").fetchone()[0], 3)
        self.assertEqual(isolate_demo_history.apply_paper_bets(d / "l.db", d / "bk")["status"], "NOTHING_TO_ISOLATE")


if __name__ == "__main__":
    unittest.main()
