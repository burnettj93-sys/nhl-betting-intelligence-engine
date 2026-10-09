import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
import verify_personal_workflow as vpw  # noqa: E402

from operational import log_signing, personal_logs as pl, paper_bet_settlement_driver as drv, player_options as po
from tests.test_daily_tickets import NOW, board, fresh_ledger
from tests.test_manual_orders import DATE
from dashboard import order_client

KEYS = {"qa-one-code-1111": "AAAA-BBBB-CCCC-DDDD-EEEE", "qa-two-code-2222": "FFFF-GGGG-HHHH-JJJJ-KKKK"}


def personal_db():
    t = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    t.close()
    return pl.connect(t.name)


class TestVerifier(unittest.TestCase):
    def setUp(self):
        self.path, self.ledger = fresh_ledger()
        self.p = personal_db()
        self.legs = board(4, price=-105, p=0.62)
        self.opts = po.build_options(self.legs, DATE)["options"]

    def add(self, code, i, via="direct", create=True, oid=None):
        h = pl.code_hash(code)
        doc = order_client.build_personal_order(self.opts[i], order_id=oid or f"ord_{code[3:6]}{i}" + "z" * 7, log_hash=h, page_generated_at=None, stake=10,
                                                create={"creation_id": "crt_" + code[3:6] + "y" * 7, "display_name": code[:6], "write_pub": log_signing.public_key_hex(KEYS[code], h)} if create else None)
        doc["via"] = via
        res = pl.process_order(self.p, log_signing.sign(doc, KEYS[code], h), current_legs=self.legs, now=NOW, source="t")
        self.assertEqual(res["status"], pl.RECORDED)

    def add_builder(self, surname, passcode, n=1):
        """A last-name account created through the app path, with one Paper Parlay Builder bet."""
        from tests.product_fixture import builder_pool_doc
        slug = pl.surname_slug(surname, n)
        h = pl.account_key(slug)
        now = dt.datetime(2026, 10, 8, 22, 0, tzinfo=dt.timezone.utc)
        create = {"creation_id": "crt_" + slug.replace("-", "x") + "yyyy", "display_name": surname, "slug": slug, "write_pub": log_signing.public_key_hex(passcode, h)}
        leg = {"game_id": "2026020900", "participant_id": "P1", "participant_name": "Test Skater One", "market_family": "PLAYER_SOG_ALTERNATE", "threshold": 2, "side": "OVER", "american_price": -110.0}
        doc = order_client.build_builder_order([leg], order_id="ord_" + slug.replace("-", "x") + "b0001", log_hash=h, page_generated_at=None, stake=10, same_game_ack=False,
                                               combined_american=-110, create=create)
        doc["via"] = "direct"
        res = pl.process_order(self.p, log_signing.sign(doc, passcode, h), current_legs=[], now=now, source="t", builder_pool=builder_pool_doc())
        self.assertEqual(res["status"], pl.RECORDED, res)

    def test_pending_until_two_logs_were_created_through_the_app_path_and_one_settled(self):
        self.assertEqual(vpw.main(self.ledger, self.p, NOW)["result"], "PENDING")
        self.add("qa-one-code-1111", 0, via="link")                      # hand-filed: proves nothing about the app's credential
        r = vpw.main(self.ledger, self.p, NOW)
        self.assertEqual(r["result"], "PENDING")
        self.assertIn("found 0", " ".join(r["pending"]))

    def test_passes_only_with_two_app_created_accounts_builder_bets_a_settled_bet_and_an_untouched_model_book(self):
        self.add_builder("Qaone", "AB2D-EF3G")
        self.add_builder("Qatwo", "HJ4K-LM5N")
        self.assertEqual(vpw.main(self.ledger, self.p, NOW)["result"], "PENDING")          # nothing settled yet
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}]):
            pl.settle_open(self.p, None, NOW + dt.timedelta(days=1))
        r = vpw.main(self.ledger, self.p, NOW)
        self.assertEqual((r["result"], r["problems"], r["pending"]), ("PASS", [], []))
        self.assertEqual(len(r["logs"]), 2)

    def test_earlier_code_based_logs_without_builder_bets_stay_pending(self):
        self.add("qa-one-code-1111", 0)
        self.add("qa-two-code-2222", 1)
        with mock.patch.object(drv, "resolve_combo_bet", side_effect=[{"status": "WIN", "leg_results": []}, {"status": "LOSS", "leg_results": []}]):
            pl.settle_open(self.p, None, NOW + dt.timedelta(days=1))
        r = vpw.main(self.ledger, self.p, NOW)
        self.assertEqual(r["result"], "PENDING")
        self.assertIn("Paper Parlay Builder", " ".join(r["pending"]))

    def test_fails_when_a_personal_bet_id_appears_in_the_model_ledger(self):
        self.add("qa-one-code-1111", 0)
        self.add("qa-two-code-2222", 1)
        with mock.patch.object(vpw.wd, "check_reconciliation", return_value={"name": "reconciliation", "status": "FAIL", "detail": "a bet id exists in both books"}):
            r = vpw.main(self.ledger, self.p, NOW)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("model_book_reconciles_three_ways_and_each_log_matches_its_rows", r["problems"])


if __name__ == "__main__":
    unittest.main()
