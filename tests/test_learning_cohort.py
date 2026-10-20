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


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = self._mem()
        self.nhl = nhl()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.nhl.close)
        patcher = mock.patch.object(lc, "is_authorised", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        bad = [leg(price=-155.0), leg(provider_contract_verified=False), leg(model_threshold_eligible=False), leg(identity_resolved=False), leg(price_fresh=False), leg(event_not_started=False),
               leg(provisional=True), leg(freshness_status="STALE_QUOTE"), leg(p=0.0), leg(fam="PUCK_LINE"), leg(fam="PLAYER_ASSISTS", k=1)]
        for l in bad:
            self.assertTrue(lc.eligibility_reasons(l, D1), (l.market_family, l.american_price))
        self.assertEqual(lc.eligibility_reasons(leg(), D1), [])

    def test_a_quote_that_aged_out_since_the_cycle_started_is_refused_at_recording_time(self):
        old = leg(quote_updated_utc="2026-10-20T17:00:00Z", retrieved_at_utc="2026-10-20T17:01:00Z", quote_age_min=5.0)
        self.assertTrue(lc.eligibility_reasons(old, D1))
        self.cycle([old], fresh=False)
        self.assertEqual(self.rows(), [])

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
        self.assertEqual(out["recording"]["shortfall"]["not_eligible_reasons"], {"price worse than the -150 floor": 2})
        self.assertEqual(len(self.rows()), 1)

    def test_diversification_prefers_a_second_game_over_a_same_game_near_tie(self):
        legs = [leg(pid="8470001", game="1", p=0.80), leg(pid="8470002", game="1", p=0.795), leg(pid="8470003", game="2", p=0.79)]
        # one slot only (cap) -> pick order shows the penalty working
        picked = lc.select([{"leg": l, "key": lc.economic_key(l), "scored": lc.score_leg(l, D1), "score": lc.score_leg(l, D1)["score"], "band": lc.band_of(l.american_price)} for l in legs], [], 3)
        self.assertEqual([c["leg"].participant_id for c in picked][:2], ["8470001", "8470003"])

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
        self.assertEqual(set(b), {"edge_class", "market_type", "price_band", "model_probability_band", "edge_band", "game", "team", "player", "data_freshness", "selection_rank"})
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
                                "edge": 0.05, "result_status": "PENDING", "profit_loss": None, "eggy_relation": "EGGY_NOT_INVOLVED"}],
                "postmortem": {"scorecard": sc, "cumulative": cu, "postmortem_for": "2026-10-20", "day_metrics": {**cu, "unresolved": 0, "open": 0, "staked": 100.0, "returned": 112.0}, "day_bets": [], "closing_price_movement": "not available",
                               "cumulative_breakdowns": {"market_type": [{"group": "PLAYER_SOG_ALTERNATE", "placed": 10, "settled": 10, "wins": 6, "losses": 4, "hit_rate": 0.6, "profit_loss": 12.0, "roi": 0.12, "expected_wins": 5.9}]},
                                "calibration": [{"band": "55-65%", "n": 10, "predicted": 0.59, "actual": 0.6, "delta_wins": 0.1}], "day_breakdowns": {}}, "final_report": None}

    def _text(self, at):
        from tests.test_product_pages import text
        return text(at) + " " + " ".join(str(m.label) + str(m.value) for m in at.metric)

    def test_the_page_shows_the_experiment_separately_and_still_shows_the_model_book(self):
        from tests.test_product_pages import run_page
        at = run_page("33_Paper_Performance.py", self._snap(self._section()))
        self.assertFalse(at.exception)
        t = self._text(at)
        for needle in ("14-DAY POSTMORTEM LEARNING EXPERIMENT", "Day 2 of 14", "Today's target", "7/10", "-150 or better", "Current cash", "Betting P/L", "Reset count", "Total reset funding", "Eggy"):
            self.assertIn(needle, t, needle)
        self.assertIn("Available cash", t)           # the model book is still there

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
    def test_classification_thresholds_and_anchoring(self):
        self.assertEqual(lc.edge_class(0.05, "ANCHORED_RECORD_ESTIMATE")[0], "POSITIVE_EDGE")
        self.assertEqual(lc.edge_class(0.02, "MARKET_NO_VIG")[0], "POSITIVE_EDGE")
        self.assertEqual(lc.edge_class(0.05, "MODEL_PROBABILITY_UNANCHORED")[0], "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(lc.edge_class(0.0, "ANCHORED_RECORD_ESTIMATE")[0], "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(lc.edge_class(-0.02, "ANCHORED_RECORD_ESTIMATE")[0], "NEUTRAL_UNCERTAIN_EDGE")
        self.assertEqual(lc.edge_class(-0.0201, "ANCHORED_RECORD_ESTIMATE")[0], "LEARNING_SAMPLE_NEGATIVE_EDGE")

    def test_every_bet_is_tagged_at_recording_time_with_a_reason(self):
        pos = leg(pid="8470001", p=0.80, model_probability=0.78, empirical_probability=0.80, empirical_hits=24, empirical_games=30, price=-130.0)
        neu = leg(pid="8470002", p=0.58, price=-130.0)
        self.cycle([pos, neu], now=dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc))
        by = {r["participant_id"]: r for r in self.rows()}
        self.assertEqual(by["8470001"]["edge_class"], "POSITIVE_EDGE")
        self.assertIn("anchored", by["8470001"]["edge_class_reason"])
        self.assertIn(by["8470002"]["edge_class"], ("NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE"))
        self.assertTrue(by["8470001"]["selection_reason"].startswith("POSITIVE_EDGE"))

    def _neg(self, pid="8470009", game="1"):
        return leg(pid=pid, game=game, p=0.50, model_probability=0.5, price=-148.0, empirical_games=30)

    def test_a_negative_edge_candidate_waits_until_no_better_price_can_arrive_then_fills_the_sample(self):
        early = dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc)           # 17:00 ET, two hours before the 19:00 drop
        late = dt.datetime(2026, 10, 20, 22, 45, tzinfo=dt.timezone.utc)           # 18:45 ET, inside the final 25 minutes
        out = self.cycle([self._neg()], now=early)
        self.assertEqual(self.rows(), [])
        self.assertEqual(out["recording"]["shortfall"]["negative_edge_candidates_held_until_the_final_window"], 1)
        self.cycle([self._neg()], now=late)
        r = self.rows()
        self.assertEqual((len(r), r[0]["edge_class"]), (1, "LEARNING_SAMPLE_NEGATIVE_EDGE"))
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
        self.cycle([bad, dataclasses.replace(self._neg(pid="8470010"), price_fresh=False), dataclasses.replace(self._neg(pid="8470011"), american_price=-155.0)], now=late)
        self.assertEqual(self.rows(), [])

    def test_the_edge_class_is_locked_on_the_ledger(self):
        self.cycle([leg()])
        with self.assertRaises(sqlite3.DatabaseError):
            self.conn.execute("UPDATE bets SET edge_class = 'POSITIVE_EDGE'")

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


if __name__ == "__main__":
    unittest.main()
