"""A game's last pregame analysis survives its start: saved on every build, recoverable from the published-snapshot archive, and never replaced by 'Game not scheduled'."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import pregame_archive as pa
from operational import slate_board as sb

DAY = "2026-10-17"


def block(gid, state="SCHEDULED", analysed=True, **extra):
    return {"game_id": gid, "away": "AAA", "home": "BBB", "state": state, "coverage": {"analysis_available": analysed, "recommended": ["x"]}, "side": {"recommendation": {"team": "BBB"}}, "goalies": [], "scorers": [], "shots": {"picks": []}, **extra}


class Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        p = mock.patch.object(pa.state_paths, "path", side_effect=lambda name, **kw: d / name)
        p.start()
        self.addCleanup(p.stop)


class TestSavedBlocks(Isolated):
    def test_the_last_scheduled_block_is_kept_and_a_started_game_never_overwrites_it(self):
        pa.remember({"date_et": DAY, "generated_at_utc": "2026-10-17T15:00:00Z", "games": [block("1"), block("2", analysed=False)]})
        pa.remember({"date_et": DAY, "generated_at_utc": "2026-10-17T16:00:00Z", "games": [block("1", scorers=[{"name": "later"}])]})
        pa.remember({"date_et": DAY, "generated_at_utc": "2026-10-17T21:00:00Z", "games": [block("1", state="STARTED", frozen_at_utc="x")]})
        b = pa.recall(DAY, "1")
        self.assertEqual(b["frozen_at_utc"], "2026-10-17T16:00:00Z")
        self.assertEqual(b["scorers"], [{"name": "later"}])
        self.assertEqual(b["frozen_source"], "SAVED_PREGAME_BLOCK")
        self.assertIsNone(pa.recall(DAY, "2"))                                                  # a game that was never analysed has nothing to keep

    def test_only_three_days_are_kept(self):
        for d in ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"):
            pa.remember({"date_et": d, "generated_at_utc": d + "T12:00:00Z", "games": [block("1")]})
        self.assertIsNone(pa.recall("2026-10-10", "1"))
        self.assertIsNotNone(pa.recall("2026-10-13", "1"))


class TestRecoveryFromTheArchive(Isolated):
    def snapshots(self):
        newest = {"slate_board": {"date_et": DAY, "generated_at_utc": "2026-10-17T21:00:00Z", "games": [block("1", state="STARTED", analysed=False), block("2")]}}
        middle = {"slate_board": {"date_et": DAY, "generated_at_utc": "2026-10-17T19:00:00Z", "games": [block("1"), block("2")]}}
        yesterday = {"slate_board": {"date_et": "2026-10-16", "generated_at_utc": "2026-10-16T19:00:00Z", "games": [block("1"), block("3")]}}
        return {"c3": json.dumps(newest), "c2": json.dumps(middle), "c1": json.dumps(yesterday)}

    def test_it_finds_the_newest_snapshot_in_which_the_game_was_still_scheduled_and_analysed(self):
        snaps = self.snapshots()
        got = pa.recover(DAY, {"1"}, log_fn=lambda: "c3 c2 c1", show_fn=snaps.get)
        self.assertEqual(got["1"]["frozen_at_utc"], "2026-10-17T19:00:00Z")
        self.assertEqual(got["1"]["frozen_source"], "PUBLISHED_SNAPSHOT_ARCHIVE")
        self.assertEqual(got["1"]["frozen_commit"], "c2")

    def test_it_never_reaches_into_another_day_and_returns_nothing_it_cannot_find(self):
        self.assertEqual(pa.recover(DAY, {"3"}, log_fn=lambda: "c3 c2 c1", show_fn=self.snapshots().get), {})        # game 3 only appears on the 16th
        self.assertEqual(pa.recover(DAY, {"1"}, log_fn=lambda: "", show_fn=lambda s: None), {})
        self.assertEqual(pa.recover(DAY, set(), log_fn=lambda: "c3"), {})

    def test_a_recovery_is_attempted_once_per_game(self):
        self.assertEqual(pa.attempted(DAY), set())
        pa.mark_attempted(DAY, {"1"})
        pa.mark_attempted(DAY, {"2"})
        self.assertEqual(pa.attempted(DAY), {"1", "2"})


class TestTheBoardUsesThem(unittest.TestCase):
    def game(self, state):
        return {"game_id": "9", "state": state, "start_et": "1:00 PM ET"}

    def test_a_recovered_block_freezes_the_game_with_its_source_and_inert_recommendations(self):
        rec = {"9": block("9", frozen_at_utc="2026-10-17T16:00:00Z", frozen_source="PUBLISHED_SNAPSHOT_ARCHIVE")}
        entry, bump = sb._frozen_block(None, DAY, self.game("FINAL"), rec)
        self.assertEqual(entry["frozen_at_utc"], "2026-10-17T16:00:00Z")
        self.assertEqual(entry["frozen_source"], "PUBLISHED_SNAPSHOT_ARCHIVE")
        self.assertIsNone(entry["side"]["recommendation"])
        self.assertEqual(entry["coverage"]["recommended"], [])
        self.assertEqual(bump["analysed"], 1)

    def test_nothing_to_freeze_returns_none_and_a_postponed_game_is_never_frozen(self):
        self.assertIsNone(sb._frozen_block(None, DAY, self.game("FINAL"), {}))
        self.assertIsNone(sb._frozen_block(None, DAY, self.game("POSTPONED"), {"9": block("9", frozen_at_utc="x")}))


if __name__ == "__main__":
    unittest.main()
