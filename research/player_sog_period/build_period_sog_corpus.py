"""
Part 1-2: real player-game-period SOG label corpus, built directly from
the validated 4-season play-by-play corpus (research/real_nhl_pbp/).

Eligible player universe per game: every skater (positionCode != 'G') in
the game's real `rosterSpots` list -- these are the players who actually
dressed for the game, giving a TRUE ZERO label for any dressed skater who
recorded no shot in a given period, rather than silently excluding them.
Goalies are excluded (SOG is a skater stat; a goalie recording a shot on
goal is not a real scenario this corpus needs to support).

SOG definition matches the project's existing accepted convention (reused,
not re-derived): a `shot-on-goal` event (role=shooter) OR a real
(non-shootout) `goal` event (role=scorer) both count as SOG -- confirmed
correct in the single-season and multi-season PBP reports via direct
/boxscore reconciliation.

Period columns cover REG periods 1-3 only (Part 1's "use only REG periods
1, 2, 3 for the primary model"); `ot_sog` is tracked separately (Part 2 --
"do not silently discard OT shots when comparing against full-game
totals"), never merged into P1/P2/P3. `full_game_sog` = P1+P2+P3+OT,
reconciled exactly (Part 2), and is intentionally NOT read from any
external corpus (e.g. MoneyPuck) -- it is computed the same way as the
period columns so the reconciliation identity is true by construction,
not by hoping two different data sources agree.
"""
from __future__ import annotations

import collections
import json
import os

from research.real_nhl_pbp import raw_archive

SEASONS = ("20222023", "20232024", "20242025", "20252026")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "player_game_period_sog.jsonl")


def _is_real_power_play(play: dict, shooting_team_id: int, home_team_id: int, away_team_id: int) -> bool:
    """Part 14: real PP state, reusing the accepted situationCode joint
    rule (both goalie digits '1' -- excludes empty-net extra-attacker
    situations from being misread as a PP -- and an unequal skater count
    in the shooting team's favor), confirmed in
    NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md Section AF. This is a per-SHOT
    proxy for PP role, not a true PP-TOI reconstruction (which would need
    shift-chart-level processing not built in this project) -- disclosed
    as a proxy, not presented as TOI."""
    sc = play.get("situationCode")
    if not sc or len(sc) != 4:
        return False
    away_goalie, away_skaters, home_skaters, home_goalie = sc[0], sc[1], sc[2], sc[3]
    if away_goalie != "1" or home_goalie != "1":
        return False
    if away_skaters == home_skaters:
        return False
    shooter_is_away = (shooting_team_id == away_team_id)
    shooter_skaters = away_skaters if shooter_is_away else home_skaters
    other_skaters = home_skaters if shooter_is_away else away_skaters
    return shooter_skaters > other_skaters


def build_one_game(season: str, game_id: int) -> list[dict]:
    raw = raw_archive.load_raw_pbp(season, game_id)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
    home_abbrev, away_abbrev = raw["homeTeam"]["abbrev"], raw["awayTeam"]["abbrev"]
    game_date = raw["gameDate"]

    skaters = {r["playerId"]: r["teamId"] for r in raw["rosterSpots"] if r["positionCode"] != "G"}
    positions = {r["playerId"]: r["positionCode"] for r in raw["rosterSpots"] if r["positionCode"] != "G"}

    period_counts: dict[int, dict[int, int]] = collections.defaultdict(lambda: {1: 0, 2: 0, 3: 0})
    period_pp_counts: dict[int, dict[int, int]] = collections.defaultdict(lambda: {1: 0, 2: 0, 3: 0})
    ot_counts: dict[int, int] = collections.defaultdict(int)

    for play in raw["plays"]:
        pd = play["periodDescriptor"]
        period_type = pd["periodType"]
        if period_type not in ("REG", "OT"):
            continue
        details = play.get("details", {})
        event_type = play["typeDescKey"]
        pid = None
        if event_type == "shot-on-goal":
            pid = details.get("shootingPlayerId")
        elif event_type == "goal":
            pid = details.get("scoringPlayerId")
        if pid is None or pid not in skaters:
            continue
        if period_type == "REG":
            period_counts[pid][pd["number"]] += 1
            if _is_real_power_play(play, skaters[pid], home_id, away_id):
                period_pp_counts[pid][pd["number"]] += 1
        else:
            ot_counts[pid] += 1

    rows = []
    for pid, team_id in skaters.items():
        p1 = period_counts[pid][1]
        p2 = period_counts[pid][2]
        p3 = period_counts[pid][3]
        pp1, pp2, pp3 = period_pp_counts[pid][1], period_pp_counts[pid][2], period_pp_counts[pid][3]
        ot = ot_counts[pid]
        opponent_id = away_id if team_id == home_id else home_id
        team_abbrev = home_abbrev if team_id == home_id else away_abbrev
        opponent_abbrev = away_abbrev if team_id == home_id else home_abbrev
        rows.append({
            "game_id": game_id, "game_date": game_date, "season": int(season),
            "player_id": str(pid), "position": positions[pid], "team_id": team_id,
            "team": team_abbrev, "opponent": opponent_abbrev,
            "opponent_id": opponent_id, "home_away": "home" if team_id == home_id else "away",
            "period_1_sog": p1, "period_2_sog": p2, "period_3_sog": p3,
            "period_1_pp_sog": pp1, "period_2_pp_sog": pp2, "period_3_pp_sog": pp3,
            "ot_sog": ot, "full_game_sog": p1 + p2 + p3 + ot,
            "went_to_ot": raw["gameOutcome"].get("lastPeriodType") in ("OT", "SO") and
                          any(pd["periodType"] == "OT" for pd in (p["periodDescriptor"] for p in raw["plays"])),
        })
    return rows


def build_corpus(seasons: tuple[str, ...] = SEASONS) -> dict:
    counts = {}
    with open(OUTPUT_PATH, "w") as f:
        for season in seasons:
            game_ids = raw_archive.archived_game_ids(season)
            n_rows = 0
            for gid in game_ids:
                rows = build_one_game(season, gid)
                for row in rows:
                    f.write(json.dumps(row) + "\n")
                n_rows += len(rows)
            counts[season] = {"games": len(game_ids), "player_game_rows": n_rows}
    return counts


if __name__ == "__main__":
    result = build_corpus()
    for season, c in result.items():
        print(season, c)
