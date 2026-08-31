"""
Maps a The Odds API event (id, home_team, away_team, commence_time) to
this project's own NHL game_id, using team-name normalization + a
commence_time match against a supplied real schedule -- The Odds API's
event ids are its own opaque identifiers and never equal an NHL game_id.

Never silently fuzzy-matches an ambiguous event (Part: "Do not silently
fuzzy-match ambiguous events. For betting comparison: AMBIGUOUS or
UNMATCHED = DATA_UNAVAILABLE").
"""
from __future__ import annotations

import datetime as dt

# The Odds API returns full franchise names ("Toronto Maple Leafs");
# every other real corpus in this project (research/real_nhl_results,
# research/player_sog) uses NHL's 3-letter team abbreviations. This is
# the SAME 32-team mapping either side of a mismatch would need --
# built once, from the real franchise list, not inferred per-event.
ODDS_API_TEAM_NAME_TO_ABBREV = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montréal Canadiens": "MTL",
    "Montreal Canadiens": "MTL", "Nashville Predators": "NSH", "New Jersey Devils": "NJD",
    "New York Islanders": "NYI", "New York Rangers": "NYR", "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA", "St Louis Blues": "STL", "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR", "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA", "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH", "Winnipeg Jets": "WPG",
}

# Match window: an event's commence_time and a schedule game's real
# start time can differ by broadcast-scheduling noise -- generous enough
# to survive that, tight enough that no two real games on the same date
# could both fall inside it.
COMMENCE_TIME_MATCH_WINDOW_HOURS = 6.0


def normalize_team_name(raw_name: str) -> str | None:
    return ODDS_API_TEAM_NAME_TO_ABBREV.get(raw_name)


def map_event_to_game(event: dict, schedule: list[dict]) -> dict:
    """`event`: {"id", "home_team", "away_team", "commence_time"} from
    The Odds API. `schedule`: real games, each with at least
    {"game_id", "home_team", "away_team", "game_date"} (3-letter
    abbreviations, ISO game_date) -- e.g. research.elo_comparison.load_corpus()'s
    output, or any future live-schedule source with the same shape.
    Returns {"status": "MATCHED"|"AMBIGUOUS"|"UNMATCHED", "game_id": ...|None,
    "reason": ...}."""
    home_abbrev = normalize_team_name(event.get("home_team", ""))
    away_abbrev = normalize_team_name(event.get("away_team", ""))
    if home_abbrev is None or away_abbrev is None:
        return {"status": "UNMATCHED", "game_id": None,
                "reason": f"unrecognized team name(s): home={event.get('home_team')!r} "
                          f"away={event.get('away_team')!r}"}

    try:
        commence = dt.datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return {"status": "UNMATCHED", "game_id": None, "reason": "missing/invalid commence_time"}

    candidates = []
    for game in schedule:
        if game["home_team"] != home_abbrev or game["away_team"] != away_abbrev:
            continue
        try:
            game_date = dt.datetime.fromisoformat(game["game_date"]).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        hours_apart = abs((commence - game_date).total_seconds()) / 3600.0
        if hours_apart <= COMMENCE_TIME_MATCH_WINDOW_HOURS or (
                game_date.date() == commence.date()):
            candidates.append(game)

    if len(candidates) == 0:
        return {"status": "UNMATCHED", "game_id": None,
                "reason": f"no schedule game found for {away_abbrev} @ {home_abbrev} "
                          f"near {event.get('commence_time')}"}
    if len(candidates) > 1:
        return {"status": "AMBIGUOUS", "game_id": None,
                "reason": f"{len(candidates)} schedule games matched {away_abbrev} @ {home_abbrev} "
                          f"near {event.get('commence_time')} -- refusing to guess"}
    return {"status": "MATCHED", "game_id": candidates[0]["game_id"], "reason": ""}
