"""
Numeric PBP team_id -> real abbreviation crosswalk. Not persisted in
research/real_nhl_pbp/store.py's own pbp_games table (only the numeric
IDs are), so this reads it once from a handful of real archived raw
payloads (research/real_nhl_pbp/raw/<season>/<game_id>.json's own
homeTeam.abbrev/awayTeam.abbrev fields) rather than hand-typing a
franchise list that could drift from what the feed actually says.

Confirmed real quirk: Utah's franchise carries TWO distinct numeric
team_ids across this corpus (59 and 68) -- both map to "UTA" here, which
is what matters for every join in this module (by abbreviation, never by
raw numeric id).
"""
from __future__ import annotations

import sqlite3

from research.real_nhl_pbp import raw_archive
from research.real_nhl_pbp.store import DB_PATH

# Derived once (2026-08-30) by scanning the real archived corpus until
# every team_id seen in pbp_games was resolved. Re-derive via
# build_team_id_to_abbrev() if the corpus ever adds a team_id not listed
# here (e.g. a future relocation/expansion team).
TEAM_ID_TO_ABBREV: dict[int, str] = {
    1: "NJD", 2: "NYI", 3: "NYR", 4: "PHI", 5: "PIT", 6: "BOS", 7: "BUF", 8: "MTL",
    9: "OTT", 10: "TOR", 12: "CAR", 13: "FLA", 14: "TBL", 15: "WSH", 16: "CHI",
    17: "DET", 18: "NSH", 19: "STL", 20: "CGY", 21: "COL", 22: "EDM", 23: "VAN",
    24: "ANA", 25: "DAL", 26: "LAK", 28: "SJS", 29: "CBJ", 30: "MIN", 52: "WPG",
    53: "ARI", 54: "VGK", 55: "SEA", 59: "UTA", 68: "UTA",
}


def build_team_id_to_abbrev(db_path: str = DB_PATH) -> dict[int, str]:
    """Real, from-scratch derivation (not used by default -- the module
    constant above is the fast path) -- kept for re-deriving if a new
    team_id ever appears."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT home_team_id FROM pbp_games")
    team_ids = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT away_team_id FROM pbp_games")
    team_ids |= {r[0] for r in cur.fetchall()}

    cur.execute("SELECT game_id, season, home_team_id, away_team_id FROM pbp_games")
    mapping: dict[int, str] = {}
    for game_id, season, home_id, away_id in cur.fetchall():
        if home_id in mapping and away_id in mapping:
            continue
        try:
            raw = raw_archive.load_raw_pbp(season, game_id)
        except (FileNotFoundError, OSError):
            continue
        mapping[home_id] = raw["homeTeam"]["abbrev"]
        mapping[away_id] = raw["awayTeam"]["abbrev"]
        if len(mapping) >= len(team_ids):
            break
    return mapping
