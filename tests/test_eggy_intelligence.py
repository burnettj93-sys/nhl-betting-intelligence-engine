"""The Eggy intelligence review (operational/eggy_intelligence.py): the rules that turn what is known into a decision, tested on synthetic teams and players with controlled evidence, so that every threshold,
every ordering and every "never" is pinned. No data file is read except the committed evidence file (docs/validation/eggy_evidence.json), and only for the team model's fitted coefficients."""
from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import json
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from research.product_models import matchup
from research.real_market_parlay.engine import ParlayLeg

NOW = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)
AS_OF = "2026-10-20"


# ------------------------------------------------------------------------------------------------ synthetic teams
def team_rows(days: int = 45, start: dt.date = dt.date(2026, 9, 5)) -> list[dict]:
    """Six teams playing every day: AAA strong, BBB weak, CCC to FFF average. Deterministic."""
    teams = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    att = {"AAA": 3.8, "BBB": 2.2, "CCC": 3.0, "DDD": 3.0, "EEE": 3.0, "FFF": 3.0}
    dfn = {"AAA": 2.2, "BBB": 3.8, "CCC": 3.0, "DDD": 3.0, "EEE": 3.0, "FFF": 3.0}
    pairings = [[(0, 1), (2, 3), (4, 5)], [(0, 2), (1, 4), (3, 5)], [(0, 3), (1, 5), (2, 4)]]
    rows, gid = [], 100
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        for a, b in pairings[i % 3]:
            gid += 1
            for t, o, home in ((teams[a], teams[b], True), (teams[b], teams[a], False)):
                gf, ga = (att[t] + dfn[o]) / 2, (att[o] + dfn[t]) / 2
                rows.append({"g": str(gid), "s": 2026, "d": d, "t": t, "o": o, "h": home, "gf": gf, "ga": ga, "xgf": gf * 0.95, "xga": ga * 0.95, "hdf": gf * 0.3, "hda": ga * 0.3, "pnf": 3.0, "pna": 3.0,
                             "cf5": 50.0 + 4 * (gf - ga), "ca5": 50.0 - 4 * (gf - ga), "xgf5": gf * 0.7, "xga5": ga * 0.7, "pkt": 300.0, "pkx": 0.6, "ppt": 300.0, "ppx": 0.6})
    rows.sort(key=lambda r: (r["d"], r["g"], r["t"]))
    return rows


def make_ctx(**kw) -> ei.Context:
    ctx = ei.Context(now=NOW, as_of=AS_OF, evidence=matchup.load_evidence())
    ctx.team_state = matchup.build_state(team_rows(), through_date=AS_OF)
    ctx.games = {"1": {"home": "AAA", "away": "BBB", "start_utc": "2026-10-20T23:00:00"}}
    ctx.skater_state = {"players": {}, "newest_game_date": "2026-10-19"}
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def base_leg(**kw) -> ParlayLeg:
    base = dict(game_id="1", event_id="e1", market_family="MONEYLINE", participant_id="AAA", participant_name="AAA", side="HOME", threshold=None, american_price=-150.0, conservative_probability=0.6,
                sportsbook="DRAFTKINGS", captured_at_utc="2026-10-20T20:50:00Z", provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True,
                team="AAA", opponent="BBB", game_start_utc="2026-10-20T23:00:00", model_version="m1", provider_start_utc="2026-10-20T23:00:00Z", retrieved_at_utc="2026-10-20T20:50:00Z",
                quote_updated_utc="2026-10-20T20:45:00Z", quote_age_min=10.0, freshness_status="FRESH", model_probability=0.5, market_probability=0.6)
    base.update(kw)
    return ParlayLeg(**base)


def prob_to_american(p: float) -> float:
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def ml_leg(ctx: ei.Context, team: str, shift: float = 0.0, elo: float = 0.5) -> ParlayLeg:
    """A moneyline leg on `team` priced `shift` probability points ABOVE the hockey model's own probability for it (so shift 0 is a fair price, a negative shift is a price the model calls too long)."""
    v = ei.team_view(ctx, "AAA", "BBB")
    h = v["p_home"] if team == "AAA" else v["p_away"]
    m = min(max(h + shift, 0.05), 0.95)
    opp = "BBB" if team == "AAA" else "AAA"
    return base_leg(participant_id=team, participant_name=team, team=team, opponent=opp, side="HOME" if team == "AAA" else "AWAY", market_probability=m, conservative_probability=m,
                    american_price=float(prob_to_american(m * 1.04)), model_probability=elo)


# ------------------------------------------------------------------------------------------------ the rules
class TestRules(unittest.TestCase):
    def test_contradiction_counts_flags_it_does_not_weight_them(self):
        F = lambda *lv: [{"name": f"f{i}", "level": l, "text": ""} for i, l in enumerate(lv)]
        self.assertEqual(ei.combine_contradiction([]), "LOW")
        self.assertEqual(ei.combine_contradiction(F("LOW", "LOW", "LOW")), "LOW")
        self.assertEqual(ei.combine_contradiction(F("MODERATE")), "MODERATE")
        self.assertEqual(ei.combine_contradiction(F("MODERATE", "MODERATE")), "HIGH")
        self.assertEqual(ei.combine_contradiction(F("HIGH")), "HIGH")
        self.assertEqual(ei.combine_contradiction(F("HIGH", "HIGH")), "SEVERE")
        self.assertEqual(ei.combine_contradiction(F("SEVERE")), "SEVERE")
        self.assertEqual(ei.combine_contradiction(F("LOW", "MODERATE", "LOW")), "MODERATE")

    def test_calibration_confidence_levels(self):
        self.assertEqual(ei.calibration_level(None, None, None, None), "INSUFFICIENT")
        self.assertEqual(ei.calibration_level(5000, 0.30, 0.31, 0.007), "HIGH")            # plentiful and within two standard errors
        self.assertEqual(ei.calibration_level(500, 0.30, 0.31, 0.02), "MEDIUM")           # 300+ and within two standard errors
        self.assertEqual(ei.calibration_level(5000, 0.30, 0.36, 0.007), "LOW")            # plentiful but the model was off
        self.assertEqual(ei.calibration_level(100, 0.30, 0.31, 0.04), "LOW")              # too few

    def test_decision_confidence_never_exceeds_the_weakest_part(self):
        self.assertEqual(ei.decision_confidence("LOW", "HIGH", 0.9), "HIGH")
        self.assertEqual(ei.decision_confidence("MODERATE", "HIGH", 0.9), "MEDIUM")
        self.assertEqual(ei.decision_confidence("HIGH", "HIGH", 0.9), "LOW")
        self.assertEqual(ei.decision_confidence("LOW", "LOW", 0.9), "LOW")
        self.assertEqual(ei.decision_confidence("LOW", "HIGH", 0.5), "LOW")

    def test_the_edge_threshold_is_the_main_engines_own(self):
        from config import MIN_CONSERVATIVE_EDGE
        self.assertAlmostEqual(ei.EDGE_MIN, MIN_CONSERVATIVE_EDGE / 100.0 if MIN_CONSERVATIVE_EDGE > 1 else MIN_CONSERVATIVE_EDGE)

    def test_american_and_implied_probability_round_trip(self):
        for a in (-250.0, -130.0, 114.0, 240.0, 800.0, 2500.0):
            self.assertAlmostEqual(ei.american_from_prob(ei.implied_from_american(a)), a, delta=1.0)
        self.assertAlmostEqual(ei.implied_from_american(-130.0), 130 / 230, places=6)
        self.assertAlmostEqual(ei.implied_from_american(2500.0), 1 / 26, places=6)

    def test_stale_state_makes_the_layer_unusable_and_says_how_old(self):
        ok, why = ei.usable(make_ctx())
        self.assertTrue(ok, why)                                                              # synthetic team data ends 2026-10-19, reviewed as of 2026-10-20: one day old
        late = make_ctx()
        late.as_of = "2026-10-22"                                                             # three days after the newest game is still current ...
        self.assertTrue(ei.usable(late)[0])
        late.as_of = "2026-10-23"                                                             # ... four is not
        ok, why = ei.usable(late)
        self.assertFalse(ok)
        self.assertIn("team data is 4 days old", why)
        stale_skaters = make_ctx()
        stale_skaters.skater_state = {"players": {}, "newest_game_date": "2026-10-10"}
        ok, why = ei.usable(stale_skaters)
        self.assertFalse(ok)
        self.assertIn("skater data is 10 days old", why)
        self.assertEqual(ei.STALE_DATA_DAYS, 3)

    def test_an_old_evidence_file_makes_the_layer_unusable_until_it_is_rebuilt(self):
        import copy
        ev = copy.deepcopy(matchup.load_evidence())
        ctx = make_ctx(evidence=ev)
        ev["built_at_utc"] = "2026-10-01T00:00:00Z"
        ctx.now = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)                   # 19 days later: inside the 21-day life
        self.assertTrue(ei.usable(ctx)[0])
        ctx.now = dt.datetime(2026, 10, 25, 21, 0, tzinfo=dt.timezone.utc)                   # 24 days
        ok, why = ei.usable(ctx)
        self.assertFalse(ok)
        self.assertIn("evidence file is 24 days old", why)
        ev["built_at_utc"] = "garbage"
        self.assertIn("unreadable", ei.usable(ctx)[1])

    def test_there_is_no_weighted_intelligence_score(self):
        src = inspect.getsource(ei)
        for word in ("intelligence_score", "INTELLIGENCE_SCORE", "WEIGHTS ="):
            self.assertNotIn(word, src)
        self.assertEqual(len(inspect.signature(ei.rank_key).parameters), 5)


class TestSkaterAnalogues(unittest.TestCase):
    """Calibration from history, in both directions, from one cell (never a product of overlapping cells)."""

    def evidence(self, cells):
        return {"skater": {"p_bands": {"goals>=1": [0, 0.1, 0.2, 0.3, 1.01]}, "toi_bands": [0, 12, 16, 20, 99], "pp_bands": [0, 0.25, 1.0, 99], "env_bands": [0, 0.35, 0.55, 1.01], "cells": {"goals>=1": cells}}}

    def cells(self):
        return [["p", 0.1, 0.2, 5000, 0.15, 0.15], ["grp", "F", 0.1, 0.2, 4000, 0.15, 0.14], ["toi", "F", 12, 16, 0.1, 0.2, 2000, 0.15, 0.11], ["pp", "F", 0.0, 0.25, 0.1, 0.2, 100, 0.15, 0.05],
                ["env", 0.0, 0.35, 0.1, 0.2, 800, 0.15, 0.13]]

    def test_the_cells_a_player_falls_in_are_found_with_their_samples_and_z(self):
        ctx = make_ctx(evidence=self.evidence(self.cells()))
        got = {c["dimension"]: c for c in ei.analogue_cells(ctx, "goals>=1", 0.16, "F", 14.0, 0.1, 0.30)}
        self.assertEqual(set(got), {"p", "grp", "toi", "pp", "env"})
        self.assertEqual((got["toi"]["n"], got["toi"]["pred"], got["toi"]["obs"]), (2000, 0.15, 0.11))
        self.assertLess(got["toi"]["z"], -5)                       # over-predicted: (0.11 - 0.15) / se
        self.assertAlmostEqual(got["p"]["z"], 0.0, places=1)

    def test_the_adjustment_is_the_single_strongest_cell_with_enough_cases(self):
        ctx = make_ctx(evidence=self.evidence(self.cells()))
        cells = ei.analogue_cells(ctx, "goals>=1", 0.16, "F", 14.0, 0.1, 0.30)
        adj = ei.best_adjustment(cells)
        self.assertEqual(adj["dimension"], "toi")                  # the pp cell is more extreme in rate but has only 100 cases, below the 300 minimum
        self.assertAlmostEqual(adj["factor"], 0.11 / 0.15, places=3)

    def test_the_adjustment_works_in_both_directions(self):
        up = [["p", 0.1, 0.2, 5000, 0.15, 0.19], ["toi", "F", 12, 16, 0.1, 0.2, 2000, 0.15, 0.19]]
        adj = ei.best_adjustment(ei.analogue_cells(make_ctx(evidence=self.evidence(up)), "goals>=1", 0.16, "F", 14.0, 0.1, None))
        self.assertGreater(adj["factor"], 1.0)                      # the model ran LOW for players like this: a price above our estimate may be the model's fault
        self.assertIsNone(ei.best_adjustment(ei.analogue_cells(make_ctx(evidence=self.evidence([["p", 0.1, 0.2, 5000, 0.15, 0.152]])), "goals>=1", 0.16, "F", 14.0, 0.1, None)))

    def test_too_few_cases_or_a_small_gap_adjusts_nothing(self):
        self.assertIsNone(ei.best_adjustment([{"dimension": "toi", "label": "", "n": 299, "pred": 0.15, "obs": 0.05, "se": 0.01, "z": -10.0}]))
        self.assertIsNone(ei.best_adjustment([{"dimension": "toi", "label": "", "n": 5000, "pred": 0.15, "obs": 0.14, "se": 0.01, "z": -1.9}]))

    def test_a_cell_with_no_events_does_not_get_a_near_zero_error_and_a_huge_z(self):
        ev = self.evidence([["p", 0.1, 0.2, 5000, 0.15, 0.15], ["toi", "F", 12, 16, 0.1, 0.2, 377, 0.015, 0.0]])
        cells = ei.analogue_cells(make_ctx(evidence=ev), "goals>=1", 0.015 + 0.1, "F", 14.0, 0.1, None)
        toi = [c for c in cells if c["dimension"] == "toi"][0]
        self.assertLess(abs(toi["z"]), 5)                                                   # the error is the one under what the model said, not under the zero that happened
        adj = ei.best_adjustment([{**toi, "n": 377, "pred": 0.015, "obs": 0.0, "z": -2.4}])
        self.assertEqual((adj["factor"], adj["raw_factor"]), (ei.FACTOR_FLOOR, 0.0))        # never scaled all the way to zero

    def test_a_probability_below_the_first_band_is_in_the_first_band(self):
        self.assertEqual(ei._band_of([0.1, 0.2, 0.3], 0.05), (0.1, 0.2))
        self.assertEqual(ei._band_of([0.1, 0.2, 0.3], 0.25), (0.2, 0.3))
        self.assertEqual(ei._band_of([0.1, 0.2, 0.3], 0.9), (0.2, 0.3))

    def test_an_unknown_market_has_no_cells(self):
        self.assertEqual(ei.analogue_cells(make_ctx(evidence={"skater": {"p_bands": {}}}), "goals>=1", 0.2, "F", 14.0, 0.1, None), [])


class TestMarketBlock(unittest.TestCase):
    def test_the_edge_is_labelled_for_what_it_is_and_the_breakeven_is_given(self):
        leg = base_leg(market_family="PLAYER_GOALS", american_price=700.0, threshold=1, participant_id="9", participant_name="X", market_probability=None)
        b = ei.market_block(leg, 0.15, 0.17)
        self.assertEqual(b["edge_label"], "MODEL EDGE AT LAST OBSERVED PRICE")
        self.assertAlmostEqual(b["implied_with_margin"], 0.125, places=4)
        self.assertAlmostEqual(b["edge_vs_price"], 0.025, places=4)
        self.assertAlmostEqual(b["model_edge_vs_price"], 0.045, places=4)
        self.assertIn("INCLUDES the bookmaker's margin", b["margin_note"])                     # a one-sided market has no two-sided margin to remove
        self.assertGreater(b["breakeven_price_for_3pt_edge"], 700)                             # +700 is only a 2.5-point edge on a 15% estimate: a 3-point edge needs a longer price
        self.assertEqual(b["breakeven_price_for_3pt_edge"], ei.american_from_prob(0.12))

    def test_a_moneyline_shows_the_no_vig_probability(self):
        b = ei.market_block(base_leg(american_price=-120.0, market_probability=0.52), 0.52, 0.55)
        self.assertEqual(b["no_vig"], 0.52)
        self.assertIn("margin is removed", b["margin_note"])


# ------------------------------------------------------------------------------------------------ the action rules
class TestActionRules(unittest.TestCase):
    def F(self, *lv):
        return [{"name": f"f{i}", "level": l, "text": ""} for i, l in enumerate(lv)]

    def test_a_prop_with_a_real_edge_and_a_surviving_thesis_is_recorded(self):
        a = ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), "HIGH", 0.9, 0.62, 0.08, False, 150)
        self.assertEqual(a["decision"], "RECORD")
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("MODERATE"), "MEDIUM", 0.9, 0.62, 0.08, False, 150)["decision"], "RECORD")

    def test_an_edge_that_does_not_survive_scrutiny_is_lower_priority_never_record(self):
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("HIGH"), "HIGH", 0.9, 0.62, 0.08, False, 150)["decision"], "LOWER_PRIORITY")
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), "LOW", 0.9, 0.62, 0.08, False, 150)["decision"], "LOWER_PRIORITY")

    def test_a_longshot_needs_high_calibration_and_a_long_record(self):
        for cal, games, want in (("HIGH", 150, "RECORD"), ("MEDIUM", 150, "LOWER_PRIORITY"), ("HIGH", 60, "LOWER_PRIORITY")):
            self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), cal, 0.9, 0.06, 0.04, True, games)["decision"], want, (cal, games))

    def test_severe_contradiction_missing_evidence_or_thin_history_is_a_pass(self):
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("HIGH", "HIGH"), "HIGH", 0.9, 0.62, 0.08, False, 150)["decision"], "PASS")
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), "INSUFFICIENT", 0.9, 0.62, 0.08, False, 150)["decision"], "PASS")
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), "HIGH", 0.4, 0.62, 0.08, False, 150)["decision"], "PASS")
        self.assertEqual(ei._prop_action("MODEL_SEES_VALUE", self.F("LOW"), "HIGH", 0.9, 0.62, 0.08, False, 12)["decision"], "PASS")

    def test_a_fair_priced_prop_is_a_control_only_when_it_is_clean(self):
        self.assertEqual(ei._prop_action("FAIR_PRICED", self.F("LOW"), "HIGH", 0.9, 0.55, 0.0, False, 150)["decision"], "LOWER_PRIORITY")
        self.assertEqual(ei._prop_action("FAIR_PRICED", self.F("HIGH"), "HIGH", 0.9, 0.15, 0.0, False, 150)["decision"], "PASS")          # fair price AND a contradiction: nothing to learn

    def test_the_market_sees_more_experiment_has_limits(self):
        ok = ei._prop_action("PRICE_ABOVE_ESTIMATE", self.F("LOW"), "HIGH", 0.9, 0.40, -0.05, False, 150)
        self.assertEqual(ok["decision"], "LOWER_PRIORITY")
        self.assertEqual(ei._prop_action("PRICE_ABOVE_ESTIMATE", self.F("LOW"), "HIGH", 0.9, 0.20, -0.05, False, 150)["decision"], "PASS")          # under 25%: too rare to teach anything
        self.assertEqual(ei._prop_action("PRICE_ABOVE_ESTIMATE", self.F("LOW"), "HIGH", 0.9, 0.40, -0.12, False, 150)["decision"], "PASS")          # priced more than 10 points above the estimate: the margin, not information
        self.assertEqual(ei._prop_action("PRICE_ABOVE_ESTIMATE", self.F("LOW"), "LOW", 0.9, 0.40, -0.05, False, 150)["decision"], "PASS")

    def test_the_price_flags_themselves_do_not_veto_the_market_sees_more_experiment(self):
        flags = [{"name": "PRICE_ABOVE_ESTIMATE", "level": "HIGH", "text": ""}]
        self.assertEqual(ei._prop_action("PRICE_ABOVE_ESTIMATE", flags, "HIGH", 0.9, 0.40, -0.06, False, 150)["decision"], "LOWER_PRIORITY")

    def test_moneyline_actions(self):
        A = ei._moneyline_action
        self.assertEqual(A("MODEL_SEES_VALUE", "MODERATE", "HIGH", 0.7, True, 0.04)["decision"], "RECORD")
        self.assertEqual(A("MODEL_SEES_VALUE", "HIGH", "HIGH", 0.7, False, 0.07)["decision"], "LOWER_PRIORITY")
        self.assertEqual(A("MODEL_SEES_VALUE", "SEVERE", "HIGH", 0.7, False, 0.07)["decision"], "PASS")
        self.assertEqual(A("UNDERDOG_AT_FAIR_PRICE", "HIGH", "HIGH", 0.7, False, 0.0)["decision"], "PASS")
        self.assertEqual(A("MODEL_SAYS_OVERPRICED", "SEVERE", "HIGH", 0.7, False, -0.1)["decision"], "PASS")
        self.assertEqual(A("LIKELY_WINNER_AT_FAIR_PRICE", "LOW", "HIGH", 0.7, True, 0.0)["decision"], "LOWER_PRIORITY")
        self.assertEqual(A("MODEL_SEES_VALUE", "LOW", "INSUFFICIENT", 0.7, True, 0.04)["decision"], "PASS")
        self.assertEqual(A("MODEL_SEES_VALUE", "LOW", "HIGH", 0.4, True, 0.04)["decision"], "PASS")


class TestPriceLengthLadder(unittest.TestCase):
    """Owner, 2026-10-21: extra skepticism for a +150 or longer moneyline, a +300 prop, and +600 and longer."""

    def test_the_rungs(self):
        f = ei._price_length_flag
        for price, level in ((149.0, None), (150.0, "MODERATE"), (299.0, "MODERATE"), (300.0, "HIGH"), (900.0, "HIGH")):
            got = f(price, ei.LONG_PRICE_ML, ei.VERY_LONG_PRICE_ML, "moneyline")
            self.assertEqual(got["level"] if got else None, level, price)
        for price, level in ((299.0, None), (300.0, "MODERATE"), (599.0, "MODERATE"), (600.0, "HIGH"), (2500.0, "HIGH")):
            got = f(price, ei.LONG_PRICE_PROP, ei.VERY_LONG_PRICE_PROP, "prop")
            self.assertEqual(got["level"] if got else None, level, price)
        self.assertEqual(f(-130.0, 300.0, 600.0, "prop"), None)
        self.assertEqual((ei.LONG_PRICE_ML, ei.VERY_LONG_PRICE_ML, ei.LONG_PRICE_PROP, ei.VERY_LONG_PRICE_PROP), (150.0, 300.0, 300.0, 600.0))

    def test_a_long_moneyline_underdog_carries_the_flag(self):
        ctx = make_ctx()
        leg = ml_leg(ctx, "BBB", -0.01)                                              # the weaker team: a long price in this synthetic league
        self.assertGreaterEqual(leg.american_price, 300.0)
        c = ei.review_leg(ctx, leg, "MARKET_NO_VIG", leg.conservative_probability)
        flags = {f["name"]: f["level"] for f in c["contradiction"]["flags"]}
        self.assertEqual(flags["LONG_PRICE"], "HIGH")

    def test_a_valued_longshot_prop_cannot_be_a_record(self):
        ctx = make_ctx()
        ctx.skater_state = {"players": {"9": skater()}, "newest_game_date": "2026-10-19"}
        ctx.by_player = {"9": history_rows()}
        leg = prop_leg(fam="PLAYER_GOALS", k=1, price=650.0, p=0.20)
        with mock.patch("research.product_models.live.project_matchup", return_value=projection(0.20, market="goals>=1")):
            c = ei.review_leg(ctx, leg, "MODEL_PROBABILITY_UNANCHORED", 0.20)
        flags = {f["name"]: f["level"] for f in c["contradiction"]["flags"]}
        self.assertEqual(flags["LONG_PRICE"], "HIGH")
        self.assertEqual(c["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY")                  # an edge exists, the price is very long: weaker than a record

    def test_a_short_priced_prop_is_untouched_by_the_ladder(self):
        ctx = make_ctx()
        ctx.skater_state = {"players": {"9": skater()}, "newest_game_date": "2026-10-19"}
        ctx.by_player = {"9": history_rows()}
        with mock.patch("research.product_models.live.project_matchup", return_value=projection(0.72)):
            c = ei.review_leg(ctx, prop_leg(price=-110.0, p=0.72), "ANCHORED_RECORD_ESTIMATE", 0.72)
        self.assertNotIn("LONG_PRICE", [f["name"] for f in c["contradiction"]["flags"]])


class TestEdgeClassAndRanking(unittest.TestCase):
    def card(self, kind="PLAYER", est=0.6, price=-130.0, cal="HIGH", model=None, action="RECORD", contradiction="LOW"):
        a = price
        implied = 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)
        mp = est if model is None else model
        return {"kind": kind, "market": {"edge_vs_price": est - implied, "model_edge_vs_price": mp - implied}, "calibration_confidence": {"level": cal}, "action": {"decision": action},
                "contradiction": {"level": contradiction}, "estimate_used": est}

    def test_classes_use_the_three_point_band_and_need_calibration_for_positive(self):
        self.assertEqual(ei.edge_class_of(self.card(est=0.65))[0], "POSITIVE_EDGE")
        self.assertEqual(ei.edge_class_of(self.card(est=0.65, cal="LOW"))[0], "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(ei.edge_class_of(self.card(est=0.55))[0], "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(ei.edge_class_of(self.card(est=0.5))[0], "LEARNING_SAMPLE_NEGATIVE_EDGE")

    def test_a_moneyline_is_judged_on_the_hockey_model_not_the_market_anchored_estimate(self):
        c = self.card(kind="MONEYLINE", est=0.282, price=240.0, model=0.174)
        self.assertEqual(ei.edge_class_of(c)[0], "LEARNING_SAMPLE_NEGATIVE_EDGE")
        self.assertAlmostEqual(ei.edge_for_class(c)[0], 0.174 - 100 / 340, places=4)

    def test_the_order_is_action_then_class_then_contradiction_then_calibration_then_hit_chance_then_edge(self):
        k = lambda **kw: ei.rank_key(self.card(**kw), ei.edge_class_of(self.card(**kw))[0], kw.get("est", 0.6), ei.edge_for_class(self.card(**kw))[0], "k")
        # a RECORD beats a LOWER_PRIORITY however large the edge
        self.assertLess(k(est=0.62, action="RECORD"), k(est=0.95, action="LOWER_PRIORITY"))
        # within an action, a better edge class beats a more likely outcome
        self.assertLess(k(est=0.66, price=-130.0), k(est=0.60, price=-250.0))
        # within a class, lower contradiction first, then better calibration, then the likelier outcome
        self.assertLess(k(est=0.66, contradiction="LOW"), k(est=0.80, contradiction="MODERATE"))
        self.assertLess(k(est=0.66, cal="HIGH"), k(est=0.80, cal="MEDIUM"))
        self.assertLess(k(est=0.80, price=-130.0), k(est=0.70, price=-130.0))

    def test_ties_are_broken_by_the_key_so_the_order_is_deterministic(self):
        c = self.card()
        a = ei.rank_key(c, "POSITIVE_EDGE", 0.6, 0.05, "a")
        b = ei.rank_key(c, "POSITIVE_EDGE", 0.6, 0.05, "b")
        self.assertLess(a, b)

    def test_compact_rounds_floats_and_keeps_everything(self):
        out = ei.compact({"a": 0.123456789, "b": [0.55557, {"c": 1.0000001}], "d": "x", "e": None})
        self.assertEqual(out, {"a": 0.1235, "b": [0.5556, {"c": 1.0}], "d": "x", "e": None})


ANSWER_KEYS = ("why_this_team_or_player", "why_tonight", "why_this_market", "why_this_price", "who_is_most_likely_to_win", "what_the_model_believes", "what_the_market_believes", "why_they_disagree",
               "what_supports_it", "what_argues_against_it", "what_history_says", "what_data_is_missing", "what_would_make_eggy_pass")


# ------------------------------------------------------------------------------------------------ the moneyline review
class TestMoneylineReview(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx()
        self.view = ei.team_view(self.ctx, "AAA", "BBB")

    def review(self, team, shift=0.0, elo=0.5):
        leg = ml_leg(self.ctx, team, shift, elo)
        return ei.review_leg(self.ctx, leg, "MARKET_NO_VIG", leg.conservative_probability)

    def test_the_stronger_team_is_the_hockey_favourite_and_the_gap_is_described(self):
        v = self.view
        self.assertTrue(v["available"])
        self.assertEqual(v["favourite"], "AAA")
        self.assertGreater(v["p_favourite"], 0.6)
        self.assertIn(v["gap"], ("CLEAR", "STRONG", "DOMINANT"))
        self.assertEqual(set(v["indicators"]), {"AAA", "BBB"})
        self.assertEqual(v["indicators"]["AAA"]["ranks"]["gd"], 1)
        self.assertEqual(v["indicators"]["BBB"]["ranks"]["gd"], 6)

    def test_every_section_the_owner_asked_for_is_on_the_card(self):
        c = self.review("AAA", shift=-0.05)
        for k in ("bet", "most_likely_outcome", "why_tonight", "hockey_view", "probabilities", "market", "why_they_disagree", "case_for", "case_against", "historical", "model_state", "pass_conditions",
                  "thesis", "contradiction", "calibration_confidence", "data_completeness", "decision_confidence", "action", "claims"):
            self.assertIn(k, c, k)
        self.assertEqual(c["bet"]["market"], "MONEYLINE")
        self.assertEqual(c["bet"]["market_key"], "h2h")
        self.assertTrue(c["bet"]["label"].startswith("AAA — MONEYLINE — "))
        self.assertIn("confirmed starting goalies", c["data_completeness"]["missing"])
        self.assertIn("injuries and lineup", c["data_completeness"]["missing"])
        self.assertTrue(c["pass_conditions"])
        self.assertTrue(c["case_for"] and c["case_against"] is not None)

    def test_the_card_answers_the_thirteen_questions_at_a_glance_in_plain_words(self):
        c = self.review("BBB", shift=-0.04)
        a = c["answers"]
        self.assertEqual(set(a), set(ANSWER_KEYS))
        for k in ("why_this_team_or_player", "why_tonight", "why_this_market", "why_this_price", "who_is_most_likely_to_win", "what_the_model_believes", "what_the_market_believes", "why_they_disagree"):
            self.assertTrue(isinstance(a[k], str) and a[k], k)
        for k in ("what_supports_it", "what_argues_against_it", "what_history_says", "what_data_is_missing", "what_would_make_eggy_pass"):
            self.assertTrue(isinstance(a[k], list) and a[k], k)
        self.assertIn("two-sided market", a["why_this_market"])
        self.assertIn("margin removed", a["why_this_price"])
        self.assertIn("AAA wins", a["who_is_most_likely_to_win"])                                           # the likelier winner is named even when the bet is on the other side
        self.assertIn("hockey model", a["what_the_model_believes"])
        self.assertIn("no-vig", a["what_the_market_believes"])
        self.assertIn("injuries and lineup", a["what_data_is_missing"])

    def test_a_fair_priced_underdog_is_a_pass_with_the_hockey_picture_against_it(self):
        c = self.review("BBB", shift=0.0)
        self.assertEqual(c["thesis"]["type"], "UNDERDOG_AT_FAIR_PRICE")
        self.assertEqual(c["action"]["decision"], "PASS")
        self.assertIn("HOCKEY_PICTURE_AGAINST", [f["name"] for f in c["contradiction"]["flags"]])
        self.assertIn(c["contradiction"]["level"], ("HIGH", "SEVERE"))
        self.assertIn("AAA wins", c["most_likely_outcome"])

    def test_the_likely_winner_at_a_fair_price_is_only_a_control(self):
        c = self.review("AAA", shift=0.0)
        self.assertEqual(c["thesis"]["type"], "LIKELY_WINNER_AT_FAIR_PRICE")
        self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY")
        self.assertEqual(c["contradiction"]["level"], "LOW")

    def test_a_model_edge_is_only_lower_priority_until_the_model_has_beaten_the_market(self):
        c = self.review("AAA", shift=-0.045)
        self.assertEqual(c["thesis"]["type"], "MODEL_SEES_VALUE")
        names = {f["name"]: f["level"] for f in c["contradiction"]["flags"]}
        self.assertEqual((names["MARKET_MAY_KNOW_MORE"], names["MODEL_NOT_SHOWN_TO_BEAT_MARKET"]), ("MODERATE", "MODERATE"))          # a gap worth explaining, and a record that has not shown the model beating the market
        self.assertEqual((c["contradiction"]["level"], c["action"]["decision"]), ("HIGH", "LOWER_PRIORITY"))                 # two moderate doubts compound
        prospective = [h for h in c["historical"] if h["kind"] == "MODEL_VS_MARKET_PROSPECTIVE"]
        self.assertTrue(prospective)                                                           # the card quotes the record of this model against the market ...
        self.assertIn("UNVALIDATED", prospective[0]["text"])                                   # ... and says the model's disagreement has not been shown to mean anything
        self.assertIn("EXPERIMENT", c["thesis"]["statement"])
        self.assertNotEqual(c["decision_confidence"], "HIGH")                                  # an unvalidated edge never earns HIGH confidence

    def _with_paired(self, z, h=0.60, m=0.64):
        import copy
        ev = copy.deepcopy(matchup.load_evidence())
        ev["matchup"]["prospective_vs_market"]["log_loss"].update({"H": h, "M": m})
        ev["matchup"]["prospective_vs_market"]["paired_difference"] = {"market_minus_model_log_loss": m - h, "standard_error": 0.01, "z": z, "games": 87}
        ctx = make_ctx(evidence=ev)
        leg = ml_leg(ctx, "AAA", -0.045)
        return ei.review_leg(ctx, leg, "MARKET_NO_VIG", leg.conservative_probability)

    def test_the_same_edge_is_recorded_once_the_model_has_led_the_market_by_two_standard_errors(self):
        c = self._with_paired(z=3.0)
        self.assertNotIn("MODEL_NOT_SHOWN_TO_BEAT_MARKET", [f["name"] for f in c["contradiction"]["flags"]])
        self.assertEqual((c["contradiction"]["level"], c["action"]["decision"]), ("MODERATE", "RECORD"))
        self.assertIn("ahead of the market", " ".join(h["text"] for h in c["historical"]))

    def test_a_lead_within_noise_is_not_a_lead_and_the_rule_does_not_flip_on_a_few_thousandths(self):
        for z in (-0.2, 0.4, 1.99):                                                          # the market ahead, level, or the model ahead by less than two standard errors
            c = self._with_paired(z=z, h=0.6400 if z < 0 else 0.6360, m=0.6390 if z < 0 else 0.6400)
            self.assertIn("MODEL_NOT_SHOWN_TO_BEAT_MARKET", [f["name"] for f in c["contradiction"]["flags"]], z)
            self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY", z)
        within = [f for f in self._with_paired(z=1.0, h=0.636, m=0.640)["contradiction"]["flags"] if f["name"] == "MODEL_NOT_SHOWN_TO_BEAT_MARKET"][0]
        self.assertIn("within noise", within["text"])
        worse = [f for f in self._with_paired(z=-1.0, h=0.644, m=0.640)["contradiction"]["flags"] if f["name"] == "MODEL_NOT_SHOWN_TO_BEAT_MARKET"][0]
        self.assertIn("the better guide", worse["text"])

    def test_an_evidence_file_without_the_paired_statistic_never_lets_the_model_lead(self):
        import copy
        ev = copy.deepcopy(matchup.load_evidence())
        del ev["matchup"]["prospective_vs_market"]["paired_difference"]
        ctx = make_ctx(evidence=ev)
        leg = ml_leg(ctx, "AAA", -0.045)
        c = ei.review_leg(ctx, leg, "MARKET_NO_VIG", leg.conservative_probability)
        self.assertIn("MODEL_NOT_SHOWN_TO_BEAT_MARKET", [f["name"] for f in c["contradiction"]["flags"]])

    def _toss_card(self, team, market_p, p_home=0.505):
        ctx = make_ctx()
        v = ei.team_view(ctx, "AAA", "BBB")
        opp = "BBB" if team == "AAA" else "AAA"
        leg = base_leg(participant_id=team, participant_name=team, team=team, opponent=opp, side="HOME" if team == "AAA" else "AWAY", market_probability=market_p, conservative_probability=market_p,
                       american_price=float(prob_to_american(market_p * 1.04)))
        fake = {**v, "p_home": p_home, "p_away": round(1 - p_home, 4), "favourite": "AAA" if p_home >= 0.5 else "BBB", "p_favourite": max(p_home, 1 - p_home)}
        with mock.patch.object(ei, "team_view", return_value=fake):
            return ei.review_leg(ctx, leg, "MARKET_NO_VIG", market_p)

    def test_a_toss_up_is_a_toss_up_not_a_weaker_team(self):
        for team, m in (("AAA", 0.505), ("AAA", 0.52), ("BBB", 0.495), ("BBB", 0.48)):
            c = self._toss_card(team, m)
            self.assertEqual(c["thesis"]["type"], "COIN_FLIP_AT_FAIR_PRICE", (team, m))
            self.assertNotIn("weaker team", c["thesis"]["statement"])
            self.assertNotIn("HOCKEY_PICTURE_AGAINST", [f["name"] for f in c["contradiction"]["flags"]])
            self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY")
            self.assertTrue(c["most_likely_outcome"].startswith("a toss-up"))

    def test_a_marginal_hockey_favourite_the_market_slightly_doubts_is_not_called_the_weaker_team(self):
        c = self._toss_card("AAA", 0.485)                                                    # model 50.5%, market 48.5%: two points, a toss-up either way
        self.assertNotEqual(c["thesis"]["type"], "UNDERDOG_AT_FAIR_PRICE")
        self.assertNotIn("the weaker team", c["thesis"]["statement"])

    def test_a_clear_hockey_favourite_is_a_likely_winner_and_a_clear_underdog_is_an_underdog(self):
        fav = self._toss_card("AAA", 0.58, p_home=0.60)
        self.assertEqual(fav["thesis"]["type"], "LIKELY_WINNER_AT_FAIR_PRICE")
        dog = self._toss_card("BBB", 0.42, p_home=0.60)
        self.assertEqual((dog["thesis"]["type"], dog["action"]["decision"]), ("UNDERDOG_AT_FAIR_PRICE", "PASS"))
        self.assertIn("HOCKEY_PICTURE_AGAINST", [f["name"] for f in dog["contradiction"]["flags"]])

    def test_a_huge_model_market_gap_is_treated_with_more_suspicion_not_less(self):
        c = self.review("AAA", shift=-0.14)
        self.assertEqual(c["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertIn("MARKET_MAY_KNOW_MORE", [f["name"] for f in c["contradiction"]["flags"]])
        self.assertIn(c["contradiction"]["level"], ("HIGH", "SEVERE"))
        self.assertEqual(c["action"]["decision"], "LOWER_PRIORITY")

    def test_an_underdog_the_model_likes_is_recorded_only_with_the_hockey_picture_declared_against_it(self):
        c = self.review("BBB", shift=0.04)             # the market prices BBB 4 points ABOVE the model: the model calls it overpriced
        self.assertEqual(c["thesis"]["type"], "MODEL_SAYS_OVERPRICED")
        self.assertEqual(c["action"]["decision"], "PASS")
        c2 = self.review("BBB", shift=-0.04)           # the market prices BBB 4 points BELOW the model: model sees value, but AAA is still the better team
        self.assertEqual(c2["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertIn("HOCKEY_PICTURE_AGAINST", [f["name"] for f in c2["contradiction"]["flags"]])
        self.assertIn("still rates AAA the better team", c2["thesis"]["statement"])
        self.assertNotEqual(c2["action"]["decision"], "PASS")                       # not banned: an underdog the model genuinely rates as underpriced can be recorded or held lower

    def test_elo_plays_no_part_in_the_decision(self):
        a = self.review("BBB", shift=0.0, elo=0.20)
        b = self.review("BBB", shift=0.0, elo=0.80)
        self.assertEqual((a["thesis"]["type"], a["action"]["decision"], a["contradiction"]["level"]), (b["thesis"]["type"], b["action"]["decision"], b["contradiction"]["level"]))
        self.assertEqual(a["probabilities"]["model"], b["probabilities"]["model"])
        self.assertIn("plays no part", " ".join(self.review("BBB", 0.0, 0.8)["case_against"]))          # a large Elo gap is shown against the bet, and explained

    def test_no_hockey_view_means_pass(self):
        ctx = make_ctx(team_state=None)
        leg = ml_leg(self.ctx, "AAA")
        c = ei.review_leg(ctx, leg, "MARKET_NO_VIG", 0.6)
        self.assertEqual(c["action"]["decision"], "PASS")
        self.assertEqual(c["contradiction"]["level"], "SEVERE")
        self.assertEqual(c["calibration_confidence"]["level"], "INSUFFICIENT")

    def test_a_review_that_fails_is_a_pass_never_an_exception(self):
        with mock.patch.object(ei, "review_moneyline", side_effect=RuntimeError("boom")):
            c = ei.review_leg(self.ctx, ml_leg(self.ctx, "AAA"), "MARKET_NO_VIG", 0.6)
        self.assertEqual((c["kind"], c["action"]["decision"], c["thesis"]["type"]), ("ERROR", "PASS", "NONE"))

    def test_the_breakeven_price_for_a_three_point_model_edge_is_stated(self):
        c = self.review("AAA", shift=-0.045)
        self.assertEqual(c["market"]["edge_label"], "MODEL EDGE AT LAST OBSERVED PRICE")
        self.assertIsNotNone(c["market"]["breakeven_price_for_3pt_edge"])


# ------------------------------------------------------------------------------------------------ the player review
def skater(pid="9", team="AAA", pos="C", toi=18.0, pp=2.4, tier=1, ppu=1, games=200):
    recent = [{"date": f"2026-10-{19 - i:02d}", "season": "2026-27", "opp": "CCC", "home": True, "toi": toi, "toi_pp": pp, "shots": 3, "goals": 0, "assists": 1, "hits": 0, "blocks": 0} for i in range(5)]
    return {"player_id": pid, "name": "Test Player", "team": team, "position": pos, "position_group": "D" if pos == "D" else "F", "last_game_date": "2026-10-19", "games_total": games,
            "season_totals": {"games": 8, "toi_avg": toi, "toi_pp_avg": pp, "shots": 24, "goals": 3, "assists": 4, "points": 7, "hits": 0, "blocks": 0}, "last_season_games": 80, "recent_games": recent,
            "recent_toi": toi, "recent_toi_pp": pp, "recent_shots": 3.0, "recent_goals": 0.3, "recent_assists": 0.4, "role_games": 5, "usage_tier": tier, "pp_usage": ppu}


def history_rows(pid="9", n=60, shots=3, goals=0, rate=3):
    return [{"player_id": pid, "date": f"2026-{9 + i // 28:02d}-{i % 28 + 1:02d}", "season": 2026, "shots": (shots if i % rate else shots + 2), "goals": (1 if i % 5 == 0 else goals), "points": (1 if i % 3 == 0 else 0)} for i in range(n)]


def projection(p_cal, market="shots>=2", toi=18.5, pp=2.4, games=200):
    return {"expected": {"shots": 3.2, "goals": 0.3, "assists": 0.4, "points": 0.7, "toi": toi, "toi_pp": pp}, "probabilities": {market: {"raw": p_cal - 0.02, "calibrated": p_cal}}, "games_observed": games,
            "pricing_eligible": True, "limited_history": False}


def prop_leg(fam="PLAYER_SOG_ALTERNATE", k=2, price=-130.0, p=0.7, **kw):
    return base_leg(market_family=fam, participant_id="9", participant_name="Test Player", side="OVER", threshold=k, american_price=price, conservative_probability=p, model_probability=p, market_probability=None,
                    empirical_probability=None, empirical_hits=None, empirical_games=40, **kw)


class TestPlayerReview(unittest.TestCase):
    def setUp(self):
        self.pid = "9"

    def ctx(self, **player_kw):
        c = make_ctx()
        c.skater_state = {"players": {self.pid: skater(**player_kw)}, "newest_game_date": "2026-10-19"}
        c.by_player = {self.pid: history_rows()}
        return c

    def review(self, leg, p_cal, market="shots>=2", ctx=None, basis="ANCHORED_RECORD_ESTIMATE", **proj):
        ctx = ctx or self.ctx()
        with mock.patch("research.product_models.live.project_matchup", return_value=projection(p_cal, market, **proj)):
            return ei.review_leg(ctx, leg, basis, leg.conservative_probability)

    def test_a_likely_event_the_price_underrates_has_a_value_thesis_and_every_section(self):
        c = self.review(prop_leg(price=-110.0, p=0.72), 0.72)
        self.assertEqual(c["thesis"]["type"], "MODEL_SEES_VALUE")
        for k in ("most_likely_outcome", "why_tonight", "player_view", "hockey_view", "probabilities", "market", "why_they_disagree", "case_for", "case_against", "historical", "pass_conditions", "claims"):
            self.assertIn(k, c, k)
        self.assertEqual(c["bet"]["label"], "Test Player — SHOTS ON GOAL 2+ — -110")
        self.assertEqual(c["bet"]["market_key"], "player_shots_on_goal_alternate")
        self.assertIn("reaches", c["most_likely_outcome"])
        self.assertEqual({x["id"] for x in c["claims"]}, {"expected_toi", "expected_pp", "expected_stat", "team_win_probability"})

    def test_a_fair_priced_longshot_is_not_recorded(self):
        leg = prop_leg(fam="PLAYER_GOALS", k=1, price=700.0, p=0.14)
        c = self.review(leg, 0.14, market="goals>=1")
        self.assertIn(c["action"]["decision"], ("PASS", "LOWER_PRIORITY"))
        self.assertNotEqual(c["action"]["decision"], "RECORD")

    def test_a_2500_plus_price_is_a_longshot_and_needs_high_calibration_and_a_long_record(self):
        leg = prop_leg(fam="PLAYER_GOALS", k=1, price=2500.0, p=0.08)
        c = self.review(leg, 0.08, market="goals>=1")
        self.assertIn("LONGSHOT_TAIL", [f["name"] for f in c["contradiction"]["flags"]])
        self.assertNotEqual(c["action"]["decision"], "RECORD")

    def test_a_price_far_above_the_estimate_is_a_pass(self):
        c = self.review(prop_leg(price=-300.0, p=0.5), 0.5)
        self.assertEqual(c["thesis"]["type"], "PRICE_ABOVE_ESTIMATE")
        self.assertEqual(c["action"]["decision"], "PASS")

    def test_a_player_with_no_state_is_a_pass_with_severe_contradiction(self):
        ctx = make_ctx()
        c = ei.review_leg(ctx, prop_leg(), "ANCHORED_RECORD_ESTIMATE", 0.7)
        self.assertEqual((c["action"]["decision"], c["contradiction"]["level"], c["calibration_confidence"]["level"]), ("PASS", "SEVERE", "INSUFFICIENT"))

    def test_a_thin_record_is_a_pass(self):
        ctx = self.ctx(games=12)
        c = self.review(prop_leg(price=-110.0, p=0.72), 0.72, ctx=ctx, games=12)
        self.assertEqual(c["action"]["decision"], "PASS")
        self.assertIn("THIN_HISTORY", [f["name"] for f in c["contradiction"]["flags"]])

    def test_a_power_play_assumption_the_player_has_not_shown_is_flagged(self):
        ctx = self.ctx(pp=0.2, ppu=None)
        c = self.review(prop_leg(fam="PLAYER_POINTS", k=1, price=150.0, p=0.5), 0.5, market="points>=1", ctx=ctx, pp=2.0)
        self.assertIn("POWER_PLAY_ASSUMPTION", [f["name"] for f in c["contradiction"]["flags"]])

    def test_a_role_the_player_has_not_shown_is_flagged(self):
        ctx = self.ctx(toi=14.0)
        c = self.review(prop_leg(price=-110.0, p=0.72), 0.72, ctx=ctx, toi=18.5)
        self.assertIn("ROLE_ASSUMPTION", [f["name"] for f in c["contradiction"]["flags"]])

    def test_the_prop_card_answers_the_same_questions_and_says_the_price_includes_the_margin(self):
        c = self.review(prop_leg(price=-110.0, p=0.72), 0.72)
        a = c["answers"]
        self.assertEqual(set(a), set(ANSWER_KEYS))
        self.assertIn("ice time and shot volume", a["why_this_market"])
        self.assertIn("INCLUDES the bookmaker's margin", a["why_this_price"])
        self.assertIn("min on ice", a["what_the_model_believes"])
        self.assertIn("margin included", a["what_the_market_believes"])
        goals = self.review(prop_leg(fam="PLAYER_GOALS", k=1, price=700.0, p=0.14), 0.14, market="goals>=1")["answers"]
        self.assertIn("one rare event", goals["why_this_market"])

    def test_a_failed_review_still_answers_every_question(self):
        c = ei.review_leg(make_ctx(), base_leg(market_family="PUCK_LINE", participant_id="9"), "X", 0.5)
        self.assertEqual(set(c["answers"]), set(ANSWER_KEYS))

    EV = {"p_bands": {"shots>=2": [0, 0.5, 0.8, 1.01]}, "toi_bands": [0, 16, 20, 99], "pp_bands": [0, 0.25, 1.0, 99], "env_bands": [0, 0.35, 0.55, 1.01]}

    def evidence_with(self, cells):
        ev = matchup.load_evidence()
        return {**ev, "skater": {**self.EV, "cells": {"shots>=2": cells}}}

    def review_with_cells(self, cells, estimate=0.62, p_model=0.60, price=-110.0):
        ctx = self.ctx()
        ctx.evidence = self.evidence_with(cells)
        leg = prop_leg(price=price, p=estimate)
        with mock.patch("research.product_models.live.project_matchup", return_value=projection(p_model)):
            return ei.review_leg(ctx, leg, "ANCHORED_RECORD_ESTIMATE", estimate)

    def test_history_can_lower_an_estimate_but_never_raise_it_above_the_legs_own(self):
        low = [["p", 0.5, 0.8, 5000, 0.60, 0.70], ["toi", "F", 16, 20, 0.5, 0.8, 3000, 0.60, 0.70]]          # the model ran LOW for players like this
        c = self.review_with_cells(low)
        self.assertEqual(c["estimate_used"], 0.62)                                                      # not 0.70
        self.assertFalse(c["probabilities"]["recalibration"]["applied"])
        self.assertIn("never raise", c["probabilities"]["recalibration"]["applied_as"])
        self.assertIn("HISTORY_SAYS_MODEL_RUNS_LOW", [f["name"] for f in c["contradiction"]["flags"]])
        self.assertAlmostEqual(c["market"]["edge_vs_price"], 0.62 - ei.implied_from_american(-110.0), places=3)
        high = [["p", 0.5, 0.8, 5000, 0.60, 0.50], ["toi", "F", 16, 20, 0.5, 0.8, 3000, 0.60, 0.50]]          # the model ran HIGH
        c = self.review_with_cells(high)
        self.assertAlmostEqual(c["estimate_used"], 0.50, places=3)                                         # 0.60 x (0.50 / 0.60)
        self.assertTrue(c["probabilities"]["recalibration"]["applied"])
        self.assertIn("HISTORY_SAYS_MODEL_RUNS_HIGH", [f["name"] for f in c["contradiction"]["flags"]])
        c = self.review_with_cells(high, estimate=0.45)                                                    # the leg's own estimate is already lower: it stays
        self.assertAlmostEqual(c["estimate_used"], 0.45, places=3)
        self.assertIn("already lower", " ".join(f["text"] for f in c["contradiction"]["flags"]))

    def test_calibration_is_high_only_when_the_cell_is_specific_to_the_player(self):
        pop = [["p", 0.5, 0.8, 5000, 0.60, 0.50]]
        self.assertEqual(self.review_with_cells(pop)["calibration_confidence"]["level"], "MEDIUM")           # a population-level band alone vouches for the model in the band, not for him
        spec = [["p", 0.5, 0.8, 5000, 0.60, 0.50], ["toi", "F", 16, 20, 0.5, 0.8, 3000, 0.60, 0.50]]
        self.assertEqual(self.review_with_cells(spec)["calibration_confidence"]["level"], "HIGH")
        few = [["p", 0.5, 0.8, 5000, 0.60, 0.50], ["toi", "F", 16, 20, 0.5, 0.8, 400, 0.60, 0.50]]
        self.assertEqual(self.review_with_cells(few)["calibration_confidence"]["level"], "MEDIUM")           # a specific cell with fewer than 1000 cases
        calibrated = [["p", 0.5, 0.8, 5000, 0.60, 0.601]]
        self.assertEqual(self.review_with_cells(calibrated)["calibration_confidence"]["level"], "MEDIUM")    # well calibrated, but only at the population level

    def test_the_thesis_wording_follows_the_probability(self):
        likely = self.review_with_cells([], estimate=0.72, p_model=0.72, price=-110.0)["thesis"]["statement"]
        self.assertIn("is likely to reach", likely)
        middling = self.review_with_cells([], estimate=0.40, p_model=0.40, price=+200.0)
        self.assertEqual(middling["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertNotIn("is likely to reach", middling["thesis"]["statement"])
        self.assertIn("less likely than not", middling["thesis"]["statement"])
        self.assertTrue(middling["most_likely_outcome"].startswith("Test Player does NOT reach"))        # the card never says both "likely" and "does not"

    def test_an_engine_error_is_an_error_card_with_its_own_field(self):
        ctx = make_ctx()
        with mock.patch.object(ei, "review_moneyline", side_effect=ValueError("bad shape")):
            c = ei.review_leg(ctx, ml_leg(ctx, "AAA"), "MARKET_NO_VIG", 0.6)
        self.assertEqual((c["kind"], c["error"]), ("ERROR", "ValueError: bad shape"))

    def test_every_card_names_the_evidence_it_stood_on(self):
        c = self.review_with_cells([])
        stamp = c["model_state"]["evidence"]
        self.assertIn(matchup.load_evidence()["_sha256"][:12], stamp)
        self.assertIn("built", stamp)
        mlc = ei.review_leg(make_ctx(), ml_leg(make_ctx(), "AAA"), "MARKET_NO_VIG", 0.6)
        self.assertIn(matchup.load_evidence()["_sha256"][:12], mlc["model_state"]["evidence"])

    def test_the_card_says_what_is_missing(self):
        c = self.review(prop_leg(price=-110.0, p=0.72), 0.72)
        self.assertIn("confirmed lineup", c["data_completeness"]["missing"])
        self.assertIn("injury news", c["data_completeness"]["missing"])

    def test_a_market_the_review_cannot_read_is_a_pass(self):
        c = ei.review_leg(self.ctx(), base_leg(market_family="PUCK_LINE", participant_id="9"), "X", 0.5)
        self.assertEqual((c["kind"], c["action"]["decision"]), ("ERROR", "PASS"))


class TestHonestyOfTheCard(unittest.TestCase):
    def test_the_card_is_json_serialisable_and_has_no_nan(self):
        ctx = make_ctx()
        leg = ml_leg(ctx, "AAA", -0.04)
        card = ei.compact(ei.review_leg(ctx, leg, "MARKET_NO_VIG", leg.conservative_probability))
        text = json.dumps(card)
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text)["thesis"]["type"], card["thesis"]["type"])

    def test_no_new_provider_database_or_scheduler_is_touched(self):
        src = inspect.getsource(ei)
        for word in ("requests.", "urllib", "sqlite3.connect", "launchctl", "schedule.every"):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
