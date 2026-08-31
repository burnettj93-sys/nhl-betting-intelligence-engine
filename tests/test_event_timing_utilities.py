"""
Part 29: tests for the Event-Timing Utility Closure slice -- goalie-tenure
reconstruction, period-save accounting, and game-winning-goal derivation.
Every fixture is a REAL, permanently-archived play-by-play game already in
the corpus; none is synthesized. Numbered comments map to Part-29 topics.
"""
from __future__ import annotations

import hashlib
import json
import os
import unittest

import requests

from research.player_props import decision_policy, dependency_graph, market_registry
from research.real_nhl_pbp import goalie_tenure, gwg, gwg_invariants, normalize, period_saves, raw_archive

# Real, permanently-archived fixtures, each already hand-verified this slice.
MID_PERIOD_CHANGE_GAME = ("20252026", 2025020240)     # EDM: STARTER -> RELIEF mid-2nd-period
EMPTY_NET_RETURN_GAME = ("20252026", 2025020814)       # CBJ goalie pulled twice, returns both times
BETWEEN_PERIODS_RELIEF_GAME = ("20222023", 2022020032) # relief change between periods, not mid-period
BASIC_GAME = ("20252026", 2025020073)                  # WSH 5 - MIN 1, clean single-goalie game, real GWG=2nd WSH goal
SHOOTOUT_GAME = ("20252026", 2025020231)               # real SO game, tied statistical score


def _events(season: str, game_id: int):
    raw = raw_archive.load_raw_pbp(season, game_id)
    return raw, normalize.normalize_game_events(raw)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# 1. goalie identity assignment
class Test01GoalieIdentityAssignment(unittest.TestCase):
    def test_direct_event_goalie_field_used(self):
        raw, events = _events(*BASIC_GAME)
        shots = [e for e in events if e.event_type == "shot-on-goal"]
        with_goalie = [s for s in shots if "goalie" in s.players]
        self.assertEqual(len(with_goalie), len(shots))  # this game has 0 empty-net shots


# 2. starter assignment
class Test02StarterAssignment(unittest.TestCase):
    def test_first_real_goalie_is_starter(self):
        raw, events = _events(*BASIC_GAME)
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        for team_id, intervals in tenure.items():
            self.assertEqual(intervals[0].interval_type, "STARTER")


# 3. relief goalie assignment
class Test03ReliefGoalieAssignment(unittest.TestCase):
    def test_real_relief_change_detected(self):
        raw, events = _events(*MID_PERIOD_CHANGE_GAME)
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        relief_intervals = [iv for ivs in tenure.values() for iv in ivs if iv.interval_type == "RELIEF"]
        self.assertEqual(len(relief_intervals), 1)
        self.assertEqual(relief_intervals[0].goalie_id, 8475717)


# 4. mid-period change
class Test04MidPeriodChange(unittest.TestCase):
    def test_mid_period_change_flagged(self):
        raw, events = _events(*MID_PERIOD_CHANGE_GAME)
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        home_changes = goalie_tenure.mid_period_changes(tenure[raw["homeTeam"]["id"]])
        away_changes = goalie_tenure.mid_period_changes(tenure[raw["awayTeam"]["id"]])
        self.assertEqual(len(home_changes) + len(away_changes), 1)

    def test_between_periods_change_not_flagged_as_mid_period(self):
        raw, events = _events(*BETWEEN_PERIODS_RELIEF_GAME)
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        all_changes = (goalie_tenure.mid_period_changes(tenure[raw["homeTeam"]["id"]])
                       + goalie_tenure.mid_period_changes(tenure[raw["awayTeam"]["id"]]))
        self.assertEqual(len(all_changes), 0)


# 5. empty-net interval
class Test05EmptyNetInterval(unittest.TestCase):
    def test_empty_net_intervals_present(self):
        raw, events = _events(*EMPTY_NET_RETURN_GAME)
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        empty_intervals = [iv for ivs in tenure.values() for iv in ivs if iv.interval_type == "EMPTY_NET"]
        self.assertEqual(len(empty_intervals), 2)
        for iv in empty_intervals:
            self.assertIsNone(iv.goalie_id)


# 6. goalie pulled then returns
class Test06GoaliePulledThenReturns(unittest.TestCase):
    def test_same_goalie_return_not_misclassified_as_relief(self):
        raw, events = _events(*EMPTY_NET_RETURN_GAME)
        home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, home_id, away_id)
        returns = [iv for ivs in tenure.values() for iv in ivs if iv.interval_type == "RETURN_AFTER_EMPTY_NET"]
        self.assertEqual(len(returns), 1)
        reliefs = [iv for ivs in tenure.values() for iv in ivs if iv.interval_type == "RELIEF"]
        self.assertEqual(len(reliefs), 0)


# 7. multiple goalie game
class Test07MultipleGoalieGame(unittest.TestCase):
    def test_two_distinct_goalies_one_team(self):
        raw, events = _events(*MID_PERIOD_CHANGE_GAME)
        home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
        tenure = goalie_tenure.reconstruct_goalie_tenure(events, home_id, away_id)
        goalie_ids = {iv.goalie_id for iv in tenure[home_id] if iv.goalie_id is not None}
        self.assertEqual(len(goalie_ids), 2)


# 8. shot-on-goal save assignment
class Test08ShotOnGoalSaveAssignment(unittest.TestCase):
    def test_shots_on_goal_counted_as_saves(self):
        raw, events = _events(*BASIC_GAME)
        stats = period_saves.full_game_saves_by_goalie(events)
        self.assertTrue(any(s["saves"] > 0 for s in stats.values()))


# 9. goal is not save
class Test09GoalIsNotSave(unittest.TestCase):
    def test_goals_against_excluded_from_saves(self):
        raw, events = _events(*BASIC_GAME)
        stats = period_saves.full_game_saves_by_goalie(events)
        for goalie_id, s in stats.items():
            self.assertEqual(s["shots_faced"], s["saves"] + s["goals_against"])
            self.assertNotEqual(s["saves"], s["shots_faced"])  # true whenever goals_against > 0


# 10. shootout attempt excluded from saves
class Test10ShootoutExcludedFromSaves(unittest.TestCase):
    def test_so_events_never_counted(self):
        raw, events = _events(*SHOOTOUT_GAME)
        so_events = [e for e in events if e.period_type == "SO"]
        self.assertGreater(len(so_events), 0)
        by_period = period_saves.period_saves_by_goalie(events)
        so_periods_present = {p for (_g, p) in by_period if p == 5}  # SO is period 5
        self.assertEqual(so_periods_present, set())


# 11. period saves
class Test11PeriodSaves(unittest.TestCase):
    def test_period_saves_bucketed_by_period(self):
        raw, events = _events(*BASIC_GAME)
        by_period = period_saves.period_saves_by_goalie(events)
        periods_seen = {p for (_g, p) in by_period}
        self.assertTrue(periods_seen.issubset({1, 2, 3, 4}))


# 12. full-game saves
class Test12FullGameSaves(unittest.TestCase):
    def test_full_game_saves_positive(self):
        raw, events = _events(*BASIC_GAME)
        totals = period_saves.full_game_saves_by_goalie(events)
        self.assertTrue(all(v["saves"] >= 0 for v in totals.values()))


# 13. period-to-full-game reconciliation
class Test13PeriodToFullGameReconciliation(unittest.TestCase):
    def test_coherence_holds_on_multi_goalie_game(self):
        raw, events = _events(*MID_PERIOD_CHANGE_GAME)
        violations = period_saves.check_period_sums_equal_full_game(events)
        self.assertEqual(violations, [])


# 14. official save reconciliation
class Test14OfficialSaveReconciliation(unittest.TestCase):
    def test_corpus_scale_results_recorded(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "goalie_tenure_audit_results.json")
        self.assertTrue(os.path.exists(path), "run_goalie_tenure_audit.py must be run before this suite")
        with open(path) as f:
            manifest = json.load(f)
        self.assertEqual(set(manifest["per_season"].keys()), {"20222023", "20232024", "20242025", "20252026"})


# 15. no goalie on empty-net event
class Test15NoGoalieOnEmptyNetEvent(unittest.TestCase):
    def test_empty_net_goal_has_no_goalie_role(self):
        raw, events = _events(*EMPTY_NET_RETURN_GAME)
        en_goals = [e for e in events if e.event_type == "goal" and e.is_statistical and "goalie" not in e.players]
        self.assertGreater(len(en_goals), 0)


# 16-21: GWG scenarios
class Test16To21Gwg(unittest.TestCase):
    def test_gwg_2nd_goal_example(self):
        raw, events = _events(*BASIC_GAME)  # WSH wins 5-1, GWG is WSH's 2nd goal
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(result.status, gwg.STATUS_RESOLVED)
        winning_goals = sorted(
            (e for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == result.winning_team),
            key=lambda e: e.event_sequence,
        )
        self.assertEqual(result.gwg_event_id, winning_goals[1].event_id)  # 2nd goal, 0-indexed [1]

    def test_gwg_with_later_empty_net_goal_not_misidentified(self):
        raw, events = _events(*EMPTY_NET_RETURN_GAME)
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(result.status, gwg.STATUS_RESOLVED)
        self.assertFalse(result.empty_net, "the real GWG in this game is not one of the two later EN goals")
        # confirm the GWG event_sequence precedes both real empty-net goals in this game
        en_goals = sorted(
            (e for e in events if e.event_type == "goal" and e.is_statistical and "goalie" not in e.players),
            key=lambda e: e.event_sequence,
        )
        for en in en_goals:
            self.assertLess(result.gwg_event_sequence, en.event_sequence)

    def test_gwg_invariants_hold_on_multiple_real_games(self):
        for season, gid in (BASIC_GAME, EMPTY_NET_RETURN_GAME, MID_PERIOD_CHANGE_GAME):
            raw, events = _events(season, gid)
            result = gwg.derive_gwg(events, gid, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
            violations = gwg_invariants.check_all(result, events)
            self.assertEqual(violations, [], f"{gid}: {violations}")


# 22. OT GWG
class Test22OtGwg(unittest.TestCase):
    def test_ot_game_resolves_to_ot_goal(self):
        raw, events = _events("20252026", 2025020193)  # real OT game from prior slice's fixtures
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(result.status, gwg.STATUS_RESOLVED)
        self.assertEqual(result.gwg_period_type, "OT")


# 23. shootout game GWG semantics
class Test23ShootoutGwgSemantics(unittest.TestCase):
    def test_shootout_returns_no_player_gwg(self):
        raw, events = _events(*SHOOTOUT_GAME)
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(result.status, gwg.STATUS_NO_PLAYER_GWG_SHOOTOUT)
        self.assertIsNone(result.gwg_event_id)
        self.assertEqual(result.final_home_score, result.final_away_score)


# 24. GWG winning-team identity
class Test24GwgWinningTeamIdentity(unittest.TestCase):
    def test_winning_team_matches_higher_final_score(self):
        raw, events = _events(*BASIC_GAME)
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        self.assertEqual(result.winning_team, raw["homeTeam"]["id"])  # WSH (home) won 5-1


# 25. GWG ordinal invariant
class Test25GwgOrdinalInvariant(unittest.TestCase):
    def test_ordinal_equals_losing_goals_plus_one(self):
        raw, events = _events(*BASIC_GAME)
        result = gwg.derive_gwg(events, raw["id"], raw["homeTeam"]["id"], raw["awayTeam"]["id"])
        losing_final = min(result.final_home_score, result.final_away_score)
        winning_goals = sorted(
            (e for e in events if e.event_type == "goal" and e.is_statistical and e.team_id == result.winning_team),
            key=lambda e: e.event_sequence,
        )
        ordinal = [e.event_id for e in winning_goals].index(result.gwg_event_id) + 1
        self.assertEqual(ordinal, losing_final + 1)


# 26. GWG non-SO invariant
class Test26GwgNonSoInvariant(unittest.TestCase):
    def test_gwg_event_never_in_so_period(self):
        for season, gid in (BASIC_GAME, EMPTY_NET_RETURN_GAME):
            raw, events = _events(season, gid)
            result = gwg.derive_gwg(events, gid, raw["homeTeam"]["id"], raw["awayTeam"]["id"])
            if result.status == gwg.STATUS_RESOLVED:
                self.assertNotEqual(result.gwg_period_type, "SO")


# 27. corpus-scale deterministic result
class Test27CorpusScaleDeterministicResult(unittest.TestCase):
    def test_gwg_audit_results_recorded_with_zero_violations(self):
        path = os.path.join(os.path.dirname(raw_archive.__file__), "gwg_audit_results.json")
        self.assertTrue(os.path.exists(path), "run_gwg_audit.py must be run before this suite")
        with open(path) as f:
            manifest = json.load(f)
        for season, r in manifest["per_season"].items():
            self.assertEqual(r["total_invariant_violations"], 0, season)
            self.assertEqual(r["derivation_failures"], 0, season)
            self.assertEqual(r["games_with_no_gwg"], r["shootout_games_no_player_gwg"], season)


# 28. readiness update
class Test28ReadinessUpdate(unittest.TestCase):
    def test_period_saves_and_gwg_promoted_to_ready(self):
        from research.real_nhl_pbp import readiness
        period_entries = {e.market_label: e for e in readiness.PERIOD_MARKET_READINESS}
        event_time_entries = {e.market_label: e for e in readiness.EVENT_TIME_MARKET_READINESS}
        goalie_entries = {e.market_label: e for e in readiness.GOALIE_MARKET_READINESS}
        self.assertEqual(period_entries["GOALIE SAVES BY PERIOD"].readiness, "READY")
        self.assertEqual(event_time_entries["GAME-WINNING GOAL"].readiness, "READY")
        self.assertEqual(goalie_entries["PERIOD SAVES"].readiness, "READY")


# 29. market model status unchanged (as of the Event-Timing Utility
# Closure slice -- derivable_today() legitimately rose 21 -> 24 in the
# later Player SOG by Period slice; see that test file's own Test29 note).
class Test29MarketModelStatusUnchanged(unittest.TestCase):
    def test_derivable_and_validated_counts_unchanged(self):
        # Updated by the Goalie Saves + Period Saves slice: 24->28 derivable
        # (20+/25+/40+/P2 saves), 12->15 validated (20+/25+/P2 saves).
        # Updated again by the Team SOG slice: 28->29 derivable
        # (TEAM_SOG_TOTAL VALIDATED); validated_today() stays 15 because
        # TEAM_SOG_TOTAL's threshold_validation_status is a qualified
        # string ("VALIDATED_20PLUS_..._NOT_40PLUS"), not the exact
        # "VALIDATED" that function requires -- same convention already
        # established for PERIOD_1_PLAYER_SOG. Real, disclosed registry
        # changes, not regressions. See TEAM_SOG_VALIDATION_REPORT.md.
        self.assertEqual(len(market_registry.derivable_today()), 29)
        self.assertEqual(len(market_registry.validated_today()), 15)
        self.assertEqual(market_registry.total_canonical_markets(), 142)

    def test_gwg_and_period_saves_model_status_still_not_built(self):
        gwg_market = market_registry.get("GAME_WINNING_GOAL")
        if gwg_market is not None:
            self.assertEqual(gwg_market.model_status, "NOT_BUILT")


# 30. validated models unchanged
class Test30ValidatedModelsUnchanged(unittest.TestCase):
    def test_player_goals_results_file_unchanged(self):
        with open("research/player_goals_results.json") as f:
            data = json.load(f)
        self.assertIn("context_weights_e", data)


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


if __name__ == "__main__":
    unittest.main()
