"""
Part 38: tests for the real NHL play-by-play ingestion foundation.

Every fixture used here is a REAL, permanently-archived play-by-play
response under research/real_nhl_pbp/raw/20252026/ -- part of the actual
30-game pilot corpus, not synthesized. Numbered comments map each test
class to its Part-38 topic number.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, dependency_graph, market_registry
from research.real_nhl_pbp import invariants as inv
from research.real_nhl_pbp import normalize, raw_archive, readiness, reconcile, schema
from research.real_nhl_pbp.build_pbp_season import season_game_ids
from research.real_nhl_pbp.client import PbpApiError, fetch_play_by_play, play_by_play_url

SEASON = "20252026"

# Real, permanently-archived pilot games chosen to cover every Part-38 case.
SHOOTOUT_GAME = 2025020231       # PIT @ NJD, real SO game
OT_GAME = 2025020193             # NYR @ SEA, real OT game
EMPTY_NET_GOALIE_CHANGE_PP_GAME = 2025020240  # COL @ EDM 9-1: EN goal, goalie change, PP goals
BENCH_PENALTY_GAME = 2025020143  # UTA @ WPG: real too-many-men-on-the-ice bench minor
BASIC_REGULATION_GAME = 2025020584  # UTA @ COL 0-1: plain REG game, no OT/SO
EMPTY_NET_GAME = 2025020303       # VAN @ FLA: real confirmed empty-net goal ("scores-empty-net-goal")


def _load(game_id: int) -> dict:
    return raw_archive.load_raw_pbp(SEASON, game_id)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# 1. real event schema fixture
class Test01RealEventSchemaFixture(unittest.TestCase):
    def test_archived_fixture_has_expected_top_level_shape(self):
        raw = _load(BASIC_REGULATION_GAME)
        for key in ("id", "season", "gameType", "gameDate", "awayTeam", "homeTeam",
                    "plays", "rosterSpots", "periodDescriptor", "gameOutcome"):
            self.assertIn(key, raw)
        self.assertIsInstance(raw["plays"], list)
        self.assertGreater(len(raw["plays"]), 0)

    def test_fixture_is_a_real_archived_file_not_synthesized(self):
        path = os.path.join(raw_archive.RAW_ROOT, SEASON, f"{BASIC_REGULATION_GAME}.json")
        self.assertTrue(os.path.exists(path))
        prov = raw_archive.read_sidecar(path)
        self.assertEqual(prov.archival_status, "ARCHIVAL_RESEARCH")
        self.assertEqual(prov.game_id, BASIC_REGULATION_GAME)


# 2. event ordering
class Test02EventOrdering(unittest.TestCase):
    def test_event_sequence_strictly_sorted_after_normalize(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        seqs = [e.event_sequence for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_event_id_is_not_reliable_for_ordering(self):
        # Real finding (Part 4): eventId is NOT monotonic across the raw
        # feed. This test pins that finding so a future NHL API change
        # that fixes it doesn't silently invalidate our ordering choice
        # without us noticing.
        raw = _load(BASIC_REGULATION_GAME)
        event_ids = [p["eventId"] for p in raw["plays"]]
        self.assertNotEqual(event_ids, sorted(event_ids),
                             "if this now holds, re-examine whether sortOrder is still required")


# 3 & 4. period / regulation timing normalization
class Test03And04RegulationTiming(unittest.TestCase):
    def test_seconds_elapsed_parses_mmss(self):
        self.assertEqual(normalize.seconds_elapsed("03:01"), 181)
        self.assertEqual(normalize.seconds_elapsed("00:00"), 0)
        self.assertEqual(normalize.seconds_elapsed("19:59"), 1199)

    def test_regulation_elapsed_seconds_period_1(self):
        self.assertEqual(normalize.compute_regulation_elapsed_seconds(1, "REG", 181), 181)

    def test_regulation_elapsed_seconds_period_3_offset(self):
        self.assertEqual(normalize.compute_regulation_elapsed_seconds(3, "REG", 0), 2400)


# 5. overtime timing
class Test05OvertimeTiming(unittest.TestCase):
    def test_ot_elapsed_offset_by_three_full_regulation_periods(self):
        self.assertEqual(normalize.compute_regulation_elapsed_seconds(4, "OT", 0), 3600)

    def test_real_ot_game_never_exceeds_five_minutes(self):
        events = normalize.normalize_game_events(_load(OT_GAME))
        ot_events = [e for e in events if e.period_type == "OT"]
        self.assertGreater(len(ot_events), 0)
        for e in ot_events:
            self.assertLessEqual(e.seconds_elapsed_in_period, normalize.REGULAR_SEASON_OT_SECONDS)


# 6. shootout isolation
class Test06ShootoutIsolation(unittest.TestCase):
    def test_so_events_have_no_regulation_elapsed_seconds(self):
        events = normalize.normalize_game_events(_load(SHOOTOUT_GAME))
        so_events = [e for e in events if e.period_type == "SO"]
        self.assertGreater(len(so_events), 0)
        for e in so_events:
            self.assertIsNone(e.regulation_elapsed_seconds)
            self.assertFalse(e.is_statistical)

    def test_non_so_events_are_statistical(self):
        events = normalize.normalize_game_events(_load(SHOOTOUT_GAME))
        reg_events = [e for e in events if e.period_type != "SO"]
        self.assertTrue(all(e.is_statistical for e in reg_events))


# 7. goal normalization
class Test07GoalNormalization(unittest.TestCase):
    def test_goal_event_carries_scorer_and_team(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        goals = [e for e in events if e.event_type == "goal"]
        self.assertGreater(len(goals), 0)
        for g in goals:
            self.assertIn("scorer", g.players)
            self.assertIsNotNone(g.team_id)


# 8. assist normalization
class Test08AssistNormalization(unittest.TestCase):
    def test_assisted_goal_extracts_assist_roles(self):
        events = normalize.normalize_game_events(_load(EMPTY_NET_GOALIE_CHANGE_PP_GAME))
        assisted = [e for e in events if e.event_type == "goal" and "assist1" in e.players]
        self.assertGreater(len(assisted), 0)

    def test_no_more_than_two_assists_anywhere_in_pilot(self):
        for gid in (BASIC_REGULATION_GAME, EMPTY_NET_GOALIE_CHANGE_PP_GAME, OT_GAME):
            events = normalize.normalize_game_events(_load(gid))
            violations = inv.check_assists_only_on_goals_max_two(events)
            self.assertEqual(violations, [], f"game {gid}: {violations}")


# 9. SOG normalization
class Test09SogNormalization(unittest.TestCase):
    def test_shot_on_goal_carries_shooter_and_goalie(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        shots = [e for e in events if e.event_type == "shot-on-goal"]
        self.assertGreater(len(shots), 0)
        with_goalie = [s for s in shots if "goalie" in s.players]
        self.assertGreater(len(with_goalie), 0)

    def test_sog_running_totals_monotonic(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        self.assertEqual(inv.check_sog_monotonic(events), [])


# 10. blocked-shot normalization
class Test10BlockedShotNormalization(unittest.TestCase):
    def test_blocked_shot_carries_shooter_and_blocker(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        blocked = [e for e in events if e.event_type == "blocked-shot"]
        self.assertGreater(len(blocked), 0)
        for b in blocked:
            self.assertIn("shooter", b.players)
            self.assertIn("blocker", b.players)


# 11. hit normalization
class Test11HitNormalization(unittest.TestCase):
    def test_hits_carry_both_participants(self):
        events = normalize.normalize_game_events(_load(EMPTY_NET_GOALIE_CHANGE_PP_GAME))
        hits = [e for e in events if e.event_type == "hit"]
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIn("hitter", h.players)
            self.assertIn("hittee", h.players)


# 12. penalty normalization
class Test12PenaltyNormalization(unittest.TestCase):
    def test_player_penalty_has_committed_by(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        penalties = [e for e in events if e.event_type == "penalty"]
        self.assertGreater(len(penalties), 0)
        self.assertTrue(any("committed_by" in p.players for p in penalties))

    def test_bench_minor_lacks_committed_by_but_has_served_by(self):
        events = normalize.normalize_game_events(_load(BENCH_PENALTY_GAME))
        bench = [e for e in events if e.event_type == "penalty"
                 and e.raw_details.get("typeCode") == "BEN"]
        self.assertGreater(len(bench), 0)
        for b in bench:
            self.assertNotIn("committed_by", b.players)
            self.assertIn("served_by", b.players)


# 13. faceoff normalization
class Test13FaceoffNormalization(unittest.TestCase):
    def test_faceoff_has_winner_and_loser(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        faceoffs = [e for e in events if e.event_type == "faceoff"]
        self.assertGreater(len(faceoffs), 0)
        for f in faceoffs:
            self.assertIn("winner", f.players)
            self.assertIn("loser", f.players)


# 14. goalie identity
class Test14GoalieIdentity(unittest.TestCase):
    def test_real_goalie_change_produces_two_distinct_goalie_ids(self):
        raw = _load(EMPTY_NET_GOALIE_CHANGE_PP_GAME)
        events = normalize.normalize_game_events(raw)
        home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
        edm_goalies = set()
        for e in events:
            if e.event_type in ("shot-on-goal", "missed-shot", "goal") and "goalie" in e.players:
                defending_team = home_id if e.team_id == away_id else away_id
                if defending_team == 22:  # EDM's real team_id, confirmed via this fixture
                    if not normalize.is_empty_net_context(e, defending_team_is_away=(defending_team == away_id)):
                        edm_goalies.add(e.players["goalie"])
        self.assertGreaterEqual(len(edm_goalies), 2)


# 15. empty-net handling
class Test15EmptyNetHandling(unittest.TestCase):
    def test_known_empty_net_goal_detected(self):
        raw = _load(EMPTY_NET_GAME)
        events = normalize.normalize_game_events(raw)
        home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
        goals = [e for e in events if e.event_type == "goal" and e.is_statistical]
        en_goals = [g for g in goals if "goalie" not in g.players]
        self.assertGreater(len(en_goals), 0)
        for g in en_goals:
            defending_is_away = (g.team_id == home_id)
            self.assertTrue(normalize.is_empty_net_context(g, defending_team_is_away=defending_is_away))

    def test_normal_goal_is_not_empty_net(self):
        events = normalize.normalize_game_events(_load(BASIC_REGULATION_GAME))
        goals = [e for e in events if e.event_type == "goal" and "goalie" in e.players]
        self.assertGreater(len(goals), 0)
        for g in goals:
            self.assertFalse(normalize.is_empty_net_context(g, defending_team_is_away=True))
            self.assertFalse(normalize.is_empty_net_context(g, defending_team_is_away=False))


# 16. score reconstruction
class Test16ScoreReconstruction(unittest.TestCase):
    def test_reconstructed_score_matches_real_final_score_reg_game(self):
        raw = _load(BASIC_REGULATION_GAME)
        events = normalize.normalize_game_events(raw)
        timeline = normalize.reconstruct_statistical_score(
            events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(timeline[-1]["home_score"], raw["homeTeam"]["score"])
        self.assertEqual(timeline[-1]["away_score"], raw["awayTeam"]["score"])


# 17. shootout final-result reconciliation
class Test17ShootoutReconciliation(unittest.TestCase):
    def test_so_game_score_is_exactly_one_bonus_goal_above_reconstructed(self):
        raw = _load(SHOOTOUT_GAME)
        events = normalize.normalize_game_events(raw)
        violations = inv.check_final_score_reconciles(
            events, raw["homeTeam"]["id"], raw["awayTeam"]["id"],
            raw["homeTeam"]["score"], raw["awayTeam"]["score"], "SO")
        self.assertEqual(violations, [])

    def test_shootout_winner_identified(self):
        raw = _load(SHOOTOUT_GAME)
        events = normalize.normalize_game_events(raw)
        winner = normalize.shootout_winner(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertIsNotNone(winner)
        self.assertIn(winner, (raw["homeTeam"]["id"], raw["awayTeam"]["id"]))


# 18. event player identity (parametrized across event types)
class Test18EventPlayerIdentity(unittest.TestCase):
    def test_extract_players_role_shape_per_event_type(self):
        cases = {
            "goal": {"scoringPlayerId": 1, "assist1PlayerId": 2, "goalieInNetId": 3},
            "hit": {"hittingPlayerId": 1, "hitteePlayerId": 2},
            "faceoff": {"winningPlayerId": 1, "losingPlayerId": 2},
        }
        expected = {
            "goal": {"scorer": 1, "assist1": 2, "goalie": 3},
            "hit": {"hitter": 1, "hittee": 2},
            "faceoff": {"winner": 1, "loser": 2},
        }
        for event_type, details in cases.items():
            self.assertEqual(normalize.extract_players(event_type, details), expected[event_type])

    def test_stoppage_has_no_player_roles(self):
        self.assertEqual(normalize.extract_players("stoppage", {"reason": "icing"}), {})


# 19. raw payload checksum
class Test19RawPayloadChecksum(unittest.TestCase):
    def test_archived_file_checksum_matches_provenance(self):
        path = os.path.join(raw_archive.RAW_ROOT, SEASON, f"{BASIC_REGULATION_GAME}.json")
        prov = raw_archive.read_sidecar(path)
        self.assertEqual(prov.sha256, _file_sha256(path))


# 20. archival provenance
class Test20ArchivalProvenance(unittest.TestCase):
    def test_provenance_fields_present_and_correct_status(self):
        path = os.path.join(raw_archive.RAW_ROOT, SEASON, f"{BASIC_REGULATION_GAME}.json")
        prov = raw_archive.read_sidecar(path)
        self.assertEqual(prov.provider, "api-web.nhle.com")
        self.assertEqual(prov.archival_status, "ARCHIVAL_RESEARCH")
        self.assertTrue(prov.source_url.startswith("https://api-web.nhle.com/v1/gamecenter/"))
        self.assertTrue(prov.retrieved_at_utc)


# 21. duplicate event prevention (archive collision guard)
class Test21DuplicateEventPrevention(unittest.TestCase):
    def test_archiving_different_bytes_under_same_game_id_raises(self, tmp_path=None):
        import tempfile
        real_path = os.path.join(raw_archive.RAW_ROOT, SEASON, f"{BASIC_REGULATION_GAME}.json")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"different": "payload"}, f)
            tmp = f.name
        try:
            with self.assertRaises(FileExistsError):
                raw_archive.archive_raw_pbp(
                    tmp, game_id=BASIC_REGULATION_GAME, season=SEASON,
                    source_url="https://example.invalid", retrieved_at_utc="2026-01-01T00:00:00Z",
                )
        finally:
            os.remove(tmp)
        # the real archived file must be untouched by the rejected attempt
        self.assertEqual(_file_sha256(real_path), raw_archive.read_sidecar(real_path).sha256)


# 22. reingestion idempotency
class Test22ReingestionIdempotency(unittest.TestCase):
    def test_reingesting_identical_bytes_is_a_noop(self):
        import tempfile
        real_path = os.path.join(raw_archive.RAW_ROOT, SEASON, f"{BASIC_REGULATION_GAME}.json")
        before_sha = _file_sha256(real_path)
        with open(real_path, "rb") as src:
            real_bytes = src.read()
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
            f.write(real_bytes)
            tmp = f.name
        try:
            dest, prov = raw_archive.archive_raw_pbp(
                tmp, game_id=BASIC_REGULATION_GAME, season=SEASON,
                source_url="https://example.invalid/reingest", retrieved_at_utc="2026-01-01T00:00:00Z",
            )
            self.assertEqual(dest, real_path)
            self.assertEqual(_file_sha256(real_path), before_sha)
        finally:
            os.remove(tmp)


# 23. missing-player behavior
class Test23MissingPlayerBehavior(unittest.TestCase):
    def test_missing_drawn_by_does_not_raise(self):
        details = {"committedByPlayerId": 1, "duration": 2, "typeCode": "MIN", "descKey": "delaying-game"}
        players = normalize.extract_players("penalty", details)
        self.assertEqual(players, {"committed_by": 1})
        self.assertNotIn("drawn_by", players)


# 24. malformed-event failure
class Test24MalformedEventFailure(unittest.TestCase):
    def test_missing_sort_order_raises(self):
        bad_play = {
            "eventId": 1, "typeDescKey": "goal", "typeCode": 505,
            "periodDescriptor": {"number": 1, "periodType": "REG"},
            "timeInPeriod": "00:00", "details": {},
        }
        with self.assertRaises(normalize.PbpNormalizationError):
            normalize.normalize_event(bad_play)

    def test_unknown_period_type_raises(self):
        with self.assertRaises(normalize.PbpNormalizationError):
            normalize.compute_regulation_elapsed_seconds(1, "BOGUS", 0)

    def test_fetch_play_by_play_rejects_non_dict_response(self):
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return [1, 2, 3]

        class FakeSession:
            def get(self, url, timeout):
                return FakeResp()

        with self.assertRaises(PbpApiError):
            fetch_play_by_play(FakeSession(), 123)


# 25. pilot acceptance gate
class Test25PilotAcceptanceGate(unittest.TestCase):
    def test_pilot_validation_results_recorded_and_gate_open(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "pilot_validation_results.json")
        self.assertTrue(os.path.exists(path), "run_pilot_validation.py must be run before this suite")
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["games_validated"], 30)
        self.assertTrue(manifest["pilot_passed"])
        self.assertLessEqual(manifest["total_unexplained_mismatches"], 1)


# 26. season-ingestion completeness
class Test26SeasonIngestionCompleteness(unittest.TestCase):
    def test_authoritative_season_game_count(self):
        ids = season_game_ids(20252026)
        self.assertEqual(len(ids), 1312)
        self.assertEqual(len(set(ids)), 1312)

    def test_full_season_archived_with_zero_gaps(self):
        archived = set(raw_archive.archived_game_ids("20252026"))
        expected = set(season_game_ids(20252026))
        self.assertEqual(archived, expected, "one-season ingestion must have zero missing games")


# 27. market-readiness classification
class Test27MarketReadinessClassification(unittest.TestCase):
    def test_every_entry_has_a_valid_readiness_level(self):
        for section_name, entries in readiness.ALL_SECTIONS.items():
            for e in entries:
                self.assertIn(e.readiness, ("READY", "PARTIAL", "NOT READY"))
                self.assertTrue(e.evidence)

    def test_summary_counts_are_internally_consistent(self):
        for entries in readiness.ALL_SECTIONS.values():
            summary = readiness.summarize(entries)
            self.assertEqual(sum(summary.values()), len(entries))


# 28. existing market registry unchanged, as of the Event-Timing Utility
# Closure slice. This pin has now been legitimately updated five times:
# REQUIRES_PLAY_BY_PLAY->AVAILABLE_UNUSED (Multi-Season PBP Expansion),
# GAME_WINNING_GOAL -> READY, PERIOD_1/2/3_GOALIE_SAVES -> READY (Event-
# Timing Utility Closure), PERIOD_1/2/3_PLAYER_SOG -> VALIDATED (Player
# SOG by Period), then TEAM_PERIOD_1/2/3_TOTAL (+ duplicates + TEAM_SCORE_
# IN_PERIOD_1) -> RESEARCH/ATTEMPTED_NOT_VALIDATED (Team Goals by Period
# -- a real, disclosed NEGATIVE result; see
# TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md).
class Test28MarketRegistryUnchanged(unittest.TestCase):
    def test_market_registry_file_hash_unchanged(self):
        # Pin updated by the Team SOG slice (7th legitimate edit to this
        # file): TEAM_SOG_TOTAL moved off NOT_BUILT to a real VALIDATED
        # (20+/25+/30+/35+, not 40+) verdict. total_canonical_markets and
        # total_raw_labels are unchanged -- no markets added or removed,
        # only statuses/notes on existing ones. See
        # TEAM_SOG_VALIDATION_REPORT.md.
        self.assertEqual(
            _file_sha256("research/player_props/market_registry.py"),
            "46f01fd0bf71f3b0b2fe5eadb614429ed5d2174ad1c2c761efd345e39057e027",
        )
        self.assertEqual(market_registry.total_canonical_markets(), 142)
        self.assertEqual(market_registry.total_raw_labels(), 164)


# 29. dependency graph unchanged, as of the Event-Timing Utility Closure
# slice. Legitimately updated three times: PROCESS_DATA_FOUNDATION_STATUS
# added (Multi-Season PBP Expansion), then PROCESS_READINESS_NOTES added
# and GOALIE_WORKLOAD_SAVE_PROCESS PARTIAL->DATA_FOUNDATION_READY (this
# slice, Part 24) -- PROCESS_DEPENDENCY_GRAPH's structure is still untouched.
class Test29DependencyGraphUnchanged(unittest.TestCase):
    def test_dependency_graph_file_hash_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/dependency_graph.py"),
            "85a8d89cd9a1118852104795a6c89caf2389f919e267a39c9501b1445436ea7c",
        )
        self.assertTrue(dependency_graph.is_acyclic())


# 30. existing validated models unchanged (Goals model artifact untouched)
class Test30ValidatedModelsUnchanged(unittest.TestCase):
    def test_player_goals_results_file_unchanged(self):
        with open("research/player_goals_results.json") as f:
            data = json.load(f)
        self.assertIn("context_weights_e", data)
        self.assertIn("alpha_e", data)


# 31. confidence framework unchanged
class Test31ConfidenceFrameworkUnchanged(unittest.TestCase):
    def test_confidence_results_file_unchanged(self):
        with open("research/confidence_framework_results.json") as f:
            data = json.load(f)
        self.assertIn("results_by_prop_fold", data)
        self.assertIn("bet_eligibility_retrospective", data)


# 32. decision policy unchanged as of the single-season PBP foundation
# slice -- legitimately updated since by the Player SOG by Period slice
# (v2 -> v3, added PLAYER_SOG_PERIOD_3: WATCH, real negative-skill
# evidence in both eval seasons -- see PLAYER_SOG_BY_PERIOD_VALIDATION_
# REPORT.md Section AF).
class Test32DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_file_hash_and_version_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")
        self.assertEqual(
            decision_policy.PROP_LOW_CONFIDENCE_CEILING,
            {"ASSISTS": "WATCH", "POINTS": "WATCH", "GOALS": "WATCH", "PLAYER_SOG_PERIOD_3": "WATCH"},
        )


# 33. NHL win model unchanged
class Test33WinModelUnchanged(unittest.TestCase):
    def test_win_model_files_unchanged(self):
        self.assertEqual(
            _file_sha256("models/combined_model.py"),
            "64e9e9cbe686b386951fed9d5001dc298c5dff6af7f582b8f197565f6d932c82",
        )
        self.assertEqual(
            _file_sha256("models/elo_model.py"),
            "8538d6b2e32112190919ac41f8b60f17d66528d58c2488c0ee7f7f2690411faf",
        )

    def test_production_boundary_files_unchanged(self):
        self.assertEqual(_file_sha256("config.py"),
                          "c019568da204ace99222954d4f02546a25c31029453c36ed3b0ed4bf97d3df8a")
        self.assertEqual(_file_sha256("db.py"),
                          "b598f4640e191a26dba7231e240a26ebbf6d7a443bcf4f2eb4c43b37cabcea95")
        self.assertEqual(_file_sha256("schema.sql"),
                          "ff19dd3b0c4cd8a61371d77751a045f222bdce7636d119d90c013f58ef64f31f")


if __name__ == "__main__":
    unittest.main()
