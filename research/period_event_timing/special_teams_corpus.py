"""
Part 7: team-game special-teams opportunity corpus, built directly from
the manpower-state reconstruction in penalties.py/manpower.py -- one row
per (game_id, team_id) with PP opportunities/seconds/shots/goals, SH
seconds/shots-allowed/goals-allowed, penalties taken/drawn, and 5-on-5
goals/SOG. PP xG is intentionally NOT included: no legitimate, already-
ingested MoneyPuck xG-per-shot field is linkable to individual PBP shot
events in this corpus (MoneyPuck's real xG lives at the player-game
aggregate level elsewhere in this project, not per-shot-event here) --
Part 7 explicitly allows omitting it ("if legitimately linkable").
"""
from __future__ import annotations

import sqlite3

from research.period_event_timing import manpower as mp
from research.period_event_timing import penalties as pw

SHOT_EVENT_TYPES = ("goal", "shot-on-goal")


def build_team_game_special_teams(conn: sqlite3.Connection, game_id: int,
                                   home_team_id: int, away_team_id: int) -> dict[int, dict]:
    events = pw.fetch_game_events_for_windows(conn, game_id)
    out = {home_team_id: _blank_row(), away_team_id: _blank_row()}

    prev_state = None
    window_start_seconds = None
    window_period = None

    def flush_window(end_period, end_seconds, state):
        nonlocal window_start_seconds, window_period
        if window_start_seconds is None:
            return
        duration = (pw.game_clock_seconds(end_period, end_seconds)
                    - pw.game_clock_seconds(window_period, window_start_seconds))
        pp_home = mp.is_power_play_for_home(state)
        if pp_home is True:
            out[home_team_id]["pp_seconds"] += duration
            out[away_team_id]["sh_seconds"] += duration
        elif pp_home is False:
            out[away_team_id]["pp_seconds"] += duration
            out[home_team_id]["sh_seconds"] += duration

    for ev in events:
        state = mp.classify_manpower_state(ev["situation_code"])
        if state != prev_state:
            if prev_state is not None:
                flush_window(ev["period_number"], ev["seconds_elapsed_in_period"], prev_state)
            window_start_seconds = ev["seconds_elapsed_in_period"]
            window_period = ev["period_number"]
            if state != prev_state and mp.is_power_play_for_home(state) is not None:
                pp_home = mp.is_power_play_for_home(state)
                if pp_home is True:
                    out[home_team_id]["pp_opportunities"] += 1
                else:
                    out[away_team_id]["pp_opportunities"] += 1
            prev_state = state

        team_id = ev["team_id"]
        if team_id not in out:
            continue
        other_team = away_team_id if team_id == home_team_id else home_team_id
        pp_home = mp.is_power_play_for_home(state)
        team_is_pp = (pp_home is True and team_id == home_team_id) or (pp_home is False and team_id == away_team_id)
        team_is_sh = (pp_home is True and team_id == away_team_id) or (pp_home is False and team_id == home_team_id)

        if ev["event_type"] in SHOT_EVENT_TYPES:
            if mp.is_even_strength(state):
                out[team_id]["five_v_five_sog"] += 1
                if ev["event_type"] == "goal":
                    out[team_id]["five_v_five_goals"] += 1
            elif team_is_pp:
                # a shot BY the advantaged team is simultaneously a PP
                # shot for them and a shot ALLOWED for the shorthanded
                # opponent -- both sides of the same event, recorded here
                # together (this is the fix for a real bug found during
                # testing: these were previously, wrongly, only recorded
                # off the rare "shorthanded team shoots" branch below,
                # which fires for a completely different, much rarer
                # event and left sh_shots_allowed near-permanently 0).
                out[team_id]["pp_shots"] += 1
                out[other_team]["sh_shots_allowed"] += 1
                if ev["event_type"] == "goal":
                    out[team_id]["pp_goals"] += 1
                    out[other_team]["sh_goals_allowed"] += 1
            elif team_is_sh:
                # rare: the shorthanded team itself generates the shot/goal.
                if ev["event_type"] == "goal":
                    out[team_id]["sh_goals_scored"] += 1

    if prev_state is not None and events:
        flush_window(events[-1]["period_number"], events[-1]["seconds_elapsed_in_period"], prev_state)

    taken_drawn = pw.team_penalties_taken_drawn(conn, game_id, home_team_id, away_team_id)
    for team_id, counts in taken_drawn.items():
        if team_id in out:
            out[team_id]["penalties_taken"] = counts["penalties_taken"]
            out[team_id]["penalties_drawn"] = counts["penalties_drawn"]

    return out


def _blank_row() -> dict:
    return {
        "pp_opportunities": 0, "pp_seconds": 0.0, "pp_shots": 0, "pp_goals": 0,
        "sh_seconds": 0.0, "sh_shots_allowed": 0, "sh_goals_allowed": 0, "sh_goals_scored": 0,
        "penalties_taken": 0, "penalties_drawn": 0,
        "five_v_five_goals": 0, "five_v_five_sog": 0,
    }
