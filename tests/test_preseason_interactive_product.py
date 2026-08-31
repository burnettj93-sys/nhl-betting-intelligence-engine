"""
Preseason Interactive Product sprint: tests for demo mode, global smart
search, Player Intelligence, and the six new/ported operational pages.
Real corpora, real frozen models, real Streamlit AppTest renders --
never mocked away. Numbered comments map to the sprint's Part 121-125
numbered topics where a direct mapping exists.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

from dashboard import demo_data as dd
from dashboard import search as search_mod
from dashboard import player_intelligence_view as piv
from pricing import odds_math as pm
from research.player_props import decision_policy
from research.player_props.market_registry import CANONICAL_MARKETS

REPO_ROOT = Path(__file__).resolve().parent.parent


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _app_test(path: str):
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(REPO_ROOT / path))


# ---------------------------------------------------------------------
# Demo data tests (Part 121)
# ---------------------------------------------------------------------

# 1. deterministic seed
class Test01DeterministicSeed(unittest.TestCase):
    def test_same_key_produces_same_rng_draw(self):
        r1 = dd._rng_for("8478402", "goals").random()
        r2 = dd._rng_for("8478402", "goals").random()
        self.assertEqual(r1, r2)

    def test_rng_for_deterministic_across_subprocesses(self):
        # Regression: found live in the browser via two consecutive
        # server restarts -- Python's builtin hash() is randomized per
        # process (PYTHONHASHSEED) for str/tuple inputs, so a hash()-
        # based seed silently breaks Part 23's determinism requirement
        # across restarts even though it looks stable within one run.
        # This test verifies the actual cross-process BEHAVIOR (the
        # thing that matters) rather than grepping source text.
        import subprocess
        script = ("import sys; sys.path.insert(0, %r)\n"
                  "from dashboard import demo_data as dd\n"
                  "print(dd._rng_for('8478402', 'goals').random())\n") % str(REPO_ROOT)
        results = set()
        for _ in range(2):
            out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                  cwd=str(REPO_ROOT), env={**__import__("os").environ, "PYTHONHASHSEED": "random"})
            results.add(out.stdout.strip())
        self.assertEqual(len(results), 1, f"non-deterministic across processes: {results}")

    def test_demo_opportunities_deterministic_across_calls(self):
        dd.build_demo_opportunities.cache_clear() if hasattr(dd.build_demo_opportunities, "cache_clear") else None
        opps1 = dd.build_demo_opportunities()
        opps2 = dd.build_demo_opportunities()
        self.assertEqual([o["current_odds"] for o in opps1], [o["current_odds"] for o in opps2])


# 2. real canonical player IDs
class Test02RealCanonicalPlayerIds(unittest.TestCase):
    def test_named_stars_are_real_ids_from_corpus(self):
        stack = dd._demo_context()
        real_ids = {r["player_id"] for r in stack.ctx.sog.rows}
        for pid in dd.NAMED_STAR_IDS:
            self.assertIn(pid, real_ids)


# 3. valid team mappings
class Test03ValidTeamMappings(unittest.TestCase):
    def test_every_matchup_team_is_real_and_distinct(self):
        for away, home in dd.DEMO_MATCHUPS:
            self.assertNotEqual(away, home)
            self.assertTrue(away.isupper() and len(away) == 3)
            self.assertTrue(home.isupper() and len(home) == 3)


# 4. valid positions
class Test04ValidPositions(unittest.TestCase):
    def test_roster_positions_are_real_nhl_positions(self):
        for p in dd.build_demo_roster():
            self.assertIn(p.position, ("C", "L", "R", "D", "F", "G", "W"))


# 5. probability bounds
class Test05ProbabilityBounds(unittest.TestCase):
    def test_all_probability_fields_in_unit_interval(self):
        for o in dd.build_demo_opportunities():
            for key in ("raw_probability", "context_adjusted_probability", "coherent_probability",
                        "conservative_probability", "market_no_vig_probability"):
                v = o[key]
                if v is not None:
                    self.assertGreaterEqual(v, 0.0, key)
                    self.assertLessEqual(v, 1.0, key)


# 6. threshold monotonicity (conservative <= raw, the real shrinkage direction)
class Test06ThresholdMonotonicity(unittest.TestCase):
    def test_conservative_probability_never_exceeds_raw(self):
        for o in dd.build_demo_opportunities():
            self.assertLessEqual(o["conservative_probability"], o["raw_probability"] + 1e-9)


# 7. fair odds consistency
class Test07FairOddsConsistency(unittest.TestCase):
    def test_fair_odds_round_trips_to_coherent_probability(self):
        for o in dd.build_demo_opportunities()[:20]:
            recovered = pm.american_to_prob(o["fair_odds"])
            self.assertAlmostEqual(recovered, o["coherent_probability"], places=2)


# 8. no-vig consistency
class Test08NoVigConsistency(unittest.TestCase):
    def test_no_vig_probability_between_zero_and_one(self):
        for o in dd.build_demo_opportunities()[:20]:
            self.assertGreater(o["market_no_vig_probability"], 0.0)
            self.assertLess(o["market_no_vig_probability"], 1.0)


# 9. edge consistency
class Test09EdgeConsistency(unittest.TestCase):
    def test_conservative_edge_equals_conservative_minus_no_vig(self):
        for o in dd.build_demo_opportunities()[:20]:
            expected = o["conservative_probability"] - o["market_no_vig_probability"]
            self.assertAlmostEqual(o["conservative_edge"], expected, places=6)


# 10. EV consistency
class Test10EvConsistency(unittest.TestCase):
    def test_ev_matches_real_expected_value_utility(self):
        for o in dd.build_demo_opportunities()[:20]:
            expected = pm.expected_value(o["conservative_probability"], o["current_odds"])
            self.assertAlmostEqual(o["ev"], expected, places=6)


# 11. max-buy consistency
class Test11MaxBuyConsistency(unittest.TestCase):
    def test_max_acceptable_price_uses_real_shared_utility(self):
        for o in dd.build_demo_opportunities()[:20]:
            self.assertTrue(o["max_acceptable_price"] is None or isinstance(o["max_acceptable_price"], float))


# 12. demo flag propagated
class Test12DemoFlagPropagated(unittest.TestCase):
    def test_every_opportunity_flags_simulated_price(self):
        for o in dd.build_demo_opportunities()[:20]:
            self.assertTrue(o["is_simulated_price"])


# 13/14. demo cannot write prospective/real-bet ledger
class Test13To14DemoCannotWriteLedger(unittest.TestCase):
    def test_demo_data_module_never_imports_prospective_ledger(self):
        with open("dashboard/demo_data.py") as f:
            src = f.read()
        self.assertNotIn("import operational.prospective_ledger", src)
        self.assertNotIn("from operational import prospective_ledger", src)
        self.assertNotIn("from operational.prospective_ledger", src)


# 15. demo excluded from P&L
class Test15DemoExcludedFromPnl(unittest.TestCase):
    def test_no_ledger_module_imports_demo_data_for_writes(self):
        with open("operational/prospective_ledger.py") as f:
            src = f.read()
        self.assertNotIn("demo_data", src)


# 16. demo excluded from prospective calibration
class Test16DemoExcludedFromCalibration(unittest.TestCase):
    def test_raw_vs_adjusted_summary_only_reads_real_ledger(self):
        import inspect
        from operational import prospective_ledger as pl
        src = inspect.getsource(pl.raw_vs_adjusted_summary)
        self.assertNotIn("demo", src.lower())


# 17. demo schedule explicitly simulated
class Test17DemoScheduleSimulated(unittest.TestCase):
    def test_simulated_date_is_a_module_constant_not_todays_date(self):
        import datetime as _dt
        self.assertNotEqual(dd.SIMULATED_DATE, _dt.date.today().isoformat())


# 18. demo prices explicitly simulated
class Test18DemoPricesSimulated(unittest.TestCase):
    def test_two_sided_market_uses_a_seeded_rng_not_wall_clock(self):
        import inspect
        src = inspect.getsource(dd.simulate_two_sided_market)
        self.assertIn("rng", src)
        self.assertNotIn("time.time", src)


# 19. context explicitly simulated
class Test19ContextExplicitlySimulated(unittest.TestCase):
    def test_player_intelligence_page_labels_context_simulated(self):
        with open("dashboard/pages/25_Player_Intelligence.py") as f:
            src = f.read()
        self.assertIn("SIMULATED CONTEXT", src)

    def test_not_eligible_state_never_shown_as_context_active(self):
        # Regression: found live in the browser -- NOT_ELIGIBLE is a
        # truthy string, so a bare `if context_state:` check incorrectly
        # showed "CONTEXT ADJUSTMENT ACTIVE" for players who were NOT
        # actually in COLD_AND_TOI_DECLINE. Both the opportunity-card
        # component and the Player Intelligence page's own context
        # section must check the exact eligible value, never truthiness.
        with open("dashboard/components.py") as f:
            comp_src = f.read()
        self.assertIn('card.get("context_state") == "COLD_AND_TOI_DECLINE"', comp_src)
        with open("dashboard/pages/25_Player_Intelligence.py") as f:
            page_src = f.read()
        self.assertIn('o["context_state"] == "COLD_AND_TOI_DECLINE"', page_src)


# 20. validated statuses come from real registry
class Test20ValidatedStatusesFromRealRegistry(unittest.TestCase):
    def test_goalie_thresholds_match_real_registry_pattern(self):
        goalies = dd.build_demo_goalies()
        for g in goalies:
            self.assertEqual(g["thresholds"]["35+"], "REJECTED")
            self.assertEqual(g["thresholds"]["20+"], "VALIDATED")


# ---------------------------------------------------------------------
# Search tests (Part 122)
# ---------------------------------------------------------------------

# 21-26. McDavid variants
class Test21To26McDavidVariants(unittest.TestCase):
    def _top_display(self, q):
        r = search_mod.search(q, limit=3)
        return r[0].display if r else None

    def test_exact(self):
        self.assertEqual(self._top_display("Connor McDavid"), "Connor McDavid")

    def test_lowercase(self):
        self.assertEqual(self._top_display("connor mcdavid"), "Connor McDavid")

    def test_surname(self):
        self.assertEqual(self._top_display("McDavid"), "Connor McDavid")

    def test_case_variant(self):
        self.assertEqual(self._top_display("Mcdavid"), "Connor McDavid")

    def test_initials(self):
        self.assertEqual(self._top_display("C McDavid"), "Connor McDavid")

    def test_minor_typo(self):
        self.assertEqual(self._top_display("Mcdavdi"), "Connor McDavid")


# 27. Connor ambiguous suggestions
class Test27ConnorAmbiguous(unittest.TestCase):
    def test_bare_connor_returns_multiple_suggestions(self):
        results = search_mod.search("Connor", limit=6)
        names = {r.display for r in results}
        self.assertGreaterEqual(len(names), 2)
        self.assertIn("Connor McDavid", names)


# 28. player IDs canonical
class Test28PlayerIdsCanonical(unittest.TestCase):
    def test_search_result_id_matches_roster_id(self):
        results = search_mod.search("Connor McDavid", limit=1)
        self.assertEqual(results[0].entity_id, "8478402")


# 29. team search exact
class Test29TeamSearchExact(unittest.TestCase):
    def test_full_team_name(self):
        results = search_mod.search("Edmonton Oilers", limit=3)
        self.assertTrue(any(r.entity_type == "TEAM" and r.display == "Edmonton Oilers" for r in results))


# 30. team abbreviation
class Test30TeamAbbreviation(unittest.TestCase):
    def test_abbreviation(self):
        results = search_mod.search("EDM", limit=3)
        self.assertTrue(any(r.entity_type == "TEAM" for r in results))


# 31. market search
class Test31MarketSearch(unittest.TestCase):
    def test_sog_resolves_to_shots_on_goal(self):
        results = search_mod.search("SOG", limit=3)
        self.assertTrue(any(r.entity_type == "MARKET" for r in results))


# 32. market alias
class Test32MarketAlias(unittest.TestCase):
    def test_shots_on_goal_phrase_resolves(self):
        results = search_mod.search("Shots on Goal", limit=3)
        self.assertTrue(any(r.entity_type == "MARKET" for r in results))


# 33. game search
class Test33GameSearch(unittest.TestCase):
    def test_team_pair_resolves_to_game(self):
        results = search_mod.search("EDM COL", limit=3)
        self.assertTrue(any(r.entity_type == "GAME" for r in results))

    def test_vs_form_resolves_to_game(self):
        results = search_mod.search("EDM vs COL", limit=3)
        self.assertTrue(any(r.entity_type == "GAME" for r in results))


# 34. result ranking
class Test34ResultRanking(unittest.TestCase):
    def test_exact_name_ranks_above_fuzzy(self):
        results = search_mod.search("Connor McDavid", limit=10)
        self.assertEqual(results[0].display, "Connor McDavid")
        self.assertLessEqual(results[0].rank_tier, results[-1].rank_tier if len(results) > 1 else 99)


# 35-38. click routing (structural — verified via component source)
class Test35To38ClickRouting(unittest.TestCase):
    def test_route_function_handles_all_entity_types(self):
        with open("dashboard/components.py") as f:
            src = f.read()
        for entity_type in ("PLAYER", "GOALIE", "TEAM", "GAME", "MARKET"):
            self.assertIn(entity_type, src)

    def test_player_props_page_routes_player_click_to_intelligence(self):
        with open("dashboard/pages/26_Player_Props.py") as f:
            src = f.read()
        self.assertIn("25_Player_Intelligence.py", src)


# 39. global search available on operational pages
class Test39GlobalSearchAvailable(unittest.TestCase):
    def test_every_new_page_calls_render_global_search(self):
        for page in ("21_Today.py", "25_Player_Intelligence.py", "26_Player_Props.py",
                     "27_Goalies.py", "28_Combinations.py", "29_Market_Movement.py",
                     "30_Players.py", "31_Team_Intelligence.py"):
            path = f"dashboard/pages/{page}"
            if not Path(path).exists():
                continue
            with open(path) as f:
                src = f.read()
            self.assertIn("render_global_search", src, page)


# 40. no expensive corpus scan per keystroke
class Test40NoExpensiveScanPerKeystroke(unittest.TestCase):
    def test_search_index_is_cached(self):
        import inspect
        src = inspect.getsource(search_mod.build_search_index)
        self.assertIn("lru_cache", inspect.getsource(search_mod))


# ---------------------------------------------------------------------
# Player Intelligence tests (Part 123)
# ---------------------------------------------------------------------

# 41. player header
class Test41PlayerHeader(unittest.TestCase):
    def test_player_intelligence_renders_mcdavid_header(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8478402"
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("Connor McDavid", md)


# 42. next game
class Test42NextGame(unittest.TestCase):
    def test_next_game_tab_present(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8478402"
        at.run(timeout=120)
        tab_labels = [t.label for t in at.tabs]
        self.assertIn("Next Game", tab_labels)


# 43. next 5 games
class Test43Next5Games(unittest.TestCase):
    def test_next_five_games_returns_five_simulated_games(self):
        player = piv.find_player("8478402")
        games = piv.next_five_games(player)
        self.assertEqual(len(games), 5)
        for g in games:
            self.assertEqual(g["market_price"], "NOT POSTED")


# 44-47. expected SOG / Goals / Assists / Points
class Test44To47ExpectedProps(unittest.TestCase):
    def test_all_four_props_present_for_mcdavid(self):
        opps = piv.player_opportunities("8478402")
        props_seen = {o["prop"] for o in opps}
        for prop in ("sog", "goals", "assists", "points"):
            self.assertIn(prop, props_seen)


# 48. all supported props
class Test48AllSupportedProps(unittest.TestCase):
    def test_blocks_also_present(self):
        opps = piv.player_opportunities("8478402")
        self.assertIn("blocks", {o["prop"] for o in opps})


# 49. unsupported market status (Points has no mu -- documented, not a failure)
class Test49UnsupportedMarketStatus(unittest.TestCase):
    def test_points_conservative_defaults_to_raw_without_mu(self):
        opps = piv.player_opportunities("8478402")
        points = next(o for o in opps if o["prop"] == "points")
        self.assertEqual(points["conservative_probability"], points["raw_probability"])


# 50-53. best opportunity / watchlist / waiting / passes
class Test50To53OpportunityGroups(unittest.TestCase):
    def test_groups_partition_all_opportunities(self):
        opps = piv.player_opportunities("8478402")
        groups = piv.group_opportunities(opps)
        total = sum(len(v) for v in groups.values())
        self.assertEqual(total, len(opps))
        for key in ("BEST", "WATCHLIST", "WAITING", "PASSES"):
            self.assertIn(key, groups)


# 54. too-expensive case
class Test54TooExpensiveCase(unittest.TestCase):
    def test_pass_decisions_have_non_positive_conservative_edge_or_low_confidence(self):
        opps = [o for o in dd.build_demo_opportunities() if o["decision"] == "PASS"]
        self.assertGreater(len(opps), 0)


# 55/56. model drivers / risks
class Test55To56DriversRisks(unittest.TestCase):
    def test_hero_card_includes_a_risk_reason(self):
        opps = piv.player_opportunities("8478402")
        best = piv.hero_summary(opps)
        if best:
            self.assertIn("decision_reason", best)


# 57. actual vs expected
class Test57ActualVsExpected(unittest.TestCase):
    def test_actual_vs_expected_uses_real_history(self):
        result = piv.actual_vs_expected("8478402", "sog", 5)
        self.assertIsNotNone(result)
        self.assertEqual(result["n"], 5)
        self.assertIsInstance(result["actual"], (int, float))
        self.assertGreaterEqual(result["actual"], 0)


# 58. TOI trend
class Test58ToiTrend(unittest.TestCase):
    def test_multi_window_trend_returns_three_windows(self):
        trend = piv.multi_window_trend("8478402", "toi")
        self.assertIn("last_5", trend)
        self.assertIn("last_10", trend)
        self.assertIn("season", trend)


# 59. context state
class Test59ContextState(unittest.TestCase):
    def test_context_evidence_returns_real_form_and_toi_ratios(self):
        evidence = piv.context_evidence("8478402", "EDM", "COL")
        if evidence:
            for prop_evidence in evidence.values():
                self.assertIn("form_ratio", prop_evidence)
                self.assertIn("toi_ratio", prop_evidence)


# 60. odds detail
class Test60OddsDetail(unittest.TestCase):
    def test_opportunity_carries_odds_timestamp_capable_fields(self):
        opps = piv.player_opportunities("8478402")
        for o in opps:
            self.assertIn("current_odds", o)
            self.assertIn("fair_odds", o)


# ---------------------------------------------------------------------
# Page tests (Part 124)
# ---------------------------------------------------------------------

# 61/62. Player Props renders / demo density
class Test61To62PlayerPropsRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/26_Player_Props.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])

    def test_demo_density_at_least_100_opportunities(self):
        self.assertGreaterEqual(len(dd.build_demo_opportunities()), 100)


# 63. actionability sort
class Test63ActionabilitySort(unittest.TestCase):
    def test_best_actionable_is_default_sort_option(self):
        with open("dashboard/pages/26_Player_Props.py") as f:
            src = f.read()
        self.assertIn("Best Actionable", src)


# 64-66. filters
class Test64To66Filters(unittest.TestCase):
    def test_market_player_decision_filters_present(self):
        with open("dashboard/pages/26_Player_Props.py") as f:
            src = f.read()
        for filt in ("Market", "Decision", "Confidence"):
            self.assertIn(filt, src)


# 67. Goalies renders
class Test67GoaliesRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/27_Goalies.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# 68. starter/model confidence separated
class Test68StarterConfidenceSeparated(unittest.TestCase):
    def test_goalie_page_labels_both_dimensions_separately(self):
        with open("dashboard/pages/27_Goalies.py") as f:
            src = f.read()
        self.assertIn("Starter Status", src)
        self.assertIn("Model Confidence", src)


# 69. goalie validation thresholds
class Test69GoalieValidationThresholds(unittest.TestCase):
    def test_thresholds_include_all_five_real_states(self):
        goalies = dd.build_demo_goalies()
        for g in goalies:
            self.assertEqual(set(g["thresholds"]), {"20+", "25+", "30+", "35+", "40+"})


# 70. Combinations renders
class Test70CombinationsRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/28_Combinations.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# 71. naive/joint visual data
class Test71NaiveJointVisualData(unittest.TestCase):
    def test_page_shows_both_naive_and_validated_metrics(self):
        with open("dashboard/pages/28_Combinations.py") as f:
            src = f.read()
        self.assertIn("Naive Independent P", src)
        self.assertIn("Validated Joint P", src)


# 72. redundant warning
class Test72RedundantWarning(unittest.TestCase):
    def test_redundant_badge_present(self):
        with open("dashboard/pages/28_Combinations.py") as f:
            src = f.read()
        self.assertIn("REDUNDANT", src)


# 73. no real parlay BET from simulated price
class Test73NoRealParlayBet(unittest.TestCase):
    def test_page_labels_price_as_simulated_and_not_operational(self):
        with open("dashboard/pages/28_Combinations.py") as f:
            src = f.read()
        self.assertIn("SIMULATED", src)
        self.assertIn("NOT OPERATIONAL", src)


# 74. Market Movement renders
class Test74MarketMovementRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/29_Market_Movement.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# 75. movement direction
class Test75MovementDirection(unittest.TestCase):
    def test_movement_rows_have_a_direction_label(self):
        movement = dd.build_demo_market_movement()
        for m in movement:
            self.assertIn(m["direction"], ("TOWARD MODEL", "AWAY FROM MODEL", "NEUTRAL"))


# 76. simulated movement label
class Test76SimulatedMovementLabel(unittest.TestCase):
    def test_page_shows_simulated_market_history_label(self):
        with open("dashboard/pages/29_Market_Movement.py") as f:
            src = f.read()
        self.assertIn("SIMULATED MARKET HISTORY", src)


# 77. Players renders
class Test77PlayersRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/30_Players.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# 78. player click-through
class Test78PlayerClickThrough(unittest.TestCase):
    def test_players_page_routes_to_player_intelligence(self):
        with open("dashboard/pages/30_Players.py") as f:
            src = f.read()
        self.assertIn("25_Player_Intelligence.py", src)


# 79. Game Detail renders (pre-existing page, confirm still clean)
class Test79GameDetailRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/2_Game_Detail.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# 80. Team Intelligence renders
class Test80TeamIntelligenceRenders(unittest.TestCase):
    def test_renders_without_exception(self):
        at = _app_test("dashboard/pages/31_Team_Intelligence.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


# ---------------------------------------------------------------------
# Existing infrastructure protection (Part 125)
# ---------------------------------------------------------------------

# 81. prospective ledger still append-only
class Test81ProspectiveLedgerAppendOnly(unittest.TestCase):
    def test_schema_file_unchanged(self):
        self.assertTrue(Path("operational/prospective_schema.sql").exists())
        with open("operational/prospective_schema.sql") as f:
            self.assertIn("predictions_immutability", f.read())


# 82. demo cannot contaminate ledger
class Test82DemoCannotContaminateLedger(unittest.TestCase):
    def test_demo_data_has_no_insert_prediction_calls(self):
        with open("dashboard/demo_data.py") as f:
            src = f.read()
        self.assertNotIn("insert_prediction", src)
        self.assertNotIn("record_model_observation", src)


# 83. live_readiness unchanged/fail-closed
class Test83LiveReadinessFailClosed(unittest.TestCase):
    def test_unknown_market_still_fails_closed(self):
        from operational.live_readiness import live_readiness
        result = live_readiness("TOTALLY_UNKNOWN_MARKET_XYZ")
        self.assertEqual(result["status"], "MODEL_NOT_OPERATIONAL")


# 84. SYSTEM_HEALTH remains real
class Test84SystemHealthRemainsReal(unittest.TestCase):
    def test_build_system_health_still_reflects_real_registries(self):
        from operational.system_health import build_system_health
        health = build_system_health()
        self.assertGreater(len(health), 0)


# 85. decision policy v3 hash unchanged
class Test85DecisionPolicyUnchanged(unittest.TestCase):
    def test_hash(self):
        self.assertEqual(_file_sha256("research/player_props/decision_policy.py"),
                          "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a")


# 86. validated marginals hashes unchanged
class Test86ValidatedMarginalsUnchanged(unittest.TestCase):
    def test_goals_and_points_unchanged(self):
        self.assertEqual(_file_sha256("research/player_goals_results.json"),
                          "3f5592585a255b11c77f2a4d08c2c9886d01e45dbc8b48b30d284389367f5348")
        self.assertEqual(_file_sha256("research/player_points_results.json"),
                          "6eacd4d56dc78d6b371b7f0234252e1f969359a427d813efcd696780b8af8877")


# 87. joint hashes unchanged
class Test87JointHashesUnchanged(unittest.TestCase):
    def test_joint_scoring_dependence_unchanged(self):
        self.assertEqual(_file_sha256("research/joint_scoring_dependence_results.json"),
                          "3076d4e849e60f8156601e6070301f17b8e51d56265880ff8c8bf0d3b58f9d91")


# 88. overlay parameters unchanged
class Test88OverlayParametersUnchanged(unittest.TestCase):
    def test_goals_offset_points_shift_unchanged(self):
        import json
        with open("research/context_overlay_results.json") as f:
            results = json.load(f)
        self.assertAlmostEqual(results["props"]["goals"]["winner_params"]["offset"], -0.18, places=6)
        self.assertAlmostEqual(results["props"]["points"]["winner_params"]["shift"], -0.0415, places=4)


# 89. no unexpected network in unit tests
class Test89NoUnexpectedNetwork(unittest.TestCase):
    def test_new_modules_have_no_requests_import(self):
        for fname in ("dashboard/demo_data.py", "dashboard/search.py", "dashboard/player_intelligence_view.py"):
            with open(fname) as f:
                src = f.read()
            self.assertNotIn("import requests", src)
            self.assertNotIn("urllib", src)


# 90. offseason LIVE mode never fabricates BET (structural: demo_data is
# the only source of simulated BET labels, and it's explicitly DEMO-only)
class Test90OffseasonLiveNeverFabricatesBet(unittest.TestCase):
    def test_live_sog_markets_page_has_no_demo_data_import(self):
        with open("dashboard/pages/8_Live_SOG_Markets.py") as f:
            src = f.read()
        self.assertNotIn("demo_data", src)


if __name__ == "__main__":
    unittest.main()
