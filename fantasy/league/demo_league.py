"""
Deterministic DEMO league settings (Part 120/121) -- for product
walkthrough only when no real Yahoo account is connected. NEVER
presented as the owner's real league (Part 120's own explicit rule);
every dashboard surface that uses this shows a prominent DEMO badge.

The scoring weights and roster shape below are the REAL settings from
the owner's own actual Yahoo league (shared directly via a screenshot
of their real league settings page, 2026-09-01) -- reused here as a
concrete, real reference case rather than an invented example, but this
function's output is still a DEMO construct: it does not reflect this
league's actual current roster, standings, or matchup, none of which
are available without a real Yahoo connection.
"""
from __future__ import annotations

from fantasy.league.settings import LeagueSettings, RosterPositionSlot, StatCategory

DEMO_LEAGUE_KEY = "demo-hockey-19959"


def _skater_stat(stat_id: str, name: str, display_name: str, modifier: float) -> StatCategory:
    return StatCategory(stat_id=stat_id, name=name, display_name=display_name,
                         position_type="P", enabled=True, modifier=modifier)


def _goalie_stat(stat_id: str, name: str, display_name: str, modifier: float) -> StatCategory:
    return StatCategory(stat_id=stat_id, name=name, display_name=display_name,
                         position_type="G", enabled=True, modifier=modifier)


def build_demo_league_settings() -> LeagueSettings:
    roster_positions = (
        RosterPositionSlot("C", "P", 2), RosterPositionSlot("LW", "P", 2),
        RosterPositionSlot("RW", "P", 2), RosterPositionSlot("D", "P", 4),
        RosterPositionSlot("Util", "P", 2), RosterPositionSlot("G", "G", 2),
        RosterPositionSlot("BN", None, 4), RosterPositionSlot("IR", None, 2),
        RosterPositionSlot("IR+", None, 2),
    )
    stat_categories = (
        _skater_stat("1", "Goals", "Goals (G)", 4.5),
        _skater_stat("2", "Assists", "Assists (A)", 3.0),
        _skater_stat("3", "Penalty Minutes", "Penalty Minutes (PIM)", 0.5),
        _skater_stat("4", "Powerplay Points", "Powerplay Points (PPP)", 0.5),
        _skater_stat("5", "Shots on Goal", "Shots on Goal (SOG)", 0.5),
        _skater_stat("6", "Hits", "Hits (HIT)", 0.5),
        _skater_stat("7", "Blocked Shots", "Blocks (BLK)", 0.75),
        _goalie_stat("8", "Wins", "Wins (W)", 4.5),
        _goalie_stat("9", "Goals Against", "Goals Against (GA)", -1.5),
        _goalie_stat("10", "Saves", "Saves (SV)", 0.3),
        _goalie_stat("11", "Shutouts", "Shutouts (SHO)", 4.5),
    )
    return LeagueSettings(
        league_key=DEMO_LEAGUE_KEY, scoring_type="point", is_points_league=True,
        roster_positions=roster_positions, stat_categories=stat_categories,
        waiver_type="R", uses_faab=False, num_playoff_teams=6, playoff_start_week="24",
        trade_end_date=None, max_teams=None,
    )
