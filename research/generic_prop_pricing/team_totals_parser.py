"""
Normalizes The Odds API's raw `alternate_team_totals` market payload into a
flat list of quotes. Built against a REAL, archived DraftKings payload
(2026-09-25T12:15:10Z, Pittsburgh Penguins @ Washington Capitals, 24
outcomes -- both teams, points 0.5 through 5.5, real Over AND Under prices
each) -- NOT a documented-but-unobserved guess, unlike the alternate SOG
ladder's original assumption. See tests/fixtures/
draftkings_alternate_team_totals_real_payload.json for the sanitized fixture.

Structurally this is the SAME real Over/Under-at-a-line shape
research/live_sog_pricing/market_parser.py's parse_standard_market() already
handles for players -- `outcomes[].name` in ("Over", "Under"), `.description`
= the named entity (a TEAM here, not a player), `.point` = the line,
`.price` = American odds -- just offered at multiple `point` values per
team (an alternate ladder) rather than one line, and the entity is a team,
not a player. Written as its own small module rather than reusing
market_parser.py directly so the field names stay honest about what they
actually hold (team_name_raw, not player_name_raw) -- functionally
duplicative parsing logic was judged less confusing than a misleadingly-
named shared function.
"""
from __future__ import annotations

ALTERNATE_TEAM_TOTAL_MARKET_KEY = "alternate_team_totals"


class UnrecognizedOutcomeShapeError(ValueError):
    """Raised when an outcome's `name` isn't the documented Over/Under shape --
    surfaced loudly rather than silently mis-parsed."""


def parse_alternate_team_total_market(event_id: str, home_team: str, away_team: str,
                                       bookmaker: dict, market: dict) -> list[dict]:
    """`market["key"] == "alternate_team_totals"`. Returns one dict per outcome (both
    Over and Under kept -- see group_alternate_team_total_ladder() for grouping into
    per-team, per-point two-sided pairs)."""
    quotes = []
    for outcome in market.get("outcomes", []):
        side = outcome.get("name")
        if side not in ("Over", "Under"):
            raise UnrecognizedOutcomeShapeError(
                f"alternate_team_totals outcome name {side!r} is neither 'Over' nor 'Under'")
        quotes.append({
            "provider_event_id": event_id, "home_team": home_team, "away_team": away_team,
            "bookmaker": bookmaker.get("key"), "bookmaker_title": bookmaker.get("title"),
            "bookmaker_last_update_utc": bookmaker.get("last_update"),
            "market_key": market.get("key"), "market_last_update_utc": market.get("last_update"),
            "team_name_raw": outcome.get("description"), "side": side.upper(),
            "point": outcome.get("point"), "price_american": outcome.get("price"),
        })
    return quotes


def parse_event_odds_response(event_odds: dict) -> list[dict]:
    """Top-level entry point, mirrors market_parser.parse_event_odds_response()'s shape."""
    event_id = event_odds.get("id")
    home_team, away_team = event_odds.get("home_team"), event_odds.get("away_team")
    quotes = []
    for bookmaker in event_odds.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") == ALTERNATE_TEAM_TOTAL_MARKET_KEY:
                quotes.extend(parse_alternate_team_total_market(event_id, home_team, away_team, bookmaker, market))
    return quotes


def group_alternate_team_total_ladder(quotes: list[dict]) -> dict[tuple, dict]:
    """Groups quotes into {(provider_event_id, bookmaker, team_name_raw, point,
    market_last_update_utc): {"over": quote_or_None, "under": quote_or_None}} -- an Over
    and Under are only paired if they came from the SAME returned market object (same
    last_update), same team, same point -- never stitched across different snapshots."""
    groups: dict[tuple, dict] = {}
    for q in quotes:
        if q["market_key"] != ALTERNATE_TEAM_TOTAL_MARKET_KEY:
            continue
        key = (q["provider_event_id"], q["bookmaker"], q["team_name_raw"], q["point"], q["market_last_update_utc"])
        groups.setdefault(key, {"over": None, "under": None})
        groups[key]["over" if q["side"] == "OVER" else "under"] = q
    return groups
