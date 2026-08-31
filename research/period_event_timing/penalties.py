"""
Part 6: penalty / manpower-window reconstruction.

Rather than trusting each penalty event's OWN declared duration (which
isn't persisted in the normalized pbp_events table -- see store.py's
schema, Part 69's "do not duplicate the corpus" -- and which wouldn't by
itself account for a PP goal ending a penalty early, offsetting
coincidental minors producing no real advantage, or a second penalty
stacking a 5-on-4 into a 5-on-3), this reconstructs REALIZED manpower
windows directly from situationCode transitions across each game's own
event sequence -- the same source of truth manpower.py already validated.
A "power play window" is a maximal run of consecutive events sharing one
non-even-strength, non-empty-net manpower state; its real duration, its
ending reason (goal / expired-back-to-even / escalated-to-a-different-
advantage / period ended), and 5-on-3 detection all fall directly out of
this reconstruction rather than being separately inferred.

Raw `penalty` events (already normalized, already in pbp_event_players)
are used only for the separate, simpler "penalties taken/drawn" COUNTS
(Part 7) -- a coincidental minor still counts as one penalty taken even
though it produces no realized advantage window.
"""
from __future__ import annotations

import sqlite3

from research.period_event_timing import manpower as mp

PERIOD_LENGTH_SECONDS = 1200  # 20 min -- used only to order/duration windows
                                # that happen to straddle a period boundary
                                # (rare); OT's real length (5 or 20 min) does
                                # not affect ordering, only reported duration
                                # for that rare cross-boundary case.


def game_clock_seconds(period_number: int, seconds_elapsed_in_period: int) -> float:
    return (period_number - 1) * PERIOD_LENGTH_SECONDS + seconds_elapsed_in_period


def fetch_game_events_for_windows(conn: sqlite3.Connection, game_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT event_id, event_sequence, event_type, period_number, period_type,
                  seconds_elapsed_in_period, situation_code, team_id
           FROM pbp_events WHERE game_id = ? AND period_type != 'SO'
           ORDER BY event_sequence""",
        (game_id,))
    cols = ["event_id", "event_sequence", "event_type", "period_number", "period_type",
            "seconds_elapsed_in_period", "situation_code", "team_id"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_manpower_windows(events: list[dict], home_team_id: int, away_team_id: int) -> list[dict]:
    """One row per maximal contiguous run of a single classified manpower
    state (Part 6). `advantaged_team` is "HOME"/"AWAY"/None (even strength
    or empty net -- "power play" isn't a meaningful label there, matching
    manpower.is_power_play_for_home's own None case). `ended_by` is
    "GOAL" (a goal event fell inside this window and the very next window
    is a different, less-advantaged state -- i.e. the penalty was killed
    by a goal), "PERIOD_END" (the window's last event is the period's
    last event), or "STATE_CHANGE" (expired back to even strength, or
    escalated/de-escalated to a different advantage, e.g. a stacked
    5-on-3)."""
    windows = []
    current = None
    for i, ev in enumerate(events):
        state = mp.classify_manpower_state(ev["situation_code"])
        if current is None or state != current["state"]:
            if current is not None:
                windows.append(current)
            current = {
                "state": state, "start_event_sequence": ev["event_sequence"],
                "end_event_sequence": ev["event_sequence"],
                "period_number": ev["period_number"],
                "start_seconds_elapsed": ev["seconds_elapsed_in_period"],
                "end_seconds_elapsed": ev["seconds_elapsed_in_period"],
                "end_period_number": ev["period_number"],
                "contains_goal": ev["event_type"] == "goal",
                "goal_scoring_team_id": ev["team_id"] if ev["event_type"] == "goal" else None,
            }
        else:
            current["end_event_sequence"] = ev["event_sequence"]
            current["end_seconds_elapsed"] = ev["seconds_elapsed_in_period"]
            current["end_period_number"] = ev["period_number"]
            if ev["event_type"] == "goal":
                current["contains_goal"] = True
                current["goal_scoring_team_id"] = ev["team_id"]
    if current is not None:
        windows.append(current)

    is_last_event_of_game = {events[-1]["event_sequence"]} if events else set()
    for idx, w in enumerate(windows):
        # True duration runs until the NEXT window's first event (the
        # actual moment the state changed), not this window's own last
        # observed event -- events are sparse, so the gap between "last
        # shot recorded during the PP" and "the faceoff/stoppage that
        # confirms the PP ended" would otherwise be silently dropped,
        # systematically underestimating every window's real length
        # (found and fixed during this sprint's own test-writing).
        if idx + 1 < len(windows):
            end_period = windows[idx + 1]["period_number"]
            end_seconds = windows[idx + 1]["start_seconds_elapsed"]
        else:
            end_period, end_seconds = w["end_period_number"], w["end_seconds_elapsed"]
        w["duration_seconds"] = (
            game_clock_seconds(end_period, end_seconds)
            - game_clock_seconds(w["period_number"], w["start_seconds_elapsed"]))
        pp_home = mp.is_power_play_for_home(w["state"])
        w["advantaged_team"] = "HOME" if pp_home is True else ("AWAY" if pp_home is False else None)
        w["is_5_on_3"] = w["state"] in ("5v3", "3v5")
        w["is_empty_net"] = mp.is_empty_net_state(w["state"])
        if w["end_event_sequence"] in is_last_event_of_game:
            w["ended_by"] = "PERIOD_END"
        elif w["contains_goal"] and idx + 1 < len(windows):
            w["ended_by"] = "GOAL"
        elif idx + 1 < len(windows) and windows[idx + 1]["period_number"] != w["end_period_number"]:
            w["ended_by"] = "PERIOD_END"
        else:
            w["ended_by"] = "STATE_CHANGE"
    return windows


def penalty_window_summary(windows: list[dict]) -> dict:
    """Part 6 required outputs, aggregated across whatever window list is
    passed in (one game or many): PP opportunity count/seconds per
    ending reason, 5-on-3 window count, and a rough overlapping/
    coincidental-penalty signal (a manpower-advantage window immediately
    followed by a DIFFERENT team's advantage window with no even-strength
    gap between them -- consistent with one penalty expiring while
    another, drawn on the other team, was already running)."""
    pp_windows = [w for w in windows if w["advantaged_team"] is not None and not w["is_empty_net"]]
    by_ended_reason: dict[str, int] = {}
    for w in pp_windows:
        by_ended_reason[w["ended_by"]] = by_ended_reason.get(w["ended_by"], 0) + 1

    overlapping_transitions = 0
    for i in range(len(windows) - 1):
        a, b = windows[i], windows[i + 1]
        if (a["advantaged_team"] is not None and b["advantaged_team"] is not None
                and a["advantaged_team"] != b["advantaged_team"]
                and not a["is_empty_net"] and not b["is_empty_net"]):
            overlapping_transitions += 1

    return {
        "total_windows": len(windows),
        "pp_opportunity_windows": len(pp_windows),
        "pp_seconds_total": sum(w["duration_seconds"] for w in pp_windows),
        "pp_windows_by_ended_reason": by_ended_reason,
        "five_on_three_windows": sum(1 for w in pp_windows if w["is_5_on_3"]),
        "goal_terminated_pp_windows": by_ended_reason.get("GOAL", 0),
        "possible_overlapping_penalty_transitions": overlapping_transitions,
    }


def team_penalties_taken_drawn(conn: sqlite3.Connection, game_id: int,
                                home_team_id: int, away_team_id: int) -> dict[int, dict]:
    """Simple, direct counts from the already-normalized `penalty` event
    rows -- one row per penalty, attributed to `event.team_id` (the
    penalized team; this is the feed's own `eventOwnerTeamId`) for
    "taken".

    "Drawn" is deliberately NOT read from pbp_event_players' `drawn_by`
    role: that table stamps EVERY role of an event (including
    `drawn_by`) with the EVENT's own owner team_id (see store.py's
    insert loop, `player_rows.append((..., e.team_id))`) -- i.e. it
    records the PENALIZED team for that role too, not the drawing
    player's real team. In a 2-team game the team that drew the penalty
    is unambiguously the OTHER team from the one charged with it, so
    "drawn" is computed as the game's other team_id instead of trusting
    that column for this one role."""
    cur = conn.cursor()
    cur.execute(
        """SELECT event_id, team_id FROM pbp_events
           WHERE game_id = ? AND event_type = 'penalty' AND period_type != 'SO'""",
        (game_id,))
    taken: dict[int, int] = {}
    drawn: dict[int, int] = {}
    for event_id, team_id in cur.fetchall():
        if team_id is None:
            continue
        taken[team_id] = taken.get(team_id, 0) + 1
        other = away_team_id if team_id == home_team_id else (
            home_team_id if team_id == away_team_id else None)
        if other is not None:
            drawn[other] = drawn.get(other, 0) + 1

    teams = set(taken) | set(drawn)
    return {t: {"penalties_taken": taken.get(t, 0), "penalties_drawn": drawn.get(t, 0)} for t in teams}
