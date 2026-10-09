"""
Acceptance tests for the unified paper-ticket workflow
(operational/daily_tickets.py + research/real_market_parlay/engine.py::select_tickets
+ operational/paper_bankroll.py). These exercise the product end to end on
isolated databases: what is displayed is what is recorded, funds are enforced,
refreshes never duplicate or rewrite stakes, alerts never refund, and settled
tickets reconcile to the account.
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from operational import bet_revalidation
from operational import daily_postmortem
from operational import daily_tickets as dtk
from operational import paper_bankroll as pb
from operational import paper_bet_settlement_driver as driver
from research.real_market_parlay import engine as rmp
from tests.test_paper_bet_settlement_driver import (_fresh_nhl_conn, _insert_game, _insert_player_stat)

NOW = dt.datetime(2026, 10, 15, 17, 0, tzinfo=dt.timezone.utc)   # 1 pm Eastern
FUTURE = "2026-10-15T23:30:00Z"
PAST = "2026-10-01T15:00:00Z"


def leg(game, pid, price=-105, p=0.60, thr=3, family="PLAYER_SOG_ALTERNATE", start=FUTURE, captured="2026-10-15T16:55:00Z"):
    return rmp.ParlayLeg(
        game_id=str(game), event_id=f"evt{game}", market_family=family, participant_id=pid, participant_name=f"Player {pid}",
        side="OVER", threshold=thr, american_price=price, conservative_probability=p, sportsbook="draftkings",
        captured_at_utc=captured, quote_updated_utc=captured, retrieved_at_utc=captured, provider_contract_verified=True, model_threshold_eligible=True,
        identity_resolved=True, price_fresh=True, event_not_started=True,
        team="TOR", opponent="MTL", game_start_utc=start, model_version="test-model-v1")


def board(n_games=8, **kw):
    return [leg(g, f"P{g}", **kw) for g in range(1, n_games + 1)]


def collected(legs):
    return {"legs": legs, "sources": {}, "second_opinion": {}}


def fresh_ledger():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name), pb.init_db(Path(tmp.name))


class TestSelectionPolicy(unittest.TestCase):
    def test_every_selected_ticket_is_at_least_plus_100_and_cross_game(self):
        picked = rmp.select_tickets(board())
        self.assertGreater(len(picked["tickets"]), 0)
        for t in picked["tickets"]:
            self.assertGreaterEqual(t.combined_decimal, 2.0)
            self.assertGreaterEqual(t.estimated_combo_price, 100)
            self.assertEqual(len({l.game_id for l in t.legs}), len(t.legs))

    def test_two_legs_by_default_and_no_padding_legs(self):
        for t in rmp.select_tickets(board())["tickets"]:
            self.assertEqual(len(t.legs), 2)

    def test_a_third_leg_is_added_only_when_needed_to_reach_plus_100(self):
        # -200 legs (1.5 decimal): two legs = 2.25 >= 2.0 would qualify, so use -300 (1.33): two = 1.78, three = 2.37.
        legs = [leg(g, f"P{g}", price=-300, p=0.82) for g in range(1, 5)]
        tickets = rmp.select_tickets(legs)["tickets"]
        self.assertTrue(all(len(t.legs) == 3 for t in tickets) and tickets)

    def test_a_losing_value_ticket_is_not_offered(self):
        legs = board(price=-110, p=0.50)          # no edge on any leg
        picked = rmp.select_tickets(legs)
        self.assertEqual(picked["tickets"], [])
        self.assertIsNotNone(picked["reason"])

    def test_uncertainty_haircut_blocks_thin_edges(self):
        # +5% estimated EV on paper, but not after every probability is lowered by 3 points.
        legs = board(price=100, p=0.52)
        self.assertEqual(rmp.select_tickets(legs)["tickets"], [])

    def test_same_game_legs_are_never_combined(self):
        legs = [leg(1, "A"), leg(1, "B", thr=2)]
        self.assertEqual(rmp.select_tickets(legs)["tickets"], [])

    def test_at_most_five_tickets_and_exposure_limits_hold(self):
        picked = rmp.select_tickets(board(10))
        self.assertLessEqual(len(picked["tickets"]), 5)
        use = {}
        for t in picked["tickets"]:
            for l in t.legs:
                use[rmp.leg_identity(l)] = use.get(rmp.leg_identity(l), 0) + 1
        self.assertLessEqual(max(use.values()), rmp.MAX_TICKETS_PER_LEG)

    def test_stale_priced_legs_never_enter_selection_even_when_they_look_great(self):
        fresh = [leg(g, f"P{g}", price=-105, p=0.62) for g in (1, 2, 3, 4)]
        stale = [dataclasses_replace(leg(g, f"S{g}", price=150, p=0.80), price_fresh=False, freshness_status="OLDER_THAN_LIMIT") for g in (5, 6, 7, 8)]
        picked = rmp.select_tickets(fresh + stale, max_tickets=5)
        self.assertTrue(picked["tickets"])
        used = {l.participant_id for t in picked["tickets"] for l in t.legs}
        self.assertFalse(used & {"S5", "S6", "S7", "S8"})
        ml_stale = dataclasses_replace(leg(9, "TOR", price=130, p=0.70, thr=None, family="MONEYLINE"), price_fresh=False)
        self.assertEqual(rmp._prepare_pool([ml_stale]), [])

    def test_the_selection_report_says_why_a_higher_hit_ticket_was_not_taken(self):
        """Tickets are taken in estimated-hit-chance order; a higher-hit alternative is skipped only for a stated exposure limit, never silently."""
        legs = [leg(1, "A", price=-110, p=0.70), leg(2, "B", price=-110, p=0.66)] + [leg(g, f"P{g}", price=100, p=0.52) for g in (3, 4, 5, 6)]
        picked = rmp.select_tickets(legs, max_tickets=4)
        rows = picked["considered"]
        self.assertTrue(rows and rows[0]["status"] == "SELECTED")
        hits = [r["hit_probability"] for r in rows]
        self.assertEqual(hits, sorted(hits, reverse=True))                       # listed best hit chance first
        self.assertEqual([r["status"] for r in rows].count("SELECTED"), len(picked["tickets"]))
        skipped = [r for r in rows if r["status"].startswith("BLOCKED")]
        self.assertTrue(skipped, "the leg limit must have skipped something here")
        self.assertTrue(all(r["reason"] for r in skipped))
        self.assertTrue(all(r["hit_probability"] <= rows[0]["hit_probability"] for r in skipped))
        later = [r for r in rows if r["status"] == "NOT_REACHED"]
        self.assertTrue(all(r["hit_probability"] <= min(t.joint_probability for t in picked["tickets"]) + 1e-9 for r in later))

    def test_one_player_is_on_at_most_two_tickets_whatever_the_market(self):
        """2026-10-08: one player's 8-minute night lost three of the day's five tickets. A player may sit on at most MAX_TICKETS_PER_PLAYER."""
        star = [leg(1, "A", price=100, p=0.75, thr=t) for t in (1, 2, 3)]
        partners = [leg(g, f"P{g}", price=-105, p=0.62) for g in (2, 3, 4, 5, 6)]
        picked = rmp.select_tickets(star + partners, max_tickets=5)
        with_a = [t for t in picked["tickets"] if any(l.participant_id == "A" for l in t.legs)]
        self.assertEqual(rmp.MAX_TICKETS_PER_PLAYER, 2)
        self.assertGreaterEqual(len(picked["tickets"]), 3)
        self.assertLessEqual(len(with_a), 2)

    def test_games_can_appear_on_more_than_one_ticket(self):
        picked = rmp.select_tickets(board(4))
        games = [l.game_id for t in picked["tickets"] for l in t.legs]
        self.assertGreater(len(games), len(set(games)))

    def test_opposite_moneyline_sides_are_never_both_held(self):
        home = leg(1, "TOR", price=105, p=0.62, thr=None, family="MONEYLINE")
        away = dataclasses_replace(home, participant_id="MTL", side="AWAY", american_price=105, conservative_probability=0.60)
        legs = [home, away] + [leg(g, f"P{g}") for g in (2, 3, 4)]
        picked = rmp.select_tickets(legs)
        sides = {l.participant_id for t in picked["tickets"] for l in t.legs if l.market_family == "MONEYLINE"}
        self.assertLessEqual(len(sides), 1)

    def test_singles_are_separate_and_at_least_plus_100(self):
        legs = board(4, price=120, p=0.55)
        singles = rmp.select_singles(legs)
        self.assertTrue(singles)
        self.assertTrue(all(rmp.leg_decimal(l) >= 2.0 for l in singles))


def dataclasses_replace(obj, **kw):
    import dataclasses
    return dataclasses.replace(obj, **kw)


class TestPaperAccountEndToEnd(unittest.TestCase):
    def setUp(self):
        self.path, self.conn = fresh_ledger()

    def tearDown(self):
        self.conn.close()

    def test_five_tickets_from_a_fresh_500_leave_450_cash_and_50_open(self):
        result = dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertEqual(result["newly_recorded"], 5)
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual(account["available_cash"], 450.0)
        self.assertEqual(account["open_stakes"], 50.0)
        self.assertEqual(account["equity"], 500.0)

    def test_displayed_and_recorded_tickets_share_ids_and_frozen_selections(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        state = dtk.read_state()
        rows = {r["paper_bet_id"]: r for r in pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")}
        self.assertEqual({c["ticket_id"] for c in state["tickets"]}, set(rows))
        for card in state["tickets"]:
            row = rows[card["ticket_id"]]
            frozen = json.loads(row["legs_json"])
            self.assertEqual([l["label"] for l in card["legs"]],
                             [f"{f['participant_name']} {f['threshold']}+ shots on goal" for f in frozen])
            self.assertEqual(row["stake"], 10.0)
            self.assertGreaterEqual(card["combined_decimal"], 2.0)
            self.assertEqual(card["status"], "RECORDED")
            for f in frozen:      # price timestamp, probability, model version, game time all frozen
                self.assertEqual(f["captured_at_utc"], "2026-10-15T16:55:00Z")
                self.assertEqual(f["model_version"], "test-model-v1")
                self.assertEqual(f["game_start_utc"], FUTURE)
                self.assertIn("conservative_probability", f)
                self.assertTrue(f["code_version"])           # exact code commit recorded on every leg
            self.assertIn("code:", row["model_version"])

    def test_points_legs_flow_through_the_same_workflow_with_their_own_labels(self):
        legs = [leg(g, f"P{g}", price=-240, p=0.75, thr=1, family="PLAYER_POINTS") for g in range(1, 5)]
        result = dtk.run_cycle(None, self.conn, NOW, collected=collected(legs))
        self.assertGreater(result["newly_recorded"], 0)
        state = dtk.read_state()
        labels = [l["label"] for c in state["tickets"] for l in c["legs"]]
        self.assertTrue(all(lbl.endswith("1+ point") for lbl in labels))
        for c in state["tickets"]:
            self.assertGreaterEqual(c["combined_decimal"], 2.0)

    def test_refresh_with_moved_prices_and_a_restart_never_duplicate_or_rewrite(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        before = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")
        moved = board(8, price=-115, p=0.63)
        again = dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=15), collected=collected(moved))
        self.assertEqual(again["newly_recorded"], 0)
        self.conn.close()
        self.conn = pb.init_db(self.path)       # a restart: fresh connection to the same file
        third = dtk.run_cycle(None, self.conn, NOW + dt.timedelta(minutes=30), collected=collected(moved))
        self.assertEqual(third["newly_recorded"], 0)
        after = pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")
        self.assertEqual(len(after), 5)
        self.assertEqual([dict(r) for r in before], [dict(r) for r in after])
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["open_stakes"], 50.0)

    def test_insufficient_funds_prevents_another_ticket_and_the_screen_says_why(self):
        for i in range(49):                    # leaves exactly $10
            pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id=f"OLD:{i}", entry_odds=110, is_combo=True, legs_json="[]")
        self.assertEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"], 10.0)
        result = dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertEqual(result["newly_recorded"], 1)
        self.assertEqual(result["insufficient_funds"], 1)
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual(account["available_cash"], 0.0)
        direct = pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                     market_id="ONE_MORE", entry_odds=110, is_combo=True, legs_json="[]")
        self.assertEqual(direct["status"], "INSUFFICIENT_FUNDS")
        self.assertIn("below", dtk.read_state()["notice"])

    def test_recommended_but_unrecorded_tickets_are_labelled_recommended(self):
        for i in range(50):
            pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id=f"OLD:{i}", entry_odds=110, is_combo=True, legs_json="[]")
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        state = dtk.read_state()
        self.assertTrue(state["tickets"])
        self.assertTrue(all(c["status"] == "RECOMMENDED" and not c["recorded"] for c in state["tickets"]))

    def test_funnel_counts_explain_each_selection_stage(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(4, price=-110, p=0.50) + [leg(9, "PX", price=-300, p=0.9)]))
        funnel = dtk.read_state()["diagnostics"]["funnel"]
        self.assertEqual(funnel["legs_offered"], 5)
        self.assertEqual(funnel["legs_without_positive_edge"], 4)       # -110 at 50% has no edge
        self.assertEqual(funnel["legs_in_pool"], 1)
        self.assertEqual(funnel["two_leg_pairs_cross_game"], 0)

    def test_empty_slots_are_explained(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(2, price=-110, p=0.50)))
        state = dtk.read_state()
        self.assertEqual(state["slots"]["empty"], 5)
        self.assertTrue(state["empty_slot_reason"])

    def test_revalidation_error_and_changed_goalie_info_do_not_refund(self):
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        nhl = _fresh_nhl_conn()
        with mock.patch.object(bet_revalidation, "_invalidation_reasons_for_leg", side_effect=RuntimeError("feed down")):
            summary = bet_revalidation.revalidate_pending_real_market_bets(self.conn, nhl, NOW)
        self.assertGreater(summary["alerts_recorded"], 0)
        reason = "GOALIE_STATUS_CHANGED: TOR starter changed"
        with mock.patch.object(bet_revalidation, "_invalidation_reasons_for_leg", return_value=[reason]):
            bet_revalidation.revalidate_pending_real_market_bets(self.conn, nhl, NOW)
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((account["available_cash"], account["open_stakes"]), (450.0, 50.0))
        self.assertTrue(all(r["result_status"] == "PENDING"
                            for r in pb.query_paper_bets(self.conn, track="REAL_MARKET_PAPER")))
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(8)))
        self.assertTrue(any(c["alerts"] for c in dtk.read_state()["tickets"]))

    def test_concurrent_connections_cannot_overspend_the_last_ten_dollars(self):
        for i in range(49):
            pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id=f"OLD:{i}", entry_odds=110, is_combo=True, legs_json="[]")
        other = pb.init_db(self.path)
        a = pb.record_paper_bet(self.conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id="A", entry_odds=110, is_combo=True, legs_json="[]")
        b = pb.record_paper_bet(other, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id="B", entry_odds=110, is_combo=True, legs_json="[]")
        other.close()
        self.assertEqual(sorted([a["status"], b["status"]]), ["INSERTED", "INSUFFICIENT_FUNDS"])


class TestTodayScreen(unittest.TestCase):
    """The Today page renders the same state document the trader writes."""

    def _state(self, legs, now=None, prefill=0):
        path, conn = fresh_ledger()
        for i in range(prefill):
            pb.record_paper_bet(conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
                                market_id=f"OLD:{i}", entry_odds=110, is_combo=True, legs_json="[]")
        now = now or dt.datetime.now(dt.timezone.utc)
        dtk.run_cycle(None, conn, now, collected=collected(legs))
        conn.close()
        return dtk.read_state()

    def _page(self, state):
        import os
        from streamlit.testing.v1 import AppTest
        page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "pages", "21_Today.py")
        with mock.patch.object(dtk, "read_state", return_value=state):
            at = AppTest.from_file(page, default_timeout=120)
            at.run()
        self.assertEqual(len(at.exception), 0)
        return at

    def test_account_header_cards_and_badges(self):
        now = dt.datetime.now(dt.timezone.utc)
        legs = [dataclasses_replace(l, model_version="EXPERIMENTAL-test-v1")
                for l in board(8, start=(now + dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))]
        state = self._state(legs, now=now)
        at = self._page(state)
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual(metrics["Available cash"], "$450.00")
        self.assertEqual(metrics["Open stakes"], "$50.00")
        self.assertEqual(metrics["Equity"], "$500.00")
        self.assertEqual(metrics["Settled P&L"], "+$0.00".replace("+", ""))
        text = " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])
        for ticket in state["tickets"]:
            self.assertIn(ticket["ticket_id"], text)
        text = " ".join(m.value for m in at.markdown)
        self.assertEqual(text.count("Recorded</span>"), 5)
        self.assertEqual(text.count("class='slip'"), 5)                  # one bet slip per ticket: a row per selection, then the stat strip
        self.assertGreaterEqual(text.count("class='odds'"), 10)
        self.assertTrue(any('Exposure across' in e.label for e in at.expander))
        self.assertIn("<span class='k'>Estimated price</span>", text)
        self.assertFalse(any("Market coverage" in m.value for m in at.markdown))

    def test_recommended_tickets_are_badged_and_the_cash_notice_is_shown(self):
        now = dt.datetime.now(dt.timezone.utc)
        state = self._state(board(8, start=(now + dt.timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")), now=now, prefill=50)
        at = self._page(state)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("Recommended</span>", text)
        self.assertTrue(any("below the" in (m.value if hasattr(m, "value") else "") for m in at.markdown))

    def test_empty_slots_are_explained_and_a_stale_board_warns(self):
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        state = self._state([], now=old)
        at = self._page(state)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("5 slot(s) empty", text)
        self.assertIn("old", text)


class TestQuoteTimestampsAreFrozenAndShown(unittest.TestCase):
    def test_both_timestamps_and_the_quote_age_are_frozen_and_displayed(self):
        from dashboard import tickets_view as tv
        path, conn = fresh_ledger()
        legs = [dataclasses_replace(l, retrieved_at_utc="2026-10-15T16:55:00Z", quote_updated_utc="2026-10-15T16:50:00Z",
                                    quote_age_min=7.0, freshness_status="FRESH") for l in board(4)]
        dtk.run_cycle(None, conn, NOW, collected=collected(legs))
        row = pb.query_paper_bets(conn, track="REAL_MARKET_PAPER")[0]
        for frozen in json.loads(row["legs_json"]):
            self.assertEqual((frozen["retrieved_at_utc"], frozen["quote_updated_utc"]),
                             ("2026-10-15T16:55:00Z", "2026-10-15T16:50:00Z"))
            self.assertEqual((frozen["quote_age_min_at_entry"], frozen["freshness_status"]), (7.0, "FRESH"))
        card = dtk.read_state()["tickets"][0]
        text = tv.quote_text(card["legs"][0])
        self.assertIn("7 min old when recorded", text)
        self.assertIn("fetched", text)
        conn.close()

    def test_a_ticket_is_never_stamped_earlier_than_its_newest_price(self):
        """Reproduces a live quirk: the run's start time preceded a capture made during the same run."""
        path, conn = fresh_ledger()
        late = "2026-10-15T17:00:09Z"                                   # 9 seconds after NOW
        legs = [dataclasses_replace(l, captured_at_utc=late, retrieved_at_utc=late, quote_updated_utc=late)
                for l in board(4)]
        dtk.run_cycle(None, conn, NOW, collected=collected(legs))
        for row in pb.query_paper_bets(conn, track="REAL_MARKET_PAPER"):
            self.assertGreaterEqual(dt.datetime.fromisoformat(row["created_at_utc"]),
                                    dt.datetime(2026, 10, 15, 17, 0, 9, tzinfo=dt.timezone.utc))
        conn.close()

    def test_pre_audit_legs_say_the_quote_time_was_not_recorded(self):
        from dashboard import tickets_view as tv
        text = tv.quote_text({"price_captured_at_utc": "2026-10-07T18:32:39+00:00", "quote_updated_utc": None})
        self.assertIn("quote time not recorded", text)


class TestDisplayedNumbersMatchSelection(unittest.TestCase):
    def test_a_recorded_card_shows_the_same_haircut_ev_the_selector_used(self):
        """Reproduces a defect: recorded cards approximated the haircut as (p - margin*n_legs), while the
        selector uses the product of (p_i - margin). The card must show what the selector actually tested."""
        path, conn = fresh_ledger()
        picked = rmp.select_tickets(board(4))
        combo = picked["tickets"][0]
        recommended_card = dtk.ticket_from_combo(combo, "2026-10-15")
        dtk.run_cycle(None, conn, NOW, collected=collected(board(4)))
        recorded = next(c for c in dtk.read_state()["tickets"] if c["ticket_id"] == recommended_card["ticket_id"])
        self.assertEqual(recorded["rationale"], recommended_card["rationale"])
        self.assertAlmostEqual(recorded["ev_after_haircut"], combo.ev_conservative, places=3)
        conn.close()


class TestSurfaceConsistency(unittest.TestCase):
    """operational/ticket_consistency.py: ledger, board, published copy and postmortems must describe the same tickets."""

    def setUp(self):
        from operational import ticket_consistency
        self.tc = ticket_consistency
        self.path, self.conn = fresh_ledger()
        dtk.run_cycle(None, self.conn, NOW, collected=collected(board(4)))
        self.state = dtk.read_state()

    def tearDown(self):
        self.conn.close()

    def test_agreeing_surfaces_report_nothing(self):
        import copy
        self.assertEqual(self.tc.audit(self.conn, self.state, copy.deepcopy(self.state)), [])

    def test_each_kind_of_disagreement_is_reported(self):
        import copy
        bad = copy.deepcopy(self.state)
        bad["tickets"][0]["legs"][0]["american_price"] += 5                      # a price that moved after recording
        bad["tickets"][1]["status"] = "WON"                                      # a status the ledger does not have
        bad["account"]["available_cash"] -= 10                                   # a balance that drifted
        bad["tickets"].pop(2)                                                    # a ticket missing from the surface
        problems = " | ".join(self.tc.audit(self.conn, self.state, bad))
        self.assertIn("legs/prices/timestamps differ", problems)
        self.assertIn("status WON vs ledger PENDING", problems)
        self.assertIn("available_cash", problems)
        self.assertIn("is missing", problems)

    def test_settled_tickets_and_their_postmortems_stay_consistent(self):
        nhl = _fresh_nhl_conn()
        for g in range(1, 5):
            _insert_game(nhl, g)
            _insert_player_stat(nhl, g, f"P{g}", "TOR", shots=1)                 # every leg misses: every ticket loses
        pb_conn = self.conn
        pb_conn.execute("UPDATE paper_bets SET event_start_utc='2026-10-01T15:00:00Z'")
        pb_conn.commit()
        driver.settle_due_bets(pb_conn, nhl)
        dtk.refresh_state_only(pb_conn, NOW)
        state = dtk.read_state()
        pms = daily_postmortem.real_market_parlay_loss_postmortems(pb_conn, _fresh_nhl_conn())
        self.assertEqual(self.tc.audit(pb_conn, state, None, postmortems=pms), [])
        self.assertEqual({p["ticket_id"] for p in pms}, {c["ticket_id"] for c in state["tickets"]})
        self.assertEqual(pb.account_state(pb_conn, "REAL_MARKET_PAPER")["settled_pnl"], -10.0 * len(pms))


class TestSharedExposure(unittest.TestCase):
    def test_players_and_games_shared_across_tickets_are_reported(self):
        path, conn = fresh_ledger()
        dtk.run_cycle(None, conn, NOW, collected=collected(board(3)))
        ex = dtk.read_state()["exposure"]
        self.assertEqual(ex["tickets_counted"], 3)
        self.assertEqual(ex["recorded_stake_at_risk"], 30.0)
        self.assertEqual(ex["players_on_multiple_tickets"], 3)         # three games, three tickets: each player on two
        self.assertTrue(all(p["count"] == 2 for p in ex["players"]))
        self.assertTrue(all(g["count"] == 2 for g in ex["games"]))
        self.assertIn("correlated", ex["note"])
        conn.close()


class TestLegSourceMerge(unittest.TestCase):
    """One pool from two leg sources; validated gates untouched, shots legs capped, no silent gate bypass."""

    def setUp(self):
        self.nhl = _fresh_nhl_conn()
        _insert_game(self.nhl, 1, state="SCHEDULED")
        _insert_game(self.nhl, 2, state="SCHEDULED")

    def test_shots_leg_in_both_sources_takes_the_lower_probability_and_both_versions(self):
        validated = dataclasses_replace(leg(1, "P1", p=0.66), model_version="sog-alternate-validated-v1", team=None)
        rolling = dataclasses_replace(leg(1, "P1", p=0.58), model_version="rolling")
        legs, notes = dtk._merge_and_add_context(self.nhl, [validated], [rolling])
        self.assertEqual(len(legs), 1)
        self.assertAlmostEqual(legs[0].conservative_probability, 0.58)
        self.assertEqual(legs[0].model_version, "sog-alternate-validated-v1+rolling(min)")
        self.assertEqual(notes["shots_legs_both_sources_min"], 1)

    def test_validated_shots_leg_without_dress_confirmation_is_dropped(self):
        legs, notes = dtk._merge_and_add_context(self.nhl, [leg(1, "P1")], [])
        self.assertEqual(legs, [])
        self.assertEqual(notes["dropped_validated_shots_leg_without_dress_confirmation"], 1)

    def test_rolling_form_only_legs_are_admitted_and_counted(self):
        legs, notes = dtk._merge_and_add_context(self.nhl, [], [leg(2, "P9")])
        self.assertEqual(len(legs), 1)
        self.assertEqual(notes["shots_legs_rolling_model_only"], 1)

    def test_moneyline_leg_passes_through_with_game_context_and_no_probability_change(self):
        ml = leg(1, "TOR", price=105, p=0.58, thr=None, family="MONEYLINE")
        legs, _ = dtk._merge_and_add_context(self.nhl, [ml], [])
        self.assertEqual(len(legs), 1)
        self.assertEqual((legs[0].team, legs[0].opponent), ("TOR", "MTL"))
        self.assertAlmostEqual(legs[0].conservative_probability, 0.58)


class TestSettlementReconciliation(unittest.TestCase):
    """Known settlement examples, reconciled to the account."""

    def setUp(self):
        self.path, self.conn = fresh_ledger()
        self.nhl = _fresh_nhl_conn()
        _insert_game(self.nhl, 1)
        _insert_game(self.nhl, 2)

    def tearDown(self):
        self.conn.close()

    def _record(self, legs):
        combo = rmp._evaluate_combo(legs)
        bet = pb.create_real_market_combo_paper_bet(self.conn, {"status": "QUALIFIED", "combo": combo},
                                                    event_start_utc=PAST, created_at_utc=NOW.isoformat())
        self.assertEqual(bet["status"], "INSERTED")
        return bet["paper_bet_id"], combo

    def _settle(self):
        return driver.settle_due_bets(self.conn, self.nhl)

    def test_winning_two_leg_ticket_pays_the_estimated_combined_price(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)
        _insert_player_stat(self.nhl, 2, "P2", "TOR", shots=5)
        tid, combo = self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        row = [r for r in pb.query_paper_bets(self.conn) if r["paper_bet_id"] == tid][0]
        self.assertEqual(row["result_status"], "WIN")
        self.assertAlmostEqual(row["profit_loss"], 10.0 * (combo.combined_decimal - 1.0), places=1)
        self.assertAlmostEqual(pb.account_state(self.conn, "REAL_MARKET_PAPER")["available_cash"],
                               500.0 + row["profit_loss"], places=2)

    def test_settled_tickets_show_won_or_lost_with_per_leg_results_on_the_same_card(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)
        _insert_player_stat(self.nhl, 2, "P2", "TOR", shots=1)
        tid, _ = self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        dtk.refresh_state_only(self.conn, NOW)
        state = dtk.read_state()
        card = next(c for c in state["tickets"] if c["ticket_id"] == tid)
        self.assertEqual(card["status"], "LOST")
        self.assertEqual([l["outcome"] for l in card["legs"]], ["WIN", "LOSS"])
        self.assertEqual(card["result"]["profit_loss"], -10.0)
        self.assertNotIn(tid, [c["ticket_id"] for c in state["recent_settled"]])   # today's ticket is not listed twice
        self.assertEqual(state["account"]["available_cash"], 490.0)

    def test_losing_ticket_loses_exactly_ten_dollars(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)
        _insert_player_stat(self.nhl, 2, "P2", "TOR", shots=1)
        self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((account["available_cash"], account["open_stakes"], account["settled_pnl"]), (490.0, 0.0, -10.0))

    @mock.patch.object(driver, "VOID_RULES_VERIFIED", True)
    def test_did_not_play_leg_is_removed_and_ticket_repriced_not_voided(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)          # P2 never dressed
        tid, _ = self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        row = [r for r in pb.query_paper_bets(self.conn) if r["paper_bet_id"] == tid][0]
        self.assertEqual(row["result_status"], "WIN")
        self.assertAlmostEqual(row["profit_loss"], 12.0, places=2)        # only the +120 leg remains
        self.assertIn("repriced", row["notes"])
        self.assertEqual(row["entry_odds"], rmp._evaluate_combo(
            [leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)]).estimated_combo_price)

    @mock.patch.object(driver, "VOID_RULES_VERIFIED", True)
    def test_ticket_with_every_leg_void_refunds_the_stake(self):
        self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((account["available_cash"], account["settled_pnl"]), (500.0, 0.0))

    def test_default_unverified_void_rule_keeps_the_stake_open_instead_of_assuming_a_refund_or_repricing(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)          # P2 never dressed
        tid, _ = self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        row = [r for r in pb.query_paper_bets(self.conn) if r["paper_bet_id"] == tid][0]
        self.assertEqual(row["result_status"], "UNRESOLVED")
        self.assertIn("unverified", row["notes"])
        self.assertIn("provisional", row["notes"])
        account = pb.account_state(self.conn, "REAL_MARKET_PAPER")
        self.assertEqual((account["available_cash"], account["open_stakes"], account["settled_pnl"]), (490.0, 10.0, 0.0))
        self._settle()                                                   # re-scanning never rewrites or duplicates
        self.assertEqual(len(self.conn.execute("SELECT * FROM paper_audit_log WHERE paper_bet_id=?", (tid,)).fetchall()), 2)

    def test_unsupported_leg_leaves_the_ticket_unresolved_and_stake_open(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)
        weird = leg(2, "P2", price=-105, p=0.60, family="PLAYER_SOG_ALTERNATE")
        tid, _ = self._record([leg(1, "P1", price=120, p=0.55), weird])
        row = pb.query_paper_bets(self.conn)[0]
        legs = json.loads(row["legs_json"])
        legs[1]["market_family"] = "SOMETHING_NEW"
        res = driver.resolve_combo_bet(self.nhl, {**row, "legs_json": json.dumps(legs)})
        self.assertEqual(res["status"], "UNRESOLVED")

    def test_losing_ticket_postmortem_uses_stored_prediction_and_leg_results(self):
        _insert_player_stat(self.nhl, 1, "P1", "TOR", shots=4)
        _insert_player_stat(self.nhl, 2, "P2", "TOR", shots=1)
        tid, _ = self._record([leg(1, "P1", price=120, p=0.55), leg(2, "P2", price=-105, p=0.60)])
        self._settle()
        row = pb.query_paper_bets(self.conn)[0]
        self.assertIsNotNone(row["settlement_json"])
        # The postmortem must not need the live game data again: drop the games and it still explains the loss.
        empty_nhl = _fresh_nhl_conn()
        pms = daily_postmortem.real_market_parlay_loss_postmortems(self.conn, empty_nhl)
        self.assertEqual(len(pms), 1)
        pm = pms[0]
        self.assertEqual(pm["ticket_id"], tid)
        self.assertEqual(len(pm["missed_legs"]), 1)
        missed = pm["missed_legs"][0]
        self.assertEqual(missed["participant_name"], "Player P2")
        self.assertEqual(missed["actual_value"], 1)
        self.assertAlmostEqual(missed["predicted_probability"], 0.60)
        self.assertEqual(missed["model_version"], "test-model-v1")
        self.assertIn("actual=1", pm["why"])
        self.assertIn("predicted 60%", pm["why"])


if __name__ == "__main__":
    unittest.main()


class TestRecordingWaves(unittest.TestCase):
    """Early puck drops cannot use up the whole day's slots while later puck drops are still to come."""

    def _games(self, early, late):
        conn = _fresh_nhl_conn()
        for gid, start in [(g, "2026-10-15T19:00:00") for g in early] + [(g, "2026-10-16T00:30:00") for g in late]:
            conn.execute("INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team, away_team, game_state, source) "
                         "VALUES (?,?,?,?,?,?,?,?)", (gid, "20262027", "2026-10-15", start, "TOR", "MTL", "SCHEDULED", "test"))
        conn.commit()
        return conn

    def test_waves_are_clusters_of_puck_drops(self):
        nhl = self._games([1, 2, 3, 4], [5, 6, 7, 8])
        waves = dtk.day_waves(nhl, "2026-10-15")
        self.assertEqual([len(w["game_ids"]) for w in waves], [4, 4])

    def test_the_early_wave_is_capped_while_a_later_wave_is_pending(self):
        nhl = self._games([1, 2, 3, 4, 5, 6], [7, 8])
        path, conn = fresh_ledger()
        early = [leg(g, f"P{g}", price=-105, p=0.64, start="2026-10-15T19:00:00Z") for g in (1, 2, 3, 4, 5, 6)]
        out = dtk.run_cycle(nhl, conn, NOW, collected=collected(early))
        self.assertEqual(out["newly_recorded"], 4)                                  # 5 slots - 1 held for the one later wave
        state = dtk.read_state()
        self.assertEqual(state["slots"]["used"], 4)
        self.assertEqual(state["diagnostics"]["recording_policy"]["waves"][0]["slot_cap"], 4)
        late = [leg(g, f"P{g}", price=-105, p=0.64, start="2026-10-16T00:30:00Z") for g in (7, 8)]
        out2 = dtk.run_cycle(nhl, conn, NOW, collected=collected(early + late))
        self.assertEqual(out2["newly_recorded"], 1)                                 # the held slot goes to the later wave
        self.assertEqual(len(dtk.recorded_today(conn, "2026-10-15")), 5)

    def test_a_single_wave_day_is_not_restricted(self):
        nhl = self._games([1, 2, 3, 4, 5, 6, 7, 8], [])
        path, conn = fresh_ledger()
        legs = board(8, start="2026-10-15T19:00:00Z")
        out = dtk.run_cycle(nhl, conn, NOW, collected=collected(legs))
        self.assertEqual(out["newly_recorded"], 5)
