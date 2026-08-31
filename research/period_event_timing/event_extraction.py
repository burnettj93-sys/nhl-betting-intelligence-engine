"""
Parts 2/3/10/11: ONE full-corpus pass that extracts every real
(non-shootout) goal with its full context (period, elapsed/remaining
time, manpower state at the moment of the goal, empty-net flag, score
differential for the scoring team BEFORE the goal, home/away), plus
period-binned SOG/goal/penalty counts for period-intensity and
within-period-timing research -- built together so the 1.6M-event corpus
is scanned once, not once per downstream question (Part 69/70).

Shootout is excluded entirely (period_type != 'SO'), matching this
project's own existing "shootout is never a statistical goal" rule
(research/real_nhl_pbp/invariants.py::check_shootout_excluded_from_statistical),
reused here as policy, not re-derived.
"""
from __future__ import annotations

import sqlite3

from research.period_event_timing import manpower as mp
from research.real_nhl_pbp.normalize import is_empty_net_context

WITHIN_PERIOD_BIN_SECONDS = 300  # 5-minute bins (Part 11's example bins)
DELAYED_PENALTY_WINDOW_SECONDS = 30  # confirmed real gap (~19s) between a
                                       # delayed-penalty signal and the
                                       # resulting pull's situationCode change


def _strength_type(state: str, scoring_team_is_home: bool) -> str:
    if mp.is_empty_net_state(state):
        return "EMPTY_NET"
    if mp.is_even_strength(state):
        return "EVEN"
    pp_home = mp.is_power_play_for_home(state)
    if pp_home is None:
        return "OTHER"
    scoring_team_on_pp = (pp_home and scoring_team_is_home) or (not pp_home and not scoring_team_is_home)
    return "PP" if scoring_team_on_pp else "SH"


def extract_game(conn: sqlite3.Connection, game_id: int, home_team_id: int, away_team_id: int,
                  season: str) -> dict:
    cur = conn.cursor()
    cur.execute(
        """SELECT event_id, event_type, period_number, period_type, seconds_elapsed_in_period,
                  seconds_remaining_in_period, situation_code, team_id
           FROM pbp_events WHERE game_id = ? AND period_type != 'SO'
           ORDER BY event_sequence""",
        (game_id,))
    events = cur.fetchall()

    goals = []
    home_score, away_score = 0, 0
    first_goal_seen = False
    period_sog_counts: dict[int, int] = {}
    period_goal_counts: dict[int, int] = {}
    within_period_goal_bins: dict[int, int] = {}   # bin index (across all periods) -> count
    pulled_state = {"HOME": False, "AWAY": False}
    first_pull: dict[str, dict] = {}   # "HOME"/"AWAY" -> pull info, first occurrence only
    last_delayed_penalty_game_seconds = None

    for event_id, event_type, period_number, period_type, secs_elapsed, secs_remaining, sitcode, team_id in events:
        state = mp.classify_manpower_state(sitcode)
        game_seconds_now = (period_number - 1) * 1200 + secs_elapsed
        if event_type == "shot-on-goal":
            period_sog_counts[period_number] = period_sog_counts.get(period_number, 0) + 1
        if event_type == "delayed-penalty":
            last_delayed_penalty_game_seconds = game_seconds_now

        # Part 20/21: goalie-pull detection -- independent of event_type,
        # a team's own goalie can show pulled on ANY event's situationCode
        # (most often stoppages/faceoffs right after the pull, not a goal).
        # A pull within DELAYED_PENALTY_WINDOW_SECONDS of a real
        # 'delayed-penalty' event is the routine, universal "pull for the
        # extra attacker while the penalty is delayed" tactic -- confirmed
        # against a real game (delayed-penalty at t=770s, situationCode
        # showing the pull at t=789s, 19s later) -- and is tagged
        # separately from a genuine late-game trailing desperation pull,
        # since conflating the two would badly distort both distributions
        # (Part 20/21 cares about the trailing phenomenon specifically).
        parsed = mp.parse_situation_code(sitcode)
        if parsed is not None:
            for side, goalie_in, skaters in (
                    ("AWAY", parsed["away_goalie_in"], parsed["away_skaters"]),
                    ("HOME", parsed["home_goalie_in"], parsed["home_skaters"])):
                is_pulled_now = (not goalie_in) and skaters == mp.MAX_VALID_SKATERS
                if is_pulled_now and not pulled_state[side] and side not in first_pull:
                    diff = (home_score - away_score) if side == "HOME" else (away_score - home_score)
                    is_delayed_penalty_pull = (
                        last_delayed_penalty_game_seconds is not None
                        and 0 <= game_seconds_now - last_delayed_penalty_game_seconds <= DELAYED_PENALTY_WINDOW_SECONDS)
                    first_pull[side] = {
                        "seconds_into_game": game_seconds_now,
                        "period_number": period_number, "period_type": period_type,
                        "score_differential_at_pull": diff,
                        "reason": "DELAYED_PENALTY_EXTRA_ATTACKER" if is_delayed_penalty_pull else "OTHER",
                    }
                pulled_state[side] = is_pulled_now

        if event_type != "goal":
            continue

        is_home = team_id == home_team_id
        score_diff_before = (home_score - away_score) if is_home else (away_score - home_score)
        # is_empty_net_context needs "defending_team_is_away": the team that
        # CONCEDED is the one that is NOT the scoring team.
        empty_net = False
        if sitcode is not None and len(sitcode) == 4:
            defending_is_away = is_home  # scorer is home -> defender is away
            goalie_digit = sitcode[0] if defending_is_away else sitcode[3]
            empty_net = goalie_digit == "0"

        goals.append({
            "game_id": game_id, "event_id": event_id, "season": season,
            "period_number": period_number, "period_type": period_type,
            "seconds_elapsed_in_period": secs_elapsed, "seconds_remaining_in_period": secs_remaining,
            "team_id": team_id, "is_home": is_home,
            "manpower_state": state, "strength_type": _strength_type(state, is_home),
            "is_empty_net": empty_net,
            "home_score_before": home_score, "away_score_before": away_score,
            "score_differential_before": score_diff_before,
            "is_first_goal_of_game": not first_goal_seen,
        })
        first_goal_seen = True
        period_goal_counts[period_number] = period_goal_counts.get(period_number, 0) + 1
        if is_home:
            home_score += 1
        else:
            away_score += 1

        if period_type == "REG":
            bin_idx = (period_number - 1) * (1200 // WITHIN_PERIOD_BIN_SECONDS) + \
                       min(secs_elapsed // WITHIN_PERIOD_BIN_SECONDS, 1200 // WITHIN_PERIOD_BIN_SECONDS - 1)
            within_period_goal_bins[bin_idx] = within_period_goal_bins.get(bin_idx, 0) + 1

    final_home, final_away = home_score, away_score
    first_goal = goals[0] if goals else None
    if first_goal is None:
        first_team_to_score = "NONE"
    else:
        first_team_to_score = "HOME" if first_goal["is_home"] else "AWAY"

    return {
        "game_id": game_id, "season": season, "home_team_id": home_team_id, "away_team_id": away_team_id,
        "goals": goals, "final_home_score": final_home, "final_away_score": final_away,
        "period_sog_counts": period_sog_counts, "period_goal_counts": period_goal_counts,
        "within_period_goal_bins": within_period_goal_bins,
        "first_team_to_score": first_team_to_score,
        "first_goal_seconds_into_game": (
            (first_goal["period_number"] - 1) * 1200 + first_goal["seconds_elapsed_in_period"]
            if first_goal else None),
        "first_pull": first_pull,
    }
