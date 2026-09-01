"""
Parses Yahoo Fantasy Sports API XML responses into plain dicts/lists.

Generic, structure-preserving XML->dict conversion (no per-resource
special-casing at the parse layer) plus a handful of typed extraction
helpers for the resources this sprint actually uses. The generic parser
means an unverified resource (Team/Roster/Player -- see contracts.py)
still parses correctly structurally; only the FIELD NAMES used by the
typed extractors below carry a verified/not-verified distinction.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

_NS_PREFIX = "{https://fantasysports.yahooapis.com/fantasy/v2/base.rng}"


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def _element_to_value(el: ET.Element):
    """Recursively converts one XML element to a dict (for elements with
    children), a string (for leaf elements with text), or None (for an
    empty leaf, e.g. <weekly_deadline/>). Repeated child tags collapse
    into a list, matching Yahoo's own collection convention."""
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        return text if text else None

    result: dict = {}
    for child in children:
        tag = _strip_ns(child.tag)
        value = _element_to_value(child)
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result


def parse_fantasy_content(xml_text: str) -> dict:
    """Top-level entry point: parses a full <fantasy_content>...
    response into a dict keyed by the top-level resource name (e.g.
    "game", "league", "team"). Raises ValueError on malformed XML --
    callers must catch this (never let a malformed Yahoo response
    propagate as a raw traceback, per Part 115)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML from Yahoo Fantasy API: {exc}") from None
    value = _element_to_value(root)
    # The top-level result must always be a dict, even for a genuinely
    # empty <fantasy_content/> -- every extract_* helper below calls
    # .get() on this return value; returning None here (the same
    # convention used for an empty LEAF element, e.g. <weekly_deadline/>)
    # would make every one of them crash with AttributeError on a
    # partial/empty real response (Part 115: fail gracefully, never a
    # raw crash).
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------
# Typed extraction helpers -- VERIFIED field names (see contracts.py)
# ---------------------------------------------------------------------

def extract_game(parsed: dict) -> dict:
    """VERIFIED shape (contracts.py). Returns the <game> element's own
    fields as a flat dict."""
    game = parsed.get("game")
    if isinstance(game, list):
        game = game[0]
    return game or {}


def extract_league_metadata(parsed: dict) -> dict:
    """VERIFIED shape. The <league> element's own top-level fields,
    excluding the nested <settings>/<standings> sub-resources."""
    league = parsed.get("league") or {}
    return {k: v for k, v in league.items() if k not in ("settings", "standings")}


def extract_league_settings(parsed: dict) -> dict:
    """VERIFIED shape: draft_type, scoring_type, roster_positions
    (list of {position, position_type, count}), stat_categories.stats
    (list of {stat_id, name, display_name, enabled, position_type,
    ...}), stat_modifiers.stats (list of {stat_id, value}), and the
    other league-settings fields documented in contracts.py."""
    league = parsed.get("league") or {}
    settings = league.get("settings") or {}

    roster_positions = settings.get("roster_positions", {}).get("roster_position", [])
    if isinstance(roster_positions, dict):
        roster_positions = [roster_positions]

    stats = settings.get("stat_categories", {}).get("stats", {}).get("stat", [])
    if isinstance(stats, dict):
        stats = [stats]

    modifiers = settings.get("stat_modifiers", {}).get("stats", {}).get("stat", [])
    if isinstance(modifiers, dict):
        modifiers = [modifiers]

    result = dict(settings)
    result["roster_positions"] = roster_positions
    result["stat_categories"] = stats
    result["stat_modifiers"] = modifiers
    return result


def extract_standings(parsed: dict) -> list[dict]:
    """VERIFIED shape: list of team_standings dicts, one per team."""
    league = parsed.get("league") or {}
    standings = league.get("standings") or {}
    teams = standings.get("teams", {}).get("team", [])
    if isinstance(teams, dict):
        teams = [teams]
    return teams


# ---------------------------------------------------------------------
# Typed extraction helpers -- NOT_VERIFIED_THIS_SESSION field names
# (see contracts.py's Team/Roster/Player section)
# ---------------------------------------------------------------------

def extract_team(parsed: dict) -> dict:
    """NOT_VERIFIED_THIS_SESSION shape -- see contracts.py."""
    team = parsed.get("team")
    if isinstance(team, list):
        team = team[0]
    return team or {}


def extract_roster_players(parsed: dict) -> list[dict]:
    """NOT_VERIFIED_THIS_SESSION shape -- see contracts.py."""
    team = extract_team(parsed)
    roster = team.get("roster") or parsed.get("roster") or {}
    players = roster.get("players", {}).get("player", [])
    if isinstance(players, dict):
        players = [players]
    return players


def extract_players(parsed: dict) -> list[dict]:
    """NOT_VERIFIED_THIS_SESSION shape -- a players collection (e.g.
    league free-agent search) -- see contracts.py."""
    players = parsed.get("players") or {}
    if "player" in players:
        p = players["player"]
        return p if isinstance(p, list) else [p]
    league = parsed.get("league") or {}
    players = league.get("players") or {}
    p = players.get("player", [])
    return p if isinstance(p, list) else ([p] if p else [])
