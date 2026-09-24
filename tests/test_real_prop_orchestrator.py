"""
Live SOG + Saves Production Certification block (2026-09-24): tests for
operational/real_prop_orchestrator.py. Uses the sanitized, realistically-
shaped fixtures under tests/fixtures/ (no real DraftKings SOG/Saves
payload has ever existed -- see docs/LIVE_SOG_SAVES_CERTIFICATION.md)
against REAL research corpora (research/player_sog, research/goalie_saves,
research/goalie_intelligence) so the identity/model/pricing path is
exercised for real, only the market payload itself is synthetic.

All ledger/paper-bankroll/nhl-schedule state is isolated (temp files or
:memory:); never the real prospective_observations.db, paper_bankroll.db,
or nhl.db.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import db
from operational import paper_bankroll as pb
from operational import prospective_ledger as pl
from operational import real_prop_orchestrator as rpo
from research.generic_prop_pricing import evaluator as ge

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


def _fresh_nhl_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return db.init_db(Path(tmp.name), wipe=True)


def _seed_tor_bos_game(conn, game_id=9001, game_date="2026-10-15"):
    conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('TOR'), ('BOS')")
    conn.execute(
        "INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, "
        "schedule_observed_at_utc, game_state, source) VALUES (?, '20262027', ?, ?, 'TOR', 'BOS', ?, 'SCHEDULED', 'test')",
        (game_id, game_date, f"{game_date}T23:00:00", f"{game_date}T00:00:00"))
    conn.commit()
    return game_id


def _tmp_pl_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pl.init_db(Path(path)), Path(path)


def _tmp_bankroll_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    Path(path).unlink()
    return pb.init_db(Path(path)), Path(path)


class SOGOrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.game_id = _seed_tor_bos_game(self.nhl_conn)
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()
        self.payload = _load_fixture("draftkings_player_shots_on_goal_shaped.json")

    def tearDown(self):
        self.nhl_conn.close()
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def _run(self, payload=None):
        return rpo.run_real_sog_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[payload if payload is not None else self.payload])


class TestSOGContractNotVerifiedFailsClosed(SOGOrchestratorTestBase):
    """Part 4/33: the real, current contract state -- DraftKings has
    never posted player_shots_on_goal -- must produce CONTRACT_NOT_VERIFIED
    and record NOTHING, even against a perfectly well-formed, correctly
    identity-matched, model-eligible quote."""

    def test_real_contract_state_is_unverified_and_nothing_is_recorded(self):
        from research.generic_prop_pricing import provider_adapter as pa
        self.assertFalse(pa.is_contract_verified("draftkings", "PLAYER_SOG"))

        summary = self._run()
        self.assertEqual(summary["status"], "SUCCESS")
        self.assertEqual(summary["quotes_seen"], 1)
        self.assertEqual(summary["contract_not_verified"], 1)
        self.assertEqual(summary["recommendations_recorded"], 0)
        self.assertEqual(summary["paper_bets_created"], 0)
        total = self.pl_conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(total, 0)


class TestSOGIdentityMatching(SOGOrchestratorTestBase):
    def test_known_real_player_on_the_right_team_matches(self):
        # Real identity resolution reaches at least the contract gate
        # (never UNMATCHED/AMBIGUOUS) for a real, correctly-teamed name.
        summary = self._run()
        result = summary["results"][0]
        self.assertNotIn(result["status"], ("UNMATCHED", "AMBIGUOUS"))

    def test_unknown_player_name_is_unmatched_not_a_guess(self):
        bad_payload = copy.deepcopy(self.payload)
        for outcome in bad_payload["bookmakers"][0]["markets"][0]["outcomes"]:
            outcome["description"] = "Totally Fictional Player Zzz"
        summary = self._run(bad_payload)
        result = summary["results"][0]
        self.assertEqual(result["status"], "UNMATCHED")
        self.assertEqual(summary["identity_unmatched"], 1)
        self.assertEqual(summary["recommendations_recorded"], 0)

    def test_unmatched_event_never_reaches_identity_or_pricing(self):
        bad_payload = copy.deepcopy(self.payload)
        bad_payload["home_team"] = "Some Made Up Team"
        summary = self._run(bad_payload)
        result = summary["results"][0]
        self.assertEqual(result["status"], "UNMATCHED")


class TestSOGThresholdMapping(SOGOrchestratorTestBase):
    """Part 7: Over 1.5->2+, Over 2.5->3+, Over 3.5->4+, Over 4.5->5+;
    Over 5.5 (6+) is real math but never decision-eligible."""

    def _payload_at_point(self, point):
        payload = copy.deepcopy(self.payload)
        for outcome in payload["bookmakers"][0]["markets"][0]["outcomes"]:
            outcome["point"] = point
        return payload

    def test_unsupported_threshold_6plus_is_not_model_validated(self):
        summary = self._run(self._payload_at_point(5.5))
        result = summary["results"][0]
        self.assertEqual(result["status"], ge.NOT_MODEL_VALIDATED)
        self.assertEqual(summary["not_model_validated"], 1)

    def test_validated_threshold_4plus_proceeds_to_contract_gate(self):
        summary = self._run(self._payload_at_point(3.5))
        result = summary["results"][0]
        # proceeds far enough to hit the (currently unverified) contract
        # gate -- never rejected for the threshold itself.
        self.assertEqual(result["status"], ge.CONTRACT_NOT_VERIFIED)


class TestSOGEligiblePathWithContractVerifiedForTesting(SOGOrchestratorTestBase):
    """Proves the REST of the pipeline (identity -> model -> pricing ->
    immutable snapshot -> paper bet) genuinely works, using the exact
    same override tests/test_generic_prop_pricing.py's own
    Test06GoalsAssistsPointsSavesModelSideReadiness class uses (a
    monkeypatched VERIFIED_CONTRACTS) -- never claiming this reflects
    real production state, which test_real_contract_state_is_unverified
    above independently locks down."""

    def _run_with_contract_verified(self, payload=None):
        from unittest import mock
        from research.generic_prop_pricing import provider_adapter as pa
        with mock.patch.object(pa, "VERIFIED_CONTRACTS",
                               frozenset({("draftkings", "MONEYLINE"), ("draftkings", "PLAYER_SOG")})):
            return self._run(payload)

    def test_eligible_quote_records_a_real_immutable_snapshot(self):
        summary = self._run_with_contract_verified()
        self.assertEqual(summary["recommendations_recorded"], 1)
        row = self.pl_conn.execute("SELECT * FROM predictions WHERE market_id='PLAYER_SOG_4PLUS'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["team"], "TOR")
        self.assertEqual(row["player_id"], "8475166")  # real John Tavares MoneyPuck id
        self.assertEqual(row["threshold"], "4+")
        self.assertIsNotNone(row["conservative_probability"])

    def test_rerun_against_the_same_quote_is_idempotent(self):
        first = self._run_with_contract_verified()
        second = self._run_with_contract_verified()
        total = self.pl_conn.execute("SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(total, 1)
        self.assertGreaterEqual(first["recommendations_recorded"] + second["recommendations_recorded"], 1)

    def test_a_later_real_price_change_creates_a_market_refresh_row(self):
        self._run_with_contract_verified()
        later_payload = copy.deepcopy(self.payload)
        later_payload["bookmakers"][0]["last_update"] = "2026-10-15T18:30:00Z"
        later_payload["bookmakers"][0]["markets"][0]["last_update"] = "2026-10-15T18:30:00Z"
        self._run_with_contract_verified(later_payload)
        rows = self.pl_conn.execute(
            "SELECT prediction_checkpoint FROM predictions WHERE market_id='PLAYER_SOG_4PLUS' "
            "ORDER BY created_at_utc").fetchall()
        self.assertEqual([r["prediction_checkpoint"] for r in rows], ["PRIMARY_DAILY", "MARKET_REFRESH"])


class SavesOrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self.nhl_conn = _fresh_nhl_conn()
        self.game_id = _seed_tor_bos_game(self.nhl_conn)
        self.pl_conn, self.pl_path = _tmp_pl_conn()
        self.bankroll_conn, self.bankroll_path = _tmp_bankroll_conn()
        self.payload = _load_fixture("draftkings_player_total_saves_shaped.json")

    def tearDown(self):
        self.nhl_conn.close()
        self.pl_conn.close()
        self.pl_path.unlink(missing_ok=True)
        self.bankroll_conn.close()
        self.bankroll_path.unlink(missing_ok=True)

    def _run(self, payload=None):
        return rpo.run_real_saves_recommendations(
            nhl_conn=self.nhl_conn, pl_conn=self.pl_conn, bankroll_conn=self.bankroll_conn,
            payloads=[payload if payload is not None else self.payload])


class TestSavesContractNotVerifiedFailsClosed(SavesOrchestratorTestBase):
    def test_real_contract_state_is_unverified_and_nothing_is_recorded(self):
        from research.generic_prop_pricing import provider_adapter as pa
        self.assertFalse(pa.is_contract_verified("draftkings", "GOALIE_SAVES"))
        summary = self._run()
        self.assertEqual(summary["contract_not_verified"], 1)
        self.assertEqual(summary["recommendations_recorded"], 0)
        self.assertEqual(summary["paper_bets_created"], 0)


class TestSavesThresholdMapping(SavesOrchestratorTestBase):
    def _payload_at_point(self, point):
        payload = copy.deepcopy(self.payload)
        for outcome in payload["bookmakers"][0]["markets"][0]["outcomes"]:
            outcome["point"] = point
        return payload

    def test_29point5_is_30plus_and_not_operationally_eligible(self):
        summary = self._run(self._payload_at_point(29.5))
        self.assertEqual(summary["results"][0]["status"], ge.NOT_MODEL_VALIDATED)

    def test_34point5_is_35plus_and_not_operationally_eligible(self):
        summary = self._run(self._payload_at_point(34.5))
        self.assertEqual(summary["results"][0]["status"], ge.NOT_MODEL_VALIDATED)

    def test_39point5_is_40plus_and_not_operationally_eligible(self):
        summary = self._run(self._payload_at_point(39.5))
        self.assertEqual(summary["results"][0]["status"], ge.NOT_MODEL_VALIDATED)

    def test_19point5_is_20plus_and_proceeds_to_contract_gate(self):
        summary = self._run(self._payload_at_point(19.5))
        self.assertEqual(summary["results"][0]["status"], ge.CONTRACT_NOT_VERIFIED)

    def test_24point5_is_25plus_and_proceeds_to_contract_gate(self):
        summary = self._run(self._payload_at_point(24.5))
        self.assertEqual(summary["results"][0]["status"], ge.CONTRACT_NOT_VERIFIED)


class TestSavesStarterCertaintyGate(SavesOrchestratorTestBase):
    """Part 12: even with the contract verified (for testing) and a real
    edge, Saves must NEVER auto-generate a confident BET -- this project
    has no real CONFIRMED-starter source for any market yet."""

    def test_verified_contract_never_produces_a_bet_without_real_starter_confirmation(self):
        from unittest import mock
        from research.generic_prop_pricing import provider_adapter as pa
        with mock.patch.object(pa, "VERIFIED_CONTRACTS",
                               frozenset({("draftkings", "MONEYLINE"), ("draftkings", "GOALIE_SAVES")})):
            summary = self._run()
        self.assertEqual(summary["paper_bets_created"], 0)
        for r in summary["results"]:
            if r.get("recorded"):
                self.assertNotEqual(r.get("action"), "BET")


if __name__ == "__main__":
    unittest.main()
