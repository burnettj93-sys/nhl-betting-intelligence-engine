"""Player identity and matchup integrity: ambiguous names, official-roster mismatches, wrong-game players, the recording gate and the join in the builder and the legs.
Regression for 2026-10-18/19: Vancouver has two Elias Pettersons and the roster index kept one of them silently; three players in our roster were not on the official rosters."""
from __future__ import annotations

import datetime as dt
import os
import unittest
from unittest import mock

from operational import best_bets as bb
from operational import player_integrity as pi
from operational import slate_board as sb
from tests.test_slate_board import NOW, avail, pool, product

OFFICIAL_AT = dt.datetime(2026, 10, 8, 20, 0, tzinfo=dt.timezone.utc)


def roster(**players):
    return {"fetched_at_utc": "2026-10-08T20:00:00Z", "players": players, "error": None}


class TestVerdicts(unittest.TestCase):
    def test_a_player_on_the_current_official_roster_is_ok_and_a_missing_one_is_a_mismatch(self):
        docs = {"VAN": roster(**{"8480012": "Elias Pettersson"})}
        now = OFFICIAL_AT + dt.timedelta(minutes=30)
        self.assertEqual(pi.verdict("8480012", "VAN", now, name="Elias Pettersson", rosters=docs)["status"], pi.OK)
        self.assertEqual(pi.verdict("1", "VAN", now, rosters=docs)["status"], pi.MISMATCH)
        self.assertEqual(pi.verdict("8480012", "VAN", now, name="Quinn Hughes", rosters=docs)["status"], pi.MISMATCH)          # the id belongs to another person

    def test_without_a_usable_official_roster_it_is_unverified_not_a_mismatch(self):
        self.assertEqual(pi.verdict("1", "VAN", OFFICIAL_AT, rosters={})["status"], pi.UNVERIFIED)
        old = {"VAN": roster(**{"1": "X Y"})}
        self.assertEqual(pi.verdict("1", "VAN", OFFICIAL_AT + dt.timedelta(hours=40), rosters=old)["status"], pi.UNVERIFIED)

    def test_two_players_with_one_name_are_ambiguous_and_withdrawn(self):
        self.assertEqual(pi.duplicate_names({"8480012": "Elias Pettersson", "8483678": "Elias Pettersson", "1": "Quinn Hughes"}), {"elias pettersson": ["8480012", "8483678"]})
        pi.record_ambiguous("2026-10-20", [{"team": "VAN", "name": "Elias Pettersson", "ids": ["8480012", "8483678"]}])
        game = {"home": "VAN", "away": "CAR"}
        c = pi.check_selection("8480012", "Elias Pettersson", "VAN", game, "2026-10-20", OFFICIAL_AT)
        self.assertEqual(c["status"], pi.AMBIGUOUS)
        self.assertTrue(pi.blocking(c["status"]))

    def test_a_player_whose_team_is_not_in_the_game_is_the_wrong_game(self):
        self.assertEqual(pi.check_selection("1", "A B", "TOR", {"home": "CBJ", "away": "MIN"}, "2026-10-18", OFFICIAL_AT)["status"], pi.WRONG_GAME)

    def test_a_failed_refresh_keeps_the_last_good_copy_and_says_so(self):
        now = OFFICIAL_AT
        self.assertEqual(pi.refresh_official(["VAN"], now, fetch=lambda t: {"1": "A B"}), {"VAN": "FETCHED"})
        later = now + dt.timedelta(hours=4)
        out = pi.refresh_official(["VAN"], later, fetch=mock.Mock(side_effect=OSError("down")))
        self.assertEqual(out, {"VAN": "FAILED_KEPT_LAST_GOOD"})
        self.assertEqual(pi.verdict("1", "VAN", later, rosters=pi._load(pi.ROSTER_STATE))["status"], pi.OK)           # the old copy is still evidence (inside 36 h)

    def test_a_current_copy_is_not_fetched_again(self):
        pi.refresh_official(["CHI"], OFFICIAL_AT, fetch=lambda t: {"1": "A B"})
        self.assertEqual(pi.refresh_official(["CHI"], OFFICIAL_AT + dt.timedelta(minutes=30), fetch=lambda t: self.fail("current")), {"CHI": "CURRENT"})


class TestNames(unittest.TestCase):
    def test_mangled_names_match_the_nhl_spelling_and_different_people_do_not(self):
        for mangled, real in (("Alexis Lafrenire", "Alexis Lafrenière"), ("Tim Sttzle", "Tim Stützle"), ("Juraj Slafkovsk", "Juraj Slafkovský"), ("Oskar Bck", "Oskar Bäck"),
                              ("Felix Unger Srum", "Felix Unger Sörum")):
            self.assertTrue(pi.names_compatible(mangled, real), mangled)
        self.assertFalse(pi.names_compatible("Sean Walker", "Sean Walters"))
        self.assertFalse(pi.names_compatible("Sebastian Aho", "Sebastian Ahlberg"))

    def test_the_players_table_is_the_name_of_record(self):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE players (player_id TEXT, full_name TEXT)")
        c.execute("INSERT INTO players VALUES ('8482109', 'Alexis Lafrenière'), ('9', NULL)")
        self.assertEqual(pi.official_names(c), {"8482109": "Alexis Lafrenière"})
        self.assertEqual(bb.norm_name(pi.official_names(c)["8482109"]), "alexis lafreniere")                 # what the provider's own spelling normalises to

    def test_a_refresh_is_paced_and_capped_so_the_nhl_rate_limit_is_not_hit(self):
        teams = [f"T{i:02d}" for i in range(25)]
        out = pi.refresh_official(teams, OFFICIAL_AT, fetch=lambda t: {"1": "A B"}, max_fetches=10)
        self.assertEqual(sum(1 for v in out.values() if v == "FETCHED"), 10)
        self.assertEqual(sum(1 for v in out.values() if v == "DEFERRED_TO_NEXT_CYCLE"), 15)
        out2 = pi.refresh_official(teams, OFFICIAL_AT + dt.timedelta(minutes=15), fetch=lambda t: {"1": "A B"}, max_fetches=10)
        self.assertEqual(sum(1 for v in out2.values() if v == "FETCHED"), 10)                              # the next cycle takes the teams that had no copy first


class TestTheModelJoin(unittest.TestCase):
    def test_a_second_player_under_the_same_name_withdraws_both_instead_of_overwriting(self):
        model, coll = {}, {}
        bb._put_player(model, coll, "elias pettersson|VAN", {"player_id": "8480012", "name": "Elias Pettersson", "team": "VAN"})
        bb._put_player(model, coll, "elias pettersson|VAN", {"player_id": "8483678", "name": "Elias Pettersson", "team": "VAN"})
        bb._put_player(model, coll, "quinn hughes|VAN", {"player_id": "1", "name": "Quinn Hughes", "team": "VAN"})
        self.assertEqual(sorted(model), ["quinn hughes|VAN"])
        bb._withdraw_collisions(model, coll, "2026-10-20")
        self.assertEqual(pi.ambiguous_for("2026-10-20")[0]["ids"], ["8480012", "8483678"])
        self.assertTrue(pi.is_ambiguous("2026-10-20", "VAN", "Elias Pettersson"))


class TestTheBoardWithdrawsWrongPlayers(unittest.TestCase):
    def test_a_player_not_on_the_official_roster_is_withdrawn_from_scorers_and_shots(self):
        p = product()
        now = NOW
        pi._save(pi.ROSTER_STATE, {"AAA": {"fetched_at_utc": "2026-10-08T21:00:00Z", "players": {"P3": "Test Skater Three"}, "error": None},
                                   "BBB": {"fetched_at_utc": "2026-10-08T21:00:00Z", "players": {"P2": "Test Skater Two"}, "error": None}})     # P1 (AAA) is NOT on AAA's official roster
        d = sb.build_doc(now, p, pool(), {}, avail(), {}, None)
        g = d["games"][0]
        self.assertEqual([w["name"] for w in g["coverage"]["integrity"]["withdrawn"]], ["Test Skater One"])
        self.assertEqual(g["coverage"]["integrity"]["withdrawn"][0]["status"], pi.MISMATCH)
        self.assertNotIn("Test Skater One", [s["name"] for s in g["scorers"]])
        self.assertNotIn("Test Skater One", [c["name"] for c in g["shots"]["picks"]])

    def test_with_no_official_rosters_players_are_shown_but_counted_unverified(self):
        pi._save(pi.ROSTER_STATE, {})
        g = sb.build_doc(NOW, product(), pool(), {}, avail(), {}, None)["games"][0]
        self.assertEqual(g["coverage"]["integrity"]["withdrawn"], [])
        self.assertGreater(g["coverage"]["integrity"]["unverified_players"], 0)


class TestTheRecordingGate(unittest.TestCase):
    def combo(self, pid="P1", team="AAA", name="Test Skater One"):
        leg = mock.Mock(participant_id=pid, team=team, participant_name=name, provisional=False, provider_start_utc=None, game_start_utc="2026-10-09T23:00:00Z", quote_updated_utc=None,
                        retrieved_at_utc=None, captured_at_utc=None)
        return mock.Mock(legs=[leg])

    def test_an_unverified_or_mismatched_leg_is_never_recorded_when_the_gate_is_on(self):
        from operational import daily_tickets as dtk
        pi._save(pi.ROSTER_STATE, {"AAA": {"fetched_at_utc": "2026-10-08T21:00:00Z", "players": {"P1": "Test Skater One"}, "error": None}})
        with mock.patch.dict(os.environ, {pi.ENV_GATE: "on"}), mock.patch("research.real_market_parlay.engine.leg_label", return_value="Test Skater One 2+"):
            ok = dtk.revalidate_before_recording(self.combo(), NOW)
            bad = dtk.revalidate_before_recording(self.combo(pid="P9"), NOW)
            none = dtk.revalidate_before_recording(self.combo(team="CCC", pid="P1"), NOW)
        self.assertFalse([r for r in ok if "identity" in r])
        self.assertTrue(any("MISMATCH" in r for r in bad))
        self.assertTrue(any("UNVERIFIED" in r for r in none))


if __name__ == "__main__":
    unittest.main()
