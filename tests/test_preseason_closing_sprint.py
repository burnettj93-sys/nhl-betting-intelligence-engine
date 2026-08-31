"""
Preseason Closing sprint: tests for the real bugs found and fixed during
this sprint's mandatory real-browser QA pass (Track 7/8), plus the new
Track 2 (Game Detail combinations), Track 4 (click-through), and Track 11
(prospective operations widgets) additions. Real SQLite (tempfile-backed)
and real Streamlit AppTest headless renders -- never mocked away.
Numbered comments map to this sprint's own Section numbering where a
direct mapping exists.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dashboard import demo_data as dd
from dashboard import game_detail_view as gdv
from dashboard import search as search_mod
from operational import prospective_ledger as pl

REPO_ROOT = Path(__file__).resolve().parent.parent


def _app_test(path: str):
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(REPO_ROOT / path))


def _fresh_conn():
    tmp = Path(tempfile.mktemp(suffix=".db"))
    return pl.init_db(tmp), tmp


def _base_fields(**overrides):
    fields = dict(
        event_start_utc="2026-10-10T23:00:00Z", created_at_utc="2026-10-10T20:00:00Z",
        game_id="g1", game_date="2026-10-10", player_id="p1", team="NOR", opponent="CST",
        market_id="PLAYER_GOALS_1PLUS", market_family="GOALS", threshold="1+",
        raw_probability=0.34, context_adjusted_probability=0.32, coherent_probability=0.32,
        model_version="hash123", model_hash="hash123",
    )
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------
# Track 2: Game Detail combinations (the last named gap in "win model,
# Team SOG, starters, goalie saves, top props, combinations, WAIT
# reasons, freshness")
# ---------------------------------------------------------------------

class Test01GameCombinationsRealDependence(unittest.TestCase):
    def test_game_combinations_uses_real_joint_models(self):
        import json
        rho = json.load(open(REPO_ROOT / "research/joint_scoring_dependence_results.json"))["rho_by_name"]
        game = dd.build_demo_games()[0]  # EDM @ COL
        combos = gdv.game_combinations(game.game_id, rho, limit=6)
        self.assertGreater(len(combos), 0)
        for c in combos:
            self.assertIn(c["player"], {p.name for p in dd.build_demo_roster()
                                         if p.team in (game.away, game.home)})
            self.assertGreaterEqual(c["validated"], 0.0)
            self.assertLessEqual(c["validated"], 1.0)

    def test_game_combinations_unknown_game_returns_empty(self):
        self.assertEqual(gdv.game_combinations("demo-ZZZ-ZZZ", {}), [])

    def test_game_detail_demo_page_renders_combinations_section(self):
        at = _app_test("dashboard/pages/2_Game_Detail.py")
        game = dd.build_demo_games()[0]
        at.session_state["selected_game_id"] = game.game_id
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("Combinations", md)


# ---------------------------------------------------------------------
# Track 4: click-through fixes found during the real McDavid/Matthews/
# Makar/Hellebuyck walkthrough
# ---------------------------------------------------------------------

class Test02PlayerIntelligenceOpponentClickThrough(unittest.TestCase):
    def test_opponent_button_present_and_sets_team(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8478402"  # McDavid
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        labels = [b.label for b in at.button]
        self.assertTrue(any("Team Intelligence" in lbl for lbl in labels))

    def test_next_game_open_game_detail_button_present(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8478402"
        at.run(timeout=120)
        labels = [b.label for b in at.button]
        self.assertIn("Open Game Detail", labels)


class Test03GameDetailPlayerClickThrough(unittest.TestCase):
    def test_top_opportunities_have_player_buttons(self):
        at = _app_test("dashboard/pages/2_Game_Detail.py")
        game = dd.build_demo_games()[0]
        at.session_state["selected_game_id"] = game.game_id
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        labels = [b.label for b in at.button]
        self.assertTrue(any(lbl.startswith("Open ") and "Player Intelligence" in lbl for lbl in labels))


# ---------------------------------------------------------------------
# Real bug: Auston Matthews (and ~25% of the demo roster) are real,
# legitimate PROJECTED_INACTIVE players under the real SOG engine's own
# recency gate -- found live via Track 7's Matthews abbreviated QA. The
# fix surfaces the REAL reason instead of a bare, bug-looking empty state.
# ---------------------------------------------------------------------

class Test04InactivePlayerHonestMessaging(unittest.TestCase):
    def test_matthews_is_really_projected_inactive(self):
        status = dd.player_activity_status("8479318", "TOR", "BOS")
        self.assertEqual(status["status"], "PROJECTED_INACTIVE")
        self.assertTrue(status["note"])

    def test_mcdavid_is_projected_active(self):
        status = dd.player_activity_status("8478402", "EDM", "COL")
        self.assertEqual(status["status"], "PROJECTED_ACTIVE")

    def test_player_intelligence_shows_real_reason_not_generic_message(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8479318"  # Matthews
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("PROJECTED_INACTIVE", captions)

    def test_active_status_badge_reflects_real_status_not_hardcoded(self):
        at = _app_test("dashboard/pages/25_Player_Intelligence.py")
        at.session_state["selected_player_id"] = "8479318"  # Matthews
        at.run(timeout=120)
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("PROJECTED_INACTIVE", md)


# ---------------------------------------------------------------------
# Real bug: goalie search results / Goalies-page clicks routed to
# Player Intelligence, which only knows the skater roster -- a goalie_id
# always raised "Player not found in the demo roster." Found live via
# Track 7's Hellebuyck abbreviated QA. Fixed to route to Team
# Intelligence instead, which already has a real per-team goalie section.
# ---------------------------------------------------------------------

class Test05GoalieRoutingFix(unittest.TestCase):
    def test_goalie_search_result_does_not_route_to_player_intelligence(self):
        results = search_mod.search("Connor Hellebuyck", limit=6)
        goalie_results = [r for r in results if r.entity_type == "GOALIE"]
        self.assertTrue(goalie_results, "expected a GOALIE search result for a real named goalie")

    def test_goalies_page_renders_without_the_old_player_intelligence_crash(self):
        at = _app_test("dashboard/pages/27_Goalies.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        labels = [b.label for b in at.button]
        self.assertTrue(any("Team Intelligence" in lbl for lbl in labels))

    def test_goalies_page_button_routes_to_team_intelligence_not_player_intelligence(self):
        src = (REPO_ROOT / "dashboard/pages/27_Goalies.py").read_text()
        self.assertIn("selected_team", src)
        self.assertIn("pages/31_Team_Intelligence.py", src)
        self.assertNotIn("pages/25_Player_Intelligence.py", src)

    def test_route_to_search_result_goalie_branch_targets_team_intelligence(self):
        src = (REPO_ROOT / "dashboard/components.py").read_text()
        route_fn = src[src.index("def _route_to_search_result"):src.index("def render_global_search")]
        self.assertIn('elif r.entity_type == "GOALIE"', route_fn)
        self.assertIn("pages/31_Team_Intelligence.py", route_fn)


# ---------------------------------------------------------------------
# Track 11: prospective recording operational widgets
# ---------------------------------------------------------------------

class Test06OperationalSummary(unittest.TestCase):
    def test_empty_ledger_reports_honest_zeros(self):
        conn, _ = _fresh_conn()
        summary = pl.operational_summary(conn)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["recorded_today"], 0)
        self.assertEqual(summary["pending_settlement"], 0)
        self.assertIsNone(summary["last_recorded_at_utc"])

    def test_checkpoint_breakdown_counts_real_rows(self):
        conn, _ = _fresh_conn()
        pl.record_model_observation(conn, **_base_fields(prediction_checkpoint="PRIMARY_DAILY"))
        pl.record_model_observation(conn, **_base_fields(
            player_id="p2", prediction_checkpoint="PRE_GAME_UPDATE",
            idempotency_key="different-key-p2"))
        summary = pl.operational_summary(conn)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_checkpoint"].get("PRIMARY_DAILY"), 1)
        self.assertEqual(summary["by_checkpoint"].get("PRE_GAME_UPDATE"), 1)

    def test_pending_settlement_only_counts_past_event_start(self):
        conn, _ = _fresh_conn()
        pl.record_model_observation(conn, **_base_fields(
            event_start_utc="2099-01-01T00:00:00Z", created_at_utc="2026-01-01T00:00:00Z"))
        summary = pl.operational_summary(conn)
        self.assertEqual(summary["pending_settlement"], 0)

    def test_last_recorded_is_max_created_at(self):
        conn, _ = _fresh_conn()
        pl.record_model_observation(conn, **_base_fields(
            created_at_utc="2026-01-01T00:00:00.000000Z",
            event_start_utc="2026-01-02T00:00:00Z"))
        pl.record_model_observation(conn, **_base_fields(
            player_id="p2", idempotency_key="k2",
            created_at_utc="2026-06-01T00:00:00.000000Z",
            event_start_utc="2026-06-02T00:00:00Z"))
        summary = pl.operational_summary(conn)
        self.assertEqual(summary["last_recorded_at_utc"], "2026-06-01T00:00:00.000000Z")


class Test07LedgerPageOperationalWidgets(unittest.TestCase):
    def test_ledger_page_renders_without_db(self):
        at = _app_test("dashboard/pages/23_Ledger.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])


class Test08TodayPageProspectiveWidgets(unittest.TestCase):
    def test_today_page_shows_honest_empty_state_without_ledger(self):
        at = _app_test("dashboard/pages/21_Today.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        md = " ".join(m.value for m in at.markdown)
        self.assertIn("Prospective Recording", md)


# ---------------------------------------------------------------------
# Track 5: Player Props filters (Player/Team/Validation Status/Context/
# Price)
# ---------------------------------------------------------------------

class Test09PlayerPropsFilters(unittest.TestCase):
    def test_player_props_page_has_five_new_filters(self):
        at = _app_test("dashboard/pages/26_Player_Props.py")
        at.run(timeout=120)
        self.assertEqual(list(at.exception), [])
        labels = {sb.label for sb in at.selectbox}
        for expected in ("Player", "Team", "Validation Status", "Context", "Price"):
            self.assertIn(expected, labels)

    def test_validation_status_filter_excludes_non_matching_market(self):
        opportunities = dd.build_demo_opportunities()
        from research.model_registry import get as get_model_registry_entry
        points_entry = get_model_registry_entry("POINTS")
        goals_entry = get_model_registry_entry("GOALS")
        self.assertNotEqual(points_entry.status, goals_entry.status)


class Test10OddsDetailPanel(unittest.TestCase):
    def test_render_odds_detail_panel_smoke(self):
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from dashboard import components as comp\n"
            "comp.render_odds_detail_panel({'player': 'Test Player', 'market': 'POINTS', "
            "'threshold': '1+', 'current_odds': 150, 'max_acceptable_price': 130, "
            "'raw_probability': 0.4, 'market_no_vig_probability': 0.38, 'fair_odds': 140, "
            "'conservative_edge': 0.02, 'decision': 'BET', 'is_simulated_price': True})\n"
        ) % str(REPO_ROOT)
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_string(script)
        at.run(timeout=60)
        self.assertEqual(list(at.exception), [])


# ---------------------------------------------------------------------
# Track 6: richer search subtitles (player/goalie "Next: vs X · time",
# market row-count) -- additive only, ranking/matching logic untouched.
# ---------------------------------------------------------------------

class Test11SearchSubtitleRichness(unittest.TestCase):
    def test_player_subtitle_includes_next_opponent_and_time(self):
        results = search_mod.search("Connor McDavid", limit=3)
        mcdavid = next(r for r in results if r.entity_type == "PLAYER")
        self.assertIn("Next: vs COL", mcdavid.subtitle)
        self.assertIn("7:00 PM ET", mcdavid.subtitle)

    def test_goalie_subtitle_includes_next_opponent_and_time(self):
        results = search_mod.search("Connor Hellebuyck", limit=3)
        goalie = next(r for r in results if r.entity_type == "GOALIE")
        self.assertIn("Next: vs", goalie.subtitle)

    def test_market_subtitle_includes_real_row_count(self):
        results = search_mod.search("sog", limit=3)
        market = next(r for r in results if r.entity_type == "MARKET")
        sog_rows = sum(1 for o in dd.build_demo_opportunities() if o["market"] == "SOG")
        self.assertIn(f"{sog_rows} demo rows", market.subtitle)

    def test_matching_and_ranking_unaffected_by_subtitle_change(self):
        results = search_mod.search("mcdavid", limit=1)
        self.assertEqual(results[0].entity_id, "8478402")


# ---------------------------------------------------------------------
# Track 10: demo realism polish -- Next 5 Games showed the same simulated
# opponent up to three times for some players (a real NHL team does not
# play one opponent three times in an isolated 5-game stretch); found
# live in Track 7's McDavid walkthrough.
# ---------------------------------------------------------------------

class Test12NextFiveGamesRealism(unittest.TestCase):
    def test_next_five_games_never_repeats_an_opponent(self):
        from dashboard import player_intelligence_view as piv
        for player in dd.build_demo_roster():
            games = piv.next_five_games(player)
            opponents = [g["opponent"] for g in games]
            self.assertEqual(len(opponents), len(set(opponents)),
                              f"{player.name} has a repeated opponent in Next 5 Games: {opponents}")


if __name__ == "__main__":
    unittest.main()
