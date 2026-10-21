"""POSTMORTEM_LEARNING_14D (operational/learning_cohort.py): the date window and automatic end, the -150 floor, every integrity gate staying closed, ranking and diversification, wave-released slots,
the separate bankroll with logged non-performance resets, immutability, settlement, fail-closed approval and separation from Eggy. Isolated: in-memory ledgers and temp official databases."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import db
from operational import learning_cohort as lc
from research.real_market_parlay.engine import ParlayLeg

D1 = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)          # 17:00 ET Oct 20, before a 19:00 ET puck drop
START1 = "2026-10-20T23:00:00"
START2 = "2026-10-21T02:00:00"


def nhl(games=(("1", START1), ("2", START1), ("3", START2))):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for t in ("TOR", "MTL", "EDM", "CHI", "COL", "PHI"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (t, t))
    for gid, start in games:
        conn.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, schedule_observed_at_utc, game_state, source) VALUES (?,?,?,?,?,?,?,?,?)",
                     (gid, "20262027", "2026-10-20", start, "TOR", "MTL", "2026-10-20T00:00:00", "SCHEDULED", "test"))
    conn.commit()
    return conn


def leg(pid="8478402", game="1", fam="PLAYER_SOG_ALTERNATE", k=2, price=-130.0, p=0.70, **kw):
    base = dict(game_id=game, event_id="e" + game, market_family=fam, participant_id=pid, participant_name="P" + pid, side="OVER", threshold=k, american_price=price, conservative_probability=p,
                sportsbook="DRAFTKINGS", captured_at_utc="2026-10-20T20:55:00Z", provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True, price_fresh=True,
                event_not_started=True, team="TOR", opponent="MTL", game_start_utc=START1 if game != "3" else START2, model_version="m1", provider_start_utc=("2026-10-20T23:00:00Z" if game != "3" else "2026-10-21T02:00:00Z"),
                retrieved_at_utc="2026-10-20T20:55:00Z", quote_updated_utc="2026-10-20T20:50:00Z", quote_age_min=10.0, freshness_status="FRESH", model_probability=p + 0.02, empirical_games=30)
    base.update(kw)
    return ParlayLeg(**base)


def ml(team="TOR", game="1", price=-120.0, p=0.58, market_probability=None):
    return leg(pid=team, game=game, fam="MONEYLINE", k=None, price=price, p=p, side="HOME", empirical_games=None, market_probability=market_probability)


def fake_card(leg, est, action="RECORD", thesis="MODEL_SEES_VALUE", contradiction="LOW", cal="HIGH", model_p=None, completeness=0.9, claims=None):
    """A review card of the shape operational/eggy_intelligence.py produces, built from the leg alone, so the cohort's selection, recording and ledger mechanics are tested without the real data files.
    (The review itself is tested in tests/test_eggy_intelligence.py.)"""
    a = float(leg.american_price)
    implied = 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)
    mp = est if model_p is None else model_p
    return {"version": "fake", "kind": "MONEYLINE" if leg.market_family == "MONEYLINE" else "PLAYER",
            "bet": {"label": f"{leg.participant_name} — {leg.market_family} — {a:+.0f}", "market_family": leg.market_family, "price": a},
            "most_likely_outcome": "fake outcome", "why_tonight": ["fake"], "case_for": ["fake for"], "case_against": ["fake against"], "historical": [], "why_they_disagree": [], "pass_conditions": ["fake pass"],
            "thesis": {"type": thesis, "statement": f"fake thesis {thesis}", "strength": "MODERATE"}, "contradiction": {"level": contradiction, "flags": []},
            "calibration_confidence": {"level": cal}, "data_completeness": {"present": ["a"], "missing": [], "k": 1, "n": 1, "fraction": completeness}, "decision_confidence": "MEDIUM",
            "action": {"decision": action, "reasons": ["fake"]}, "probabilities": {"model": mp, "market_no_vig": implied, "estimate_used_for_edge": est},
            "market": {"price": a, "implied_with_margin": implied, "estimate_used": est, "model": mp, "edge_vs_price": est - implied, "model_edge_vs_price": mp - implied, "edge_label": "MODEL EDGE AT LAST OBSERVED PRICE"},
            "estimate_used": est, "model_probability": mp, "claims": claims or []}


class Base(unittest.TestCase):
    cards: dict

    def setUp(self):
        self.conn = self._mem()
        self.nhl = nhl()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.nhl.close)
        patcher = mock.patch.object(lc, "is_authorised", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        p = mock.patch("operational.learning_postmortem._load_rows", return_value=([], []))          # the postmortem's MoneyPuck files: never read in a unit test
        p.start()
        self.addCleanup(p.stop)
        self.cards = {}                    # participant_id -> overrides for the fake review (action, thesis, contradiction, cal, model_p)
        self.context_ok = True
        for target, kw in (("build_context", {"side_effect": lambda *a, **k: SimpleNamespace(fake=True)}), ("usable", {"side_effect": lambda ctx: (self.context_ok, "" if self.context_ok else "fake: evidence missing")}),
                           ("review_leg", {"side_effect": self._review})):
            p = mock.patch.object(lc.ei, target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def _review(self, ctx, leg, basis, est):
        return fake_card(leg, float(est), **{"model_p": leg.model_probability, **(self.cards.get(str(leg.participant_id)) or {})})

    @staticmethod
    def _mem():
        return lc.open_ledger(":memory:")

    def _bet(self, rank, game, pid, price, p, status, day=1, date="2026-10-20", fam="PLAYER_SOG_ALTERNATE", k=2, edge_class="NEUTRAL_UNCERTAIN_EDGE"):
        stake = 10.0
        pl = {"WIN": lc.pb_profit(stake, price) if hasattr(lc, "pb_profit") else (stake * price / 100.0 if price > 0 else stake * 100.0 / abs(price)), "LOSS": -stake}.get(status)
        ret = {"WIN": stake + (pl or 0), "LOSS": 0.0}.get(status)
        dec = 1 + (price / 100.0 if price > 0 else 100.0 / abs(price))
        self.conn.execute(
            """INSERT INTO bets (bet_id, idempotency_key, experiment_id, cohort, policy_digest, et_date, experiment_day, selection_rank, wave_index, created_at_utc, created_at_et, game_id, market_family, settlement_market_id,
               threshold, side, participant_id, participant_name, team, opponent, american_price, decimal_price, stake, model_probability, conservative_probability, implied_probability, edge, ev, selection_score,
               score_components_json, selection_reason, price_source, leg_json, quote_age_min, result_status, profit_loss, return_amount, edge_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"B{rank}{day}{pid}", f"K{rank}{day}{pid}", lc.EXPERIMENT_ID, lc.COHORT, lc.policy_digest(), date, day, rank, 0, "x", "x", game, fam, "PLAYER_SOG_2PLUS", k, "OVER", pid, "Player " + pid, "TOR", "MTL", price, dec, stake,
             p, p - 0.02, 1 / dec, p - 0.02 - 1 / dec, 0.0, 0.5, "{}", "r", "LIVE_DRAFTKINGS", "{}", 12.0, status, pl, ret, edge_class))

    def cycle(self, legs, now=D1, fresh=True):
        if fresh:          # the quotes were just captured, whatever the test clock says
            stamp = lambda m: (now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
            legs = [dataclasses.replace(l, quote_updated_utc=stamp(10), retrieved_at_utc=stamp(9), captured_at_utc=stamp(9)) for l in legs]
        return lc.run_cycle(self.nhl, now, {"legs": legs, "sources": {}}, conn=self.conn)

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM bets ORDER BY selection_rank")]


class TestWindowAndAutoEnd(unittest.TestCase):
    def test_the_window_is_fourteen_eastern_days_and_ends_by_arithmetic(self):
        et = lc.et.EASTERN
        self.assertEqual(lc.phase(dt.datetime(2026, 10, 19, 23, 59, tzinfo=et))["phase"], "NOT_STARTED")
        self.assertEqual(lc.phase(dt.datetime(2026, 10, 20, 0, 1, tzinfo=et)), {"phase": "ACTIVE", "day": 1, "today": "2026-10-20"})
        self.assertEqual(lc.phase(dt.datetime(2026, 11, 2, 23, 59, tzinfo=et)), {"phase": "ACTIVE", "day": 14, "today": "2026-11-02"})
        self.assertEqual(lc.phase(dt.datetime(2026, 11, 3, 0, 1, tzinfo=et))["phase"], "ENDED")

    def test_the_policy_digest_changes_with_any_policy_field(self):
        d = lc.policy_digest()
        with mock.patch.dict(lc.POLICY, {"daily_target": 11}):
            self.assertNotEqual(lc.policy_digest(), d)

    def test_an_unapproved_or_changed_policy_records_nothing(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lc.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            self.assertFalse(lc.is_authorised())
            Path(t, lc.STATE_NAME).write_text(json.dumps({"approved": True, "policy_digest": "0" * 16}))
            self.assertFalse(lc.is_authorised())
            Path(t, lc.STATE_NAME).write_text(json.dumps({"approved": True, "policy_digest": lc.policy_digest()}))
            self.assertTrue(lc.is_authorised())


class TestIntegrityGatesStayClosed(Base):
    def test_each_failed_gate_keeps_a_leg_out(self):
        bad = [leg(price=-155.0), leg(provider_contract_verified=False), leg(model_threshold_eligible=False), leg(identity_resolved=False), leg(event_not_started=False),
               leg(freshness_status="MISSING_QUOTE_TIMESTAMP"), leg(p=0.0), leg(fam="PUCK_LINE"), leg(fam="PLAYER_ASSISTS", k=1)]
        for l in bad:
            self.assertTrue(lc.eligibility_reasons(l, D1), (l.market_family, l.american_price))
        self.assertEqual(lc.eligibility_reasons(leg(), D1), [])

    def test_a_started_game_is_never_recorded(self):
        late = D1 + dt.timedelta(hours=3)
        self.assertTrue(lc.eligibility_reasons(leg(), late))

    def test_the_floor_is_minus_150_inclusive_and_plus_prices_pass(self):
        for price in (-150.0, -125.0, 100.0, 250.0):
            self.assertEqual(lc.eligibility_reasons(leg(price=price), D1), [], price)
        self.assertTrue(lc.eligibility_reasons(leg(price=-151.0), D1))

    def test_a_game_not_on_todays_schedule_is_not_recorded(self):
        self.cycle([leg(game="99")])
        self.assertEqual(self.rows(), [])

    def test_identity_is_checked_on_the_official_roster_when_the_gate_is_on(self):
        with mock.patch.dict("os.environ", {"NHL_ENGINE_ROSTER_GATE": "on"}):
            self.assertTrue(lc.eligibility_reasons(leg(), D1))      # no roster on file: not verified, nothing staked


class TestSelectionAndRecording(Base):
    def test_records_a_real_leg_with_all_metadata_and_the_actual_price(self):
        out = self.cycle([leg(price=-135.0, p=0.72)])
        self.assertEqual(out["recording"]["recorded_today"], 1)
        r = self.rows()[0]
        self.assertEqual((r["cohort"], r["experiment_id"], r["stake"], r["american_price"], r["selection_rank"], r["experiment_day"]), (lc.COHORT, lc.EXPERIMENT_ID, 10.0, -135.0, 1, 1))
        for k in ("market_family", "model_probability", "conservative_probability", "implied_probability", "edge", "ev", "freshness_status", "model_version", "created_at_utc", "game_id", "participant_id",
                  "selection_reason", "quote_updated_utc", "dependence_json", "leg_json", "policy_digest"):
            self.assertIsNotNone(r[k], k)
        self.assertAlmostEqual(r["implied_probability"], 135 / 235, places=4)
        self.assertEqual(r["price_source"], "LIVE_DRAFTKINGS")

    def test_never_more_than_ten_a_day_and_never_a_duplicate(self):
        legs = [leg(pid=str(8470000 + i), game="1" if i % 2 else "2") for i in range(25)] + [leg(pid=str(8479000 + i), game="3") for i in range(6)]
        self.cycle(legs)
        self.cycle(legs)
        self.cycle(legs, now=dt.datetime(2026, 10, 21, 1, 50, tzinfo=dt.timezone.utc))          # 21:50 ET: ten minutes before the last wave
        self.assertEqual(len(self.rows()), 10)
        self.assertEqual(len({r["idempotency_key"] for r in self.rows()}), 10)

    def test_ranks_the_best_first_and_records_the_shortfall_honestly(self):
        a, b = leg(pid="8470001", p=0.80), leg(pid="8470002", p=0.55, price=-120.0)
        out = self.cycle([b, a])
        rows = self.rows()
        self.assertEqual([r["participant_id"] for r in rows], ["8470001", "8470002"])
        sf = out["recording"]["shortfall"]
        self.assertEqual((sf["target"], sf["recorded"], sf["slots_open"]), (10, 2, 8))
        self.assertIn("why", sf)

    def test_ineligible_legs_are_counted_in_the_shortfall_reasons(self):
        out = self.cycle([leg(), leg(pid="8470003", price=-170.0), leg(pid="8470004", price=-180.0)])
        self.assertEqual(out["recording"]["shortfall"]["reasons_candidates_were_not_recorded"], {"price worse than the -150 floor": 2})
        self.assertEqual(len(self.rows()), 1)

    def _cand(self, l, **kw):
        card = fake_card(l, l.conservative_probability, **kw)
        ecls, _ = lc.ei.edge_class_of(card)
        edge, _what = lc.ei.edge_for_class(card)
        return {"leg": l, "card": card, "rank_key": lc.ei.rank_key(card, ecls, card["estimate_used"], edge, lc.economic_key(l))}

    def test_a_fourth_bet_in_one_game_waits_behind_other_games_but_still_fills_a_slot_if_nothing_else_is_left(self):
        legs = [leg(pid="8470001", game="1", p=0.80), leg(pid="8470002", game="1", p=0.79), leg(pid="8470003", game="1", p=0.78), leg(pid="8470004", game="1", p=0.77),
                leg(pid="8470005", game="2", p=0.60, price=-120.0)]
        cands = [self._cand(l) for l in legs]
        self.assertEqual([c["leg"].participant_id for c in lc.select_ranked(cands, [], 4)], ["8470001", "8470002", "8470003", "8470005"])          # the fourth game-1 bet waits
        self.assertEqual([c["leg"].participant_id for c in lc.select_ranked(cands, [], 5)][-1], "8470004")          # ... and fills the last slot when nothing else remains
        self.assertEqual(lc.ei.SAME_GAME_SOFT_CAP, 3)

    def test_a_strong_bet_is_not_rejected_only_for_sharing_a_game(self):
        legs = [leg(pid=str(8470000 + i), game="1", p=0.78) for i in range(4)]
        self.cycle(legs)
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(json.loads(self.rows()[3]["dependence_json"])["same_game_bets_before"], 3)

    def test_slots_are_released_with_the_waves(self):
        # game 1,2 at 19:00 ET (wave 0); game 3 at 22:00 ET (wave 1): wave 0 may use ceil(10*2/3)=7 of the 10
        legs = [leg(pid=str(8470000 + i), game="1" if i % 2 else "2") for i in range(12)]
        self.cycle(legs)
        self.assertEqual(len(self.rows()), 7)            # only the first wave's prices exist: it may use 7 of the 10
        later = [leg(pid=str(8479000 + i), game="3") for i in range(6)]
        self.cycle(legs + later)
        self.assertEqual(len(self.rows()), 10)            # the later wave's own prices fill the rest as soon as they exist

    def test_the_cap_relaxes_just_before_a_wave_so_slots_are_not_stranded(self):
        legs = [leg(pid=str(8470000 + i), game="1" if i % 2 else "2") for i in range(12)]
        self.cycle(legs, now=dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc))          # 18:45 ET, 15 min before the 19:00 drop
        self.assertEqual(len(self.rows()), 9)            # ten less one reserved for the one later wave

    def test_moneyline_singles_and_player_markets_are_both_allowed(self):
        self.cycle([ml(), leg(pid="8470009", fam="PLAYER_POINTS", k=1, price=100.0, p=0.62), leg(pid="8470010", fam="GOALIE_SAVES", k=25, price=-140.0, p=0.7)])
        self.assertEqual({r["market_family"] for r in self.rows()}, {"MONEYLINE", "PLAYER_POINTS", "GOALIE_SAVES"})

    def test_a_leg_of_eggys_pick_is_labelled_and_the_two_ledgers_are_separate(self):
        eggy = {("1", "8478402", "PLAYER_SOG_ALTERNATE"): "T-EGGY"}
        with mock.patch.object(lc, "_eggy_legs", return_value=eggy):
            self.cycle([leg(), leg(pid="8470005")])
        rel = {r["participant_id"]: r["eggy_relation"] for r in self.rows()}
        self.assertTrue(rel["8478402"].startswith("SAME_PLAYER_AND_MARKET_AS_EGGY_PICK_LEG:T-EGGY"))
        self.assertEqual(rel["8470005"], "EGGY_NOT_INVOLVED")

    def test_the_run_never_raises_into_the_trader(self):
        with mock.patch.object(lc, "_record_cycle", side_effect=RuntimeError("boom")):
            self.assertEqual(self.cycle([leg()])["status"], "ERROR")

    def test_no_new_bets_after_the_window_but_the_cycle_still_settles(self):
        after = dt.datetime(2026, 11, 3, 15, 0, tzinfo=dt.timezone.utc)
        out = self.cycle([leg()], now=after)
        self.assertEqual(out["recording"]["status"], "NO_NEW_BETS")
        self.assertEqual(self.rows(), [])
        self.assertIn("settlement", out)


class TestIntelligenceDecidesTheBets(Base):
    """The review decides, then the ranking orders: a PASS is never recorded, no review means no bet, every bet carries the card it was recorded with, and the order is lexicographic (no score)."""

    def test_a_pass_is_never_recorded_and_the_reason_says_it_was_a_judgment(self):
        self.cards["8470001"] = {"action": "PASS", "thesis": "UNDERDOG_AT_FAIR_PRICE"}
        out = self.cycle([leg(pid="8470001"), leg(pid="8470002")])
        self.assertEqual([r["participant_id"] for r in self.rows()], ["8470002"])
        self.assertEqual(out["recording"]["shortfall"]["reasons_candidates_were_not_recorded"], {"passed on judgment: underdog at fair price": 1})

    def test_no_review_no_bet(self):
        self.context_ok = False
        out = self.cycle([leg()])
        self.assertEqual(self.rows(), [])
        self.assertIn("evidence missing", out["recording"]["shortfall"]["intelligence_unavailable"])

    def test_a_review_that_raises_never_becomes_a_bet(self):
        with mock.patch.object(lc.ei, "review_leg", side_effect=lambda ctx, l, b, e: {**fake_card(l, e), "action": {"decision": "PASS", "reasons": ["the review could not be completed"]}, "thesis": {"type": "NONE", "statement": "x", "strength": "NONE"}}):
            self.cycle([leg()])
        self.assertEqual(self.rows(), [])

    def test_every_bet_carries_the_card_it_was_recorded_with(self):
        self.cards["8470001"] = {"thesis": "MODEL_SEES_VALUE", "contradiction": "MODERATE", "cal": "MEDIUM"}
        self.cycle([leg(pid="8470001")])
        r = self.rows()[0]
        self.assertEqual((r["thesis_type"], r["action"], r["contradiction_level"], r["calibration_level"], r["decision_confidence"]), ("MODEL_SEES_VALUE", "RECORD", "MODERATE", "MEDIUM", "MEDIUM"))
        card = json.loads(r["intelligence_json"])
        self.assertEqual(card["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertTrue(card["case_against"])
        self.assertIn("Action:", r["selection_reason"])
        self.assertEqual(json.loads(r["score_components_json"])["ranking"], "lexicographic, no weighted score")
        self.assertEqual(r["selection_score"], 0.0)

    def test_the_review_card_is_locked_with_the_rest_of_the_entry(self):
        self.cycle([leg()])
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE bets SET intelligence_json = '{}'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE bets SET action = 'PASS'")

    def test_the_lower_contradiction_ranks_first_all_else_equal(self):
        self.cards["8470001"] = {"contradiction": "MODERATE"}
        self.cycle([leg(pid="8470001", p=0.7), leg(pid="8470002", p=0.7)])
        self.assertEqual([r["participant_id"] for r in self.rows()], ["8470002", "8470001"])

    def test_the_better_calibrated_estimate_ranks_first_all_else_equal(self):
        self.cards["8470001"] = {"cal": "MEDIUM"}
        self.cycle([leg(pid="8470001", p=0.7), leg(pid="8470002", p=0.7)])
        self.assertEqual([r["participant_id"] for r in self.rows()], ["8470002", "8470001"])

    def test_a_record_ranks_before_a_lower_priority_even_with_a_bigger_edge(self):
        self.cards["8470001"] = {"action": "LOWER_PRIORITY", "thesis": "FAIR_PRICED"}
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)
        self.cycle([leg(pid="8470001", p=0.9), leg(pid="8470002", p=0.6)], now=late)
        self.assertEqual([(r["participant_id"], r["action"]) for r in self.rows()], [("8470002", "RECORD"), ("8470001", "LOWER_PRIORITY")])

    def test_the_hit_chance_orders_bets_of_equal_standing_and_there_is_no_weighted_score(self):
        self.cycle([leg(pid="8470001", p=0.62, price=-120.0), leg(pid="8470002", p=0.80, price=-120.0)])
        self.assertEqual([r["participant_id"] for r in self.rows()], ["8470002", "8470001"])
        import inspect
        self.assertFalse(hasattr(lc, "score_leg") or hasattr(lc, "SELECTION_WEIGHTS"))
        self.assertNotIn("weights", inspect.getsource(lc.select_ranked))

    def test_the_status_document_carries_each_bets_card_and_its_complete_label(self):
        self.cycle([leg(pid="8470001"), dataclasses.replace(ml(team="TOR", game="2", p=0.58, price=-120.0, market_probability=0.55), participant_name="TOR")])
        doc = lc.status_doc(D1, conn=self.conn)
        labels = {b["label"] for b in doc["bets_today"]}
        self.assertIn("P8470001 — SHOTS ON GOAL 2+ — -130", labels)
        self.assertIn("TOR — MONEYLINE — -120", labels)
        self.assertEqual(set(doc["cards"]), {b["bet_id"] for b in doc["bets_today"]})
        self.assertTrue(all(b["action"] and b["thesis_type"] for b in doc["bets_today"]))

    def test_the_ledger_label_is_player_market_threshold_price(self):
        self.assertEqual(lc.label_of({"participant_name": "Filip Chytil", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "american_price": -130.0}), "Filip Chytil — SHOTS ON GOAL 2+ — -130")
        self.assertEqual(lc.label_of({"participant_name": "Jamie Oleksiak", "market_family": "PLAYER_GOALS", "threshold": 1, "american_price": 2500.0}), "Jamie Oleksiak — GOALS 1+ — +2500")
        self.assertEqual(lc.label_of({"participant_name": "VAN", "market_family": "MONEYLINE", "threshold": None, "american_price": 240.0}), "VAN — MONEYLINE — +240")
        self.assertEqual(lc.label_of({"participant_name": "Quinn Hughes", "market_family": "PLAYER_POINTS", "threshold": 2, "american_price": 350.0}), "Quinn Hughes — POINTS 2+ — +350")
        self.assertEqual(lc.label_of({"participant_name": "Some Goalie", "market_family": "GOALIE_SAVES", "threshold": 25, "american_price": -125.0}), "Some Goalie — SAVES 25+ — -125")

    def test_the_cap_logic_and_every_gate_are_untouched_by_the_review(self):
        # a leg that fails an integrity gate is never reviewed or recorded, whatever its card would say
        self.cycle([leg(identity_resolved=False), leg(price=-155.0)])
        self.assertEqual(self.rows(), [])


class TestBankroll(Base):
    def test_starts_at_500_and_each_bet_takes_10(self):
        self.cycle([leg()])
        b = lc.balance(self.conn)
        self.assertEqual((b["initial_funding"], b["cash"], b["open_stakes"], b["equity"], b["reset_count"]), (500.0, 490.0, 10.0, 500.0, 0))

    def test_resets_to_500_when_it_cannot_fund_the_next_bet_and_logs_a_non_performance_event(self):
        lc.ensure_funded(self.conn, D1)
        self.conn.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason) VALUES ('x','x','TEST',0,-495,0,1,'t')")
        self.assertEqual(lc.balance(self.conn)["cash"], 5.0)
        ev = lc.reset_if_needed(self.conn, D1, 1)
        self.assertEqual((ev["balance_before"], ev["amount"], ev["balance_after"]), (5.0, 495.0, 500.0))
        row = dict(self.conn.execute("SELECT * FROM funding_events WHERE kind='RESET'").fetchone())
        self.assertEqual((row["reason"], row["experiment_day"]), (lc.RESET_REASON, 1))
        self.assertTrue(row["at_et"].endswith(("EDT", "EST")))
        b = lc.balance(self.conn)
        self.assertEqual((b["betting_pl"], b["reset_count"], b["reset_funding_added"]), (0.0, 1, 495.0))

    def test_a_reset_is_never_profit_and_never_a_returned_stake(self):
        lc.ensure_funded(self.conn, D1)
        self.conn.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason) VALUES ('x','x','TEST',0,-495,0,1,'t')")
        lc.reset_if_needed(self.conn, D1, 1)
        self.assertIsNone(lc.balance(self.conn)["roi"])
        self.assertEqual(lc.balance(self.conn)["betting_pl"], 0.0)

    def test_no_reset_when_the_bankroll_can_fund_a_bet_or_after_the_experiment_ends(self):
        lc.ensure_funded(self.conn, D1)
        self.assertIsNone(lc.reset_if_needed(self.conn, D1, 1))
        self.conn.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason) VALUES ('x','x','TEST',0,-495,0,1,'t')")
        self.assertIsNone(lc.reset_if_needed(self.conn, dt.datetime(2026, 11, 3, 15, tzinfo=dt.timezone.utc), 14))

    def test_funding_events_are_append_only_and_bets_are_never_deleted_or_edited(self):
        self.cycle([leg()])
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE funding_events SET amount = 1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM funding_events")
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("DELETE FROM bets")
        for col, val in (("american_price", -110.0), ("stake", 50.0), ("edge", 0.5), ("leg_json", "{}")):
            with self.assertRaises(sqlite3.DatabaseError, msg=col):
                self.conn.execute(f"UPDATE bets SET {col} = ?", (val,))
        self.conn.execute("UPDATE bets SET result_status='WIN'")            # a result is allowed

    def test_a_reset_during_recording_keeps_the_ten_a_day_target(self):
        lc.ensure_funded(self.conn, D1)
        self.conn.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason) VALUES ('x','x','TEST',0,-495,0,1,'t')")
        self.cycle([leg(pid=str(8470000 + i), game="1" if i % 2 else "2") for i in range(8)])
        self.assertEqual(lc.balance(self.conn)["reset_count"], 1)
        self.assertGreaterEqual(len(self.rows()), 7)


class TestSettlement(Base):
    def _final(self, gid, shots, team="TOR", pid="8478402"):
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id=?", (gid,))
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES (?,?,?,?,?,?,?,?,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')", (gid, pid, team, 20.0, 0, 0, shots, 1))
        self.nhl.commit()

    def test_a_win_and_a_loss_settle_with_profit_only_p_and_l(self):
        self.cycle([leg(pid="8478402", price=-125.0), leg(pid="8470002", price=150.0)])
        self._final("1", 3)
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1','8470002','TOR',20.0,0,0,0,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')")
        self.nhl.commit()
        out = lc.settle_due(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        self.assertEqual(out["settled"], 2)
        by = {r["participant_id"]: r for r in self.rows()}
        self.assertEqual((by["8478402"]["result_status"], round(by["8478402"]["profit_loss"], 2), by["8478402"]["return_amount"]), ("WIN", 8.0, 18.0))
        self.assertEqual((by["8470002"]["result_status"], by["8470002"]["profit_loss"], by["8470002"]["return_amount"]), ("LOSS", -10.0, 0.0))
        b = lc.balance(self.conn)
        self.assertEqual((b["betting_pl"], b["open_stakes"], b["cash"], b["roi"]), (-2.0, 0.0, 498.0, -0.1))

    def test_a_game_not_final_stays_pending_and_settlement_is_idempotent(self):
        self.cycle([leg()])
        self.assertEqual(lc.settle_due(self.conn, self.nhl, D1)["settled"], 0)
        self._final("1", 3)
        self.assertEqual(lc.settle_due(self.conn, self.nhl, D1)["settled"], 1)
        self.assertEqual(lc.settle_due(self.conn, self.nhl, D1)["scanned"], 0)

    def test_a_player_who_did_not_play_is_never_booked_as_a_win_or_loss(self):
        self.cycle([leg()])
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id='1'")
        self.nhl.commit()
        lc.settle_due(self.conn, self.nhl, D1)
        self.assertNotIn(self.rows()[0]["result_status"], ("WIN", "LOSS"))
        self.assertEqual(lc.balance(self.conn)["betting_pl"], 0.0)


class TestStatusAndSeparation(Base):
    def test_status_document(self):
        self.cycle([leg(), leg(pid="8470002")])
        s = lc.status_doc(D1, conn=self.conn)
        self.assertEqual((s["day"], s["recorded_today"], s["target_per_day"], s["stake_usd"], s["exposure_today_usd"], s["odds_requirement"]), (1, 2, 10, 10.0, 20.0, "-150 or better"))
        self.assertEqual(s["eggy"]["official_picks_per_day"], 1)
        self.assertEqual(s["bankroll"]["reset_count"], 0)

    def test_the_cohort_never_touches_the_other_ledgers(self):
        import inspect
        src = inspect.getsource(lc)
        for forbidden in ("paper_bankroll.db", "record_paper_bet", "recording_pause.pause", "recording_pause.resume", "settle_paper_bet", "approve_policy", "ticket_policy"):
            self.assertNotIn(forbidden, src.replace("paper_bankroll as pb", ""), forbidden)

    def test_paper_only_and_no_real_money_surface(self):
        self.assertTrue(lc.POLICY["paper_only"])
        import inspect
        for word in ("place_bet", "submit_wager", "draftkings.com/api"):
            self.assertNotIn(word, inspect.getsource(lc))


class TestTraderIntegration(unittest.TestCase):
    def _run(self):
        from tests import test_real_parlay_paper_trader as tt
        nhl_path, bankroll = tt._fresh_nhl_db_with_games(tt.GAMES), tt._bankroll()
        return tt._run_with(nhl_path, bankroll, tt._legs(8), tt.NOW1)

    def test_the_trader_reports_the_cohort_and_it_does_nothing_until_approved(self):
        result = self._run()
        self.assertEqual(result["learning_cohort"]["status"], "NOT_AUTHORISED")
        self.assertEqual(result["stake_result"]["newly_recorded"], 5)          # the normal book is untouched

    def test_a_failing_cohort_never_stops_the_model_book(self):
        with mock.patch.object(lc, "run_cycle", side_effect=RuntimeError("boom")):
            result = self._run()
        self.assertEqual(result["learning_cohort"]["status"], "ERROR")
        self.assertEqual(result["stake_result"]["newly_recorded"], 5)

    def test_the_snapshot_carries_a_learning_experiment_section(self):
        from operational import cloud_snapshot_builder as csb
        self.assertIn("learning_experiment", csb._SECTION_BUILDERS)
        sec = csb._SECTION_BUILDERS["learning_experiment"]()
        self.assertEqual(sec["cohort"], lc.COHORT)


class TestActivationTool(unittest.TestCase):
    def test_the_activation_log_and_digest_are_the_only_things_apply_writes(self):
        import inspect
        from deploy import activate_learning_experiment as act
        src = inspect.getsource(act)
        self.assertIn("lc.activate(", src)
        for forbidden in ("recording_pause.resume", "recording_pause.pause", "ticket_policy.approve", "record_paper_bet"):
            self.assertNotIn(forbidden, src)
        self.assertEqual(act.EXPECTED["daily_target"], 10)

    def test_activation_funds_the_bankroll_and_authorises_exactly_this_digest(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lc.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            c = lc.open_ledger(":memory:")
            doc = lc.activate(D1, approved_by="owner", evidence="test", conn=c)
            self.assertEqual(doc["policy_digest"], lc.policy_digest())
            self.assertTrue(lc.is_authorised())
            self.assertEqual(lc.balance(c)["initial_funding"], 500.0)
            lc.activate(D1, approved_by="owner", evidence="again", conn=c)          # re-running never funds twice
            self.assertEqual(lc.balance(c)["initial_funding"], 500.0)


class TestPostmortemAndScorecard(Base):
    def test_metrics_use_settled_wins_and_losses_only(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        for i, (st, price) in enumerate([("WIN", -125.0), ("LOSS", -125.0), ("LOSS", 150.0), ("PENDING", -110.0), ("VOID", -110.0)]):
            self._bet(i + 1, "1", str(i), price, 0.6, st)
        m = lpm.metrics(lpm.rows(self.conn))
        self.assertEqual((m["placed"], m["settled"], m["wins"], m["losses"], m["voids"], m["open"]), (5, 3, 1, 2, 1, 1))
        self.assertAlmostEqual(m["hit_rate"], 1 / 3, places=4)
        self.assertEqual((m["staked"], m["profit_loss"]), (30.0, -12.0))
        self.assertAlmostEqual(m["roi"], -0.4, places=4)
        self.assertAlmostEqual(m["expected_wins"], 1.8, places=3)
        self.assertAlmostEqual(m["calibration_delta"], -0.8, places=3)

    def test_breakdowns_cover_every_requested_dimension_and_price_bands(self):
        from operational import learning_postmortem as lpm
        for i, price in enumerate([-145.0, -120.0, -105.0, 120.0, 200.0]):
            self._bet(i + 1, "1", str(i), price, 0.6, "WIN" if i % 2 else "LOSS")
        b = lpm.breakdowns(lpm.rows(self.conn))
        self.assertEqual(set(b), {"edge_class", "price_status", "market_type", "price_band", "model_probability_band", "edge_band", "game", "team", "player", "data_freshness", "selection_rank"})
        self.assertEqual({r["group"] for r in b["price_band"]}, {"-150 to -131", "-130 to -111", "-110 to +100", "+101 to +150", "+151 and longer"})
        self.assertEqual({r["group"] for r in b["selection_rank"]}, {"ranks 1-3", "ranks 4-6"})

    def test_scorecard_has_fourteen_days_and_marks_future_days(self):
        from operational import learning_postmortem as lpm
        self._bet(1, "1", "1", -120.0, 0.6, "WIN")
        sc = lpm.daily_rows(lpm.rows(self.conn), 3)
        self.assertEqual(len(sc), 14)
        self.assertEqual((sc[0]["recorded"], sc[0]["target"], sc[0]["wins"], sc[0]["date"]), (1, 10, 1, "2026-10-20"))
        self.assertEqual((sc[13]["date"], sc[13]["is_future"], sc[2]["is_future"], sc[3]["is_future"]), ("2026-11-02", True, False, True))

    def test_build_separates_betting_pl_from_funding_events(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        self._bet(1, "1", "1", -120.0, 0.6, "LOSS")
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(doc["postmortem_for"], "2026-10-20")
        self.assertEqual(doc["day_metrics"]["profit_loss"], -10.0)
        self.assertEqual(doc["bankroll"]["betting_pl"], -10.0)
        self.assertEqual(doc["funding_events"][0]["kind"], "INITIAL")
        self.assertIn("not available", doc["closing_price_movement"])

    def test_the_final_report_waits_for_open_bets_then_answers_all_sixteen_questions_as_recommendations(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        self._bet(1, "1", "1", -120.0, 0.6, "PENDING")
        after = dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lpm.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            self.assertEqual(lpm.final_report_if_ready(self.conn, self.nhl, after)["status"], "WAITING")
            self.conn.execute("UPDATE bets SET result_status='LOSS', profit_loss=-10, return_amount=0")
            self.assertEqual(lpm.final_report_if_ready(self.conn, self.nhl, after)["status"], "WRITTEN")
            rep = json.loads(Path(t, lpm.REPORT_NAME).read_text())
            self.assertTrue(rep["recommendations_only"])
            keys = list(rep["answers"])
            for n in range(1, 17):
                self.assertTrue(any(k.startswith(f"{n}_") for k in keys), n)
            self.assertEqual(rep["answers"]["market_verdicts"]["PLAYER_SOG_ALTERNATE"]["recommendation"], "WATCH")      # one bet is never a verdict
            self.assertTrue(Path(t, lpm.REPORT_MD).exists())

    def test_a_real_loss_is_classified_end_to_end_without_changing_anything(self):
        from operational import learning_postmortem as lpm
        self.cycle([leg(pid="8478402")])
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id='1'")
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1','8478402','TOR',20.0,0,0,1,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')")
        self.nhl.commit()
        lc.settle_due(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        self.assertEqual(self.rows()[0]["result_status"], "LOSS")
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        c = doc["day_bets"][0]
        self.assertEqual(c["result"], "LOSS")
        self.assertIn(c["classification"], {"NORMAL_VARIANCE", "UNKNOWN", "MODEL_CALIBRATION", "ROLE_CHANGE_MISSED", "TOI_PROJECTION_ERROR", "TEAM_SHOT_ENVIRONMENT_ERROR", "STALE_DATA", "IDENTITY_LINEUP_ERROR"})
        self.assertIn(c["action"], {"NO_ACTION", "WATCH", "INVESTIGATE", "BUG_FIX", "CHALLENGER_IDEA", "HALT_MARKET"})
        self.assertEqual(doc["loss_classification"]["classified_failures"] is not None, True)

    def test_the_no_pause_guarantee_holds_for_the_cohort_modules(self):
        import ast
        for mod in ("learning_cohort", "learning_postmortem"):
            tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath("operational", mod + ".py").read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("pause", "resume", "set_pause"):
                    self.fail(f"{mod} touches recording pause: {node.attr}")

    def test_no_final_report_while_the_experiment_is_still_running(self):
        from operational import learning_postmortem as lpm
        self.assertIsNone(lpm.final_report_if_ready(self.conn, self.nhl, D1))

    def test_a_small_sample_is_never_a_promotion(self):
        from operational import learning_postmortem as lpm
        m = {"settled": 12, "roi": 0.4, "wins": 9, "expected_wins": 7.0}
        self.assertEqual(lpm.verdict_for_market(m, 1.0)[0], "WATCH")
        m = {"settled": 60, "roi": 0.1, "wins": 36, "expected_wins": 35.0}
        self.assertEqual(lpm.verdict_for_market(m, 0.3)[0], "PROMOTE_CANDIDATE")
        m = {"settled": 60, "roi": -0.2, "wins": 25, "expected_wins": 36.0}
        self.assertEqual(lpm.verdict_for_market(m, -3.0)[0], "CHALLENGER_IDEA")

    def test_the_postmortem_never_pauses_or_changes_anything(self):
        import inspect
        from operational import learning_postmortem as lpm
        src = inspect.getsource(lpm)
        for forbidden in ("recording_pause", "ticket_policy", "UPDATE bets", "DELETE", "INSERT INTO bets", "record_paper_bet"):
            self.assertNotIn(forbidden, src)


class TestDecisionReviewInThePostmortem(Base):
    """Every bet gets two results: the outcome, and a judgment of the decision made from the card it was recorded with. A bet recorded before the review existed is reconstructed and labelled."""
    NOW = dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc)
    CLAIMS = [{"id": "expected_toi", "text": "", "value": 18.0}, {"id": "expected_pp", "text": "", "value": 2.0}, {"id": "expected_stat", "text": "", "value": 3.0}, {"id": "team_win_probability", "text": "", "value": 0.55}]

    def _final(self, shots=1, toi=19.0, pid="8478402", home=3, away=2):
        self.nhl.execute(f"UPDATE games SET game_state='FINAL', home_score={home}, away_score={away}, final_period_type='REG' WHERE game_id='1'")
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1',?,'TOR',?,0,0,?,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')", (pid, toi, shots))
        self.nhl.commit()
        lc.settle_due(self.conn, self.nhl, D1 + dt.timedelta(hours=8))

    def _build(self, **kw):
        from operational import learning_postmortem as lpm
        return lpm.build(self.conn, self.nhl, self.NOW, **kw)

    def test_a_recorded_bet_is_reviewed_from_its_own_card_with_the_facts_of_the_game(self):
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final(shots=1)
        doc = self._build()
        bet = self.rows()[0]
        r = doc["bet_reviews"][bet["bet_id"]]
        self.assertEqual((r["source"], r["result_status"], r["facts_complete"]), ("RECORDED", "LOSS", True))
        self.assertEqual(r["card"]["thesis"]["type"], "MODEL_SEES_VALUE")
        self.assertEqual(r["facts"]["player"]["shots"], 1)
        self.assertEqual(r["facts"]["game"]["winner"], "TOR")
        a = r["assessment"]
        self.assertEqual((a["decision_quality"]["label"], a["outcome"]), ("GOOD_DECISION", "LOSS"))
        self.assertEqual(a["lesson"]["signal"], "GOOD_DECISION_LOST_NORMAL_VARIANCE")
        self.assertEqual(a["classification"], "NORMAL_VARIANCE")
        line = doc["day_bets"][0]
        self.assertEqual((line["bet"], line["decision"], line["classification"], line["result"]), ("P8478402 — SHOTS ON GOAL 2+ — -130", "GOOD_DECISION", "NORMAL_VARIANCE", "LOSS"))
        self.assertEqual(doc["decision_review"]["earned_normal_variance"], 1)
        self.assertIn({"decision": "GOOD_DECISION", "outcome": "LOSS", "n": 1}, doc["decision_review"]["grid"])

    def test_a_loss_whose_ice_time_assumption_failed_is_not_called_variance(self):
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final(shots=1, toi=12.0)
        line = self._build()["day_bets"][0]
        self.assertEqual(line["decision"], "GOOD_DECISION")
        self.assertEqual(line["classification"], "TOI_PROJECTION_ERROR")
        self.assertEqual(line["action"], "INVESTIGATE")

    def test_a_bad_decision_that_won_is_a_lucky_win(self):
        # an old bet (the live cohort would never record this one): its reconstructed card says the review would have passed
        self._bet(1, "1", "8478402", -130.0, 0.6, "WIN")
        row = self.conn.execute("SELECT * FROM bets").fetchone()
        self.assertIsNone(row["intelligence_json"])                       # a legacy row: no card of its own
        with mock.patch("operational.eggy_decisions.reconstruct_card", return_value=fake_card(leg(pid="8478402"), 0.6, action="PASS", thesis="FAIR_PRICED", contradiction="HIGH")):
            line = self._build()["day_bets"][0]
        self.assertEqual((line["decision"], line["classification"], line["lesson"]), ("BAD_DECISION", "LUCKY_WIN", "BAD_DECISION_WON_LUCK"))

    def test_a_bet_recorded_before_the_review_existed_is_reconstructed_and_labelled(self):
        self._bet(1, "1", "8478402", -130.0, 0.6, "LOSS")
        rec = fake_card(leg(pid="8478402"), 0.6, action="PASS", thesis="UNDERDOG_AT_FAIR_PRICE", contradiction="HIGH")
        rec["reconstructed"] = True
        with mock.patch("operational.eggy_decisions.reconstruct_card", return_value=rec) as rc:
            doc = self._build()
        r = next(iter(doc["bet_reviews"].values()))
        self.assertEqual(r["source"], "RECONSTRUCTED")
        self.assertTrue(r["card"]["reconstructed"])
        self.assertEqual(doc["decision_review"]["reconstructed"], 1)
        self.assertEqual(rc.call_count, 1)

    def test_a_bet_whose_card_cannot_be_rebuilt_is_insufficient_evidence_never_good_or_bad(self):
        self._bet(1, "1", "8478402", -130.0, 0.6, "LOSS")
        with mock.patch("operational.eggy_decisions.reconstruct_card", side_effect=ValueError("no leg")):
            line = self._build()["day_bets"][0]
        self.assertEqual(line["decision"], "INSUFFICIENT_EVIDENCE")

    def test_a_finished_review_is_reused_not_recomputed(self):
        from operational import learning_postmortem as lpm
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final()
        doc = self._build()
        with mock.patch("operational.eggy_decisions.assess", side_effect=AssertionError("recomputed")), mock.patch.object(lpm, "load", return_value=doc):
            again = lpm.build(self.conn, self.nhl, self.NOW)
        self.assertEqual(again["bet_reviews"], doc["bet_reviews"])

    def test_a_review_that_waited_for_the_games_data_is_completed_when_it_lands(self):
        from operational import learning_postmortem as lpm
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        first = self._build()
        r1 = next(iter(first["bet_reviews"].values()))
        self.assertEqual((r1["result_status"], r1["facts_complete"], r1["assessment"]["lesson"]["signal"]), ("PENDING", False, "NOT_SETTLED"))
        self._final()
        with mock.patch.object(lpm, "load", return_value=first):
            second = lpm.build(self.conn, self.nhl, self.NOW)
        r2 = next(iter(second["bet_reviews"].values()))
        self.assertEqual((r2["result_status"], r2["facts_complete"]), ("LOSS", True))

    def test_the_big_data_files_are_not_read_for_a_bet_that_is_still_open(self):
        self.cycle([leg(pid="8478402")])
        with mock.patch("operational.learning_postmortem._load_rows", side_effect=AssertionError("read the files")):
            self._build()

    def test_the_classification_never_declares_variance_without_a_review(self):
        from operational import learning_postmortem as lpm
        self.assertEqual(lpm.combined_classification({"category": "NORMAL_VARIANCE", "recommended_action": "NO_ACTION"}, {})[0], "UNKNOWN")
        self.assertEqual(lpm.combined_classification({"category": "ROLE_CHANGE_MISSED", "recommended_action": "WATCH"}, {})[0], "ROLE_CHANGE_MISSED")

    def test_earned_variance_gives_way_to_a_specific_cause_the_signals_found(self):
        from operational import learning_postmortem as lpm
        earned = {"classification": "NORMAL_VARIANCE", "why": "earned", "recommended_action": "NO_ACTION", "outcome": "LOSS"}
        cat, why, action = lpm.combined_classification({"category": "TOI_PROJECTION_ERROR", "recommended_action": "WATCH"}, earned)
        self.assertEqual((cat, action), ("TOI_PROJECTION_ERROR", "WATCH"))
        self.assertIn("not earned variance", why)
        self.assertEqual(lpm.combined_classification({"category": "UNKNOWN", "recommended_action": "NO_ACTION"}, earned)[0], "NORMAL_VARIANCE")

    def test_the_hosted_snapshot_carries_the_reviews_of_the_last_three_days_only(self):
        from operational import learning_postmortem as lpm
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final()
        doc = self._build()
        old = dict(next(iter(doc["bet_reviews"].values())), et_date="2026-10-10", bet_id="OLD")
        doc["bet_reviews"]["OLD"] = old
        with mock.patch.object(lpm, "load", return_value=doc), mock.patch.object(lpm.lc, "phase", return_value={"phase": "ACTIVE", "day": 2, "today": "2026-10-21"}):
            sec = lpm.snapshot_section()
        self.assertEqual(len(sec["postmortem"]["bet_reviews"]), 1)
        self.assertNotIn("OLD", sec["postmortem"]["bet_reviews"])
        self.assertIn("decision_review", sec["postmortem"])

    def test_the_published_snapshot_carries_each_card_once_and_without_the_rebuildable_answers(self):
        from operational import learning_postmortem as lpm
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final()
        doc = self._build()
        bid = next(iter(doc["bet_reviews"]))
        doc["bet_reviews"][bid]["card"]["answers"] = {"why_tonight": "x"}
        status = {"cohort": lc.COHORT, "authorised": True, "phase": "ACTIVE", "day": 2, "cards": {bid: {"answers": {"a": 1}, "thesis": {}}, "OTHER": {"answers": {"a": 1}, "thesis": {}}}}
        with mock.patch.object(lpm, "load", return_value=doc), mock.patch.object(lpm.lc, "load_status", return_value=status), \
                mock.patch.object(lpm.lc, "phase", return_value={"phase": "ACTIVE", "day": 2, "today": "2026-10-21"}):
            sec = lpm.snapshot_section()
        self.assertNotIn(bid, sec["cards"])                                           # the review carries it
        self.assertIn("OTHER", sec["cards"])                                          # a bet with no review yet keeps its stored card
        self.assertNotIn("answers", sec["cards"]["OTHER"])
        self.assertNotIn("answers", sec["postmortem"]["bet_reviews"][bid]["card"])
        self.assertIn("thesis", sec["postmortem"]["bet_reviews"][bid]["card"])

    def test_the_final_report_carries_the_decision_against_outcome_grid(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        self.cards["8478402"] = {"claims": self.CLAIMS}
        self.cycle([leg(pid="8478402", p=0.62)])
        self._final()
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lpm.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            lpm.final_report_if_ready(self.conn, self.nhl, dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc))
            rep = json.loads(Path(t, lpm.REPORT_NAME).read_text())
        self.assertIn("decision_quality_vs_outcome", rep["answers"])
        self.assertIn("earned_by_the_decision_review", rep["answers"]["9_normal_variance"])


class TestPaperPerformancePanel(unittest.TestCase):
    def _snap(self, section):
        from tests.product_fixture import snapshot
        s = snapshot()
        if section is not None:
            s["learning_experiment"] = section
        return s

    def _section(self):
        sc = [{"day": d, "date": f"2026-10-{19 + d:02d}", "recorded": 10 if d == 1 else 0, "target": 10, "is_future": d > 2, "wins": 6 if d == 1 else 0, "losses": 4 if d == 1 else 0, "pushes_voids": 0,
               "unresolved": 0, "open": 0, "hit_rate": 0.6 if d == 1 else None, "profit_loss": 12.0 if d == 1 else 0.0, "roi": 0.12 if d == 1 else None, "average_decimal_odds": 1.8,
               "expected_wins": 5.9, "actual_wins": 6, "calibration_delta": 0.1} for d in range(1, 15)]
        cu = {"placed": 10, "settled": 10, "wins": 6, "losses": 4, "voids": 0, "hit_rate": 0.6, "profit_loss": 12.0, "roi": 0.12, "expected_wins": 5.9, "actual_wins": 6, "brier": 0.23}
        return {"cohort": lc.COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "authorised": True, "phase": "ACTIVE", "day": 2, "days": 14, "start_date": "2026-10-20", "end_date": "2026-11-02",
                "target_per_day": 10, "stake_usd": 10.0, "odds_requirement": "-150 or better", "recorded_today": 7, "exposure_today_usd": 70.0,
                "bankroll": {"cash": 430.0, "equity": 500.0, "betting_pl": 12.0, "roi": 0.12, "reset_count": 1, "reset_funding_added": 495.0},
                "record": {"wins": 6, "losses": 4, "voids": 0, "open": 7, "hit_rate": 0.6}, "resets": [{"at_et": "x EDT", "experiment_day": 2, "balance_before": 5.0, "amount": 495.0, "balance_after": 500.0, "reason": lc.RESET_REASON}],
                "shortfall_today": {"target": 10, "recorded": 7, "slots_open": 3, "why": "fewer than 10 legitimate eligible bets exist so far today", "not_eligible_reasons": {"price not fresh": 4}, "eligible_not_yet_recorded": 0, "legs_in_pool": 11},
                "bets_today": [{"selection_rank": 1, "market_family": "PLAYER_SOG_ALTERNATE", "participant_name": "A Player", "team": "TOR", "threshold": 2, "american_price": -135.0, "conservative_probability": 0.7,
                                "edge": 0.05, "result_status": "PENDING", "profit_loss": None, "eggy_relation": "EGGY_NOT_INVOLVED", "price_status": "LAST_KNOWN_PREGAME", "price_age_at_recording_min": 34.0}],
                "postmortem": {"scorecard": sc, "cumulative": cu, "postmortem_for": "2026-10-20", "day_metrics": {**cu, "unresolved": 0, "open": 0, "staked": 100.0, "returned": 112.0}, "day_bets": [], "closing_price_movement": "not available",
                               "cumulative_breakdowns": {"market_type": [{"group": "PLAYER_SOG_ALTERNATE", "placed": 10, "settled": 10, "wins": 6, "losses": 4, "hit_rate": 0.6, "profit_loss": 12.0, "roi": 0.12, "expected_wins": 5.9}]},
                                "calibration": [{"band": "55-65%", "n": 10, "predicted": 0.59, "actual": 0.6, "delta_wins": 0.1}], "day_breakdowns": {},
                               "cumulative_price_age": [{"bucket": "31-60 min", "n": 4, "settled": 4, "wins": 3, "losses": 1, "pushes_voids": 0, "hit_rate": 0.75, "profit_loss": 9.0, "roi": 0.22,
                                                         "average_model_probability": 0.7, "average_implied_probability": 0.6, "average_estimated_edge": 0.04, "expected_wins": 2.8, "actual_wins": 3}]}, "final_report": None}

    def _text(self, at):
        from tests.test_product_pages import text
        return text(at) + " " + " ".join(str(m.label) + str(m.value) for m in at.metric)

    def test_the_page_shows_the_experiment_separately_and_still_shows_the_model_book(self):
        from tests.test_product_pages import run_page
        at = run_page("33_Paper_Performance.py", self._snap(self._section()))
        self.assertFalse(at.exception)
        t = self._text(at)
        for needle in ("14-DAY POSTMORTEM LEARNING EXPERIMENT", "Day 2 of 14", "Today's target", "7/10", "-150 or better", "Current cash", "Betting P/L", "Reset count", "Total reset funding", "Eggy", "By price age at recording", "Price age is recorded, never a reason to skip a bet"):
            self.assertIn(needle, t, needle)
        self.assertIn("Available cash", t)           # the model book is still there

    def _section_with_reviews(self):
        """A section whose bets carry real review cards (built by the review itself on a synthetic game) and a postmortem with the decision review, so the page's card renderer runs on the real shape."""
        from operational import eggy_decisions as ed
        from tests import test_eggy_intelligence as t
        ctx = t.make_ctx()
        ctx.skater_state = {"players": {"9": t.skater()}, "newest_game_date": "2026-10-19"}
        ctx.by_player = {"9": t.history_rows()}
        with mock.patch("research.product_models.live.project_matchup", return_value=t.projection(0.72)):
            prop = lc.ei.compact(lc.ei.review_leg(ctx, t.prop_leg(price=-130.0, p=0.72), "ANCHORED_RECORD_ESTIMATE", 0.72))
        mlcard = lc.ei.compact(lc.ei.review_leg(ctx, t.ml_leg(ctx, "BBB", 0.0), "MARKET_NO_VIG", 0.3))
        facts = {"game": {"home": "AAA", "away": "BBB", "home_score": 4, "away_score": 1, "period_type": "REG", "winner": "AAA", "team_stats": {"AAA": {"xg_for": 3.1}, "BBB": {"xg_for": 1.2}}},
                 "player": {"source": "MoneyPuck game log", "toi": 12.0, "toi_pp": 0.0, "shots": 1.0, "goals": 0.0, "assists": 0.0, "points": 0.0}}
        bet = {"market_family": "PLAYER_SOG_ALTERNATE", "participant_id": "9", "game_id": 1, "team": "AAA", "result_status": "LOSS"}
        rv_prop = {"bet_id": "B1", "et_date": "2026-10-20", "label": prop["bet"]["label"], "result_status": "LOSS", "source": "RECORDED", "card": prop, "facts": facts, "facts_complete": True, "assessment": ed.assess(bet, prop, facts)}
        rv_ml = {"bet_id": "B2", "et_date": "2026-10-20", "label": mlcard["bet"]["label"], "result_status": "LOSS", "source": "RECONSTRUCTED", "card": dict(mlcard, reconstructed=True, reconstruction_note="reconstructed note"),
                 "facts": facts, "facts_complete": True, "assessment": ed.assess(dict(bet, market_family="MONEYLINE", participant_id="BBB", team="BBB"), mlcard, facts)}
        sec = self._section()
        sec["bets_today"] = [{"bet_id": "B1", "selection_rank": 1, "market_family": "PLAYER_SOG_ALTERNATE", "participant_name": "Test Player", "team": "AAA", "threshold": 2, "american_price": -130.0, "conservative_probability": 0.72,
                              "edge": 0.15, "result_status": "LOSS", "profit_loss": -10.0, "eggy_relation": "EGGY_NOT_INVOLVED", "price_status": "RECENT", "price_age_at_recording_min": 9.0, "edge_class": "POSITIVE_EDGE",
                              "thesis_type": "MODEL_SEES_VALUE", "action": "RECORD", "contradiction_level": "LOW", "calibration_level": "HIGH", "decision_confidence": "HIGH", "label": prop["bet"]["label"]}]
        sec["cards"] = {"B1": prop}
        pm = sec["postmortem"]
        pm["bet_reviews"] = {"B1": rv_prop, "B2": rv_ml}
        pm["decision_review"] = {"grid": [{"decision": "GOOD_DECISION", "outcome": "LOSS", "n": 1}, {"decision": "BAD_DECISION", "outcome": "LOSS", "n": 1}], "lucky_wins": 0, "earned_normal_variance": 0, "process_failures": 1,
                                 "not_variance": 1, "reconstructed": 1, "bets_reviewed": 2, "note": "OUTCOME and DECISION are separate results."}
        pm["day_bets"] = [{"rank": 1, "bet": prop["bet"]["label"], "bet_id": "B1", "result": "LOSS", "decision": "GOOD_DECISION", "classification": "ROLE_CHANGE_MISSED", "action": "INVESTIGATE"},
                          {"rank": 2, "bet": mlcard["bet"]["label"], "bet_id": "B2", "result": "LOSS", "decision": "BAD_DECISION", "classification": "MODEL_CALIBRATION", "action": "INVESTIGATE"}]
        return sec

    def test_every_bet_opens_into_the_whole_review_and_the_postmortem_shows_two_results(self):
        from tests.test_product_pages import run_page
        at = run_page("33_Paper_Performance.py", self._snap(self._section_with_reviews()))
        self.assertFalse(at.exception)
        t = self._text(at) + " " + " ".join(str(e.label) for e in at.expander)
        for needle in ("Test Player — SHOTS ON GOAL 2+ — -130", "BBB — MONEYLINE — ", "WHY THIS TEAM / PLAYER", "WHY TONIGHT", "WHY THIS MARKET", "WHY THIS PRICE", "WHO IS MOST LIKELY TO WIN", "WHAT THE MODEL BELIEVES",
                       "WHAT THE MARKET BELIEVES", "WHY THEY DISAGREE", "WHAT SUPPORTS THE BET", "WHAT ARGUES AGAINST IT", "WHAT HISTORICAL EVIDENCE SAYS", "WHAT DATA IS MISSING", "WHAT WOULD MAKE EGGY PASS",
                       "OUTCOME", "DECISION RESULT", "WHAT HAPPENED", "WAS THE THESIS RIGHT?", "WHAT TO LEARN", "CLASSIFICATION", "RECONSTRUCTED", "Decision quality against outcome"):
            self.assertIn(needle, t, needle)

    def test_a_final_report_left_on_disk_is_not_shown_while_the_experiment_is_running(self):
        from operational import learning_postmortem as lpm
        with mock.patch.object(lpm, "load_final", return_value={"statement": "x", "answers": {}}), mock.patch.object(lpm, "load", return_value={}), mock.patch.object(lpm.lc, "load_status", return_value=None):
            with mock.patch.object(lpm.lc, "phase", return_value={"phase": "ACTIVE", "day": 2, "today": "2026-10-21"}):
                self.assertIsNone(lpm.snapshot_section()["final_report"])
            with mock.patch.object(lpm.lc, "phase", return_value={"phase": "ENDED", "day": 14, "today": "2026-11-03"}):
                self.assertEqual(lpm.snapshot_section()["final_report"]["statement"], "x")

    def test_a_bet_without_a_review_says_so_instead_of_inventing_one(self):
        from tests.test_product_pages import run_page
        sec = self._section_with_reviews()
        sec["cards"] = {}
        sec["postmortem"]["bet_reviews"] = {}
        at = run_page("33_Paper_Performance.py", self._snap(sec))
        self.assertFalse(at.exception)
        self.assertIn("There is no review on file for this bet", self._text(at))

    def test_an_older_snapshot_without_the_section_changes_nothing(self):
        from tests.test_product_pages import run_page
        at = run_page("33_Paper_Performance.py", self._snap(None))
        self.assertFalse(at.exception)
        self.assertIn("Available cash", self._text(at))

    def test_an_experiment_not_yet_activated_says_so(self):
        from tests.test_product_pages import run_page
        sec = {"cohort": lc.COHORT, "authorised": False, "phase": "ACTIVE", "day": 1, "days": 14}
        at = run_page("33_Paper_Performance.py", self._snap(sec))
        self.assertFalse(at.exception)
        self.assertIn("not recording yet", self._text(at))

    def test_the_ended_state_is_stated(self):
        from tests.test_product_pages import run_page
        sec = dict(self._section(), phase="ENDED", day=14)
        at = run_page("33_Paper_Performance.py", self._snap(sec))
        self.assertFalse(at.exception)
        self.assertIn("ended", self._text(at))


class TestReviewFindings(Base):
    def test_the_trader_hook_survives_every_summary_shape(self):
        from operational import real_parlay_paper_trader as tr
        ended = self.cycle([leg()], now=dt.datetime(2026, 11, 3, 15, 0, tzinfo=dt.timezone.utc))
        self.assertIsInstance(ended["recording"], dict)
        for summary in (ended, {"status": "NOT_AUTHORISED"}, {"status": "ERROR", "error": "x"}, {"recording": "NO_NEW_BETS: text"}, {"settlement": None}, {}):
            self.assertIsInstance(tr._learning_changed(summary), bool)
        self.assertTrue(tr._learning_changed({"recording": {"recorded_this_cycle": 2}}))
        self.assertTrue(tr._learning_changed({"settlement": {"settled": 1}}))
        self.assertFalse(tr._learning_changed({"recording": {"recorded_this_cycle": 0}, "settlement": {"settled": 0}}))

    def test_the_recorded_probability_is_the_books_anchored_estimate_not_a_raw_model_number(self):
        hot = leg(p=0.80, model_probability=0.70, empirical_probability=0.95, empirical_hits=29, empirical_games=30)
        self.cycle([hot])
        r = self.rows()[0]
        self.assertAlmostEqual(r["conservative_probability"], 0.72, places=4)           # the record held to the model + 0.02
        self.assertEqual(r["probability_basis"], "ANCHORED_RECORD_ESTIMATE")
        self.assertAlmostEqual(r["source_conservative_probability"], 0.80, places=4)
        self.assertAlmostEqual(r["model_probability"], 0.70, places=4)

    def test_a_thin_record_falls_back_to_the_model_probability_and_is_labelled(self):
        thin = leg(p=0.75, model_probability=0.66, empirical_probability=0.9, empirical_hits=9, empirical_games=10)
        self.cycle([thin])
        r = self.rows()[0]
        self.assertEqual(r["probability_basis"], "MODEL_PROBABILITY_RECORD_NOT_USABLE")
        self.assertLessEqual(r["conservative_probability"], 0.66)

    def test_points_and_goals_are_labelled_unanchored_and_a_moneyline_uses_the_market(self):
        self.cycle([leg(pid="8470001", fam="PLAYER_POINTS", k=1, price=100.0, p=0.62), ml(team="TOR", game="2", p=0.60, market_probability=0.57)])
        by = {r["market_family"]: r for r in self.rows()}
        self.assertEqual(by["PLAYER_POINTS"]["probability_basis"], "MODEL_PROBABILITY_UNANCHORED")
        self.assertEqual((by["MONEYLINE"]["probability_basis"], by["MONEYLINE"]["conservative_probability"]), ("MARKET_NO_VIG", 0.57))

    def test_both_sides_of_a_moneyline_are_never_both_recorded(self):
        self.cycle([ml(team="TOR", price=-140.0, p=0.58, game="1"), dataclasses.replace(ml(team="MTL", price=120.0, p=0.45, game="1"), side="AWAY")])
        self.assertEqual([r["market_family"] for r in self.rows()], ["MONEYLINE"])

    def test_one_threshold_per_player_statistic_and_the_two_shots_families_are_one_statistic(self):
        self.cycle([leg(k=2, p=0.85), leg(k=3, p=0.7, price=110.0), leg(fam="PLAYER_SOG", k=2, p=0.84), leg(fam="PLAYER_POINTS", k=1, p=0.6, price=100.0)])
        self.assertEqual(sorted((r["market_family"], r["threshold"]) for r in self.rows()), [("PLAYER_POINTS", 1), ("PLAYER_SOG_ALTERNATE", 2)])

    def test_a_held_bet_blocks_a_later_overlapping_one(self):
        self.cycle([leg(k=2, p=0.8)])
        self.cycle([leg(k=3, p=0.7, price=110.0)])
        self.assertEqual(len(self.rows()), 1)

    def test_open_bets_still_settle_after_the_digest_changes_or_the_window_closes(self):
        self.cycle([leg()])
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id='1'")
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1','8478402','TOR',20.0,0,0,4,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')")
        self.nhl.commit()
        with mock.patch.object(lc, "is_authorised", return_value=False):
            out = lc.run_cycle(self.nhl, dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc), {"legs": [leg(pid="8479999")]}, conn=self.conn)
        self.assertEqual(out["status"], "NOT_AUTHORISED")
        self.assertEqual(out["settlement"]["settled"], 1)
        self.assertEqual(len(self.rows()), 1)                    # and nothing new was recorded

    def test_a_settled_result_and_every_entry_field_are_locked(self):
        self.cycle([leg()])
        self.conn.execute("UPDATE bets SET result_status='WIN', profit_loss=8.0, return_amount=18.0, settled_at_utc='x'")
        for col, val in (("result_status", "LOSS"), ("profit_loss", -10.0), ("return_amount", 0.0), ("decimal_price", 9.0), ("team", "XXX"), ("event_start_utc", "x"), ("settlement_market_id", "x"),
                         ("policy_digest", "x"), ("selection_reason", "x"), ("experiment_day", 9), ("dependence_json", "{}"), ("eggy_relation", "x"), ("probability_basis", "x"), ("quote_updated_utc", "x")):
            with self.assertRaises(sqlite3.DatabaseError, msg=col):
                self.conn.execute(f"UPDATE bets SET {col} = ?", (val,))

    def test_a_bet_cannot_be_replaced_by_insert_or_replace(self):
        self.cycle([leg()])
        row = dict(self.rows()[0])
        cols = ", ".join(row)
        row["stake"] = 9999.0
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute(f"INSERT OR REPLACE INTO bets ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
        self.assertEqual(self.rows()[0]["stake"], 10.0)

    def test_initial_funding_can_only_happen_once(self):
        lc.ensure_funded(self.conn, D1)
        with self.assertRaises(sqlite3.DatabaseError):
            lc._fund(self.conn, D1, "INITIAL", 500.0, "x", 1)

    def test_an_owner_pause_stops_recording_but_not_settlement(self):
        with mock.patch.object(lc, "_paused", return_value=True):
            out = self.cycle([leg()])
        self.assertEqual(out["recording"]["status"], "PAUSED_BY_OWNER")
        self.assertEqual(self.rows(), [])
        self.assertIn("settlement", out)

    def test_a_losing_bet_never_sets_the_pause(self):
        import inspect
        self.assertNotIn("recording_pause.pause", inspect.getsource(lc))
        self.assertNotIn("recording_pause.set", inspect.getsource(lc))

    def test_a_key_already_on_the_ledger_from_another_day_is_skipped_not_fatal(self):
        self.cycle([leg()])
        out = self.cycle([leg(), leg(pid="8470002")])
        self.assertEqual(out["status"], "OK")
        self.assertEqual(len(self.rows()), 2)

    def test_the_engines_own_leg_gate_applies_unchanged(self):
        self.assertTrue(lc.eligibility_reasons(leg(k=9), D1))                     # a threshold the engine does not allow
        self.assertTrue(lc.eligibility_reasons(leg(fam="GOALIE_SAVES", k=7, price=-130.0), D1))

    def test_the_digest_covers_the_behaviour_constants(self):
        d = lc.policy_digest()
        with mock.patch.dict(lc.POLICY["behaviour"], {"relax_min": 99}):
            self.assertNotEqual(lc.policy_digest(), d)

    def test_the_status_document_is_dated_and_publishable(self):
        from operational import cloud_snapshot_schema as schema
        self.cycle([leg()])
        s = lc.status_doc(D1, conn=self.conn)
        self.assertEqual(s["generated_at_utc"], "2026-10-20T21:00:00Z")
        self.assertEqual(json.loads(schema.strict_dumps(s))["recorded_today"], 1)


class TestEdgeClasses(Base):
    def _cls(self, est, price=-130.0, **kw):
        card = fake_card(leg(price=price, p=est), est, **kw)
        return lc.ei.edge_class_of(card)[0]

    def test_classification_thresholds_and_calibration(self):
        implied = 130 / 230
        self.assertEqual(self._cls(implied + 0.05), "POSITIVE_EDGE")
        self.assertEqual(self._cls(implied + 0.0301), "POSITIVE_EDGE")
        self.assertEqual(self._cls(implied + 0.05, cal="LOW"), "NEUTRAL_UNCERTAIN_EDGE")                # an edge on an estimate nobody can vouch for is uncertain, not positive
        self.assertEqual(self._cls(implied + 0.02), "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(self._cls(implied - 0.0299), "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(self._cls(implied - 0.03), "LEARNING_SAMPLE_NEGATIVE_EDGE")                     # the band is three points, the main engine's own edge threshold (it was two)
        self.assertEqual(self._cls(implied - 0.08), "LEARNING_SAMPLE_NEGATIVE_EDGE")

    def test_a_moneyline_is_classed_on_the_hockey_model_not_on_the_market_anchored_estimate(self):
        l = ml(team="MTL", price=240.0, p=0.282, market_probability=0.282)
        implied = 100 / 340
        anchored = fake_card(l, 0.282, model_p=0.174)                          # the estimate used is the market's; the hockey model says 17.4%
        self.assertEqual(lc.ei.edge_class_of(anchored)[0], "LEARNING_SAMPLE_NEGATIVE_EDGE")
        self.assertAlmostEqual(lc.ei.edge_for_class(anchored)[0], 0.174 - implied, places=4)
        agrees = fake_card(l, 0.282, model_p=0.30)
        self.assertEqual(lc.ei.edge_class_of(agrees)[0], "NEUTRAL_UNCERTAIN_EDGE")

    def test_every_bet_is_tagged_at_recording_time_with_a_reason(self):
        pos = leg(pid="8470001", p=0.80, price=-130.0)
        neu = leg(pid="8470002", p=0.58, price=-130.0)
        self.cycle([pos, neu], now=dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc))
        by = {r["participant_id"]: r for r in self.rows()}
        self.assertEqual(by["8470001"]["edge_class"], "POSITIVE_EDGE")
        self.assertIn("estimate less price", by["8470001"]["edge_class_reason"])
        self.assertIn(by["8470002"]["edge_class"], ("NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE"))
        self.assertTrue(by["8470001"]["selection_reason"].startswith("POSITIVE_EDGE"))

    def _neg(self, pid="8470009", game="1"):
        return leg(pid=pid, game=game, p=0.50, model_probability=0.5, price=-148.0, empirical_games=30)

    def test_a_lower_priority_candidate_waits_until_no_better_price_can_arrive_then_fills_the_sample(self):
        early = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)           # 17:00 ET, two hours before the 19:00 drop
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)           # 18:45 ET, inside the final 25 minutes
        self.cards["8470009"] = {"action": "LOWER_PRIORITY", "thesis": "PRICE_ABOVE_ESTIMATE"}
        out = self.cycle([self._neg()], now=early)
        self.assertEqual(self.rows(), [])
        self.assertEqual(out["recording"]["shortfall"]["lower_priority_waiting_for_their_final_window"], 1)
        self.cycle([self._neg()], now=late)
        r = self.rows()
        self.assertEqual((len(r), r[0]["edge_class"], r[0]["action"]), (1, "LEARNING_SAMPLE_NEGATIVE_EDGE", "LOWER_PRIORITY"))
        self.assertIn("learning sample", r[0]["edge_class_reason"])

    def test_negative_edge_never_displaces_a_better_candidate(self):
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)
        legs = [self._neg(pid=str(8471000 + i), game="1" if i % 2 else "2") for i in range(8)] + [leg(pid=str(8472000 + i), game="1" if i % 2 else "2", p=0.75) for i in range(5)]
        self.cycle(legs, now=late)
        cls = [r["edge_class"] for r in self.rows()]
        self.assertGreaterEqual(len(cls), 5)
        first_neg = next((i for i, c in enumerate(cls) if c == "LEARNING_SAMPLE_NEGATIVE_EDGE"), len(cls))
        self.assertTrue(all(c != "LEARNING_SAMPLE_NEGATIVE_EDGE" for c in cls[:first_neg]))
        self.assertEqual(sum(1 for c in cls[:first_neg]), 5)           # all five better candidates were taken before any negative one

    def test_a_negative_edge_candidate_still_needs_every_gate(self):
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)
        bad = dataclasses.replace(self._neg(), identity_resolved=False)
        self.cycle([bad, dataclasses.replace(self._neg(pid="8470010"), identity_resolved=False), dataclasses.replace(self._neg(pid="8470011"), american_price=-155.0)], now=late)
        self.assertEqual(self.rows(), [])

    def test_the_edge_class_is_locked_on_the_ledger(self):
        self.cycle([leg()])
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE bets SET edge_class = 'NEUTRAL_UNCERTAIN_EDGE'")

    def test_postmortem_reports_each_class_separately_with_every_measure(self):
        from operational import learning_postmortem as lpm
        for i, (cls, st, price) in enumerate([("POSITIVE_EDGE", "WIN", -120.0), ("POSITIVE_EDGE", "LOSS", -120.0), ("LEARNING_SAMPLE_NEGATIVE_EDGE", "LOSS", -145.0),
                                              ("LEARNING_SAMPLE_NEGATIVE_EDGE", "WIN", -145.0), ("NEUTRAL_UNCERTAIN_EDGE", "WIN", 110.0)]):
            self._bet(i + 1, "1", str(i), price, 0.6, st, edge_class=cls)
        t = {r["edge_class"]: r for r in lpm.edge_class_table(lpm.rows(self.conn))}
        self.assertEqual(set(t), {"POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE"})
        p = t["POSITIVE_EDGE"]
        self.assertEqual((p["n"], p["wins"], p["losses"], p["pushes_voids"], p["hit_rate"]), (2, 1, 1, 0, 0.5))
        for k in ("average_decimal_odds", "average_model_probability", "average_implied_probability", "average_estimated_edge", "profit_loss", "roi", "expected_wins", "calibration_delta"):
            self.assertIn(k, p)
        self.assertNotEqual(p["profit_loss"], t["LEARNING_SAMPLE_NEGATIVE_EDGE"]["profit_loss"])
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(len(doc["day_edge_classes"]), 3)
        self.assertEqual(len(doc["cumulative_edge_classes"]), 3)

    def test_the_final_report_compares_the_classes_without_overclaiming(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        self._bet(1, "1", "1", -120.0, 0.6, "WIN", edge_class="POSITIVE_EDGE")
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lpm.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            lpm.final_report_if_ready(self.conn, self.nhl, dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc))
            rep = json.loads(Path(t, lpm.REPORT_NAME).read_text())
        self.assertEqual(len(rep["answers"]["edge_class_results"]), 3)
        self.assertIn("no conclusion is drawn", rep["answers"]["did_positive_edge_outperform"])

    def test_the_reset_is_reachable_and_the_bankroll_starts_at_500(self):
        lc.ensure_funded(self.conn, D1)
        for i in range(49):
            self._bet(i + 1, "1", f"p{i}", -120.0, 0.6, "LOSS")
        self.assertEqual(lc.balance(self.conn)["cash"], 10.0)
        self._bet(50, "1", "p50", -120.0, 0.6, "LOSS")
        self.assertEqual(lc.balance(self.conn)["cash"], 0.0)
        ev = lc.reset_if_needed(self.conn, D1, 5)
        self.assertEqual((ev["balance_before"], ev["amount"], ev["balance_after"]), (0.0, 500.0, 500.0))
        b = lc.balance(self.conn)
        self.assertEqual((b["betting_pl"], b["reset_count"], b["reset_funding_added"], b["cash"]), (-500.0, 1, 500.0, 500.0))
        self.assertEqual(b["roi"], -1.0)           # the reset never softens ROI


class TestEdgeClassReviewFixes(Base):
    LATE = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)           # 18:45 ET: inside the final 25 minutes before the 19:00 drop

    def _neg(self, pid, game="1", **kw):
        return leg(pid=pid, game=game, p=0.50, model_probability=0.5, price=-148.0, empirical_games=30, **kw)

    def test_a_negative_candidate_does_not_shadow_a_better_classed_alternative_of_the_same_exposure(self):
        fav = ml(team="TOR", price=-140.0, p=0.5, market_probability=0.50)                                     # edge about -7 pts: negative
        dog = dataclasses.replace(ml(team="MTL", price=120.0, p=0.46, market_probability=0.455), side="AWAY")  # edge about -1 pt: neutral
        self.cycle([fav, dog], now=dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc))
        self.assertEqual([(r["participant_id"], r["edge_class"]) for r in self.rows()], [("MTL", "NEUTRAL_UNCERTAIN_EDGE")])

    def test_negative_fill_never_uses_the_relaxed_headroom_held_for_better_candidates(self):
        legs = [self._neg(str(8473000 + i), game="1" if i % 2 else "2") for i in range(9)]
        self.cards.update({l.participant_id: {"action": "LOWER_PRIORITY", "thesis": "PRICE_ABOVE_ESTIMATE"} for l in legs})
        self.cycle(legs, now=self.LATE)
        self.assertLessEqual(len(self.rows()), 7)           # wave 0's proportional cap (7 of 10), not the relaxed 9

    def test_the_final_window_is_the_bets_own_game_not_the_waves_first_game(self):
        games = (("1", START1), ("2", START1), ("4", "2026-10-21T00:15:00"))           # game 4 drops 75 minutes after the wave's first game
        self.nhl.close()
        self.nhl = nhl(games)
        late_game = self._neg("8474001", game="4", game_start_utc="2026-10-21T00:15:00", provider_start_utc="2026-10-21T00:15:00Z")
        self.cards["8474001"] = {"action": "LOWER_PRIORITY", "thesis": "PRICE_ABOVE_ESTIMATE"}
        self.cycle([late_game], now=self.LATE)
        self.assertEqual(self.rows(), [])                  # 18:45 ET is 90 minutes before ITS puck drop: better prices can still arrive
        self.cycle([late_game], now=dt.datetime(2026, 10, 20, 23, 52, tzinfo=dt.timezone.utc))          # 8 minutes before its own drop
        self.assertEqual([r["edge_class"] for r in self.rows()], ["LEARNING_SAMPLE_NEGATIVE_EDGE"])

    def test_the_comparison_text_never_overclaims(self):
        from operational import learning_postmortem as lpm
        lc.ensure_funded(self.conn, D1)
        for i in range(32):
            self._bet(i + 1, "1", f"a{i}", -120.0, 0.6, "WIN" if i % 2 else "LOSS", edge_class="POSITIVE_EDGE")
            self._bet(i + 1, "1", f"b{i}", -120.0, 0.6, "WIN" if i % 3 else "LOSS", edge_class="LEARNING_SAMPLE_NEGATIVE_EDGE")
        with tempfile.TemporaryDirectory() as t, mock.patch.object(lpm.state_paths, "path", side_effect=lambda n, **k: Path(t) / n):
            lpm.final_report_if_ready(self.conn, self.nhl, dt.datetime(2026, 11, 4, 12, 0, tzinfo=dt.timezone.utc))
            rep = json.loads(Path(t, lpm.REPORT_NAME).read_text())
        text = rep["answers"]["did_positive_edge_outperform"]
        self.assertIn("did NOT outperform", text)
        self.assertIn("suggestive only", text)

    def test_the_morning_report_carries_each_edge_class_and_the_reset_funding_line(self):
        from operational import daily_postmortem as dpm
        row = {"edge_class": "POSITIVE_EDGE", "n": 3, "wins": 2, "losses": 1, "pushes_voids": 0, "hit_rate": 0.67, "average_model_probability": 0.7, "average_implied_probability": 0.6,
               "average_estimated_edge": 0.03, "profit_loss": 4.0, "roi": 0.13, "expected_wins": 2.1, "actual_wins": 2}
        dm = {"placed": 3, "settled": 3, "wins": 2, "losses": 1, "voids": 0, "hit_rate": 0.67, "profit_loss": 4.0, "roi": 0.13, "expected_wins": 2.1}
        lcp = {"cohort": lc.COHORT, "postmortem_for": "2026-10-20", "day_metrics": dm, "day_edge_classes": [row], "cumulative_edge_classes": [row], "cumulative": dm,
               "bankroll": {"cash": 490.0, "betting_pl": 4.0, "reset_count": 1, "reset_funding_added": 495.0}}
        report = {"what_worked": "x", "what_didnt": "x", "why": [], "normal_variance_vs_systematic": "x", "investigate": [], "learning_cohort_postmortem": lcp}
        with tempfile.TemporaryDirectory() as t:
            md = dpm.write_report_markdown(report, out_dir=Path(t)).read_text()
        self.assertIn("edge class POSITIVE_EDGE (day)", md)
        self.assertIn("edge class POSITIVE_EDGE (cumulative)", md)
        self.assertIn("$495.0 in funding (not profit)", md)
        self.assertIn("betting P/L 4.0", md)

    def test_the_initial_funding_note_states_the_corrected_start(self):
        lc.ensure_funded(self.conn, D1)
        row = self.conn.execute("SELECT amount, note FROM funding_events WHERE kind='INITIAL'").fetchone()
        self.assertEqual((row["amount"], "500.00" in row["note"]), (500.0, True))


class TestPriceAgeIsMetadata(Base):
    def _old(self, minutes, **kw):
        """A real quote last observed `minutes` before D1 (the legs keep their own times: cycle(fresh=False))."""
        t = D1 - dt.timedelta(minutes=minutes)
        stamp = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        return leg(quote_updated_utc=stamp, retrieved_at_utc=stamp, captured_at_utc=stamp, quote_age_min=float(minutes), freshness_status="STALE_QUOTE" if minutes > 10 else "FRESH", price_fresh=minutes <= 10, **kw)

    def test_an_old_real_quote_is_recorded_with_its_age_and_status(self):
        self.cycle([self._old(34)], fresh=False)
        r = self.rows()[0]
        self.assertEqual((r["price_status"], r["price_age_bucket"], r["price_age_at_recording_min"]), ("LAST_KNOWN_PREGAME", "31-60 min", 34.0))
        self.assertEqual(r["price_capture_timestamp"], (D1 - dt.timedelta(minutes=34)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(r["price_source"], "LIVE_DRAFTKINGS")
        self.assertIn("LAST_KNOWN_PREGAME", r["selection_reason"])
        self.assertEqual(r["american_price"], -130.0)               # the actual last observed price, never adjusted

    def test_a_very_old_pregame_quote_is_still_a_valid_learning_bet(self):
        self.cycle([self._old(480)], fresh=False)                   # a morning price
        r = self.rows()[0]
        self.assertEqual((r["price_status"], r["price_age_bucket"]), ("VERY_OLD_PREGAME", "120+ min"))

    def test_status_and_bucket_boundaries(self):
        self.assertEqual([lc.price_age_status(x) for x in (0, 5, 5.1, 30, 31, 120, 121)], ["CURRENT", "CURRENT", "RECENT", "RECENT", "LAST_KNOWN_PREGAME", "LAST_KNOWN_PREGAME", "VERY_OLD_PREGAME"])
        self.assertEqual([lc.price_age_bucket(x) for x in (0, 5, 6, 15, 16, 30, 31, 60, 61, 120, 121)],
                         ["0-5 min", "0-5 min", "6-15 min", "6-15 min", "16-30 min", "16-30 min", "31-60 min", "31-60 min", "61-120 min", "61-120 min", "120+ min"])

    def test_accuracy_gates_remain_hard_for_an_old_quote(self):
        for bad in (self._old(34, identity_resolved=False), self._old(34, event_not_started=False), self._old(34, model_threshold_eligible=False), self._old(34, provider_contract_verified=False),
                    self._old(34, price=-155.0), self._old(34, fam="PUCK_LINE"), self._old(34, k=9)):
            self.assertTrue(lc.eligibility_reasons(bad, D1), (bad.market_family, bad.threshold))
        self.assertEqual(lc.eligibility_reasons(self._old(34), D1), [])

    def test_a_quote_without_a_real_observation_time_is_rejected(self):
        for kw in ({"freshness_status": "MISSING_QUOTE_TIMESTAMP"}, {"freshness_status": "MALFORMED_QUOTE_TIMESTAMP"}, {"freshness_status": "FUTURE_QUOTE_TIMESTAMP"},
                   {"quote_updated_utc": None, "retrieved_at_utc": None, "captured_at_utc": None},
                   {"quote_updated_utc": "garbage", "retrieved_at_utc": "garbage", "captured_at_utc": "garbage"},
                   {"quote_updated_utc": "2026-10-20T23:30:00Z", "retrieved_at_utc": "2026-10-20T23:30:00Z"}):          # in the future
            self.assertTrue(lc.eligibility_reasons(dataclasses.replace(self._old(34), **kw), D1), kw)

    def test_a_started_game_with_an_old_quote_is_not_recorded(self):
        late = D1 + dt.timedelta(hours=3)
        self.assertTrue(lc.eligibility_reasons(self._old(34), late))

    def test_ranking_ignores_price_age(self):
        import inspect
        keys = []
        for l in (self._old(2), self._old(300)):
            card = fake_card(l, l.conservative_probability)
            ecls, _ = lc.ei.edge_class_of(card)
            edge, _what = lc.ei.edge_for_class(card)
            keys.append(lc.ei.rank_key(card, ecls, card["estimate_used"], edge, "k"))
        self.assertEqual(keys[0], keys[1])
        self.assertFalse([n for n in inspect.signature(lc.ei.rank_key).parameters if "age" in n or "fresh" in n])

    def test_the_strict_engine_paths_are_unchanged_outside_the_cohort(self):
        from operational import daily_tickets as dtk
        from research.real_market_parlay import real_slate_adapter as adapter
        start = "2026-10-20T23:00:00Z"
        old = (D1 - dt.timedelta(minutes=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertFalse(adapter._price_freshness(old, start, D1)["fresh"])                    # default: an old quote is excluded
        with adapter.advisory_price_age():
            v = adapter._price_freshness(old, start, D1)
            self.assertTrue(v["fresh"] and v["advisory_old"] and v["status"] == "STALE_QUOTE")
            self.assertFalse(adapter._price_freshness(None, start, D1)["fresh"])               # a missing time is never advisory
            self.assertFalse(adapter._price_freshness("2026-10-21T05:00:00Z", start, D1)["fresh"])          # nor a future one
        self.assertFalse(adapter._price_freshness(old, start, D1)["fresh"])                    # and the context does not leak
        combo = SimpleNamespace(legs=[self._old(300)])
        self.assertTrue(dtk.revalidate_before_recording(combo, D1))                            # the normal book still refuses an old quote at recording
        self.assertEqual(dtk.revalidate_before_recording(combo, D1, price_age_is_advisory=True), [])

    def test_the_cohort_collects_its_own_pool_and_the_normal_pool_is_untouched(self):
        seen = []
        out = lc.run_cycle(self.nhl, D1, {"legs": []}, conn=self.conn, collector=lambda: seen.append(1) or {"legs": [self._old(300)], "sources": {}})
        self.assertEqual(seen, [1])
        self.assertEqual(out["recording"]["recorded_today"], 1)
        bad = lc.run_cycle(self.nhl, D1, {"legs": []}, conn=self.conn, collector=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.assertEqual(bad["recording"]["status"], "POOL_UNAVAILABLE")

    def test_an_old_ledger_is_migrated_and_the_new_fields_are_then_protected(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "old.db"
            c = lc.open_ledger(path)
            self.cycle([leg()])                                    # prove the helper works on memory; now build the old-shape ledger by hand
            c.close()
            old = sqlite3.connect(path)
            old.row_factory = sqlite3.Row
            old.executescript("DROP TRIGGER IF EXISTS bets_entry_is_immutable;")
            for col, _ in lc.PRICE_COLUMNS:
                old.execute(f"ALTER TABLE bets DROP COLUMN {col}")
            row = dict(self.rows()[0])
            for col, _ in lc.PRICE_COLUMNS:
                row.pop(col)
            old.execute(f"INSERT INTO bets ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            old.commit()
            old.close()
            m = lc.open_ledger(path)
            r = m.execute("SELECT price_status, price_age_at_recording_min, price_age_bucket FROM bets").fetchone()
            self.assertEqual(r["price_status"], "RECENT")
            self.assertEqual(r["price_age_at_recording_min"], 9.0)          # observed 9 minutes before it was recorded
            with self.assertRaises(sqlite3.DatabaseError):
                m.execute("UPDATE bets SET price_status = 'CURRENT'")
            m.close()

    def test_postmortem_age_buckets_and_selection_versus_price_quality(self):
        from operational import learning_postmortem as lpm
        specs = [(3, "WIN"), (10, "LOSS"), (20, "WIN"), (45, "LOSS"), (90, "WIN"), (200, "LOSS"), (4, "LOSS")]
        for i, (age, st) in enumerate(specs):
            self._bet(i + 1, "1", f"x{i}", -120.0, 0.6, st)
            self.conn.execute("DROP TRIGGER bets_entry_is_immutable") if i == 0 else None
            self.conn.execute("UPDATE bets SET price_age_at_recording_min = ?, price_status = ? WHERE bet_id = ?", (float(age), lc.price_age_status(age), f"B{i + 1}1x{i}"))
        t = {r["bucket"]: r for r in lpm.price_age_table(lpm.rows(self.conn))}
        self.assertEqual(list(t), ["0-5 min", "6-15 min", "16-30 min", "31-60 min", "61-120 min", "120+ min"])
        self.assertEqual((t["0-5 min"]["n"], t["0-5 min"]["wins"], t["0-5 min"]["losses"]), (2, 1, 1))
        for k in ("hit_rate", "profit_loss", "roi", "average_model_probability", "average_implied_probability", "average_estimated_edge", "calibration_delta", "expected_ev_per_stake"):
            self.assertIn(k, t["0-5 min"])
        self.assertEqual([r["price_status"] for r in lpm.price_status_table(lpm.rows(self.conn))], ["CURRENT", "RECENT", "LAST_KNOWN_PREGAME", "VERY_OLD_PREGAME"])

    def test_dependence_is_measured_not_assumed(self):
        from operational import learning_postmortem as lpm
        for i, st in enumerate(["WIN", "WIN", "LOSS"]):
            self._bet(i + 1, "7", f"d{i}", -120.0, 0.6, st)
        d = lpm.dependence_report(lpm.rows(self.conn))
        g = d["games_with_multiple_bets"][0]
        self.assertEqual((g["bets"], g["actual_hits"], g["pairs"], g["pairs_both_hit"]), (3, 2, 3, 1))
        self.assertAlmostEqual(g["pairs_both_hit_if_independent"], 3 * 0.36, places=2)
        self.assertIn("not enough", d["reading"])
        self.assertIn("not captured", d["not_captured"].lower())

    def test_the_policy_says_age_is_not_a_gate_and_the_digest_moved(self):
        self.assertFalse(lc.POLICY["price_age_policy"]["gate"])
        self.assertIn("MODEL EDGE AT LAST OBSERVED PRICE", lc.POLICY["price_age_policy"]["edge_label"])
        self.assertNotEqual(lc.policy_digest(), "df96584493d9d3e8")


class TestPriceAgeReviewFixes(Base):
    _old = TestPriceAgeIsMetadata._old

    def test_the_newest_observation_of_a_bet_is_the_price_even_if_it_fails_a_gate(self):
        older = self._old(300, price=-140.0)
        newer = self._old(10, price=-165.0)                                    # the last observed price is past the -150 floor
        self.cycle([older, newer], fresh=False)
        self.assertEqual(self.rows(), [])                                      # the older, better price must not stand in for it

    def test_the_newest_observation_wins_when_both_pass(self):
        self.cycle([self._old(300, price=-140.0), self._old(10, price=-135.0)], fresh=False)
        self.assertEqual([r["american_price"] for r in self.rows()], [-135.0])

    def test_a_price_flagged_for_a_reason_other_than_age_is_rejected(self):
        self.assertTrue(lc.eligibility_reasons(dataclasses.replace(self._old(34), price_fresh=False, freshness_status="FRESH"), D1))

    def test_an_unreadable_retrieval_time_is_corrupt_data(self):
        bad = dataclasses.replace(self._old(34), retrieved_at_utc="not-a-time")
        self.assertIsNone(lc.price_observation(bad.retrieved_at_utc, bad.quote_updated_utc, D1))
        self.assertTrue(lc.eligibility_reasons(bad, D1))

    def test_status_and_bucket_use_the_stored_rounded_age(self):
        t = (D1 - dt.timedelta(minutes=5, seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        po = lc.price_observation(t, t, D1)
        self.assertEqual((po["age_min"], po["status"], po["bucket"]), (5.0, "CURRENT", "0-5 min"))

    def test_an_old_quote_loss_is_not_labelled_stale_data(self):
        from operational import learning_postmortem as lpm
        self.cycle([self._old(300, k=2, p=0.8)], fresh=False)
        self.nhl.execute("UPDATE games SET game_state='FINAL', home_score=3, away_score=2, final_period_type='REG' WHERE game_id='1'")
        self.nhl.execute("INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played, revision_number, effective_at_utc, observed_at_utc, source) "
                         "VALUES ('1','8478402','TOR',20.0,0,0,1,1,1,'2026-10-21T03:00:00Z','2026-10-21T03:00:00Z','test')")
        self.nhl.commit()
        lc.settle_due(self.conn, self.nhl, D1 + dt.timedelta(hours=8))
        self.assertEqual(self.rows()[0]["result_status"], "LOSS")
        self.assertEqual(self.rows()[0]["price_status"], "VERY_OLD_PREGAME")
        doc = lpm.build(self.conn, self.nhl, dt.datetime(2026, 10, 21, 12, 0, tzinfo=dt.timezone.utc))
        self.assertNotEqual(doc["day_bets"][0]["classification"], "STALE_DATA")
        self.assertFalse(doc["loss_classification"].get("integrity_defects"))

    def test_a_half_finished_migration_is_completed_on_the_next_open(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "half.db"
            c = lc.open_ledger(path)
            self.cycle([leg()])
            row = dict(self.rows()[0])
            for k in ("price_capture_timestamp", "price_provider_update_utc", "price_age_at_recording_min", "provider_quote_age_at_recording_min", "price_status", "price_age_bucket"):
                row[k] = None                                         # columns exist, values were never filled (an interrupted earlier attempt)
            c.execute(f"INSERT INTO bets ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            c.commit()
            c.close()
            m = lc.open_ledger(path)
            self.assertEqual(m.execute("SELECT price_status FROM bets").fetchone()[0], "RECENT")
            m.close()

    def test_the_archive_reader_keeps_only_the_newest_capture_per_event_and_tags_when_it_was_observed(self):
        from operational import real_prop_orchestrator as rpo
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for name, when, bms in (("a.json", "2026-10-20T20:00:00Z", [{"key": "draftkings", "markets": [{"key": "player_total_saves"}]}]), ("b.json", "2026-10-20T21:00:00Z", []),
                                    ("c.json", "2026-10-20T20:30:00Z", [{"key": "draftkings", "markets": [{"key": "player_total_saves"}]}])):
                (d / name).write_text(json.dumps({"meta": {"market_filter": "player_total_saves", "retrieved_at_utc": when}, "response": {"id": "E1" if name != "c.json" else "E2", "bookmakers": bms}}))
            with mock.patch.object(rpo, "RUNTIME_ARCHIVE_DIR", d), mock.patch.object(rpo, "LEGACY_ARCHIVE_DIR", d / "none"):
                got = rpo._recent_archive_payloads("player_total_saves", now=D1, newest_per_event=True, tag_retrieval=True)
                plain = rpo._recent_archive_payloads("player_total_saves", now=D1)
        by = {r["id"]: r for r in got}
        self.assertEqual(by["E1"]["bookmakers"], [])                           # the newer capture lacks the market: withdrawn, the older one does not resurrect it
        self.assertEqual(by["E1"]["__retrieved_at_utc"], "2026-10-20T21:00:00Z")
        self.assertEqual(len(got), 2)
        self.assertEqual(len(plain), 3)                                        # the default reader is unchanged
        self.assertNotIn("__retrieved_at_utc", plain[0])

    def test_the_advisory_moneyline_keeps_an_old_real_quote_and_refuses_what_could_be_withdrawn_or_mismatched(self):
        from pricing.engine import BetReport
        from research.real_market_parlay import real_slate_adapter as adapter
        from operational import best_bets
        from tests.test_real_slate_adapter import _fresh_db_with_game, _pred
        start = "2026-10-11T23:10:00"
        now = dt.datetime(2026, 10, 11, 17, 0, tzinfo=dt.timezone.utc)

        def run(captured_by_side, *, advisory):
            conn = _fresh_db_with_game(start_utc=start)
            reports = []
            for sel, captured in captured_by_side.items():
                cur = conn.execute("INSERT INTO odds_snapshots (game_id, sportsbook, data_provider, market, selection, event_start_utc, price_american, status, captured_at_utc, received_at_utc, snapshot_label) "
                                   "VALUES (1, 'draftkings', 'test', 'MONEYLINE', ?, ?, -120, 'ACTIVE', ?, ?, 'morning')", (sel, start, captured, captured))
                reports.append(BetReport(game_label="MTL @ TOR", market="MONEYLINE", selection=sel, action="PASS", model_true_probability=0.56, model_conservative_probability=0.50,
                                         market_no_vig_probability=0.55, current_draftkings_price=-120, odds_snapshot_id_selection=cur.lastrowid))
            conn.commit()
            with mock.patch("run_slate.build_prediction_for_game", return_value=_pred(start_utc=start)), mock.patch("pricing.engine.evaluate_moneyline_for_game", return_value=reports) as ev, \
                    mock.patch("operational.eastern_time.eastern_today", return_value="2026-10-11"), mock.patch("operational.moneyline_model_path.record_shadow") as shadow:
                ctx = adapter.advisory_price_age() if advisory else mock.MagicMock()
                with ctx:
                    out = adapter.moneyline_candidate_legs(conn, now=now, freshness_limit_min=best_bets.price_age_limit_min)
            return out, ev, shadow

        old = "2026-10-10T21:00:00"                                         # last evening's quote: excluded by the strict path, kept (with its age) by the advisory one
        (legs, excluded), _, _ = run({"TOR": old, "MTL": old}, advisory=False)
        self.assertEqual(legs, [])
        (legs, excluded), ev, shadow = run({"TOR": old, "MTL": old}, advisory=True)
        self.assertEqual(len(legs), 2)
        self.assertTrue(all((not l.price_fresh) and l.freshness_status == "STALE_QUOTE" and not l.provisional for l in legs))
        self.assertEqual(ev.call_args.kwargs["max_staleness_minutes"], 1440.0)
        shadow.assert_not_called()                                         # the cohort's second look never writes the shadow log
        (legs, excluded), _, _ = run({"TOR": "2026-10-11T12:00:00", "MTL": "2026-10-11T12:30:00"}, advisory=True)
        self.assertEqual(legs, [])
        self.assertEqual([e["reason"] for e in excluded], ["SIDES_FROM_DIFFERENT_PULLS"])


class TestPriceAgeSecondReviewFixes(Base):
    def test_the_latest_pull_rule_uses_the_pull_time_not_the_providers_per_game_quote_time(self):
        from research.real_market_parlay import real_slate_adapter as adapter
        pull = "2026-10-20T12:00:05Z"

        def ml_leg(game, team, quote, retrieved):
            return dataclasses.replace(ml(team=team, game=game), captured_at_utc=quote, quote_updated_utc=quote, retrieved_at_utc=retrieved)
        legs = [ml_leg("1", "TOR", "2026-10-20T11:58:00Z", pull), ml_leg("1", "MTL", "2026-10-20T11:58:00Z", pull),
                ml_leg("2", "EDM", "2026-10-20T11:30:00Z", pull), ml_leg("2", "CHI", "2026-10-20T11:30:00Z", pull)]        # provider quote times differ by 28 minutes inside ONE pull
        excluded = []
        self.assertEqual(len(adapter.latest_pull_filter(legs, excluded)), 4)
        self.assertEqual(excluded, [])
        later = "2026-10-20T17:38:00Z"                                              # a newer pull that did not include game 2: it may have been withdrawn
        legs = [ml_leg("1", "TOR", "2026-10-20T17:30:00Z", later), ml_leg("1", "MTL", "2026-10-20T17:30:00Z", later), ml_leg("2", "EDM", "2026-10-20T11:30:00Z", pull), ml_leg("2", "CHI", "2026-10-20T11:30:00Z", pull)]
        excluded = []
        kept = adapter.latest_pull_filter(legs, excluded)
        self.assertEqual({l.game_id for l in kept}, {"1"})
        self.assertIn("NOT_IN_LATEST_PULL", excluded[0]["reason"])
        mixed = [ml_leg("1", "TOR", "x", pull), ml_leg("1", "MTL", "x", later)]
        excluded = []
        self.assertEqual(adapter.latest_pull_filter(mixed, excluded), [])
        self.assertEqual(excluded[0]["reason"], "SIDES_FROM_DIFFERENT_PULLS")

    def test_the_exact_market_is_matched_so_an_alternate_only_capture_is_not_a_withdrawn_standard_market(self):
        from operational import real_prop_orchestrator as rpo
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            docs = (("std.json", "2026-10-20T20:00:00Z", "player_shots_on_goal,player_total_saves", [{"key": "draftkings", "markets": [{"key": "player_shots_on_goal"}]}]),
                    ("alt.json", "2026-10-20T21:00:00Z", "player_shots_on_goal_alternate,player_points", []))
            for name, when, flt, bms in docs:
                (d / name).write_text(json.dumps({"meta": {"market_filter": flt, "retrieved_at_utc": when}, "response": {"id": "E1", "bookmakers": bms}}))
            (d / "noid.json").write_text(json.dumps({"meta": {"market_filter": "player_shots_on_goal", "retrieved_at_utc": "2026-10-20T21:30:00Z"}, "response": {"bookmakers": []}}))
            with mock.patch.object(rpo, "RUNTIME_ARCHIVE_DIR", d), mock.patch.object(rpo, "LEGACY_ARCHIVE_DIR", d / "none"):
                got = rpo._recent_archive_payloads("player_shots_on_goal", now=D1, newest_per_event=True)
                default = rpo._recent_archive_payloads("player_shots_on_goal", now=D1)
        self.assertEqual([r["bookmakers"] for r in got], [[{"key": "draftkings", "markets": [{"key": "player_shots_on_goal"}]}]])          # the standard capture, not the newer alternate-only one
        self.assertEqual(len(default), 3)                                              # the default reader is unchanged (substring match, id-less responses included)

    def test_a_corrupt_retrieval_time_never_wins_the_newest_observation_collapse(self):
        good = self._old(30)
        corrupt = dataclasses.replace(self._old(5), retrieved_at_utc="garbage")
        self.assertLess(lc._observed_at(corrupt), lc._observed_at(good))

    def _old(self, minutes, **kw):
        return TestPriceAgeIsMetadata._old(self, minutes, **kw)

    def test_migration_adds_the_columns_and_the_full_trigger_even_when_the_old_ledger_has_no_bets(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "empty_old.db"
            c = lc.open_ledger(path)
            c.execute("DROP TRIGGER bets_entry_is_immutable")
            for col, _ in lc.PRICE_COLUMNS:
                c.execute(f"ALTER TABLE bets DROP COLUMN {col}")
            c.commit()
            c.close()
            m = lc.open_ledger(path)
            self.assertTrue({n for n, _ in lc.PRICE_COLUMNS} <= {r[1] for r in m.execute("PRAGMA table_info(bets)")})
            self.assertIn("bets_entry_is_immutable", {r[0] for r in m.execute("SELECT name FROM sqlite_master WHERE type='trigger'")})
            m.close()
            self.cycle([leg()])
            row = dict(self.rows()[0])
            m = lc.open_ledger(path)
            m.execute(f"INSERT INTO bets ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            with self.assertRaises(sqlite3.DatabaseError):
                m.execute("UPDATE bets SET price_status = 'VERY_OLD_PREGAME'")          # the new columns are protected from the first bet on
            m.close()

    def test_an_underivable_entry_does_not_keep_taking_the_write_lock(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "x.db"
            c = lc.open_ledger(path)
            self.cycle([leg()])
            row = dict(self.rows()[0])
            row.update(created_at_utc="not-a-time", bet_id="Bx", idempotency_key="Kx", price_status=None, price_capture_timestamp=None, price_age_bucket=None, price_age_at_recording_min=None)
            c.execute(f"INSERT INTO bets ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            c.commit()
            c.close()
            statements = []
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.set_trace_callback(statements.append)
            lc._migrate(conn)
            conn.close()
            self.assertFalse([s for s in statements if "BEGIN IMMEDIATE" in s or "DROP TRIGGER" in s], statements)

    def test_reasons_are_specific_not_just_non_empty(self):
        self.assertIn("price flagged for a reason other than its age", lc.eligibility_reasons(dataclasses.replace(self._old(34), price_fresh=False, freshness_status="FRESH"), D1))
        self.assertIn("price cannot be tied to a real observation (missing, malformed or future time)", lc.eligibility_reasons(dataclasses.replace(self._old(34), retrieved_at_utc="not-a-time"), D1))


if __name__ == "__main__":
    unittest.main()
