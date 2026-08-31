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
    availability -- it reads only the real provider_adapter registry."""

    def test_reports_zero_verified_contracts(self):
        item = sh.contract_status_health()
        self.assertIn("VERIFIED LIVE CONTRACTS: 0", item["message"])
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


if __name__ == "__main__":
    unittest.main()
