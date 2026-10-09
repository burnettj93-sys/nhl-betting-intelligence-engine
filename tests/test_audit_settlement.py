"""The settlement audit re-derives results from raw box scores. It must reach the right answer for each documented case and flag a disagreement."""
import json
import unittest

from deploy import audit_settlement as S


def box(state="FINAL", skaters=None, goalies=None, home=("AAA", 3), away=("BBB", 2)):
    sk = [{"playerId": pid, "sog": s, "goals": g, "assists": a, "points": g + a} for pid, s, g, a in (skaters or [])]
    gl = [{"playerId": pid, "saves": sv, "toi": toi} for pid, sv, toi in (goalies or [])]
    return {"gameState": state, "homeTeam": {"abbrev": home[0], "score": home[1]}, "awayTeam": {"abbrev": away[0], "score": away[1]},
            "playerByGameStats": {"homeTeam": {"forwards": sk, "defense": [], "goalies": gl}, "awayTeam": {"forwards": [], "defense": [], "goalies": []}}}


def leg(pid, fam="PLAYER_SOG_ALTERNATE", k=2, price=-125.0, gid="1", **kw):
    return {"participant_id": str(pid), "market_family": fam, "threshold": k, "american_price": price, "game_id": gid, **kw}


def row(legs, status, pnl, odds, stake=10.0, settlement=None):
    return {"bet_id": "X", "legs_json": json.dumps(legs), "entry_odds": odds, "stake": stake, "result_status": status, "profit_loss": pnl,
            "settlement_json": json.dumps(settlement) if settlement else None, "market_family": None, "team": None, "event_id": None, "player_id": None}


class TestSettlementAudit(unittest.TestCase):
    def setUp(self):
        S._cache.clear()

    def test_parlay_win_and_loss(self):
        S._cache["1"] = box(skaters=[(1, 3, 0, 0), (2, 2, 0, 1)])
        win = S.audit_row("t", row([leg(1), leg(2, "PLAYER_POINTS", 1, 110.0)], "WIN", 10 * (1.8 * 2.1 - 1), 278.0))
        self.assertTrue(win["agrees"], win["problems"])
        lost = S.audit_row("t", row([leg(1, k=4), leg(2)], "LOSS", -10.0, 300.0))
        self.assertTrue(lost["agrees"], lost["problems"])

    def test_wrong_stored_result_is_flagged(self):
        S._cache["1"] = box(skaters=[(1, 1, 0, 0)])
        r = S.audit_row("t", row([leg(1)], "WIN", 8.0, -125.0))
        self.assertFalse(r["agrees"])

    def test_did_not_play_leg_is_unresolved_not_a_win(self):
        S._cache["1"] = box(skaters=[(1, 3, 0, 0)])
        r = S.audit_row("t", row([leg(1), leg(9)], "UNRESOLVED", None, 200.0))
        self.assertEqual(r["recomputed"]["status"], "UNRESOLVED")
        self.assertTrue(r["agrees"])

    def test_a_lost_leg_loses_even_with_a_did_not_play_leg(self):
        S._cache["1"] = box(skaters=[(1, 0, 0, 0)])
        r = S.audit_row("t", row([leg(1), leg(9)], "LOSS", -10.0, 200.0))
        self.assertEqual(r["recomputed"]["status"], "LOSS")

    def test_game_not_final_is_pending(self):
        S._cache["1"] = box(state="LIVE", skaters=[(1, 3, 0, 0)])
        r = S.audit_row("t", row([leg(1)], "PENDING", None, -125.0))
        self.assertEqual(r["recomputed"]["status"], "PENDING")
        self.assertTrue(r["agrees"])

    def test_goalie_who_did_not_play_and_saves_threshold(self):
        S._cache["1"] = box(goalies=[(7, 30, "59:00"), (8, 0, "00:00")])
        ok = S.audit_row("t", row([leg(7, "GOALIE_SAVES", 28, -110.0)], "WIN", 9.09, -110.0))
        self.assertTrue(ok["agrees"], ok["problems"])
        dnp = S.audit_row("t", row([leg(8, "GOALIE_SAVES", 20, -110.0)], "UNRESOLVED", None, -110.0))
        self.assertEqual(dnp["recomputed"]["status"], "UNRESOLVED")

    def test_moneyline_follows_the_final_score(self):
        S._cache["1"] = box(home=("AAA", 4), away=("BBB", 3))
        r = S.audit_row("t", {**row([], "WIN", 9.5, 195.0), "legs_json": None, "market_family": "MONEYLINE", "team": "AAA", "event_id": "1", "stake": 10.0, "profit_loss": 19.5})
        self.assertEqual(r["recomputed"]["status"], "WIN")

    def test_a_later_correction_is_reported(self):
        S._cache["1"] = box(skaters=[(1, 2, 0, 0)])
        saved = {"leg_results": [{"status": "RESOLVED", "actual_value": 1, "outcome_hit": False}]}
        r = S.audit_row("t", row([leg(1)], "LOSS", -10.0, -125.0, settlement=saved))
        self.assertFalse(r["agrees"])
        self.assertTrue(any("correction" in p for p in r["problems"]))


if __name__ == "__main__":
    unittest.main()
