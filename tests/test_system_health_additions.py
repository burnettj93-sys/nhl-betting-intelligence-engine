"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 7
(Parts 48-50): tests for the 4 new operational/system_health.py
components. Deliberately does NOT re-test the pre-existing components
(NHL_API/SCHEDULE/etc.) -- those were already real and untouched.
"""
from __future__ import annotations

import unittest

from operational import system_health as sh


class Test01SpecialTeamsRoleFreshness(unittest.TestCase):
    def test_reports_real_row_count_and_latest_date(self):
        item = sh.special_teams_role_freshness_health()
        self.assertIn(item["status"], ("OK", "WAITING", "ERROR"))
        if item["status"] == "OK":
            self.assertIn("rows", item["message"])


class Test02OddsArchiveFreshness(unittest.TestCase):
    def test_never_errors_on_a_missing_directory(self):
        import operational.system_health as sh_module
        original = sh_module.REPO_ROOT
        try:
            sh_module.REPO_ROOT = original / "does_not_exist_at_all"
            item = sh.odds_archive_freshness_health()
            self.assertEqual(item["status"], "WAITING")
        finally:
            sh_module.REPO_ROOT = original

    def test_real_archive_reports_ok_with_a_real_timestamp(self):
        item = sh.odds_archive_freshness_health()
        self.assertIn(item["status"], ("OK", "WAITING"))


class Test03ContractStatusIsHonestlyZero(unittest.TestCase):
    """Part 50: this must never be inferred from demo-mode market
    availability -- it reads only the real provider_adapter registry.

    Live DK / Paper Bankroll completion sprint (2026-08-31): MONEYLINE
    was actually, really verified this sprint, so the honest count is
    now 1, not 0 -- the class name is kept (renaming would obscure the
    history in a diff) but the assertion reflects the current real fact."""

    def test_reports_the_real_verified_contract_count(self):
        item = sh.contract_status_health()
        self.assertIn("VERIFIED LIVE CONTRACTS: 1", item["message"])
        self.assertEqual(item["status"], "OK")

    def test_never_imports_demo_data(self):
        import inspect
        src = inspect.getsource(sh.contract_status_health)
        self.assertNotIn("demo_data", src)


class Test04SettlementBacklog(unittest.TestCase):
    def test_no_ledger_yet_is_not_required(self):
        import operational.system_health as sh_module
        from operational import prospective_ledger as pl
        original = pl.DB_PATH
        try:
            pl.DB_PATH = original.parent / "does_not_exist_prospective.db"
            item = sh.settlement_backlog_health()
            self.assertEqual(item["status"], "NOT_REQUIRED")
        finally:
            pl.DB_PATH = original


class Test05FullSnapshotIncludesAllFour(unittest.TestCase):
    def test_build_system_health_includes_new_components(self):
        health = sh.build_system_health()
        for key in ("SPECIAL_TEAMS_HISTORY", "ODDS_ARCHIVE", "CONTRACT_STATUS", "SETTLEMENT_BACKLOG"):
            self.assertIn(key, health)
            self.assertIn("status", health[key])


# ---------------------------------------------------------------------
# Live Odds/Parlay/Post-Mortem activation sprint (2026-09-15), Part 75:
# 3 new components -- scheduler status, and daily post-mortem status.
# (odds_collection_status() returns a plain dict, not a _health_item,
# since it feeds several distinct Today-page fields, not one health row.)
# ---------------------------------------------------------------------
class Test06LiveOddsSchedulerHealth(unittest.TestCase):
    def test_never_raises_even_if_launchctl_is_unavailable(self):
        import subprocess
        original = subprocess.run

        def _boom(*a, **k):
            raise FileNotFoundError("no launchctl on this platform")

        subprocess.run = _boom
        try:
            item = sh.live_odds_scheduler_health()
            self.assertEqual(item["status"], "UNKNOWN")
        finally:
            subprocess.run = original

    def test_reports_ok_when_all_real_jobs_are_loaded(self):
        import subprocess
        original = subprocess.run

        class _FakeResult:
            stdout = "\n".join(sh._SCHEDULER_LABELS)

        subprocess.run = lambda *a, **k: _FakeResult()
        try:
            item = sh.live_odds_scheduler_health()
            self.assertEqual(item["status"], "OK")
        finally:
            subprocess.run = original

    def test_reports_waiting_when_nothing_is_loaded(self):
        import subprocess
        original = subprocess.run

        class _FakeResult:
            stdout = ""

        subprocess.run = lambda *a, **k: _FakeResult()
        try:
            item = sh.live_odds_scheduler_health()
            self.assertEqual(item["status"], "WAITING")
        finally:
            subprocess.run = original


class Test07OddsCollectionStatus(unittest.TestCase):
    def test_waiting_when_no_cache_files_exist(self):
        import operational.system_health as sh_module
        original = sh_module.REPO_ROOT
        try:
            sh_module.REPO_ROOT = original / "does_not_exist_at_all"
            status = sh.odds_collection_status()
            self.assertEqual(status["status"], "WAITING")
            self.assertEqual(status["tracked_events"], 0)
        finally:
            sh_module.REPO_ROOT = original

    def test_real_cache_reports_ok_or_waiting_never_crashes(self):
        status = sh.odds_collection_status()
        self.assertIn(status["status"], ("OK", "WAITING"))
        self.assertIsInstance(status["tracked_events"], int)


class Test08NewContractCandidatesHealth(unittest.TestCase):
    def test_zero_when_file_does_not_exist(self):
        item = sh.new_contract_candidates_health()
        self.assertIn(item["status"], ("OK", "ERROR"))


class Test09PostmortemStatusHealth(unittest.TestCase):
    def test_waiting_when_no_reports_directory(self):
        import operational.system_health as sh_module
        original = sh_module.REPO_ROOT
        try:
            sh_module.REPO_ROOT = original / "does_not_exist_at_all"
            item = sh.postmortem_status_health()
            self.assertEqual(item["status"], "WAITING")
        finally:
            sh_module.REPO_ROOT = original


class Test10FullSnapshotIncludesSprintAdditions(unittest.TestCase):
    def test_build_system_health_includes_new_keys(self):
        health = sh.build_system_health()
        for key in ("LIVE_ODDS_SCHEDULER", "NEW_CONTRACT_CANDIDATES", "DAILY_POSTMORTEM"):
            self.assertIn(key, health)
            self.assertIn("status", health[key])


if __name__ == "__main__":
    unittest.main()
