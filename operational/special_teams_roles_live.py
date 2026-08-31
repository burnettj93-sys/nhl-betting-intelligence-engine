"""
Part 7/9: the LIVE/operational role-feature computation. Guarantees
exact parity with the historical research detector NOT by re-deriving
the same logic a second time (Part 7's explicit warning against a
"slightly different live version"), but by literally importing and
calling the SAME functions from
research.period_event_timing.special_teams_roles -- unit-ranking,
mode-based recent/baseline classification, transition naming, magnitude
computation are all the frozen, unmodified research code. The only new
code here is the DATA PLUMBING: reading from
operational/special_teams_history.db (real completed games, live or
backfilled) instead of the research JSONL corpora, and enforcing the
strict PIT boundary against a caller-supplied `as_of_date` (Part 3).
"""
from __future__ import annotations

import sqlite3

from operational import special_teams_history_store as sths
from research.period_event_timing import special_teams_roles as sr
from research.special_teams_role_overlay import core as ov_core

RECENT_GAMES = sr.RECENT_GAMES
BASELINE_GAMES = sr.BASELINE_GAMES


def _team_pp_sh_totals_by_game(conn: sqlite3.Connection, game_ids: list[int]) -> dict:
    if not game_ids:
        return {}
    placeholders = ",".join("?" * len(game_ids))
    cur = conn.execute(
        f"""SELECT game_id, team, SUM(pp_toi_seconds), SUM(sh_toi_seconds)
            FROM special_teams_history WHERE game_id IN ({placeholders})
            GROUP BY game_id, team""", game_ids)
    out = {}
    for game_id, team, pp_sum, sh_sum in cur.fetchall():
        out[(game_id, team)] = {"pp_team_total": pp_sum, "sh_team_total": sh_sum}
    return out


def _label_unit(rank: int, toi: float) -> str:
    if toi < sr.MIN_MEANINGFUL_TOI_SECONDS:
        return "NONE"
    if rank < sr.PP_UNIT_SIZE:
        return "UNIT1"
    if rank < 2 * sr.PP_UNIT_SIZE:
        return "UNIT2"
    return "NONE"


def _game_unit_label(conn: sqlite3.Connection, game_id: int, team: str, player_id: str,
                      toi_field: str) -> str | None:
    """Reproduces research.special_teams_roles.build_game_unit_labels'
    exact per-team-game ranking rule, but for a single (game, team, toi
    field) -- reused inline here (not re-imported per-row for
    performance reasons) with IDENTICAL constants
    (sr.PP_UNIT_SIZE / sr.MIN_MEANINGFUL_TOI_SECONDS), never
    independently redefined."""
    cur = conn.execute(
        f"SELECT player_id, {toi_field} FROM special_teams_history WHERE game_id=? AND team=?",
        (game_id, team))
    ranked = sorted(cur.fetchall(), key=lambda row: row[1] or 0.0, reverse=True)
    for i, (pid, toi) in enumerate(ranked):
        if pid == player_id:
            return _label_unit(i, toi or 0.0)
    return None


def _most_recent_tenure(history: list[dict], current_team: str) -> list[dict]:
    """Real bug found and fixed while building this module: a player who
    LEFT `current_team` and was later RE-ACQUIRED by it (a real, if rare,
    case -- confirmed on a real player who left Tampa Bay in 2023 and
    returned in 2026) would otherwise have games from BOTH stints, years
    apart, treated as one contiguous "current team" history -- the
    recent/baseline windows could then mix a 3-year-old game with a
    yesterday's game. This walks backward from the most recent game on
    record and stops at the first team change, returning only the
    player's LATEST contiguous run with `current_team` (chronological
    order preserved)."""
    if not history:
        return []
    last_idx = next((i for i in range(len(history) - 1, -1, -1) if history[i]["team"] == current_team), None)
    if last_idx is None:
        return []
    start_idx = last_idx
    while start_idx > 0 and history[start_idx - 1]["team"] == current_team:
        start_idx -= 1
    return history[start_idx:last_idx + 1]


def compute_player_role_state(conn: sqlite3.Connection, player_id: str, current_team: str,
                               as_of_date: str, toi_field: str = "pp_toi_seconds",
                               unit_prefix: str = "PP", _include_transition_info: bool = True) -> dict:
    """The live, prospective entry point: role state for `player_id` as
    of `as_of_date`, using ONLY games with game_date < as_of_date (Part
    3 -- enforced by player_history_before's own strict `<`). Restricts
    the recent/baseline windows to games played for the player's
    CURRENT team (Part 6/42: a trade resets the operationally-relevant
    role history, even though the player's own skill history in other
    modules is preserved elsewhere) -- a game played for a PRIOR team is
    excluded from both windows, so a freshly-traded player naturally
    lands in ROLE_UNCERTAIN until enough current-team games accumulate,
    never silently carrying over a stale unit label from the old team.
    """
    history = sths.player_history_before(conn, player_id, as_of_date)
    current_team_games = _most_recent_tenure(history, current_team)
    if not current_team_games:
        return {"state": "ROLE_UNCERTAIN", "recent_role": None, "baseline_role": None,
                "n_recent": 0, "n_baseline": 0, "reason": "no games on record for current team"}

    recent_slice = current_team_games[-RECENT_GAMES:]
    baseline_slice = current_team_games[-(RECENT_GAMES + BASELINE_GAMES):-RECENT_GAMES]

    recent_labels, baseline_labels = [], []
    for g in recent_slice:
        label = _game_unit_label(conn, g["game_id"], g["team"], player_id, toi_field)
        if label:
            recent_labels.append(label)
    for g in baseline_slice:
        label = _game_unit_label(conn, g["game_id"], g["team"], player_id, toi_field)
        if label:
            baseline_labels.append(label)

    result = sr.classify_role_state(recent_labels, baseline_labels, unit_prefix)

    recent_toi = [g[toi_field] for g in recent_slice]
    baseline_toi = [g[toi_field] for g in baseline_slice]
    team_totals = _team_pp_sh_totals_by_game(
        conn, [g["game_id"] for g in recent_slice + baseline_slice])
    total_key = "pp_team_total" if toi_field == "pp_toi_seconds" else "sh_team_total"
    recent_team_toi = [team_totals.get((g["game_id"], g["team"]), {}).get(total_key, 0.0) for g in recent_slice]
    baseline_team_toi = [team_totals.get((g["game_id"], g["team"]), {}).get(total_key, 0.0) for g in baseline_slice]
    magnitude = sr.role_change_magnitude(recent_toi, baseline_toi, recent_team_toi, baseline_team_toi)

    result.update(magnitude)
    result["last_game_date_on_record"] = current_team_games[-1]["game_date"]

    if not _include_transition_info:
        result["games_since_onset"] = None
        result["direction"] = None
        return result

    # Part 14/62: games-since-onset/direction for the SOG shadow overlay's
    # transition term -- computed by walking this player's OWN current-
    # team game sequence once and reusing
    # research_overlay.core.add_games_since_onset verbatim (the exact
    # same function the historical fit was validated against), never a
    # second, live-only reimplementation of onset tracking.
    # `_include_transition_info=False` on the inner per-game calls below
    # is essential -- without it, each of these n calls would recurse
    # into computing its OWN n-length sequence, an O(n^2) blowup.
    state_sequence = [
        compute_player_role_state(conn, player_id, current_team, g["game_date"], toi_field, unit_prefix,
                                   _include_transition_info=False)["state"]
        for g in current_team_games
    ] if len(current_team_games) > 1 else []
    if state_sequence:
        seq_rows = [{"state": s} for s in state_sequence]
        ov_core.add_games_since_onset({"_": seq_rows}, "state", "since", "direction")
        result["games_since_onset"] = seq_rows[-1]["since"]
        result["direction"] = seq_rows[-1]["direction"]
    else:
        result["games_since_onset"] = None
        result["direction"] = None
    return result


def compute_pp_role_state(conn: sqlite3.Connection, player_id: str, current_team: str, as_of_date: str) -> dict:
    return compute_player_role_state(conn, player_id, current_team, as_of_date, "pp_toi_seconds", "PP")


def compute_pk_role_state(conn: sqlite3.Connection, player_id: str, current_team: str, as_of_date: str) -> dict:
    return compute_player_role_state(conn, player_id, current_team, as_of_date, "sh_toi_seconds", "PK")
