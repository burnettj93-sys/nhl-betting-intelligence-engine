"""
Regression guard for a real, reproduced bug (2026-09-01): the public
GitHub / Streamlit Cloud deployment crashed with a raw FileNotFoundError
on nearly every page, because dashboard/demo_data.py and several
research pages depend on large research/*.jsonl corpora that were
gitignored and therefore absent from a fresh clone. Fixed by tracking a
real (filtered, for the 5 largest) or full subset of each file directly
-- see .gitignore's own comments for exactly which files and why.

This test simulates a fresh clone by temporarily hiding every path that
is STILL gitignored today (the ones deliberately left untracked --
raw CSV dumps, operational caches/dbs regenerated fresh each run, and
the handful of large corpora whose pages already degrade gracefully
without them) and running every dashboard page through Streamlit's own
AppTest harness. A file is always restored in a `finally` block, even if
the test itself fails or errors -- this must never be able to leave a
developer's checkout in a broken state.

If this test ever fails after someone adds a new page or a new gitignored
data dependency, that is the signal: either commit a small real subset of
the new file (see .gitignore's player_sog/goalie_saves comments for the
established pattern), or make the page degrade gracefully instead of
crashing raw -- never silently skip this test.
"""
from __future__ import annotations

import os
import shutil
import unittest

from streamlit.testing.v1 import AppTest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exactly the set of paths that remain gitignored after the 2026-09-01
# deploy fix -- kept in sync with .gitignore's own entries. If a new
# gitignored data file is added later and a page depends on it, this
# list (and likely .gitignore's real-subset commit pattern) needs a
# matching update, not a workaround here.
STILL_IGNORED_PATHS = [
    "operational/data_readiness_cache.json",
    "operational/live_multimarket_board_cache.json",
    "operational/paper_bankroll.db",
    "operational/prospective_observations.db",
    "operational/special_teams_history.db",
    "research/goalie_intelligence/raw",
    "research/moneypuck_ingestion/raw",
    "research/moneypuck_ingestion/research_moneypuck.db",
    "research/player_sog/raw",
    "research/real_nhl_pbp/raw",
    "research/real_nhl_pbp/research_pbp.db",
    "research/special_teams_role_transitions_table.jsonl",
    "research/team_game_special_teams_table.jsonl",
    "research/joint_scoring_dependence/joint_scoring.jsonl",
    "research/joint_shot_workload/joint_shot_workload.jsonl",
]

_SUFFIX = ".hidden_for_test_test_deploy_readiness"


class _HideIgnoredFiles:
    """Context manager: moves every existing path in STILL_IGNORED_PATHS
    aside, guarantees it's moved back in __exit__ even on exception."""

    def __enter__(self):
        self.moved = []
        for rel in STILL_IGNORED_PATHS:
            src = os.path.join(REPO_ROOT, rel)
            if os.path.exists(src):
                dst = src + _SUFFIX
                shutil.move(src, dst)
                self.moved.append((src, dst))
        return self

    def __exit__(self, exc_type, exc, tb):
        for src, dst in self.moved:
            if os.path.exists(dst):
                shutil.move(dst, src)
        return False


class TestFreshCloneNeverCrashesADashboardPage(unittest.TestCase):
    """Every dashboard page must render with zero exceptions using ONLY
    what a fresh `git clone` of this repo actually provides -- no local-
    only regenerated caches/dbs/raw downloads present."""

    @classmethod
    def setUpClass(cls):
        cls.pages = sorted(
            os.path.join(REPO_ROOT, "dashboard", "pages", f)
            for f in os.listdir(os.path.join(REPO_ROOT, "dashboard", "pages"))
            if f.endswith(".py")
        )
        assert cls.pages, "expected to find dashboard pages"

    def test_every_page_renders_without_exception_on_a_simulated_fresh_clone(self):
        failures = []
        with _HideIgnoredFiles():
            for page in self.pages:
                at = AppTest.from_file(page, default_timeout=120)
                at.run()
                if len(at.exception):
                    failures.append((os.path.basename(page), [str(e) for e in at.exception]))
        if failures:
            detail = "\n".join(f"{name}: {errs}" for name, errs in failures)
            self.fail(f"{len(failures)} page(s) raised an exception on a simulated fresh clone "
                      f"(missing gitignored file most likely -- see .gitignore's comments for the "
                      f"real-subset commit pattern):\n{detail}")


if __name__ == "__main__":
    unittest.main()
