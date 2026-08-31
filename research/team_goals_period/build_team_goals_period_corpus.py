"""
Part 1-2: real team-game-period GOAL label corpus, built directly from the
validated 4-season play-by-play corpus (research/real_nhl_pbp/) -- mirrors
research/player_sog_period/build_period_sog_corpus.py's discipline exactly
(same real-event extraction, same REG-only primary periods with OT tracked
separately, never merged), but counts STATISTICAL GOALS per TEAM per
period, not player SOG.

Shootout goals are excluded entirely (Part 1) -- the same accepted
shootout-isolation rule this project has used since the single-season PBP
foundation slice (a shootout goal is never a statistical goal). "Real
(non-SO) goal" is identified the same way normalize.py does:
`typeDescKey == "goal"` inside a `periodType != "SO"` play.

Two rows per game (home team, away team), each carrying its own
period-by-period goals-for AND the opponent's period-by-period goals-for
(goals-against, needed for opponent-defensive-context features, Part 9,
without a second join) -- symmetric, so summing both rows' full_game_team_goals
gives the real final combined score.
"""
from __future__ import annotations

import collections
import json
import os

from research.real_nhl_pbp import raw_archive

SEASONS = ("20222023", "20232024", "20242025", "20252026")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "team_game_period_goals.jsonl")


def build_one_game(season: str, game_id: int) -> list[dict]:
    raw = raw_archive.load_raw_pbp(season, game_id)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
    home_abbrev, away_abbrev = raw["homeTeam"]["abbrev"], raw["awayTeam"]["abbrev"]
    game_date = raw["gameDate"]

    period_goals: dict[int, dict[int, int]] = {home_id: {1: 0, 2: 0, 3: 0}, away_id: {1: 0, 2: 0, 3: 0}}
    period_pp_goals: dict[int, dict[int, int]] = {home_id: {1: 0, 2: 0, 3: 0}, away_id: {1: 0, 2: 0, 3: 0}}
    ot_goals: dict[int, int] = {home_id: 0, away_id: 0}

    for play in raw["plays"]:
        if play["typeDescKey"] != "goal":
            continue
        pd = play["periodDescriptor"]
        period_type = pd["periodType"]
        if period_type not in ("REG", "OT"):
            continue  # shootout goals excluded entirely -- Part 1
        details = play.get("details", {})
        scoring_team = details.get("eventOwnerTeamId")
        if scoring_team not in (home_id, away_id):
            continue
        if period_type == "REG":
            period_goals[scoring_team][pd["number"]] += 1
            if _is_real_power_play(play, scoring_team, home_id, away_id):
                period_pp_goals[scoring_team][pd["number"]] += 1
        else:
            ot_goals[scoring_team] += 1

    rows = []
    for team_id, opp_id, team_abbrev, opp_abbrev, is_home in (
        (home_id, away_id, home_abbrev, away_abbrev, True),
        (away_id, home_id, away_abbrev, home_abbrev, False),
    ):
        p1, p2, p3 = period_goals[team_id][1], period_goals[team_id][2], period_goals[team_id][3]
        pp1, pp2, pp3 = period_pp_goals[team_id][1], period_pp_goals[team_id][2], period_pp_goals[team_id][3]
        op1, op2, op3 = period_goals[opp_id][1], period_goals[opp_id][2], period_goals[opp_id][3]
        ot = ot_goals[team_id]
        rows.append({
            "game_id": game_id, "game_date": game_date, "season": int(season),
            "team_id": team_id, "team": team_abbrev, "opponent_id": opp_id, "opponent": opp_abbrev,
            "home_away": "home" if is_home else "away",
            "period_1_goals": p1, "period_2_goals": p2, "period_3_goals": p3,
            "period_1_pp_goals": pp1, "period_2_pp_goals": pp2, "period_3_pp_goals": pp3,
            "opponent_period_1_goals": op1, "opponent_period_2_goals": op2, "opponent_period_3_goals": op3,
            "ot_goals": ot, "full_game_team_goals": p1 + p2 + p3 + ot,
        })
    return rows


def _is_real_power_play(play: dict, scoring_team_id: int, home_team_id: int, away_team_id: int) -> bool:
    """Part 11: real PP state via the accepted situationCode joint rule
    (both goalie digits '1', unequal skater count in the scoring team's
    favor) -- same rule reused unchanged from
    research/player_sog_period/build_period_sog_corpus.py and confirmed
    in NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md Section AF."""
    sc = play.get("situationCode")
    if not sc or len(sc) != 4:
        return False
    away_goalie, away_skaters, home_skaters, home_goalie = sc[0], sc[1], sc[2], sc[3]
    if away_goalie != "1" or home_goalie != "1":
        return False
    if away_skaters == home_skaters:
        return False
    scorer_is_away = (scoring_team_id == away_team_id)
    scorer_skaters = away_skaters if scorer_is_away else home_skaters
    other_skaters = home_skaters if scorer_is_away else away_skaters
    return scorer_skaters > other_skaters


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
            counts[season] = {"games": len(game_ids), "team_game_rows": n_rows}
    return counts


if __name__ == "__main__":
    result = build_corpus()
    for season, c in result.items():
        print(season, c)
