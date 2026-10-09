"""The recommendation audit must be able to FAIL: wrong team and a small-sample player marked eligible are both caught.
Uses the engine's own stored product state and roster database; skipped where those do not exist (a fresh worktree)."""
import copy
import json
import unittest
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "operational" / "runtime" / "product_state.json"
GAMES = STATE.parent / "product_skater_games.jsonl.gz"


@unittest.skipUnless(STATE.exists() and GAMES.exists(), "needs the engine's stored product state")
class TestRecommendationAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db
        from deploy import audit_recommendations as A
        cls.A = A
        cls.state = json.loads(STATE.read_text())
        cls.conn = db.get_conn()
        cls.hist = A._history()
        cls.now = "2026-10-09T14:30:00Z"
        cls.pid = next(k for k, v in cls.state["players"].items() if v.get("next_game") and (v.get("games_total") or 0) >= 200)

    def run_one(self, p, published=()):
        return self.A.audit_player(self.pid, "t", p, self.conn, self.hist, self.now, set(published))

    def test_unmodified_established_player_passes(self):
        self.assertTrue(self.run_one(copy.deepcopy(self.state["players"][self.pid]))["checks"]["sample_gate"]["ok"])

    def test_wrong_team_is_caught(self):
        p = copy.deepcopy(self.state["players"][self.pid])
        p["team"] = "ZZZ"
        r = self.run_one(p)
        self.assertFalse(r["checks"]["identity_team"]["ok"])

    def test_small_sample_marked_eligible_is_caught(self):
        p = copy.deepcopy(self.state["players"][self.pid])
        p["projection"]["games_observed"] = self.A.MIN_GAMES_FOR_PRICING - 1
        p["projection"]["pricing_eligible"] = True
        self.assertFalse(self.run_one(p)["checks"]["sample_gate"]["ok"])

    def test_small_sample_in_a_published_option_is_caught(self):
        p = copy.deepcopy(self.state["players"][self.pid])
        p["projection"]["games_observed"] = 10
        p["projection"]["pricing_eligible"] = False
        self.assertFalse(self.run_one(p, {self.pid})["checks"]["sample_gate"]["ok"])


if __name__ == "__main__":
    unittest.main()
