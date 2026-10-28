"""PERSONNEL (operational/personnel.py): attributable injury reports only, exact states, honest freshness, visible conflicts, transparent importance, one family in Eggy, no probability change, nothing fetched."""
from __future__ import annotations

import datetime as dt
import inspect
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from operational import eggy_pick as ep
from operational import eggy_pick_ledger as epl
from operational import personnel as pe
from tests.test_eggy_pick import cand

Z = dt.timezone.utc
NOW = dt.datetime(2026, 10, 28, 21, 20, tzinfo=Z)
AGO = lambda h: (NOW - dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rep(pid="1", name="Star Forward", team="DAL", pos="F", status="OUT", hours=2.0, source="NHL.com", authority="OFFICIAL", **kw):
    return {"type": "REPORT", "player_id": pid, "player_name": name, "team": team, "position": pos, "status": status, "designation": kw.get("designation"), "expected_return": kw.get("expected_return"),
            "source_name": source, "source_url": "https://example.com/x", "source_at_utc": AGO(hours), "authority": authority, "recorded_at_utc": AGO(hours - 0.1)}


def nhl_db():
    """Ten games for DAL: forwards f1..f12 with falling ice time, defencemen d1..d6, f1 the top scorer."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE players (player_id TEXT PRIMARY KEY, full_name TEXT, position TEXT)")
    db.execute("CREATE TABLE player_game_stats (game_id INTEGER, player_id TEXT, team_id TEXT, toi_minutes REAL, goals INTEGER, assists INTEGER, played INTEGER DEFAULT 1)")
    for i in range(1, 13):
        db.execute("INSERT INTO players VALUES (?,?,?)", (f"f{i}", f"Forward {i}", "F"))
    for i in range(1, 8):
        db.execute("INSERT INTO players VALUES (?,?,?)", (f"d{i}", f"Defender {i}", "D"))
    for pre, team in (("", "DAL"), ("w", "WPG")):
        for i in range(1, 13):
            db.execute("INSERT OR IGNORE INTO players VALUES (?,?,?)", (f"{pre}f{i}", f"{pre}Forward {i}", "F"))
        for i in range(1, 8):
            db.execute("INSERT OR IGNORE INTO players VALUES (?,?,?)", (f"{pre}d{i}", f"{pre}Defender {i}", "D"))
        for g in range(1, 11):
            for i in range(1, 13):
                db.execute("INSERT INTO player_game_stats VALUES (?,?,?,?,?,?,1)", (g, f"{pre}f{i}", team, 20.0 - i, 1 if i <= 2 else 0, 1 if i <= 3 else 0))
            for i in range(1, 8):
                db.execute("INSERT INTO player_game_stats VALUES (?,?,?,?,?,?,1)", (g, f"{pre}d{i}", team, 26.0 - i, 0, 0))
    return db


class TestAttributableOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"NHL_ENGINE_STATE_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def ok(self, **kw):
        a = dict(player_id="8478402", player_name="Star Forward", team="DAL", position="F", status="OUT", source_name="NHL.com", source_url="https://www.nhl.com/news/x", source_at_utc=AGO(1), now=NOW)
        a.update(kw)
        return pe.record_report(**a)

    def test_a_complete_report_is_stored_with_both_clocks(self):
        r = self.ok(designation="lower body", expected_return="week-to-week")
        self.assertEqual((r["source_at_utc"], r["recorded_at_utc"]), (AGO(1), "2026-10-28T21:20:00Z"))
        self.assertEqual(pe.read_reports(), [r])

    def test_anything_vague_unsourced_or_unsupported_is_refused_and_nothing_is_stored(self):
        for bad in ({"source_name": ""}, {"source_url": "n/a"}, {"source_at_utc": None}, {"source_at_utc": (NOW + dt.timedelta(hours=2)).isoformat()}, {"status": "HURT"}, {"team": "XXX"},
                    {"player_name": ""}, {"position": "G"}, {"authority": "RUMOUR"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.ok(**bad)
        self.assertEqual(pe.read_reports(), [])

    def test_every_supported_state_exists_and_unknown_is_the_absence_of_a_usable_report(self):
        self.assertEqual(set(pe.STATUSES), {"OUT", "IR", "DAY_TO_DAY", "QUESTIONABLE", "GAME_TIME_DECISION", "EXPECTED_TO_PLAY", "EXPECTED_TO_RETURN", "ACTIVE_CLEARED"})
        st = pe.resolve_all(None, NOW, [])
        self.assertEqual(st, {})
        self.assertEqual(pe.team_view(st, "DAL")["coverage"], "NONE")                          # no report is NOT health
        self.assertIn("NOT AVAILABLE", pe.describe(pe.team_view(st, "DAL")))

    def test_the_module_reads_no_website_writes_no_roster_table_and_cannot_open_other_gates(self):
        src = inspect.getsource(pe)
        for banned in ("import requests", "urlopen", "INSERT INTO", "UPDATE ", "DELETE FROM", "http.client", "get_conn().execute"):
            self.assertNotIn(banned, src, banned)


class TestFreshnessOrderingAndConflict(unittest.TestCase):
    def test_freshness_depends_on_the_kind_of_status(self):
        fr = lambda **kw: pe.freshness(rep(**kw), NOW)
        self.assertEqual(fr(status="IR", hours=120), pe.CURRENT)                                # long-term IR stays current for days
        self.assertEqual(fr(status="IR", hours=24 * 20), pe.RECENT)
        self.assertEqual(fr(status="IR", hours=24 * 30), pe.STALE)
        self.assertEqual(fr(status="OUT", hours=30), pe.CURRENT)
        self.assertEqual(fr(status="OUT", hours=60), pe.RECENT)
        self.assertEqual(fr(status="OUT", hours=80), pe.STALE)
        self.assertEqual(fr(status="OUT", hours=80, expected_return="out 6 weeks"), pe.CURRENT)  # a long-term OUT is current longer
        self.assertEqual(fr(status="GAME_TIME_DECISION", hours=4), pe.CURRENT)
        self.assertEqual(fr(status="GAME_TIME_DECISION", hours=13), pe.STALE)                   # a morning-skate doubt is not current late in the day
        self.assertEqual(fr(status="QUESTIONABLE", hours=20), pe.CURRENT)
        self.assertEqual(fr(status="QUESTIONABLE", hours=50), pe.STALE)
        self.assertEqual(fr(status="OUT", hours=-3), pe.STALE)                                  # a timestamp in the future is not trusted

    def test_a_stale_report_is_history_not_current_state(self):
        s = pe.resolve_player([rep(status="GAME_TIME_DECISION", hours=30)], NOW)
        self.assertEqual((s["current_status"], s["freshness"], s["class"]), ("UNKNOWN", pe.STALE, "UNKNOWN"))

    def test_the_newer_statement_from_the_same_source_replaces_the_older_and_keeps_the_previous_status(self):
        s = pe.resolve_player([rep(status="OUT", hours=20), rep(status="EXPECTED_TO_RETURN", hours=3)], NOW)
        self.assertEqual((s["current_status"], s["previous_status"], s["conflict"]), ("EXPECTED_TO_RETURN", "OUT", None))

    def test_different_sources_at_the_same_moment_go_to_the_more_authoritative_and_equals_are_a_conflict(self):
        s = pe.resolve_player([rep(status="OUT", hours=2, source="NHL.com", authority="OFFICIAL"), rep(status="EXPECTED_TO_PLAY", hours=2.05, source="Aggregator", authority="AGGREGATOR")], NOW)
        self.assertEqual((s["current_status"], s["conflict"]), ("OUT", None))
        s = pe.resolve_player([rep(status="OUT", hours=2, source="Reporter A", authority="REPORTER"), rep(status="EXPECTED_TO_PLAY", hours=2.05, source="Reporter B", authority="REPORTER")], NOW)
        self.assertIsNotNone(s["conflict"])
        self.assertIn("Reporter A", s["conflict"])

    def test_a_newer_but_less_authoritative_contradiction_is_a_conflict_not_a_quiet_override(self):
        s = pe.resolve_player([rep(status="OUT", hours=10, source="NHL.com", authority="OFFICIAL"), rep(status="EXPECTED_TO_PLAY", hours=2, source="Aggregator", authority="AGGREGATOR")], NOW)
        self.assertIsNotNone(s["conflict"])
        s = pe.resolve_player([rep(status="OUT", hours=10, source="Aggregator", authority="AGGREGATOR"), rep(status="EXPECTED_TO_PLAY", hours=2, source="NHL.com", authority="OFFICIAL")], NOW)
        self.assertEqual((s["current_status"], s["conflict"]), ("EXPECTED_TO_PLAY", None))


class TestImportance(unittest.TestCase):
    def test_importance_comes_from_ice_time_and_scoring_and_is_position_aware(self):
        db = nhl_db()
        imp = lambda pid, pos: pe.impact_of(db, pid, "DAL", pos)["impact"]
        self.assertEqual(imp("f1", "F"), pe.MATERIAL)
        self.assertEqual(imp("f6", "F"), pe.MATERIAL)                                           # top-six by ice time
        self.assertEqual(imp("f8", "F"), pe.MODERATE)
        self.assertEqual(imp("f12", "F"), pe.DEPTH)
        self.assertEqual(imp("d1", "D"), pe.MATERIAL)
        self.assertEqual(imp("d5", "D"), pe.MODERATE)
        self.assertEqual(imp("d7", "D"), pe.DEPTH)
        self.assertEqual(imp("nobody", "F")["impact"] if isinstance(imp("nobody", "F"), dict) else imp("nobody", "F"), pe.UNRATED)
        self.assertIn("power-play role not measured", pe.impact_of(db, "f1", "DAL", "F")["basis"])

    def test_no_engine_data_is_unrated_never_invented(self):
        self.assertEqual(pe.impact_of(None, "f1", "DAL", "F")["impact"], pe.UNRATED)

    def test_a_depth_player_is_never_decision_relevant_and_a_fresh_active_player_is_not_either(self):
        db = nhl_db()
        st = pe.resolve_all(db, NOW, [rep("f12", "Forward 12", status="OUT"), rep("f1", "Forward 1", status="ACTIVE_CLEARED", hours=1), rep("f2", "Forward 2", status="OUT")])
        v = pe.team_view(st, "DAL")
        self.assertEqual([p["name"] for p in v["relevant"]], ["Forward 2"])
        self.assertEqual(v["coverage"], "REPORTS_ONLY")                                         # reports but no sweep: silence is not health
        self.assertIn("no full sweep", pe.describe(pe.team_view(pe.resolve_all(db, NOW, [rep("f12", "Forward 12", status="OUT")]), "DAL")))

    def test_a_fresh_sweep_lets_silence_mean_no_material_absence_and_a_stale_one_does_not(self):
        db = nhl_db()
        sweep = {"type": "SWEEP", "team": "DAL", "source_name": "NHL.com", "source_url": "https://www.nhl.com/", "source_at_utc": AGO(3), "recorded_at_utc": AGO(3)}
        v = pe.team_view(pe.resolve_all(db, NOW, [sweep]), "DAL")
        self.assertEqual((v["coverage"], pe.describe(v)), ("SWEPT", "DAL - no material current absence identified"))
        old = {**sweep, "source_at_utc": AGO(30)}
        self.assertEqual(pe.team_view(pe.resolve_all(db, NOW, [old]), "DAL")["coverage"], "NONE")

    def test_a_material_return_and_the_new_today_flag(self):
        db = nhl_db()
        st = pe.resolve_all(db, NOW, [rep("f1", "Forward 1", status="OUT", hours=20), rep("f1", "Forward 1", status="EXPECTED_TO_RETURN", hours=2)])
        v = pe.team_view(st, "DAL")
        self.assertEqual([p["name"] for p in v["material_returns"]], ["Forward 1"])
        self.assertEqual(v["relevant"], [])                                                     # the absence concern is removed


def view(team, reports, db=None):
    return pe.team_view(pe.resolve_all(db or nhl_db(), NOW, reports), team)


def with_personnel(own_reports=(), opp_reports=(), **kw):
    c = cand(**kw)
    own = view("DAL", list(own_reports))
    opp = view("WPG", [{**r, "team": "WPG", "player_id": "w" + r["player_id"]} for r in opp_reports]) if opp_reports else pe.team_view({}, "WPG")
    c["personnel"] = {"side": own, "opponent": opp}
    return c


class TestEggyConsumesPersonnel(unittest.TestCase):
    def items(self, c):
        cs = ep.cases(c)
        return [(i["kind"], i["strength"], "for") for i in cs["case_for"] if i["family"] == "MATCHUP_PERSONNEL"] + [(i["kind"], i["strength"], "against") for i in cs["case_against"] if i["family"] == "MATCHUP_PERSONNEL"]

    def test_the_sides_own_material_absence_is_evidence_against_and_the_opponents_is_evidence_for(self):
        own = with_personnel([rep("f1", "Forward 1", status="OUT")])
        self.assertIn(("PERSONNEL_AVAILABILITY_AGAINST", "MODERATE", "against"), self.items(own))
        opp = with_personnel(opp_reports=[rep("f1", "Forward 1", status="OUT")])
        self.assertIn(("PERSONNEL_ABSENCE_OPPONENT", "MODERATE", "for"), self.items(opp))

    def test_several_material_absences_make_one_reason_stronger_not_several_reasons(self):
        c = with_personnel([rep("f1", "Forward 1", status="OUT"), rep("f2", "Forward 2", status="IR", hours=5), rep("d1", "Defender 1", status="OUT")])
        against = [i for i in self.items(c) if i[2] == "against" and i[0] == "PERSONNEL_AVAILABILITY_AGAINST"]
        self.assertEqual(against, [("PERSONNEL_AVAILABILITY_AGAINST", "STRONG", "against")])

    def test_the_same_underlying_absence_is_one_reason_however_many_reports_describe_it(self):
        c = with_personnel([rep("f1", "Forward 1", status="OUT", hours=30), rep("f1", "Forward 1", status="IR", hours=3, source="Team"), rep("f1", "Forward 1", status="OUT", hours=2, source="Reporter", authority="REPORTER")])
        self.assertEqual(len([i for i in self.items(c) if i[0].startswith("PERSONNEL_")]), 1)

    def test_a_depth_absence_is_not_shown_and_nothing_known_for_either_team_is_a_weak_data_note(self):
        d = with_personnel([rep("f12", "Forward 12", status="OUT")])
        self.assertEqual([i for i in self.items(d) if i[0].startswith("PERSONNEL_")], [])
        none = with_personnel()
        self.assertEqual([i for i in self.items(none) if i[0].startswith("PERSONNEL_")], [("PERSONNEL_UNKNOWN", "WEAK", "against")])

    def test_a_player_in_doubt_or_a_source_conflict_is_weak_uncertainty_and_costs_one_confidence_category(self):
        base = cand()
        keep = ep.confidence(base)
        for reports in ([rep("f1", "Forward 1", status="GAME_TIME_DECISION", hours=3)],
                        [rep("f1", "Forward 1", status="OUT", hours=2, source="Reporter A", authority="REPORTER"), rep("f1", "Forward 1", status="EXPECTED_TO_PLAY", hours=2.05, source="Reporter B", authority="REPORTER")]):
            c = with_personnel(reports)
            self.assertEqual([i[1] for i in self.items(c) if i[0] == "PERSONNEL_AVAILABILITY_AGAINST"], ["WEAK"])
            self.assertLessEqual(["LOW", "MEDIUM", "HIGH"].index(ep.confidence(c)), max(0, ["LOW", "MEDIUM", "HIGH"].index(keep) - 1))

    def test_goalie_and_injury_uncertainty_together_still_cost_one_category(self):
        goalie = {"state": "UNKNOWN_GOALIE", "sides": {}, "home": "DAL", "away": "WPG"}
        g_only = cand()
        g_only["goalies"] = goalie
        both = with_personnel([rep("f1", "Forward 1", status="GAME_TIME_DECISION", hours=3)])
        both["goalies"] = goalie
        self.assertEqual(ep.confidence(both), ep.confidence(g_only))

    def test_personnel_never_changes_a_probability_a_price_or_the_edge(self):
        plain = cand()
        c = with_personnel([rep("f1", "Forward 1", status="OUT")], [rep("f2", "Forward 2", status="OUT")])
        for k in ("model_probability", "supported_probability", "edge", "conservative_edge", "price", "market_probability_market"):
            self.assertEqual(plain[k], c[k], k)

    def test_no_wired_report_means_the_frozen_behaviour_is_untouched(self):
        c = cand()
        self.assertIsNone(c.get("personnel"))
        self.assertEqual(ep.personnel_items(c), ([], []))
        self.assertFalse(ep.personnel_uncertain(c))

    def test_personnel_alone_cannot_make_a_best_available_bet_an_edge_thesis(self):
        c = with_personnel(opp_reports=[rep("f1", "Forward 1", status="OUT"), rep("f2", "Forward 2", status="OUT")], model=0.50, mkt=0.50, trust="INSUFFICIENT_EVIDENCE", drivers=0)
        self.assertEqual(c["structure"], "NONE")
        self.assertEqual(ep.confidence(c), "LOW")


class TestDecisionTimeRecord(unittest.TestCase):
    def test_the_observation_stores_what_eggy_knew_and_the_signals_are_prospective_only(self):
        c = with_personnel([rep("f1", "Forward 1", status="OUT")], [rep("f1", "Forward 9", status="GAME_TIME_DECISION", hours=3)])
        o = {"type": "OBSERVATION", "date": "2026-10-28", "game_id": "1", "personnel": epl._personnel_record([c]), "goalie_state": "BOTH_CONFIRMED",
             "sides": [{"side": "DAL", "market": "MONEYLINE", "line": None, "picked": True, "qualifies": False, "market_p": 0.5, "price_class": "IN_PREFERRED_REGION", "confidence": "LOW", "structure": "NONE",
                        "edge_family_names": [], "gap": 0.0}]}
        rec = o["personnel"]["DAL"]
        self.assertEqual(rec["coverage"], "REPORTS_ONLY")
        self.assertEqual([p["name"] for p in rec["relevant"]], ["Forward 1"])
        self.assertEqual({k for k in rec["relevant"][0]}, set(epl._PERSON_FIELDS))
        self.assertIn("source_at_utc", rec["relevant"][0])
        self.assertIn("recorded_at_utc", rec["relevant"][0])
        o.update({"home": "DAL", "away": "WPG", "home_margin": 1})
        names = {n for n, _p, _y in epl.signal_bets(o)}
        self.assertTrue({"PICK_OWN_MATERIAL_ABSENCE", "PICK_MATERIAL_PLAYER_IN_DOUBT", "PICK_BEST_AVAILABLE_PERSONNEL_ISSUE"} <= names, names)
        self.assertNotIn("PICK_INJURY_COVERAGE_NONE", names)
        for name in ("PICK_OWN_MATERIAL_ABSENCE", "PICK_MATERIAL_PLAYER_IN_DOUBT", "PICK_INJURY_COVERAGE_NONE", "PICK_EDGE_THESIS_PERSONNEL_ISSUE", "PICK_BEST_AVAILABLE_CLEAN_PERSONNEL"):
            self.assertIn(name, epl.SIGNALS)

    def test_the_cycle_wires_personnel_before_ranking_and_a_disabled_goalie_feed_is_no_feed(self):
        src = inspect.getsource(epl.run_cycle)
        self.assertLess(src.index("pers.resolve_all"), src.rindex("ep.decide("))
        self.assertIn('gstate.get("status") == "DISABLED"', src)


if __name__ == "__main__":
    unittest.main()
