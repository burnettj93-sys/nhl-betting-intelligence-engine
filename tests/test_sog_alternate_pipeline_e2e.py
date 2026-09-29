"""
SOG Contract Certification block (2026-09-29), Sections 12-13: proves a real
archived DraftKings player_shots_on_goal_alternate payload can flow end to
end -- raw payload -> event mapping -> market parsing -> player identity ->
threshold mapping -> price -> captured_at -> NormalizedPropMarket -> real
NHL settlement (WIN/LOSS) -- using ONLY fixture data and the already-ingested
nhl.db schema (via db.init_db, the same frozen-schema pattern
tests/test_outcome_resolver.py already uses). No new provider request is
made anywhere in this file (Section 12's explicit instruction).

Also proves the SOG settlement boundary cases across the DK-verified ladder
(2+/3+/4+/5+): exactly-at-threshold (WIN) and threshold-minus-one (LOSS) for
each, and that a sportsbook "Over 2.5" line is never confused with the
model's own "2+" threshold anywhere in this chain (Section 13).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import db
from operational import outcome_resolver as resolver
from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing.line_mapping import line_to_threshold
from research.live_sog_pricing import event_mapping
from research.live_sog_pricing import market_parser
from research.live_sog_pricing import player_mapping


def _load_fixture():
    path = Path(__file__).resolve().parent / "fixtures" / "draftkings_player_sog_alternate_real_payload.json"
    with open(path) as f:
        return json.load(f)


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = db.init_db(db_path=Path(tmp.name), wipe=True)
    for team in ("CAR", "FLA"):
        conn.execute("INSERT OR IGNORE INTO teams (team_id, full_name) VALUES (?, ?)", (team, team))
    conn.commit()
    return conn


def _insert_final_game(conn, game_id):
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, home_team, away_team, game_state,
           home_score, away_score, final_period_type, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, "20262027", "2026-09-29", "CAR", "FLA", "FINAL", 4, 2, "REG", "test_fixture"))
    conn.commit()


def _insert_player_stat(conn, game_id, player_id, team_id, shots):
    conn.execute(
        """INSERT INTO player_game_stats (game_id, player_id, team_id, toi_minutes, goals,
           assists, shots, hits, blocked_shots, played, revision_number, effective_at_utc,
           observed_at_utc, source) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
        (game_id, player_id, team_id, 18.0, 0, 0, shots, None, None, 1,
         "2026-09-29T23:30:00Z", "2026-09-29T23:30:00Z", "test_fixture"))
    conn.commit()


# A minimal stand-in for research/player_sog's real identity corpus, built with
# build_player_index()'s exact real output shape -- never the real corpus
# itself (this fixture's players are not necessarily on these real-world
# rosters; only the PIPELINE is under test here, not the corpus's own
# correctness, which TestPlayerSogAlternateContractParity and the real
# player_mapping test suite already cover separately).
_SCHEDULE = [{"game_id": 555, "home_team": "CAR", "away_team": "FLA", "game_date": "2026-09-29"}]


def _player_index():
    return player_mapping.build_player_index([
        {"player_id": "P_TKACHUK_BRADY", "player_name": "Brady Tkachuk", "team": "CAR",
         "game_date": "2026-09-20"},
    ])


class TestSogAlternateEndToEndIngestion(unittest.TestCase):
    """Section 12: raw payload -> parser -> event mapping -> player identity ->
    threshold -> price -> captured_at -> operational quote object -> real
    settlement, using fixtures only, never a new provider request."""

    @staticmethod
    def _quote_at(point, player_name="Brady Tkachuk"):
        payload = _load_fixture()
        quotes = market_parser.parse_event_odds_response(payload)
        ladder = market_parser.group_alternate_ladder(quotes)
        key = next(k for k in ladder if k[2] == player_name)
        return payload, ladder[key][point]

    def test_full_pipeline_produces_a_win(self):
        payload, quote = self._quote_at(3.5)  # sportsbook "Over 3.5" == model's own "4+"

        event_result = event_mapping.map_event_to_game(
            {"id": payload["id"], "home_team": payload["home_team"],
             "away_team": payload["away_team"], "commence_time": payload["commence_time"]},
            schedule=_SCHEDULE)
        self.assertEqual(event_result["status"], "MATCHED")
        game_id = event_result["game_id"]

        home_abbrev = event_mapping.normalize_team_name(payload["home_team"])
        away_abbrev = event_mapping.normalize_team_name(payload["away_team"])
        identity_result = player_mapping.map_player(
            "Brady Tkachuk", home_abbrev, away_abbrev, _player_index())
        self.assertEqual(identity_result["status"], "MATCHED")
        player_id = identity_result["player_id"]

        parsed = pa.parse_the_odds_api_market(
            quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id=player_id)
        self.assertEqual(parsed["status"], "PARSED")
        market = parsed["market"]
        self.assertEqual(market.threshold, 4)
        self.assertEqual(market.american_price, 195)
        self.assertEqual(market.captured_at_utc, "2026-09-29T12:14:34Z")

        conn = _fresh_db()
        _insert_final_game(conn, game_id)
        _insert_player_stat(conn, game_id, player_id, home_abbrev, shots=4)  # exactly at threshold
        settlement = resolver.resolve_player_stat_threshold(
            conn, market_family="PLAYER_SOG", game_id=game_id, player_id=player_id,
            threshold=market.threshold, side=market.side)
        self.assertEqual(settlement["status"], resolver.RESOLVED)
        self.assertTrue(settlement["outcome_hit"])
        self.assertEqual(settlement["actual_value"], 4)
        self.assertEqual(settlement["resolution_source"], "OFFICIAL_NHL_BOXSCORE")

    def test_full_pipeline_produces_a_loss(self):
        payload, quote = self._quote_at(3.5)  # still model threshold 4+
        event_result = event_mapping.map_event_to_game(
            {"id": payload["id"], "home_team": payload["home_team"],
             "away_team": payload["away_team"], "commence_time": payload["commence_time"]},
            schedule=_SCHEDULE)
        game_id = event_result["game_id"]
        home_abbrev = event_mapping.normalize_team_name(payload["home_team"])
        away_abbrev = event_mapping.normalize_team_name(payload["away_team"])
        player_id = player_mapping.map_player(
            "Brady Tkachuk", home_abbrev, away_abbrev, _player_index())["player_id"]
        market = pa.parse_the_odds_api_market(
            quote, sportsbook="draftkings", canonical_market_id="PLAYER_SOG_ALTERNATE",
            event_id=payload["id"], player_id=player_id)["market"]

        conn = _fresh_db()
        _insert_final_game(conn, game_id)
        _insert_player_stat(conn, game_id, player_id, home_abbrev, shots=3)  # one short of 4+
        settlement = resolver.resolve_player_stat_threshold(
            conn, market_family="PLAYER_SOG", game_id=game_id, player_id=player_id,
            threshold=market.threshold, side=market.side)
        self.assertEqual(settlement["status"], resolver.RESOLVED)
        self.assertFalse(settlement["outcome_hit"])
        self.assertEqual(settlement["actual_value"], 3)

    def test_unmatched_identity_never_reaches_settlement(self):
        # Sam Bennett is a real quote in this fixture but is NOT in this test's
        # stub identity index -- proves the pipeline refuses to settle an
        # unresolved player rather than guessing.
        identity_result = player_mapping.map_player("Sam Bennett", "CAR", "FLA", _player_index())
        self.assertEqual(identity_result["status"], "UNMATCHED")
        self.assertIsNone(identity_result["player_id"])


class TestSogSettlementBoundaryCases(unittest.TestCase):
    """Section 13: boundary-case settlement proof across the DK-verified
    ladder (2+/3+/4+/5+) -- exactly-at-threshold (WIN) and
    threshold-minus-one (LOSS) for each -- plus an explicit proof that
    sportsbook 'Over 2.5' is never confused with model threshold '2+'
    anywhere in this chain."""

    @staticmethod
    def _settle(threshold, shots):
        conn = _fresh_db()
        _insert_final_game(conn, 1)
        _insert_player_stat(conn, 1, "P1", "CAR", shots=shots)
        return resolver.resolve_player_stat_threshold(
            conn, market_family="PLAYER_SOG", game_id=1, player_id="P1",
            threshold=threshold, side="OVER")

    def test_threshold_2plus_boundary(self):
        self.assertTrue(self._settle(2, shots=2)["outcome_hit"])
        self.assertFalse(self._settle(2, shots=1)["outcome_hit"])

    def test_threshold_3plus_boundary(self):
        self.assertTrue(self._settle(3, shots=3)["outcome_hit"])
        self.assertFalse(self._settle(3, shots=2)["outcome_hit"])

    def test_threshold_4plus_boundary(self):
        self.assertTrue(self._settle(4, shots=4)["outcome_hit"])
        self.assertFalse(self._settle(4, shots=3)["outcome_hit"])

    def test_threshold_5plus_boundary(self):
        self.assertTrue(self._settle(5, shots=5)["outcome_hit"])
        self.assertFalse(self._settle(5, shots=4)["outcome_hit"])

    def test_sportsbook_over_2_5_maps_to_3plus_never_settled_as_2plus(self):
        mapped_threshold = line_to_threshold(2.5)
        self.assertEqual(mapped_threshold, 3)
        # 2 shots would be a WIN under an (incorrect) "2+" reading of "Over 2.5" --
        # it must NOT be a win under the correctly-mapped "3+" threshold.
        self.assertFalse(self._settle(mapped_threshold, shots=2)["outcome_hit"])
        self.assertTrue(self._settle(mapped_threshold, shots=3)["outcome_hit"])


if __name__ == "__main__":
    unittest.main()
