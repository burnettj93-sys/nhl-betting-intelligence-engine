"""
Global smart search (Preseason Interactive Product sprint, Parts 25-37).
Index built from canonical sources -- demo roster/goalies/games (Part 6),
the real team registry, and research.player_props.market_registry's own
aliases -- never a separate hand-written stale index.

Conservative fuzzy matching (Part 29): uses stdlib difflib only (no new
dependency), and always resolves to a canonical entity id -- never lets
a fuzzy match silently pick the wrong player without it being the clear
best candidate.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import lru_cache

from research.live_sog_pricing.player_mapping import normalize_name

TEAM_NAMES = {
    "EDM": "Edmonton Oilers", "COL": "Colorado Avalanche", "TOR": "Toronto Maple Leafs",
    "BOS": "Boston Bruins", "TBL": "Tampa Bay Lightning", "NJD": "New Jersey Devils",
    "MIN": "Minnesota Wild", "CHI": "Chicago Blackhawks", "VAN": "Vancouver Canucks",
    "WPG": "Winnipeg Jets", "NYR": "New York Rangers", "DAL": "Dallas Stars",
}


@dataclass
class SearchResult:
    entity_type: str  # PLAYER, GOALIE, TEAM, GAME, MARKET
    entity_id: str
    display: str
    subtitle: str
    rank_tier: int  # 0=best, higher=worse; used for sorting only


_RANK_EXACT_NAME = 0
_RANK_EXACT_ALIAS = 1
_RANK_PREFIX = 2
_RANK_SURNAME = 3
_RANK_FUZZY = 4
_RANK_TEAM = 5
_RANK_MARKET = 6

MIN_FUZZY_RATIO = 0.72


def _initials_form(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name.lower()
    return f"{parts[0][0]} {parts[-1]}".lower()


@lru_cache(maxsize=1)
def build_search_index() -> list[dict]:
    """One real entity per player/goalie/team/game/market. Rebuilt once
    per process (Part 36/37 -- cheap, cached, never a per-keystroke
    corpus scan)."""
    from dashboard import demo_data as dd
    from research.player_props.market_registry import CANONICAL_MARKETS

    entries: list[dict] = []
    start_time_by_team = {}
    for g in dd.build_demo_games():
        start_time_by_team[g.away] = g.start_time
        start_time_by_team[g.home] = g.start_time

    for p in dd.build_demo_roster():
        next_game = f" · Next: vs {p.opponent} · {start_time_by_team[p.team]}" if p.team in start_time_by_team else ""
        entries.append({
            "entity_type": "PLAYER", "entity_id": p.player_id, "display": p.name,
            "subtitle": f"{p.team} · {p.position} · PLAYER{next_game}",
            "normalized_full": normalize_name(p.name),
            "normalized_surname": normalize_name(p.name.split()[-1]) if p.name.split() else "",
            "initials": _initials_form(p.name),
        })

    for g in dd.build_demo_goalies():
        next_game = f" · Next: vs {g['opponent']} · {start_time_by_team[g['team']]}" if g["team"] in start_time_by_team else ""
        entries.append({
            "entity_type": "GOALIE", "entity_id": g["goalie_id"], "display": g["name"],
            "subtitle": f"{g['team']} · G · GOALIE{next_game}",
            "normalized_full": normalize_name(g["name"]),
            "normalized_surname": normalize_name(g["name"].split()[-1]) if g["name"].split() else "",
            "initials": _initials_form(g["name"]),
        })

    for abbr, full in TEAM_NAMES.items():
        entries.append({
            "entity_type": "TEAM", "entity_id": abbr, "display": full,
            "subtitle": f"{abbr} · TEAM",
            "normalized_full": normalize_name(full), "normalized_surname": "",
            "initials": abbr.lower(),
            "aliases": {normalize_name(abbr), normalize_name(full), normalize_name(full.split()[-1])},
        })

    for g in dd.build_demo_games():
        entries.append({
            "entity_type": "GAME", "entity_id": g.game_id, "display": f"{g.away} @ {g.home}",
            "subtitle": f"{g.start_time} · GAME",
            "normalized_full": normalize_name(f"{g.away} {g.home}"), "normalized_surname": "",
            "initials": f"{g.away.lower()} {g.home.lower()}",
            "aliases": {normalize_name(f"{g.away} {g.home}"), normalize_name(f"{g.away} vs {g.home}"),
                        normalize_name(f"{TEAM_NAMES.get(g.away, g.away)} {TEAM_NAMES.get(g.home, g.home)}")},
        })

    # Aggregate EVERY market's aliases within a category (Part 34/37: build
    # from the real registry, but a bare "SOG" or "Shots on Goal" query
    # should resolve the category even though no single individual
    # threshold entry's own display_name/aliases happens to say that).
    category_display_overrides = {
        "PLAYER_SOG": "Shots on Goal", "PLAYER_GOALS_SCORING": "Goals",
        "PLAYER_ASSISTS": "Assists", "PLAYER_POINTS": "Points",
        "PLAYER_BLOCKED_SHOTS": "Blocked Shots", "GOALIE": "Goalie Saves",
    }
    category_extra_aliases = {
        "PLAYER_SOG": {"sog", "shots", "shots on goal", "shot on goal"},
        "PLAYER_GOALS_SCORING": {"goal", "goals", "anytime goal", "anytime goal scorer"},
        "PLAYER_ASSISTS": {"assist", "assists"},
        "PLAYER_POINTS": {"point", "points"},
        "PLAYER_BLOCKED_SHOTS": {"block", "blocks", "blocked shots"},
        "GOALIE": {"saves", "goalie saves"},
    }
    by_category: dict[str, list] = {}
    for m in CANONICAL_MARKETS:
        by_category.setdefault(m.category, []).append(m)
    row_counts: dict[str, int] = {}
    for o in dd.build_demo_opportunities():
        row_counts[o["market"]] = row_counts.get(o["market"], 0) + 1
    for category, markets in by_category.items():
        aliases = set()
        for m in markets:
            aliases.add(normalize_name(m.display_name))
            aliases |= {normalize_name(a) for a in m.aliases}
        aliases |= {normalize_name(a) for a in category_extra_aliases.get(category, set())}
        display = category_display_overrides.get(category, markets[0].display_name)
        row_count = row_counts.get(category.replace("PLAYER_", "").replace("_SCORING", ""), 0)
        row_suffix = f" · {row_count} demo rows" if row_count else ""
        entries.append({
            "entity_type": "MARKET", "entity_id": category, "display": display,
            "subtitle": f"{markets[0].model_status} · MARKET{row_suffix}",
            "normalized_full": normalize_name(display), "normalized_surname": "",
            "initials": "", "aliases": aliases,
        })

    return entries


def search(query: str, limit: int = 8) -> list[SearchResult]:
    """Part 32 ranking: exact full name > exact alias > prefix > surname
    > fuzzy > team > market. Returns at most `limit` results, best first."""
    nq = normalize_name(query)
    if not nq:
        return []

    scored: list[tuple[int, float, dict]] = []
    for e in build_search_index():
        aliases = e.get("aliases", set())
        full = e["normalized_full"]
        surname = e.get("normalized_surname", "")
        initials = e.get("initials", "")

        if nq == full or nq == initials:
            scored.append((_RANK_EXACT_NAME, 1.0, e))
            continue
        if nq in aliases:
            scored.append((_RANK_EXACT_ALIAS, 1.0, e))
            continue
        if full.startswith(nq) and len(nq) >= 2:
            scored.append((_RANK_PREFIX, len(nq) / max(len(full), 1), e))
            continue
        if surname and nq == surname:
            base_rank = _RANK_SURNAME
            scored.append((base_rank, 1.0, e))
            continue
        if any(a.startswith(nq) for a in aliases) and len(nq) >= 2:
            scored.append((_RANK_PREFIX, 0.5, e))
            continue

        ratio = difflib.SequenceMatcher(None, nq, full).ratio()
        if surname:
            ratio = max(ratio, difflib.SequenceMatcher(None, nq, surname).ratio())
        if ratio >= MIN_FUZZY_RATIO:
            tier = _RANK_TEAM if e["entity_type"] == "TEAM" else (
                _RANK_MARKET if e["entity_type"] == "MARKET" else _RANK_FUZZY)
            scored.append((tier, ratio, e))

    scored.sort(key=lambda t: (t[0], -t[1]))
    results, seen_ids = [], set()
    for tier, _score, e in scored:
        key = (e["entity_type"], e["entity_id"])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append(SearchResult(e["entity_type"], e["entity_id"], e["display"], e["subtitle"], tier))
        if len(results) >= limit:
            break
    return results
