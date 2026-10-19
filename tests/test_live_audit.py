"""The live acceptance audit's own checks, on doctored snapshots (the audit itself runs on production data)."""
from __future__ import annotations

import copy
import datetime as dt
import unittest

import operational.system_health  # noqa: F401
from operational import live_audit as la
from operational import player_integrity as pi
from operational import state_paths


def _clean_integrity_state():
    for name in (pi.ROSTER_STATE, pi.AMBIGUOUS_STATE):                      # these tests write official rosters and ambiguity lists: never leave them for other tests in the process
        state_paths.path(name).unlink(missing_ok=True)

NOW = dt.datetime(2026, 10, 18, 23, 30, tzinfo=dt.timezone.utc)


def snap():
    q = "2026-10-18T23:20:00Z"
    return {"slate_board": {"date_et": "2026-10-18", "generated_at_utc": "2026-10-18T23:27:32Z", "games": [{
        "game_id": "1", "away": "CBJ", "home": "MIN", "state": "SCHEDULED",
        "scorers": [{"name": "Kirill Kaprizov", "team": "MIN", "player_id": "8478864", "price": {"american": -115.0, "quote_updated_utc": q}}],
        "shots": {"picks": [{"name": "Blake Coleman", "team": "MIN", "player_id": "8476399", "chosen": {"quote_updated_utc": q}, "ladder": [{"threshold": 2, "american": -175.0}]}]},
        "coverage": {"price_state": "FRESH_PRICES", "integrity": {"withdrawn": [], "unverified_players": 0}}}]},
        "builder_pool": {"generated_at_utc": "2026-10-18T23:27:32Z", "games": {"1": {"players": {
            "8478864": {"n": "Kirill Kaprizov", "t": "MIN", "m": {"GOAL": {"q": q, "l": [[1, -115.0, 0.4]]}}}, "8476399": {"n": "Blake Coleman", "t": "MIN", "m": {"SOG": {"q": q, "l": [[2, -175.0, 0.6]]}}}}}}},
        "tickets": {"generated_at_utc": "2026-10-18T23:27:32Z", "options": {"options": []}}, "metadata": {"generated_at": "2026-10-18T23:27:00Z"}}


class TestAuditChecks(unittest.TestCase):
    def setUp(self):
        _clean_integrity_state()
        self.addCleanup(_clean_integrity_state)

    def rosters(self, mp=True):
        pi._save(pi.ROSTER_STATE, {"MIN": {"fetched_at_utc": "2026-10-18T22:00:00Z", "players": {"8478864": "Kirill Kaprizov", "8476399": "Blake Coleman"} if mp else {}, "error": None},
                                   "CBJ": {"fetched_at_utc": "2026-10-18T22:00:00Z", "players": {"1": "X Y"}, "error": None}})

    def test_correct_selections_pass_and_a_wrong_player_fails(self):
        self.rosters()
        ok = la.check_integrity(snap(), NOW)
        self.assertEqual([c["status"] for c in ok], [la.PASS, la.PASS])
        bad = snap()
        bad["slate_board"]["games"][0]["scorers"].append({"name": "Matthew Knies", "team": "TOR", "player_id": "8482720", "price": None})
        res = la.check_integrity(bad, NOW)
        self.assertEqual(res[0]["status"], la.FAIL)
        self.assertIn("WRONG_GAME", res[0]["detail"])

    def test_builder_players_and_slip_legs_are_checked_too_and_unverifiable_is_never_a_pass(self):
        self.rosters()
        s = snap()
        s["builder_pool"]["games"]["1"]["players"]["8478864"].update(n="Kirill Kaprizov", t="MIN")
        s["builder_pool"]["games"]["1"]["players"]["999"] = {"n": "Not A Minnesota Player", "t": "MIN", "m": {}}
        res = la.check_integrity(s, NOW)
        self.assertEqual(res[0]["status"], la.FAIL)
        self.assertIn("Not A Minnesota Player", res[0]["detail"])
        s2 = snap()
        s2["tickets"]["options"] = {"options": [{"legs": [{"participant_id": "8482720", "participant_name": "Matthew Knies", "team": "MIN"}, {"participant_id": "BOS", "participant_name": "BOS", "team": "BOS"}]}]}
        res2 = la.check_integrity(s2, NOW)
        self.assertEqual(res2[0]["status"], la.FAIL)                                                  # Knies is not on MIN's roster; the team-code leg is not a player and is skipped
        pi._save(pi.ROSTER_STATE, {})
        res3 = la.check_integrity(snap(), NOW)
        self.assertEqual(res3[1]["status"], la.WARN)                                                  # nothing could be verified: a warning, not a pass

    def test_nothing_compared_is_a_warning_not_a_pass(self):
        s = snap()
        s["builder_pool"]["games"] = {}
        self.assertEqual(la.check_surfaces(s)[0]["status"], la.WARN)

    def test_a_player_missing_from_the_official_roster_fails(self):
        self.rosters(mp=False)
        self.assertEqual(la.check_integrity(snap(), NOW)[0]["status"], la.PASS)                  # an empty roster is UNVERIFIED (reported as a warning), not a wrong player
        self.assertEqual(la.check_integrity(snap(), NOW)[1]["status"], la.WARN)
        pi._save(pi.ROSTER_STATE, {"MIN": {"fetched_at_utc": "2026-10-18T22:00:00Z", "players": {"5": "Someone Else"}, "error": None}})
        self.assertEqual(la.check_integrity(snap(), NOW)[0]["status"], la.FAIL)

    def test_the_surfaces_check_catches_a_price_that_differs_between_the_board_and_the_builder(self):
        self.assertEqual(la.check_surfaces(snap())[0]["status"], la.PASS)
        s = copy.deepcopy(snap())
        s["builder_pool"]["games"]["1"]["players"]["8478864"]["m"]["GOAL"]["l"][0][1] = 105.0
        res = la.check_surfaces(s)[0]
        self.assertEqual(res["status"], la.FAIL)
        self.assertIn("Kaprizov", res["detail"])
        s2 = copy.deepcopy(snap())
        s2["builder_pool"]["games"]["1"]["players"]["8476399"]["m"]["SOG"]["q"] = "x"
        self.assertEqual(la.check_surfaces(s2)[0]["status"], la.PASS)                              # shots are compared by price per threshold

    def test_an_old_quote_labelled_fresh_fails_the_freshness_check(self):
        s = snap()
        s["slate_board"]["games"][0]["start_utc"] = "2026-10-19T00:00:00Z"
        self.assertEqual(la.check_freshness(s, NOW)[0]["status"], la.PASS)                         # the 10-minute-old quote is inside the limit
        s["slate_board"]["games"][0]["scorers"][0]["price"]["quote_updated_utc"] = "2026-10-18T20:00:00Z"        # 3.5 hours old, game 90 minutes away
        self.assertEqual(la.check_freshness(s, NOW)[0]["status"], la.FAIL)


if __name__ == "__main__":
    unittest.main()
