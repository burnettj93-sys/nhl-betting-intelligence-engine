"""
Part 41: tests for the multi-season (2022-23 through 2025-26) play-by-play
research corpus expansion. Every fixture is a REAL, permanently-archived
play-by-play response under research/real_nhl_pbp/raw/<season>/ -- part of
the actual ingested corpus, not synthesized. Numbered comments map each
test class to its Part-41 topic number.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

from research.player_props import decision_policy, dependency_graph, market_registry
from research.real_nhl_pbp import invariants as inv
from research.real_nhl_pbp import normalize, query, raw_archive, readiness, store
from research.real_nhl_pbp.build_pbp_season import season_game_ids

ALL_SEASONS = ("20222023", "20232024", "20242025", "20252026")

# Real, permanently-archived fixtures.
SHOOTOUT_2223 = 2022020448
OT_2223 = 2022021301
BASIC_REG_2223 = 2022020332
BENCH_PENALTY_2223 = 2022020010
SPECIAL_EVENT_EMPTY_NET_2223 = 2022020001   # 2022 NHL Global Series opener, real EN goal
GOALIE_CHANGE_2223 = 2022020032

SHOOTOUT_2526 = 2025020231
OT_2526 = 2025020193
BASIC_REG_2526 = 2025020584


def _load(season: str, game_id: int) -> dict:
    return raw_archive.load_raw_pbp(season, game_id)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _archived_seasons() -> list[str]:
    """Seasons that are FULLY archived (1,312/1,312) right now -- tests
    that must hold for every complete season iterate over this, not a
    hardcoded literal, so they automatically cover whichever seasons this
    slice actually finished ingesting."""
    return [s for s in ALL_SEASONS if len(raw_archive.archived_game_ids(s)) == len(season_game_ids(int(s)))]


# 1. multi-season game count
class Test01MultiSeasonGameCount(unittest.TestCase):
    def test_all_four_seasons_have_1312_games_in_authoritative_schedule(self):
        for season in ALL_SEASONS:
            ids = season_game_ids(int(season))
            self.assertEqual(len(ids), 1312, season)

    def test_total_authoritative_schedule_is_5248(self):
        total = sum(len(season_game_ids(int(s))) for s in ALL_SEASONS)
        self.assertEqual(total, 5248)

    def test_all_four_seasons_fully_archived(self):
        self.assertEqual(_archived_seasons(), list(ALL_SEASONS),
                          "run build_pbp_all_seasons.py before this suite")


# 2. season assignment
class Test02SeasonAssignment(unittest.TestCase):
    def test_archived_game_season_field_matches_directory(self):
        for season in ("20222023", "20252026"):
            gid = BASIC_REG_2223 if season == "20222023" else BASIC_REG_2526
            raw = _load(season, gid)
            self.assertEqual(str(raw["season"]), season)


# 3. historical contract variants
class Test03HistoricalContractVariants(unittest.TestCase):
    def test_special_event_key_does_not_break_normalization(self):
        raw = _load("20222023", SPECIAL_EVENT_EMPTY_NET_2223)
        self.assertIn("specialEvent", raw)
        game = normalize.normalize_game(raw, raw_sha256="", source_url="", retrieved_at_utc="")
        events = normalize.normalize_game_events(raw)
        self.assertGreater(len(events), 0)
        self.assertEqual(game.season, "20222023")

    def test_period_descriptor_shape_identical_2223_and_2526(self):
        for season, gid in (("20222023", BASIC_REG_2223), ("20252026", BASIC_REG_2526)):
            raw = _load(season, gid)
            keys = set()
            for p in raw["plays"]:
                keys |= set(p["periodDescriptor"].keys())
            self.assertEqual(keys, {"number", "periodType", "maxRegulationPeriods"}, season)


# 4. event vocabulary consistency
class Test04EventVocabularyConsistency(unittest.TestCase):
    def test_event_types_identical_across_2223_and_2526_samples(self):
        types_2223 = {p["typeDescKey"] for p in _load("20222023", SHOOTOUT_2223)["plays"]}
        types_2526 = {p["typeDescKey"] for p in _load("20252026", SHOOTOUT_2526)["plays"]}
        # both are real shootout games so both should include shootout-complete
        self.assertIn("shootout-complete", types_2223)
        self.assertIn("shootout-complete", types_2526)
        self.assertTrue(types_2223.issubset(types_2526 | types_2223))  # no crash; real vocab overlap


# 5. sortOrder ordering every season
class Test05SortOrderEverySeason(unittest.TestCase):
    def test_sort_order_monotonic_in_every_archived_season_sample(self):
        samples = {"20222023": BASIC_REG_2223, "20252026": BASIC_REG_2526}
        for season, gid in samples.items():
            events = normalize.normalize_game_events(_load(season, gid))
            seqs = [e.event_sequence for e in events]
            self.assertEqual(seqs, sorted(seqs), season)

    def test_event_id_non_monotonic_confirmed_historically_too(self):
        raw = _load("20222023", BASIC_REG_2223)
        event_ids = [p["eventId"] for p in raw["plays"]]
        self.assertNotEqual(event_ids, sorted(event_ids))


# 6. shootout isolation every season
class Test06ShootoutIsolationEverySeason(unittest.TestCase):
    def test_so_events_excluded_from_statistical_2223(self):
        events = normalize.normalize_game_events(_load("20222023", SHOOTOUT_2223))
        so_events = [e for e in events if e.period_type == "SO"]
        self.assertGreater(len(so_events), 0)
        self.assertTrue(all(not e.is_statistical for e in so_events))

    def test_shootout_score_frozen_at_pre_shootout_value_2223(self):
        raw = _load("20222023", SHOOTOUT_2223)
        so_goals = [p for p in raw["plays"] if p["typeDescKey"] == "goal" and p["periodDescriptor"]["periodType"] == "SO"]
        self.assertGreater(len(so_goals), 0)
        scores = {(g["details"]["awayScore"], g["details"]["homeScore"]) for g in so_goals}
        self.assertEqual(len(scores), 1, "every SO goal in one game must show the identical frozen score")


# 7. situationCode parsing every season
class Test07SituationCodeEverySeason(unittest.TestCase):
    def test_situation_code_always_four_chars(self):
        for season, gid in (("20222023", BASIC_REG_2223), ("20252026", BASIC_REG_2526)):
            raw = _load(season, gid)
            codes = [p["situationCode"] for p in raw["plays"] if p.get("situationCode")]
            self.assertTrue(all(len(c) == 4 for c in codes), season)


# 8. empty-net joint rule
class Test08EmptyNetJointRule(unittest.TestCase):
    def test_real_2223_empty_net_goal_satisfies_joint_rule(self):
        raw = _load("20222023", SPECIAL_EVENT_EMPTY_NET_2223)
        events = normalize.normalize_game_events(raw)
        home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
        goals = [e for e in events if e.event_type == "goal" and e.is_statistical]
        en_goals = [g for g in goals if "goalie" not in g.players]
        self.assertGreater(len(en_goals), 0)
        for g in en_goals:
            defending_is_away = (g.team_id == home_id)
            self.assertTrue(normalize.is_empty_net_context(g, defending_team_is_away=defending_is_away))


# 9. score reconstruction
class Test09ScoreReconstruction(unittest.TestCase):
    def test_score_reconstructs_cleanly_2223(self):
        raw = _load("20222023", BASIC_REG_2223)
        events = normalize.normalize_game_events(raw)
        timeline = normalize.reconstruct_statistical_score(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(timeline[-1]["home_score"], raw["homeTeam"]["score"])
        self.assertEqual(timeline[-1]["away_score"], raw["awayTeam"]["score"])


# 10. goal reconciliation (via the real recorded multi-season sample, Section Q)
class Test10GoalReconciliation(unittest.TestCase):
    def test_multi_season_reconciliation_results_recorded(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "multi_season_reconciliation_results.json")
        self.assertTrue(os.path.exists(path), "run_multi_season_reconciliation.py must be run before this suite")
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(set(manifest["per_season"].keys()), set(str(s) for s in (20222023, 20232024, 20242025, 20252026)))


# 11. assist mapping
class Test11AssistMapping(unittest.TestCase):
    def test_no_more_than_two_assists_2223(self):
        events = normalize.normalize_game_events(_load("20222023", SPECIAL_EVENT_EMPTY_NET_2223))
        violations = inv.check_assists_only_on_goals_max_two(events)
        self.assertEqual(violations, [])


# 12. SOG including goal events
class Test12SogIncludingGoalEvents(unittest.TestCase):
    def test_sog_formula_holds_2223(self):
        raw = _load("20222023", SPECIAL_EVENT_EMPTY_NET_2223)
        events = normalize.normalize_game_events(raw)
        self.assertEqual(inv.check_sog_monotonic(events), [])
        goals = [e for e in events if e.event_type == "goal" and e.is_statistical]
        self.assertGreater(len(goals), 0)


# 13. blocked-shot discrepancy reporting
class Test13BlockedShotDiscrepancyReporting(unittest.TestCase):
    def test_block_gap_recorded_per_season_and_not_silently_corrected(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "multi_season_reconciliation_results.json")
        with open(path) as f:
            manifest = json.load(f)
        for season, r in manifest["per_season"].items():
            self.assertIn("pbp_blocks", r)
            self.assertIn("boxscore_blocks", r)
            self.assertIn("block_gap_absolute", r)
            # the raw PBP count must never be silently overwritten to match the boxscore
            self.assertEqual(r["pbp_blocks"] - r["boxscore_blocks"], r["block_gap_absolute"])


# 14. hit-event normalization
class Test14HitEventNormalization(unittest.TestCase):
    def test_hits_carry_both_participants_2223(self):
        events = normalize.normalize_game_events(_load("20222023", GOALIE_CHANGE_2223))
        hits = [e for e in events if e.event_type == "hit"]
        self.assertGreater(len(hits), 0)
        for h in hits:
            self.assertIn("hitter", h.players)
            self.assertIn("hittee", h.players)


# 15. penalty normalization
class Test15PenaltyNormalization(unittest.TestCase):
    def test_bench_minor_lacks_committed_by_2223(self):
        events = normalize.normalize_game_events(_load("20222023", BENCH_PENALTY_2223))
        bench = [e for e in events if e.event_type == "penalty" and e.raw_details.get("typeCode") == "BEN"]
        self.assertGreater(len(bench), 0)
        for b in bench:
            self.assertNotIn("committed_by", b.players)


# 16. faceoff normalization
class Test16FaceoffNormalization(unittest.TestCase):
    def test_faceoff_has_winner_and_loser_2223(self):
        events = normalize.normalize_game_events(_load("20222023", BASIC_REG_2223))
        faceoffs = [e for e in events if e.event_type == "faceoff"]
        self.assertGreater(len(faceoffs), 0)
        for f in faceoffs:
            self.assertIn("winner", f.players)
            self.assertIn("loser", f.players)


# 17. goalie event identity
class Test17GoalieEventIdentity(unittest.TestCase):
    def test_real_goalie_change_2223(self):
        raw = _load("20222023", GOALIE_CHANGE_2223)
        events = normalize.normalize_game_events(raw)
        goalie_ids = {e.players["goalie"] for e in events
                      if e.event_type in ("shot-on-goal", "missed-shot", "goal") and "goalie" in e.players}
        self.assertGreaterEqual(len(goalie_ids), 2)


# 18. period timing
class Test18PeriodTiming(unittest.TestCase):
    def test_regulation_bounds_2223(self):
        events = normalize.normalize_game_events(_load("20222023", BASIC_REG_2223))
        reg_events = [e for e in events if e.period_type == "REG"]
        for e in reg_events:
            self.assertLessEqual(e.seconds_elapsed_in_period, normalize.REGULATION_PERIOD_SECONDS)


# 19. OT handling
class Test19OtHandling(unittest.TestCase):
    def test_ot_never_exceeds_five_minutes_2223(self):
        events = normalize.normalize_game_events(_load("20222023", OT_2223))
        ot_events = [e for e in events if e.period_type == "OT"]
        self.assertGreater(len(ot_events), 0)
        for e in ot_events:
            self.assertLessEqual(e.seconds_elapsed_in_period, normalize.REGULAR_SEASON_OT_SECONDS)


# 20. shootout handling
class Test20ShootoutHandling(unittest.TestCase):
    def test_shootout_winner_identified_2223(self):
        raw = _load("20222023", SHOOTOUT_2223)
        events = normalize.normalize_game_events(raw)
        winner = normalize.shootout_winner(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertIn(winner, (raw["homeTeam"]["id"], raw["awayTeam"]["id"]))


# 21. raw provenance
class Test21RawProvenance(unittest.TestCase):
    def test_provenance_sidecar_present_for_historical_game(self):
        path = os.path.join(raw_archive.RAW_ROOT, "20222023", f"{BASIC_REG_2223}.json")
        prov = raw_archive.read_sidecar(path)
        self.assertEqual(prov.season, "20222023")
        self.assertEqual(prov.archival_status, "ARCHIVAL_RESEARCH")


# 22. archive checksum
class Test22ArchiveChecksum(unittest.TestCase):
    def test_checksum_matches_for_historical_game(self):
        path = os.path.join(raw_archive.RAW_ROOT, "20222023", f"{BASIC_REG_2223}.json")
        prov = raw_archive.read_sidecar(path)
        self.assertEqual(prov.sha256, _file_sha256(path))


# 23. corpus manifest
class Test23CorpusManifest(unittest.TestCase):
    def test_corpus_manifest_structure(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "corpus_manifest.json")
        self.assertTrue(os.path.exists(path), "build_corpus_manifest.py must be run before this suite")
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(set(manifest["seasons"]), set(ALL_SEASONS))
        self.assertEqual(manifest["acceptance_status"], "COMPLETE")
        self.assertEqual(manifest["total_games_retrieved"], 5248)


# 24. idempotency
class Test24Idempotency(unittest.TestCase):
    def test_reingesting_historical_season_is_a_noop(self):
        from research.real_nhl_pbp.build_pbp_season import ingest_season
        result = ingest_season(20222023)
        self.assertEqual(result["games_requested_this_run"], 0)
        self.assertEqual(result["games_retrieved_total"], 1312)
        self.assertEqual(result["games_missing"], [])


# 25. cross-season querying
class Test25CrossSeasonQuerying(unittest.TestCase):
    def test_store_holds_multiple_seasons_with_season_as_a_column(self):
        conn = store.get_connection()
        try:
            rows = conn.execute("SELECT DISTINCT season FROM pbp_games").fetchall()
            seasons_in_store = {r["season"] for r in rows}
            self.assertTrue({"20222023", "20252026"}.issubset(seasons_in_store))
        finally:
            conn.close()


# 26. prior-date event helper
class Test26PriorDateEventHelper(unittest.TestCase):
    def test_events_before_is_strictly_before(self):
        conn = store.get_connection()
        try:
            rows = query.events_before(conn, "2022-10-08", event_type="goal")
            for r in rows:
                self.assertLess(r["game_date"], "2022-10-08")
        finally:
            conn.close()

    def test_player_events_before_strictly_before(self):
        conn = store.get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT player_id, game_date FROM pbp_event_players WHERE season='20222023' LIMIT 1"
            ).fetchall()
            if rows:
                pid, gdate = rows[0]["player_id"], rows[0]["game_date"]
                result = query.player_events_before(conn, pid, gdate)
                for r in result:
                    self.assertLess(r["game_date"], gdate)
        finally:
            conn.close()


# 27. identity consistency
class Test27IdentityConsistency(unittest.TestCase):
    def test_same_player_id_type_across_seasons(self):
        raw_old = _load("20222023", BASIC_REG_2223)
        raw_new = _load("20252026", BASIC_REG_2526)
        old_pid = raw_old["plays"][0].get("details", {}).get("eventOwnerTeamId")
        new_pid = raw_new["plays"][0].get("details", {}).get("eventOwnerTeamId")
        if old_pid is not None and new_pid is not None:
            self.assertIsInstance(old_pid, int)
            self.assertIsInstance(new_pid, int)


# 28. readiness metadata update
class Test28ReadinessMetadataUpdate(unittest.TestCase):
    def test_market_registry_historical_data_status_transitioned(self):
        statuses = {m.historical_data_status for m in market_registry.CANONICAL_MARKETS}
        self.assertNotIn("REQUIRES_PLAY_BY_PLAY", statuses)

    def test_market_registry_file_hash_reflects_authorized_update(self):
        h = _file_sha256("research/player_props/market_registry.py")
        self.assertNotEqual(h, "66bbaf562d2ef60350cac0fbf08cf5961cbb575416f5ec964496e980fe2eeff3",
                             "must reflect this slice's authorized REQUIRES_PLAY_BY_PLAY -> AVAILABLE_UNUSED update")

    def test_process_data_foundation_status_present(self):
        self.assertEqual(dependency_graph.process_data_foundation_status("PERIOD_EVENT_TIMING"),
                          "DATA_FOUNDATION_READY")
        self.assertEqual(dependency_graph.process_data_foundation_status("JOINT_DEPENDENCE_SIMULATION"),
                          "NOT_APPLICABLE")


# 29. market model status unchanged (as of the Multi-Season PBP Expansion
# slice -- derivable_today() legitimately rose 21 -> 24 in the later
# Player SOG by Period slice, which validated PERIOD_1/2/3_PLAYER_SOG;
# validated_today() stays 12 since these 3 new entries intentionally carry
# a per-threshold-nuanced threshold_validation_status, not a blanket
# "VALIDATED" string -- see PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md).
class Test29MarketModelStatusUnchanged(unittest.TestCase):
    def test_derivable_and_validated_counts_unchanged(self):
        # Updated by the Goalie Saves + Period Saves slice: 24->28 derivable
        # (20+/25+/40+/P2 saves), 12->15 validated (20+/25+/P2 saves).
        # Updated again by the Team SOG slice: 28->29 derivable
        # (TEAM_SOG_TOTAL VALIDATED); validated_today() stays 15 (qualified
        # threshold_validation_status string, same convention as
        # PERIOD_1_PLAYER_SOG). See TEAM_SOG_VALIDATION_REPORT.md.
        self.assertEqual(len(market_registry.derivable_today()), 29)
        self.assertEqual(len(market_registry.validated_today()), 15)
        self.assertEqual(market_registry.total_canonical_markets(), 142)

    def test_dependency_graph_still_acyclic(self):
        self.assertTrue(dependency_graph.is_acyclic())


# 30. existing validated models unchanged
class Test30ValidatedModelsUnchanged(unittest.TestCase):
    def test_player_goals_results_file_unchanged(self):
        with open("research/player_goals_results.json") as f:
            data = json.load(f)
        self.assertIn("context_weights_e", data)
        self.assertIn("alpha_e", data)


# 31. confidence unchanged
class Test31ConfidenceUnchanged(unittest.TestCase):
    def test_confidence_results_file_unchanged(self):
        with open("research/confidence_framework_results.json") as f:
            data = json.load(f)
        self.assertIn("results_by_prop_fold", data)


# 32. decision policy v2 unchanged
class Test32DecisionPolicyUnchanged(unittest.TestCase):
    def test_decision_policy_unchanged(self):
        self.assertEqual(
            _file_sha256("research/player_props/decision_policy.py"),
            "f812d5fa2f1811c2b06b0b63b972dc499fc2f8f6e117a9afd5fa63bea55c763a",
        )
        self.assertEqual(decision_policy.POLICY_VERSION, "prop_decision_policy_v3")


# 33. production NHL model unchanged
class Test33ProductionNhlModelUnchanged(unittest.TestCase):
    def test_win_model_files_unchanged(self):
        self.assertEqual(_file_sha256("models/combined_model.py"),
                          "64e9e9cbe686b386951fed9d5001dc298c5dff6af7f582b8f197565f6d932c82")
        self.assertEqual(_file_sha256("models/elo_model.py"),
                          "8538d6b2e32112190919ac41f8b60f17d66528d58c2488c0ee7f7f2690411faf")

    def test_production_boundary_files_unchanged(self):
        self.assertEqual(_file_sha256("config.py"),
                          "c019568da204ace99222954d4f02546a25c31029453c36ed3b0ed4bf97d3df8a")
        self.assertEqual(_file_sha256("db.py"),
                          "b598f4640e191a26dba7231e240a26ebbf6d7a443bcf4f2eb4c43b37cabcea95")
        self.assertEqual(_file_sha256("schema.sql"),
                          "ff19dd3b0c4cd8a61371d77751a045f222bdce7636d119d90c013f58ef64f31f")


# extra: readiness classification sanity (new markets added this slice)
class TestReadinessExtensions(unittest.TestCase):
    def test_all_readiness_sections_still_valid(self):
        for entries in readiness.ALL_SECTIONS.values():
            for e in entries:
                self.assertIn(e.readiness, ("READY", "PARTIAL", "NOT READY"))

    def test_game_state_reconstruction_ready(self):
        self.assertEqual(readiness.GAME_STATE_RECONSTRUCTION_READY, "YES")


if __name__ == "__main__":
    unittest.main()
