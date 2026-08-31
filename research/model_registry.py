"""
Part 1 (Preseason Master Consolidation): MODEL_REGISTRY -- one
machine-readable, comprehensive inventory of every predictive/research
model family in this project, consolidating (never replacing or
duplicating the authority of):

- research/player_props/registry.py::REGISTRY (the pre-existing player-
  prop-level registry -- market support / confidence-validation status)
- research/player_props/market_registry.py (per-market-id sportsbook
  contract fields)
- research/joint_shot_workload/joint_dependence_registry.py
- research/player_context_state/registry.py (PLAYER_CONTEXT_REGISTRY)
- research/context_overlay/registry.py (CONTEXT_OVERLAY_REGISTRY)

MODEL_REGISTRY exists one layer ABOVE those -- it is the place a reader
goes to answer "what is the validation status of family X, at which
thresholds, and is it safe to build on top of it" without reading five
different files. Every status/threshold claim below is sourced from an
already-completed, on-disk validation report (see `validation_report`
per entry) -- nothing here is a new finding; this module only indexes
findings that already exist.

`code_hash` is computed dynamically (never hardcoded) from the entry's
own frozen results file, so a silent drift is caught immediately by
tests/test_preseason_consolidation.py rather than trusted blindly.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sha(relpath: str) -> str | None:
    path = REPO_ROOT / relpath
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@dataclass
class ModelRegistryEntry:
    model_id: str
    display_name: str
    market_family: str
    target: str
    model_version: str
    status: str
    validated_thresholds: list[str] = field(default_factory=list)
    partial_thresholds: list[str] = field(default_factory=list)
    rejected_thresholds: list[str] = field(default_factory=list)
    insufficient_thresholds: list[str] = field(default_factory=list)
    confidence_behavior: str = "CURRENT_FRAMEWORK (v1, unchanged)"
    low_policy: str = "NORMAL"
    pit_status: str = "PIT_SAFE"
    upstream_dependencies: list[str] = field(default_factory=list)
    downstream_consumers: list[str] = field(default_factory=list)
    freeze_manifest: str | None = None
    validation_report: str | None = None
    results_file: str | None = None  # used to compute code_hash dynamically
    operational_status: str = "RESEARCH"

    @property
    def code_hash(self) -> str | None:
        return _sha(self.results_file) if self.results_file else None


# Allowed operational_status values (Part 1):
OPERATIONAL_STATUSES = (
    "PRODUCTION_READY", "SHADOW_VALIDATED", "RESEARCH", "WAITING_FOR_LIVE_DATA",
    "NOT_OPERATIONAL", "REJECTED",
)

MODEL_REGISTRY: list[ModelRegistryEntry] = [
    ModelRegistryEntry(
        model_id="NHL_WIN_MODEL", display_name="NHL Game Win Probability", market_family="MONEYLINE",
        target="team win probability", model_version="production (unchanged this session)",
        status="VALIDATED", confidence_behavior="N/A (team-level model, no player confidence framework)",
        low_policy="NORMAL", pit_status="PIT_SAFE",
        upstream_dependencies=["NHL schedule/results API", "Elo ratings"],
        downstream_consumers=["run_slate.py", "pricing/"],
        validation_report="ELO_REAL_DATA_COMPARISON_REPORT.md",
        results_file="research/elo_comparison_results.json",
        operational_status="PRODUCTION_READY",
    ),
    ModelRegistryEntry(
        model_id="PLAYER_SOG", display_name="Player Shots on Goal", market_family="PLAYER_SOG",
        target="P(SOG >= k)", model_version="Negative Binomial GLM, headline stage",
        status="VALIDATED", validated_thresholds=["2+", "3+", "4+", "5+"],
        # Corrected 2026-08-30 (Preseason Operational Readiness Closure sprint):
        # PLAYER_SOG_FOUNDATION_REPORT.md Section AI, criterion 6, states validation
        # only for "2+/3+/4+/5+, the standard sportsbook SOG lines" -- 1+ was never
        # separately tested (near-universal base rate, not a real book market) and
        # 6+/7+/8+ were never bootstrap-validated (real tail sparsity). The previous
        # entry here (["1+".."6+"]) contradicted its own cited validation_report and
        # research/player_props/market_registry.py's per-market PLAYER_SOG_1PLUS
        # (DERIVABLE_NOT_VALIDATED) / PLAYER_SOG_6PLUS (INSUFFICIENT_TAIL_DATA) entries.
        insufficient_thresholds=["1+ (trivial/near-universal base rate, never separately tested)",
                                  "6+", "7+", "8+"],
        low_policy="NORMAL", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_shot_workload", "research/joint_scoring_dependence",
                               "research/player_context_state", "dashboard/8_Live_SOG_Markets.py"],
        validation_report="PLAYER_SOG_FOUNDATION_REPORT.md", results_file="research/player_sog_results.json",
        operational_status="SHADOW_VALIDATED",
    ),
    ModelRegistryEntry(
        model_id="PLAYER_SOG_PERIOD", display_name="Player SOG by Period", market_family="PLAYER_SOG_PERIOD",
        target="P(period SOG >= k)", model_version="per-period GLM",
        status="PARTIAL", validated_thresholds=["P1_1+", "P1_2+", "P1_3+", "P2_1+", "P2_2+", "P3_1+", "P3_2+"],
        partial_thresholds=["P2_3+", "P3_3+"], low_policy="WATCH_ONLY (PLAYER_SOG_PERIOD_3 only)",
        pit_status="PIT_SAFE", upstream_dependencies=["PLAYER_SOG (uses its own reimplemented marginal)"],
        validation_report="PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md",
        results_file="research/player_sog_period_results.json", operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="GOALS", display_name="Player Goals / Anytime Goal Scorer", market_family="PLAYER_GOALS",
        target="P(goals >= k)", model_version="locked candidate E (hierarchical + context offset)",
        status="VALIDATED", validated_thresholds=["1+"],
        # 3+ added 2026-08-30 (Preseason Operational Readiness Closure sprint,
        # Part 4 cross-registry consistency test): PLAYER_GOALS_VALIDATION_REPORT.md
        # Section D reports a real 3+ event rate of 0.20% ("should not be
        # prioritized"); market_registry.py's PLAYER_GOALS_3PLUS is INSUFFICIENT_DATA.
        # This entry previously omitted 3+ from every threshold bucket.
        insufficient_thresholds=["2+", "3+"],
        low_policy="WATCH_ONLY", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_scoring_dependence", "research/player_context_state",
                               "research/context_overlay"],
        validation_report="PLAYER_GOALS_VALIDATION_REPORT.md", results_file="research/player_goals_results.json",
        operational_status="SHADOW_VALIDATED",
    ),
    ModelRegistryEntry(
        model_id="ASSISTS", display_name="Player Assists", market_family="PLAYER_ASSISTS",
        target="P(assists >= k)", model_version="M4_plus_h2h stage",
        status="VALIDATED", validated_thresholds=["1+", "2+"],
        # Corrected 2026-08-30 (Preseason Operational Readiness Closure sprint):
        # MULTI_PROP_RESEARCH_REPORT.md Section E states "occurs in only 0.6% of
        # real games -- too rare... to evaluate meaningfully" for 3+; market_registry.py
        # independently codes PLAYER_ASSISTS_3PLUS as model_status=INSUFFICIENT_DATA.
        # The previous entry here wrongly included "3+" in validated_thresholds.
        insufficient_thresholds=["3+ (occurs in only 0.6% of real games -- too rare to evaluate meaningfully)"],
        low_policy="WATCH_ONLY", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_scoring_dependence"],
        validation_report="MULTI_PROP_RESEARCH_REPORT.md", results_file="research/player_assists_results.json",
        operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="POINTS", display_name="Player Points", market_family="PLAYER_POINTS",
        target="P(points >= k)", model_version="shrunk empirical baseline (D_empirical_distribution) -- "
                                                 "NOT the GLM",
        status="EMPIRICAL_BASELINE_REMAINS_CHAMPION", validated_thresholds=["1+", "2+"],
        insufficient_thresholds=["3+"], low_policy="WATCH_ONLY", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_scoring_dependence", "research/player_context_state",
                               "research/context_overlay"],
        validation_report="PLAYER_POINTS_REDESIGN_REPORT.md", results_file="research/player_points_results.json",
        operational_status="SHADOW_VALIDATED",
    ),
    ModelRegistryEntry(
        model_id="BLOCKED_SHOTS", display_name="Player Blocked Shots", market_family="PLAYER_BLOCKS",
        target="P(blocks >= k)", model_version="M4_plus_h2h stage",
        status="VALIDATED", validated_thresholds=["1+", "2+", "3+"],
        # 4+ added 2026-08-30 (Preseason Operational Readiness Closure sprint,
        # Part 4 cross-registry consistency test): MULTI_PROP_RESEARCH_REPORT.md
        # Section E states "4+/5+/6+ exist in the data but are too rare for the
        # sportsbook-relevant range"; market_registry.py's PLAYER_BLOCKS_4PLUS is
        # INSUFFICIENT_TAIL_DATA. This entry previously omitted 4+ entirely.
        insufficient_thresholds=["4+"],
        low_policy="NORMAL", pit_status="PIT_SAFE",
        downstream_consumers=["research/player_context_state (rink-recording-effect diagnostic)"],
        validation_report="MULTI_PROP_RESEARCH_REPORT.md", results_file="research/player_blocks_results.json",
        operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="TEAM_SOG", display_name="Team Shots on Goal", market_family="TEAM_SOG",
        target="P(team SOG >= k)", model_version="GLM headline",
        status="VALIDATED", validated_thresholds=["20+", "25+", "30+", "35+"], partial_thresholds=["40+"],
        low_policy="NORMAL", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_shot_workload"],
        validation_report="TEAM_SOG_VALIDATION_REPORT.md", results_file="research/team_sog_results.json",
        operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="GOALIE_SAVES", display_name="Goalie Saves (full-game + period)", market_family="GOALIE_SAVES",
        target="P(saves >= k)", model_version="GLM headline, full-game + period-level",
        status="PARTIAL", validated_thresholds=["20+", "25+", "P2"], partial_thresholds=["30+", "P1", "P3"],
        rejected_thresholds=["35+"], insufficient_thresholds=["40+"],
        low_policy="NORMAL", pit_status="PIT_SAFE",
        downstream_consumers=["research/joint_shot_workload"],
        validation_report="GOALIE_SAVES_VALIDATION_REPORT.md", results_file="research/goalie_saves_results.json",
        operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="TEAM_GOALS_PERIOD", display_name="Team Goals by Period", market_family="TEAM_GOALS_PERIOD",
        target="P(period goals >= k)", model_version="attempted GLM + offset combinations",
        status="ATTEMPTED_NOT_VALIDATED",
        low_policy="NORMAL", pit_status="PIT_SAFE",
        validation_report="TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md",
        results_file="research/team_goals_period_results.json", operational_status="NOT_OPERATIONAL",
    ),
    ModelRegistryEntry(
        model_id="JOINT_SHOT_WORKLOAD", display_name="Joint Shot/Workload Dependence",
        market_family="COMBINATIONS", target="joint P(SOG, Team SOG, Goalie Saves combinations)",
        model_version="data-driven winner per combination (structural / copula / naive)",
        status="VALIDATED",
        validated_thresholds=["SOG+TeamSOG", "TeamSOG+GoalieSaves", "SOG+GoalieSaves", "SOG+TeamSOG+GoalieSaves"],
        low_policy="N/A (combination-level, inherits leg-level policy)", pit_status="PIT_SAFE",
        upstream_dependencies=["PLAYER_SOG", "TEAM_SOG", "GOALIE_SAVES"],
        validation_report="JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md",
        results_file="research/joint_shot_workload_results.json", operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="JOINT_SCORING_DEPENDENCE", display_name="Joint Scoring/Contribution Dependence",
        market_family="COMBINATIONS", target="joint P(SOG, Goals, Assists, Points combinations)",
        model_version="data-driven winner per combination (naive / structural / empirical / copula)",
        status="VALIDATED",
        # Corrected 2026-08-30 (Preseason Operational Readiness Closure sprint):
        # research/joint_shot_workload/joint_dependence_registry.py is the source of
        # truth for exact validated_combinations per pair. The previous entry here
        # both mislabeled 2 structurally-redundant "triple" combinations as validated
        # (joint_dependence_registry.py has status="RESEARCH", validated_combinations=[]
        # for both -- they reduce exactly to an already-validated pair via the logical
        # Goal=>Point / Assist=>Point identity, never independently fit) and omitted
        # GOAL_POINT, an actually-validated combination.
        validated_thresholds=["SOG2+GOAL", "SOG3+GOAL", "SOG4+GOAL", "GOAL_POINT", "ASSIST_POINT",
                               "SOG3+POINT", "SOG4+POINT", "SOG2+ASSIST", "SOG3+ASSIST"],
        insufficient_thresholds=[
            "SOG3+GOAL+POINT (triple) -- structurally redundant reduction of SOG3+GOAL "
            "(Goal=>Point logical identity), never independently validated",
            "SOG3+ASSIST+POINT (triple) -- structurally redundant reduction of SOG3+ASSIST "
            "(Assist=>Point logical identity), never independently validated",
        ],
        low_policy="N/A (combination-level, inherits leg-level policy)", pit_status="PIT_SAFE",
        upstream_dependencies=["PLAYER_SOG", "GOALS", "ASSISTS", "POINTS"],
        validation_report="JOINT_SCORING_DEPENDENCE_VALIDATION_REPORT.md",
        results_file="research/joint_scoring_dependence_results.json", operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="PLAYER_CONTEXT_STATE", display_name="Player Context State (Cold/Hot/TOI-decline)",
        market_family="CONTEXT_SIGNAL", target="residual (actual - frozen P) by context state",
        model_version="v1, TUNING-fit percentile cutoffs", status="VALIDATION_COMPLETE (MIXED, see registry)",
        low_policy="N/A (research signal, not a priced market)", pit_status="PIT_SAFE",
        upstream_dependencies=["PLAYER_SOG", "GOALS", "ASSISTS", "POINTS", "BLOCKED_SHOTS"],
        downstream_consumers=["research/context_overlay"],
        validation_report="PLAYER_CONTEXT_STATE_VALIDATION_REPORT.md",
        results_file="research/player_context_state_results.json", operational_status="RESEARCH",
    ),
    ModelRegistryEntry(
        model_id="CONTEXT_OVERLAY_GOALS", display_name="Context Overlay: Goals 1+ (COLD_AND_TOI_DECLINE)",
        market_family="PLAYER_GOALS", target="context-adjusted P(goals >= 1)",
        model_version="B_FIXED_LOGIT_OFFSET, offset=-0.180", status="VALIDATED_OVERLAY",
        validated_thresholds=["1+ (COLD_AND_TOI_DECLINE state only)"],
        low_policy="WATCH_ONLY (inherited from GOALS, overlay cannot override)", pit_status="PIT_SAFE",
        upstream_dependencies=["GOALS", "PLAYER_CONTEXT_STATE"],
        validation_report="CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md",
        results_file="research/context_overlay_results.json", operational_status="SHADOW_VALIDATED",
    ),
    ModelRegistryEntry(
        model_id="CONTEXT_OVERLAY_POINTS", display_name="Context Overlay: Points 1+ (COLD_AND_TOI_DECLINE)",
        market_family="PLAYER_POINTS", target="context-adjusted P(points >= 1)",
        model_version="D_BAYESIAN_CONTEXT_BLEND, shift=-0.0415", status="VALIDATED_OVERLAY",
        validated_thresholds=["1+ (COLD_AND_TOI_DECLINE state only)"],
        low_policy="WATCH_ONLY (inherited from POINTS, overlay cannot override)", pit_status="PIT_SAFE",
        upstream_dependencies=["POINTS", "PLAYER_CONTEXT_STATE"],
        validation_report="CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md",
        results_file="research/context_overlay_results.json", operational_status="SHADOW_VALIDATED",
    ),
    ModelRegistryEntry(
        model_id="PLAYER_SOG_PP_ROLE_OVERLAY", display_name="SOG Special-Teams PP-Role Overlay (shadow)",
        market_family="PLAYER_SOG", target="role-adjusted P(SOG >= k)",
        model_version="architecture C (absolute role + direction-separated transition, "
                       "certainty-shrunk)", status="PARTIAL",
        validated_thresholds=["1+", "2+", "3+"], insufficient_thresholds=["4+", "5+", "6+"],
        low_policy="SHADOW ONLY -- never affects a real probability, price, or bet decision",
        pit_status="PIT_SAFE",
        upstream_dependencies=["PLAYER_SOG", "operational/special_teams_history_store.py "
                                "(live NHL TOI reports + one-time MoneyPuck archival backfill)",
                                "operational/special_teams_roles_live.py"],
        downstream_consumers=["operational/record_sog_shadow_observation.py (prospective ledger only)",
                               "dashboard/pages/25_Player_Intelligence.py (PP Role expander)"],
        validation_report="SPECIAL_TEAMS_ROLE_OVERLAY_VALIDATION_REPORT.md",
        results_file="research/special_teams_role_overlay_sog_results.json",
        operational_status="SHADOW_VALIDATED",
    ),
]


def get(model_id: str) -> ModelRegistryEntry | None:
    return next((e for e in MODEL_REGISTRY if e.model_id == model_id), None)


def by_operational_status(status: str) -> list[ModelRegistryEntry]:
    return [e for e in MODEL_REGISTRY if e.operational_status == status]
