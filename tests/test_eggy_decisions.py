"""Decision review (operational/eggy_decisions.py): a bet has two results, the OUTCOME (win/loss) and the DECISION (good/questionable/bad), and the second is judged from the pre-bet review alone.

Pinned here: the decision never reads the outcome; a bad decision that wins is luck and teaches nothing; a good decision that loses is NORMAL_VARIANCE only when that is earned, and says why; the factual
claims in a thesis are checked against what happened; a bet recorded before the review existed is reconstructed from its own day's data and labelled as such."""
from __future__ import annotations

import dataclasses
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
from operational import eggy_decisions as ed
from operational import eggy_intelligence as ei
from tests.test_eggy_intelligence import base_leg, make_ctx, ml_leg

GOOD, QUESTIONABLE, BAD, INSUFFICIENT = ed.GOOD, ed.QUESTIONABLE, ed.BAD, ed.INSUFFICIENT


def card(action="RECORD", thesis="MODEL_SEES_VALUE", contradiction="LOW", cal="HIGH", completeness=0.9, kind="PLAYER", estimate=0.6, claims=None, flags=None, favourite=None, elo=None):
    c = {"version": "t", "kind": kind, "thesis": {"type": thesis, "statement": "s", "strength": "MODERATE"}, "contradiction": {"level": contradiction, "flags": flags or []},
         "calibration_confidence": {"level": cal}, "data_completeness": {"fraction": completeness}, "action": {"decision": action, "reasons": ["because"]}, "estimate_used": estimate,
         "claims": claims if claims is not None else [], "probabilities": {"disagreement": 0.05, "elo_not_used": elo}}
    if favourite:
        c["hockey_view"] = {"favourite": favourite, "p_favourite": 0.64}
    return c


class TestDecisionQuality(unittest.TestCase):
    def test_a_surviving_value_thesis_with_calibration_is_a_good_decision(self):
        for contradiction in ("LOW", "MODERATE"):
            for cal in ("HIGH", "MEDIUM"):
                self.assertEqual(ed.decision_quality(card(contradiction=contradiction, cal=cal))["label"], GOOD)

    def test_a_pass_a_severe_contradiction_or_no_thesis_is_a_bad_decision(self):
        self.assertEqual(ed.decision_quality(card(action="PASS", thesis="FAIR_PRICED", contradiction="HIGH"))["label"], BAD)
        self.assertEqual(ed.decision_quality(card(contradiction="SEVERE"))["label"], BAD)
        q = ed.decision_quality(card(action="PASS", thesis="UNDERDOG_AT_FAIR_PRICE", contradiction="HIGH", kind="MONEYLINE"))
        self.assertEqual(q["label"], BAD)
        self.assertIn("fair-priced underdog", q["reasons"][0])
        q = ed.decision_quality(card(action="PASS", thesis="MODEL_SAYS_OVERPRICED", contradiction="SEVERE", kind="MONEYLINE"))
        self.assertIn("overpriced", " ".join(q["reasons"]))

    def test_a_control_or_an_experiment_is_questionable_not_good(self):
        self.assertEqual(ed.decision_quality(card(action="LOWER_PRIORITY", thesis="FAIR_PRICED"))["label"], QUESTIONABLE)
        self.assertEqual(ed.decision_quality(card(action="LOWER_PRIORITY", thesis="PRICE_ABOVE_ESTIMATE"))["label"], QUESTIONABLE)
        self.assertEqual(ed.decision_quality(card(action="RECORD", thesis="MODEL_SEES_VALUE", contradiction="HIGH"))["label"], QUESTIONABLE)

    def test_too_little_to_judge_is_said_not_guessed(self):
        self.assertEqual(ed.decision_quality(card(cal="INSUFFICIENT"))["label"], INSUFFICIENT)
        self.assertEqual(ed.decision_quality(card(completeness=0.3))["label"], INSUFFICIENT)
        self.assertEqual(ed.decision_quality({})["label"], INSUFFICIENT)
        self.assertEqual(ed.decision_quality({"kind": "ERROR"})["label"], INSUFFICIENT)

    def test_the_decision_is_judged_before_and_without_the_outcome(self):
        self.assertEqual(list(inspect.signature(ed.decision_quality).parameters), ["card"])
        self.assertNotIn("result", inspect.getsource(ed.decision_quality))


class TestWhatToLearn(unittest.TestCase):
    def sig(self, quality, outcome, claims=None, p=0.4, cal="HIGH", defect=False):
        return ed.learning_signal(quality, outcome, claims or [], p, cal, defect)

    def test_a_good_decision_that_won_is_the_expected_reward_and_changes_nothing(self):
        s = self.sig(GOOD, "WIN")
        self.assertEqual((s["signal"], s["confidence"]), ("GOOD_DECISION_WON", "UNCHANGED"))

    def test_a_bad_decision_that_won_is_luck_and_must_not_raise_confidence(self):
        s = self.sig(BAD, "WIN")
        self.assertEqual((s["signal"], s["confidence"]), ("BAD_DECISION_WON_LUCK", "NOT_INCREASED"))
        self.assertIn("LUCK", s["text"])
        s = self.sig(QUESTIONABLE, "WIN")
        self.assertEqual(s["confidence"], "NOT_INCREASED")

    def test_a_bad_decision_that_lost_is_a_process_failure_and_the_loss_is_not_blamed_for_losing(self):
        s = self.sig(BAD, "LOSS", p=0.14)
        self.assertEqual(s["signal"], "BAD_DECISION_LOST")
        self.assertFalse(s["variance"])
        self.assertIn("likelier outcome", s["text"])
        self.assertIn("86%", s["text"])

    def test_a_questionable_decision_that_lost_is_neither_punished_nor_excused(self):
        s = self.sig(QUESTIONABLE, "LOSS")
        self.assertEqual((s["signal"], s["confidence"], s["variance"]), ("QUESTIONABLE_DECISION_LOST", "UNCHANGED", False))

    def test_normal_variance_is_earned_and_explained(self):
        held = [{"id": "expected_toi", "claim": "expected 18 minutes", "verdict": "HELD", "note": "played 18.4"}]
        s = self.sig(GOOD, "LOSS", claims=held, p=0.62, cal="HIGH")
        self.assertEqual((s["signal"], s["variance"], s["confidence"]), ("GOOD_DECISION_LOST_NORMAL_VARIANCE", True, "UNCHANGED"))
        for must in ("earned", "defensible", "calibration was HIGH", "data was right", "1 factual claim(s) held and none failed", "62%"):
            self.assertIn(must, s["text"])

    def test_normal_variance_is_not_available_when_any_condition_fails(self):
        failed = [{"id": "expected_pp", "claim": "expected 2.4 PP minutes", "verdict": "FAILED", "note": "had 0.00"}]
        for kw, why in (({"claims": failed}, "a factual claim failed"), ({"cal": "LOW"}, "calibration was LOW"), ({"defect": True}, "data integrity defect"), ({"p": 0.05}, "5% event")):
            s = self.sig(GOOD, "LOSS", **kw)
            self.assertEqual(s["signal"], "GOOD_DECISION_LOST_NOT_VARIANCE", kw)
            self.assertFalse(s["variance"], kw)
            self.assertIn(why, s["text"], kw)
            self.assertEqual(s["confidence"], "UNCHANGED_PENDING_INVESTIGATION")

    def test_a_loss_the_estimate_itself_called_rare_is_not_ordinary_variance(self):
        self.assertEqual(ed.SURPRISE_FLOOR, 0.10)
        self.assertTrue(self.sig(GOOD, "LOSS", p=0.10)["variance"])           # exactly the floor still counts as ordinary
        self.assertFalse(self.sig(GOOD, "LOSS", p=0.0999)["variance"])

    def test_nothing_is_learned_from_an_unsettled_or_unjudgeable_bet(self):
        for outcome in ("PENDING", "VOID", "UNRESOLVED", None):
            self.assertEqual(self.sig(GOOD, outcome)["signal"], "NOT_SETTLED")
        self.assertEqual(self.sig(INSUFFICIENT, "WIN")["signal"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(self.sig(INSUFFICIENT, "LOSS")["confidence"], "UNCHANGED")


# ------------------------------------------------------------------------------------------------ the facts
def nhl_with_game(home_score=3, away_score=6, state="FINAL"):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("VAN", "CAR"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    conn.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, home_score, away_score, final_period_type, source) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (2026020151, "20262027", "2026-10-20", "2026-10-21T02:00:00", "VAN", "CAR", "2026-10-20T00:00:00", state, home_score, away_score, "REG", "test"))
    conn.commit()
    return conn


def mp_skater(pid, toi, pp, shots=0, goals=0, assists=0):
    return {"player_id": pid, "game_id": 2026020151, "toi": toi, "toi_pp": pp, "shots": shots, "goals": goals, "assists": assists, "points": goals + assists}


def mp_team(team, xgf, xga):
    return {"g": "2026020151", "t": team, "xgf": xgf, "xga": xga, "cf5": 50, "ca5": 45, "hdf": 1.0, "hda": 0.8}


BET = {"game_id": 2026020151, "participant_id": "8480000", "market_family": "PLAYER_GOALS", "threshold": 1, "team": "VAN", "result_status": "LOSS"}
PLAYER_CLAIMS = [{"id": "expected_toi", "text": "", "value": 16.0}, {"id": "expected_pp", "text": "", "value": 2.0}, {"id": "expected_stat", "text": "", "value": 0.2}, {"id": "team_win_probability", "text": "", "value": 0.3}]


class TestFacts(unittest.TestCase):
    def setUp(self):
        self.nhl = nhl_with_game()
        self.addCleanup(self.nhl.close)

    def test_the_game_is_known_only_when_it_is_final(self):
        idx = ed.index_rows([], [], [2026020151])
        f = ed.facts_for(BET, card(), self.nhl, idx)
        self.assertEqual((f["game"]["winner"], f["game"]["home_score"], f["game"]["away_score"]), ("CAR", 3, 6))
        live = nhl_with_game(state="LIVE")
        self.addCleanup(live.close)
        self.assertIsNone(ed.facts_for(BET, card(), live, idx))
        self.assertIsNone(ed.facts_for(BET, card(), None, idx))

    def test_the_players_game_comes_from_the_moneypuck_log_or_the_box_score(self):
        idx = ed.index_rows([mp_skater("8480000", 15.4, 0.0, shots=2)], [], [2026020151])
        p = ed.facts_for(BET, card(), self.nhl, idx)["player"]
        self.assertEqual((p["source"], p["toi"], p["toi_pp"], p["shots"]), ("MoneyPuck game log", 15.4, 0.0, 2.0))
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES (2026020151,'8480001','VAN',12.5,0,1,1,1,1,'x','x','test')")
        self.nhl.commit()
        b = dict(BET, participant_id="8480001")
        p = ed.facts_for(b, card(), self.nhl, ed.index_rows([], [], [2026020151]))["player"]
        self.assertEqual((p["source"], p["toi"], p["points"]), ("NHL box score", 12.5, 1))
        self.assertIsNone(ed.facts_for(dict(BET, participant_id="1"), card(), self.nhl, idx)["player"])

    def test_team_chances_are_attached_when_both_teams_rows_exist(self):
        idx = ed.index_rows([], [mp_team("VAN", 2.1, 3.9), mp_team("CAR", 3.9, 2.1)], [2026020151])
        f = ed.facts_for(BET, card(kind="MONEYLINE"), self.nhl, idx)
        self.assertEqual(f["game"]["team_stats"]["CAR"]["xg_for"], 3.9)
        self.assertNotIn("team_stats", ed.facts_for(BET, card(kind="MONEYLINE"), self.nhl, ed.index_rows([], [mp_team("VAN", 2.1, 3.9)], [2026020151]))["game"])

    def test_the_index_reads_only_the_games_asked_for(self):
        idx = ed.index_rows([mp_skater("1", 10, 0), {**mp_skater("2", 10, 0), "game_id": 999}], [], [2026020151])
        self.assertEqual(list(idx["skaters"]), [("1", 2026020151)])


class TestClaimsAgainstWhatHappened(unittest.TestCase):
    def setUp(self):
        self.nhl = nhl_with_game()
        self.addCleanup(self.nhl.close)

    def claims(self, toi, pp, **kw):
        idx = ed.index_rows([mp_skater("8480000", toi, pp, **kw)], [], [2026020151])
        c = card(claims=PLAYER_CLAIMS)
        facts = ed.facts_for(BET, c, self.nhl, idx)
        return {x["id"]: x for x in ed.review_claims(c, BET, facts)}

    def test_ice_time_held_partly_held_or_failed(self):
        self.assertEqual(self.claims(16.5, 2.0)["expected_toi"]["verdict"], "HELD")
        self.assertEqual(self.claims(18.5, 2.0)["expected_toi"]["verdict"], "HELD")
        self.assertEqual(self.claims(22.0, 2.0)["expected_toi"]["verdict"], "HELD")           # far MORE ice time than expected only supports the bet (the note still shows the gap)
        self.assertIn("+6.0", self.claims(22.0, 2.0)["expected_toi"]["note"])
        self.assertEqual(self.claims(12.0, 2.0)["expected_toi"]["verdict"], "FAILED")
        self.assertEqual(self.claims(13.5, 2.0)["expected_toi"]["verdict"], "PARTLY_HELD")

    def test_a_power_play_role_that_did_not_exist_is_a_failed_claim(self):
        self.assertEqual(self.claims(16.0, 0.0)["expected_pp"]["verdict"], "FAILED")
        self.assertEqual(self.claims(16.0, 1.2)["expected_pp"]["verdict"], "HELD")
        self.assertEqual(self.claims(16.0, 0.4)["expected_pp"]["verdict"], "PARTLY_HELD")

    def test_a_negligible_expected_power_play_is_not_a_claim_worth_checking(self):
        c = card(claims=[{"id": "expected_pp", "text": "", "value": 0.3}, {"id": "expected_toi", "text": "", "value": 16.0}])
        idx = ed.index_rows([mp_skater("8480000", 16.0, 0.0)], [], [2026020151])
        got = {x["id"] for x in ed.review_claims(c, BET, ed.facts_for(BET, c, self.nhl, idx))}
        self.assertNotIn("expected_pp", got)

    def test_what_the_player_and_the_team_did_is_information_not_a_verdict(self):
        got = self.claims(16.0, 2.0, shots=3, goals=0)
        self.assertEqual((got["expected_stat"]["verdict"], got["team_result"]["verdict"]), ("INFORMATION", "INFORMATION"))
        self.assertIn("VAN 3, CAR 6", got["team_result"]["note"])

    def test_a_player_who_did_not_play_fails_the_claim_that_he_would(self):
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES (2026020151,'8480000','VAN',0,0,0,0,0,1,'x','x','test')")
        self.nhl.commit()
        c = card(claims=PLAYER_CLAIMS)
        out = ed.review_claims(c, BET, ed.facts_for(BET, c, self.nhl, ed.index_rows([], [], [2026020151])))
        self.assertEqual((out[0]["id"], out[0]["verdict"]), ("played", "FAILED"))

    def test_without_the_games_data_every_claim_reads_not_testable(self):
        out = ed.review_claims(card(), BET, None)
        self.assertEqual([x["verdict"] for x in out], ["NOT_TESTABLE"])

    def test_a_moneyline_favourite_is_judged_on_the_chances_not_only_the_score(self):
        mlbet = dict(BET, market_family="MONEYLINE", participant_id="VAN", team="VAN")
        c = card(kind="MONEYLINE", favourite="CAR", claims=[])
        won = ed.facts_for(mlbet, c, self.nhl, ed.index_rows([], [mp_team("VAN", 2.1, 3.9), mp_team("CAR", 3.9, 2.1)], [2026020151]))
        got = {x["id"]: x for x in ed.review_claims(c, mlbet, won)}
        self.assertEqual(got["favourite"]["verdict"], "HELD")                            # CAR won
        self.assertEqual(got["disagreement"]["verdict"], "NOT_TESTABLE")                  # one game cannot show a model-market gap means anything
        lost_nhl = nhl_with_game(home_score=2, away_score=1)
        self.addCleanup(lost_nhl.close)
        out = ed.review_claims(c, mlbet, ed.facts_for(mlbet, c, lost_nhl, ed.index_rows([], [mp_team("VAN", 1.5, 3.5), mp_team("CAR", 3.5, 1.5)], [2026020151])))
        self.assertEqual({x["id"]: x for x in out}["favourite"]["verdict"], "HELD")      # CAR lost 1-2 but out-chanced VAN: the thesis about who was better held
        out = ed.review_claims(c, mlbet, ed.facts_for(mlbet, c, lost_nhl, ed.index_rows([], [mp_team("VAN", 3.5, 1.5), mp_team("CAR", 1.5, 3.5)], [2026020151])))
        self.assertEqual({x["id"]: x for x in out}["favourite"]["verdict"], "FAILED")


class TestAssess(unittest.TestCase):
    def setUp(self):
        self.nhl = nhl_with_game()
        self.addCleanup(self.nhl.close)

    def test_a_bad_longshot_decision_that_lost_names_the_flaw_and_asks_for_investigation(self):
        c = card(action="PASS", thesis="FAIR_PRICED", contradiction="HIGH", estimate=0.14, claims=PLAYER_CLAIMS,
                 flags=[{"name": "HISTORY_SAYS_MODEL_RUNS_HIGH", "level": "HIGH", "text": "history"}])
        idx = ed.index_rows([mp_skater("8480000", 11.0, 0.0)], [], [2026020151])
        a = ed.assess(BET, c, ed.facts_for(BET, c, self.nhl, idx))
        self.assertEqual(a["decision_quality"]["label"], BAD)
        self.assertEqual((a["lesson"]["signal"], a["outcome"]), ("BAD_DECISION_LOST", "LOSS"))
        self.assertEqual(a["classification"], "TOI_PROJECTION_ERROR")                         # the ice time failed: the most specific cause wins
        self.assertEqual(a["recommended_action"], "INVESTIGATE")
        self.assertAlmostEqual(a["p_outcome"], 0.86, places=2)

    def test_a_good_decision_that_lost_to_ordinary_variance_is_classified_earned_normal_variance(self):
        c = card(estimate=0.62, claims=PLAYER_CLAIMS)
        idx = ed.index_rows([mp_skater("8480000", 16.5, 2.2, shots=1)], [], [2026020151])
        a = ed.assess(dict(BET, market_family="PLAYER_SOG_ALTERNATE"), c, ed.facts_for(BET, c, self.nhl, idx))
        self.assertEqual((a["decision_quality"]["label"], a["classification"], a["recommended_action"]), (GOOD, "NORMAL_VARIANCE", "NO_ACTION"))
        self.assertIn("earned", a["why"])

    def test_a_good_decision_that_lost_with_a_failed_claim_is_not_variance(self):
        c = card(estimate=0.62, claims=PLAYER_CLAIMS)
        idx = ed.index_rows([mp_skater("8480000", 16.5, 0.0)], [], [2026020151])                  # no power play at all
        a = ed.assess(BET, c, ed.facts_for(BET, c, self.nhl, idx))
        self.assertNotEqual(a["classification"], "NORMAL_VARIANCE")
        self.assertEqual((a["classification"], a["recommended_action"]), ("ROLE_CHANGE_MISSED", "INVESTIGATE"))

    def test_a_bad_decision_that_won_is_a_lucky_win(self):
        c = card(action="PASS", thesis="FAIR_PRICED", contradiction="HIGH", estimate=0.14)
        a = ed.assess(dict(BET, result_status="WIN"), c, None)
        self.assertEqual((a["classification"], a["lesson"]["signal"], a["recommended_action"]), ("LUCKY_WIN", "BAD_DECISION_WON_LUCK", "WATCH"))

    def test_a_good_decision_that_won_needs_no_classification(self):
        a = ed.assess(dict(BET, result_status="WIN"), card(), None)
        self.assertEqual((a["classification"], a["recommended_action"]), (None, "NO_ACTION"))

    def test_a_pending_bet_is_judged_but_not_classified(self):
        a = ed.assess(dict(BET, result_status="PENDING"), card(), None)
        self.assertEqual((a["decision_quality"]["label"], a["lesson"]["signal"], a["classification"]), (GOOD, "NOT_SETTLED", None))

    def test_a_moneyline_bet_on_an_estimate_that_was_not_the_best_available_one_is_a_model_calibration_loss(self):
        c = card(action="PASS", thesis="MODEL_SAYS_OVERPRICED", contradiction="SEVERE", kind="MONEYLINE", favourite="CAR", elo=0.418, estimate=0.282)
        a = ed.assess(dict(BET, market_family="MONEYLINE", participant_id="VAN"), c, None)
        self.assertEqual(a["classification"], "MODEL_CALIBRATION")
        self.assertIn("Elo said 42%", a["why"])

    def test_the_summary_counts_the_grid_the_lucky_wins_and_the_earned_variance(self):
        rows = [ed.assess(dict(BET, result_status=r), c, None) for r, c in (("WIN", card(action="PASS", thesis="FAIR_PRICED", contradiction="HIGH")), ("LOSS", card(action="PASS", thesis="FAIR_PRICED", contradiction="HIGH")),
                                                                              ("LOSS", card(estimate=0.62)), ("WIN", card()))]
        s = ed.summarize(rows)
        self.assertEqual((s["lucky_wins"], s["process_failures"], s["earned_normal_variance"]), (1, 1, 1))
        self.assertIn({"decision": BAD, "outcome": "LOSS", "n": 1}, s["grid"])
        self.assertIn({"decision": GOOD, "outcome": "WIN", "n": 1}, s["grid"])


class TestLegacyBets(unittest.TestCase):
    """A bet recorded before the review existed is reviewed from its own day's data, and labelled as reconstructed."""

    def bet(self, leg, conservative=0.282, basis="MARKET_NO_VIG"):
        return {"leg_json": json.dumps(dataclasses.asdict(leg), default=str), "et_date": "2026-10-20", "created_at_utc": "2026-10-20T23:02:00Z", "conservative_probability": conservative, "probability_basis": basis,
                "market_family": leg.market_family, "participant_id": leg.participant_id}

    def test_the_card_is_reconstructed_with_the_old_engines_own_estimate_and_says_so(self):
        ctx = make_ctx()
        leg = ml_leg(ctx, "BBB", 0.0, elo=0.418)
        with mock.patch.object(ei, "build_context", return_value=ctx) as bc:
            c = ed.reconstruct_card(None, self.bet(leg), {})
        self.assertTrue(c["reconstructed"])
        self.assertIn("not the reasoning the engine used", c["reconstruction_note"])
        self.assertEqual(c["estimate_used"], 0.282)                                       # what the old engine itself used for this bet
        self.assertEqual(c["thesis"]["type"], "UNDERDOG_AT_FAIR_PRICE")
        self.assertEqual(c["action"]["decision"], "PASS")
        self.assertEqual(bc.call_args.kwargs["as_of"], "2026-10-20")                      # built from data before that day, never later

    def test_the_context_is_built_once_per_day(self):
        ctx = make_ctx()
        cache = {}
        with mock.patch.object(ei, "build_context", return_value=ctx) as bc:
            for team in ("AAA", "BBB"):
                ed.reconstruct_card(None, self.bet(ml_leg(ctx, team)), cache)
        self.assertEqual(bc.call_count, 1)

    def test_a_bet_with_no_readable_leg_has_no_card(self):
        self.assertIsNone(ed.reconstruct_card(None, {"leg_json": "not json", "et_date": "2026-10-20", "created_at_utc": "2026-10-20T23:02:00Z", "conservative_probability": 0.5}, {}))


if __name__ == "__main__":
    unittest.main()
