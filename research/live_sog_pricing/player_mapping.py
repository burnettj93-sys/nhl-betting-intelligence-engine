"""
Maps a sportsbook/provider player name string (The Odds API's
`outcomes[].description`) to this project's own real `player_id`
(MoneyPuck's numeric id, from research/player_sog's corpus) --
conservatively: normalized full name PLUS team context (never last name
alone -- Part: "Do not match solely on last name," which matters for
real duplicate-surname NHL cases like the two Sebastian Ahos, or the two
Zachs/Zacharys on many rosters).

Required statuses: MATCHED / AMBIGUOUS / UNMATCHED -- no
model-vs-market comparison is ever produced for AMBIGUOUS or UNMATCHED
(Part: "No model-vs-market comparison for ambiguous players").
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(raw: str) -> str:
    """Lowercase, strip accents (NFKD decompose + drop combining marks),
    drop periods, collapse hyphens/whitespace to single spaces, drop a
    trailing generational suffix."""
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFKD", raw)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace(".", "").replace("-", " ")
    tokens = [t for t in stripped.lower().split() if t]
    if tokens and tokens[-1].strip(",") in _SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def build_player_index(sog_corpus_rows: list[dict]) -> dict[str, list[dict]]:
    """{normalized_name: [{"player_id", "player_name", "most_recent_team",
    "most_recent_game_date"}, ...]} -- one entry per DISTINCT player_id
    sharing that normalized name (so real duplicate-name players are
    preserved as separate candidates, not merged), built from the
    already-real, PIT-safe-irrelevant-here identity corpus (team
    CONTEXT for mapping is legitimately allowed to use the player's most
    recent known team, unlike a PREDICTIVE feature -- this is identity
    resolution, not a forecast)."""
    latest_by_player: dict[str, dict] = {}
    for r in sog_corpus_rows:
        pid = r["player_id"]
        if pid not in latest_by_player or r["game_date"] > latest_by_player[pid]["game_date"]:
            latest_by_player[pid] = {"player_name": r["player_name"], "team": r["team"],
                                      "game_date": r["game_date"]}

    index: dict[str, list[dict]] = defaultdict(list)
    for pid, info in latest_by_player.items():
        key = normalize_name(info["player_name"])
        index[key].append({"player_id": pid, "player_name": info["player_name"],
                            "most_recent_team": info["team"], "most_recent_game_date": info["game_date"]})
    return dict(index)


def map_player(raw_name: str, home_team: str, away_team: str, player_index: dict[str, list[dict]]) -> dict:
    """Returns {"status": "MATCHED"|"AMBIGUOUS"|"UNMATCHED", "player_id": ...|None,
    "reason": ...}."""
    key = normalize_name(raw_name)
    candidates = player_index.get(key, [])
    if not candidates:
        return {"status": "UNMATCHED", "player_id": None,
                "reason": f"no known player matches normalized name {key!r}"}

    if len(candidates) == 1:
        c = candidates[0]
        if c["most_recent_team"] not in (home_team, away_team):
            return {"status": "AMBIGUOUS", "player_id": None,
                    "reason": f"{c['player_name']}'s most recent known team "
                              f"({c['most_recent_team']}) is neither {home_team} nor {away_team} -- "
                              f"possible recent trade not yet reflected in the corpus"}
        return {"status": "MATCHED", "player_id": c["player_id"], "reason": ""}

    # Multiple distinct players share this normalized name (a real
    # duplicate-name case) -- team context is the only allowed
    # disambiguator; never guess by any other heuristic.
    team_matches = [c for c in candidates if c["most_recent_team"] in (home_team, away_team)]
    if len(team_matches) == 1:
        return {"status": "MATCHED", "player_id": team_matches[0]["player_id"], "reason": ""}
    if len(team_matches) == 0:
        return {"status": "AMBIGUOUS", "player_id": None,
                "reason": f"{len(candidates)} distinct players share the name {key!r}; "
                          f"none play for {home_team} or {away_team}"}
    return {"status": "AMBIGUOUS", "player_id": None,
            "reason": f"{len(team_matches)} distinct players named {key!r} both play for "
                      f"{home_team}/{away_team} -- team context does not disambiguate"}
