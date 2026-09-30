"""
Platform Recovery block (2026-09-29): proves dashboard/pages/26_Player_Props.py's
new real default view -- every real eligible PLAYER_SOG_ALTERNATE leg,
real fields verbatim, never backfilled with a demo row, honest empty
state when nothing qualifies.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from dashboard import real_player_props_view as rppv
from research.real_market_parlay.engine import ParlayLeg


def _fresh_db_with_game(game_id, home, away):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in (home, away):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
        (game_id, "20262027", "2026-09-29", "2026-09-29T23:00:00", home, away, "2026-09-29T12:00:00",
         "SCHEDULED", "test"))
    conn.commit()
    return conn


def _leg(game_id="1", participant_id="P1", participant_name="Test Player", threshold=4,
         american_price=-150, conservative_probability=0.62):
    return ParlayLeg(
        game_id=game_id, event_id="e1", market_family="PLAYER_SOG_ALTERNATE",
        participant_id=participant_id, participant_name=participant_name, side="OVER", threshold=threshold,
        american_price=american_price, conservative_probability=conservative_probability,
        sportsbook="draftkings", captured_at_utc="2026-09-29T18:00:00Z",
        provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
        price_fresh=True, event_not_started=True)


class TestRealPlayerPropsNeverImportsDemo(unittest.TestCase):
    def test_module_never_imports_demo_data(self):
        import inspect
        src = inspect.getsource(rppv)
        self.assertNotIn("demo_data", src)
        self.assertNotIn("build_demo_opportunities", src)


class TestRealEligibleLegsFlowVerbatim(unittest.TestCase):
    def test_a_real_leg_produces_a_row_with_real_fields_and_correct_math(self):
        conn = _fresh_db_with_game("1", "TOR", "MTL")
        leg = _leg(game_id="1", conservative_probability=0.62, american_price=-150)
        with mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([leg], [])):
            state = rppv.build_real_player_props_state(conn, sog_archive_payloads=[])
        self.assertEqual(len(state["rows"]), 1)
        row = state["rows"][0]
        self.assertEqual(row["player"], "Test Player")
        self.assertEqual(row["market"], "PLAYER_SOG_ALTERNATE")
        self.assertEqual(row["threshold"], 4)
        self.assertEqual(row["dk_price"], -150)
        self.assertEqual(row["conservative_probability"], 0.62)
        self.assertEqual(row["data_label"], "REAL MARKET DATA")
        # implied probability from -150 is 0.6 (150/250); edge = 0.62 - 0.6 = 0.02
        self.assertAlmostEqual(row["implied_probability"], 0.6, places=3)
        self.assertAlmostEqual(row["edge"], 0.02, places=3)

    def test_no_eligible_legs_is_an_honest_empty_list_never_a_demo_substitute(self):
        conn = _fresh_db_with_game("1", "TOR", "MTL")
        with mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], [])):
            state = rppv.build_real_player_props_state(conn, sog_archive_payloads=[])
        self.assertEqual(state["rows"], [])
        self.assertEqual(state["provenance"], "REAL MARKET DATA")

    def test_excluded_candidates_are_counted_and_bucketed_by_reason(self):
        conn = _fresh_db_with_game("1", "TOR", "MTL")
        excluded = [{"identifier": "x", "market_family": "PLAYER_SOG_ALTERNATE", "reason": "STALE_PRICE (age=99min)"}]
        with mock.patch("research.real_market_parlay.real_slate_adapter.sog_alternate_candidate_legs",
                        return_value=([], excluded)):
            state = rppv.build_real_player_props_state(conn, sog_archive_payloads=[])
        self.assertEqual(state["excluded_count"], 1)
        self.assertEqual(state["excluded_by_reason"], {"STALE_PRICE": 1})


class TestCloudSnapshotRoundTrip(unittest.TestCase):
    def test_snapshot_builder_real_player_props_section_is_json_serializable(self):
        import json
        from operational import cloud_snapshot_builder as csb
        doc, errors = csb.build_live_snapshot(sections=("real_player_props",))
        self.assertEqual(errors, {})
        self.assertIn("real_player_props", doc)
        reloaded = json.loads(json.dumps(doc["real_player_props"]))
        self.assertEqual(reloaded, doc["real_player_props"])


if __name__ == "__main__":
    unittest.main()
