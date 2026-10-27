"""Eggy's OFFICIAL daily output (operational/eggy_candidates.py OFFICIAL_POLICY): at most one flagship parlay, taken from the learning pool's existing flagship result, or PASS; never a combined price."""
from __future__ import annotations

import datetime as dt
import inspect
import unittest

from operational import eggy_candidates as ec
from operational import slate_board

NOW = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.timezone.utc)
FL = {"parlay_id": "p1", "legs": [{"label": "A — SHOTS ON GOAL 2+ — -240", "estimate": 0.66}], "weakest_leg": {"label": "A", "estimate": 0.66}, "construction_quality": "GOOD", "action": "RECORD",
      "price_status": "UNPRICED_LEARNING_PARLAY", "combined_probability": None, "reference_decimal": 2.1, "dependence_level": "ASSUMED_INDEPENDENT"}


def status(fl=FL, at="2026-10-22T14:00:00Z"):
    return {"generated_at_utc": at, "eggy_flagship": fl}


def build(flag, primary=None):
    return ec.build(NOW, games=[], players_by_game={}, product={"et_today": "2026-10-22"}, pool=None, availability=None, plan=None, primary=primary, flagship=flag)


class TestOfficialOutput(unittest.TestCase):
    def test_a_good_flagship_is_the_official_output_unpriced_and_with_no_combined_price(self):
        out = build(ec.flagship_state(status(), "2026-10-22"))
        self.assertEqual(out["daily_output"], ec.FLAGSHIP)
        self.assertEqual(out["flagship"]["price_status"], "UNPRICED_LEARNING_PARLAY")
        self.assertIsNone(out["flagship"]["combined_probability"])
        self.assertNotIn("combined_american", out["flagship"])
        self.assertEqual(out["official_policy"]["digest"], ec.official_policy_digest())

    def test_no_good_flagship_is_pass(self):
        self.assertEqual(build(ec.flagship_state(status(None), "2026-10-22"))["daily_output"], ec.PASS)
        self.assertEqual(build(ec.flagship_state(status({**FL, "construction_quality": "QUESTIONABLE"}), "2026-10-22"))["daily_output"], ec.PASS)
        self.assertEqual(build(ec.flagship_state(status({**FL, "action": "PASS"}), "2026-10-22"))["daily_output"], ec.PASS)

    def test_the_old_primary_slip_is_not_the_official_output_any_more(self):
        out = build(ec.flagship_state(status(None), "2026-10-22"), primary={"ticket": {"legs": []}})
        self.assertEqual(out["daily_output"], ec.PASS)
        out = build(ec.flagship_state(status(), "2026-10-22"), primary={"ticket": {"legs": []}})
        self.assertEqual(out["daily_output"], ec.FLAGSHIP)

    def test_a_missing_or_stale_status_is_unavailable_never_read_as_pass(self):
        self.assertEqual(build(ec.flagship_state(None, "2026-10-22"))["daily_output"], "UNAVAILABLE")
        self.assertEqual(build(ec.flagship_state(status(at="2026-10-20T14:00:00Z"), "2026-10-22"))["daily_output"], "UNAVAILABLE")
        self.assertEqual(build(ec.flagship_state({"eggy_flagship": FL}, "2026-10-22"))["daily_output"], "UNAVAILABLE")

    def test_the_eastern_date_decides_the_day(self):
        self.assertEqual(ec.flagship_state(status(at="2026-10-23T02:00:00Z"), "2026-10-22")["state"], "OK")         # 22:00 ET on the 22nd

    def test_without_a_flagship_argument_the_legacy_behavior_is_unchanged(self):
        out = ec.build(NOW, games=[], players_by_game={}, product={}, pool=None, availability=None, plan=None, primary={"ticket": {"legs": []}})
        self.assertEqual(out["daily_output"], ec.OFFICIAL)
        self.assertIsNone(out["official_policy"])

    def test_the_policy_digest_is_pinned_to_this_exact_policy(self):
        self.assertEqual(ec.OFFICIAL_POLICY["max_outputs_per_day"], 1)
        self.assertEqual(ec.OFFICIAL_POLICY["no_qualifying_side"], "PASS")
        self.assertEqual(ec.OFFICIAL_POLICY["policy_id"], "EGGY_SIDE_PICK_V2")                  # Eggy's output is now ONE moneyline / puck-line side or PASS (operational/eggy_pick.py)
        self.assertEqual(ec.official_policy_digest(), "248f99b34b00aa12")

    def test_the_board_reads_the_existing_flagship_and_has_no_second_selection_path(self):
        src = inspect.getsource(slate_board)
        self.assertIn("flagship_state(learning_cohort.load_status()", src)
        self.assertNotIn("def _flagship", inspect.getsource(ec))


class TestThePageAndBoard(unittest.TestCase):
    def _page(self, status):
        from tests.test_slate_board import NOW as BOARD_NOW, _dated_for_the_page, doc, run_page, snapshot
        s = snapshot()
        s["slate_board"] = _dated_for_the_page(doc(status=status))
        at = run_page("41_Slate_Board.py", s)
        self.assertFalse(at.exception, at.exception)
        return s["slate_board"], (" ".join(x.value for x in at.markdown) + " " + " ".join(x.value for x in at.caption)).replace("&#36;", "$")

    def test_the_board_document_carries_the_flagship_and_the_page_labels_it_unpriced(self):
        from tests.test_slate_board import NOW as BOARD_NOW
        at = BOARD_NOW.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        board, blob = self._page({"generated_at_utc": at, "eggy_flagship": FL})
        self.assertEqual(board["eggy"]["daily_output"], ec.FLAGSHIP)
        self.assertIn("UNPRICED", blob)
        self.assertIn("A — SHOTS ON GOAL 2+ — -240", blob)
        self.assertIn("not a sportsbook price", blob)
        for banned in ("To return", "Paper stake", "Combined price"):
            self.assertNotIn(banned, blob)

    def test_with_no_flagship_the_page_says_pass_and_never_shows_the_old_primary_slip(self):
        from tests.test_slate_board import NOW as BOARD_NOW
        at = BOARD_NOW.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        board, blob = self._page({"generated_at_utc": at, "eggy_flagship": None})
        self.assertEqual(board["eggy"]["daily_output"], ec.PASS)
        self.assertIn("PASS", blob)
        self.assertNotIn("Today's single primary ticket", blob)


if __name__ == "__main__":
    unittest.main()
