"""Losing-ticket post-mortem (operational/ticket_loss_classifier.py) and the recording-status line (operational/daily_tickets.recording_status): real-signal classification, honest UNKNOWN, integrity
defects recommended (never acted on), and the guarantee that a losing ticket, day or streak can never pause recording. Isolated: in-memory ledgers and temp official databases."""
from __future__ import annotations

import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import daily_postmortem as dpm
from operational import daily_tickets as dtk
from operational import paper_bankroll as pb
from operational import ticket_loss_classifier as tlc
from tests.product_fixture import snapshot
from tests.test_product_pages import run_page

REPO = Path(__file__).resolve().parent.parent


def official_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("EDM", "CHI", "MIN", "COL"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.commit()
    return conn


def game(conn, gid, date, home="EDM", away="CHI"):
    conn.execute("""INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state, home_score, away_score, final_period_type, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (gid, "20262027", date, home, away, "FINAL", 3, 2, "REG", "fixture"))


def pstat(conn, gid, pid, team, toi, shots=0, played=1):
    conn.execute("""INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source)
                    VALUES (?,?,?,?,?,?,?,?,1,'2026-10-10T23:00:00Z','2026-10-10T23:00:00Z','fixture')""", (gid, pid, team, toi, 0, 0, shots, played))


def tstat(conn, gid, team, sog):
    conn.execute("""INSERT INTO team_game_stats (game_id, team_id, sog, revision_number, effective_at_utc, observed_at_utc, source) VALUES (?,?,?,1,'2026-10-10T23:00:00Z','2026-10-10T23:00:00Z','fixture')""", (gid, team, sog))


def world(*, toi=20.0, team_sog=30.0):
    """Five prior games at 20 min and 30 team shots, then the game of the ticket (id 100, 2026-10-12) with the given ice time and team shots."""
    c = official_db()
    for i in range(5):
        gid, d = 90 + i, f"2026-10-0{i + 1}"
        game(c, gid, d)
        pstat(c, gid, "1001", "EDM", 20.0, 3)
        tstat(c, gid, "EDM", 30.0)
    game(c, 100, "2026-10-12")
    pstat(c, 100, "1001", "EDM", toi, 1)
    tstat(c, 100, "EDM", team_sog)
    c.commit()
    return c


def frozen(**kw):
    leg = {"participant_id": "1001", "participant_name": "Test Player", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "side": "OVER", "game_id": 100, "team": "EDM", "opponent": "CHI",
           "game_start_utc": "2026-10-12T23:00:00Z", "captured_at_utc": "2026-10-12T21:00:00+00:00", "quote_age_min_at_entry": 5.0, "freshness_status": "FRESH", "conservative_probability": 0.62, "american_price": -150}
    leg.update(kw)
    return leg


def ticket(tid="T1", **kw):
    f = frozen(**kw)
    missed = {"participant_id": f["participant_id"], "market_family": f["market_family"], "participant_name": f["participant_name"], "threshold": f["threshold"], "side": "OVER", "game_id": f["game_id"],
              "actual_value": 1, "resolver_status": "RESOLVED", "predicted_probability": 0.62, "recorded_price": -150, "model_version": "m"}
    return {"ticket_id": tid, "paper_bet_id": tid, "missed_legs": [missed], "hit_legs": [], "other_legs": [], "frozen_legs": [f], "why": "x", "stake": 10.0, "profit_loss": -10.0}


def classify(pm, conn):
    res = tlc.classify_all([pm], conn)
    return pm["classification"], res


class TestRealSignalClassification(unittest.TestCase):
    def test_a_clean_miss_is_normal_variance_with_no_action(self):
        cls, _ = classify(ticket(), world())
        self.assertEqual((cls["category"], cls["recommended_action"], cls["integrity_defect"]), (dpm.NORMAL_VARIANCE, dpm.NO_ACTION, False))
        self.assertEqual(set(cls["legs"][0]["evaluated"]), {"price_freshness_at_entry", "identity_against_box_score", "ice_time_vs_recent_average", "team_shots_vs_recent_average"})

    def test_a_collapse_in_ice_time_is_a_role_change_and_a_smaller_one_a_projection_error(self):
        self.assertEqual(classify(ticket(), world(toi=11.0))[0]["category"], dpm.ROLE_CHANGE_MISSED)                 # 55% of his recent average
        self.assertEqual(classify(ticket(), world(toi=15.0))[0]["category"], dpm.TOI_PROJECTION_ERROR)               # 75%

    def test_a_poor_team_shot_total_is_a_team_shot_environment_miss(self):
        self.assertEqual(classify(ticket(), world(team_sog=18.0))[0]["category"], dpm.TEAM_SHOT_ENVIRONMENT_ERROR)

    def test_a_ticket_recorded_on_a_stale_quote_is_a_confirmed_integrity_defect_and_a_bug_fix(self):
        cls, res = classify(ticket(quote_age_min_at_entry=400.0), world())
        self.assertEqual((cls["category"], cls["recommended_action"], cls["integrity_defect"]), (dpm.STALE_DATA, dpm.BUG_FIX, True))
        self.assertEqual(res["integrity_defects"][0]["recommended_action"], dpm.BUG_FIX)

    def test_a_player_the_box_score_puts_on_another_team_is_an_identity_defect(self):
        c = world()
        c.execute("UPDATE player_game_stats SET team_id = 'CHI' WHERE game_id = 100 AND player_id = '1001'")
        cls, _ = classify(ticket(), c)
        self.assertEqual((cls["category"], cls["integrity_defect"]), (dpm.IDENTITY_LINEUP_ERROR, True))

    def test_what_could_not_be_checked_is_unknown_never_a_guess(self):
        pm = ticket()
        for k in ("quote_age_min_at_entry", "captured_at_utc"):
            pm["frozen_legs"][0].pop(k)                              # an older ticket that did not freeze its quote age
        cls, _ = classify(pm, world())
        self.assertEqual(cls["category"], dpm.UNKNOWN)
        self.assertIn("price_freshness_at_entry", cls["legs"][0]["not_evaluated"])
        self.assertTrue(cls["legs"][0]["unknown_reason"])

    def test_a_team_market_leg_is_judged_on_its_price_alone(self):
        pm = ticket(participant_id=None, participant_name="COL", market_family="MONEYLINE", threshold=None)
        cls, _ = classify(pm, world())
        self.assertEqual(cls["category"], dpm.NORMAL_VARIANCE)

    def test_a_loss_with_no_gradable_missed_leg_is_unknown_and_says_so(self):
        pm = ticket()
        pm["missed_legs"] = []
        cls, _ = classify(pm, world())
        self.assertEqual((cls["category"], cls["legs"]), (dpm.UNKNOWN, []))


class TestPatternsAndActions(unittest.TestCase):
    def test_the_same_miss_on_three_tickets_is_one_event_not_a_pattern(self):
        c = world(toi=11.0)
        pms = [ticket(f"T{i}") for i in range(3)]
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["by_category"], {dpm.ROLE_CHANGE_MISSED: 1})
        self.assertTrue(all(p["classification"]["recommended_action"] == dpm.NO_ACTION for p in pms))             # one distinct event: below the WATCH bar

    def test_role_changes_measured_against_the_own_average_never_go_beyond_a_watch(self):
        """The ticket does not freeze the model's projection, so ice time against the player's own last-5 average fires on ordinary losses: it can justify a watch, never an investigation or a challenger."""
        c = world(toi=11.0)
        pms = [ticket("A")]
        for gid, pid in ((101, "1002"), (102, "1003"), (103, "1004"), (104, "1005")):
            game(c, gid, f"2026-10-1{gid - 100 + 2}")
            pstat(c, gid, pid, "EDM", 9.0)
            tstat(c, gid, "EDM", 30.0)
            for i in range(5):
                game(c, 200 + gid * 10 + i, f"2026-09-0{i + 1}")
                pstat(c, 200 + gid * 10 + i, pid, "EDM", 20.0)
            pms.append(ticket(f"T{gid}", participant_id=pid, game_id=gid, participant_name=pid))
        c.commit()
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["by_category"][dpm.ROLE_CHANGE_MISSED], 5)                                            # five distinct incidents
        self.assertEqual({p["classification"]["recommended_action"] for p in pms}, {dpm.WATCH})
        self.assertIn("own last-5-game average", " ".join(f["explanation"] for f in res["classified_failures"]))

    def test_a_repeated_integrity_defect_in_one_market_recommends_halting_that_market_and_still_acts_on_nothing(self):
        c = world()
        game(c, 101, "2026-10-13")
        pstat(c, 101, "1001", "EDM", 20.0)
        tstat(c, 101, "EDM", 30.0)
        c.commit()
        pms = [ticket("A", quote_age_min_at_entry=400.0), ticket("B", quote_age_min_at_entry=400.0, game_id=101, game_start_utc="2026-10-13T23:00:00Z", captured_at_utc="2026-10-13T21:00:00+00:00")]
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["integrity_defects"][0]["recommended_action"], dpm.HALT_MARKET)
        self.assertEqual(res["integrity_defects"][0]["distinct_incidents"], 2)
        self.assertEqual(pms[0]["classification"]["recommended_action"], dpm.HALT_MARKET)
        self.assertIn("recommendation", res["policy_note"].lower())

    def test_a_losing_streak_of_normal_variance_never_produces_a_halt_or_any_action(self):
        c = world()
        pms = []
        for i in range(8):
            gid = 300 + i
            game(c, gid, f"2026-10-{12 + (i % 3)}")
            pstat(c, gid, "1001", "EDM", 20.0, 1)
            tstat(c, gid, "EDM", 30.0)
            pms.append(ticket(f"L{i}", game_id=gid))
        c.commit()
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["by_action"][dpm.HALT_MARKET], 0)
        self.assertEqual(res["by_action"][dpm.NO_ACTION], 8)
        self.assertEqual(res["integrity_defects"], [])


class TestARecordingPauseCanNeverComeFromPerformance(unittest.TestCase):
    def test_no_module_pauses_recording_except_the_owner_command(self):
        offenders = []
        for f in (REPO / "operational").glob("*.py"):
            if f.name == "recording_pause.py":
                continue
            src = f.read_text()
            if re.search(r"recording_pause\.pause\(|from operational\.recording_pause import pause|recording_pause import .*\bpause\b", src):
                offenders.append(f.name)
        for f in list((REPO / "deploy").glob("*.py")) + list((REPO / "dashboard").rglob("*.py")):
            if re.search(r"recording_pause\.pause\(", f.read_text()):
                offenders.append(str(f.relative_to(REPO)))
        self.assertEqual(offenders, [], "something can pause recording by itself")

    def test_the_postmortem_and_its_classifier_do_not_touch_the_pause_at_all(self):
        for mod in (tlc, dpm):
            src = Path(mod.__file__).read_text()
            imports = "\n".join(l for l in src.splitlines() if re.match(r"\s*(from|import)\s", l))
            self.assertNotIn("recording_pause", imports, mod.__name__)
            self.assertNotIn("ticket_policy", imports, mod.__name__)

    def test_a_whole_losing_day_through_the_daily_postmortem_leaves_the_pause_state_alone(self):
        c = world()
        conn = pb.init_db(db_path=":memory:")
        pms = [ticket(f"D{i}") for i in range(4)]
        with mock.patch.object(dpm, "real_market_parlay_loss_postmortems", return_value=pms), mock.patch("operational.recording_pause.pause") as pause, \
                mock.patch("operational.recording_pause.resume") as resume:
            report = dpm.run_daily_postmortem(conn, nhl_conn=c)
        pause.assert_not_called()
        resume.assert_not_called()
        self.assertEqual(report["real_market_ticket_classification"]["tickets_classified"], 4)
        self.assertIn("never pauses recording", report["recording_policy_note"])
        self.assertTrue(all(p["classification"]["category"] for p in report["real_market_parlay_postmortems"]))

    def test_the_markdown_report_lists_each_losing_ticket_with_its_category_and_action(self):
        c = world(toi=11.0)
        conn = pb.init_db(db_path=":memory:")
        pms = [ticket("R1")]
        with mock.patch.object(dpm, "real_market_parlay_loss_postmortems", return_value=pms):
            report = dpm.run_daily_postmortem(conn, nhl_conn=c)
        with tempfile.TemporaryDirectory() as d:
            text = dpm.write_report_markdown(report, out_dir=Path(d)).read_text()
        self.assertIn("## Losing tickets: why", text)
        self.assertIn("R1: ROLE_CHANGE_MISSED", text)
        self.assertIn("never pauses recording", text)


class TestRecordingStatus(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 20, 19, 40, tzinfo=dt.timezone.utc)          # 15:40 ET
    ACCOUNT = {"available_cash": 500.0, "equity": 500.0, "open_stakes": 0.0}

    def status(self, rows=(), paused=False):
        pause = {"paused": paused, "since_utc": "2026-10-13T15:35:57Z" if paused else None, "reason": "r" if paused else None, "set_by": "owner" if paused else None}
        with mock.patch("operational.recording_pause.status", return_value=pause):
            return dtk.recording_status(self.NOW, list(rows), self.ACCOUNT)

    def test_active_status_carries_bankroll_counts_stake_and_the_next_daily_jobs(self):
        s = self.status([{"result_status": "PENDING"}, {"result_status": "WIN"}, {"result_status": "UNRESOLVED"}])
        self.assertEqual((s["active"], s["label"]), (True, "AUTOMATIC PAPER RECORDING: ACTIVE"))
        self.assertEqual((s["tickets_recorded_today"], s["pending_today"], s["settled_today"]), (3, 2, 1))
        self.assertEqual((s["bankroll"]["available_cash"], s["stake_usd"], s["daily_slots"]), (500.0, 10.0, dtk.slot_count_for_display()))
        self.assertEqual((s["next_settlement"]["label"], s["next_postmortem"]["label"]), ("Wed 7:15 AM ET", "Wed 7:30 AM ET"))
        self.assertIsNone(s["pause"])

    def test_before_seven_fifteen_the_next_settlement_is_today(self):
        s = None
        with mock.patch("operational.recording_pause.status", return_value={"paused": False}):
            s = dtk.recording_status(dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.timezone.utc), [], self.ACCOUNT)       # 06:00 ET
        self.assertEqual(s["next_settlement"]["label"], "Tue 7:15 AM ET")

    def test_paused_status_says_paused_and_carries_the_pause(self):
        s = self.status(paused=True)
        self.assertEqual((s["active"], s["label"]), (False, "AUTOMATIC PAPER RECORDING: PAUSED"))
        self.assertTrue(s["pause"]["paused"])


class TestTheBanner(unittest.TestCase):
    def page(self, rec, file="21_Today.py"):
        snap = snapshot()
        if file == "21_Today.py":
            snap["tickets"]["recording"] = rec
        else:
            from tests.test_slate_board import doc
            snap["slate_board"] = doc()
            snap["slate_board"]["recording"] = rec
        at = run_page(file, snap)
        self.assertFalse(at.exception, at.exception)
        return " ".join(m.value for m in at.markdown)

    def rec(self, active=True):
        return TestRecordingStatus().status(paused=not active)

    def test_the_active_banner_replaces_the_paused_notice_on_today_and_the_slate_board(self):
        for f in ("21_Today.py", "41_Slate_Board.py"):
            blob = self.page(self.rec(), f)
            self.assertIn("AUTOMATIC PAPER RECORDING: ACTIVE", blob, f)
            self.assertIn("paper bankroll", blob)
            self.assertIn("next daily settlement", blob)
            self.assertNotIn("PAUSED", blob)

    def test_no_active_banner_while_paused(self):
        blob = self.page(self.rec(active=False))
        self.assertNotIn("AUTOMATIC PAPER RECORDING: ACTIVE", blob)


class TestReviewFixes(unittest.TestCase):
    def test_one_incident_on_several_thresholds_is_one_event_so_a_single_stale_quote_cannot_recommend_a_halt(self):
        c = world()
        pms = [ticket("A", quote_age_min_at_entry=400.0, threshold=2), ticket("B", quote_age_min_at_entry=400.0, threshold=3)]
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["integrity_defects"][0]["distinct_incidents"], 1)
        self.assertEqual({p["classification"]["recommended_action"] for p in pms}, {dpm.BUG_FIX})                  # fix it, but one incident is not a repeat

    def test_distinct_dates_come_from_the_games_not_the_game_ids(self):
        c = world(toi=11.0)
        game(c, 101, "2026-10-12")                                                                                 # a second game the same night
        pstat(c, 101, "1002", "EDM", 9.0)
        tstat(c, 101, "EDM", 30.0)
        for i in range(5):
            game(c, 500 + i, f"2026-09-0{i + 1}")
            pstat(c, 500 + i, "1002", "EDM", 20.0)
        c.commit()
        res = tlc.classify_all([ticket("A"), ticket("B", participant_id="1002", game_id=101, participant_name="Two")], c)
        f = next(x for x in res["classified_failures"] if x["category"] == dpm.ROLE_CHANGE_MISSED)
        self.assertEqual((f["occurrences"], f["unique_game_dates"]), (2, 1))

    def test_an_old_ticket_is_classified_but_neither_counts_toward_a_pattern_nor_carries_an_action(self):
        c = world(toi=11.0)
        old, recent = ticket("OLD"), ticket("NEW", game_id=100)
        old["settled_at_utc"] = "2026-08-01T12:00:00+00:00"
        recent["settled_at_utc"] = "2026-10-12T12:00:00+00:00"
        res = tlc.classify_all([old, recent], c, now=dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc))
        self.assertEqual(old["classification"]["category"], dpm.ROLE_CHANGE_MISSED)
        self.assertEqual(old["classification"]["recommended_action"], dpm.NO_ACTION)
        self.assertTrue(old["classification"]["legs"][0]["outside_window"])
        self.assertEqual(res["by_category"], {dpm.ROLE_CHANGE_MISSED: 1})                                         # only the recent one counts

    def test_two_integrity_events_months_apart_are_not_a_repeat(self):
        c = world()
        game(c, 101, "2026-10-13")
        pstat(c, 101, "1001", "EDM", 20.0)
        tstat(c, 101, "EDM", 30.0)
        c.commit()
        a, b = ticket("A", quote_age_min_at_entry=400.0), ticket("B", quote_age_min_at_entry=400.0, game_id=101, game_start_utc="2026-10-13T23:00:00Z", captured_at_utc="2026-10-13T21:00:00+00:00")
        a["settled_at_utc"], b["settled_at_utc"] = "2026-06-01T12:00:00+00:00", "2026-10-14T12:00:00+00:00"
        res = tlc.classify_all([a, b], c, now=dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc))
        self.assertEqual(res["integrity_defects"][0]["distinct_incidents"], 1)
        self.assertEqual(res["integrity_defects"][0]["recommended_action"], dpm.BUG_FIX)

    def test_a_player_with_too_little_history_is_unknown_not_normal_variance(self):
        c = official_db()
        game(c, 100, "2026-10-12")
        pstat(c, 100, "1001", "EDM", 20.0)
        tstat(c, 100, "EDM", 30.0)
        c.commit()                                                                                                  # no prior games: ice time cannot be compared
        cls, _ = classify(ticket(), c)
        self.assertEqual(cls["category"], dpm.UNKNOWN)
        self.assertIn("ice_time_vs_recent_average", cls["legs"][0]["not_evaluated"])

    def test_goalie_legs_are_not_concluded_on(self):
        cls, _ = classify(ticket(market_family="GOALIE_SAVES", threshold=25), world())
        self.assertEqual(cls["category"], dpm.UNKNOWN)
        self.assertIn("starter_and_workload", cls["legs"][0]["not_evaluated"])

    def test_a_missing_team_id_is_not_a_checked_identity_and_never_a_mismatch(self):
        c = world()
        c.execute("UPDATE player_game_stats SET team_id = NULL WHERE game_id = 100 AND player_id = '1001'")
        cls, _ = classify(ticket(), c)
        self.assertNotEqual(cls["category"], dpm.IDENTITY_LINEUP_ERROR)
        self.assertEqual(cls["category"], dpm.UNKNOWN)

    def test_a_non_fresh_status_with_an_in_limit_age_is_still_stale_data(self):
        cls, _ = classify(ticket(freshness_status="STALE_QUOTE", quote_age_min_at_entry=5.0), world())
        self.assertEqual((cls["category"], cls["integrity_defect"]), (dpm.STALE_DATA, True))

    def test_a_ticket_with_one_clean_and_one_unknown_leg_shows_unknown(self):
        pm = ticket("M")
        extra = dict(pm["missed_legs"][0], participant_id="9999", participant_name="No Box Score")
        pm["missed_legs"].append(extra)
        pm["frozen_legs"].append(frozen(participant_id="9999", participant_name="No Box Score"))
        classify(pm, world())
        self.assertEqual(pm["classification"]["category"], dpm.UNKNOWN)

    def test_a_confirmed_integrity_defect_reaches_the_report_lists_the_morning_review_reads(self):
        c = world()
        conn = pb.init_db(db_path=":memory:")
        with mock.patch.object(dpm, "real_market_parlay_loss_postmortems", return_value=[ticket("S", quote_age_min_at_entry=400.0)]):
            report = dpm.run_daily_postmortem(conn, nhl_conn=c)
        self.assertEqual([i["category"] for i in report["software_bug_candidates"]], [dpm.STALE_DATA])
        self.assertEqual(report["software_bug_candidates"][0]["recommended_action"], dpm.BUG_FIX)
        self.assertEqual([i["category"] for i in report["investigate"]], [dpm.STALE_DATA])
        self.assertTrue(report["normal_variance_vs_systematic"].startswith("SYSTEMATIC") or "STALE_DATA" in report["normal_variance_vs_systematic"])
        self.assertNotIn("frozen_legs", report["real_market_parlay_postmortems"][0])                                 # not published

    def test_a_failure_mid_pass_leaves_no_partial_classification(self):
        c = world()
        pms = [ticket("A"), ticket("B")]
        real = tlc.leg_diagnostics
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return real(*a, **k)
        with mock.patch.object(tlc, "leg_diagnostics", flaky):
            with self.assertRaises(RuntimeError):
                tlc.classify_all(pms, c)
        self.assertTrue(all("classification" not in p for p in pms))

    def test_a_losing_streak_of_anomalous_misses_on_one_player_never_halts_or_pauses(self):
        c = world(toi=11.0)
        pms = [ticket(f"S{i}", threshold=2 + (i % 2), market_family="PLAYER_SOG_ALTERNATE" if i % 3 else "PLAYER_POINTS") for i in range(9)]
        res = tlc.classify_all(pms, c)
        self.assertEqual(res["by_action"][dpm.HALT_MARKET], 0)
        self.assertLessEqual({p["classification"]["recommended_action"] for p in pms}, {dpm.NO_ACTION, dpm.WATCH})


class TestStatusIsTruthful(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 20, 19, 40, tzinfo=dt.timezone.utc)

    def status(self, cash=500.0, describe_fails=False):
        pause = {"paused": False, "since_utc": None, "reason": None, "set_by": None}
        with mock.patch("operational.recording_pause.status", return_value=pause), \
             mock.patch("operational.ticket_policy.describe", side_effect=RuntimeError("no policy") if describe_fails else None, return_value={"policy_id": "p", "digest": "d", "approved": True}):
            return dtk.recording_status(self.NOW, [], {"available_cash": cash, "equity": cash, "open_stakes": 0.0})

    def test_it_never_says_active_when_the_policy_cannot_load_or_cash_is_below_the_stake(self):
        s = self.status(describe_fails=True)
        self.assertEqual((s["active"], s["label"]), (False, "AUTOMATIC PAPER RECORDING: BLOCKED"))
        self.assertIn("policy", s["blocked_reason"])
        s2 = self.status(cash=4.0)
        self.assertEqual((s2["active"], s2["label"]), (False, "AUTOMATIC PAPER RECORDING: BLOCKED"))
        self.assertIn("below the $10 stake", s2["blocked_reason"])
        self.assertTrue(self.status()["active"])

    def test_the_banner_shows_a_blocked_state_as_a_warning_not_as_active(self):
        snap = snapshot()
        snap["tickets"]["recording"] = self.status(cash=4.0)
        at = run_page("21_Today.py", snap)
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("AUTOMATIC PAPER RECORDING: BLOCKED", blob)
        self.assertNotIn("RECORDING: ACTIVE", blob)

    def test_the_paused_state_shows_the_pause_notice_not_the_active_banner(self):
        snap = snapshot()
        snap["tickets"]["recording"] = TestRecordingStatus().status(paused=True)
        snap["tickets"]["notice"] = "Automatic paper tickets are PAUSED since 2026-10-13: test notice"
        at = run_page("21_Today.py", snap)
        blob = " ".join(m.value for m in at.markdown)
        self.assertIn("PAUSED since 2026-10-13", blob)
        self.assertNotIn("RECORDING: ACTIVE", blob)


class TestNothingCanPauseRecordingByItself(unittest.TestCase):
    def test_an_ast_scan_of_every_non_test_module_finds_no_automatic_pause(self):
        import ast
        offenders = []
        allowed = {"operational/recording_pause.py", "deploy/reset_model_bankroll.py"}
        for f in REPO.rglob("*.py"):
            rel = str(f.relative_to(REPO))
            if rel.startswith(("tests/", ".git/", "venv/", ".venv/")) or "site-packages" in rel or rel in allowed:
                continue
            try:
                tree = ast.parse(f.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            names, funcs = set(), set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names |= {(a.asname or a.name.split(".")[-1]) for a in node.names if a.name.endswith("recording_pause")}
                elif isinstance(node, ast.ImportFrom):
                    if (node.module or "").endswith("recording_pause"):
                        funcs |= {(a.asname or a.name) for a in node.names if a.name == "pause"}
                    names |= {(a.asname or a.name) for a in node.names if a.name == "recording_pause"}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Attribute) and fn.attr == "pause" and isinstance(fn.value, ast.Name) and fn.value.id in names:
                        offenders.append(rel)
                    if isinstance(fn, ast.Name) and fn.id in funcs:
                        offenders.append(rel)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and "recording_pause.json" in node.value and rel != "operational/recording_pause.py":
                    offenders.append(f"{rel} (writes or names the pause file)")
        self.assertEqual(sorted(set(offenders)), [], "something can pause recording by itself")


if __name__ == "__main__":
    unittest.main()
