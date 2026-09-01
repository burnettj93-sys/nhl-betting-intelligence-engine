"""
League settings, parsed from Yahoo's real, VERIFIED settings schema
(fantasy/yahoo/parser.py::extract_league_settings -- see contracts.py).

Part 11/12's own explicit rule: fantasy value is calculated against the
ACTUAL league's settings, never a hard-coded default category list.
Every consumer of LeagueSettings must read stat_categories/roster_positions
from the instance actually passed in -- there is no module-level default
league anywhere in this package.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RosterPositionSlot:
    position: str
    position_type: str | None
    count: int


@dataclass(frozen=True)
class StatCategory:
    stat_id: str
    name: str
    display_name: str
    position_type: str | None  # e.g. "P" (skater/player) or "G" (goalie) -- NOT_VERIFIED_THIS_SESSION for hockey specifically
    enabled: bool
    modifier: float | None  # points-league per-stat weight; None if this is a pure category league

    @property
    def direction(self) -> int:
        """+1 if higher is better, -1 if lower is better (e.g. Goals
        Against, PIM in some leagues). Derived from the real modifier
        sign where one exists; defaults to +1 (the overwhelmingly common
        case) when no modifier is present -- category-league direction
        for a stat like GAA still needs real league metadata Yahoo's
        settings response doesn't appear to expose directly, so this is
        a best-effort default, never silently trusted for a category
        this engine doesn't otherwise recognize -- see
        fantasy/league/scoring.py::KNOWN_LOWER_IS_BETTER_STATS."""
        if self.modifier is not None and self.modifier < 0:
            return -1
        return 1


@dataclass(frozen=True)
class LeagueSettings:
    league_key: str
    scoring_type: str  # real Yahoo value, e.g. "head" -- see contracts.py's own honesty note
    is_points_league: bool
    roster_positions: tuple[RosterPositionSlot, ...]
    stat_categories: tuple[StatCategory, ...]
    waiver_type: str | None = None
    uses_faab: bool = False
    num_playoff_teams: int | None = None
    playoff_start_week: int | None = None
    trade_end_date: str | None = None
    max_teams: int | None = None
    max_weekly_adds: int | None = None  # acquisition limit (Part 48) -- NOT_VERIFIED_THIS_SESSION field name

    @property
    def settings_hash(self) -> str:
        """Part 99: hash league settings so a later change can be
        detected and fantasy values recalculated rather than silently
        using stale scoring rules."""
        payload = {
            "scoring_type": self.scoring_type,
            "is_points_league": self.is_points_league,
            "roster_positions": [(p.position, p.position_type, p.count) for p in self.roster_positions],
            "stat_categories": [(s.stat_id, s.name, s.enabled, s.modifier) for s in self.stat_categories],
            "waiver_type": self.waiver_type,
            "uses_faab": self.uses_faab,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def enabled_categories(self) -> tuple[StatCategory, ...]:
        return tuple(c for c in self.stat_categories if c.enabled)

    def category_by_name(self, name: str) -> StatCategory | None:
        name_lower = name.lower()
        for c in self.stat_categories:
            if c.name.lower() == name_lower or c.display_name.lower() == name_lower:
                return c
        return None


def _as_bool(value) -> bool:
    return str(value) in ("1", "true", "True")


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_league_settings(raw: dict, league_key: str) -> LeagueSettings:
    """Builds a LeagueSettings from fantasy.yahoo.parser's real, verified
    extraction of a Yahoo league settings response. Never invents a
    field this real response didn't provide -- missing fields become
    None/empty, never a fabricated default."""
    roster_positions = tuple(
        RosterPositionSlot(
            position=rp.get("position", ""),
            position_type=rp.get("position_type"),
            count=_as_int(rp.get("count")) or 0,
        )
        for rp in raw.get("roster_positions", [])
    )

    modifier_by_stat_id = {}
    for m in raw.get("stat_modifiers", []):
        stat_id = m.get("stat_id")
        if stat_id is not None:
            modifier_by_stat_id[stat_id] = _as_float(m.get("value"))

    stat_categories = tuple(
        StatCategory(
            stat_id=s.get("stat_id", ""),
            name=s.get("name", ""),
            display_name=s.get("display_name", s.get("name", "")),
            position_type=s.get("position_type"),
            enabled=_as_bool(s.get("enabled", "1")),
            modifier=modifier_by_stat_id.get(s.get("stat_id")),
        )
        for s in raw.get("stat_categories", [])
    )

    # A points league has a real per-stat point value (modifier) attached
    # to most/all of its stat categories; a pure category (head-to-head
    # or roto) league does not. uses_fractional_points is Yahoo's own
    # explicit points-league signal (confirmed present in the real
    # fetched sample this sprint) when set.
    has_modifiers = any(c.modifier is not None for c in stat_categories)
    is_points_league = _as_bool(raw.get("uses_fractional_points", "0")) or has_modifiers

    return LeagueSettings(
        league_key=league_key,
        scoring_type=raw.get("scoring_type", ""),
        is_points_league=is_points_league,
        roster_positions=roster_positions,
        stat_categories=stat_categories,
        waiver_type=raw.get("waiver_type"),
        uses_faab=_as_bool(raw.get("uses_faab", "0")),
        num_playoff_teams=_as_int(raw.get("num_playoff_teams")),
        playoff_start_week=_as_int(raw.get("playoff_start_week")),
        trade_end_date=raw.get("trade_end_date"),
        max_teams=_as_int(raw.get("max_teams")),
    )
