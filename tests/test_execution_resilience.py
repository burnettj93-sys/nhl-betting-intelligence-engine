"""EXECUTION RESILIENCE (2026-10-31): Eggy's recorded pick must reach the hosted page and a missing pick must be a loud production failure; neither changes a betting rule.

Oct 31: Eggy decided COL ML pregame (15:43Z, 77 minutes before the first game) but the hosted snapshot was rejected from that moment because the decision card carried the news credit
'Associated Press (via Yahoo Sports)' and the publisher's structural Yahoo tripwire refuses any value that names the provider; the rejection message then re-entered the snapshot through the job-health detail,
so every later publication failed too."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from operational import cloud_snapshot_schema as schema
from operational import eggy_pick_ledger as epl
from operational import learning_cohort as lc
from tests.test_eggy_pick import Z, game_db

CREDIT = "CGY is missing Samuel Honzek (F, OUT; Associated Press (via Yahoo Sports), 10-30 04:00Z): evidence for COL"
URL = "https://sports.yahoo.com/articles/flames-look-extend-home-win-081020622.html"


class TestNewsCreditDoesNotBlockPublication(unittest.TestCase):
    def test_the_wire_credit_is_published_without_the_excluded_provider_name(self):
        out = schema.redact_news_credit({"text": CREDIT, "source": "Associated Press (via Yahoo Sports)", "source_url": URL, "n": [CREDIT]})
        self.assertNotIn("yahoo", json.dumps(out).lower())
        self.assertIn("Associated Press", out["source"])
        self.assertEqual(out["source"], "Associated Press")
        self.assertEqual(out["source_url"], "[link withheld]")

    def test_the_validator_message_that_names_the_provider_cannot_poison_the_next_snapshot(self):
        msg = "VALIDATION: Yahoo content is structurally excluded (value at /eggy_pick/today/pick/case_for[5]/text)"
        self.assertNotIn("yahoo", schema.redact_news_credit(msg).lower())

    def test_keys_are_never_rewritten_and_the_ledger_copy_is_untouched(self):
        original = {"yahoo_thing": CREDIT}
        out = schema.redact_news_credit(original)
        self.assertIn("yahoo_thing", out)                                  # the key tripwire still fires in validate()
        self.assertIn("Yahoo", original["yahoo_thing"])                    # a copy: the stored record keeps the true credit

    def test_the_structural_value_tripwire_still_refuses_other_surfaces(self):
        self.assertTrue(schema._YAHOO_RE.search("a Yahoo fantasy roster"))
        import inspect
        src = inspect.getsource(schema.validate_snapshot)
        self.assertIn("Yahoo content is structurally excluded (value at", src)

    def test_the_eggy_section_and_job_health_use_the_redaction(self):
        import inspect
        from operational import cloud_snapshot_builder as b
        self.assertIn("redact_news_credit", inspect.getsource(epl.snapshot_section))
        self.assertIn("redact_news_credit", inspect.getsource(b._data_status))
        self.assertIn("redact_news_credit", inspect.getsource(b._health))


class TestTheDeadlineGuard(unittest.TestCase):
    TODAY = "2026-10-26"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.dict("os.environ", {"NHL_ENGINE_STATE_DIR": self.dir.name})
        p.start()
        self.addCleanup(p.stop)
        self.db = game_db()
        self.first = dt.datetime(2026, 10, 26, 23, 0, tzinfo=Z)

    def slate(self):
        self.db.execute("INSERT INTO games VALUES (1, ?, '2026-10-26T23:00:00Z', 'DAL', 'WPG', 'SCHEDULED', NULL, NULL, NULL)", (self.TODAY,))

    def guard(self, now, events=None, caps=()):
        with mock.patch.object(epl.ep, "league_captures", return_value=list(caps)):
            return epl.deadline_guard(self.db, now, events=[] if events is None else events)

    def decision(self, at="2026-10-26T21:40:00Z", kind="PICK"):
        return {"type": "DECISION", "date": self.TODAY, "decision_doc": {"decision": kind, "decided_at_utc": at, "pass_reason": "x"}}

    def test_no_games_is_no_failure(self):
        self.assertEqual(self.guard(dt.datetime(2026, 10, 26, 22, 45, tzinfo=Z))["state"], "NO_SLATE")

    def test_before_the_deadline_it_is_waiting_and_after_it_a_missing_pick_is_the_named_production_failure(self):
        self.slate()
        self.assertEqual(self.guard(dt.datetime(2026, 10, 26, 22, 29, tzinfo=Z))["state"], "WAITING")
        g = self.guard(dt.datetime(2026, 10, 26, 22, 31, tzinfo=Z), caps=[{"at": dt.datetime(2026, 10, 26, 12, 0, tzinfo=Z), "events": {}}])
        self.assertEqual((g["state"], g["headline"]), ("MISSING_AT_DEADLINE", "EGGY PICK NOT RECORDED BY DEADLINE"))
        for k in ("date", "slate_games", "first_game_utc", "decision_deadline_utc", "latest_side_price_capture_utc", "latest_trader_run_utc", "reason"):
            self.assertIn(k, g)
        self.assertEqual(g["decision_deadline_utc"], "2026-10-26T22:30:00Z")
        self.assertIn("older than the pregame target", g["reason"])

    def test_a_recorded_decision_is_recorded_and_one_the_page_never_received_is_flagged(self):
        self.slate()
        now = dt.datetime(2026, 10, 26, 22, 40, tzinfo=Z)
        self.assertEqual(self.guard(now, events=[self.decision()])["state"], "RECORDED")
        with mock.patch("operational.ingestion_health.load_health", return_value={"cloud_snapshot_publish": {"last_success_utc": "2026-10-26T12:00:00+00:00"}}):
            g = self.guard(now, events=[self.decision()])
        self.assertEqual(g["state"], "RECORDED_NOT_PUBLISHED")
        with mock.patch("operational.ingestion_health.load_health", return_value={"cloud_snapshot_publish": {"last_success_utc": "2026-10-26T21:50:00+00:00"}}):
            self.assertEqual(self.guard(now, events=[self.decision()])["state"], "RECORDED")

    def test_the_guard_notifies_once_per_failing_state(self):
        self.slate()
        now = dt.datetime(2026, 10, 26, 22, 40, tzinfo=Z)
        sent = []
        with mock.patch("operational.notify.send", side_effect=lambda *a, **k: sent.append(a) or True), mock.patch.object(epl.ep, "league_captures", return_value=[]):
            first = epl.run_guard(self.db, now)
            second = epl.run_guard(self.db, now + dt.timedelta(minutes=10))
        self.assertEqual(first["state"], "MISSING_AT_DEADLINE")
        self.assertEqual(len(sent), 1)
        self.assertTrue(second["notified"])

    def test_a_pick_is_not_blocked_by_an_unusable_intelligence_layer_after_the_last_chance_it_is_an_honest_recorded_pass(self):
        self.slate()
        from operational import eggy_intelligence as ei
        from operational import learning_cohort as lc
        cap = {"at": dt.datetime(2026, 10, 26, 21, 35, tzinfo=Z), "events": {}}
        with mock.patch.object(epl.ep, "league_captures", return_value=[cap]), mock.patch.object(ei, "build_context", return_value=object()), mock.patch.object(ei, "usable", return_value=(False, "no team ratings")), \
                mock.patch.object(lc, "_capture_context", return_value={"by_game": {"1": self.first}, "first_start": self.first, "funded": False}):
            before = epl.run_cycle(self.db, dt.datetime(2026, 10, 26, 22, 20, tzinfo=Z))
            self.assertEqual(before["status"], "INTELLIGENCE_UNAVAILABLE")                      # still time: wait
            after = epl.run_cycle(self.db, dt.datetime(2026, 10, 26, 22, 50, tzinfo=Z))
        self.assertEqual(after["status"], "PASS_TECHNICAL_AT_DEADLINE")
        d = [e for e in epl.read() if e["type"] == "DECISION"][0]["decision_doc"]
        self.assertEqual((d["decision"], d["pass_kind"]), ("PASS", "TECHNICAL_OR_DATA_FAILURE"))


class TestTheEggyStepRunsBeforeTheHeavyParlayWork(unittest.TestCase):
    def test_order_in_the_trader_cycle(self):
        import inspect
        from operational import real_parlay_paper_trader as t
        src = inspect.getsource(t)
        self.assertLess(src.index("eggy_pick_ledger.run_cycle"), src.index("daily_tickets.collect_candidate_legs(nhl_conn, now)"))


class TestHeldTicketsSurviveANarrowMissedWindow(unittest.TestCase):
    """The parlay engine: a ticket with a lesser leg waited for its first game's last 25 minutes, and EVERY recorded ticket to date (75 of 75) was recorded there, so one sleeping host in that window lost a whole day (Oct 30).
    The wait exists so that 'no better price or candidate for THIS game can still arrive'. Where the capture schedule has no newer capture due for any leg, that is already true and the ticket records."""
    pass


def _build():
    from tests.test_learning_parlays import TestTheCohortRecordsParlaysNotStraightBets as T
    from tests.test_learning_cohort import Base, D1, leg                 # noqa: F401

    class Held(T):
        GAME_START = dt.datetime(2026, 10, 20, 23, 0, tzinfo=Z)

        def lesser(self):
            for pid in range(8470001, 8470007):
                self.cards[str(pid)] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}

        def legs3(self):
            return self.legs()

        def at(self, minutes_before_drop, quote_age_min=9, legs=None):
            now = self.GAME_START - dt.timedelta(minutes=minutes_before_drop)
            legs = legs or self.legs()
            stamp = lambda m: (now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
            legs = [dataclasses.replace(l, quote_updated_utc=stamp(quote_age_min + 1), retrieved_at_utc=stamp(quote_age_min), captured_at_utc=stamp(quote_age_min)) for l in legs]
            return lc.run_cycle(self.nhl, now, {"legs": legs, "sources": {}}, conn=self.conn)

        def test_A_an_awake_host_records_a_settled_ticket_early_and_the_old_wait_would_not_have(self):
            self.lesser()
            out = self.at(75)                                              # 21:45Z: the wave capture (target 21:30Z) is in, no newer capture is due for any leg
            self.assertTrue(self.parlays())
            sf = out["recording"]["shortfall"]
            self.assertGreater(sf["parlays_recorded_before_the_final_window_no_newer_price_due"], 0)
            self.assertEqual(sf["parlays_waiting_for_their_final_window"], 0)

        def test_F_the_same_tickets_record_at_the_final_window_as_before_when_nothing_is_settled_early(self):
            self.lesser()
            self.at(75, quote_age_min=75)                                  # the price is from BEFORE the wave's capture: a capture is still due, so the ticket waits as before
            self.assertEqual(self.parlays(), [])
            self.at(20, quote_age_min=9)
            self.assertTrue(self.parlays())

        def test_a_ticket_recorded_early_is_the_ticket_the_final_window_would_have_recorded(self):
            self.lesser()
            self.at(75)
            early = {r["idempotency_key"] for r in self.parlays()}
            self.assertTrue(early)
            self.at(20)                                                    # later cycles add nothing different: the ledger is idempotent
            self.assertTrue(early <= {r["idempotency_key"] for r in self.parlays()})

        def test_B_a_host_that_wakes_inside_the_last_minutes_still_records_a_fresh_ticket(self):
            self.lesser()
            self.at(8, quote_age_min=9)
            self.assertTrue(self.parlays())

        def test_C_a_host_that_wakes_after_puck_drop_records_nothing(self):
            self.lesser()
            out = self.at(-5)
            self.assertEqual(self.parlays(), [])

        def test_C2_even_a_leg_wrongly_flagged_not_started_cannot_be_recorded_after_puck_drop(self):
            self.lesser()
            now = self.GAME_START + dt.timedelta(minutes=5)
            stamp = lambda m: (now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
            legs = [dataclasses.replace(l, quote_updated_utc=stamp(10), retrieved_at_utc=stamp(9), captured_at_utc=stamp(9), event_not_started=True) for l in self.legs()]
            out = lc.run_cycle(self.nhl, now, {"legs": legs, "sources": {}}, conn=self.conn)
            self.assertEqual(self.parlays(), [])

        def test_D_a_held_price_that_went_stale_is_not_recorded_without_a_newer_capture(self):
            self.lesser()
            self.at(8, quote_age_min=125)                                  # the only price is from the morning: a newer capture was due at 21:30Z and does not exist
            self.assertEqual(self.parlays(), [])

        def test_the_predicate_needs_a_known_schedule_and_fresh_prices(self):
            c = {"card": {"market_data_quality": {"status": "FRESH", "newer_capture_due_at_utc": None}}, "leg": SimpleNamespace(game_id="1")}
            self.assertTrue(lc._no_newer_price_due([c], {"by_game": {"1": 1}}))
            self.assertFalse(lc._no_newer_price_due([c], {"by_game": {}}))                                         # schedule unknown
            self.assertFalse(lc._no_newer_price_due([{**c, "card": {"market_data_quality": {"status": "STALE", "newer_capture_due_at_utc": None}}}], {"by_game": {"1": 1}}))
            self.assertFalse(lc._no_newer_price_due([{**c, "card": {"market_data_quality": {"status": "FRESH", "newer_capture_due_at_utc": "2026-10-20T21:30:00Z"}}}], {"by_game": {"1": 1}}))

    return Held


Held = _build()


if __name__ == "__main__":
    unittest.main()
