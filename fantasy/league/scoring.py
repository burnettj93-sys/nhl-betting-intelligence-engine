"""
League-aware fantasy scoring engine + the Yahoo stat -> internal NHL
stat mapping table (Part 14). Supports points leagues (weighted sum)
and category leagues (head-to-head or rotisserie -- no single scalar,
per-category comparison instead), per Part 13.

Part 15's own rule: if this engine's stat mapping says
PROJECTION_NOT_AVAILABLE for a category the league actually scores,
recommendations must acknowledge reduced completeness -- never silently
drop the category or fabricate a number for it.
"""
from __future__ import annotations

from dataclasses import dataclass

from fantasy.league.settings import LeagueSettings, StatCategory

# Yahoo stat display names (as seen on Yahoo's own settings/scoring UI,
# confirmed via the owner's real league screenshot this sprint for the
# hockey-specific set, and Yahoo's general stat-category convention for
# the rest) -> (internal stat key, model support level). "internal stat
# key" matches this engine's own real, existing prop families
# (dashboard/demo_data.py::PROP_MARKET_FAMILY and the goalie saves
# engine) wherever a real model exists.
#
# MODEL_SUPPORT values:
#   VALIDATED             -- this engine has a real, validated per-game
#                             projection model for this exact stat
#   PARTIAL                -- a real model exists but coverage/validation
#                             is incomplete (e.g. only some thresholds)
#   NOT_AVAILABLE           -- no projection model exists; Part 15 status
#                             (HISTORICAL_ONLY or PROJECTION_NOT_AVAILABLE)
#                             applies, never fabricated
YAHOO_STAT_NAME_TO_INTERNAL = {
    "Goals": ("goals", "VALIDATED"),
    "Assists": ("assists", "VALIDATED"),
    "Points": ("points", "PARTIAL"),  # EMPIRICAL_BASELINE_REMAINS_CHAMPION in research/model_registry.py, not VALIDATED
    "Shots on Goal": ("sog", "VALIDATED"),
    "SOG": ("sog", "VALIDATED"),
    "Blocked Shots": ("blocks", "VALIDATED"),
    "Blocks": ("blocks", "VALIDATED"),
    "Powerplay Points": ("pp_points", "NOT_AVAILABLE"),
    "PPP": ("pp_points", "NOT_AVAILABLE"),
    "Powerplay Goals": ("pp_goals", "NOT_AVAILABLE"),
    "Powerplay Assists": ("pp_assists", "NOT_AVAILABLE"),
    "Penalty Minutes": ("pim", "NOT_AVAILABLE"),
    "PIM": ("pim", "NOT_AVAILABLE"),
    "Hits": ("hits", "NOT_AVAILABLE"),
    "Plus/Minus": ("plus_minus", "NOT_AVAILABLE"),
    "+/-": ("plus_minus", "NOT_AVAILABLE"),
    "Faceoffs Won": ("faceoffs_won", "NOT_AVAILABLE"),
    "Game Winning Goals": ("game_winning_goals", "NOT_AVAILABLE"),
    "Short Handed Points": ("sh_points", "NOT_AVAILABLE"),
    "Wins": ("goalie_wins", "NOT_AVAILABLE"),
    "W": ("goalie_wins", "NOT_AVAILABLE"),
    "Goals Against": ("goalie_ga", "NOT_AVAILABLE"),
    "GA": ("goalie_ga", "NOT_AVAILABLE"),
    "Goals Against Average": ("goalie_gaa", "NOT_AVAILABLE"),
    "GAA": ("goalie_gaa", "NOT_AVAILABLE"),
    "Saves": ("goalie_saves", "VALIDATED"),
    "SV": ("goalie_saves", "VALIDATED"),
    "Save Percentage": ("goalie_sv_pct", "NOT_AVAILABLE"),
    "SV%": ("goalie_sv_pct", "NOT_AVAILABLE"),
    "Shutouts": ("goalie_shutouts", "NOT_AVAILABLE"),
    "SHO": ("goalie_shutouts", "NOT_AVAILABLE"),
}

# Stats where a LOWER value is fantasy-better -- used only as a fallback
# when a category's own real Yahoo `modifier` sign doesn't already make
# this unambiguous (see StatCategory.direction). Goals Against Average
# and Goals Against are the clear, uncontroversial real-world examples;
# this list is deliberately short rather than guessed-at broadly.
KNOWN_LOWER_IS_BETTER_STATS = {"goalie_gaa", "goalie_ga"}


def map_stat_category(category: StatCategory) -> tuple[str | None, str]:
    """Returns (internal_stat_key, model_support_level). internal_stat_key
    is None if this Yahoo stat isn't in the mapping table at all (an
    even more unusual/league-specific stat) -- callers treat that
    exactly like NOT_AVAILABLE, never guess a mapping."""
    entry = YAHOO_STAT_NAME_TO_INTERNAL.get(category.display_name) or YAHOO_STAT_NAME_TO_INTERNAL.get(category.name)
    if entry is None:
        return None, "NOT_AVAILABLE"
    return entry


@dataclass(frozen=True)
class CategoryProjection:
    internal_stat: str
    projected_value: float | None
    confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    status: str  # "PROJECTED" | "HISTORICAL_ONLY" | "PROJECTION_NOT_AVAILABLE"


def points_value(settings: LeagueSettings, projections: dict[str, CategoryProjection]) -> dict:
    """Points-league scoring: sum(projected_value * modifier) over every
    ENABLED category this league scores. Returns a breakdown, never just
    a bare number, so callers can show completeness (Part 15). Returns
    is_points_league=False (and total=None) if this isn't a points
    league -- callers must use category_contributions() instead."""
    if not settings.is_points_league:
        return {"is_points_league": False, "total": None, "breakdown": [], "missing_categories": []}

    total = 0.0
    breakdown = []
    missing = []
    for cat in settings.enabled_categories():
        internal_stat, support = map_stat_category(cat)
        proj = projections.get(internal_stat) if internal_stat else None
        if proj is None or proj.status != "PROJECTED" or proj.projected_value is None:
            missing.append({"yahoo_stat": cat.display_name, "internal_stat": internal_stat,
                             "model_support": support,
                             "status": proj.status if proj else "PROJECTION_NOT_AVAILABLE"})
            continue
        modifier = cat.modifier if cat.modifier is not None else 1.0
        contribution = proj.projected_value * modifier
        total += contribution
        breakdown.append({"yahoo_stat": cat.display_name, "internal_stat": internal_stat,
                           "projected_value": proj.projected_value, "modifier": modifier,
                           "contribution": contribution, "confidence": proj.confidence})
    return {"is_points_league": True, "total": total, "breakdown": breakdown, "missing_categories": missing}


def category_contributions(settings: LeagueSettings, projections: dict[str, CategoryProjection]) -> dict:
    """Category-league scoring: per-category projected values with
    direction, never collapsed into one scalar (Part 36's own rule --
    do not maximize generic total value only in a category league)."""
    contributions = []
    missing = []
    for cat in settings.enabled_categories():
        internal_stat, support = map_stat_category(cat)
        proj = projections.get(internal_stat) if internal_stat else None
        direction = -1 if internal_stat in KNOWN_LOWER_IS_BETTER_STATS else cat.direction
        if proj is None or proj.status != "PROJECTED" or proj.projected_value is None:
            missing.append({"yahoo_stat": cat.display_name, "internal_stat": internal_stat,
                             "model_support": support,
                             "status": proj.status if proj else "PROJECTION_NOT_AVAILABLE"})
            continue
        contributions.append({"yahoo_stat": cat.display_name, "internal_stat": internal_stat,
                               "projected_value": proj.projected_value, "direction": direction,
                               "confidence": proj.confidence})
    return {"is_points_league": False, "contributions": contributions, "missing_categories": missing}
