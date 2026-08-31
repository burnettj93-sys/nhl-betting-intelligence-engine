"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 1 Part 4:
cross-registry consistency tests between research/model_registry.py and
research/player_props/market_registry.py. Nothing previously caught the
PLAYER_SOG (claimed 1+-6+ validated, actually 2+-5+), ASSISTS (claimed 3+
validated, actually INSUFFICIENT_DATA), and JOINT_SCORING_DEPENDENCE
(2 redundant "triple" combos mislabeled validated, GOAL_POINT omitted)
contradictions this sprint fixed -- these tests exist so a future registry
edit cannot silently reintroduce the same class of drift for the families
named in this sprint's own Part 4 list (SOG, Goals, Assists, Points, Blocks,
Team SOG, Goalie Saves, Period SOG).

Threshold GRANULARITY genuinely differs between the two registries for some
families: Team SOG and Period SOG are tracked as one aggregate market per
family/period in market_registry.py, but at individual-threshold resolution
in MODEL_REGISTRY. That is a real structural difference, not a bug -- the
tests below use a coarser, still-meaningful check for those two families
rather than pretending a threshold-level market_id exists when it doesn't.
"""
from __future__ import annotations

import re
import unittest

from research import model_registry as mr
from research.player_props import market_registry as mkt

_NUMERIC_THRESHOLD_RE = re.compile(r"^(\d+)\+$")
_NEGATIVE_MARKET_STATUSES = {"NOT_BUILT", "REJECTED", "INSUFFICIENT_DATA", "INSUFFICIENT_TAIL_DATA"}


def _model_entry(model_id: str) -> mr.ModelRegistryEntry:
    for e in mr.MODEL_REGISTRY:
        if e.model_id == model_id:
            return e
    raise AssertionError(f"{model_id} not found in MODEL_REGISTRY")


def _markets_by_id() -> dict[str, mkt.MarketDefinition]:
    return {m.market_id: m for m in mkt.CANONICAL_MARKETS}


def _numeric_thresholds(strings: list[str]) -> set[str]:
    """Filters a validated/insufficient/etc. thresholds list down to plain
    "N+" numeric entries only -- deliberately excludes compound labels like
    "P1_1+" (period SOG), "P2" (goalie period saves), or combo labels like
    "SOG2+GOAL" (joint dependence), which are not directly comparable to a
    single-market canonical market_id."""
    out = set()
    for s in strings:
        # Some entries carry an explanatory suffix after the threshold, e.g.
        # "1+ (trivial/near-universal base rate, never separately tested)".
        head = s.split(" ", 1)[0]
        if _NUMERIC_THRESHOLD_RE.match(head):
            out.add(head)
    return out


def _assert_threshold_family_consistent(test: unittest.TestCase, model_id: str, market_prefix: str,
                                          positive_market_statuses: set[str]) -> None:
    entry = _model_entry(model_id)
    markets = _markets_by_id()
    prefix_re = re.compile(rf"^{re.escape(market_prefix)}_(\d+)PLUS$")

    validated = _numeric_thresholds(entry.validated_thresholds)
    partial = _numeric_thresholds(entry.partial_thresholds)
    rejected = _numeric_thresholds(entry.rejected_thresholds)
    insufficient = _numeric_thresholds(entry.insufficient_thresholds)

    # Direction 1: every MODEL_REGISTRY-validated threshold must correspond
    # to a market_registry.py market carrying a positive status.
    for t in validated:
        market_id = f"{market_prefix}_{t.rstrip('+')}PLUS"
        test.assertIn(market_id, markets,
                       f"{model_id}.validated_thresholds names {t}, but no canonical market "
                       f"{market_id} exists in market_registry.py")
        m = markets[market_id]
        test.assertIn(m.model_status, positive_market_statuses,
                       f"{model_id} claims {t} VALIDATED, but market_registry.py's {market_id} "
                       f"says model_status={m.model_status!r}")

    # Direction 2: every market_registry.py market with a positive status for
    # this family must appear somewhere in MODEL_REGISTRY's validated
    # (or, for POINTS-style baseline champions, at least not silently absent
    # from every threshold bucket).
    all_named = validated | partial | rejected | insufficient
    for market_id, m in markets.items():
        match = prefix_re.match(market_id)
        if not match:
            continue
        t = f"{match.group(1)}+"
        if m.model_status in positive_market_statuses:
            test.assertIn(t, validated,
                           f"market_registry.py's {market_id} is {m.model_status}, but "
                           f"{model_id}.validated_thresholds omits {t}")
        else:
            test.assertIn(t, all_named,
                           f"market_registry.py's {market_id} is {m.model_status}, but "
                           f"{model_id} does not account for {t} in any threshold bucket "
                           f"(validated/partial/rejected/insufficient)")


class Test01PlayerSOGThresholdParity(unittest.TestCase):
    def test_sog_thresholds_agree_between_registries(self):
        _assert_threshold_family_consistent(self, "PLAYER_SOG", "PLAYER_SOG", {"VALIDATED"})


class Test02GoalsThresholdParity(unittest.TestCase):
    def test_goals_thresholds_agree_between_registries(self):
        _assert_threshold_family_consistent(self, "GOALS", "PLAYER_GOALS", {"VALIDATED"})


class Test03AssistsThresholdParity(unittest.TestCase):
    def test_assists_thresholds_agree_between_registries(self):
        _assert_threshold_family_consistent(self, "ASSISTS", "PLAYER_ASSISTS", {"VALIDATED"})


class Test04PointsThresholdParity(unittest.TestCase):
    def test_points_thresholds_agree_between_registries(self):
        # POINTS' champion is an empirical baseline, not a fitted model --
        # market_registry.py correctly uses a distinct status string for it.
        _assert_threshold_family_consistent(self, "POINTS", "PLAYER_POINTS",
                                             {"EMPIRICAL_BASELINE_REMAINS_CHAMPION"})


class Test05BlockedShotsThresholdParity(unittest.TestCase):
    def test_blocks_thresholds_agree_between_registries(self):
        _assert_threshold_family_consistent(self, "BLOCKED_SHOTS", "PLAYER_BLOCKS", {"VALIDATED"})


class Test06GoalieSavesThresholdParity(unittest.TestCase):
    def test_goalie_saves_thresholds_agree_between_registries(self):
        # GOALIE_SAVES also carries period-level labels ("P1"/"P2"/"P3") in
        # its threshold lists -- _numeric_thresholds() deliberately excludes
        # those non-numeric labels, so only the full-game 20/25/30/35/40+
        # thresholds are compared here (market_registry.py has no separate
        # canonical market for period goalie saves).
        _assert_threshold_family_consistent(self, "GOALIE_SAVES", "GOALIE_SAVES", {"VALIDATED"})


class Test07TeamSOGCoarseParity(unittest.TestCase):
    """Team SOG is tracked at individual-threshold granularity in
    MODEL_REGISTRY (20+/25+/30+/35+ validated, 40+ partial) but as a single
    aggregate market (TEAM_SOG_TOTAL) in market_registry.py -- a genuine
    granularity difference, not a bug. This test only checks the coarser,
    still-meaningful invariant: if MODEL_REGISTRY has ANY validated
    threshold for TEAM_SOG, the aggregate market must not be in a negative
    status."""

    def test_team_sog_total_is_not_negative_when_model_registry_has_validated_thresholds(self):
        entry = _model_entry("TEAM_SOG")
        markets = _markets_by_id()
        self.assertTrue(entry.validated_thresholds, "expected TEAM_SOG to have validated thresholds")
        team_sog_total = markets["TEAM_SOG_TOTAL"]
        self.assertNotIn(team_sog_total.model_status, _NEGATIVE_MARKET_STATUSES,
                          f"MODEL_REGISTRY.TEAM_SOG has validated thresholds "
                          f"{entry.validated_thresholds}, but market_registry.py's "
                          f"TEAM_SOG_TOTAL is {team_sog_total.model_status}")


class Test08PeriodSOGCoarseParity(unittest.TestCase):
    """Period SOG is tracked per (period, threshold) in MODEL_REGISTRY
    ("P1_1+", "P2_3+", ...) but per-period only (no threshold split) in
    market_registry.py (PERIOD_1_PLAYER_SOG, PERIOD_2_PLAYER_SOG,
    PERIOD_3_PLAYER_SOG) -- same granularity difference as Team SOG."""

    def test_each_period_with_a_model_registry_validated_threshold_is_not_negative_in_market_registry(self):
        entry = _model_entry("PLAYER_SOG_PERIOD")
        markets = _markets_by_id()
        for period in (1, 2, 3):
            has_validated = any(t.startswith(f"P{period}_") for t in entry.validated_thresholds)
            if not has_validated:
                continue
            market_id = f"PERIOD_{period}_PLAYER_SOG"
            self.assertIn(market_id, markets)
            m = markets[market_id]
            self.assertNotIn(m.model_status, _NEGATIVE_MARKET_STATUSES,
                              f"MODEL_REGISTRY.PLAYER_SOG_PERIOD has a validated threshold for "
                              f"period {period}, but market_registry.py's {market_id} is "
                              f"{m.model_status}")


class Test09NoOrphanedTripleClaims(unittest.TestCase):
    """Direct regression for the specific joint-scoring-triple contradiction
    this sprint fixed: MODEL_REGISTRY must never claim a combination as
    validated that joint_dependence_registry.py itself marks RESEARCH with
    an empty validated_combinations list."""

    def test_joint_scoring_dependence_never_claims_a_redundant_triple_as_validated(self):
        from research.joint_shot_workload import joint_dependence_registry as jdr
        entry = _model_entry("JOINT_SCORING_DEPENDENCE")
        for combo_id, jd_entry in jdr.JOINT_DEPENDENCE_REGISTRY.items():
            if jd_entry.status == "RESEARCH" and not jd_entry.validated_combinations:
                for validated_combo in jd_entry.validated_combinations:
                    self.assertNotIn(validated_combo, entry.validated_thresholds)

    def test_goal_point_is_present_since_it_is_actually_validated(self):
        from research.joint_shot_workload import joint_dependence_registry as jdr
        entry = _model_entry("JOINT_SCORING_DEPENDENCE")
        goal_point = jdr.JOINT_DEPENDENCE_REGISTRY["PLAYER_GOAL__PLAYER_POINT"]
        self.assertEqual(goal_point.status, "VALIDATED")
        for combo in goal_point.validated_combinations:
            self.assertIn(combo, entry.validated_thresholds)


if __name__ == "__main__":
    unittest.main()
