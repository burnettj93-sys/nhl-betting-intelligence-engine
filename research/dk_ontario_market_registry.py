"""
DraftKings-Ontario-Aligned Parlay Roadmap block (2026-09-29), Phase B: a
formal TARGET-BOOK market registry, separate from and layered ABOVE the
existing model/provider-contract registries (research/player_props/
market_registry.py, research/model_registry.py). Neither of those answers
"does DraftKings Ontario actually sell this as a bettable market" -- this
one does, from the owner's own manually-verified live screenshots of the
real DK Ontario NHL menu (2026-09-29), not from any assumption.

New gating order this block establishes (Part: "IMPORTANT CORRECTION"):
    MODEL STATUS -> TARGET_BOOK_AVAILABLE -> LIVE PROVIDER CONTRACT ->
    INGESTION -> IDENTITY -> SETTLEMENT -> CONTEXT GATE -> PARLAY ELIGIBLE

A market clearing every OTHER gate but not offered by the target book is
not a parlay candidate, no matter how good the model is (Hits/Blocks).
Conversely, a market the book clearly sells is NOT auto-eligible either --
every other gate must still clear independently.

TARGET_BOOK_STATUS values (never invented per-market without evidence):
  VERIFIED_DK_ON            -- confirmed present in the owner's screenshots
  NOT_CURRENTLY_OFFERED_DK_ON -- confirmed absent (Hits, Blocks) -- NOT the
                                same claim as "unsupported globally"; DK may
                                add it later, other books may already have it
  UNKNOWN                   -- not yet checked against the screenshots

This module is a pure data registry (no model/provider-contract fields
duplicated here -- those stay authoritative in their own modules; join on
market_family/threshold when building the combined eligibility matrix).
"""
from __future__ import annotations

from dataclasses import dataclass, field

VERIFIED_DK_ON = "VERIFIED_DK_ON"
NOT_CURRENTLY_OFFERED_DK_ON = "NOT_CURRENTLY_OFFERED_DK_ON"
UNKNOWN = "UNKNOWN"

TARGET_BOOK_STATUSES = (VERIFIED_DK_ON, NOT_CURRENTLY_OFFERED_DK_ON, UNKNOWN)


@dataclass
class DKMarketEntry:
    market_family: str
    dk_display_name: str
    target_book_available: str
    sgp_available: bool | None            # None = not observed/unknown from the screenshots
    observed_structure: str
    observed_thresholds: tuple = field(default_factory=tuple)
    tier: int = 1                          # this block's own Tier 1/2/3 build-priority grouping
    notes: str = ""


# ============================================================================
# GAME markets
# ============================================================================
GAME_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry("MONEYLINE", "Moneyline", VERIFIED_DK_ON, True, "3-way home/draw-none/away", tier=1,
                  notes="Already production-ready (T-35 pipeline); the only market with a "
                        "code-level VERIFIED_CONTRACTS entry today."),
    DKMarketEntry("PUCK_LINE", "Puck Line", VERIFIED_DK_ON, True, "spread, typically +/-1.5", tier=1,
                  notes="Not a simple moneyline transformation -- needs its own historical "
                        "label/settlement definition before modeling (Phase I)."),
    DKMarketEntry("GAME_TOTAL", "Main Total", VERIFIED_DK_ON, True, "single main total line", tier=1),
    DKMarketEntry("ALTERNATE_GAME_TOTAL", "Alternate Total", VERIFIED_DK_ON, True,
                  "full ladder, observed 3.5-8.5", (3.5, 4.5, 5.5, 6.5, 7.5, 8.5), tier=1),
    DKMarketEntry("ALTERNATE_PUCK_LINE", "Alternate Puck Line", VERIFIED_DK_ON, True, "spread ladder", tier=2),
    DKMarketEntry("SIXTY_MIN_LINE", "60 Min Line", VERIFIED_DK_ON, True,
                  "regulation-only moneyline (OT/SO excluded)", tier=1,
                  notes="Needs its own regulation-only settlement definition, distinct from the "
                        "full-game moneyline resolver."),
    DKMarketEntry("TIE_NO_BET", "Tie No Bet", VERIFIED_DK_ON, None, "void on a tie after regulation", tier=2),
    DKMarketEntry("MONEYLINE_TOTAL_COMBO", "Moneyline / Total Goals", VERIFIED_DK_ON, True, "combo market", tier=3),
    DKMarketEntry("PUCK_LINE_TOTAL_COMBO", "Puck Line / Total Goals", VERIFIED_DK_ON, True, "combo market", tier=3),
    DKMarketEntry("FIRST_PERIOD_END_REG_COMBO", "1st Period / End of Regulation", VERIFIED_DK_ON, True,
                  "combo market", tier=3),
]

# ============================================================================
# PLAYER markets
# ============================================================================
PLAYER_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry("PLAYER_SOG", "Shots on Goal", VERIFIED_DK_ON, True,
                  "milestone ladder", (1, 2, 3, 4, 5), tier=1,
                  notes="Observed live: Matthews 4+, Caufield 4+, Nylander 3+, lower-volume "
                        "players 2+ -- ladder length scales with player volume."),
    DKMarketEntry("PLAYER_SOG_OU", "Shots on Goal O/U", VERIFIED_DK_ON, True, "half-line O/U", tier=1),
    DKMarketEntry("PLAYER_SOG_P1", "Shots on Goal - 1st Period", VERIFIED_DK_ON, True, "period-scoped", tier=2),
    DKMarketEntry("PLAYER_POINTS", "Points", VERIFIED_DK_ON, True, "milestone ladder", (1, 2, 3), tier=1),
    DKMarketEntry("PLAYER_POINTS_OU", "Points O/U", VERIFIED_DK_ON, True, "half-line O/U", tier=1),
    DKMarketEntry("PLAYER_POINTS_P1", "Points - 1st Period", VERIFIED_DK_ON, True, "period-scoped", tier=2),
    DKMarketEntry("PLAYER_ASSISTS", "Assists", VERIFIED_DK_ON, True, "milestone ladder", (1, 2), tier=1),
    DKMarketEntry("PLAYER_ASSISTS_P1", "Assists - 1st Period", VERIFIED_DK_ON, True, "period-scoped", tier=2),
    DKMarketEntry("ANYTIME_GOALSCORER", "Anytime Goalscorer", VERIFIED_DK_ON, True, "event odds", tier=2),
    DKMarketEntry("FIRST_GOALSCORER", "First Goalscorer", VERIFIED_DK_ON, True, "event-time, field market", tier=3),
    DKMarketEntry("GOALSCORER_P1", "Goalscorer - 1st Period", VERIFIED_DK_ON, True, "period-scoped event odds", tier=3),
    # ---- explicit DK-Ontario exclusions (Part: HITS / BLOCKS CORRECTION) ----
    DKMarketEntry("PLAYER_HITS", "(not on DK Ontario menu)", NOT_CURRENTLY_OFFERED_DK_ON, None,
                  "n/a", tier=3,
                  notes="Model VALIDATED (1+/2+/5+) and ingestion built (research/Stat Ingestion "
                        "Enablement blocks) -- kept for research/registry/diagnostics/future "
                        "sportsbook support. PARLAY_EXCLUDED_DK_ON: no near-term dev effort to "
                        "operationalize for the DK Ontario parlay product. Revisit if DK adds it."),
    DKMarketEntry("PLAYER_BLOCKS", "(not on DK Ontario menu)", NOT_CURRENTLY_OFFERED_DK_ON, None,
                  "n/a", tier=3,
                  notes="Model VALIDATED (1+/2+/3+, pending re-check on the repaired corpus) and "
                        "ingestion built -- same PARLAY_EXCLUDED_DK_ON treatment as Hits. Also has "
                        "its own separate, real methodology blocker (boxscore-vs-MoneyPuck drift) "
                        "even setting DK availability aside."),
]

# ============================================================================
# GOALIE markets
# ============================================================================
GOALIE_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry("GOALIE_SAVES", "Saves milestone ladders", VERIFIED_DK_ON, True,
                  "milestone ladder, observed 24+/26+/28+/30+/32+/34+",
                  (24, 26, 28, 30, 32, 34), tier=1,
                  notes="Existing research (20+/25+) does NOT cover the real DK ladder -- 24+/26+/"
                        "28+/32+/34+ are untested; only 30+ overlaps loosely with prior PARTIAL "
                        "research (which tested 30+ as a milestone, not DK's exact line)."),
    DKMarketEntry("GOALIE_SAVES_OU", "Saves O/U", VERIFIED_DK_ON, True,
                  "half-line, observed 24.5/26.5+", (24.5, 26.5), tier=1,
                  notes="A milestone N+ and a half-line (N-0.5) O/U over the SAME count "
                        "distribution are the mathematically IDENTICAL event (P(saves>=N) == "
                        "P(saves > N-0.5)) -- one validation covers both representations; never "
                        "double-counted as two separate studies."),
    DKMarketEntry("GOALIE_SHUTOUT", "Shutout / team to win with shutout", VERIFIED_DK_ON, True,
                  "event odds", tier=2),
]

# ============================================================================
# TEAM markets
# ============================================================================
TEAM_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry("TEAM_TOTAL", "Team Total Goals", VERIFIED_DK_ON, True, "single main line", tier=1),
    DKMarketEntry("ALTERNATE_TEAM_TOTAL", "Alternate Team Total Goals", VERIFIED_DK_ON, True,
                  "full ladder, observed 0.5-5.5", (0.5, 1.5, 2.5, 3.5, 4.5, 5.5), tier=1,
                  notes="research/generic_prop_pricing archives already have 31 REAL, confirmed "
                        "observations of this exact market key (alternate_team_totals) -- see "
                        "PROVIDER CONTRACT RECONCILIATION section of this block's report. Top "
                        "model priority per this block."),
    DKMarketEntry("BOTH_TEAMS_TO_SCORE", "Both Teams to Score", VERIFIED_DK_ON, True, "yes/no", tier=2),
    DKMarketEntry("BOTH_TEAMS_TO_SCORE_2PLUS", "Both Teams to Score 2+ Goals", VERIFIED_DK_ON, True,
                  "yes/no, higher bar", tier=2),
]

# ============================================================================
# PERIOD markets
# ============================================================================
PERIOD_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry("P1_PUCK_LINE", "1st Period Puck Line", VERIFIED_DK_ON, True, "period-scoped spread", tier=2),
    DKMarketEntry("P1_TOTAL", "1st Period Total", VERIFIED_DK_ON, True, "period-scoped total", tier=2),
    DKMarketEntry("P1_TIE_NO_BET", "1st Period Tie No Bet", VERIFIED_DK_ON, None, "void on P1 tie", tier=2),
    DKMarketEntry("TEAM_TOTAL_LISTED_PERIOD", "Team Totals - Listed Period", VERIFIED_DK_ON, True,
                  "period-scoped team total", tier=2),
    DKMarketEntry("CORRECT_SCORE_LISTED_PERIOD", "Correct Score - Listed Period", VERIFIED_DK_ON, True,
                  "exact score, period-scoped", tier=3),
    DKMarketEntry("ALT_TOTAL_LISTED_PERIOD", "Alt Total Goals - Listed Period", VERIFIED_DK_ON, True,
                  "period-scoped alt ladder", tier=3),
    DKMarketEntry("BOTH_TEAMS_SCORE_P1", "Both Teams to Score - 1st Period", VERIFIED_DK_ON, True,
                  "period-scoped yes/no", tier=2),
    DKMarketEntry("GOAL_IN_FIRST_10_LISTED_PERIOD", "Goal in First 10 - Listed Period", VERIFIED_DK_ON, True,
                  "event-time, period-scoped", tier=3),
]

# ============================================================================
# GAME PROPS / SPECIALTY (Tier 3 -- explicitly deprioritized this block)
# ============================================================================
SPECIALTY_MARKETS: list[DKMarketEntry] = [
    DKMarketEntry(mid, name, VERIFIED_DK_ON, True, "specialty/field market", tier=3)
    for mid, name in (
        ("FIRST_LAST_TO_SCORE", "First/Last to Score"), ("CORRECT_SCORE", "Correct Score"),
        ("EXACT_GOALS", "Exact Goals"), ("EXACT_TEAM_GOALS", "Exact Team Goals"),
        ("OVERTIME", "Overtime"), ("WINNING_MARGIN", "Winning Margin"),
        ("WIN_FROM_BEHIND", "Win From Behind"), ("RACE_TO_X_GOALS", "Race to X Goals"),
        ("FIRST_TO_5_SOG", "First to 5 Shots on Goal"), ("FIRST_SOG", "First Shot on Goal"),
        ("OPENING_FACEOFF_WINNER", "Opening Faceoff Winner"), ("FIRST_PP", "First Power Play"),
        ("FIRST_GOAL_STRENGTH", "First Goal Strength"), ("FIRST_TEAM_GOAL_STRENGTH", "First Team Goal Strength"),
        ("TEAM_FIRST_PP", "Team First Power Play"), ("SHOTS_FIRST_2MIN", "Shots in First Two Minutes"),
        ("FIRST_GOAL_EXACT", "First Goal Exact"),
    )
]

ALL_DK_MARKETS: list[DKMarketEntry] = (
    GAME_MARKETS + PLAYER_MARKETS + GOALIE_MARKETS + TEAM_MARKETS + PERIOD_MARKETS + SPECIALTY_MARKETS
)


def _validate_registry() -> None:
    ids = [m.market_family for m in ALL_DK_MARKETS]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate DK market_family id(s): {dupes}")
    for m in ALL_DK_MARKETS:
        if m.target_book_available not in TARGET_BOOK_STATUSES:
            raise ValueError(f"{m.market_family} has unknown target_book_available {m.target_book_available!r}")
        if m.tier not in (1, 2, 3):
            raise ValueError(f"{m.market_family} has unknown tier {m.tier!r}")


_validate_registry()


def get(market_family: str) -> DKMarketEntry | None:
    return next((m for m in ALL_DK_MARKETS if m.market_family == market_family), None)


def by_target_book_status(status: str) -> list[DKMarketEntry]:
    return [m for m in ALL_DK_MARKETS if m.target_book_available == status]


def by_tier(tier: int) -> list[DKMarketEntry]:
    return [m for m in ALL_DK_MARKETS if m.tier == tier]


def excluded_from_dk_parlay() -> list[DKMarketEntry]:
    return by_target_book_status(NOT_CURRENTLY_OFFERED_DK_ON)
