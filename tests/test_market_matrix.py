import json
import unittest

from operational import daily_tickets as dtk
from operational import market_matrix as mm
from operational import paper_bankroll as pb
from research.real_market_parlay import engine as rmp
from tests.test_daily_tickets import NOW, board, fresh_ledger

VERD = {f"shots>={k}": {"verdict": "BEATS_BASELINES"} for k in range(1, 6)} | {"points>=1": {"verdict": "BEATS_BASELINES"}, "points>=2": {"verdict": "BEATS_BASELINES"},
                                                                            "goals>=1": {"verdict": "BEATS_BASELINES"}, "hits>=1": {"verdict": "DOES_NOT_BEAT_BASELINES"}}
SAVES = {f"saves>={k}": {"verdict": "BEATS_BASELINES"} for k in range(20, 36)}


class TestMatrix(unittest.TestCase):
    def test_every_requested_market_has_a_row_with_the_six_kinds_of_evidence_kept_apart(self):
        rows = mm.build(VERD, SAVES, None, data_through={"skaters": "2026-10-07", "goalies": "2026-10-07", "schedule_results": "2026-10-09T02:00:00Z"})
        names = [r["market"] for r in rows]
        for wanted in ("Shots on goal", "Points", "Anytime goal", "Goalie saves", "Moneyline", "Puck line"):
            self.assertTrue(any(wanted in n for n in names), wanted)
        for r in rows:
            for k in ("status", "data", "contract", "validation", "calibration", "betting_value", "live"):
                self.assertTrue(r[k], (r["market"], k))
        by = {r["market"]: r for r in rows}
        self.assertTrue(by["Shots on goal (alternate ladder)"]["contract"].startswith("VERIFIED"))
        self.assertIn("NOT VERIFIED", by["Puck line / spread"]["contract"])
        self.assertIn("BLOCKED", by["Goalie saves"]["status"])
        self.assertIn("BLOCKED", by["Puck line / spread"]["status"])
        self.assertIn("does NOT beat the baseline", by["Hits / blocks"]["validation"])

    def test_no_row_claims_a_betting_edge(self):
        rows = mm.build(VERD, SAVES, None, data_through={})
        for r in rows[:3]:
            self.assertIn("no historical sportsbook prices", r["betting_value"])
        blob = json.dumps(rows).lower()
        self.assertNotIn("proven edge", blob)
        self.assertNotIn("profitable", blob.replace("profitability is not claimed", ""))

    def test_live_stats_come_from_the_ledger_and_expected_wins_are_the_models_own(self):
        path, conn = fresh_ledger()
        picked = rmp.select_tickets(board(4, price=-105, p=0.62), max_tickets=2)
        dtk.record_tickets(conn, picked["tickets"], NOW)
        for row in pb.query_paper_bets(conn, track="REAL_MARKET_PAPER"):
            legs = json.loads(row["legs_json"])
            res = [{"leg": l, "outcome": "WIN" if i == 0 else "LOSS"} for i, l in enumerate(legs)]
            pb.settle_paper_bet(conn, row["paper_bet_id"], "LOSS", settlement_json=json.dumps({"leg_results": res}))
        s = mm.live_leg_stats(conn)["PLAYER_SOG_ALTERNATE"]
        self.assertEqual(s["legs"], 4)
        self.assertEqual((s["won"], s["lost"], s["open"]), (2, 2, 0))
        self.assertAlmostEqual(s["expected_wins"], 4 * 0.62, places=2)
        self.assertIn("2 won, 2 lost", mm._live_text(s))
        self.assertEqual(mm.live_leg_stats(None)["PLAYER_POINTS"]["legs"], 0)


if __name__ == "__main__":
    unittest.main()
