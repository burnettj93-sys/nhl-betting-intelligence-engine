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

    def test_unresolved_identity_is_excluded_real_finding(self):
        # The REAL, unmocked player_mapping against the REAL (but narrow)
        # research/player_sog identity corpus -- every player in this real
        # fixture is genuinely IDENTITY_UNMATCHED today (a real, disclosed
        # corpus-coverage gap, not fabricated by this test).
        payload = self._load_payload()
        conn = self._schedule_conn()
        legs, excluded = adapter.sog_alternate_candidate_legs(conn, [payload], now=NOW)
        self.assertEqual(legs, [])
        self.assertTrue(all("IDENTITY_UNMATCHED" in e["reason"] for e in excluded))
        self.assertGreater(len(excluded), 0)

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

    def test_cross_game_3leg_and_4leg_candidates_from_real_shaped_legs(self):
        legs3 = [self._leg(f"G{i}") for i in range(3)]
        result3 = rmp.build_real_market_parlay(legs3)
        self.assertEqual(result3["status"], "QUALIFIED")
        self.assertEqual(result3["recommended_legs"], 3)

        # 3-leg joint = 0.90^3 = 0.729; a 4th leg must keep the product >= 0.70
        # (0.729 * 0.97 = 0.707) for the engine to prefer 4 legs over 3.
        legs4 = legs3 + [self._leg("G3", conservative_probability=0.97)]
        result4 = rmp.build_real_market_parlay(legs4)
        self.assertEqual(result4["status"], "QUALIFIED")
        self.assertEqual(result4["recommended_legs"], 4)

    def test_below_70_percent_rejected_and_at_or_above_accepted(self):
        below = [self._leg(f"G{i}", conservative_probability=0.85) for i in range(3)]
        self.assertEqual(rmp.build_real_market_parlay(below)["status"], "NO_QUALIFYING_PARLAY")
        at_or_above = [self._leg(f"G{i}", conservative_probability=0.90) for i in range(3)]
        self.assertEqual(rmp.build_real_market_parlay(at_or_above)["status"], "QUALIFIED")

    def test_ten_dollar_paper_object_created_in_isolated_test_db(self):
        legs = [self._leg(f"G{i}") for i in range(3)]
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
        legs = [self._leg(f"G{i}") for i in range(3)]
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
