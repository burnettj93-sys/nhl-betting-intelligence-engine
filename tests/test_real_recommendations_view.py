"""
Real Recommendation Pipeline block (2026-09-24), Parts 2/12/13: tests
for dashboard/real_recommendations_view.py -- the real, non-demo
counterpart to dashboard/eligible_bets.py. All isolated temp databases;
never the real prospective_observations.db or paper_bankroll.db.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dashboard import eligible_bets
from dashboard import real_recommendations_view as rrv
from dashboard.live_dk import LIVE_SOURCE_LABEL, SIMULATED_SOURCE_LABEL
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl

EVENT_START = "2026-10-15T23:00:00.000000Z"
CUTOFF = "2026-10-15T18:00:00.000000Z"


def _tmp_pl_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pl.init_db(Path(path)), Path(path)


def _tmp_bankroll_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pb.init_db(Path(path)), Path(path)


class TestRealMoneylineRecommendations(unittest.TestCase):
    def setUp(self):
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()

    def tearDown(self):
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def _record(self, side, prospective_status):
        return pl.record_model_observation(
            self.pl_conn, event_start_utc=EVENT_START, created_at_utc=CUTOFF,
            prediction_cutoff_utc=CUTOFF, game_id="1", game_date="2026-10-15", team=side,
            opponent="CHI" if side == "EDM" else "EDM", market_id="MONEYLINE", side=side,
            raw_probability=0.6, conservative_probability=0.55, odds_american=-150,
            sportsbook="DraftKings", prospective_status=prospective_status)

    def test_a_bet_row_is_labeled_with_the_canonical_live_draftkings_label(self):
        self._record("EDM", "BET")
        pb.record_paper_bet(self.bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                             market_id="MONEYLINE", entry_odds=-150, event_id="1", team="EDM")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        edm = next(r for r in rows if r["team"] == "EDM")
        self.assertEqual(edm["source"], LIVE_SOURCE_LABEL)
        self.assertFalse(edm["is_demo"])
        self.assertTrue(edm["has_real_paper_bet"])

    def test_a_pass_row_never_gets_the_live_label(self):
        self._record("CHI", "PASS")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        chi = next(r for r in rows if r["team"] == "CHI")
        self.assertNotEqual(chi["source"], LIVE_SOURCE_LABEL)
        self.assertIn("REAL MARKET", chi["source"])
        self.assertIn("PASS", chi["source"])
        self.assertFalse(chi["has_real_paper_bet"])

    def test_never_returns_a_demo_row(self):
        """dashboard/eligible_bets.py rows must never appear in this
        module's output, and vice versa -- distinct provenance, Part 12."""
        self._record("EDM", "BET")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        for r in rows:
            self.assertFalse(r["is_demo"])
            self.assertNotEqual(r["source"], SIMULATED_SOURCE_LABEL)

    def test_filters_by_team_and_by_game(self):
        self._record("EDM", "BET")
        self._record("CHI", "PASS")
        rows = rrv.real_moneyline_recommendations(pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn)
        edm_only = rrv.real_moneyline_recommendations_for_team("EDM", rows)
        self.assertEqual([r["team"] for r in edm_only], ["EDM"])
        game_rows = rrv.real_moneyline_recommendations_for_game("EDM", "CHI", rows)
        self.assertEqual({r["team"] for r in game_rows}, {"EDM", "CHI"})


class TestZeroQualifyingBetsState(unittest.TestCase):
    def test_no_rows_at_all_is_no_qualifying_bets(self):
        self.assertEqual(rrv.real_market_summary_label([]), rrv.NO_QUALIFYING_BETS_LABEL)

    def test_rows_with_no_paper_bet_is_still_no_qualifying_bets(self):
        rows = [{"has_real_paper_bet": False}, {"has_real_paper_bet": False}]
        self.assertEqual(rrv.real_market_summary_label(rows), rrv.NO_QUALIFYING_BETS_LABEL)

    def test_a_real_qualifying_bet_is_reported_honestly(self):
        rows = [{"has_real_paper_bet": True}, {"has_real_paper_bet": False}]
        self.assertIn("1", rrv.real_market_summary_label(rows))
        self.assertNotEqual(rrv.real_market_summary_label(rows), rrv.NO_QUALIFYING_BETS_LABEL)


class TestDemoRowsAreLabeledSimulated(unittest.TestCase):
    """dashboard/eligible_bets.py's own existing demo rows -- confirms
    Part 2's labeling was added without breaking the demo path."""

    def test_player_prop_rows_carry_the_simulated_label(self):
        rows = eligible_bets.build_all_player_prop_opportunities()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["source"], SIMULATED_SOURCE_LABEL)
            self.assertTrue(r["is_demo"])

    def test_goalie_saves_rows_carry_the_simulated_label(self):
        rows = eligible_bets.build_goalie_saves_opportunities()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["source"], SIMULATED_SOURCE_LABEL)
            self.assertTrue(r["is_demo"])


class TestRealPropOpportunityShapeAdapter(unittest.TestCase):
    """Live SOG + Saves Production Certification block (2026-09-24),
    Parts 19-22: the minimum shape adapter feeding real SOG/Saves
    observations into the existing Top Conviction / Game Edge Parlay
    engines, unmodified."""

    def _sog_bet_row(self, **overrides):
        row = {
            "market_family": "SOG", "player_id": "8475166", "player_name_snapshot": "John Tavares",
            "team": "TOR", "opponent": "BOS", "market_id": "PLAYER_SOG_4PLUS", "threshold": "4+",
            "side": "OVER", "prospective_status": "BET", "confidence": "HIGH",
            "conservative_probability": 0.60, "raw_probability": 0.65,
            "market_no_vig_probability": 0.50, "odds_american": -115,
        }
        row.update(overrides)
        return row

    def test_bet_row_converts_to_a_valid_top_conviction_candidate(self):
        from dashboard import conviction
        opp = rrv.real_prop_observation_to_opportunity_shape(self._sog_bet_row())
        self.assertEqual(opp["prop"], "sog")
        self.assertEqual(opp["decision"], "BET")
        self.assertGreater(opp["conservative_edge"], 0)
        self.assertGreater(opp["ev"], 0)
        ranked = conviction.top_conviction([opp])
        self.assertEqual(len(ranked), 1)

    def test_saves_market_family_maps_to_lowercase_saves_prop(self):
        row = self._sog_bet_row(market_family="GOALIE_SAVES", market_id="GOALIE_SAVES_20PLUS", threshold="20+")
        opp = rrv.real_prop_observation_to_opportunity_shape(row)
        self.assertEqual(opp["prop"], "saves")

    def test_unrecognized_market_family_returns_none(self):
        row = self._sog_bet_row(market_family="MONEYLINE")
        self.assertIsNone(rrv.real_prop_observation_to_opportunity_shape(row))

    def test_wait_row_never_qualifies_for_top_conviction(self):
        from dashboard import conviction
        opp = rrv.real_prop_observation_to_opportunity_shape(self._sog_bet_row(prospective_status="WAIT"))
        self.assertEqual(conviction.top_conviction([opp]), [])

    def test_no_real_observations_yields_no_qualifying_game_edge_parlay(self):
        """The real, current, honest state: zero real SOG/Saves
        observations exist (no contract verified yet -- Part 33), so the
        existing Game Edge Parlay engine correctly refuses to qualify a
        parlay, never manufacturing one from nothing (Part 20/31)."""
        from research.game_edge_parlay.engine import build_game_edge_parlay
        result = build_game_edge_parlay([], "TOR", "BOS")
        self.assertEqual(result["status"], "NO_QUALIFYING_GAME_EDGE_PARLAY")

    def test_real_prop_recommendations_for_conviction_and_parlay_reads_isolated_ledger(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        path = Path(path)
        path.unlink()
        pl_conn = pl.init_db(path)
        try:
            pl.record_model_observation(
                pl_conn, event_start_utc=EVENT_START, created_at_utc=CUTOFF, prediction_cutoff_utc=CUTOFF,
                game_id="1", game_date="2026-10-15", player_id="8475166", team="TOR", opponent="BOS",
                market_id="PLAYER_SOG_4PLUS", market_family="SOG", threshold="4+", side="OVER",
                raw_probability=0.45, conservative_probability=0.40, market_no_vig_probability=0.30,
                odds_american=-115, prospective_status="BET")
            opps = rrv.real_prop_recommendations_for_conviction_and_parlay(pl_conn=pl_conn)
            self.assertEqual(len(opps), 1)
            self.assertEqual(opps[0]["prop"], "sog")
        finally:
            pl_conn.close()
            path.unlink(missing_ok=True)

    def test_three_real_bet_legs_reach_the_parlay_engines_own_quality_bar_no_adapter_bug(self):
        """Starting-Goalie Certainty + Prop Contract Watch block
        (2026-09-24), Part 10: proves the adapter feeds
        research/game_edge_parlay/engine.py::game_eligible_legs() cleanly
        -- all 3 real-shaped legs are recognized as eligible (no field
        this adapter produces is missing/malformed) -- so any
        NO_QUALIFYING_GAME_EDGE_PARLAY result comes from the unmodified
        parlay engine's own real joint-probability/dependence math, never
        a structural adapter defect. No bug was found; nothing was
        changed in the parlay engine."""
        from research.game_edge_parlay.engine import build_game_edge_parlay, game_eligible_legs

        def row(prop, market_family, player_id, threshold, conservative_p, raw_p, no_vig, odds):
            return {
                "market_family": market_family, "player_id": player_id, "player_name_snapshot": player_id,
                "team": "TOR", "opponent": "BOS", "market_id": f"{market_family}_{threshold}",
                "threshold": threshold, "side": "OVER", "prospective_status": "BET", "confidence": "HIGH",
                "conservative_probability": conservative_p, "raw_probability": raw_p,
                "market_no_vig_probability": no_vig, "odds_american": odds,
            }

        rows = [
            row("sog", "SOG", "P1", "3+", 0.62, 0.66, 0.50, -115),
            row("sog", "SOG", "P2", "2+", 0.70, 0.74, 0.55, -140),
            row("saves", "GOALIE_SAVES", "G1", "20+", 0.65, 0.68, 0.52, -120),
        ]
        opps = [rrv.real_prop_observation_to_opportunity_shape(r) for r in rows]

        legs = game_eligible_legs(opps, "TOR", "BOS")
        self.assertEqual(len(legs), 3, "the unmodified parlay engine's own eligibility filter rejected a "
                                        "well-formed real leg -- this WOULD be a structural adapter bug")

        result = build_game_edge_parlay(opps, "TOR", "BOS")
        self.assertIn(result["status"], ("QUALIFIED", "NO_QUALIFYING_GAME_EDGE_PARLAY"))


if __name__ == "__main__":
    unittest.main()
