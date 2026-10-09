"""
Last-name personal accounts, their own $500 paper bankrolls, and the Paper Parlay Builder's orders: slugs and duplicate surnames, passcode signing, funds enforcement,
builder revalidation (stale, moved, started, same-game acknowledgement, duplicate), settlement into the right account, the earlier code-based logs, and the central guarantee:
nothing a person does here can change the model's original $500 book or another person's account.
"""
from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from dashboard import order_client
from operational import log_signing
from operational import paper_bet_settlement_driver as drv
from operational import personal_logs as pl
from tests.product_fixture import builder_pool_doc
from tests.test_daily_tickets import fresh_ledger
from tests.test_personal_logs import fresh_logs, ledger_fingerprint

NOW = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)           # a minute after the fixture quotes, an hour before the fixture game
GAME, P1, P2 = "2026020900", "P1", "P2"
PASS_A, PASS_B = "ABCD-2345", "WXYZ-6789"


def leg(pid=P1, k=2, price=-110.0, game=GAME, name="Test Skater One", fam="PLAYER_SOG_ALTERNATE"):
    return {"game_id": game, "participant_id": pid, "participant_name": name, "market_family": fam, "threshold": k, "side": "OVER", "american_price": price}


class Acct:
    """A person: a last name, a number, a passcode."""
    def __init__(self, surname, number=1, passcode=PASS_A):
        self.surname, self.number, self.passcode = surname, number, passcode
        self.slug = pl.surname_slug(surname, number)
        self.hash = pl.account_key(self.slug)
        self.creation = {"creation_id": "crt_" + self.slug.replace("-", "x")[:20].ljust(10, "x"), "display_name": surname, "slug": self.slug,
                         "write_pub": log_signing.public_key_hex(passcode, self.hash)}
        self._n = 0

    def oid(self):
        self._n += 1
        return f"ord_{self.slug.replace('-', 'x')}_{self._n:03d}"

    def create_order(self):
        doc = order_client.build_personal_order(None, order_id=self.oid(), log_hash=self.hash, page_generated_at=NOW.isoformat(), stake=0, create=self.creation, kind="PERSONAL_LOG_CREATE")
        return log_signing.sign(doc, self.passcode, self.hash)

    def builder_order(self, legs, stake=10.0, ack=False, key=None, combined=None, create=False, oid=None):
        doc = order_client.build_builder_order(legs, order_id=oid or self.oid(), log_hash=self.hash, page_generated_at=NOW.isoformat(), stake=stake, same_game_ack=ack,
                                               combined_american=combined, create=self.creation if create else None)
        return log_signing.sign(doc, key or self.passcode, self.hash)


class Base(unittest.TestCase):
    def setUp(self):
        self.logs = fresh_logs()
        self.pool = builder_pool_doc()
        self.casey = Acct("Casey")
        self.burnett = Acct("Burnett", passcode=PASS_B)

    def go(self, doc, now=NOW, pool="default"):
        return pl.process_order(self.logs, doc, current_legs=[], now=now, source="test:1", builder_pool=self.pool if pool == "default" else pool)

    def open(self, *accts):
        for a in accts:
            self.assertEqual(self.go(a.create_order())["status"], pl.CREATED)

    def add(self, acct, legs, **kw):
        return self.go(acct.builder_order(legs, **kw))


class TestNames(unittest.TestCase):
    def test_slug_is_lowercase_ascii_and_the_nth_person_gets_a_number(self):
        self.assertEqual(pl.surname_slug("Burnett"), "burnett")
        self.assertEqual(pl.surname_slug("Burnett", 2), "burnett-2")
        self.assertEqual(pl.surname_slug("O'Brien"), "obrien")
        self.assertEqual(pl.surname_slug("Van der Berg"), "van-der-berg")
        self.assertEqual(pl.surname_slug("Bélanger"), "belanger")
        self.assertEqual(pl.display_for("Burnett", 2), "Burnett 2")

    def test_typed_names_parse_with_a_duplicate_number(self):
        self.assertEqual(pl.parse_account_name("burnett"), ("burnett", 1, None))
        self.assertEqual(pl.parse_account_name("Burnett 2")[:2], ("Burnett", 2))
        self.assertEqual(pl.parse_account_name("  Burnett   3 ")[:2], ("Burnett", 3))
        for bad in ("", "a", "x" * 40, "me@x.com", "12345", "my yahoo"):
            self.assertIsNotNone(pl.parse_account_name(bad)[2], bad)

    def test_next_free_number_counts_up_without_reusing(self):
        self.assertEqual(pl.next_free_number([], "Burnett"), 1)
        self.assertEqual(pl.next_free_number(["burnett"], "Burnett"), 2)
        self.assertEqual(pl.next_free_number(["burnett", "burnett-2", "casey"], "Burnett"), 3)

    def test_account_key_is_deterministic_and_does_not_contain_the_name(self):
        k = pl.account_key("burnett")
        self.assertEqual(k, pl.account_key("burnett"))
        self.assertNotEqual(k, pl.account_key("burnett-2"))
        self.assertRegex(k, r"^[0-9a-f]{32}$")
        self.assertNotIn("burnett", k)


class TestPasscodes(unittest.TestCase):
    def test_new_passcodes_are_eight_characters_in_two_groups_and_vary(self):
        seen = {log_signing.new_passcode() for _ in range(30)}
        self.assertGreater(len(seen), 25)
        for p in seen:
            self.assertRegex(p, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
            self.assertTrue(log_signing.valid_key_shape(p) and log_signing.is_passcode(p))

    def test_a_passcode_signs_and_only_that_passcode_verifies(self):
        h = pl.account_key("casey")
        doc = log_signing.sign({"schema": 1, "type": "x", "order_id": "ord_12345678"}, PASS_A, h)
        self.assertTrue(log_signing.verify(doc, log_signing.public_key_hex(PASS_A, h)))
        self.assertTrue(log_signing.verify(doc, log_signing.public_key_hex("abcd 2345", h)))           # case and spacing do not matter
        self.assertFalse(log_signing.verify(doc, log_signing.public_key_hex(PASS_B, h)))
        self.assertFalse(log_signing.verify(doc, log_signing.public_key_hex(PASS_A, pl.account_key("casey-2"))))      # bound to the account


class TestAccounts(Base):
    def test_a_new_account_starts_with_five_hundred_dollars(self):
        self.open(self.casey)
        st = pl.account_state(self.logs, self.casey.hash)
        self.assertEqual((st["starting_balance"], st["available_cash"], st["open_stakes"], st["settled_pnl"]), (500.0, 500.0, 0.0, 0.0))
        self.assertEqual(pl.log_row(self.logs, self.casey.hash)["display_name"], "Casey")

    def test_the_same_name_cannot_be_taken_over_by_a_different_person(self):
        self.open(self.casey)
        thief = Acct("Casey", passcode=PASS_B)
        thief.creation["creation_id"] = "crt_thiefthief"
        thief._n = 50                                                                      # a different order id from the real Casey's
        res = self.go(thief.create_order())
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("NAME_TAKEN", res["reason"])
        self.assertEqual(pl.log_row(self.logs, self.casey.hash)["write_pub"], self.casey.creation["write_pub"])

    def test_a_second_person_with_the_same_surname_gets_a_numbered_account_of_their_own(self):
        self.open(self.burnett)
        second = Acct("Burnett", 2, PASS_A)
        self.open(second)
        names = {r["display_name"] for r in self.logs.execute("SELECT display_name FROM logs")}
        self.assertEqual(names, {"Burnett", "Burnett 2"})
        self.assertNotEqual(self.burnett.hash, second.hash)

    def test_an_account_key_that_does_not_match_its_name_is_refused(self):
        a = Acct("Casey")
        a.creation = {**a.creation, "slug": "someone-else"}
        res = self.go(a.create_order())
        self.assertEqual(res["status"], pl.REJECTED)

    def test_an_order_for_an_account_that_does_not_exist_is_refused(self):
        res = self.add(self.casey, [leg()])
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("LOG_NOT_FOUND", res["reason"])

    def test_a_bad_signature_is_refused_and_does_not_use_up_the_accounts_daily_limit(self):
        self.open(self.casey)
        with mock.patch.object(pl, "MAX_BET_ORDERS_PER_LOG_PER_DAY", 2):
            for i in range(5):
                res = self.go(self.casey.builder_order([leg()], key=PASS_B))
                self.assertEqual(res["status"], pl.REJECTED)
                self.assertIn("BAD_SIGNATURE", res["reason"])
            self.assertEqual(self.add(self.casey, [leg()])["status"], pl.RECORDED)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)


class TestBuilderOrders(Base):
    def setUp(self):
        super().setUp()
        self.open(self.casey, self.burnett)

    def test_a_single_leg_is_recorded_at_the_current_price_as_a_sportsbook_quote(self):
        res = self.add(self.casey, [leg(price=-110.0)], stake=20)
        self.assertEqual(res["status"], pl.RECORDED)
        bet = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertEqual((bet["log_hash"], bet["stake"], bet["entry_odds"], bet["result_status"]), (self.casey.hash, 20.0, -110.0, "PENDING"))
        self.assertIn("SPORTSBOOK_QUOTE", bet["provenance_json"])
        self.assertIsNone(bet["ev"])                                                        # a personal choice: no model edge is claimed

    def test_a_two_game_slip_is_an_estimated_product_not_a_quote(self):
        self.pool["games"]["2026020901"] = {**self.pool["games"][GAME], "away": "CCC", "home": "DDD", "players": {"P9": {**self.pool["games"][GAME]["players"][P1], "n": "Test Skater Nine", "t": "CCC"}}}
        res = self.add(self.casey, [leg(), leg(pid="P9", game="2026020901", name="Test Skater Nine")], stake=10)
        self.assertEqual(res["status"], pl.RECORDED)
        bet = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertIn("ESTIMATED_PRODUCT_OF_LEG_PRICES", bet["provenance_json"])
        self.assertGreater(bet["entry_odds"], 100)

    def test_no_model_edge_or_plus_100_is_required(self):
        res = self.add(self.casey, [leg(k=1, price=-300.0)], stake=10)                  # a heavy favourite: far below +100, no probability edge
        self.assertEqual(res["status"], pl.RECORDED)
        self.assertEqual(self.logs.execute("SELECT entry_odds FROM bets").fetchone()[0], -300.0)

    def test_a_price_that_moved_records_nothing_and_returns_the_new_price(self):
        res = self.add(self.casey, [leg(price=-130.0)])                                  # the pool says -110
        self.assertEqual(res["status"], pl.NEEDS_ACCEPTANCE)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 0)
        self.assertIn("moved", res["reason"].lower())

    def test_a_stale_price_records_nothing(self):
        res = self.go(self.casey.builder_order([leg()]), now=dt.datetime(2026, 10, 8, 22, 30, tzinfo=dt.timezone.utc), pool=_aged(self.pool, hours=5))
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("UNAVAILABLE", res["reason"])

    def test_a_game_that_has_started_records_nothing(self):
        res = self.go(self.casey.builder_order([leg()]), now=dt.datetime(2026, 10, 8, 23, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("started", res["reason"])

    def test_a_line_that_is_not_on_the_price_list_records_nothing(self):
        res = self.add(self.casey, [leg(k=9)])
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("no current DraftKings price", res["reason"])
        res = self.add(self.casey, [leg(fam="NOT_A_MARKET")])                           # a made-up market is a refusal, never a crash
        self.assertEqual(res["status"], pl.REJECTED)

    def test_without_a_current_price_list_nothing_is_recorded(self):
        res = self.go(self.casey.builder_order([leg()]), pool=None)
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("UNAVAILABLE", res["reason"])

    def test_same_game_legs_need_an_acknowledgement_and_are_labelled_multiplied(self):
        two = [leg(), leg(pid=P2, name="Test Skater Two", price=120.0)]
        no = self.add(self.casey, two)
        self.assertEqual(no["status"], pl.REJECTED)
        self.assertIn("SAME_GAME", no["reason"])
        yes = self.add(self.casey, two, ack=True)
        self.assertEqual(yes["status"], pl.RECORDED)
        bet = dict(self.logs.execute("SELECT * FROM bets").fetchone())
        self.assertIn("SAME_GAME_MULTIPLIED_NOT_A_QUOTE", bet["provenance_json"])
        self.assertIsNone(bet["model_probability"])                                         # a product of probabilities would be wrong for correlated legs

    def test_the_same_player_twice_in_one_market_is_one_bet_not_two(self):
        res = self.add(self.casey, [leg(k=2), leg(k=3, price=180.0)], ack=True)
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("once per market", res["reason"])

    def test_more_than_six_legs_or_none_is_refused(self):
        self.assertEqual(self.add(self.casey, [])["status"], pl.REJECTED)
        self.assertEqual(self.add(self.casey, [leg(pid=f"X{i}") for i in range(7)], ack=True)["status"], pl.REJECTED)

    def test_the_same_slip_twice_is_a_duplicate_and_costs_nothing_extra(self):
        first = self.add(self.casey, [leg()])
        again = self.add(self.casey, [leg()])
        self.assertEqual((first["status"], again["status"]), (pl.RECORDED, pl.ALREADY))
        self.assertEqual(pl.account_state(self.logs, self.casey.hash)["open_stakes"], 10.0)

    def test_resending_the_same_order_returns_the_first_answer(self):
        doc = self.casey.builder_order([leg()])
        a, b = self.go(doc), self.go(doc)
        self.assertEqual(a["bet_id"], b["bet_id"])
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)

    def test_the_same_slip_in_another_account_is_not_a_duplicate(self):
        self.assertEqual(self.add(self.casey, [leg()])["status"], pl.RECORDED)
        self.assertEqual(self.add(self.burnett, [leg()])["status"], pl.RECORDED)

    def test_stake_limits(self):
        for bad in (0, 0.5, -5, 1_000_000):
            self.assertEqual(self.add(self.casey, [leg()], stake=bad)["status"], pl.REJECTED, bad)


class TestBankroll(Base):
    def setUp(self):
        super().setUp()
        self.open(self.casey, self.burnett)

    def cash(self, a):
        return pl.account_state(self.logs, a.hash)

    def test_a_stake_is_set_aside_while_the_bet_is_open(self):
        self.add(self.casey, [leg()], stake=120)
        st = self.cash(self.casey)
        self.assertEqual((st["available_cash"], st["open_stakes"], st["equity"]), (380.0, 120.0, 500.0))

    def test_a_stake_larger_than_the_cash_is_refused_and_nothing_is_recorded(self):
        self.add(self.casey, [leg()], stake=400)
        res = self.add(self.casey, [leg(pid=P2, name="Test Skater Two", price=120.0, k=2)], stake=150)
        self.assertEqual(res["status"], pl.REJECTED)
        self.assertIn("INSUFFICIENT_FUNDS", res["reason"])
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)
        self.assertEqual(self.cash(self.casey)["available_cash"], 100.0)

    def test_the_whole_balance_can_be_staked_but_not_a_cent_more(self):
        self.assertEqual(self.add(self.casey, [leg()], stake=500)["status"], pl.RECORDED)
        res = self.add(self.casey, [leg(pid=P2, name="Test Skater Two", price=120.0)], stake=10)
        self.assertIn("INSUFFICIENT_FUNDS", res["reason"])

    def test_settlement_pays_into_that_account_only(self):
        self.add(self.casey, [leg(price=-110.0)], stake=110)
        self.add(self.burnett, [leg(price=-110.0)], stake=50)
        with mock.patch.object(drv, "resolve_combo_bet", return_value={"status": "WIN", "leg_results": []}):
            out = pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))
        self.assertEqual(out["settled"], 2)
        c, b = self.cash(self.casey), self.cash(self.burnett)
        self.assertEqual((c["settled_pnl"], c["available_cash"], c["open_stakes"], c["payouts_received"]), (100.0, 600.0, 0.0, 210.0))
        self.assertEqual((b["settled_pnl"], b["available_cash"]), (round(50 * 100 / 110, 2), round(500 + 50 * 100 / 110, 2)))

    def test_a_loss_costs_the_stake_and_frees_nothing(self):
        self.add(self.casey, [leg()], stake=200)
        with mock.patch.object(drv, "resolve_combo_bet", return_value={"status": "LOSS", "leg_results": []}):
            pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))
        st = self.cash(self.casey)
        self.assertEqual((st["available_cash"], st["settled_pnl"], st["open_stakes"]), (300.0, -200.0, 0.0))
        self.assertEqual(self.cash(self.burnett)["available_cash"], 500.0)

    def test_a_void_returns_the_stake(self):
        self.add(self.casey, [leg()], stake=75)
        with mock.patch.object(drv, "resolve_combo_bet", return_value={"status": "VOID", "leg_results": []}):
            pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))
        st = self.cash(self.casey)
        self.assertEqual((st["available_cash"], st["payouts_received"]), (500.0, 75.0))

    def test_cash_is_not_reset_by_reopening_the_database_or_the_account(self):
        self.add(self.casey, [leg()], stake=60)
        path = self.logs.execute("PRAGMA database_list").fetchone()[2]
        again = pl.connect(path)
        self.assertEqual(pl.account_state(again, self.casey.hash)["available_cash"], 440.0)
        self.assertEqual(pl.process_order(again, self.casey.create_order(), current_legs=[], now=NOW + dt.timedelta(hours=1), source="t", builder_pool=self.pool)["status"], pl.CREATED)
        self.assertEqual(pl.account_state(again, self.casey.hash)["available_cash"], 440.0)
        self.assertEqual(again.execute("SELECT COUNT(*) FROM logs WHERE slug = 'casey'").fetchone()[0], 1)

    def test_every_account_reconciles_a_second_way(self):
        self.add(self.casey, [leg()], stake=30)
        self.add(self.burnett, [leg(pid=P2, name="Test Skater Two", price=120.0)], stake=45)
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}]):
            pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))
        self.add(self.casey, [leg(k=3, price=180.0)], stake=20, ack=True)
        rec = pl.reconcile(self.logs)
        self.assertEqual(len(rec), 2)
        self.assertTrue(all(r["agrees"] for r in rec))


class TestModelBookAndIsolation(Base):
    def test_two_accounts_building_adding_and_settling_leave_the_model_ledger_identical(self):
        _, ledger = fresh_ledger()
        before = ledger_fingerprint(ledger)
        self.open(self.casey, self.burnett)
        self.assertEqual(self.add(self.casey, [leg()], stake=40)["status"], pl.RECORDED)
        self.assertEqual(self.add(self.burnett, [leg(pid=P2, name="Test Skater Two", price=120.0), leg(k=3, price=180.0)], stake=25, ack=True)["status"], pl.RECORDED)
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}]):
            self.assertEqual(pl.settle_open(self.logs, None, NOW + dt.timedelta(days=1))["settled"], 2)
        self.assertEqual(ledger_fingerprint(ledger), before)

    def test_one_account_cannot_write_to_or_see_changes_in_another(self):
        self.open(self.casey, self.burnett)
        self.add(self.casey, [leg()], stake=100)
        before = pl.account_state(self.logs, self.burnett.hash)
        # Casey signs an order addressed to Burnett's account: refused, nothing moves
        forged = order_client.build_builder_order([leg(pid=P2, name="Test Skater Two", price=120.0)], order_id="ord_forged_001", log_hash=self.burnett.hash,
                                                  page_generated_at=None, stake=10, same_game_ack=False, combined_american=None)
        res = self.go(log_signing.sign(forged, PASS_A, self.burnett.hash))
        self.assertIn("BAD_SIGNATURE", res["reason"])
        self.assertEqual(pl.account_state(self.logs, self.burnett.hash), before)
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets WHERE log_hash = ?", (self.burnett.hash,)).fetchone()[0], 0)

    def test_the_published_section_has_a_balance_per_account_and_no_keys(self):
        self.open(self.casey, self.burnett)
        self.add(self.casey, [leg()], stake=100)
        sec = pl.section(self.logs, NOW)
        blob = repr(sec)
        for secret in (PASS_A, PASS_B, "ABCD2345"):
            self.assertNotIn(secret, blob)
        self.assertEqual(len(sec["logs"]), 2)
        mine = sec["logs"][self.casey.hash]
        self.assertEqual(mine["bankroll"]["available_cash"], 400.0)
        self.assertEqual(sec["logs"][self.burnett.hash]["bankroll"]["available_cash"], 500.0)


class TestEarlierLogsKeepTheirHistory(Base):
    def old_log(self, stake=20.0, status="PENDING", pnl=None):
        h = pl.code_hash("otter-maple-puck-4821")
        self.logs.execute("INSERT INTO logs (log_hash, display_name, created_at_utc, created_by_order, write_pub) VALUES (?,?,?,?,?)",
                          (h, "Casey old", "2026-10-01T12:00:00Z", "crt_oldoldold", "0" * 64))
        self.logs.execute("INSERT INTO bets (bet_id, log_hash, order_id, fingerprint, legs_json, entry_odds, stake, created_at_utc, origin, result_status, profit_loss) "
                          "VALUES ('Pold','%s','ord_old_00001','fp','[]',150,?,?,'MANUALLY_ADDED',?,?)" % h, (stake, "2026-10-01T12:00:00Z", status, pnl))
        self.logs.commit()
        return h

    def test_migration_gives_every_existing_log_five_hundred_and_counts_each_open_stake_once(self):
        h = self.old_log(stake=20.0)
        self.assertEqual(pl.migrate_bankrolls(self.logs, NOW)["migrated"], 1)
        st = pl.account_state(self.logs, h)
        self.assertEqual((st["starting_balance"], st["open_stakes"], st["available_cash"]), (500.0, 20.0, 480.0))
        self.assertEqual(self.logs.execute("SELECT COUNT(*) FROM bets").fetchone()[0], 1)

    def test_migration_is_idempotent(self):
        h = self.old_log(stake=20.0)
        pl.migrate_bankrolls(self.logs, NOW)
        self.assertEqual(pl.migrate_bankrolls(self.logs, NOW + dt.timedelta(hours=1))["migrated"], 0)
        self.assertEqual(pl.account_state(self.logs, h)["available_cash"], 480.0)

    def test_settled_history_is_kept_and_counted_in_the_balance(self):
        h = self.old_log(stake=20.0, status="WIN", pnl=30.0)
        pl.migrate_bankrolls(self.logs, NOW)
        st = pl.account_state(self.logs, h)
        self.assertEqual((st["settled_pnl"], st["available_cash"], st["open_stakes"]), (30.0, 530.0, 0.0))

    def test_the_unclaimed_legacy_bucket_has_no_bankroll(self):
        self.logs.execute("INSERT OR IGNORE INTO logs (log_hash, display_name, created_at_utc) VALUES (?,?,?)", (pl.UNCLAIMED_LEGACY, "Earlier manual tickets", "2026-10-01T00:00:00Z"))
        self.logs.commit()
        self.assertEqual(pl.migrate_bankrolls(self.logs, NOW)["migrated"], 0)
        self.assertEqual(pl.reconcile(self.logs), [])


def _aged(pool, hours):
    import copy
    p = copy.deepcopy(pool)
    for g in p["games"].values():
        for pl_ in g["players"].values():
            for mk in pl_["m"].values():
                mk["q"] = (dt.datetime(2026, 10, 8, 21, 58, tzinfo=dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
                mk["r"] = mk["q"]
    return p


if __name__ == "__main__":
    unittest.main()
