"""
Tests for research/real_market_parlay/real_slate_adapter.py (Real-Slate
Parlay Certification block, 2026-09-29). Covers translating REAL production
MONEYLINE (pricing.engine.evaluate_moneyline_for_game) and PLAYER_SOG_ALTERNATE
(market_parser + event_mapping + player_mapping + project_player_sog) output
into research.real_market_parlay.engine.ParlayLeg objects, every real
exclusion path (stale price, non-actionable threshold, unverified contract,
unresolved identity, unmatched event, goalie not confirmed, event already
started), and that no synthetic/demo fallback ever substitutes for missing
real data.

The T-35 MONEYLINE decision engine itself is NOT re-tested here (it has its
own extensive suite) -- these tests mock its OUTPUT (a BetReport) to prove
the ADAPTER's own translation logic, never the model's own numbers.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import db
from operational import paper_bankroll as pb
from pricing.engine import BetReport
from research.generic_prop_pricing import provider_adapter as pa
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import real_slate_adapter as adapter

NOW = dt.datetime(2026, 9, 29, 19, 0, 0, tzinfo=dt.timezone.utc)


def _fresh_db_with_game(game_id=1, home="TOR", away="MTL", start_utc="2026-09-29T23:00:00", state="SCHEDULED"):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in (home, away):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?, '20262027', ?, ?, ?, ?, ?, ?, 'test')",
        (game_id, start_utc[:10], start_utc, home, away, start_utc, state))
    conn.commit()
    return conn


def _pred(game_id=1, home="TOR", away="MTL", start_utc="2026-09-29T23:00:00"):
    return SimpleNamespace(game_id=game_id, home_team=home, away_team=away, game_date=start_utc[:10],
                            scheduled_start_utc=start_utc, prediction_time_utc="2026-09-29T18:50:00")


class TestMoneylineAdapter(unittest.TestCase):
    def test_bet_action_produces_an_eligible_leg(self):
        conn = _fresh_db_with_game()
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="BET",
                            model_conservative_probability=0.62, current_draftkings_price=-150,
                            odds_snapshot_id_selection=None)
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(len(legs), 1)
        self.assertEqual(excluded, [])
        leg = legs[0]
        self.assertEqual(leg.market_family, "MONEYLINE")
        self.assertEqual(leg.participant_id, "TOR")
        self.assertEqual(leg.side, "HOME")
        self.assertEqual(leg.american_price, -150)
        self.assertEqual(leg.conservative_probability, 0.62)
        self.assertTrue(leg.provider_contract_verified)
        self.assertTrue(leg.price_fresh)

    def test_pass_action_still_produces_an_eligible_leg(self):
        # PASS still carries a real, usable conservative_probability -- this
        # engine's own combo-level edge check judges value, not the solo action.
        conn = _fresh_db_with_game()
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="PASS",
                            model_conservative_probability=0.55, current_draftkings_price=-130,
                            odds_snapshot_id_selection=None)
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(len(legs), 1)

    def test_data_unavailable_is_excluded(self):
        conn = _fresh_db_with_game()
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR",
                            action="DATA_UNAVAILABLE", action_reason="no valid DraftKings quote")
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [])
        self.assertEqual(len(excluded), 1)
        self.assertIn("DATA_UNAVAILABLE", excluded[0]["reason"])

    def test_wait_action_excluded_as_goalie_not_confirmed(self):
        conn = _fresh_db_with_game()
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="WAIT",
                            action_reason="starting goalie not confirmed",
                            model_conservative_probability=0.6, current_draftkings_price=-150)
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [])
        self.assertIn("GOALIE_NOT_CONFIRMED", excluded[0]["reason"])

    def test_event_already_started_is_excluded_before_pricing(self):
        conn = _fresh_db_with_game(start_utc="2026-09-29T18:00:00")  # already before NOW
        legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [])
        self.assertEqual(excluded[0]["reason"], "EVENT_ALREADY_STARTED")

    def test_no_scheduled_games_is_zero_legs_never_a_fallback(self):
        conn = _fresh_db_with_game(state="FINAL")
        legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [])
        self.assertEqual(excluded, [])

    def test_unverified_contract_is_excluded(self):
        conn = _fresh_db_with_game()
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="BET",
                            model_conservative_probability=0.62, current_draftkings_price=-150)
        with mock.patch.object(pa, "VERIFIED_CONTRACTS", frozenset()), \
             mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [])
        self.assertEqual(excluded[0]["reason"], "CONTRACT_NOT_VERIFIED")

    def test_a_scheduled_game_several_days_out_is_not_todays_candidate(self):
        """Production Gap Closure sprint (2026-09-30): this adapter
        previously scanned EVERY scheduled game in nhl.db regardless of
        date, while the visible Today slate and the SOG leg pool are both
        scoped to today's real Eastern hockey day. A future-dated
        SCHEDULED game (still far from puck drop, so it would never hit
        the EVENT_ALREADY_STARTED exclusion) must not appear at all."""
        conn = _fresh_db_with_game(game_id=1, start_utc="2026-10-05T23:00:00")  # 6 real days after NOW
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="BET",
                            model_conservative_probability=0.62, current_draftkings_price=-150)
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred(start_utc="2026-10-05T23:00:00")), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(legs, [], "a game several days out is not part of TODAY's real slate")
        self.assertEqual(excluded, [], "excluded from the SQL query itself, never reached far enough to log a reason")

    def test_todays_scheduled_game_is_still_a_candidate(self):
        conn = _fresh_db_with_game(game_id=1, start_utc="2026-09-29T23:00:00")  # same ET date as NOW
        report = BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection="TOR", action="BET",
                            model_conservative_probability=0.62, current_draftkings_price=-150)
        with mock.patch("run_slate.build_prediction_for_game", return_value=_pred()), \
             mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=[report]):
            legs, excluded = adapter.moneyline_candidate_legs(conn, now=NOW)
        self.assertEqual(len(legs), 1)


class TestSogAlternateAdapter(unittest.TestCase):
    """Uses the REAL, certified fixture payload (the same one
    TestPlayerSogAlternateContractParity uses) driven through the REAL
    parser/event-mapping pipeline -- only identity resolution and the model
    projection are mocked, since the real research/player_sog identity
    corpus does not (as of this block) cover this fixture's specific
    players -- a real, disclosed, pre-existing gap, not something this
    adapter papers over (see the block's own report)."""

    @staticmethod
    def _load_payload():
        import json
        path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_sog_alternate_real_payload.json"
        with open(path) as f:
            raw = json.load(f)
        return raw

    def _schedule_conn(self):
        return _fresh_db_with_game(game_id=555, home="CAR", away="FLA", start_utc="2026-09-29T21:10:47")

    def _matched_player_mapping(self, player_id="P1"):
        return {"status": "MATCHED", "player_id": player_id, "reason": ""}

    def _active_projection(self, conservative_prob=0.55):
        return {"status": "PROJECTED_ACTIVE", "probs": {2: 0.8, 3: 0.65, 4: conservative_prob, 5: 0.3},
                "conservative_probs": {2: 0.75, 3: 0.6, 4: conservative_prob, 5: 0.25}, "confidence": "HIGH"}

    def test_real_certified_payload_produces_an_eligible_leg_when_fresh(self):
        payload = self._load_payload()
        conn = self._schedule_conn()
        # The real fixture's own captured timestamp is 2026-09-29T12:14:34Z --
        # "fresh" here means evaluated near that real capture time, not the
        # module-level NOW (19:00Z), which is genuinely hours later.
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)
        with mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)), \
             mock.patch("research.player_sog.live_projection.project_player_sog",
                         return_value=self._active_projection()):
            legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=fresh_now)
        # The mocked map_player() matches every real name in this fixture to the
        # same stub player_id -- several players' Over 3.5 lines all map to
        # threshold 4, so isolate Brady Tkachuk's specific leg for the price
        # assertion below rather than asserting a total count this mock doesn't
        # meaningfully constrain.
        sog_legs = [l for l in legs if l.threshold == 4 and l.participant_name == "Brady Tkachuk"]
        self.assertEqual(len(sog_legs), 1)
        leg = sog_legs[0]
        self.assertEqual(leg.market_family, "PLAYER_SOG_ALTERNATE")
        self.assertEqual(leg.threshold, 4)
        self.assertEqual(leg.american_price, 195)  # Brady Tkachuk Over 3.5 == 4+, real archived price
        self.assertEqual(leg.conservative_probability, 0.55)
        self.assertTrue(leg.provider_contract_verified)

    def test_late_game_prediction_date_uses_et_not_a_naive_utc_slice(self):
        """Production Gap Closure sprint (2026-09-30): commence_time[:10]
        used to feed project_player_sog() directly -- for a 10 PM ET game
        (already the next UTC calendar day), that fed the model the WRONG
        real hockey day. This fixture's real commence_time is
        2026-09-29T21:10:47Z = 2026-09-29 17:10 ET (EDT, UTC-4) -- same ET
        and UTC date, so it does not itself expose the bug, but the actual
        argument passed to the model must be the correctly ET-derived date
        either way, not an accidentally-correct coincidence of this fixture."""
        payload = self._load_payload()
        conn = self._schedule_conn()
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)
        captured_dates = []

        def _capture_projection(*args, **kwargs):
            # project_player_sog(sog_rows, sog_index, team_schedules, opponent_allowed,
            #   league_avg_sog_allowed, weights, alpha, player_id, team, opponent,
            #   prediction_date, season)
            captured_dates.append(args[10])
            return self._active_projection()

        with mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)), \
             mock.patch("research.player_sog.live_projection.project_player_sog",
                         side_effect=_capture_projection):
            adapter.sog_alternate_candidate_legs(conn, [payload], now=fresh_now)
        self.assertTrue(captured_dates)
        from operational import eastern_time as et
        self.assertEqual(captured_dates[0], et.eastern_date_of(payload["commence_time"]))

    def test_stale_price_is_excluded_never_treated_as_current(self):
        # The real fixture's own captured timestamp is 2026-09-29T12:14:34Z --
        # hours before NOW (19:00Z) -- genuinely stale for a 21:10:47Z game.
        payload = self._load_payload()
        conn = self._schedule_conn()
        with mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)), \
             mock.patch("research.player_sog.live_projection.project_player_sog",
                         return_value=self._active_projection()):
            legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=NOW)
        self.assertEqual(legs, [])
        self.assertTrue(any("STALE_PRICE" in e["reason"] for e in excluded))

    def _run_with_market_time(self, last_update, now):
        import copy
        payload = copy.deepcopy(self._load_payload())
        for market in payload["bookmakers"][0]["markets"]:
            if last_update is None:
                market.pop("last_update", None)
            else:
                market["last_update"] = last_update
        with mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)), \
             mock.patch("research.player_sog.live_projection.project_player_sog",
                         return_value=self._active_projection()):
            return adapter.sog_alternate_candidate_legs(self._schedule_conn(), [payload], now=now)

    def test_quote_timestamp_policy_missing_malformed_future_and_stale_quotes_never_qualify(self):
        """Audit regression: the adapters' freshness must follow the provider's own market last_update."""
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)
        legs, _ = self._run_with_market_time("2026-09-29T12:14:34Z", fresh_now)
        self.assertTrue(legs and all(l.quote_updated_utc == "2026-09-29T12:14:34Z" and l.freshness_status == "FRESH"
                                     for l in legs))
        self.assertTrue(all(l.quote_age_min == 5.4 for l in legs))
        for value, expected in ((None, "MISSING_QUOTE_TIMESTAMP"), ("garbage", "MALFORMED_QUOTE_TIMESTAMP"),
                                ("2026-09-29T13:30:00Z", "FUTURE_QUOTE_TIMESTAMP"),
                                ("2026-09-23T12:14:34Z", "STALE_PRICE")):          # six days old, fetched "now"
            legs, excluded = self._run_with_market_time(value, fresh_now)
            self.assertEqual(legs, [], value)
            reasons = {e["reason"].split(" ")[0] for e in excluded if e["market_family"] == "PLAYER_SOG_ALTERNATE"}
            self.assertIn(expected, reasons, (value, reasons))

    def test_1plus_threshold_is_excluded(self):
        payload = self._load_payload()
        conn = self._schedule_conn()
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)  # near the real capture time
        with mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)), \
             mock.patch("research.player_sog.live_projection.project_player_sog",
                         return_value=self._active_projection()):
            legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=fresh_now)
        one_plus = [e for e in excluded if "1+" in e["identifier"] or "point=0.5" in e["identifier"]]
        self.assertTrue(any("MODEL_THRESHOLD_NOT_ACTIONABLE" in e["reason"] for e in excluded))
        self.assertFalse(any(l.threshold == 1 for l in legs))

    def test_unverified_contract_is_excluded(self):
        payload = self._load_payload()
        conn = self._schedule_conn()
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)
        with mock.patch.object(pa, "VERIFIED_CONTRACTS", frozenset()), \
             mock.patch("research.live_sog_pricing.player_mapping.map_player",
                         return_value=self._matched_player_mapping()), \
             mock.patch("research.real_market_parlay.real_slate_adapter._sog_model_inputs",
                         return_value=([], object(), {}, {}, 1.0, [], None)):
            legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=fresh_now)
        self.assertEqual(legs, [])
        self.assertTrue(any("CONTRACT_NOT_VERIFIED" in e["reason"] for e in excluded))

    def test_real_identity_resolves_after_the_p0_corpus_repair(self):
        # P0 block (2026-09-29): research/player_sog/player_game_sog.jsonl was
        # a stale build artifact (26,465 rows, 378 players -- generated
        # 2026-09-01, never re-run since, the identical bug already found and
        # fixed for the Blocks corpus the same day) missing every one of this
        # fixture's real players entirely, including both Tkachuks. Re-running
        # the existing, unmodified build_sog_corpus.py against its own
        # already-complete raw CSVs produced the real, correct 188,863-row/
        # 1,356-player corpus -- confirmed a pure staleness bug, not a data
        # gap or an extraction-logic bug. With NOW (a stale reference time),
        # every real quote is still excluded, but for STALE_PRICE /
        # MODEL_THRESHOLD_NOT_ACTIONABLE now -- never IDENTITY_UNMATCHED.
        payload = self._load_payload()
        conn = self._schedule_conn()
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=NOW)
        self.assertEqual(legs, [])
        self.assertGreater(len(excluded), 0)
        self.assertFalse(any("IDENTITY_UNMATCHED" in e["reason"] for e in excluded))
        self.assertTrue(all(e["reason"].split(":")[0].split("(")[0].strip()
                             in ("STALE_PRICE", "MODEL_THRESHOLD_NOT_ACTIONABLE", "IDENTITY_AMBIGUOUS")
                             for e in excluded))

    def test_real_identity_resolves_near_the_real_capture_time(self):
        """Evaluated near the quote's OWN real capture time (12:14:34Z) rather
        than a stale reference -- proves real event/identity mapping genuinely
        reaches the model for real players (never "unmatched"). Production Gap
        Closure sprint (2026-10-01): research/player_sog's corpus is frozen at
        2026-04-16, so the real model itself now honestly reports CORPUS_STALE
        rather than fabricating a PROJECTED_ACTIVE result from 6-month-old data
        (the exact class of bug a real-production investigation found via
        Auston Matthews) -- zero eligible legs is the correct, honest outcome
        here today, not a regression. See test_sog_alternate_pipeline_e2e.py
        for proof the model/pricing stack itself works correctly against a
        non-stale (mocked) corpus."""
        payload = self._load_payload()
        conn = self._schedule_conn()
        fresh_now = dt.datetime(2026, 9, 29, 12, 20, 0, tzinfo=dt.timezone.utc)
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=fresh_now)
        self.assertEqual(legs, [])
        reasons = {e["reason"] for e in excluded}
        self.assertTrue(any(r.startswith("MODEL_CORPUS_STALE") for r in reasons), reasons)
        # Brady Tkachuk's real most-recent-known team (per the real corpus)
        # doesn't match this fixture's CAR/FLA game -- correctly AMBIGUOUS,
        # never guessed past.
        self.assertTrue(any("IDENTITY_AMBIGUOUS" in e["reason"] and "Brady Tkachuk" in e["identifier"]
                             for e in excluded))

    def test_unmatched_event_is_excluded(self):
        payload = dict(self._load_payload())
        payload["home_team"] = "Some Made Up Team"
        conn = self._schedule_conn()
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=NOW)
        self.assertEqual(legs, [])
        self.assertTrue(any(e["reason"].startswith("EVENT_UNMATCHED") for e in excluded))

    def test_no_archive_payloads_is_zero_legs_never_a_fallback(self):
        conn = self._schedule_conn()
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [], now=NOW)
        self.assertEqual(legs, [])
        self.assertEqual(excluded, [])


class TestRealSlateEndToEnd(unittest.TestCase):
    """Integration: real-shaped ParlayLeg objects (as the adapter would
    produce) flow correctly through the already-tested engine, and a
    qualifying result can be recorded into an ISOLATED test-only paper
    ledger -- never operational/paper_bankroll.db."""

    @staticmethod
    def _leg(game_id, conservative_probability=0.90, american_price=-150):
        return rmp.ParlayLeg(
            game_id=game_id, event_id=f"evt-{game_id}", market_family="MONEYLINE", participant_id="TOR",
            participant_name="Toronto Maple Leafs", side="HOME", threshold=None,
            american_price=american_price, conservative_probability=conservative_probability,
            sportsbook="draftkings", captured_at_utc="2026-09-29T18:00:00Z",
            provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
            price_fresh=True, event_not_started=True)

    def test_two_leg_cross_game_ticket_from_real_shaped_legs_reaches_plus_100(self):
        legs = [self._leg(f"G{i}", conservative_probability=0.70) for i in range(2)]   # two -150 legs: 2.78x
        result = rmp.build_real_market_parlay(legs)
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 2)
        self.assertGreaterEqual(result["combo"].combined_decimal, 2.0)

    def test_a_third_leg_is_only_added_when_two_cannot_reach_plus_100(self):
        short = [self._leg(f"G{i}", conservative_probability=0.80, american_price=-300) for i in range(4)]
        result = rmp.build_real_market_parlay(short)           # two -300 legs = 1.78x; three = 2.37x
        self.assertEqual(result["status"], "QUALIFIED")
        self.assertEqual(result["recommended_legs"], 3)

    def test_no_edge_is_rejected_even_with_a_high_hit_chance(self):
        no_edge = [self._leg(f"G{i}", conservative_probability=0.60, american_price=-150) for i in range(3)]
        self.assertEqual(rmp.build_real_market_parlay(no_edge)["status"], "NO_QUALIFYING_PARLAY")
        edge = [self._leg(f"G{i}", conservative_probability=0.70, american_price=-150) for i in range(3)]
        self.assertEqual(rmp.build_real_market_parlay(edge)["status"], "QUALIFIED")

    def test_ten_dollar_paper_object_created_in_isolated_test_db(self):
        legs = [self._leg(f"G{i}", conservative_probability=0.70) for i in range(3)]
        result = rmp.build_real_market_parlay(legs)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        test_conn = pb.init_db(Path(tmp.name))
        try:
            bet = pb.create_real_market_combo_paper_bet(test_conn, result)
            self.assertEqual(bet["status"], "INSERTED")
            rows = pb.query_paper_bets(test_conn, track="REAL_MARKET_PAPER")
            self.assertEqual(rows[0]["stake"], 10.00)
        finally:
            test_conn.close()

    def test_offered_parlay_price_remains_null_end_to_end(self):
        legs = [self._leg(f"G{i}", conservative_probability=0.70) for i in range(3)]
        result = rmp.build_real_market_parlay(legs)
        self.assertIsNone(result["combo"].offered_parlay_price)

    def test_zero_eligible_legs_creates_zero_bets(self):
        result = rmp.build_real_market_parlay([])
        self.assertEqual(result["status"], "NO_QUALIFYING_PARLAY")
        with self.assertRaises(pb.InvalidPaperBetError):
            tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
            tmp.close()
            test_conn = pb.init_db(Path(tmp.name))
            pb.create_real_market_combo_paper_bet(test_conn, result)


if __name__ == "__main__":
    unittest.main()
