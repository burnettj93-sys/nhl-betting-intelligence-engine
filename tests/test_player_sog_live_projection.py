"""
Production Gap Closure sprint (2026-10-01): tests for
research/player_sog/live_projection.py::corpus_covers_date() -- the
real-production-only staleness guard.

Root-cause: a real-production bug hunt found a real, currently-active
player (Auston Matthews) silently misclassified PROJECTED_INACTIVE for a
real 2026-27 game because research/player_sog's corpus (and the paired
team-schedule corpus) is frozen at 2026-04-16 -- any gap between the
corpus's own most recent team game and the real target date was silently
ignored, so a 6-month-old "recent appearance" window was treated as if it
meant something about the target date.

This check deliberately lives OUTSIDE project_player_sog() itself (see
that module's own docstring): project_player_sog() is ALSO called by
dashboard/demo_data.py against its own permanently-disclosed SIMULATED_DATE
constant, using this SAME frozen corpus on purpose -- an earlier version of
this fix put the guard INSIDE project_player_sog() and broke that demo path
outright. Only the three real call sites (operational/real_prop_orchestrator.py
x2, research/real_market_parlay/real_slate_adapter.py x1) call
corpus_covers_date() explicitly before trusting project_player_sog()'s result;
demo_data.py calls neither.
"""
from __future__ import annotations

import unittest

from research.player_sog.live_projection import MAX_TEAM_SCHEDULE_GAP_DAYS, corpus_covers_date


def _team_schedule(n=5, start="2026-04-01"):
    import datetime as dt
    base = dt.date.fromisoformat(start)
    return [{"game_id": 1000 + i, "game_date": (base + dt.timedelta(days=i * 2)).isoformat()}
            for i in range(n)]


class TestCorpusCoversDate(unittest.TestCase):
    def test_a_large_gap_between_corpus_and_target_date_does_not_cover(self):
        """The exact real scenario: the corpus's last real TOR game is
        2026-04-09, but the target is a real 2026-27 game -- a gap no
        real NHL season ever produces."""
        team_schedules = {"TOR": _team_schedule(start="2026-04-01")}  # last game 2026-04-09
        result = corpus_covers_date(team_schedules, "TOR", "2026-09-30")
        self.assertFalse(result["covers"])
        self.assertIn("TOR", result["reason"])
        self.assertIn("2026-04-09", result["reason"])

    def test_a_small_in_season_gap_covers(self):
        team_schedules = {"TOR": _team_schedule(start="2026-04-01")}  # last game 2026-04-09
        result = corpus_covers_date(team_schedules, "TOR", "2026-04-12")
        self.assertTrue(result["covers"])
        self.assertIsNone(result["reason"])

    def test_exactly_at_the_threshold_boundary(self):
        import datetime as dt
        team_schedules = {"TOR": _team_schedule(n=1, start="2026-01-01")}
        just_inside = (dt.date(2026, 1, 1) + dt.timedelta(days=MAX_TEAM_SCHEDULE_GAP_DAYS)).isoformat()
        just_outside = (dt.date(2026, 1, 1) + dt.timedelta(days=MAX_TEAM_SCHEDULE_GAP_DAYS + 1)).isoformat()
        self.assertTrue(corpus_covers_date(team_schedules, "TOR", just_inside)["covers"])
        self.assertFalse(corpus_covers_date(team_schedules, "TOR", just_outside)["covers"])

    def test_no_schedule_entry_at_all_is_a_different_concern_never_falsely_stale(self):
        """An empty/missing team schedule is NOT staleness -- it's a
        separate, pre-existing concern (project_player_sog's own
        no-context fallback). This function must never claim staleness
        about a gap it was never given data to measure."""
        self.assertTrue(corpus_covers_date({}, "TOR", "2026-09-30")["covers"])
        self.assertTrue(corpus_covers_date({"TOR": []}, "TOR", "2026-09-30")["covers"])

    def test_never_called_by_the_demo_data_module(self):
        """Architectural guard: dashboard/demo_data.py deliberately uses
        this same frozen corpus against its own disclosed SIMULATED_DATE
        on purpose -- it must never call corpus_covers_date()."""
        import inspect
        from research.demo_board import demo_data as dd
        source = inspect.getsource(dd)
        self.assertNotIn("corpus_covers_date", source)


if __name__ == "__main__":
    unittest.main()
