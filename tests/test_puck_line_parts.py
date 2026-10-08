"""Puck line: the settlement resolver and the (uncertified) price-shape check. Nothing here enables a puck-line prediction or ticket leg."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from operational import outcome_resolver as r
from operational import paper_bet_settlement_driver as driver
from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing import puck_line_contract as plc
from research.real_market_parlay import engine as rmp


def db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((Path(__file__).parent.parent / "schema.sql").read_text())
    for t in ("TOR", "MTL"):
        c.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    return c


def game(c, gid, hs, as_, kind="REG", state="FINAL"):
    c.execute("INSERT INTO games (game_id, home_team, away_team, game_state, home_score, away_score, final_period_type) VALUES (?,?,?,?,?,?,?)",
              (gid, "TOR", "MTL", state, hs, as_, kind))


class TestSettlement(unittest.TestCase):
    def test_minus_one_and_a_half_needs_a_two_goal_win(self):
        c = db()
        game(c, 1, 5, 2)
        game(c, 2, 3, 2)
        self.assertTrue(r.resolve_puck_line(c, game_id=1, team_id="TOR", line=-1.5)["outcome_hit"])
        self.assertFalse(r.resolve_puck_line(c, game_id=2, team_id="TOR", line=-1.5)["outcome_hit"])
        self.assertTrue(r.resolve_puck_line(c, game_id=2, team_id="MTL", line=1.5)["outcome_hit"])       # lost by one still covers +1.5
        self.assertFalse(r.resolve_puck_line(c, game_id=1, team_id="MTL", line=1.5)["outcome_hit"])

    def test_extra_time_margin_is_one_goal_so_the_favourite_never_covers(self):
        c = db()
        game(c, 3, 4, 3, "SO")
        game(c, 4, 2, 1, "OT")
        for gid in (3, 4):
            self.assertFalse(r.resolve_puck_line(c, game_id=gid, team_id="TOR", line=-1.5)["outcome_hit"])
            self.assertTrue(r.resolve_puck_line(c, game_id=gid, team_id="MTL", line=1.5)["outcome_hit"])

    def test_fails_closed(self):
        c = db()
        game(c, 5, None, None, None, "SCHEDULED")
        game(c, 6, 3, 1)
        self.assertEqual(r.resolve_puck_line(c, game_id=5, team_id="TOR", line=-1.5)["status"], r.GAME_NOT_FINAL)
        self.assertEqual(r.resolve_puck_line(c, game_id=6, team_id="BOS", line=-1.5)["status"], r.TEAM_DID_NOT_PLAY)

    def test_dispatch_and_ledger_leg_mapping(self):
        c = db()
        game(c, 7, 6, 2)
        out = r.resolve_prediction(c, {"market_id": "PUCK_LINE", "threshold": -1.5, "side": "TEAM", "game_id": 7, "team": "TOR"})
        self.assertEqual((out["status"], out["outcome_hit"]), ("RESOLVED", True))
        self.assertEqual(r.resolve_prediction(c, {"market_id": "PUCK_LINE", "threshold": "x", "game_id": 7, "team": "TOR"})["status"], r.UNSUPPORTED_SETTLEMENT_MARKET)
        self.assertEqual(driver._leg_settlement_market_id({"market_family": "PUCK_LINE", "threshold": -1.5}), "PUCK_LINE")
        bet = {"legs_json": '[{"market_family": "PUCK_LINE", "participant_id": "TOR", "game_id": 7, "threshold": -1.5, "side": "TEAM", "american_price": 150}]'}
        self.assertEqual(driver.resolve_combo_bet(c, bet)["status"], "WIN")


EVENT = {"home_team": "Toronto Maple Leafs", "away_team": "Montréal Canadiens", "bookmakers": [{"key": "draftkings", "markets": [
    {"key": "spreads", "last_update": "2026-10-08T20:00:00Z", "outcomes": [
        {"name": "Toronto Maple Leafs", "price": 150, "point": -1.5}, {"name": "Montréal Canadiens", "price": -180, "point": 1.5}]}]}]}


class TestShapeCheckAndNonEnablement(unittest.TestCase):
    def test_a_well_formed_event_passes_the_shape_check_only(self):
        out = plc.validate_spreads_event(EVENT)
        self.assertEqual(out["status"], "SHAPE_OK")
        self.assertTrue(out["rows"][0]["standard_line"])

    def test_malformed_shapes_are_refused(self):
        bad = [{**EVENT, "bookmakers": []},
               {**EVENT, "bookmakers": [{"key": "draftkings", "markets": [{"key": "spreads", "outcomes": [{"name": "x", "price": 1, "point": 1}]}]}]},
               {**EVENT, "away_team": "Someone Else"}]
        for e in bad:
            self.assertEqual(plc.validate_spreads_event(e)["status"], "MALFORMED")

    def test_the_contract_is_not_certified_and_the_family_is_not_in_the_ticket_allowlist(self):
        self.assertFalse(pa.is_contract_verified("draftkings", "PUCK_LINE"))
        self.assertNotIn("PUCK_LINE", rmp.ALLOWED_MARKET_FAMILIES)

    def test_the_capture_script_refuses_to_spend_without_confirmation(self):
        import subprocess, sys
        out = subprocess.run([sys.executable, str(Path(__file__).parent.parent / "deploy" / "capture_puck_line_contract.py")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("Refusing", out.stdout)


if __name__ == "__main__":
    unittest.main()
