"""The prospective record of the team model against the market (operational/prospective_moneyline.py): measured on every finished priced game, trust earned by evidence, nothing retrained."""
from __future__ import annotations

import json
import random
import sqlite3
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import prospective_moneyline as pm
from tests import test_eggy_intelligence as tei


def rows(n, bias=0.0, seed=1, model_better=False):
    """n synthetic finished games: the true home win chance varies; the market sees it with noise; the hockey model sees it with less noise if model_better."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        true = min(max(rng.gauss(0.55, 0.12), 0.2), 0.85)
        market = min(max(true + rng.gauss(0, 0.07 if model_better else 0.01), 0.05), 0.95)
        hockey = min(max(true + bias + rng.gauss(0, 0.01 if model_better else 0.05), 0.05), 0.95)
        out.append({"game_id": str(i), "date": "2026-10-10", "home": "AAA", "away": "BBB", "hockey_home": hockey, "market_home": market, "elo_home": 0.5, "strength_home": 0.5, "home_won": int(rng.random() < true)})
    return out


class TestSummary(unittest.TestCase):
    def test_too_few_games_is_insufficient_evidence_with_the_numbers_still_shown(self):
        s = pm.summarise(rows(40))
        self.assertEqual(s["state"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("home_side_log_loss", s)

    def test_a_model_that_sees_more_than_the_market_is_a_possible_edge_only_when_z_clears_two(self):
        s = pm.summarise(rows(4000, model_better=True))
        self.assertEqual(s["state"], "MODEL_MAY_HAVE_EDGE")
        self.assertGreaterEqual(s["z"], 2.0)

    def test_a_model_the_market_beats_is_labelled_market_probably_better(self):
        s = pm.summarise(rows(4000, model_better=False))
        self.assertEqual(s["state"], "MARKET_PROBABLY_BETTER")

    def test_a_biased_model_gap_band_reads_as_overconfidence_not_edge(self):
        s = pm.summarise(rows(6000, bias=0.045, seed=5))
        up = s["by_gap_band"][pm.BANDS[1][0]]
        self.assertGreaterEqual(up["games"], pm.MIN_GAMES_PER_BAND)
        self.assertEqual(up["state"], "DISAGREEMENT_IS_OVERCONFIDENCE")

    def test_the_within_three_points_band_is_agreement_and_band_state_reads_the_right_band(self):
        s = pm.summarise(rows(3000))
        self.assertEqual(pm.band_state(s, 0.0), "MODEL_AND_MARKET_AGREE")
        self.assertEqual(pm.band_state(None, 0.05), "INSUFFICIENT_EVIDENCE")
        self.assertEqual(pm.band_state(s, 0.05), s["by_gap_band"][pm.BANDS[1][0]]["state"])
        self.assertEqual(pm.band_state(s, -0.05), pm.band_state(s, 0.05))                          # the side the model likes LESS is the other end of the same game: the same band, the same evidence


class TestIndependentObservations(unittest.TestCase):
    def test_every_game_is_counted_once_across_the_bands(self):
        r = rows(900, bias=0.02, seed=3)
        s = pm.summarise(r)
        self.assertEqual(sum(b["games"] for b in s["by_gap_band"].values()), len(r))                # no mirrored band: the bands PARTITION the games

    def test_a_band_never_exceeds_the_games_that_exist(self):
        r = rows(250, bias=0.045, seed=9)
        s = pm.summarise(r)
        self.assertLessEqual(max(b["games"] for b in s["by_gap_band"].values()), len(r))

    def test_the_threshold_is_not_loosened_because_the_count_is_smaller(self):
        self.assertEqual(pm.MIN_GAMES_PER_BAND, 100)
        self.assertEqual(pm.MIN_GAMES, 100)
        s = pm.summarise(rows(150, bias=0.06, seed=4))
        self.assertTrue(all(b["state"] in ("INSUFFICIENT_EVIDENCE", "MODEL_AND_MARKET_AGREE") for b in s["by_gap_band"].values() if b["games"] < 100))

    def test_the_old_double_counted_sample_would_have_cleared_the_bar_that_the_games_do_not(self):
        r = rows(120, bias=0.05, seed=2)
        s = pm.summarise(r)
        mirrored_sides = 2 * sum(b["games"] for b in s["by_gap_band"].values())
        self.assertGreater(mirrored_sides, 100)
        self.assertTrue(all(b["games"] < 100 for b in s["by_gap_band"].values()))


class TestTheLedger(unittest.TestCase):
    def test_a_finished_priced_game_is_recorded_once_and_a_game_without_both_sides_or_a_result_never(self):
        import tempfile
        from pathlib import Path
        from research.product_models import matchup as M
        nhl = sqlite3.connect(":memory:")
        nhl.row_factory = sqlite3.Row
        nhl.execute("CREATE TABLE games (game_id INTEGER, game_state TEXT, home_score INTEGER, away_score INTEGER)")
        nhl.executemany("INSERT INTO games VALUES (?,?,?,?)", [(1, "FINAL", 3, 2), (2, "SCHEDULED", None, None), (3, "FINAL", 1, 4)])
        side = lambda gid, team, p: {"game_id": str(gid), "game_date": "2026-10-10", "home": "AAA", "away": "BBB", "selection": team, "market_no_vig": p, "elo": 0.5, "strength": 0.5, "price": -120.0}
        log = [side(1, "AAA", 0.6), side(1, "BBB", 0.4), side(2, "AAA", 0.6), side(2, "BBB", 0.4), side(3, "AAA", 0.6)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.jsonl"
            with mock.patch.object(pm, "_path", return_value=path), mock.patch.object(M, "build_state", return_value=object()), \
                    mock.patch.object(M, "view", return_value={"available": True, "p_home": 0.63, "p_away": 0.37}):
                first = pm.refresh(nhl, read_log=lambda: log, team_rows=[], evidence={})
                again = pm.refresh(nhl, read_log=lambda: log, team_rows=[], evidence={})
                got = pm.read_ledger()
        self.assertEqual(first["added"], 1)                                                      # game 1 only: 2 is not final, 3 has one side
        self.assertEqual(again["added"], 0)                                                      # once
        self.assertEqual((got[0]["home_won"], got[0]["market_home"], got[0]["hockey_home"]), (1, 0.6, 0.63))


class TestTheReviewReadsIt(unittest.TestCase):
    def test_a_moneyline_card_carries_the_trust_state_of_its_gap_band(self):
        t = tei.TestMoneylineReview()
        t.setUp()
        t.ctx.moneyline_trust = {"state": "MARKET_PROBABLY_BETTER", "games": 250, "by_gap_band": {pm.BANDS[1][0]: {"state": "DISAGREEMENT_IS_OVERCONFIDENCE"}}}
        c = t.review("AAA", shift=-0.05)
        self.assertEqual(c["market_trust"]["disagreement_state"], "DISAGREEMENT_IS_OVERCONFIDENCE")
        self.assertEqual(c["market_trust"]["overall_state"], "MARKET_PROBABLY_BETTER")
        self.assertEqual(c["decision_layers"]["D_model_trust"]["disagreement_trust"]["finished_games_measured"], 250)

    def test_without_a_record_the_state_is_insufficient_evidence(self):
        t = tei.TestMoneylineReview()
        t.setUp()
        self.assertEqual(t.review("AAA", shift=-0.05)["market_trust"]["disagreement_state"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
