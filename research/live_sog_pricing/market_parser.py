"""
Normalizes The Odds API's raw player_shots_on_goal /
player_shots_on_goal_alternate market payloads into a flat list of
quotes. Built against the OFFICIALLY DOCUMENTED player-prop outcome
shape (`outcomes[].name` = "Over"/"Under", `.description` = player
name, `.price` = American odds, `.point` = the line -- the same shape
The Odds API documents across every player-prop market in every sport
it covers) -- NOT against a genuine captured non-empty NHL payload,
because none was available this slice (Phase A's real smoke test found
zero DraftKings markets currently posted -- see
PLAYER_SOG_LIVE_PRICING_REPORT.md Section A/G/H). This is stated
explicitly rather than silently assumed, per the "do not assume
alternate-market semantics" instruction for this slice.

The alternate-market parser is deliberately SCHEMA-TOLERANT: it inspects
each outcome's actual `name` field rather than hardcoding one hypothesis,
handling both a plausible "Over"/"Under" shape (same as the standard
market, just more `point` values) and a plausible "X+" milestone shape.
Whichever shape DraftKings actually uses will only be confirmed once a
live payload exists -- `research/run_live_sog_phase_a_smoke.py` should
be re-run closer to puck drop to verify byte-for-byte and this docstring
updated with the real finding.
"""
from __future__ import annotations

import re

STANDARD_MARKET_KEY = "player_shots_on_goal"
ALTERNATE_MARKET_KEY = "player_shots_on_goal_alternate"

_MILESTONE_RE = re.compile(r"^\s*(\d+)\s*\+\s*$")


class UnrecognizedOutcomeShapeError(ValueError):
    """Raised when an outcome's `name` matches neither the documented
    Over/Under shape nor a plausible X+ milestone shape -- surfaced
    loudly rather than silently mis-parsed, since guessing wrong here
    would corrupt every downstream probability comparison."""


def parse_standard_market(event_id: str, home_team: str, away_team: str,
                           bookmaker: dict, market: dict) -> list[dict]:
    """`market["key"] == "player_shots_on_goal"`. Returns one dict per
    outcome (Over AND Under both kept -- see market_grouping.py for
    pairing them into two-sided quotes)."""
    quotes = []
    for outcome in market.get("outcomes", []):
        side = outcome.get("name")
        if side not in ("Over", "Under"):
            raise UnrecognizedOutcomeShapeError(
                f"standard SOG market outcome name {side!r} is neither 'Over' nor 'Under'")
        quotes.append({
            "provider_event_id": event_id, "home_team": home_team, "away_team": away_team,
            "bookmaker": bookmaker.get("key"), "bookmaker_title": bookmaker.get("title"),
            "bookmaker_last_update_utc": bookmaker.get("last_update"),
            "market_key": STANDARD_MARKET_KEY, "market_last_update_utc": market.get("last_update"),
            "player_name_raw": outcome.get("description"), "side": side.upper(),
            "point": outcome.get("point"), "price_american": outcome.get("price"),
            "shape": "over_under",
        })
    return quotes


def parse_alternate_market(event_id: str, home_team: str, away_team: str,
                            bookmaker: dict, market: dict) -> list[dict]:
    """`market["key"] == "player_shots_on_goal_alternate"`. Detects the
    real outcome shape per-outcome rather than assuming one globally, so
    a mixed or unexpected payload fails loudly (UnrecognizedOutcomeShapeError)
    instead of silently mis-labeling a side."""
    quotes = []
    for outcome in market.get("outcomes", []):
        name = (outcome.get("name") or "").strip()
        milestone_match = _MILESTONE_RE.match(name)
        if name in ("Over", "Under"):
            side, threshold, shape = name.upper(), None, "over_under"
        elif milestone_match:
            side, threshold, shape = "OVER_MILESTONE", int(milestone_match.group(1)), "milestone"
        else:
            raise UnrecognizedOutcomeShapeError(
                f"alternate SOG market outcome name {name!r} matches neither documented "
                f"Over/Under nor a plausible 'N+' milestone shape -- re-verify against a "
                f"live captured payload before trusting this market")
        quotes.append({
            "provider_event_id": event_id, "home_team": home_team, "away_team": away_team,
            "bookmaker": bookmaker.get("key"), "bookmaker_title": bookmaker.get("title"),
            "bookmaker_last_update_utc": bookmaker.get("last_update"),
            "market_key": ALTERNATE_MARKET_KEY, "market_last_update_utc": market.get("last_update"),
            "player_name_raw": outcome.get("description"), "side": side,
            "point": outcome.get("point") if shape == "over_under" else None,
            "milestone_threshold": threshold, "price_american": outcome.get("price"),
            "shape": shape,
        })
    return quotes


def parse_event_odds_response(event_odds: dict) -> list[dict]:
    """Top-level entry point: given one event's raw /odds response
    (`client.get_event_odds()`'s `.data`), returns every SOG quote from
    every requested market, across every bookmaker present (normally
    just DraftKings, since that's the only bookmaker this project
    requests -- Part: "DraftKings first")."""
    event_id = event_odds.get("id")
    home_team, away_team = event_odds.get("home_team"), event_odds.get("away_team")
    quotes = []
    for bookmaker in event_odds.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") == STANDARD_MARKET_KEY:
                quotes.extend(parse_standard_market(event_id, home_team, away_team, bookmaker, market))
            elif market.get("key") == ALTERNATE_MARKET_KEY:
                quotes.extend(parse_alternate_market(event_id, home_team, away_team, bookmaker, market))
    return quotes


def group_standard_two_sided(quotes: list[dict]) -> dict[tuple, dict]:
    """Groups standard-market Over/Under quotes into two-sided pairs,
    keyed by (provider_event_id, bookmaker, player_name_raw, point,
    market_last_update_utc) -- the market-coherence policy this slice
    requires: an Over and Under are only paired if they came from the
    SAME returned market object (same last_update), never stitched
    together across different snapshots. Returns
    {key: {"over": quote_or_None, "under": quote_or_None}}."""
    groups: dict[tuple, dict] = {}
    for q in quotes:
        if q["market_key"] != STANDARD_MARKET_KEY:
            continue
        key = (q["provider_event_id"], q["bookmaker"], q["player_name_raw"], q["point"],
               q["market_last_update_utc"])
        groups.setdefault(key, {"over": None, "under": None})
        groups[key]["over" if q["side"] == "OVER" else "under"] = q
    return groups
