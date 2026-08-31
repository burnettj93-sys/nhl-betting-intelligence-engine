"""
Part 1: real team-game Shots on Goal label corpus, built directly from the
accepted 4-season PBP foundation (research/real_nhl_pbp/). A dedicated
package, not a reuse of research/goalie_saves/team_game_sog.jsonl --
that file was a byproduct of the Goalie Saves slice; this is the
authoritative Team SOG corpus for THIS slice, rebuilt independently per
this project's established "don't cross-import between sibling prop
packages" convention (confirmed across team_goals_period, player_sog_period,
goalie_saves). The extraction logic is intentionally the same real-event
rule (shot-on-goal + goal events by the attacking team, excluding
shootout, using the same joint situationCode PP/PK rule) -- one accepted
definition of "real SOG," reimplemented, not re-derived differently.

Two rows per game (home team, away team), each carrying its own
period-by-period SOG-for AND the opponent's SOG-for that same game
(SOG-against, Part 4's opponent-suppression baselines need this without a
second join) -- symmetric, matching team_goals_period's design.

`opponent_starting_goalie_id` is carried for LABEL/AUDIT purposes ONLY
(Part 1's explicit instruction) -- reconstructed via the already-audited,
unmodified `research/real_nhl_pbp/goalie_tenure.py` (STARTER interval),
never read as a pregame feature anywhere in this package's features.py or
the driver.
"""
from __future__ import annotations

import json
import os

from research.real_nhl_pbp import goalie_tenure, normalize, raw_archive

SEASONS = ("20222023", "20232024", "20242025", "20252026")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "team_game_sog.jsonl")


def _pp_state(situation_code: str | None, team_id: int, home_id: int, away_id: int) -> str:
    """Same joint situationCode rule reused unchanged from
    research/team_goals_period and research/goalie_saves."""
    if not situation_code or len(situation_code) != 4:
        return "EV"
    away_goalie, away_skaters, home_skaters, home_goalie = situation_code[0], situation_code[1], \
        situation_code[2], situation_code[3]
    if away_goalie != "1" or home_goalie != "1":
        return "EV"
    if away_skaters == home_skaters:
        return "EV"
    is_away = (team_id == away_id)
    own_skaters = away_skaters if is_away else home_skaters
    other_skaters = home_skaters if is_away else away_skaters
    if own_skaters > other_skaters:
        return "PP"
    if own_skaters < other_skaters:
        return "PK"
    return "EV"


def _starting_goalies(events, home_id: int, away_id: int) -> dict[int, int | None]:
    tenure = goalie_tenure.reconstruct_goalie_tenure(events, home_id, away_id)
    starters: dict[int, int | None] = {home_id: None, away_id: None}
    for team_id, intervals in tenure.items():
        starter_interval = next((iv for iv in intervals if iv.interval_type == "STARTER"), None)
        starters[team_id] = starter_interval.goalie_id if starter_interval is not None else None
    return starters


def build_one_game(season: str, game_id: int) -> list[dict]:
    raw = raw_archive.load_raw_pbp(season, game_id)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]
    home_abbrev, away_abbrev = raw["homeTeam"]["abbrev"], raw["awayTeam"]["abbrev"]
    game_date = raw["gameDate"]
    events = normalize.normalize_game_events(raw)
    starters = _starting_goalies(events, home_id, away_id)

    sog: dict[int, dict] = {
        home_id: {"periods": {1: 0, 2: 0, 3: 0}, "pp_periods": {1: 0, 2: 0, 3: 0}, "ot": 0, "goals": 0},
        away_id: {"periods": {1: 0, 2: 0, 3: 0}, "pp_periods": {1: 0, 2: 0, 3: 0}, "ot": 0, "goals": 0},
    }
    for e in events:
        if e.period_type == "SO" or not e.is_statistical:
            continue
        if e.event_type not in ("shot-on-goal", "goal"):
            continue
        shooting_team = e.team_id
        if shooting_team not in (home_id, away_id):
            continue
        state = _pp_state(e.situation_code, shooting_team, home_id, away_id)
        if e.period_number <= 3:
            sog[shooting_team]["periods"][e.period_number] += 1
            if state == "PP":
                sog[shooting_team]["pp_periods"][e.period_number] += 1
        else:
            sog[shooting_team]["ot"] += 1
        if e.event_type == "goal":
            sog[shooting_team]["goals"] += 1

    rows = []
    for team_id, opp_id, team_abbrev, opp_abbrev, is_home in (
        (home_id, away_id, home_abbrev, away_abbrev, True),
        (away_id, home_id, away_abbrev, home_abbrev, False),
    ):
        t = sog[team_id]
        opp = sog[opp_id]
        p1, p2, p3 = t["periods"][1], t["periods"][2], t["periods"][3]
        pp1, pp2, pp3 = t["pp_periods"][1], t["pp_periods"][2], t["pp_periods"][3]
        op1, op2, op3 = opp["periods"][1], opp["periods"][2], opp["periods"][3]
        opp_pp1, opp_pp2, opp_pp3 = opp["pp_periods"][1], opp["pp_periods"][2], opp["pp_periods"][3]
        rows.append({
            "game_id": game_id, "game_date": game_date, "season": int(season),
            "team_id": team_id, "team": team_abbrev, "opponent_id": opp_id, "opponent": opp_abbrev,
            "home_away": "home" if is_home else "away",
            "P1_team_sog": p1, "P2_team_sog": p2, "P3_team_sog": p3, "OT_team_sog": t["ot"],
            "actual_team_sog": p1 + p2 + p3 + t["ot"],
            "P1_pp_sog": pp1, "P2_pp_sog": pp2, "P3_pp_sog": pp3,
            "actual_opponent_sog": op1 + op2 + op3 + opp["ot"],
            "opponent_P1_sog": op1, "opponent_P2_sog": op2, "opponent_P3_sog": op3,
            "opponent_P1_pp_sog": opp_pp1, "opponent_P2_pp_sog": opp_pp2, "opponent_P3_pp_sog": opp_pp3,
            "actual_team_goals": t["goals"],
            "opponent_starting_goalie_id": starters.get(opp_id),
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
            counts[season] = {"games": len(game_ids), "team_game_rows": n_rows}
    return counts


if __name__ == "__main__":
    result = build_corpus()
    for season, c in result.items():
        print(season, c)
