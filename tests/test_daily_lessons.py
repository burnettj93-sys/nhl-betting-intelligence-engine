"""Yesterday makes today's parlays smarter: the postmortem persists its lessons (operational/learning_postmortem.py build_lessons) and tomorrow's review reads them as temporary cautions
(operational/eggy_intelligence.py _load_overlay_inputs / recency_overlay), with no conversational memory and no change to any model parameter or threshold."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from unittest import mock

from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from operational import learning_postmortem as lpm
from tests import test_recency_overlay as tro
from tests.test_learning_cohort import Base, D1, leg


def lr(key, player, outcome, cls=None, team="TOR", decision="BAD_DECISION", pp=None):
    claims = [{"id": "expected_pp", "verdict": pp}] if pp else []
    return {"key": key, "label": f"{player} — SHOTS ON GOAL 2+ — -200", "player": player, "team": team, "outcome": outcome,
            "assessment": {"classification": cls, "decision_quality": {"label": decision}, "claims": claims}}


def ticket(pid, day, status, reviews, weakest=None, dep="ASSUMED_INDEPENDENT"):
    return {"parlay_id": pid, "et_date": day, "result_status": status, "leg_reviews": reviews, "dependence_level": dep, "weakest_leg": {"label": weakest or reviews[0]["label"]}}


class TestBuildLessons(unittest.TestCase):
    def lessons(self, parlays, reviews=None, today="2026-10-25"):
        with mock.patch.object(lc, "_flagship_public", return_value=None):
            return lpm.build_lessons(None, parlays, reviews or {}, today)

    def test_a_role_or_ice_time_miss_becomes_a_named_caution_with_the_team(self):
        p = [ticket("A", "2026-10-24", "MISS", [lr("k1", "Martin Necas", "LOSS", "TOI_PROJECTION_ERROR", team="CAR"), lr("k2", "Nazem Kadri", "WIN")])]
        c = self.lessons(p)["cautions"]
        self.assertEqual([(x["player"], x["team"], x["classification"], x["date"]) for x in c], [("Martin Necas", "CAR", "TOI_PROJECTION_ERROR", "2026-10-24")])

    def test_a_failed_power_play_claim_is_a_pp_role_miss(self):
        p = [ticket("A", "2026-10-24", "MISS", [lr("k1", "Brock Nelson", "LOSS", "MODEL_CALIBRATION", pp="FAILED")])]
        self.assertEqual(self.lessons(p)["cautions"][0]["classification"], "PP_ROLE_MISS")

    def test_repeated_killer_legs_weak_legs_and_decision_outcomes_are_recorded(self):
        a, b = lr("k1", "Connor Bedard", "LOSS"), lr("k1", "Connor Bedard", "LOSS")
        p = [ticket("A", "2026-10-24", "MISS", [a, lr("k2", "Patrick Kane", "WIN", decision="GOOD_DECISION")], weakest=a["label"]),
             ticket("B", "2026-10-24", "MISS", [b, lr("k3", "Nazem Kadri", "LOSS", decision="GOOD_DECISION")], weakest=b["label"]),
             ticket("C", "2026-10-24", "HIT", [lr("k4", "Cale Makar", "WIN", decision="BAD_DECISION")], dep="UNCERTAIN")]
        d = self.lessons(p)["by_date"]["2026-10-24"]
        self.assertEqual(d["repeated_killer_legs"], ["Connor Bedard"])
        self.assertIn("Connor Bedard", d["weakest_legs_that_failed"])
        self.assertEqual(d["decisions"], {"good_decision_lost": 1, "bad_decision_won": 1})
        self.assertEqual(d["same_game_tickets"], {"n": 1, "hit": 1, "miss": 0})
        self.assertEqual((d["tickets"], d["hit"], d["miss"]), (3, 1, 2))

    def test_the_flagship_comes_from_the_one_existing_selection_not_a_second_path(self):
        with mock.patch.object(lc, "_flagship_public", return_value={"legs": [{"label": "A"}, {"label": "B"}], "result_status": "MISS"}) as f:
            out = lpm.build_lessons(None, [ticket("A", "2026-10-24", "MISS", [lr("k1", "X", "LOSS")])], {}, "2026-10-25")
        self.assertEqual(out["by_date"]["2026-10-24"]["flagship"], {"legs": ["A", "B"], "result": "MISS"})
        f.assert_called_once()

    def test_only_the_last_three_days_count_and_earlier_straight_bet_findings_are_kept_for_them(self):
        old = [ticket("O", "2026-10-20", "MISS", [lr("k0", "Old Guy", "LOSS", "TOI_PROJECTION_ERROR")])]
        reviews = {"b1": {"et_date": "2026-10-23", "assessment": {"classification": "ROLE_CHANGE_MISSED"}, "card": {"bet": {"player_or_team": "Noah Cates", "team": "PHI"}}},
                   "b2": {"et_date": "2026-10-19", "assessment": {"classification": "ROLE_CHANGE_MISSED"}, "card": {"bet": {"player_or_team": "Too Old", "team": "PHI"}}}}
        out = self.lessons(old, reviews)
        self.assertEqual([c["player"] for c in out["cautions"]], ["Noah Cates"])
        self.assertNotIn("2026-10-20", out["by_date"])

    def test_the_document_says_nothing_about_changing_any_model(self):
        out = self.lessons([])
        self.assertIn("no model parameter or threshold is changed", out["note"])


class TestTomorrowReadsIt(tro.Overlay):
    DOC = {"lessons": {"cautions": [{"player": "Test Player", "team": "AAA", "classification": "TOI_PROJECTION_ERROR", "date": "2026-10-19", "source": "PARLAY_LEG"},
                                    {"player": "Today Guy", "team": "AAA", "classification": "ROLE_CHANGE_MISSED", "date": "2026-10-21", "source": "PARLAY_LEG"},
                                    {"player": "Stale Guy", "team": "AAA", "classification": "ROLE_CHANGE_MISSED", "date": "2026-10-10", "source": "PARLAY_LEG"}]},
           "bet_reviews": {"x": {"et_date": "2026-10-19", "assessment": {"classification": "ROLE_CHANGE_MISSED"}, "card": {"bet": {"player_or_team": "Legacy Guy", "team": "AAA"}}}}}

    def test_the_review_loads_the_persisted_cautions_inside_the_window_and_before_today(self):
        ctx = self.ov_ctx()
        ctx.as_of = "2026-10-21"
        with mock.patch("operational.learning_postmortem.load", return_value=self.DOC):
            ei._load_overlay_inputs(ctx, None)
        self.assertEqual(set(ctx.role_cautions), {("test player", "AAA")})                    # not today's, not stale, and the old bet_reviews path is not used once lessons exist

    def test_a_document_from_before_the_lessons_block_still_works(self):
        ctx = self.ov_ctx()
        ctx.as_of = "2026-10-21"
        with mock.patch("operational.learning_postmortem.load", return_value={"bet_reviews": self.DOC["bet_reviews"]}):
            ei._load_overlay_inputs(ctx, None)
        self.assertEqual(set(ctx.role_cautions), {("legacy guy", "AAA")})

    def test_a_persisted_lesson_flags_the_player_in_today_s_review_even_with_no_team_on_it(self):
        ctx = self.ov_ctx(cautions={("test player", None): {"classification": "PP_ROLE_MISS", "date": "2026-10-19"}})
        c = self.run_review(ctx, toi=18.5)
        self.assertEqual(self.names(c)["POSTMORTEM_ROLE_CAUTION"], "MODERATE")
        self.assertIn("PP_ROLE_MISS", " ".join(c["case_against"]))
        self.assertEqual(c["probabilities"]["estimate_used"], self.run_review(self.ov_ctx(live=False), toi=18.5)["probabilities"]["estimate_used"])


class TestInTheRealPostmortem(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(lc, "UNIT", "PARLAY")
        p.start()
        self.addCleanup(p.stop)
        self.nhl.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("4", "20262027", "2026-10-20", "2026-10-20T23:00:00", "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
        self.nhl.commit()

    def test_the_built_document_carries_the_lessons_block(self):
        self.cycle([leg(pid=str(8470001 + i), game=g, p=0.70 - 0.02 * i, price=-130.0) for i, g in enumerate(("1", "2", "4", "1", "2", "4"))])
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        self.assertIn("lessons", doc)
        self.assertEqual(doc["lessons"]["window_days"], lpm.LESSON_DAYS)
        self.assertIn("2026-10-20", doc["lessons"]["by_date"])
        self.assertEqual(doc["lessons"]["by_date"]["2026-10-20"]["tickets"], len(doc["parlays"]))


class TestAutomaticChain(unittest.TestCase):
    def test_the_cohort_cycle_refreshes_the_postmortem_on_settlement_and_reads_it_for_the_next_review(self):
        import inspect
        run = inspect.getsource(lc.run_cycle)
        self.assertIn("_refresh_postmortem", run)
        self.assertIn("live_overlay=True", inspect.getsource(lc))
        self.assertIn("lpm.load()", inspect.getsource(ei._load_overlay_inputs).replace("lpm.load() or {}", "lpm.load()"))


if __name__ == "__main__":
    unittest.main()
