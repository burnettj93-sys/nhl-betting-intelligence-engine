"""
Part 50: a research-level JOINT_DEPENDENCE_REGISTRY -- explicitly NOT a
sportsbook market registry (research/player_props/market_registry.py is
untouched by this slice). Tracks the validation status of each
combination FAMILY (not individual sportsbook markets, which do not yet
exist for any of these combinations) so that future work referencing
"can Player SOG + Team SOG be priced together" has one real, disclosed
source of truth rather than scattered notes.

Populated by research/run_joint_shot_workload_model.py's own frozen
results (research/joint_shot_workload_results.json) -- this module holds
only the STRUCTURE (dataclass, entries, statuses); the entries below are
filled in with the slice's real, frozen findings once evaluation completes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JointDependenceEntry:
    combination_id: str
    status: str                          # VALIDATED / PARTIAL / RESEARCH / INSUFFICIENT_DATA
    marginal_model_versions: dict[str, str]
    joint_model_version: str
    validated_combinations: list[str]    # e.g. ["PLAYER3_TEAM30"]
    known_exclusions: list[str]
    confidence_status: str
    notes: str


JOINT_DEPENDENCE_REGISTRY: dict[str, JointDependenceEntry] = {
    "PLAYER_SOG__TEAM_SOG": JointDependenceEntry(
        combination_id="PLAYER_SOG__TEAM_SOG",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "team_sog": "winner=B_poisson_direct (research/team_sog_results.json)",
        },
        joint_model_version="joint_shot_workload_v1 / D_structural_factorization "
                             "(Poisson team SOG x Binomial player-share allocation)",
        validated_combinations=["PLAYER2_TEAM25", "PLAYER3_TEAM30", "PLAYER4_TEAM30"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG (combination confidence)",
        notes="Structural factorization beats naive independence with bootstrap evidence "
              "clearing this project's bar in both eval seasons. See "
              "JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md Sections E-G, Q-Y.",
    ),
    "TEAM_SOG__GOALIE_SAVES": JointDependenceEntry(
        combination_id="TEAM_SOG__GOALIE_SAVES",
        status="VALIDATED",
        marginal_model_versions={
            "team_sog": "winner=B_poisson_direct (research/team_sog_results.json)",
            "goalie_saves": "full_game_winner=E_hybrid_offset (research/goalie_saves_results.json)",
        },
        joint_model_version="joint_shot_workload_v1 / D_structural_factorization "
                             "(Team SOG minus empty-net-adjusted shots-faced, "
                             "Saves ~ Binomial(shots_faced, league_save_pct))",
        validated_combinations=["TEAM25_GOALIE20", "TEAM30_GOALIE25"],
        known_exclusions=["GOALIE_SAVES 35+/40+ thresholds -- excluded from this slice's headline "
                           "matrix per Part 6's explicit instruction (rejected/insufficient marginal)"],
        confidence_status="RESEARCH -- see report Section AG",
        notes="Near-mechanical dependence (Team SOG minus goals allowed minus empty-net shots). "
              "See JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md Sections H-K, Q-Y.",
    ),
    "PLAYER_SOG__GOALIE_SAVES": JointDependenceEntry(
        combination_id="PLAYER_SOG__GOALIE_SAVES",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "goalie_saves": "full_game_winner=E_hybrid_offset (research/goalie_saves_results.json)",
        },
        joint_model_version="joint_shot_workload_v1 / D_structural_factorization "
                             "(mediated through Team SOG -- Part 15's own description)",
        validated_combinations=["PLAYER3_GOALIE20", "PLAYER4_GOALIE25"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="Mediated dependence -- real, and (after the Frechet-clipping coherence fix, see "
              "report Section AB) beats naive independence with bootstrap evidence clearing this "
              "project's bar in both eval seasons for both tested thresholds, though the point-"
              "estimate margin over naive is the smallest of the three pairs (mean dependence lift "
              "~1.07-1.25x vs ~1.15-1.75x for the other two pairs) since it flows entirely through "
              "the shared Team SOG channel with no direct structural link. See "
              "JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md Sections L, Q-Y.",
    ),
    "PLAYER_SOG__TEAM_SOG__GOALIE_SAVES": JointDependenceEntry(
        combination_id="PLAYER_SOG__TEAM_SOG__GOALIE_SAVES",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "team_sog": "winner=B_poisson_direct (research/team_sog_results.json)",
            "goalie_saves": "full_game_winner=E_hybrid_offset (research/goalie_saves_results.json)",
        },
        joint_model_version="joint_shot_workload_v1 / D_structural_factorization (three-way)",
        validated_combinations=["PLAYER3_TEAM30_GOALIE20"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="Three-way structural factorization beats naive independence with bootstrap "
              "frac_improved=1.0 in both eval seasons, real adequate support (5,101 / 4,848 "
              "positive events), and materially better calibration in the low-probability bands "
              "where naive independence badly underestimates the true joint rate. See "
              "JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md Section M/V/W.",
    ),

    # ------------------------------------------------------------------
    # Extended by the Joint Scoring/Contribution Dependence slice (Part 46):
    # Player SOG + Goals + Assists + Points. Two families are EXACT LOGICAL
    # IDENTITIES (Goal->Point, Assist->Point), never fitted; the remaining
    # families use a real winner-take-all comparison among naive/shrunk-
    # empirical/conditional-empirical/structural-conversion/Gaussian-copula
    # candidates -- the winner is NOT always the structural one (see notes).
    # ------------------------------------------------------------------
    "PLAYER_SOG__PLAYER_GOAL": JointDependenceEntry(
        combination_id="PLAYER_SOG__PLAYER_GOAL",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "goals": "locked candidate E (research/player_goals_results.json)",
        },
        joint_model_version="joint_scoring_dependence_v1 / E_gaussian_copula (winner over the "
                             "structural shot-conversion candidate at all 3 thresholds tested)",
        validated_combinations=["SOG2_GOAL", "SOG3_GOAL", "SOG4_GOAL"],
        known_exclusions=["SOG5+_GOAL not tested this slice (Part 8's own instruction: only if "
                           "individually validated)"],
        confidence_status="RESEARCH -- see JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Section AC",
        notes="Real, mechanically-justified dependence (a goal converts one of the player's own "
              "shots) -- both the structural shot-conversion model AND the Gaussian copula beat "
              "naive independence with bootstrap frac_improved=1.0 in both eval seasons at all 3 "
              "thresholds; the copula wins by a narrow margin. See "
              "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Sections D, N, Q-U.",
    ),
    "PLAYER_GOAL__PLAYER_POINT": JointDependenceEntry(
        combination_id="PLAYER_GOAL__PLAYER_POINT",
        status="VALIDATED",
        marginal_model_versions={
            "goals": "locked candidate E (research/player_goals_results.json)",
            "points": "D_empirical_distribution (shrunk empirical baseline, research/"
                      "player_points_results.json) -- NOT the GLM, per Part 4",
        },
        joint_model_version="joint_scoring_dependence_v1 / EXACT LOGICAL IDENTITY -- "
                             "P(Goal>=1 AND Point>=1) = P(Goal>=1), Frechet-clipped against the "
                             "frozen Point marginal (no coherence violations found for this pair)",
        validated_combinations=["GOAL_POINT"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="STRUCTURAL CONTROL CASE (Part 12) -- no fitted dependence model. Naive independence "
              "understates the true joint rate by a real, large margin (dependence lift ~2.33x). "
              "See JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Section E.",
    ),
    "PLAYER_ASSIST__PLAYER_POINT": JointDependenceEntry(
        combination_id="PLAYER_ASSIST__PLAYER_POINT",
        status="VALIDATED",
        marginal_model_versions={
            "assists": "M4_plus_h2h (research/player_assists_results.json)",
            "points": "D_empirical_distribution (shrunk empirical baseline) -- NOT the GLM, per Part 4",
        },
        joint_model_version="joint_scoring_dependence_v1 / EXACT LOGICAL IDENTITY -- "
                             "P(Assist>=1 AND Point>=1) = P(Assist>=1), Frechet-CLIPPED against the "
                             "frozen Point marginal wherever the two frozen marginals themselves "
                             "disagree (real, quantified: ~8% of real rows show the frozen Assist "
                             "marginal exceeding the frozen Point marginal, a genuine independently-"
                             "fit-model incoherence -- see report Section L)",
        validated_combinations=["ASSIST_POINT"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="STRUCTURAL CONTROL CASE (Part 13), with a real, disclosed marginal-coherence "
              "correction applied (not a silent edit to either raw marginal). Dependence lift "
              "~2.47-2.48x. See JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Sections F, L.",
    ),
    "PLAYER_SOG__PLAYER_POINT": JointDependenceEntry(
        combination_id="PLAYER_SOG__PLAYER_POINT",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "points": "D_empirical_distribution (shrunk empirical baseline) -- NOT the GLM, per Part 4",
        },
        joint_model_version="joint_scoring_dependence_v1 / E_gaussian_copula (winner -- the "
                             "structural shot-conversion candidate is a WEAKER fit here since "
                             "points are not generated purely from the player's own shots)",
        validated_combinations=["SOG3_POINT", "SOG4_POINT"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="Real dependence, bootstrap frac_improved=1.0 both eval seasons at both thresholds, "
              "but the WINNING architecture is the copula, not the structural shot-conversion model "
              "-- a real, honest finding (Part 14), not assumed. See "
              "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Sections G, Q-U.",
    ),
    "PLAYER_SOG__PLAYER_ASSIST": JointDependenceEntry(
        combination_id="PLAYER_SOG__PLAYER_ASSIST",
        status="VALIDATED",
        marginal_model_versions={
            "player_sog": "headline_stage=M4_plus_h2h (research/player_sog_results.json)",
            "assists": "M4_plus_h2h (research/player_assists_results.json)",
        },
        joint_model_version="joint_scoring_dependence_v1 / E_gaussian_copula (winner -- the "
                             "structural shot-conversion candidate actually LOSES to naive "
                             "independence for this pair, a real negative finding)",
        validated_combinations=["SOG2_ASSIST", "SOG3_ASSIST"],
        known_exclusions=[],
        confidence_status="RESEARCH -- see report Section AG",
        notes="Weak but real positive dependence (raw corr 0.14, copula rho 0.046). The structural "
              "shot-conversion architecture is NOT mechanically appropriate here (an assist is not "
              "generated from the passer's own shots) and its Brier is WORSE than naive on both "
              "thresholds/seasons -- the copula still narrowly beats naive (frac_improved=1.0 both "
              "seasons). Direction was not assumed (Part 15) -- tested, found positive and role-"
              "mediated (see raw vs. copula-rho gap). See "
              "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Sections H, Q-U.",
    ),
    "PLAYER_SOG__PLAYER_GOAL__PLAYER_POINT": JointDependenceEntry(
        combination_id="PLAYER_SOG__PLAYER_GOAL__PLAYER_POINT",
        status="RESEARCH",
        marginal_model_versions={},
        joint_model_version="N/A -- STRUCTURALLY REDUNDANT (Part 30/31)",
        validated_combinations=[],
        known_exclusions=["Fully reduces to PLAYER_SOG__PLAYER_GOAL: Goal>=1 implies Point>=1, so "
                           "the POINT leg adds zero information once the GOAL leg is present."],
        confidence_status="N/A",
        notes="Automatically detected as redundant, not separately scored or double-counted. See "
              "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Section AE.",
    ),
    "PLAYER_SOG__PLAYER_ASSIST__PLAYER_POINT": JointDependenceEntry(
        combination_id="PLAYER_SOG__PLAYER_ASSIST__PLAYER_POINT",
        status="RESEARCH",
        marginal_model_versions={},
        joint_model_version="N/A -- STRUCTURALLY REDUNDANT (Part 30/31)",
        validated_combinations=[],
        known_exclusions=["Fully reduces to PLAYER_SOG__PLAYER_ASSIST: Assist>=1 implies Point>=1, "
                           "so the POINT leg adds zero information once the ASSIST leg is present."],
        confidence_status="N/A",
        notes="Automatically detected as redundant, not separately scored or double-counted. See "
              "JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md Section AE.",
    ),
}


def get(combination_id: str) -> JointDependenceEntry | None:
    return JOINT_DEPENDENCE_REGISTRY.get(combination_id)
