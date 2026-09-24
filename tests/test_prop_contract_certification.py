"""
Starting-Goalie Certainty + Prop Contract Watch block (2026-09-24), Part
7: tests for operational/prop_contract_certification.py -- the
deterministic first-observation certification process. Never touches
research/generic_prop_pricing/provider_adapter.py::VERIFIED_CONTRACTS;
that remains a manual human step, proven by asserting it every time.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import prop_contract_certification as pcc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _tor_bos_schedule():
    return [{"game_id": 9001, "home_team": "TOR", "away_team": "BOS", "game_date": "2026-10-15"}]


class TestCertifySOGPayload(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        Path(path).unlink()
        self.fixture_path = Path(path)
        self._patcher = mock.patch.object(pcc, "FIXTURES_DIR", self.fixture_path.parent)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        (self.fixture_path.parent / "draftkings_player_shots_on_goal_CANDIDATE.json").unlink(missing_ok=True)

    def test_a_well_formed_real_shaped_payload_passes_every_check(self):
        payload = _load_fixture("draftkings_player_shots_on_goal_shaped.json")
        result = pcc.certify_sog_payload(payload, schedule=_tor_bos_schedule())
        for name, check in result["checks"].items():
            self.assertTrue(check["passed"], f"{name} failed: {check['detail']}")
        self.assertTrue(result["all_checks_passed"])

    def test_never_updates_verified_contracts_itself(self):
        payload = _load_fixture("draftkings_player_shots_on_goal_shaped.json")
        result = pcc.certify_sog_payload(payload, schedule=_tor_bos_schedule())
        self.assertFalse(result["verified_contracts_updated"])
        from research.generic_prop_pricing import provider_adapter as pa
        self.assertFalse(pa.is_contract_verified("draftkings", "PLAYER_SOG"))

    def test_writes_a_sanitized_fixture_only_when_all_checks_pass(self):
        payload = _load_fixture("draftkings_player_shots_on_goal_shaped.json")
        result = pcc.certify_sog_payload(payload, schedule=_tor_bos_schedule())
        self.assertIsNotNone(result["fixture_written"])
        written = Path(result["fixture_written"])
        self.assertTrue(written.exists())
        content = json.loads(written.read_text())
        self.assertIn("_provenance", content)

    def test_unmatched_event_fails_the_event_mapping_check_and_writes_no_fixture(self):
        payload = _load_fixture("draftkings_player_shots_on_goal_shaped.json")
        result = pcc.certify_sog_payload(payload, schedule=[])  # no real schedule to match against
        self.assertFalse(result["checks"]["event_mapping"]["passed"])
        self.assertFalse(result["all_checks_passed"])
        self.assertIsNone(result["fixture_written"])


class TestCertifySavesPayload(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        Path(path).unlink()
        self.fixture_path = Path(path)
        self._patcher = mock.patch.object(pcc, "FIXTURES_DIR", self.fixture_path.parent)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        (self.fixture_path.parent / "draftkings_player_total_saves_CANDIDATE.json").unlink(missing_ok=True)

    def test_a_well_formed_real_shaped_payload_passes_every_check(self):
        payload = _load_fixture("draftkings_player_total_saves_shaped.json")
        result = pcc.certify_saves_payload(payload, schedule=_tor_bos_schedule())
        for name, check in result["checks"].items():
            self.assertTrue(check["passed"], f"{name} failed: {check['detail']}")
        self.assertTrue(result["all_checks_passed"])

    def test_never_updates_verified_contracts_itself(self):
        payload = _load_fixture("draftkings_player_total_saves_shaped.json")
        result = pcc.certify_saves_payload(payload, schedule=_tor_bos_schedule())
        self.assertFalse(result["verified_contracts_updated"])
        from research.generic_prop_pricing import provider_adapter as pa
        self.assertFalse(pa.is_contract_verified("draftkings", "GOALIE_SAVES"))

    def test_unknown_goalie_name_fails_identity_check(self):
        import copy
        payload = copy.deepcopy(_load_fixture("draftkings_player_total_saves_shaped.json"))
        for outcome in payload["bookmakers"][0]["markets"][0]["outcomes"]:
            outcome["description"] = "Totally Fictional Goalie Zzz"
        result = pcc.certify_saves_payload(payload, schedule=_tor_bos_schedule())
        self.assertFalse(result["checks"]["goalie_identity"]["passed"])
        self.assertFalse(result["all_checks_passed"])


if __name__ == "__main__":
    unittest.main()
