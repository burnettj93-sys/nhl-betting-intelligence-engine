"""The learning cohort driven by the REAL review (not the fake cards of tests/test_learning_cohort.py): a synthetic league and a synthetic player stand in for the data files, everything else is the code that
runs in production -- review_leg, edge_class_of, rank_key, selection, recording, the stored card, the postmortem's reading of it. This is what catches a card whose shape the cohort does not expect."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import unittest
from unittest import mock

from operational import eggy_decisions as ed
from operational import eggy_intelligence as ei
from operational import learning_cohort as lc
from research.product_models import matchup
from tests import test_eggy_intelligence as t
from tests.test_learning_cohort import D1, leg, ml, nhl

TEAMS = ("TOR", "MTL", "EDM", "CHI", "COL", "PHI")          # the teams the cohort tests' temporary schedule knows


CENTRES = {"gf": 3.0, "ga": 3.0, "xgf": 2.85, "xga": 2.85, "hdf": 0.9, "hda": 0.9, "xgf5": 2.1, "xga5": 2.1, "cf5": 50.0, "ca5": 50.0}


def league_rows(days: int = 45, spread: float = 0.06):
    """The synthetic league of tests/test_eggy_intelligence.py with the cohort fixture's team codes: TOR the better team, MTL the worse, the gap compressed (`spread`) to an ordinary NHL one, so that the
    favourite's price is inside the cohort's -150 floor."""
    mapping = dict(zip(("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), TEAMS))
    rows = []
    for r in t.team_rows(days):
        rows.append({**r, "t": mapping[r["t"]], "o": mapping[r["o"]], **{k: c + spread * (r[k] - c) for k, c in CENTRES.items()}})
    return rows


class RealReviewBase(unittest.TestCase):
    def setUp(self):
        self.conn = lc.open_ledger(":memory:")
        self.nhl = nhl()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.nhl.close)
        for p in (mock.patch.object(lc, "is_authorised", return_value=True), mock.patch("operational.learning_postmortem._load_rows", return_value=([], []))):
            p.start()
            self.addCleanup(p.stop)
        ctx = ei.Context(now=D1, as_of="2026-10-20", evidence=matchup.load_evidence())
        ctx.team_state = matchup.build_state(league_rows(), through_date="2026-10-20")
        ctx.skater_state = {"players": {"8478402": t.skater(pid="8478402", team="TOR")}, "newest_game_date": "2026-10-19"}
        ctx.by_player = {"8478402": t.history_rows(pid="8478402")}
        ctx.games = {"1": {"home": "TOR", "away": "MTL", "start_utc": "2026-10-20T23:00:00"}, "2": {"home": "EDM", "away": "CHI", "start_utc": "2026-10-20T23:00:00"}}
        self.ctx = ctx
        p = mock.patch.object(lc.ei, "build_context", return_value=ctx)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("research.product_models.live.project_matchup", return_value=t.projection(0.72))
        p.start()
        self.addCleanup(p.stop)

    def cycle(self, legs, now=D1):
        stamp = lambda m: (now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        legs = [dataclasses.replace(l, quote_updated_utc=stamp(10), retrieved_at_utc=stamp(9), captured_at_utc=stamp(9)) for l in legs]
        return lc.run_cycle(self.nhl, now, {"legs": legs, "sources": {}}, conn=self.conn)

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM bets ORDER BY selection_rank")]

    def fair_ml(self, team, opp, side, game="1"):
        v = ei.team_view(self.ctx, "TOR", "MTL")
        h = v["p_home"] if team == "TOR" else v["p_away"]
        price = float(t.prob_to_american(h * 1.04))
        return dataclasses.replace(ml(team=team, game=game, price=price, p=h, market_probability=h), participant_name=team, opponent=opp, side=side)


class TestRealCardsThroughTheCohort(RealReviewBase):
    def test_a_fair_priced_underdog_is_passed_and_never_recorded_a_fair_priced_favourite_is_a_control(self):
        fav = self.fair_ml("TOR", "MTL", "HOME")
        dog = self.fair_ml("MTL", "TOR", "AWAY")
        out = self.cycle([fav, dog])
        self.assertEqual(out["status"], "OK", out)
        self.assertEqual(self.rows(), [])                                                   # the favourite is LOWER_PRIORITY (waits for its own final window); the underdog is a PASS
        sf = out["recording"]["shortfall"]
        self.assertEqual(sf["lower_priority_waiting_for_their_final_window"], 1)
        self.assertTrue(any("passed on judgment" in k for k in sf["reasons_candidates_were_not_recorded"]))
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)                    # 18:45 ET: inside the last 25 minutes before the 19:00 puck drop
        self.cycle([fav, dog], now=late)
        rows = self.rows()
        self.assertEqual([(r["participant_id"], r["action"], r["thesis_type"]) for r in rows], [("TOR", "LOWER_PRIORITY", "LIKELY_WINNER_AT_FAIR_PRICE")])

    def test_the_stored_card_is_the_real_review_with_every_section_and_the_complete_label(self):
        self.cycle([self.fair_ml("TOR", "MTL", "HOME")], now=dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc))
        r = self.rows()[0]
        card = json.loads(r["intelligence_json"])
        for k in ("bet", "most_likely_outcome", "hockey_view", "probabilities", "market", "case_for", "case_against", "historical", "contradiction", "calibration_confidence", "data_completeness",
                  "decision_confidence", "action", "thesis", "answers", "claims"):
            self.assertIn(k, card, k)
        self.assertEqual(card["bet"]["label"], lc.label_of(r))
        self.assertTrue(card["bet"]["label"].startswith("TOR — MONEYLINE — "))
        self.assertEqual(r["calibration_level"], card["calibration_confidence"]["level"])
        self.assertAlmostEqual(r["conservative_probability"], card["estimate_used"], places=4)
        self.assertEqual(json.loads(r["leg_json"])["market_family"], "MONEYLINE")

    def test_a_prop_the_calibrated_model_likes_is_recorded_at_once_with_its_review(self):
        prop = dataclasses.replace(leg(pid="8478402", price=-110.0, p=0.72, model_probability=0.7, empirical_probability=0.74, empirical_hits=22, empirical_games=30), team="TOR", opponent="MTL")
        out = self.cycle([prop])
        rows = self.rows()
        self.assertEqual(len(rows), 1, out)
        self.assertEqual((rows[0]["action"], rows[0]["thesis_type"], rows[0]["edge_class"]), ("RECORD", "MODEL_SEES_VALUE", "POSITIVE_EDGE"))
        card = json.loads(rows[0]["intelligence_json"])
        self.assertEqual(card["bet"]["label"], "P8478402 — SHOTS ON GOAL 2+ — -110")
        self.assertIn("min on ice", card["answers"]["what_the_model_believes"])

    def test_a_record_ranks_ahead_of_a_lower_priority_control_in_the_same_cycle(self):
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)
        prop = dataclasses.replace(leg(pid="8478402", price=-110.0, p=0.72, model_probability=0.7, empirical_probability=0.74, empirical_hits=22, empirical_games=30), team="TOR", opponent="MTL")
        self.cycle([self.fair_ml("TOR", "MTL", "HOME"), prop], now=late)
        self.assertEqual([(r["selection_rank"], r["action"]) for r in self.rows()], [(1, "RECORD"), (2, "LOWER_PRIORITY")])

    def test_the_postmortem_reads_the_stored_real_card_end_to_end(self):
        from operational import learning_postmortem as lpm
        prop = dataclasses.replace(leg(pid="8478402", price=-110.0, p=0.72, model_probability=0.7, empirical_probability=0.74, empirical_hits=22, empirical_games=30), team="TOR", opponent="MTL")
        self.cycle([prop])
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id='1'")
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1','8478402','TOR',19.0,0,0,1,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')")
        self.nhl.commit()
        lc.settle_due(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        review = next(iter(doc["bet_reviews"].values()))
        a = review["assessment"]
        self.assertEqual((review["source"], review["result_status"], review["facts_complete"]), ("RECORDED", "LOSS", False))          # the box score only: provisional
        self.assertEqual(a["decision_quality"]["label"], ed.GOOD)
        self.assertEqual({c["id"] for c in a["claims"]} & {"expected_toi", "expected_pp", "expected_stat", "team_result"}, {"expected_toi", "expected_pp", "expected_stat", "team_result"})
        self.assertIn(a["lesson"]["signal"], ("GOOD_DECISION_LOST_NORMAL_VARIANCE", "GOOD_DECISION_LOST_NOT_VARIANCE"))
        line = doc["day_bets"][0]
        self.assertEqual(line["bet"], "P8478402 — SHOTS ON GOAL 2+ — -110")

    def test_a_stale_state_stops_the_cohort_instead_of_reviewing_old_form(self):
        self.ctx.as_of = "2026-10-30"                                                       # the newest team game is ten days before the date being reviewed
        out = self.cycle([self.fair_ml("TOR", "MTL", "HOME")])
        self.assertEqual(self.rows(), [])
        self.assertIn("days old", out["recording"]["shortfall"]["intelligence_unavailable"])


if __name__ == "__main__":
    unittest.main()
