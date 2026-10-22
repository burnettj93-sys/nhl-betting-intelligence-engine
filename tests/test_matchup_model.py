"""The team matchup model (research/product_models/matchup.py) and the evidence file that backs it (docs/validation/eggy_evidence.json, built by deploy/build_eggy_evidence.py).

The state is the model's memory of each team, advanced one game at a time and read strictly BEFORE the date asked about, which is what makes a replay honest. The evidence file is a committed artifact: these
tests pin what the cards quote from it and the claims it must never make (an edge over the market, profit)."""
from __future__ import annotations

import datetime as dt
import json
import unittest

from research.product_models import matchup as m
from tests.test_eggy_intelligence import team_rows


def row(team, opp, date, gf, ga, home=True, **kw):
    base = {"g": f"{team}{opp}{date}", "s": 2026, "d": date, "t": team, "o": opp, "h": home, "gf": gf, "ga": ga, "xgf": gf, "xga": ga, "hdf": 1.0, "hda": 1.0, "pnf": 3.0, "pna": 3.0, "cf5": 50.0, "ca5": 50.0,
            "xgf5": 2.0, "xga5": 2.0, "pkt": 300.0, "pkx": 0.6, "ppt": 300.0, "ppx": 0.6}
    base.update(kw)
    return base


class TestState(unittest.TestCase):
    def test_the_state_is_built_from_games_strictly_before_the_date(self):
        rows = [row("AAA", "BBB", "2026-10-01", 5, 1), row("BBB", "AAA", "2026-10-01", 1, 5, home=False), row("AAA", "BBB", "2026-10-02", 5, 1)]
        st = m.build_state(rows, through_date="2026-10-02")
        self.assertEqual(st.played["AAA"], 1)                      # the game ON the date is not in the state
        self.assertEqual(st.newest, "2026-10-01")
        self.assertEqual(m.build_state(rows).played["AAA"], 2)     # no date: everything

    def test_a_few_games_are_shrunk_toward_the_league_mean_and_more_games_count_for_more(self):
        one = m.build_state([row("AAA", "BBB", "2026-10-01", 6, 0), row("BBB", "AAA", "2026-10-01", 0, 6, home=False)])
        many = m.build_state([row("AAA", "BBB", f"2026-10-{d:02d}", 6, 0) for d in range(1, 21)] + [row("BBB", "AAA", f"2026-10-{d:02d}", 0, 6, home=False) for d in range(1, 21)])
        self.assertGreater(one.est("AAA", "gd"), 0)
        self.assertLess(one.est("AAA", "gd"), 3.0)                 # a six-goal win in one game is not read as a six-goal team
        self.assertGreater(many.est("AAA", "gd"), one.est("AAA", "gd"))
        self.assertAlmostEqual(one.est("AAA", "gd"), -one.est("BBB", "gd"), places=6)

    def test_old_games_count_for_less(self):
        st = m.State()
        for d in range(1, 41):
            st.update(row("AAA", "BBB", (dt.date(2026, 9, 1) + dt.timedelta(days=d)).isoformat(), 6 if d <= 20 else 0, 0 if d <= 20 else 6))
        self.assertLess(st.est("AAA", "gd"), 0)                    # twenty recent six-goal losses outweigh twenty older six-goal wins

    def test_rest_days_and_the_back_to_back_feature(self):
        st = m.State()
        st.update(row("AAA", "CCC", "2026-10-19", 3, 2))
        st.update(row("BBB", "CCC", "2026-10-17", 3, 2))
        f = m.features(st, "AAA", "BBB", "2026-10-20")
        self.assertEqual((f["_rest_home"], f["_rest_away"]), (1, 3))
        self.assertEqual(f["b2b"], -1.0)                           # the home team is on a back-to-back and the away team is not: against the home team
        st.update(row("BBB", "CCC", "2026-10-19", 3, 2))
        self.assertEqual(m.features(st, "AAA", "BBB", "2026-10-20")["b2b"], 0.0)
        self.assertEqual(m.build_state([]).rest_days("ZZZ", "2026-10-20"), 7)       # a team with no games is fully rested

    def test_a_franchise_keeps_its_state_across_a_rename(self):
        self.assertEqual((m.alias("N.J"), m.alias("T.B"), m.alias("ATL"), m.alias("ARI"), m.alias("BOS")), ("NJD", "TBL", "WPG", "UTA", "BOS"))


class TestProbability(unittest.TestCase):
    COEFS = {"const": 0.2, "gd": 0.4, "xgd": 1.0, "hd": -1.0, "b2b": 0.25}

    def test_home_ice_alone_gives_the_home_team_a_small_edge(self):
        p, contrib = m.home_probability(self.COEFS, {"gd": 0, "xgd": 0, "hd": 0, "b2b": 0})
        self.assertAlmostEqual(p, m.sigmoid(0.2), places=6)
        self.assertEqual(set(contrib), {"home_ice", "gd", "xgd", "hd", "b2b"})

    def test_the_probability_is_monotone_in_the_better_team_and_symmetric_in_the_features(self):
        p1, _ = m.home_probability(self.COEFS, {"gd": 0.5, "xgd": 0.3, "hd": 0.1, "b2b": 0})
        p2, _ = m.home_probability(self.COEFS, {"gd": 1.0, "xgd": 0.3, "hd": 0.1, "b2b": 0})
        self.assertGreater(p2, p1)
        pa, _ = m.home_probability({**self.COEFS, "const": 0.0}, {"gd": 0.7, "xgd": 0.2, "hd": 0.1, "b2b": 0})
        pb, _ = m.home_probability({**self.COEFS, "const": 0.0}, {"gd": -0.7, "xgd": -0.2, "hd": -0.1, "b2b": 0})
        self.assertAlmostEqual(pa + pb, 1.0, places=6)

    def test_logit_and_sigmoid_are_inverse(self):
        for p in (0.05, 0.3, 0.5, 0.77, 0.95):
            self.assertAlmostEqual(m.sigmoid(m.logit(p)), p, places=6)


class TestView(unittest.TestCase):
    def setUp(self):
        self.ev = m.load_evidence()
        self.st = m.build_state(team_rows(), through_date="2026-10-20")

    def test_the_view_names_the_favourite_the_gap_the_drivers_and_the_indicators(self):
        v = m.view(self.st, self.ev, "AAA", "BBB", "2026-10-20")
        self.assertTrue(v["available"])
        self.assertEqual((v["favourite"], v["model"]), ("AAA", "eggy-matchup-v1"))
        self.assertAlmostEqual(v["p_home"] + v["p_away"], 1.0, places=3)
        self.assertEqual([d["feature"] for d in v["drivers"]], ["home_ice", "gd", "xgd", "hd", "b2b"])
        self.assertEqual(v["data_through"], "2026-10-19")
        self.assertEqual(set(v["indicator_votes"]), {"AAA", "BBB"})
        self.assertGreater(v["indicator_votes"]["AAA"], v["indicator_votes"]["BBB"])
        for t in ("AAA", "BBB"):
            self.assertEqual(len(v["indicators"][t]["last10"]), 5)
            self.assertIn("gd", v["indicators"][t]["ranks"])

    def test_swapping_home_and_away_moves_the_probability_by_home_ice_only(self):
        a = m.view(self.st, self.ev, "AAA", "BBB", "2026-10-20")
        b = m.view(self.st, self.ev, "BBB", "AAA", "2026-10-20")
        self.assertEqual(b["favourite"], "AAA")
        self.assertLess(b["p_favourite"], a["p_favourite"])

    def test_no_coefficients_or_an_unknown_team_is_unavailable_with_a_reason(self):
        self.assertFalse(m.view(self.st, None, "AAA", "BBB", "2026-10-20")["available"])
        v = m.view(self.st, self.ev, "AAA", "ZZZ", "2026-10-20")
        self.assertFalse(v["available"])
        self.assertIn("no games", v["reason"])

    def test_the_gap_is_described_from_where_the_matchup_sits_among_tested_matchups(self):
        pct = self.ev["matchup"]["favourite_percentiles"]
        self.assertEqual(m._label_gap(pct["p95"] + 0.01, pct), "DOMINANT")
        self.assertEqual(m._label_gap(pct["p85"] + 0.001, pct), "STRONG")
        self.assertEqual(m._label_gap(pct["p65"] + 0.001, pct), "CLEAR")
        self.assertEqual(m._label_gap(pct["p35"] + 0.001, pct), "SLIGHT")
        self.assertEqual(m._label_gap(0.5, pct), "NEGLIGIBLE")
        self.assertEqual(m._label_gap(0.7, None), "UNRATED")

    def test_the_historical_analogue_for_a_probability_is_its_reliability_band(self):
        a = m.analogues(self.ev, 0.62)
        self.assertGreater(a["n"], 500)
        self.assertLess(abs(a["predicted"] - a["observed"]), 4 * a["standard_error"])           # in the tested seasons the model's 60-65% sides won about that often
        self.assertIn("2017-2025", a["seasons"])
        self.assertIsNone(m.analogues(None, 0.6))
        self.assertIsNone(m.analogues({"matchup": {}}, 0.6))


class TestEvidenceFile(unittest.TestCase):
    """What the committed evidence file may and may not say."""

    @classmethod
    def setUpClass(cls):
        cls.ev = m.load_evidence()

    def test_the_coefficients_are_the_four_features_and_a_constant(self):
        self.assertEqual(set(self.ev["matchup"]["coefficients"]), {"const", "gd", "xgd", "hd", "b2b"})
        self.assertEqual(self.ev["matchup"]["features"], ["gd", "xgd", "hd", "b2b"])
        self.assertEqual((self.ev["matchup"]["half_life_games"], self.ev["matchup"]["shrink_games"]), (m.HALF_LIFE_GAMES, m.SHRINK_GAMES))

    def test_the_model_beat_the_simple_baselines_chronologically_with_intervals_that_exclude_zero(self):
        e = self.ev["matchup"]["evaluation"]
        for k in ("delta_vs_gd_only", "delta_vs_gd_plus_xgd", "delta_vs_home_rate"):
            self.assertLess(e[k]["ci_high"], 0, k)
        self.assertGreater(e["n_games"], 10000)
        self.assertIn("expanding window", e["test_seasons"])

    def test_the_feature_set_was_chosen_on_an_earlier_period_than_it_was_tested(self):
        self.assertEqual(self.ev["matchup"]["feature_selection"]["validation_seasons"], "2014-2016")
        self.assertTrue(self.ev["matchup"]["evaluation"]["test_seasons"].startswith("2017-2025"))

    def test_reliability_bands_cover_the_range_without_gaps(self):
        bands = self.ev["matchup"]["reliability_side"]
        self.assertEqual(bands[0]["lo"], 0.0)
        for a, b in zip(bands, bands[1:]):
            self.assertAlmostEqual(a["hi"], b["lo"], places=6)
        self.assertGreaterEqual(bands[-1]["hi"], 1.0)

    def test_it_never_claims_an_edge_over_the_market_or_any_profit(self):
        text = json.dumps(self.ev).lower()
        for phrase in ("beats the market", "beat the market", "profitable", "positive roi", "guaranteed"):
            self.assertNotIn(phrase, text)
        self.assertTrue(any("no historical sportsbook prices" in n.lower() for n in self.ev["notes"]))
        pros = self.ev["matchup"]["prospective_vs_market"]
        self.assertEqual(set(pros["log_loss"]), {"M", "H", "S", "E"})
        self.assertLessEqual(pros["weight_on_disagreement"]["ci95"][0], 0.0)                     # the data cannot reject "the model's disagreement is worth nothing"

    def test_the_skater_cells_carry_their_sample_sizes(self):
        cells = self.ev["skater"]["cells"]
        self.assertEqual(set(cells), {"goals>=1", "points>=1", "points>=2", "shots>=2", "shots>=3", "shots>=4", "shots>=5"})
        for market, rows in cells.items():
            self.assertTrue(rows, market)
            for c in rows:
                n, pred, obs = c[-3], c[-2], c[-1]
                self.assertGreaterEqual(n, 1)
                self.assertTrue(0.0 <= pred <= 1.0 and 0.0 <= obs <= 1.0)
        self.assertEqual(self.ev["skater"]["rows"], sum(c[-3] for c in cells["goals>=1"] if c[0] == "p"))



class TestTheBuilderLooksAheadOfNothing(unittest.TestCase):
    """deploy/build_eggy_evidence.py fits and tests the team model on features that must be exactly what the live review would have had: built from games strictly BEFORE each game's date."""

    def rows(self):
        import copy
        return copy.deepcopy(team_rows(days=30))

    def test_a_games_own_score_never_changes_its_own_features_or_those_of_any_game_that_day(self):
        from deploy import build_eggy_evidence as B
        base = {r["game"]: r for r in B.walk_features(self.rows())}
        rows = self.rows()
        target = rows[100]                                                  # one team's row of one game; make its result wildly different
        day = target["d"]
        target["gf"], target["gd"] = target["gf"] + 9, 9
        target["xgf"] += 9
        changed = {r["game"]: r for r in B.walk_features(rows)}
        same_day = [g for g, r in base.items() if r["date"] == day]
        self.assertTrue(same_day)
        for g in same_day:
            self.assertEqual(changed[g]["f"], base[g]["f"], g)               # nothing that happened that day reaches any feature of that day
        later = [g for g, r in base.items() if r["date"] > day and target["t"] in (r["home"], r["away"])]
        self.assertTrue(later)
        self.assertTrue(any(changed[g]["f"] != base[g]["f"] for g in later))   # ... but it does reach the team's later games (the detector is not blind)

    def test_the_outcome_is_the_games_own_and_a_shootout_has_none(self):
        from deploy import build_eggy_evidence as B
        rows = self.rows()
        for r in rows:
            if r["g"] == rows[0]["g"]:
                r["gf"] = r["ga"] = 3.0                                      # a tie after regulation: decided in a shootout, no outcome for the fit
        out = {r["game"]: r for r in B.walk_features(rows)}
        self.assertIsNone(out[rows[0]["g"]]["y"])

    def test_the_chosen_feature_set_must_be_the_one_the_model_hard_codes(self):
        from deploy import build_eggy_evidence as B
        self.assertEqual(B.CANDIDATES["gd+xgd+hd+b2b"], m.MODEL_FEATURES)
        self.assertFalse(hasattr(B, "FINAL"))


if __name__ == "__main__":
    unittest.main()
