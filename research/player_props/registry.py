"""
Section W: the central player-prop model registry. One entry per prop
family, so no dashboard page or report can imply every prop is equally
mature. Every field here is either a fact checked directly this slice
(market support, data availability) or a conclusion from a real,
completed research report (model_status) -- never a guess. See
MULTI_PROP_RESEARCH_REPORT.md for the full evidence behind each entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field


CONFIDENCE_FRAMEWORK_VERSION = "v1"  # unchanged this slice -- a v2 redesign was attempted and evaluated
                                       # (research/run_confidence_diagnostics.py) but did NOT beat v1's
                                       # HIGH>=MEDIUM>=LOW ordering consistency on any of 3 forward-tested
                                       # candidates, so v1 was kept. See CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md.


@dataclass
class PropRegistryEntry:
    market_type: str
    model_status: str          # VALIDATED / PROMISING / RESEARCH / REJECTED / BLOCKED / UNSUPPORTED_MARKET
    live_market_support: str   # CONNECTED / WAITING_FOR_MARKET / NOT_CURRENTLY_AVAILABLE
    odds_api_market_key: str | None
    summary: str
    report: str | None = None
    correlation_data_preserved: bool = False
    confidence_framework_version: str = CONFIDENCE_FRAMEWORK_VERSION
    # VALIDATED (HIGH/MEDIUM/LOW ordering holds, LOW is non-negative-skill or only marginally so) /
    # CONDITIONAL (ordering holds but LOW shows real negative skill -- recommend WAIT/WATCH gating for
    # LOW-confidence predictions specifically, not a framework change) / NOT_YET_ASSESSED
    confidence_validation_status: str = "NOT_YET_ASSESSED"
    # NORMAL (LOW confidence follows whatever the prop's own pricing math decides) /
    # WATCH_ONLY (LOW-confidence predictions for this market are capped at WATCH and can never
    # resolve to BET, per research/player_props/decision_policy.py -- see Part 14/PROP_LOW_CONFIDENCE_CEILING)
    low_confidence_bet_eligibility: str = "NORMAL"


REGISTRY: list[PropRegistryEntry] = [
    PropRegistryEntry(
        market_type="SOG", model_status="VALIDATED", live_market_support="WAITING_FOR_MARKET",
        odds_api_market_key="player_shots_on_goal",
        summary="Negative Binomial beats every naive baseline; H2H and TOI/role add real value; "
                "recent form and opponent context do not.",
        report="PLAYER_SOG_FOUNDATION_REPORT.md",
        confidence_validation_status="VALIDATED",
    ),
    PropRegistryEntry(
        market_type="BLOCKED_SHOTS", model_status="VALIDATED", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key=None,
        summary="Beats both naive baselines (100% bootstrap credibility). TOI/role, opponent shot-"
                "attempt environment, and H2H all independently confirmed valuable; recent form is not.",
        report="MULTI_PROP_RESEARCH_REPORT.md",
        confidence_validation_status="VALIDATED",
    ),
    PropRegistryEntry(
        market_type="ASSISTS", model_status="VALIDATED", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_assists",
        summary="Beats the naive baseline (100% bootstrap credibility). TOI/role, opponent points-"
                "allowed environment, and H2H confirmed valuable; recent form only marginal (70% "
                "bootstrap, below the 95% credibility bar used elsewhere in this project).",
        report="MULTI_PROP_RESEARCH_REPORT.md",
        confidence_validation_status="CONDITIONAL", low_confidence_bet_eligibility="WATCH_ONLY",
    ),
    PropRegistryEntry(
        market_type="POINTS", model_status="EMPIRICAL_BASELINE_REMAINS_CHAMPION",
        live_market_support="NOT_CURRENTLY_AVAILABLE", odds_api_market_key="player_points",
        summary="Redesign Cycle 2 (rolling 3-fold walk-forward across all 4 real seasons, reused "
                "historical data honestly labeled as such -- not pristine holdout) directly targeted "
                "Cycle 1's finding that a per-player empirical-distribution baseline beats a parametric "
                "Negative Binomial GLM. Result confirmed and explained: a role-hierarchical empirical "
                "baseline (C2) ties the flat baseline (C1); the old GLM (C5) loses in all 3 folds, "
                "0% bootstrap credibility every time; a hierarchical mean + opponent-context offset "
                "model (C3) shows real but INCONSISTENT improvement at the primary 1+ threshold (wins "
                "big in one fold, loses in another) and makes the LOW-confidence bucket WORSE, not "
                "better. Per the pre-registered adoption standard's fold-consistency requirement: "
                "EMPIRICAL BASELINE REMAINS CHAMPION. Not forced into VALIDATED or PARTIAL.",
        report="PLAYER_POINTS_REDESIGN_REPORT.md",
        confidence_validation_status="CONDITIONAL", low_confidence_bet_eligibility="WATCH_ONLY",
    ),
    PropRegistryEntry(
        market_type="GOALS", model_status="VALIDATED", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_goals",
        summary="1+ GOAL VALIDATED: a hierarchical empirical baseline (player->role->league) plus a "
                "shrunk-shooting-talent/opponent-context/H2H-goals offset adjustment beats the best "
                "naive baseline with 99.4% game-clustered and 99.2% date-clustered bootstrap "
                "credibility, generalizes cleanly across both eval seasons, and is well calibrated. "
                "Real season-to-season shooting-talent persistence confirmed (r=0.37 to 0.61, "
                "increasing with shot volume) and used to justify volume-weighted shrinkage. "
                "Upstream SOG model ruled NOT ELIGIBLE (same conservative policy as Points). "
                "2+ GOALS: INSUFFICIENT DATA (fails only the per-confidence-bucket support check). "
                "MODEL: CURRENT INCUMBENT RETAINED -- a shot-quality refinement cycle (xG/shot, "
                "high-danger rate, finishing-above-xG, PP shot quality) tested 5 challengers against "
                "the frozen incumbent; none cleared the pre-registered 95% development bar (best "
                "87.6%), and even the reported final-fold Brier deltas were microscopic "
                "(~0.000006) -- consistent with shot quality being highly redundant with the "
                "shooting-talent signal already in the incumbent (r=0.73-0.96 among these metrics, "
                "measured directly). No refinement adopted; incumbent unchanged.",
        report="PLAYER_GOALS_SHOT_QUALITY_REPORT.md",
        confidence_validation_status="CONDITIONAL", low_confidence_bet_eligibility="WATCH_ONLY",
    ),
    PropRegistryEntry(
        market_type="PP_POINTS", model_status="RESEARCH", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_power_play_points",
        summary="Real PP-situation data confirmed available (the '5on4' situation rows already used "
                "as a feature for SOG/assists). Not modeled this slice -- deferred per the sprint's "
                "own priority order (Tier 2, after core assists/points/goals foundations).",
        report="MULTI_PROP_RESEARCH_REPORT.md",
    ),
    PropRegistryEntry(
        market_type="GOALIE_SAVES", model_status="RESEARCH", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_total_saves",
        summary="Depends on the already-validated starter-projection system (P(goalie starts)) "
                "combined with a conditional-on-start saves distribution -- architecture designed, "
                "not built. Actual historical starter must never be used as a pregame feature (same "
                "discipline as the SOG/goalie-quality slices).",
        report="MULTI_PROP_RESEARCH_REPORT.md",
    ),
    PropRegistryEntry(
        market_type="HITS", model_status="PROMISING", live_market_support="UNSUPPORTED_MARKET",
        odds_api_market_key=None,
        summary="Real data confirmed good quality: mean 1.19 hits/game, var/mean 1.71 (meaningfully "
                "overdispersed), only 40.9% zero-games -- better volume/variance than blocks in some "
                "respects. Not currently a documented Odds API NHL market key, so NO live pricing "
                "plumbing was built. Modelability is real; live access is not.",
        report="MULTI_PROP_RESEARCH_REPORT.md",
    ),
    PropRegistryEntry(
        market_type="PLUS_MINUS", model_status="REJECTED", live_market_support="UNSUPPORTED_MARKET",
        odds_api_market_key=None,
        summary="Not a documented Odds API market key. Depends on team scoring/conceding, deployment, "
                "empty-net states, teammates, and goalie -- a genuinely hard, high-noise target with "
                "no current market to price it against. Recommend: DEFER / DO NOT PRIORITIZE.",
        report="MULTI_PROP_RESEARCH_REPORT.md",
    ),
    PropRegistryEntry(
        market_type="ANYTIME_GOAL", model_status="SUPPORTED_BY_GOALS_MODEL", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_goal_scorer_anytime",
        summary="P(anytime goal) = P(goals >= 1) from the now-VALIDATED GOALS model, conceptually. "
                "Real DraftKings settlement semantics for this exact market key have NOT been "
                "verified against a live payload (no live NHL goal markets are currently posted) -- "
                "this status means the underlying probability is ready, not that live pricing has "
                "been market-confirmed. Verify against a real payload once markets return. "
                "LOW-confidence Anytime Goal inherits the SAME WATCH_ONLY gate as GOALS "
                "(research/player_props/decision_policy.py's market-family alias, not a duplicated "
                "rule) -- the two are settlement-equivalent to the same underlying event.",
        report="PLAYER_GOALS_VALIDATION_REPORT.md",
        confidence_validation_status="CONDITIONAL", low_confidence_bet_eligibility="WATCH_ONLY",
    ),
    PropRegistryEntry(
        market_type="FIRST_GOAL", model_status="RESEARCH", live_market_support="NOT_CURRENTLY_AVAILABLE",
        odds_api_market_key="player_goal_scorer_first",
        summary="NOT equivalent to anytime-goal probability -- requires an event-order/time model "
                "(who scores FIRST, not just whether they score). Materially harder; architecture not "
                "designed this slice beyond naming the requirement.",
        report="MULTI_PROP_RESEARCH_REPORT.md",
    ),
]


def get(market_type: str) -> PropRegistryEntry | None:
    return next((e for e in REGISTRY if e.market_type == market_type), None)


def validated_prop_families() -> list[str]:
    return [e.market_type for e in REGISTRY if e.model_status == "VALIDATED"]
