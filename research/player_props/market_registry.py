"""
Complete NHL Market Universe + Dependency Architecture.

ARCHITECTURE SLICE -- no new models, no refitting, no play-by-play
ingestion beyond the single real, live NHL API probe documented in
COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md Section Q (one GET request
against a real historical game, not bulk ingestion). Every field below
is either read from an already-existing, already-validated result file
in this project, or a documented judgment call, never a guess presented
as fact.

CORE PRINCIPLE: sportsbook market LABELS are not statistical MODELS. A
sportsbook may print "3+ SOG", "SOG Over 2.5", and "SOG O/U 2.5" as three
buttons -- all three are the SAME canonical target,
`P(player SOG >= 3)`, read from the ONE already-validated player SOG
distribution. RAW_MARKET_LABELS below is the literal (transcribed,
countable) sportsbook-label universe from the architecture prompt;
CANONICAL_MARKETS is what it normalizes down to.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ============================================================================
# Part 2: the 17 underlying stochastic-process families. Confirmed
# sufficient for the full requested market universe below -- no 18th
# foundation was found necessary (see report Section AB0/architecture
# discussion).
# ============================================================================
PROCESS_FAMILIES = (
    "PLAYER_ACTIVE_ROLE_TOI", "PLAYER_SHOT_GENERATION", "PLAYER_BLOCK_EVENT_GENERATION",
    "PLAYER_HIT_EVENT_GENERATION", "PLAYER_GOAL_GENERATION", "GOAL_ASSIST_POINT_ATTRIBUTION",
    "SPECIAL_TEAMS_STATE", "PENALTY_PROCESS", "FACEOFF_PROCESS", "GOALIE_WORKLOAD_SAVE_PROCESS",
    "TEAM_SHOT_GENERATION", "TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING", "GAME_SCORE_STATE",
    "EMPTY_NET_STATE", "OT_SHOOTOUT_STATE", "JOINT_DEPENDENCE_SIMULATION",
)

DERIVATION_TYPES = ("DIRECT_MODEL", "DISTRIBUTION_THRESHOLD", "EVENT_TIME", "SIMULATION", "COMPOSITE")

# ============================================================================
# Part 1 raw input: the literal sportsbook-label universe from the
# architecture prompt, transcribed verbatim and grouped exactly as given
# -- this is what Part A's count is computed FROM, not a hand-typed
# number, so the count in the report is reproducible from this file.
# Cross-section repeats (e.g. "Power-play goal" appearing under both
# PLAYER GOALS and PLAYER SPECIAL TEAMS) are preserved as separate raw
# entries -- that repetition is itself evidence for the alias-
# consolidation exercise, not a transcription error.
# ============================================================================
RAW_MARKET_LABELS: dict[str, list[str]] = {
    "PLAYER_GOALS_SCORING": [
        "Anytime goal scorer", "2+ goals", "3+ goals / hat trick", "First goal scorer",
        "Last goal scorer", "Team first goal scorer", "Goals O/U", "Goal in 1st period",
        "Goal in 2nd period", "Goal in 3rd period", "Power-play goal", "Short-handed goal",
        "Game-winning goal", "Player goal + team win", "Multiple named players both to score",
    ],
    "PLAYER_POINTS": [
        "Points O/U 0.5", "Points O/U 1.5", "Points O/U 2.5", "1+ point", "2+ points", "3+ points",
    ],
    "PLAYER_ASSISTS": [
        "Assists O/U 0.5", "Assists O/U 1.5", "1+ assist", "2+ assists", "3+ assists",
    ],
    "PLAYER_SOG": [
        "SOG O/U", "1+ SOG", "2+ SOG", "3+ SOG", "4+ SOG", "5+ SOG", "6+ SOG", "7+ SOG", "8+ SOG",
    ],
    "PLAYER_BLOCKED_SHOTS": [
        "Blocked shots O/U", "1+ blocked shot", "2+ blocks", "3+ blocks", "4+ blocks",
    ],
    "PLAYER_HITS": [
        "Hits O/U", "1+ hit", "2+ hits", "3+ hits", "4+ hits",
    ],
    "PLAYER_SPECIAL_TEAMS": [
        "Power-play point", "Power-play points O/U", "Power-play goal", "Short-handed point",
        "Short-handed goal", "Game-winning goal",
    ],
    "PLAYER_PENALTIES": [
        "Penalty minutes O/U", "Player to receive a penalty",
    ],
    "PLAYER_FACEOFFS": [
        "Faceoff wins O/U", "Faceoffs taken O/U", "Faceoff percentage",
    ],
    "PLAYER_USAGE_OTHER": [
        "Time on ice O/U", "Plus/minus",
    ],
    "GOALIE": [
        "Goalie saves O/U", "20+ saves", "25+ saves", "30+ saves", "35+ saves", "40+ saves",
        "Goals allowed O/U", "Goalie to record win", "Goalie shutout", "Goalie saves by period",
        "1st-period saves", "Goalie to allow first goal", "Goalie assists", "Goalie points",
        "Both goalies X+ saves",
    ],
    "TEAM": [
        "Team total goals", "Team total 2+", "Team total 3+", "Team total 4+", "Team total 5+",
        "Team to score first", "Team to score last", "Team to score in 1st period",
        "Team 1st-period total", "Team 2nd-period total", "Team 3rd-period total",
        "Team to score every period", "Team to win every period", "Team highest-scoring period",
        "Team clean sheet / shutout", "Team to score PP goal", "Team PP goals",
        "Team short-handed goal", "Team empty-net goal", "Team shots on goal",
        "Team penalty minutes", "Team total penalties", "Team faceoff wins",
    ],
    "GAME_OUTCOME": [
        "Game to overtime", "Game to shootout", "Method of victory: regulation",
        "Method of victory: overtime", "Method of victory: shootout", "Winning margin",
        "Exact winning margin", "Exact final score", "Both teams to score",
        "Both teams 2+ goals", "Both teams 3+ goals", "First goal timing",
        "First goal method: EV", "First goal method: PP", "First goal method: SH",
        "Race to 1", "Race to 2", "Race to 3", "Race to 4", "Team to lead 1-0",
        "Team to lead 2-0", "Largest lead", "Lead after every period", "Come-from-behind win",
        "Any empty-net goal", "Any short-handed goal", "Any power-play goal", "Total PP goals",
        "Total penalties", "Total PIM", "Total shots", "Total saves", "Total blocked shots",
        "Total faceoffs",
    ],
    "PERIOD_MARKETS": [
        f"{stat} ({period})"
        for period in ("1st period", "2nd period", "3rd period")
        for stat in ("Player goal", "Player point", "Player SOG", "Team total goals",
                     "Game total goals", "Team to score", "First team to score",
                     "Both teams score", "Correct score", "Goalie saves", "Period winning margin")
    ] + ["Highest-scoring period (period markets callout)"],
}


def total_raw_labels() -> int:
    return sum(len(v) for v in RAW_MARKET_LABELS.values())


@dataclass
class MarketDefinition:
    market_id: str
    display_name: str
    aliases: list[str]
    category: str
    underlying_process: tuple[str, ...]
    target_variable: str
    derivation_type: str
    historical_data_status: str
    model_status: str
    threshold_validation_status: str
    confidence_status: str
    low_confidence_policy: str
    conservative_probability_status: str
    odds_api_support: str                 # SUPPORTED / UNSUPPORTED / UNKNOWN / NOT_OBSERVED
    dk_contract_verified: bool
    requires_starting_goalie: bool
    requires_projected_active: bool
    requires_play_by_play: bool
    requires_joint_simulation: bool
    parlay_eligibility_status: str
    notes: str = ""


# ============================================================================
# Part 3/4-8/30: canonical markets, hand-specified for every market this
# project can currently derive and/or has validated (the well-understood
# "front of the registry"), then a compact, still fully machine-readable
# generated tail for the remaining requested markets that are honestly
# NOT_BUILT (Parts 9-19). Every DERIVABLE/VALIDATED claim below traces to
# a real, already-existing result file cited in `notes`.
# ============================================================================

_HAND_SPECIFIED: list[MarketDefinition] = [
    # ---- SOG family: process = PLAYER_SHOT_GENERATION ----
    *[
        MarketDefinition(
            market_id=f"PLAYER_SOG_{n}PLUS", display_name=f"Player {n}+ SOG",
            aliases=[f"{n}+ SOG"] + (["SOG O/U"] if n in (2, 3) else []),
            category="PLAYER_SOG", underlying_process=("PLAYER_SHOT_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
            target_variable="P(player SOG >= n)", derivation_type="DISTRIBUTION_THRESHOLD",
            historical_data_status="AVAILABLE_USED",
            model_status="VALIDATED" if n in (2, 3, 4, 5) else ("DERIVABLE_NOT_VALIDATED" if n == 1 else "INSUFFICIENT_TAIL_DATA"),
            threshold_validation_status="VALIDATED" if n in (2, 3, 4, 5) else ("DERIVABLE_TRIVIAL_NOT_VALIDATED" if n == 1 else "INSUFFICIENT_TAIL_DATA"),
            confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="NORMAL",
            conservative_probability_status="IMPLEMENTED",
            odds_api_support="SUPPORTED" if n in (2, 3, 4) else "UNKNOWN", dk_contract_verified=False,
            requires_starting_goalie=False, requires_projected_active=True,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="ELIGIBLE" if n in (2, 3, 4, 5) else "NOT_YET_ELIGIBLE",
            notes="research/player_sog_results.json confidence_breakdown/season_breakdown, thresholds 2-5. "
                  "1+ never separately tested (near-universal base rate, not a real book market). 6+/7+/8+ "
                  "never bootstrap-validated -- real tail sparsity, not extrapolated.",
        ) for n in range(1, 9)
    ],
    # ---- Blocks family: process = PLAYER_BLOCK_EVENT_GENERATION ----
    *[
        MarketDefinition(
            market_id=f"PLAYER_BLOCKS_{n}PLUS", display_name=f"Player {n}+ Blocked Shots",
            aliases=[f"{n}+ block{'s' if n>1 else ''}"] + (["1+ blocked shot"] if n == 1 else []) + (["Blocked shots O/U"] if n in (1, 2) else []),
            category="PLAYER_BLOCKED_SHOTS", underlying_process=("PLAYER_BLOCK_EVENT_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
            target_variable="P(player blocks >= n)", derivation_type="DISTRIBUTION_THRESHOLD",
            historical_data_status="AVAILABLE_USED",
            model_status="VALIDATED" if n in (1, 2, 3) else "INSUFFICIENT_TAIL_DATA",
            threshold_validation_status="VALIDATED" if n in (1, 2, 3) else "INSUFFICIENT_TAIL_DATA",
            confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="NORMAL",
            conservative_probability_status="IMPLEMENTED",
            odds_api_support="UNKNOWN", dk_contract_verified=False,
            requires_starting_goalie=False, requires_projected_active=True,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="ELIGIBLE" if n in (1, 2, 3) else "NOT_YET_ELIGIBLE",
            notes="research/player_blocks_results.json, HEADLINE_THRESHOLDS=(1,2,3). 4+ never tested (real "
                  "sparsity: mean 0.878 blocks/game, 50% zero-rate).",
        ) for n in range(1, 5)
    ],
    # ---- Assists family: process = GOAL_ASSIST_POINT_ATTRIBUTION ----
    *[
        MarketDefinition(
            market_id=f"PLAYER_ASSISTS_{n}PLUS", display_name=f"Player {n}+ Assists",
            aliases=[f"{n}+ assist{'s' if n>1 else ''}"] + (["Assists O/U 0.5"] if n == 1 else ["Assists O/U 1.5"] if n == 2 else []),
            category="PLAYER_ASSISTS", underlying_process=("GOAL_ASSIST_POINT_ATTRIBUTION", "PLAYER_ACTIVE_ROLE_TOI"),
            target_variable="P(player assists >= n)", derivation_type="DISTRIBUTION_THRESHOLD",
            historical_data_status="AVAILABLE_USED",
            model_status="VALIDATED" if n in (1, 2) else "INSUFFICIENT_DATA",
            threshold_validation_status="VALIDATED" if n in (1, 2) else "INSUFFICIENT_DATA",
            confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="WATCH_ONLY",
            conservative_probability_status="IMPLEMENTED",
            odds_api_support="SUPPORTED" if n == 1 else "UNKNOWN", dk_contract_verified=False,
            requires_starting_goalie=False, requires_projected_active=True,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="ELIGIBLE_UNLESS_LOW_CONFIDENCE" if n in (1, 2) else "NOT_YET_ELIGIBLE",
            notes="research/player_assists_results.json. 3+ explicitly documented as too rare to evaluate "
                  "meaningfully. LOW-confidence gate: decision_policy_v2.",
        ) for n in (1, 2, 3)
    ],
    # ---- Points family: process = GOAL_ASSIST_POINT_ATTRIBUTION ----
    *[
        MarketDefinition(
            market_id=f"PLAYER_POINTS_{n}PLUS", display_name=f"Player {n}+ Points",
            aliases=[f"{n}+ point{'s' if n>1 else ''}"] + ({1: ["Points O/U 0.5"], 2: ["Points O/U 1.5"], 3: ["Points O/U 2.5"]}.get(n, [])),
            category="PLAYER_POINTS", underlying_process=("GOAL_ASSIST_POINT_ATTRIBUTION", "PLAYER_ACTIVE_ROLE_TOI"),
            target_variable="P(player points >= n)", derivation_type="DISTRIBUTION_THRESHOLD",
            historical_data_status="AVAILABLE_USED",
            model_status="EMPIRICAL_BASELINE_REMAINS_CHAMPION" if n in (1, 2) else "INSUFFICIENT_DATA",
            threshold_validation_status="USABLE_VIA_CHAMPION_BASELINE_NOT_A_VALIDATED_NEW_MODEL" if n in (1, 2) else "INSUFFICIENT_DATA",
            confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="WATCH_ONLY",
            conservative_probability_status="IMPLEMENTED",
            odds_api_support="SUPPORTED" if n == 1 else "UNKNOWN", dk_contract_verified=False,
            requires_starting_goalie=False, requires_projected_active=True,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="NOT_YET_ELIGIBLE",
            notes="PLAYER_POINTS_VALIDATION_REPORT.md + PLAYER_POINTS_REDESIGN_REPORT.md -- the empirical "
                  "baseline beats every tested parametric candidate; it is USABLE as the champion "
                  "probability source but is explicitly NOT a 'VALIDATED new model' -- Part 7's required "
                  "distinction, preserved exactly.",
        ) for n in (1, 2, 3)
    ],
    # ---- Goals family: process = PLAYER_GOAL_GENERATION ----
    MarketDefinition(
        market_id="PLAYER_GOALS_1PLUS", display_name="Player 1+ Goal (Anytime Goal Scorer)",
        aliases=["Anytime goal scorer", "Goals O/U (0.5 line)", "1+ Goal"],
        category="PLAYER_GOALS_SCORING", underlying_process=("PLAYER_GOAL_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
        target_variable="P(player goals >= 1)", derivation_type="DISTRIBUTION_THRESHOLD",
        historical_data_status="AVAILABLE_USED", model_status="VALIDATED", threshold_validation_status="VALIDATED",
        confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="WATCH_ONLY",
        conservative_probability_status="IMPLEMENTED", odds_api_support="SUPPORTED", dk_contract_verified=False,
        requires_starting_goalie=False, requires_projected_active=True, requires_play_by_play=False,
        requires_joint_simulation=False, parlay_eligibility_status="ELIGIBLE_UNLESS_LOW_CONFIDENCE",
        notes="PLAYER_GOALS_VALIDATION_REPORT.md. Canonical target for ANYTIME_GOAL and any future "
              "'Goals Over 0.5' label -- Part 1/12's alias consolidation, reusing "
              "decision_policy.py's _MARKET_FAMILY_ALIASES mechanism directly.",
    ),
    MarketDefinition(
        market_id="PLAYER_GOALS_2PLUS", display_name="Player 2+ Goals",
        aliases=["2+ goals"], category="PLAYER_GOALS_SCORING",
        underlying_process=("PLAYER_GOAL_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
        target_variable="P(player goals >= 2)", derivation_type="DISTRIBUTION_THRESHOLD",
        historical_data_status="AVAILABLE_USED", model_status="INSUFFICIENT_DATA", threshold_validation_status="INSUFFICIENT_DATA",
        confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="WATCH_ONLY",
        conservative_probability_status="IMPLEMENTED", odds_api_support="UNKNOWN", dk_contract_verified=False,
        requires_starting_goalie=False, requires_projected_active=True, requires_play_by_play=False,
        requires_joint_simulation=False, parlay_eligibility_status="NOT_YET_ELIGIBLE",
        notes="Fails only the per-confidence-bucket support check (LOW bucket had 5 real 2+ events).",
    ),
    MarketDefinition(
        market_id="PLAYER_GOALS_3PLUS", display_name="Player 3+ Goals (Hat Trick)",
        aliases=["3+ goals / hat trick", "Hat Trick"], category="PLAYER_GOALS_SCORING",
        underlying_process=("PLAYER_GOAL_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
        target_variable="P(player goals >= 3)", derivation_type="DISTRIBUTION_THRESHOLD",
        historical_data_status="AVAILABLE_USED", model_status="INSUFFICIENT_DATA", threshold_validation_status="INSUFFICIENT_DATA",
        confidence_status="CURRENT_FRAMEWORK", low_confidence_policy="WATCH_ONLY",
        conservative_probability_status="IMPLEMENTED", odds_api_support="UNKNOWN", dk_contract_verified=False,
        requires_starting_goalie=False, requires_projected_active=True, requires_play_by_play=False,
        requires_joint_simulation=False, parlay_eligibility_status="NOT_YET_ELIGIBLE",
        notes="Real full-corpus 3+ goal rate is 0.20% (PLAYER_GOALS_VALIDATION_REPORT.md Section D) -- "
              "never realistically supportable at this corpus size. DEFER, do not force.",
    ),
]

_HAND_SPECIFIED += [
    # ---- Hits family: process = PLAYER_HIT_EVENT_GENERATION. Real data
    # confirmed good (MULTI_PROP_RESEARCH_REPORT.md: mean 1.19 hits/game,
    # var/mean 1.71, 40.9% zero-rate) but MODELABILITY was never turned
    # into an actual fitted count model this session -- Part 14's
    # explicit distinction (modelability vs. built) preserved honestly.
    *[
        MarketDefinition(
            market_id=f"PLAYER_HITS_{n}PLUS", display_name=f"Player {n}+ Hits",
            aliases=[f"{n}+ hit{'s' if n>1 else ''}"] + (["Hits O/U"] if n in (1, 2) else []),
            category="PLAYER_HITS", underlying_process=("PLAYER_HIT_EVENT_GENERATION", "PLAYER_ACTIVE_ROLE_TOI"),
            target_variable="P(player hits >= n)", derivation_type="DISTRIBUTION_THRESHOLD",
            historical_data_status="AVAILABLE_UNUSED",
            model_status="RESEARCH", threshold_validation_status="NOT_BUILT",
            confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL",
            conservative_probability_status="NOT_YET_BUILT",
            odds_api_support="UNSUPPORTED_MARKET", dk_contract_verified=False,
            requires_starting_goalie=False, requires_projected_active=True,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="NOT_YET_ELIGIBLE",
            notes="MULTI_PROP_RESEARCH_REPORT.md: real data quality confirmed good (better volume/"
                  "variance than blocks in some respects), but no Odds API market key documented -- "
                  "modelability is real, live access is not (Part 14's distinction).",
        ) for n in (1, 2, 3, 4)
    ],
    # ---- Goalie saves: process = GOALIE_WORKLOAD_SAVE_PROCESS ----
    *[
        MarketDefinition(
            market_id=f"GOALIE_SAVES_{n}PLUS", display_name=f"Goalie {n}+ Saves",
            aliases=[f"{n}+ saves"] + (["Goalie saves O/U"] if n == 25 else []),
            category="GOALIE", underlying_process=("GOALIE_WORKLOAD_SAVE_PROCESS", "TEAM_SHOT_GENERATION"),
            target_variable="P(goalie saves >= n | starts)", derivation_type="COMPOSITE",
            historical_data_status="AVAILABLE_UNUSED",
            model_status="RESEARCH", threshold_validation_status="NOT_BUILT",
            confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL",
            conservative_probability_status="NOT_YET_BUILT",
            odds_api_support="SUPPORTED" if n == 25 else "UNKNOWN", dk_contract_verified=False,
            requires_starting_goalie=True, requires_projected_active=False,
            requires_play_by_play=False, requires_joint_simulation=False,
            parlay_eligibility_status="NOT_YET_ELIGIBLE",
            notes="Depends on the already-validated starter-projection system (P(goalie starts)) "
                  "combined with a conditional-on-start saves distribution -- architecture designed "
                  "(MULTI_PROP_RESEARCH_REPORT.md), not built. Actual historical starter must never "
                  "become a pregame feature.",
        ) for n in (20, 25, 30, 35, 40)
    ],
    MarketDefinition(
        market_id="GOALIE_GOALS_ALLOWED_OU", display_name="Goalie Goals Allowed O/U",
        aliases=["Goals allowed O/U"], category="GOALIE",
        underlying_process=("GOALIE_WORKLOAD_SAVE_PROCESS", "TEAM_SHOT_GENERATION"),
        target_variable="goalie goals allowed distribution | starts", derivation_type="COMPOSITE",
        historical_data_status="AVAILABLE_UNUSED", model_status="RESEARCH", threshold_validation_status="NOT_BUILT",
        confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL", conservative_probability_status="NOT_YET_BUILT",
        odds_api_support="UNKNOWN", dk_contract_verified=False, requires_starting_goalie=True,
        requires_projected_active=False, requires_play_by_play=False, requires_joint_simulation=False,
        parlay_eligibility_status="NOT_YET_ELIGIBLE", notes="Same GOALIE_WORKLOAD_SAVE_PROCESS as saves; not built.",
    ),
    MarketDefinition(
        market_id="GOALIE_SHUTOUT", display_name="Goalie Shutout", aliases=["Goalie shutout"], category="GOALIE",
        underlying_process=("GOALIE_WORKLOAD_SAVE_PROCESS", "TEAM_GOAL_GENERATION"),
        target_variable="P(opponent goals == 0 | goalie plays full game)", derivation_type="COMPOSITE",
        historical_data_status="AVAILABLE_UNUSED", model_status="RESEARCH", threshold_validation_status="NOT_BUILT",
        confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL", conservative_probability_status="NOT_YET_BUILT",
        odds_api_support="UNKNOWN", dk_contract_verified=False, requires_starting_goalie=True,
        requires_projected_active=False, requires_play_by_play=False, requires_joint_simulation=True,
        parlay_eligibility_status="NOT_YET_ELIGIBLE", notes="Requires a coherent opponent-team-goals distribution, not just a saves count.",
    ),
    MarketDefinition(
        market_id="GOALIE_WIN", display_name="Goalie to Record Win", aliases=["Goalie to record win"], category="GOALIE",
        underlying_process=("GOALIE_WORKLOAD_SAVE_PROCESS", "GAME_SCORE_STATE"),
        target_variable="P(goalie's team wins | goalie starts, NHL win-credit rules)", derivation_type="COMPOSITE",
        historical_data_status="AVAILABLE_USED", model_status="RESEARCH", threshold_validation_status="NOT_BUILT",
        confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL", conservative_probability_status="NOT_YET_BUILT",
        odds_api_support="UNKNOWN", dk_contract_verified=False, requires_starting_goalie=True,
        requires_projected_active=False, requires_play_by_play=False, requires_joint_simulation=False,
        parlay_eligibility_status="NOT_YET_ELIGIBLE",
        notes="The production NHL win model already estimates team win probability -- this market needs "
              "only P(starter) x P(team win), not a new marginal model. Highest-leverage 'cheap' goalie market.",
    ),
    # ---- A representative, fully-specified TEAM example (the rest of
    # TEAM/GAME_OUTCOME/PERIOD are generated below, same honest statuses) ----
    MarketDefinition(
        market_id="TEAM_SOG_TOTAL", display_name="Team Total SOG", aliases=["Team shots on goal"], category="TEAM",
        underlying_process=("TEAM_SHOT_GENERATION",), target_variable="team SOG distribution",
        derivation_type="DIRECT_MODEL", historical_data_status="AVAILABLE_UNUSED_AS_STANDALONE_TARGET",
        model_status="NOT_BUILT", threshold_validation_status="NOT_BUILT", confidence_status="NOT_YET_ASSESSED",
        low_confidence_policy="NORMAL", conservative_probability_status="NOT_YET_BUILT",
        odds_api_support="UNKNOWN", dk_contract_verified=False, requires_starting_goalie=False,
        requires_projected_active=False, requires_play_by_play=False, requires_joint_simulation=False,
        parlay_eligibility_status="NOT_YET_ELIGIBLE",
        notes="Directly derivable from summing the ALREADY-CAPTURED player-level SOG corpus per team-game "
              "(the exact team_game_totals pattern already reused across SOG/Blocks/Assists/Points/Goals "
              "as a CONTEXT feature) -- never turned into its OWN standalone target. Lowest-effort "
              "not-yet-built market in this entire registry.",
    ),
]


# ============================================================================
# Compact generated tail: every remaining requested market not already
# hand-specified above. Category-level defaults reflect the REAL,
# honest state of each process family (Parts 9-19) -- not a template
# used to avoid writing detail, but a faithful representation that this
# entire tail genuinely shares the same NOT_BUILT status and the same
# handful of real reasons why.
# ============================================================================

# Multi-Season PBP Expansion slice: every market previously passed
# data_status="REQUIRES_PLAY_BY_PLAY" was updated to "AVAILABLE_UNUSED" --
# a real, verified 4-season (2022-23 through 2025-26, 5,248 games) play-by-
# play research corpus now exists (research/real_nhl_pbp/), so the raw data
# these markets need is genuinely no longer missing. model_status is
# deliberately left "NOT_BUILT" for every one of them (Part 35's explicit
# instruction): historical_data_status answers "does the raw data exist,"
# not "has a model been built" -- those are two different fields and this
# change touches only the first. See NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md
# Section AL for the full list of transitioned markets.
def _generated(market_id, display_name, aliases, category, process, derivation_type,
                data_status, requires_pbp=False, requires_sim=False, requires_goalie=False, notes=""):
    assert isinstance(data_status, str), f"{market_id}: data_status must be a str, got {data_status!r}"
    assert isinstance(requires_pbp, bool), f"{market_id}: requires_pbp must be a bool, got {requires_pbp!r}"
    assert isinstance(requires_sim, bool), f"{market_id}: requires_sim must be a bool, got {requires_sim!r}"
    return MarketDefinition(
        market_id=market_id, display_name=display_name, aliases=aliases, category=category,
        underlying_process=process, target_variable=display_name, derivation_type=derivation_type,
        historical_data_status=data_status, model_status="NOT_BUILT", threshold_validation_status="NOT_BUILT",
        confidence_status="NOT_YET_ASSESSED", low_confidence_policy="NORMAL",
        conservative_probability_status="NOT_YET_BUILT", odds_api_support="UNKNOWN", dk_contract_verified=False,
        requires_starting_goalie=requires_goalie, requires_projected_active=not requires_pbp,
        requires_play_by_play=requires_pbp, requires_joint_simulation=requires_sim, parlay_eligibility_status="NOT_YET_ELIGIBLE",
        notes=notes,
    )


_GENERATED_TAIL: list[MarketDefinition] = [
    # ---- Event-time GOALS markets (Part 23: cannot derive from anytime-goal) ----
    _generated("PLAYER_FIRST_GOAL_SCORER", "Player First Goal Scorer", ["First goal scorer"],
               "PLAYER_GOALS_SCORING", ("PLAYER_GOAL_GENERATION", "PERIOD_EVENT_TIMING", "JOINT_DEPENDENCE_SIMULATION"),
               "EVENT_TIME", "AVAILABLE_UNUSED", True, True,
               notes="Competing-risk/hazard problem across all 12+ skaters on ice -- explicitly NOT "
                     "P(goals>=1). Registry already carries this distinction (FIRST_GOAL entry, RESEARCH)."),
    _generated("PLAYER_LAST_GOAL_SCORER", "Player Last Goal Scorer", ["Last goal scorer"],
               "PLAYER_GOALS_SCORING", ("PLAYER_GOAL_GENERATION", "PERIOD_EVENT_TIMING", "JOINT_DEPENDENCE_SIMULATION"),
               "EVENT_TIME", "AVAILABLE_UNUSED", True, True,
               notes="Symmetric to first-goal-scorer; also requires full event ordering."),
    _generated("TEAM_FIRST_GOAL_SCORER", "Team First Goal Scorer", ["Team first goal scorer"],
               "PLAYER_GOALS_SCORING", ("PLAYER_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME",
               "AVAILABLE_UNUSED", True, False,
               notes="Conditional on which TEAM scores first (a team-level event-time question) then WHICH player on that team."),
    *[_generated(f"PLAYER_GOAL_PERIOD_{i}", f"Player Goal in Period {i}", [f"Goal in {['1st','2nd','3rd'][i-1]} period"],
                 "PLAYER_GOALS_SCORING", ("PLAYER_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False,
                 notes="A crude period-share heuristic (assume goals split proportionally to period length) is "
                       "mechanically possible without PBP, but would ignore real third-period/empty-net effects -- "
                       "not built, not recommended as a first version.")
      for i in (1, 2, 3)],
    _generated("PLAYER_PP_GOAL", "Player Power-Play Goal", ["Power-play goal"], "PLAYER_GOALS_SCORING",
               ("PLAYER_GOAL_GENERATION", "SPECIAL_TEAMS_STATE"), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False,
               notes="pp.goals is ALREADY captured in the Goals/Points/Assists corpora (situation='5on4') -- "
                     "never turned into its own PP-goal count-distribution target. One of the lowest-effort "
                     "not-yet-built markets given existing data."),
    _generated("PLAYER_SH_GOAL", "Player Short-Handed Goal", ["Short-handed goal"], "PLAYER_GOALS_SCORING",
               ("PLAYER_GOAL_GENERATION", "SPECIAL_TEAMS_STATE"), "DISTRIBUTION_THRESHOLD", "REQUIRES_NEW_EXTRACTION", False, False,
               notes="Raw 'situation=4on5' rows exist in the same already-downloaded MoneyPuck files, but were "
                     "only ever parsed for the BLOCKING side (research/player_blocks' 'pk' block) -- a forward's "
                     "own SH-goal extraction from the same rows was never built."),
    _generated("GAME_WINNING_GOAL", "Player Game-Winning Goal", ["Game-winning goal"], "PLAYER_GOALS_SCORING",
               ("PLAYER_GOAL_GENERATION", "GAME_SCORE_STATE", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "READY", True, True,
               notes="Event-Timing Utility Closure slice: the RETROSPECTIVE ground-truth GWG is now "
                     "deterministically derivable for every non-shootout historical game -- "
                     "research/real_nhl_pbp/gwg.py, corpus-validated with 0 invariant violations across "
                     "all 4,875 non-SO games in the 4-season corpus (see "
                     "EVENT_TIMING_UTILITY_CLOSURE_REPORT.md). historical_data_status reflects that "
                     "ground-truth availability only -- requires_joint_simulation stays True because "
                     "PREDICTING (pre-game) who will score a future GWG is a different, still-unbuilt "
                     "question from reconstructing what the GWG WAS in a completed game; model_status "
                     "remains NOT_BUILT (Part 23's explicit instruction -- no predictive model exists)."),
    _generated("PLAYER_GOAL_AND_TEAM_WIN", "Player Goal + Team Win", ["Player goal + team win"], "PLAYER_GOALS_SCORING",
               ("PLAYER_GOAL_GENERATION", "GAME_SCORE_STATE"), "COMPOSITE", "AVAILABLE_USED", False, True,
               notes="Structural dependence (Part 27) -- a player scoring correlates with their team's win "
                     "probability; must not be priced as P(goal)xP(win)."),
    _generated("MULTIPLE_PLAYERS_BOTH_SCORE", "Two Named Players Both to Score", ["Multiple named players both to score"],
               "PLAYER_GOALS_SCORING", ("PLAYER_GOAL_GENERATION", "JOINT_DEPENDENCE_SIMULATION"), "COMPOSITE",
               "AVAILABLE_USED", False, True, notes="Cross-player joint dependence -- teammates share the same game-level scoring environment."),

    # ---- Special teams (player), penalties, faceoffs, usage ----
    _generated("PLAYER_PP_POINT", "Player Power-Play Point", ["Power-play point", "Power-play points O/U"],
               "PLAYER_SPECIAL_TEAMS", ("SPECIAL_TEAMS_STATE", "GOAL_ASSIST_POINT_ATTRIBUTION"), "DISTRIBUTION_THRESHOLD",
               "AVAILABLE_UNUSED", False, False, notes="pp block already captures points/goals/assists; never modeled as its own target."),
    _generated("PLAYER_SH_POINT", "Player Short-Handed Point", ["Short-handed point"], "PLAYER_SPECIAL_TEAMS",
               ("SPECIAL_TEAMS_STATE", "GOAL_ASSIST_POINT_ATTRIBUTION"), "DISTRIBUTION_THRESHOLD", "REQUIRES_NEW_EXTRACTION", False, False),
    _generated("PLAYER_PIM_OU", "Player Penalty Minutes O/U", ["Penalty minutes O/U"], "PLAYER_PENALTIES",
               ("PENALTY_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False,
               notes="I_F_penalityMinutes / penalties fields confirmed present in the raw MoneyPuck CSV since "
                     "the very first data audit this session -- never used as a modeling target."),
    _generated("PLAYER_TO_RECEIVE_PENALTY", "Player to Receive a Penalty", ["Player to receive a penalty"],
               "PLAYER_PENALTIES", ("PENALTY_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("PLAYER_FACEOFF_WINS_OU", "Player Faceoff Wins O/U", ["Faceoff wins O/U"], "PLAYER_FACEOFFS",
               ("FACEOFF_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False,
               notes="I_F_faceOffsWon / faceoffsWon / faceoffsLost confirmed present in the same raw CSVs, unused."),
    _generated("PLAYER_FACEOFFS_TAKEN_OU", "Player Faceoffs Taken O/U", ["Faceoffs taken O/U"], "PLAYER_FACEOFFS",
               ("FACEOFF_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("PLAYER_FACEOFF_PCT", "Player Faceoff Percentage", ["Faceoff percentage"], "PLAYER_FACEOFFS",
               ("FACEOFF_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("PLAYER_TOI_OU", "Player Time on Ice O/U", ["Time on ice O/U"], "PLAYER_USAGE_OTHER",
               ("PLAYER_ACTIVE_ROLE_TOI",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_USED", False, False,
               notes="icetime_seconds is the single most heavily-used PIT-safe feature in this entire project "
                     "-- but was never itself fit as a standalone continuous-outcome sportsbook target."),
    # PLUS_MINUS deliberately reuses the existing, already-considered
    # verdict from the Preseason Product Sprint slice (REJECTED) rather
    # than re-litigating it -- Part 16's "existing model protection" spirit.
    MarketDefinition(
        market_id="PLAYER_PLUS_MINUS", display_name="Player Plus/Minus", aliases=["Plus/minus"],
        category="PLAYER_USAGE_OTHER", underlying_process=("TEAM_GOAL_GENERATION", "JOINT_DEPENDENCE_SIMULATION"),
        target_variable="player plus/minus", derivation_type="SIMULATION", historical_data_status="AVAILABLE_UNUSED",
        model_status="REJECTED", threshold_validation_status="NOT_BUILT", confidence_status="NOT_YET_ASSESSED",
        low_confidence_policy="NORMAL", conservative_probability_status="NOT_YET_BUILT", odds_api_support="UNSUPPORTED_MARKET",
        dk_contract_verified=False, requires_starting_goalie=False, requires_projected_active=False,
        requires_play_by_play=True, requires_joint_simulation=True, parlay_eligibility_status="NOT_ELIGIBLE",
        notes="Verdict carried forward unchanged from MULTI_PROP_RESEARCH_REPORT.md: no documented Odds API "
              "market key; depends on teammates/opponents/goalie/empty-net state. DEFER, do not re-litigate.",
    ),

    # ---- Team markets (beyond the one hand-specified TEAM_SOG_TOTAL) ----
    *[_generated(f"TEAM_GOALS_{n}PLUS", f"Team {n}+ Goals" if n else "Team Total Goals",
                 [f"Team total {n}+" if n else "Team total goals"], "TEAM", ("TEAM_GOAL_GENERATION",),
                 "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED_AS_STANDALONE_TARGET", False, False,
                 notes="Team-level goal aggregates are already computed as CONTEXT features for every "
                       "player prop this session -- never fit as their own coherent count distribution.")
      for n in (0, 2, 3, 4, 5)],
    _generated("TEAM_TO_SCORE_FIRST", "Team to Score First", ["Team to score first"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    _generated("TEAM_TO_SCORE_LAST", "Team to Score Last", ["Team to score last"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    *[_generated(f"TEAM_PERIOD_{i}_TOTAL", f"Team Period {i} Total Goals", [f"Team {['1st','2nd','3rd'][i-1]}-period total"],
                 "TEAM", ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False) for i in (1, 2, 3)],
    _generated("TEAM_SCORE_IN_PERIOD_1", "Team to Score in 1st Period", ["Team to score in 1st period"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    _generated("TEAM_SCORE_EVERY_PERIOD", "Team to Score Every Period", ["Team to score every period"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    _generated("TEAM_WIN_EVERY_PERIOD", "Team to Win Every Period", ["Team to win every period"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    _generated("TEAM_HIGHEST_SCORING_PERIOD", "Team Highest-Scoring Period", ["Team highest-scoring period"], "GAME_OUTCOME",
               ("TEAM_GOAL_GENERATION", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False),
    _generated("TEAM_SHUTOUT", "Team Clean Sheet / Shutout", ["Team clean sheet / shutout"], "TEAM",
               ("TEAM_GOAL_GENERATION",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED_AS_STANDALONE_TARGET", False, True),
    _generated("TEAM_PP_GOAL_ANYTIME", "Team to Score a PP Goal", ["Team to score PP goal"], "TEAM",
               ("SPECIAL_TEAMS_STATE", "TEAM_GOAL_GENERATION"), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False,
               notes="Directly summable from the already-captured player-level pp.goals blocks across a team's own roster."),
    _generated("TEAM_PP_GOALS_TOTAL", "Team Total PP Goals", ["Team PP goals"], "TEAM",
               ("SPECIAL_TEAMS_STATE", "TEAM_GOAL_GENERATION"), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("TEAM_SH_GOAL", "Team Short-Handed Goal", ["Team short-handed goal"], "TEAM",
               ("SPECIAL_TEAMS_STATE", "TEAM_GOAL_GENERATION"), "DISTRIBUTION_THRESHOLD", "REQUIRES_NEW_EXTRACTION", False, False),
    _generated("TEAM_EMPTY_NET_GOAL", "Team Empty-Net Goal", ["Team empty-net goal"], "TEAM",
               ("EMPTY_NET_STATE", "TEAM_GOAL_GENERATION"), "EVENT_TIME", "AVAILABLE_UNUSED", True, False,
               notes="Empty-net goals are NOT ordinary 5v5 scoring events (Part 26) -- requires game-state awareness of pulled goalies."),
    _generated("TEAM_PIM_OU", "Team Penalty Minutes O/U", ["Team penalty minutes"], "TEAM",
               ("PENALTY_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("TEAM_PENALTIES_OU", "Team Total Penalties O/U", ["Team total penalties"], "TEAM",
               ("PENALTY_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),
    _generated("TEAM_FACEOFF_WINS_OU", "Team Faceoff Wins O/U", ["Team faceoff wins"], "TEAM",
               ("FACEOFF_PROCESS",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, False),

    # ---- Game/outcome markets (final-score-only vs. genuinely event-time) ----
    _generated("GAME_TO_OT", "Game to Overtime", ["Game to overtime"], "GAME_OUTCOME",
               ("GAME_SCORE_STATE", "OT_SHOOTOUT_STATE"), "SIMULATION", "AVAILABLE_USED", False, True,
               notes="Derivable from a coherent joint (home_goals, away_goals) distribution alone -- does NOT require event timing, only final-score simulation."),
    _generated("GAME_TO_SHOOTOUT", "Game to Shootout", ["Game to shootout"], "GAME_OUTCOME",
               ("GAME_SCORE_STATE", "OT_SHOOTOUT_STATE"), "SIMULATION", "AVAILABLE_USED", False, True),
    *[_generated(f"METHOD_OF_VICTORY_{m.upper()}", f"Method of Victory: {m.title()}", [f"Method of victory: {m}"],
                 "GAME_OUTCOME", ("GAME_SCORE_STATE", "OT_SHOOTOUT_STATE"), "SIMULATION", "AVAILABLE_USED", False, True)
      for m in ("regulation", "overtime", "shootout")],
    _generated("WINNING_MARGIN", "Winning Margin", ["Winning margin", "Exact winning margin"], "GAME_OUTCOME",
               ("GAME_SCORE_STATE",), "SIMULATION", "AVAILABLE_USED", False, True,
               notes="A function of the joint (home_goals, away_goals) distribution alone -- final-score-only, not event-time."),
    _generated("EXACT_FINAL_SCORE", "Exact Final Score", ["Exact final score"], "GAME_OUTCOME",
               ("GAME_SCORE_STATE",), "SIMULATION", "AVAILABLE_USED", False, True),
    *[_generated(f"BOTH_TEAMS_{n}PLUS_GOALS" if n > 1 else "BOTH_TEAMS_TO_SCORE",
                 f"Both Teams {n}+ Goals" if n > 1 else "Both Teams to Score",
                 [f"Both teams {n}+ goals" if n > 1 else "Both teams to score"], "GAME_OUTCOME",
                 ("TEAM_GOAL_GENERATION",), "SIMULATION", "AVAILABLE_USED", False, True) for n in (1, 2, 3)],
    _generated("FIRST_GOAL_TIMING", "First Goal Timing", ["First goal timing"], "GAME_OUTCOME",
               ("PERIOD_EVENT_TIMING",), "EVENT_TIME", "AVAILABLE_UNUSED", True, True),
    *[_generated(f"FIRST_GOAL_METHOD_{m}", f"First Goal Method: {m}", [f"First goal method: {m}"], "GAME_OUTCOME",
                 ("SPECIAL_TEAMS_STATE", "PERIOD_EVENT_TIMING"), "EVENT_TIME", "AVAILABLE_UNUSED", True, True) for m in ("EV", "PP", "SH")],
    *[_generated(f"RACE_TO_{n}", f"Race to {n}", [f"Race to {n}"], "GAME_OUTCOME",
                 ("PERIOD_EVENT_TIMING",), "EVENT_TIME", "AVAILABLE_UNUSED", True, True) for n in (1, 2, 3, 4)],
    *[_generated(f"TEAM_LEAD_{a}_{b}", f"Team to Lead {a}-{b}", [f"Team to lead {a}-{b}"], "GAME_OUTCOME",
                 ("PERIOD_EVENT_TIMING", "GAME_SCORE_STATE"), "EVENT_TIME", "AVAILABLE_UNUSED", True, True) for a, b in ((1, 0), (2, 0))],
    _generated("LARGEST_LEAD", "Largest Lead", ["Largest lead"], "GAME_OUTCOME",
               ("PERIOD_EVENT_TIMING", "GAME_SCORE_STATE"), "EVENT_TIME", "AVAILABLE_UNUSED", True, True),
    _generated("LEAD_AFTER_EVERY_PERIOD", "Lead After Every Period", ["Lead after every period"], "GAME_OUTCOME",
               ("PERIOD_EVENT_TIMING",), "EVENT_TIME", "AVAILABLE_UNUSED", True, True),
    _generated("COME_FROM_BEHIND_WIN", "Come-From-Behind Win", ["Come-from-behind win"], "GAME_OUTCOME",
               ("PERIOD_EVENT_TIMING", "GAME_SCORE_STATE"), "EVENT_TIME", "AVAILABLE_UNUSED", True, True),
    _generated("ANY_EMPTY_NET_GOAL", "Any Empty-Net Goal", ["Any empty-net goal"], "GAME_OUTCOME",
               ("EMPTY_NET_STATE",), "EVENT_TIME", "AVAILABLE_UNUSED", True, True),
    _generated("ANY_SH_GOAL", "Any Short-Handed Goal", ["Any short-handed goal"], "GAME_OUTCOME",
               ("SPECIAL_TEAMS_STATE",), "DISTRIBUTION_THRESHOLD", "REQUIRES_NEW_EXTRACTION", False, True),
    _generated("ANY_PP_GOAL", "Any Power-Play Goal", ["Any power-play goal"], "GAME_OUTCOME",
               ("SPECIAL_TEAMS_STATE",), "DISTRIBUTION_THRESHOLD", "AVAILABLE_UNUSED", False, True),
    *[_generated(f"GAME_TOTAL_{stat.upper().replace(' ', '_')}", f"Game Total {stat}", [f"Total {stat.lower()}"],
                 "GAME_OUTCOME", (proc,), "DISTRIBUTION_THRESHOLD", status, False, False)
      for stat, proc, status in (
          ("PP Goals", "SPECIAL_TEAMS_STATE", "AVAILABLE_UNUSED"), ("Penalties", "PENALTY_PROCESS", "AVAILABLE_UNUSED"),
          ("PIM", "PENALTY_PROCESS", "AVAILABLE_UNUSED"), ("Shots", "TEAM_SHOT_GENERATION", "AVAILABLE_UNUSED_AS_STANDALONE_TARGET"),
          ("Saves", "GOALIE_WORKLOAD_SAVE_PROCESS", "AVAILABLE_UNUSED"), ("Blocked Shots", "PLAYER_BLOCK_EVENT_GENERATION", "AVAILABLE_UNUSED_AS_STANDALONE_TARGET"),
          ("Faceoffs", "FACEOFF_PROCESS", "AVAILABLE_UNUSED"))],

    # ---- Period markets: every one requires PBP by construction ----
    *[_generated(f"PERIOD_{i}_{stat.upper().replace(' ', '_')}", f"{stat} ({['1st','2nd','3rd'][i-1]} Period)",
                 [f"{stat} ({['1st','2nd','3rd'][i-1]} period)"], "PERIOD_MARKETS", ("PERIOD_EVENT_TIMING",),
                 "EVENT_TIME", "AVAILABLE_UNUSED", True, ("Both teams score" in stat or "Correct score" in stat))
      for i in (1, 2, 3)
      for stat in ("Player goal", "Player point", "Player SOG", "Team total goals", "Game total goals",
                   "Team to score", "First team to score", "Both teams score", "Correct score",
                   "Goalie saves", "Period winning margin")],
]


# Event-Timing Utility Closure slice, Part 23: PERIOD_1/2/3_GOALIE_SAVES
# were generated generically above (data_status="AVAILABLE_UNUSED", the
# same as every other period market) -- they are overridden here to
# "READY" specifically, because the mid-period-goalie-change reconstruction
# that used to block them now exists and is corpus-validated (5,248/5,248
# exact matches against the official /boxscore, 0 coherence violations --
# see EVENT_TIMING_UTILITY_CLOSURE_REPORT.md Section G/H). model_status
# stays NOT_BUILT (Part 23's explicit instruction -- no predictive model
# was built). dataclasses.replace() is used rather than editing the
# generator loop itself, since this READY status is specific to these 3
# markets, not to "Goalie saves" as a stat category in general.
from dataclasses import replace as _dc_replace  # noqa: E402

_PERIOD_GOALIE_SAVES_NOTE = (
    "Event-Timing Utility Closure slice: period-level goalie-save reconstruction is READY "
    "(research/real_nhl_pbp/{goalie_tenure,period_saves}.py), corpus-validated at 100% (5,248/5,248 "
    "exact full-game save matches, 0 period-to-full-game coherence violations). model_status remains "
    "NOT_BUILT -- no predictive model was built this slice."
)
_GENERATED_TAIL = [
    _dc_replace(m, historical_data_status="READY", notes=_PERIOD_GOALIE_SAVES_NOTE)
    if m.market_id in ("PERIOD_1_GOALIE_SAVES", "PERIOD_2_GOALIE_SAVES", "PERIOD_3_GOALIE_SAVES")
    else m
    for m in _GENERATED_TAIL
]


# Player SOG by Period slice: PERIOD_1/2/3_PLAYER_SOG were generated
# generically (model_status="NOT_BUILT") -- overridden here now that a
# real, corpus-validated predictive model exists (Candidate E, hybrid
# offset over the upstream full-game SOG model). historical_data_status
# moves AVAILABLE_UNUSED -> AVAILABLE_USED (matching the full-game SOG
# convention: data that is now genuinely used by a real model).
# threshold_validation_status is deliberately PER-PERIOD, not one blanket
# string: P1 validated at 1+/2+/3+ (all three tested thresholds cleared
# the bootstrap bar in both eval seasons); P2/P3 validated only at 1+/2+
# (3+ showed weak/inconsistent bootstrap evidence -- frac_improved as low
# as 0.28-0.8 in one eval season, below this project's adoption bar, so
# NOT claimed validated even though the point estimate favored it). 4+/5+
# were never tested at all -- pre-specified insufficient tail support
# (<300 positive events in the eval set, Part 26's rule) -- see
# PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md Sections Q-Y, AE.
_PERIOD_SOG_NOTE_TEMPLATE = (
    "PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md: winning model is Candidate E (hybrid offset -- upstream "
    "full-game SOG expectation, from the existing VALIDATED research.player_sog model recomputed PIT-safely "
    "per row, plus a small period-specific contextual adjustment). Beats the best PIT-safe baseline with "
    "frac_improved >= 0.98 (game- and date-clustered bootstrap) at 1+/2+ in every period, every eval season. "
    "{threshold_note} 4+/5+ never tested: <300 positive events in the eval set (pre-specified tail-support "
    "rule). Full-game coherence: sum(P1+P2+P3) exceeds the upstream full-game prediction by ~0.10 SOG on "
    "average (disclosed, not forced to zero). {confidence_note}"
)
_PERIOD_SOG_OVERRIDES = {
    "PERIOD_1_PLAYER_SOG": {
        "threshold_note": "3+ ALSO validated for period 1 specifically (frac_improved 0.999-1.000 both eval seasons).",
        "confidence_note": "LOW-confidence bucket shows non-negative skill in both eval seasons (+0.004, +0.017) -- unrestricted.",
        "threshold_validation_status": "VALIDATED_1PLUS_2PLUS_3PLUS", "low_confidence_policy": "NORMAL",
    },
    "PERIOD_2_PLAYER_SOG": {
        "threshold_note": "3+ NOT validated for period 2 (frac_improved dropped to 0.8 in the 2025-26 eval season).",
        "confidence_note": "LOW-confidence bucket showed mixed skill across eval seasons (-0.015, +0.037) -- left unrestricted (no repeated negative pattern).",
        "threshold_validation_status": "VALIDATED_1PLUS_2PLUS_ONLY", "low_confidence_policy": "NORMAL",
    },
    "PERIOD_3_PLAYER_SOG": {
        "threshold_note": "3+ NOT validated for period 3 (frac_improved only 0.28-0.58 across eval seasons).",
        "confidence_note": "LOW-confidence bucket showed NEGATIVE skill in BOTH eval seasons (-0.014, -0.016) -- "
                            "gated WATCH_ONLY via decision_policy.py v3 (PLAYER_SOG_PERIOD_3: WATCH).",
        "threshold_validation_status": "VALIDATED_1PLUS_2PLUS_ONLY", "low_confidence_policy": "WATCH_ONLY",
    },
}


def _apply_period_sog_override(m: MarketDefinition) -> MarketDefinition:
    override = _PERIOD_SOG_OVERRIDES.get(m.market_id)
    if override is None:
        return m
    note = _PERIOD_SOG_NOTE_TEMPLATE.format(threshold_note=override["threshold_note"],
                                             confidence_note=override["confidence_note"])
    return _dc_replace(
        m, historical_data_status="AVAILABLE_USED", model_status="VALIDATED",
        threshold_validation_status=override["threshold_validation_status"],
        confidence_status="CURRENT_FRAMEWORK", low_confidence_policy=override["low_confidence_policy"],
        conservative_probability_status="IMPLEMENTED", notes=note,
    )


_GENERATED_TAIL = [_apply_period_sog_override(m) for m in _GENERATED_TAIL]


# Team Goals by Period slice: a real model was built and evaluated
# (research/run_team_goals_period_model.py) for TEAM_PERIOD_1/2/3_TOTAL,
# their generic-tail duplicates PERIOD_1/2/3_TEAM_TOTAL_GOALS (both
# market_ids represent the same real-world market and must never diverge
# in status, per this project's own settlement-equivalence principle), and
# TEAM_SCORE_IN_PERIOD_1. UNLIKE Player SOG by Period, this is a real,
# disclosed NEGATIVE/NULL result: no candidate beat the strongest PIT-safe
# baseline with bootstrap evidence clearing this project's usual bar
# (frac_improved >= 0.95, consistent across BOTH eval seasons) at ANY
# period or threshold -- see TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md
# Sections N/AC/AD/AF for the full account, including a real xG-context
# add-on test (Part 8) that made P1 performance WORSE in one eval season
# (frac_improved 0.003) and did not help in the other. historical_data_
# status moves to AVAILABLE_USED (real data was genuinely used in a real,
# disciplined attempt) but model_status is "RESEARCH", not "VALIDATED" --
# an honest attempted-but-not-validated outcome, the same category this
# registry already uses for Hits.
_TEAM_GOALS_PERIOD_NOTE = (
    "TEAM_GOALS_BY_PERIOD_VALIDATION_REPORT.md: a real model (upstream full-game team-goal expectation "
    "x league-average period share + small offset-GLM adjustment, and a separate direct Poisson/NB GLM) "
    "was built and evaluated under strict walk-forward discipline. NEITHER beat the strongest PIT-safe "
    "baseline (full-game expectation x league-average share) with bootstrap evidence clearing this "
    "project's usual bar at any period/threshold, in both eval seasons simultaneously -- point-estimate "
    "differences were real but tiny (~0.0002-0.0007 mean Brier) and did not survive game- or "
    "date-clustered bootstrap scrutiny consistently. A real add-on test of MoneyPuck xG context (Part 8) "
    "made period-1 performance WORSE in the 2024-25 eval season (frac_improved 0.003) and did not clear "
    "the bar in 2025-26 either -- disclosed, not silently dropped. Real, disclosed home/away goal "
    "dependence exists and GROWS by period (raw correlation ~0 in P1, ~-0.05 to -0.07 in P2, ~-0.08 to "
    "-0.14 in P3) and is NOT explained away by the model (residual correlation nearly identical to raw) "
    "-- independence should not be assumed for future BTTS/correct-score pricing, especially in P3."
)
_TEAM_GOALS_PERIOD_MARKET_IDS = (
    "TEAM_PERIOD_1_TOTAL", "TEAM_PERIOD_2_TOTAL", "TEAM_PERIOD_3_TOTAL",
    "PERIOD_1_TEAM_TOTAL_GOALS", "PERIOD_2_TEAM_TOTAL_GOALS", "PERIOD_3_TEAM_TOTAL_GOALS",
    "TEAM_SCORE_IN_PERIOD_1",
)


def _apply_team_goals_period_override(m: MarketDefinition) -> MarketDefinition:
    if m.market_id not in _TEAM_GOALS_PERIOD_MARKET_IDS:
        return m
    return _dc_replace(
        m, historical_data_status="AVAILABLE_USED", model_status="RESEARCH",
        threshold_validation_status="ATTEMPTED_NOT_VALIDATED",
        confidence_status="CURRENT_FRAMEWORK", conservative_probability_status="IMPLEMENTED",
        notes=_TEAM_GOALS_PERIOD_NOTE,
    )


_HAND_SPECIFIED = [_apply_team_goals_period_override(m) for m in _HAND_SPECIFIED]
_GENERATED_TAIL = [_apply_team_goals_period_override(m) for m in _GENERATED_TAIL]


# Goalie Saves + Period Saves slice: a real, CONDITIONAL_ON_ACTUAL_START
# count model (upstream opponent-SOG-rolling-rate x hierarchically-shrunk
# goalie save% as a fixed offset, plus a small offset-GLM adjustment --
# "E_hybrid_offset", research/run_goalie_saves_model.py) was built and
# evaluated under strict walk-forward discipline. UNLIKE Team Goals by
# Period, this is a genuine MIXED result, not a uniform negative: 20+/25+
# saves cleared this project's usual bootstrap bar (frac_improved >= 0.95,
# game- and date-clustered, BOTH eval seasons: 20+ and 25+ both hit 1.000
# in both seasons). 30+ passed in 2024-25 (0.988) but collapsed in 2025-26
# (0.535) -- a real, disclosed PARTIAL/inconsistent result, not forced to
# either extreme. 35+ failed clearly in both seasons with real, adequate
# positive-event support (178-196/season) -- REJECTED, not just unproven.
# 40+ has thin positive-event support (39-48/season, below this slice's
# pre-specified 50-event floor) -- INSUFFICIENT_DATA, distinct from a
# tested-and-failed REJECTED verdict. Every status here is
# CONDITIONAL_ON_ACTUAL_START (Part 2/34): the existing, separately-
# audited projected-starter model (research/goalie_intelligence/) is
# referenced for live architecture, never folded into these numbers, and
# ACTUAL_STARTER is never used as a pregame feature -- see
# GOALIE_SAVES_VALIDATION_REPORT.md Section B.
_GOALIE_SAVES_NOTE_TEMPLATE = (
    "GOALIE_SAVES_VALIDATION_REPORT.md: winning model is a hybrid offset-GLM (opponent team SOG-rolling-rate "
    "x GOALIE->TEAM->LEAGUE hierarchically shrunk save%, as a fixed offset, plus a small contextual "
    "adjustment) -- beats the strongest PIT-safe baseline (opponent SOG-rolling-rate x league-average save%) "
    "on a genuinely mixed basis by threshold. {threshold_note} All numbers are CONDITIONAL_ON_ACTUAL_START -- "
    "the separately-audited projected-starter model is referenced for live pricing architecture but not "
    "re-fit or folded into these figures; ACTUAL_STARTER is never a pregame feature."
)
_GOALIE_SAVES_OVERRIDES = {
    "GOALIE_SAVES_20PLUS": {
        "threshold_note": "20+ VALIDATED: frac_improved=1.000 in both 2024-25 and 2025-26 (game- and "
                           "date-clustered bootstrap).",
        "model_status": "VALIDATED", "threshold_validation_status": "VALIDATED",
    },
    "GOALIE_SAVES_25PLUS": {
        "threshold_note": "25+ VALIDATED: frac_improved=1.000 in both eval seasons.",
        "model_status": "VALIDATED", "threshold_validation_status": "VALIDATED",
    },
    "GOALIE_SAVES_30PLUS": {
        "threshold_note": "30+ PARTIAL: frac_improved=0.988 in 2024-25 but only 0.535 in 2025-26 -- a real, "
                           "disclosed season-inconsistent result, not claimed validated.",
        "model_status": "PARTIAL", "threshold_validation_status": "PARTIAL_SEASON_INCONSISTENT",
    },
    "GOALIE_SAVES_35PLUS": {
        "threshold_note": "35+ REJECTED: frac_improved 0.02-0.41 in both eval seasons, with adequate positive "
                           "support (178-196 events/season) to conclude a real negative, not just insufficient data.",
        "model_status": "REJECTED", "threshold_validation_status": "REJECTED",
    },
    "GOALIE_SAVES_40PLUS": {
        "threshold_note": "40+ INSUFFICIENT_DATA: only 39-48 positive events/season, below this slice's "
                           "pre-specified 50-event support floor (Part 24).",
        "model_status": "INSUFFICIENT_DATA", "threshold_validation_status": "INSUFFICIENT_DATA",
    },
}
_PERIOD_GOALIE_SAVES_NOTE_TEMPLATE = (
    "GOALIE_SAVES_VALIDATION_REPORT.md: period saves compared a share-of-validated-full-game-model baseline "
    "against a direct per-period Poisson GLM candidate. {threshold_note} All figures CONDITIONAL_ON_ACTUAL_START."
)
_PERIOD_GOALIE_SAVES_OVERRIDES = {
    "PERIOD_1_GOALIE_SAVES": {
        "threshold_note": "P1: direct-Poisson beat the share baseline in 2025-26 (frac_improved=0.997) but not "
                           "2024-25 (0.831) -- PARTIAL, season-inconsistent.",
        "model_status": "PARTIAL", "threshold_validation_status": "PARTIAL_SEASON_INCONSISTENT",
    },
    "PERIOD_2_GOALIE_SAVES": {
        "threshold_note": "P2: direct-Poisson beat the share baseline in BOTH eval seasons "
                           "(frac_improved=0.973, 0.999) -- VALIDATED.",
        "model_status": "VALIDATED", "threshold_validation_status": "VALIDATED",
    },
    "PERIOD_3_GOALIE_SAVES": {
        "threshold_note": "P3: direct-Poisson beat the share baseline in 2025-26 (frac_improved=1.000) but not "
                           "2024-25 (0.763) -- PARTIAL, season-inconsistent.",
        "model_status": "PARTIAL", "threshold_validation_status": "PARTIAL_SEASON_INCONSISTENT",
    },
}


def _apply_goalie_saves_override(m: MarketDefinition) -> MarketDefinition:
    override = _GOALIE_SAVES_OVERRIDES.get(m.market_id)
    if override is not None:
        note = _GOALIE_SAVES_NOTE_TEMPLATE.format(threshold_note=override["threshold_note"])
        return _dc_replace(
            m, historical_data_status="AVAILABLE_USED", model_status=override["model_status"],
            threshold_validation_status=override["threshold_validation_status"],
            confidence_status="CURRENT_FRAMEWORK", conservative_probability_status="IMPLEMENTED",
            notes=note,
        )
    period_override = _PERIOD_GOALIE_SAVES_OVERRIDES.get(m.market_id)
    if period_override is not None:
        note = _PERIOD_GOALIE_SAVES_NOTE_TEMPLATE.format(threshold_note=period_override["threshold_note"])
        return _dc_replace(
            m, historical_data_status="AVAILABLE_USED", model_status=period_override["model_status"],
            threshold_validation_status=period_override["threshold_validation_status"],
            confidence_status="CURRENT_FRAMEWORK", conservative_probability_status="IMPLEMENTED",
            notes=note,
        )
    return m


_HAND_SPECIFIED = [_apply_goalie_saves_override(m) for m in _HAND_SPECIFIED]
_GENERATED_TAIL = [_apply_goalie_saves_override(m) for m in _GENERATED_TAIL]


# Team Shots on Goal slice: a real, freshly-reconciled team-game SOG corpus
# (research/team_sog/) and a direct Poisson GLM (research/run_team_sog_model.py)
# were built and evaluated under strict walk-forward discipline. TEAM_SOG_TOTAL
# is the ONE canonical market_id for this whole family (Part 44's explicit
# instruction: do not create separate alternate-threshold market_ids that
# would just be aliases of the same real target) -- this single entry's
# notes carry the per-threshold breakdown. Result: 20+/25+/30+/35+ saves
# clear this project's bootstrap adoption bar (frac_improved >= 0.95) in
# BOTH eval seasons; 40+ is season-inconsistent (0.999/0.693 game-clustered
# across the two seasons) despite adequate positive-event support
# (122-134/season, above the 50-event floor) -- a real PARTIAL result at
# that one extreme threshold, not folded into the overall VALIDATED verdict
# for the market as a whole (which real sportsbook lines, typically posted
# near the ~29-30 SOG mean, would sit well inside).
_TEAM_SOG_NOTE = (
    "TEAM_SOG_VALIDATION_REPORT.md: direct Poisson GLM (7 features: log-baseline team SOG, recent-form, "
    "home/away, log-opponent-SOG-allowed-factor, H2H shrunk delta, back-to-back indicator) beats the "
    "strongest PIT-safe baseline (plain rolling team SOG/game) with bootstrap frac_improved >= 0.95 "
    "(game- and date-clustered) at 20+/25+/30+/35+ in BOTH eval seasons. 40+ is season-inconsistent "
    "(2024-25: 0.999; 2025-26: 0.693) despite adequate support (122-134 positive events/season) -- a real "
    "PARTIAL result at that one extreme tail threshold. Offense/defense multiplicative decomposition and "
    "player-SOG roster aggregation were BOTH tested and underperformed the simple direct GLM (aggregation "
    "scored ~22% worse, consistent with the Goalie Saves slice's finding that this architecture "
    "underperforms direct team-level modeling -- re-confirmed here, not assumed). Poisson vs Negative "
    "Binomial: alpha=0.0097, near-zero, Poisson adequate; a Normal/Gaussian approximation was also tested "
    "and found materially indistinguishable from Poisson (not adopted, no material improvement). Real "
    "reconciliation: 99.58% exact match against the independently-built Player SOG corpus's team-game "
    "sums; 83.3% exact match against opposing-goalie shots-faced (the residual is always explained by "
    "empty-net shots, which correctly do not count as a goalie's shots faced)."
)


def _apply_team_sog_override(m: MarketDefinition) -> MarketDefinition:
    if m.market_id != "TEAM_SOG_TOTAL":
        return m
    return _dc_replace(
        m, historical_data_status="AVAILABLE_USED", model_status="VALIDATED",
        threshold_validation_status="VALIDATED_20PLUS_25PLUS_30PLUS_35PLUS_NOT_40PLUS",
        confidence_status="CURRENT_FRAMEWORK", conservative_probability_status="IMPLEMENTED",
        notes=_TEAM_SOG_NOTE,
    )


_HAND_SPECIFIED = [_apply_team_sog_override(m) for m in _HAND_SPECIFIED]
_GENERATED_TAIL = [_apply_team_sog_override(m) for m in _GENERATED_TAIL]


CANONICAL_MARKETS: list[MarketDefinition] = _HAND_SPECIFIED + _GENERATED_TAIL


def _validate_registry() -> None:
    ids = [m.market_id for m in CANONICAL_MARKETS]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate canonical market_id(s): {dupes}")
    for m in CANONICAL_MARKETS:
        for p in m.underlying_process:
            if p not in PROCESS_FAMILIES:
                raise ValueError(f"{m.market_id} references unknown process {p!r}")
        if m.derivation_type not in DERIVATION_TYPES:
            raise ValueError(f"{m.market_id} has unknown derivation_type {m.derivation_type!r}")


_validate_registry()


def get(market_id: str) -> MarketDefinition | None:
    return next((m for m in CANONICAL_MARKETS if m.market_id == market_id), None)


def by_process(process: str) -> list[MarketDefinition]:
    return [m for m in CANONICAL_MARKETS if process in m.underlying_process]


def total_canonical_markets() -> int:
    return len(CANONICAL_MARKETS)


_DERIVABLE_MODEL_STATUSES = (
    "VALIDATED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION", "INSUFFICIENT_DATA",
    "DERIVABLE_NOT_VALIDATED", "INSUFFICIENT_TAIL_DATA",
)


def derivable_today() -> list[MarketDefinition]:
    """Part M: mathematically DERIVABLE from an already-existing, already-
    FITTED model TODAY (one of the 5 already-built prop families: SOG,
    Blocks, Assists, Points, Goals), regardless of whether this SPECIFIC
    threshold was separately bootstrap-VALIDATED (Part 3's required
    distinction). Deliberately excludes "RESEARCH" markets (e.g. Hits) --
    confirmed data QUALITY there is not the same as an existing FITTED
    model that can produce a number today. Goalie Saves is no longer a
    uniform example here: the Goalie Saves + Period Saves slice gave it a
    real, mixed VALIDATED/PARTIAL/REJECTED/INSUFFICIENT_DATA status by
    threshold (see GOALIE_SAVES_VALIDATION_REPORT.md)."""
    return [m for m in CANONICAL_MARKETS if m.model_status in _DERIVABLE_MODEL_STATUSES]


def validated_today() -> list[MarketDefinition]:
    """Part N: genuinely VALIDATED (or, for Points, usable via its real
    champion-baseline status) -- never conflated with mere derivability."""
    return [m for m in CANONICAL_MARKETS
            if m.model_status in ("VALIDATED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION")
            and m.threshold_validation_status in ("VALIDATED", "USABLE_VIA_CHAMPION_BASELINE_NOT_A_VALIDATED_NEW_MODEL")]


def market_leverage_counts() -> dict[str, int]:
    """Part 33: for every process family, how many CANONICAL markets
    (not raw aliases) are gated on it via requires_play_by_play,
    requires_joint_simulation, or simply not yet built and needing that
    process as a first-class prerequisite."""
    counts: dict[str, int] = {p: 0 for p in PROCESS_FAMILIES}
    for m in CANONICAL_MARKETS:
        if m.model_status in ("VALIDATED", "EMPIRICAL_BASELINE_REMAINS_CHAMPION"):
            continue
        for p in m.underlying_process:
            counts[p] += 1
    return counts
