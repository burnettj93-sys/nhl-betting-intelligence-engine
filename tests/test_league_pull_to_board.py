"""The whole path of a league-wide pull, on isolated data and with the provider's own timestamps:
provider response -> pull workflow -> stored cache -> odds bridge -> odds_snapshots -> product quotes -> Slate Board (both moneyline sides and the spread) -> published snapshot.
Regression for 2026-10-18: the 15-minute trader ran the pull but not the bridge, so the board kept the previous night's moneylines."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import operational.system_health  # noqa: F401
from operational import cloud_snapshot_schema as schema
from operational import live_odds_daily_pull as lop
from operational import moneyline_freshness as mf
from operational import product_data
from operational import real_odds_bridge as rob
from operational import slate_board as sb
from tests.product_fixture import snapshot
from tests.test_league_pull import Isolated, NOW, event
from tests.test_live_odds_daily_pull import EVENT_B, _fake_response
from tests.test_real_odds_bridge import _fresh_conn, _seed_game
from tests.test_slate_board import avail, pool, product

PULL_AT = NOW                                                  # 2026-09-29 15:00Z: the response is retrieved now
H2H_UPDATED = "2026-09-29T14:55:00"                           # the provider's own quote times (naive, as stored), NOT the retrieval time
SPREADS_UPDATED = "2026-09-29T14:56:00"


class TestThePullReachesTheBoard(Isolated):
    def run_workflow(self, chain=True):
        events_resp = _fake_response(json_data=[EVENT_B], headers={"x-requests-remaining": "99", "x-requests-last": "0"})
        odds_resp = _fake_response(json_data=[event()], headers={"x-requests-remaining": "97", "x-requests-last": "2"})
        self.cache = self.dir / "ml_cache.json"
        self.conn = _fresh_conn()
        _seed_game(self.conn, 2026020999, "FLA", "CAR", "2026-09-29")
        real_sync = rob.sync_moneyline_odds_to_snapshots
        stub = mock.Mock(return_value={"status": "SUCCESS", "games_evaluated": 1, "recommendations_recorded": 0})
        patches = [mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", self.cache), mock.patch("operational.live_odds_daily_pull._now_utc", return_value=PULL_AT),
                   mock.patch("research.live_sog_pricing.archive.ARCHIVE_DIR", self.dir / "arch"), mock.patch.object(lop.client, "get_the_odds_api_key", return_value="fake"),
                   mock.patch("requests.get", side_effect=[events_resp, odds_resp]),
                   mock.patch.object(rob, "sync_moneyline_odds_to_snapshots", side_effect=lambda: real_sync(conn=self.conn, cache_path=self.cache, now=dt.datetime.now(dt.timezone.utc))),      # the fake response's retrieval time is the real clock's
                   mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations", stub)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.orchestrator = stub
        out = mf.run_if_due(PULL_AT, label="morning", due_fn=lambda n: {"due": True, "reason": "TEST", "target_max_age_min": None, "age_min": None}, guard_fn=lambda n: {"allow": True},
                            active_fn=lambda: True, state_path=self.dir / "ml_state.json", lock_path=self.dir / "ml.lock",
                            pull_fn=lambda: lop.run_moneyline_snapshot("morning"), chain_fn=mf.post_pull_chain if chain else None)
        return out

    def test_the_pull_workflow_itself_runs_the_bridge_and_the_recommendation_step(self):
        out = self.run_workflow()
        self.assertEqual(out["action"], "RAN")
        self.assertEqual((out["real_odds_bridge"]["status"], out["real_odds_bridge"]["rows_written"]), ("SUCCESS", 2))       # both sides of the one game
        self.orchestrator.assert_called_once()

    def test_a_pull_that_did_not_run_changes_nothing_downstream(self):
        self.assertEqual(mf.post_pull_chain({"ran": False}), {})
        self.assertEqual(mf.post_pull_chain({}), {})

    def test_both_moneyline_sides_are_stored_with_the_providers_quote_time_not_the_retrieval_time(self):
        self.run_workflow()
        rows = {r["selection"]: dict(r) for r in self.conn.execute("SELECT * FROM odds_snapshots WHERE market='MONEYLINE'")}
        self.assertEqual({k: v["price_american"] for k, v in rows.items()}, {"FLA": -150, "CAR": 130})
        self.assertEqual({v["captured_at_utc"] for v in rows.values()}, {H2H_UPDATED})                  # the provider's 14:55, not the 15:00 retrieval
        quotes = product_data.moneyline_quotes(self.conn, PULL_AT + dt.timedelta(minutes=10))
        q = quotes["2026020999"]
        self.assertEqual((q["FLA"]["american"], q["CAR"]["american"]), (-150, 130))
        self.assertEqual(q["FLA"]["quote_captured_at_utc"], "2026-09-29T14:55:00Z")
        self.assertEqual(q["FLA"]["age_min"], 15.0)                                                  # aged from the provider's time

    def test_the_board_shows_both_sides_and_the_spread_with_provider_times_and_no_missing_price(self):
        self.run_workflow()
        now = PULL_AT + dt.timedelta(minutes=10)
        q = product_data.moneyline_quotes(self.conn, now)["2026020999"]
        with mock.patch("operational.live_odds_daily_pull.MONEYLINE_CACHE_PATH", self.cache):
            spreads = product_data.puck_line_quotes(now)[("FLA", "CAR")]
        self.assertEqual((spreads["home_point"], spreads["home_price"], spreads["away_point"], spreads["away_price"]), (-1.5, 190, 1.5, -235))
        self.assertEqual(spreads["quote_updated_at_utc"], "2026-09-29T14:56:00Z")
        p = product()
        g = p["games"]["games"][0]
        g["moneyline"] = {"home": {**q["FLA"], "status": "ACTIVE"}, "away": {**q["CAR"], "status": "ACTIVE"}, "provider": "DraftKings"}
        g["puck_line"] = spreads
        doc = sb.build_doc(now, p, pool(), {}, avail(), {}, None)
        game = doc["games"][0]
        sides = game["side"]["sides"]
        self.assertEqual((sides["home"]["american"], sides["away"]["american"]), (-150.0, 130.0))
        self.assertEqual({sides["home"]["quote_captured_at_utc"], sides["away"]["quote_captured_at_utc"]}, {"2026-09-29T14:55:00Z"})
        pl = game["side"]["puck_line"]
        self.assertEqual(pl["status"], "QUOTED_NOT_ASSESSED")
        self.assertEqual((pl["price"]["home"]["american"], pl["price"]["away"]["american"], pl["price"]["quote_updated_utc"]), (190, -235, "2026-09-29T14:56:00Z"))
        self.assertNotIn("Puck line price", [m["item"] for m in game["coverage"]["missing"]])       # a quoted spread is a price we have; the unsupported model/contract is stated separately
        self.assertEqual(pl["model"]["status"], "UNVALIDATED")
        self.assertEqual(pl["contract"]["status"], "UNCERTIFIED")

    def test_the_published_snapshot_with_those_sections_validates(self):
        self.run_workflow()
        now = PULL_AT + dt.timedelta(minutes=10)
        q = product_data.moneyline_quotes(self.conn, now)["2026020999"]
        p = product()
        p["games"]["games"][0]["moneyline"] = {"home": {**q["FLA"], "status": "ACTIVE"}, "away": {**q["CAR"], "status": "ACTIVE"}, "provider": "DraftKings"}
        snap = snapshot()
        snap["slate_board"] = sb.build_doc(now, p, pool(), {}, avail(), {}, None)
        json.dumps(snap["slate_board"])
        res = schema.validate_snapshot(snap, for_publication=True)
        self.assertFalse(res.get("errors"), res.get("errors"))


if __name__ == "__main__":
    unittest.main()
