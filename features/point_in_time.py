"""
Point-in-time (PIT) queries — the single mechanism that prevents look-ahead
leakage. Every function here takes an explicit `prediction_time_utc` and
filters strictly on `observed_at_utc <= prediction_time_utc` (or the
equivalent schedule/result-specific column). Nothing in this module infers
"what was known" from game_id order, insertion order, list position,
current wall-clock time, or caller discipline.

If you need a new point-in-time fact, add it here with the same pattern —
never read roster/lineup/goalie/injury/odds/schedule/stat facts directly
from a table anywhere else in the codebase. This is the ONLY module
permitted to issue a raw SELECT against: team_membership_events,
roster_status_events, lineup_snapshots, pp_unit_snapshots,
goalie_status_events, odds_snapshots, game_schedule_events,
player_game_stats, goalie_game_stats, game_result_events. (Write paths in ingest/*.py and
ingest/demo_data.py are the sanctioned exception — INSERTing an event is
not a point-in-time read. tests/helpers.py's Fixture is a second sanctioned
exception, for constructing test scenarios. See
tests/test_structural_reads.py, which audits this mechanically.)

v2.1 (temporal-hardening pass) additions: `game_schedule_as_of` /
`scheduled_games_before` now read the append-only `game_schedule_events`
table instead of the mutable `games` cache columns, so a later schedule
correction cannot change what an earlier prediction reconstructs.
`completed_games_known_before` is the ONLY sanctioned way to decide which
historical games are eligible to update model state for a prediction at a
given time — it orders strictly by when a result was first observed, never
by game_id or game_date, so out-of-order / rescheduled / late-finishing
games are handled correctly. `player_game_stats_as_of` / `goalie_game_stats_as_of`
read only the latest stat revision that had been observed by a given
learn_time_utc, so a later box-score correction cannot retroactively alter
what a historical model update learned.

v2.1.1 addition: `game_result_as_of` / `game_result_first_observed_at` read
the append-only, revision-versioned `game_result_events` table, NOT the
mutable `games.home_score`/`away_score`/`result_observed_at_utc` cache
columns — `completed_games_known_before` now derives both eligibility and
chronological order from this table, so a later score correction can
neither change when a game "first became known" nor retroactively alter
what a historical model learned from it. See models/combined_model.py::learn().
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


# ---------------------------------------------------------------- roster --

def team_of_player(conn: sqlite3.Connection, player_id: str, prediction_time_utc: str) -> str | None:
    """Which team a player belonged to as of prediction_time_utc, per the
    latest team_membership_events row we had OBSERVED by then. A trade
    recorded after prediction_time_utc must not change this."""
    row = conn.execute(
        """SELECT team_id FROM team_membership_events
           WHERE player_id=? AND observed_at_utc <= ?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (player_id, prediction_time_utc),
    ).fetchone()
    return row["team_id"] if row else None


def roster_ids_for_team(conn: sqlite3.Connection, team_id: str, prediction_time_utc: str) -> list[str]:
    """Every player whose latest observed-by-prediction_time membership
    row points at this team (their 'organizational' roster, not tonight's
    lineup — see lineup_for_game for that)."""
    rows = conn.execute(
        """SELECT player_id, team_id FROM (
               SELECT player_id, team_id,
                      ROW_NUMBER() OVER (
                          PARTITION BY player_id
                          ORDER BY observed_at_utc DESC, id DESC
                      ) AS rn
               FROM team_membership_events
               WHERE observed_at_utc <= ?
           ) WHERE rn = 1 AND team_id = ?""",
        (prediction_time_utc, team_id),
    ).fetchall()
    return [r["player_id"] for r in rows]


def roster_status(conn: sqlite3.Connection, player_id: str, prediction_time_utc: str) -> str:
    """Latest availability status (ACTIVE/OUT/IR/...) observed by
    prediction_time_utc. Defaults to ACTIVE if nothing's ever been
    reported — an unreported player is assumed healthy, not excluded."""
    row = conn.execute(
        """SELECT status FROM roster_status_events
           WHERE player_id=? AND observed_at_utc <= ?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (player_id, prediction_time_utc),
    ).fetchone()
    return row["status"] if row else "ACTIVE"


def available_roster(conn: sqlite3.Connection, team_id: str, prediction_time_utc: str) -> list[str]:
    """Organizational roster minus anyone whose latest observed status is
    not ACTIVE. This is the pregame-safe replacement for reading the final
    box score's `played` column."""
    roster = roster_ids_for_team(conn, team_id, prediction_time_utc)
    return [pid for pid in roster if roster_status(conn, pid, prediction_time_utc) == "ACTIVE"]


# ---------------------------------------------------------------- lineup --

def lineup_for_game(conn: sqlite3.Connection, game_id: int, team_id: str,
                     prediction_time_utc: str) -> dict[str, dict]:
    """Latest-observed-by-prediction_time role per player for this game
    (their most recent lineup_snapshots row). Returns {player_id: {role,
    status}}. Empty dict means no lineup information yet."""
    rows = conn.execute(
        """SELECT player_id, role, status FROM (
               SELECT player_id, role, status,
                      ROW_NUMBER() OVER (
                          PARTITION BY player_id
                          ORDER BY observed_at_utc DESC, id DESC
                      ) AS rn
               FROM lineup_snapshots
               WHERE game_id=? AND team_id=? AND observed_at_utc <= ?
           ) WHERE rn = 1""",
        (game_id, team_id, prediction_time_utc),
    ).fetchall()
    return {r["player_id"]: {"role": r["role"], "status": r["status"]} for r in rows}


# ----------------------------------------------------------------- goalie --

@dataclass
class GoalieStatus:
    player_id: str | None
    status: str          # UNKNOWN / EXPECTED / CONFIRMED / CHANGED
    observed_at_utc: str | None


def goalie_status(conn: sqlite3.Connection, game_id: int, team_id: str,
                   prediction_time_utc: str) -> GoalieStatus:
    row = conn.execute(
        """SELECT player_id, status, observed_at_utc FROM goalie_status_events
           WHERE game_id=? AND team_id=? AND observed_at_utc <= ?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (game_id, team_id, prediction_time_utc),
    ).fetchone()
    if row is None:
        return GoalieStatus(None, "UNKNOWN", None)
    return GoalieStatus(row["player_id"], row["status"], row["observed_at_utc"])


# ------------------------------------------------------------- schedule --

def game_schedule_as_of(conn: sqlite3.Connection, game_id: int,
                         prediction_time_utc: str) -> dict | None:
    """The latest-observed-by-prediction_time schedule facts for one game,
    from the APPEND-ONLY game_schedule_events history — never from games'
    mutable cache columns (see schema.sql's note on that table). Returns
    None if no schedule fact for this game had been observed yet as of
    prediction_time_utc (the game effectively didn't exist to the system
    at that point). A later schedule correction (time/venue/teams changed)
    is a new row with a later observed_at_utc and is correctly excluded
    here for any prediction_time_utc before that correction was observed."""
    row = conn.execute(
        """SELECT game_date, scheduled_start_utc, home_team, away_team, venue,
                  effective_at_utc, observed_at_utc
           FROM game_schedule_events
           WHERE game_id=? AND observed_at_utc <= ?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (game_id, prediction_time_utc),
    ).fetchone()
    return dict(row) if row is not None else None


def scheduled_games_before(conn: sqlite3.Connection, team_id: str, before_date: str,
                            prediction_time_utc: str) -> list[str]:
    """Game dates for this team strictly before `before_date`, restricted
    to games whose SCHEDULE (not result) had been observed by
    prediction_time_utc — reconstructed entirely from game_schedule_events,
    so a later reschedule of one of these OTHER games can't change what an
    earlier prediction sees either. Rest features must use this, not
    result data — the schedule is public long before any given game is
    played. Uses the LATEST schedule fact observed for each game by
    prediction_time_utc (not the latest in absolute terms), same
    point-in-time discipline as every other function in this module."""
    rows = conn.execute(
        """SELECT game_date FROM (
               SELECT game_id, game_date, home_team, away_team,
                      ROW_NUMBER() OVER (
                          PARTITION BY game_id
                          ORDER BY observed_at_utc DESC, id DESC
                      ) AS rn
               FROM game_schedule_events
               WHERE observed_at_utc <= ?
           )
           WHERE rn = 1 AND (home_team = ? OR away_team = ?) AND game_date < ?
           ORDER BY game_date""",
        (prediction_time_utc, team_id, team_id, before_date),
    ).fetchall()
    return [r["game_date"] for r in rows]


def rest_context(conn: sqlite3.Connection, game_id: int, team_id: str,
                  prediction_time_utc: str) -> dict:
    """Rolling schedule-congestion features computed purely from the
    point-in-time schedule (see game_schedule_as_of / scheduled_games_before)
    — works identically for a completed game or a future one, since it
    never needs the result, and is immune to a later reschedule of the
    TARGET game itself (a real leakage risk fixed in v2.1: this used to
    read the target's game_date directly from the mutable `games` cache
    with no time guard at all)."""
    import datetime as dt

    sched = game_schedule_as_of(conn, game_id, prediction_time_utc)
    if sched is None:
        raise ValueError(f"no schedule observed for game_id {game_id} as of {prediction_time_utc}")
    target_date = dt.date.fromisoformat(sched["game_date"][:10])

    prior_dates = [dt.date.fromisoformat(d[:10]) for d in
                   scheduled_games_before(conn, team_id, sched["game_date"], prediction_time_utc)]
    if not prior_dates:
        rest_days = 5   # no prior game observed yet -> treat as fully rested
    else:
        rest_days = (target_date - max(prior_dates)).days

    def games_in_last(n_days: int) -> int:
        cutoff = target_date - dt.timedelta(days=n_days)
        return sum(1 for d in prior_dates if cutoff <= d < target_date)

    games_3 = games_in_last(3)
    games_4 = games_in_last(4)
    games_5 = games_in_last(5)
    games_6 = games_in_last(6)
    games_7 = games_in_last(7)
    games_10 = games_in_last(10)

    return {
        "rest_days": rest_days,
        "back_to_back": int(rest_days <= 1),
        "three_in_four": int(games_4 >= 2),     # this game + 2 more in the prior 4 days = 3-in-4
        "four_in_six": int(games_6 >= 3),       # this game + 3 more in the prior 6 days = 4-in-6
        "games_last_3_days": games_3,
        "games_last_4_days": games_4,
        "games_last_5_days": games_5,
        "games_last_6_days": games_6,
        "games_last_7_days": games_7,
        "games_last_10_days": games_10,
    }


# -------------------------------------------------------------- odds/DK --

DEFAULT_REFERENCE_SPORTSBOOK = "DraftKings"


def latest_draftkings_snapshot(conn: sqlite3.Connection, game_id: int, market: str,
                                selection: str, prediction_time_utc: str,
                                max_staleness_minutes: float | None = None,
                                sportsbook: str = DEFAULT_REFERENCE_SPORTSBOOK):
    """The latest ACTIVE DraftKings price for (game_id, market, selection)
    that was BOTH captured_at_utc <= prediction_time_utc AND
    received_at_utc <= prediction_time_utc -- i.e. a price that not only
    existed at the book by the moment we're pricing, but that THIS SYSTEM
    had actually ingested by then too. A snapshot captured after
    prediction_time_utc (including the closing line) can never be
    returned here; neither can one this system had not yet received, even
    if the book's own capture timestamp was already in the past -- a
    quote isn't "known" until it's been received, exactly like every
    other observed_at_utc-style fact in this system (v2.1.1a spec item
    1). If the most-recently-captured eligible-by-capture-time quote had
    not yet been received by prediction_time_utc, the next-older quote
    that genuinely HAD been received by then is used instead -- never a
    fabricated "no data" when an older, actually-known quote exists.

    Rejects (returns None for): missing data, SUSPENDED/INCOMPLETE status,
    a price captured more than max_staleness_minutes before
    prediction_time_utc (if set), and a price captured at or after the
    event's scheduled start (post-start prices aren't pregame prices).
    """
    row = conn.execute(
        """SELECT * FROM odds_snapshots
           WHERE game_id=? AND sportsbook=? AND market=? AND selection=?
             AND captured_at_utc <= ?
             AND received_at_utc <= ?
             AND status = 'ACTIVE'
             AND price_american IS NOT NULL
             AND (event_start_utc IS NULL OR captured_at_utc < event_start_utc)
           ORDER BY captured_at_utc DESC, id DESC LIMIT 1""",
        (game_id, sportsbook, market, selection, prediction_time_utc, prediction_time_utc),
    ).fetchone()
    if row is None:
        return None
    if max_staleness_minutes is not None:
        import datetime as dt

        captured = dt.datetime.fromisoformat(row["captured_at_utc"])
        as_of = dt.datetime.fromisoformat(prediction_time_utc)
        age_minutes = (as_of - captured).total_seconds() / 60.0
        if age_minutes > max_staleness_minutes:
            return None
    return row


def latest_draftkings_two_sided(conn: sqlite3.Connection, game_id: int, market: str,
                                 selection_a: str, selection_b: str, prediction_time_utc: str,
                                 max_staleness_minutes: float | None = None):
    """Both sides of a DraftKings market as of prediction_time_utc, or
    (None, None) if either leg is missing/stale/suspended — callers must
    treat that as DATA UNAVAILABLE, never silently price one side alone or
    fall back to another book."""
    a = latest_draftkings_snapshot(conn, game_id, market, selection_a, prediction_time_utc,
                                    max_staleness_minutes)
    b = latest_draftkings_snapshot(conn, game_id, market, selection_b, prediction_time_utc,
                                    max_staleness_minutes)
    if a is None or b is None:
        return None, None
    return a, b


def closing_draftkings_snapshot(conn: sqlite3.Connection, game_id: int, market: str,
                                 selection: str, sportsbook: str = DEFAULT_REFERENCE_SPORTSBOOK):
    """The LAST DraftKings price captured before the event started — used
    ONLY for closing-line-value calculation after a bet has already been
    decided, never for the decision itself. Deliberately has no
    prediction_time_utc parameter: calling this from decision-time code is
    the bug this split is meant to catch.

    Correctness fix (Phase 1.5 Part A): previously this query had NO
    `captured_at_utc < event_start_utc` predicate at all, so it could
    return a live/post-puck-drop quote if one happened to be the
    most-recently-captured ACTIVE row for this game/market/selection --
    exactly backwards for a "closing line" (the last price BEFORE the
    game started). Fixed: the closing quote is now required to have been
    captured STRICTLY before the game's own scheduled start
    (`odds_snapshots.event_start_utc`), per-row, matching how every other
    pre-start eligibility check in this module already works (see
    latest_draftkings_snapshot()'s `captured_at_utc < event_start_utc`
    predicate above). A quote captured exactly AT event_start_utc is
    excluded (strict `<`, not `<=`) -- puck drop itself is not "before"
    puck drop. A row with a NULL event_start_utc is excluded here (unlike
    latest_draftkings_snapshot(), which treats NULL as "no pre-start
    boundary to enforce" for a live decision-time read) -- a "closing"
    price is meaningless without knowing the event's start time to close
    against, so silently accepting an undated row as "the close" would
    reintroduce the same category of bug this fix removes. See
    tests/test_closing_line_pre_start_integrity.py.

    Deliberately UNCHANGED by this fix (Phase 1.5 Part A is scoped to
    this one predicate only): no `received_at_utc` boundary parameter is
    added here yet -- that question depends on the historical-provider
    knowledge-time contract still under evaluation (Phase 1.5 Part H),
    not on this pre-start correctness bug."""
    row = conn.execute(
        """SELECT * FROM odds_snapshots
           WHERE game_id=? AND sportsbook=? AND market=? AND selection=?
             AND status='ACTIVE' AND price_american IS NOT NULL
             AND event_start_utc IS NOT NULL
             AND captured_at_utc < event_start_utc
           ORDER BY captured_at_utc DESC, id DESC LIMIT 1""",
        (game_id, sportsbook, market, selection),
    ).fetchone()
    return row


# ------------------------------------------------------ training eligibility --
#
# v2.1 (temporal-hardening pass): the ONLY sanctioned way anywhere in this
# codebase to decide which historical games are eligible to update model
# state for a prediction made at a given time. Never game_id < target_id,
# never games[:some_index], never game_date ordering alone -- a
# rescheduled, postponed, or late-finishing game can make ALL of those
# proxies wrong. The only correct rule is the timestamp comparison below.

def completed_games_known_before(conn: sqlite3.Connection,
                                  prediction_time_utc: str | None = None,
                                  strict: bool = True) -> list[int]:
    """game_ids whose FINAL result had genuinely been observed strictly
    before (or at-or-before, if strict=False) prediction_time_utc -- or
    every completed game currently in the database if prediction_time_utc
    is None. ALWAYS ordered by when the result was FIRST observed (the
    earliest observed_at_utc across that game's game_result_events
    revisions -- i.e. the moment the system first learned the game was
    FINAL), then game_id only as a tiebreaker for two results first
    observed at the identical timestamp -- never by game_date or game_id
    alone, and never by a LATER correction's timestamp. This is what makes
    an out-of-order game_id (a reschedule that finishes before an
    earlier-numbered game) resolve correctly: see
    tests/test_game_id_independence.py.

    v2.1.1: derives result availability entirely from the append-only
    game_result_events table (see game_result_as_of /
    game_result_first_observed_at below), never from the mutable
    games.result_observed_at_utc cache column -- a later score correction
    cannot change a game's first-known time or its position in this
    ordering. games.game_state/result_observed_at_utc are current-state
    cache only (schema.sql) and are never read here."""
    clauses = ["game_state = 'FINAL'"]
    params: list = []
    having = ""
    if prediction_time_utc is not None:
        op = "<" if strict else "<="
        having = f"HAVING first_observed {op} ?"
        params.append(prediction_time_utc)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""SELECT game_id, MIN(observed_at_utc) AS first_observed
            FROM game_result_events
            WHERE {where}
            GROUP BY game_id
            {having}
            ORDER BY first_observed, game_id""",
        params,
    ).fetchall()
    return [r["game_id"] for r in rows]


# ----------------------------------------------------- result revisions --
#
# v2.1.1: game_result_events is the append-only, revision-versioned source
# of truth for final scores -- mirrors player_game_stats/goalie_game_stats
# above. games.home_score/away_score/final_period_type/game_state/
# result_observed_at_utc are a current-state cache only (schema.sql) and
# must never be treated as authoritative for historical reconstruction.

def game_result_first_observed_at(conn: sqlite3.Connection, game_id: int) -> str | None:
    """The earliest observed_at_utc across this game's FINAL
    game_result_events revisions -- i.e. when the system FIRST learned
    this game was final, regardless of any later correction. None if no
    FINAL result has ever been observed for this game. This is what
    completed_games_known_before() orders by, and the default
    learn_time_utc CombinedMoneylineModel.learn() uses -- "the earliest
    legitimate moment this model is allowed to learn from the game at
    all," exactly mirroring the stat-revision default (see
    player_game_stats_as_of's docstring)."""
    row = conn.execute(
        """SELECT MIN(observed_at_utc) AS first_observed FROM game_result_events
           WHERE game_id=? AND game_state='FINAL'""",
        (game_id,),
    ).fetchone()
    return row["first_observed"] if row is not None else None


def game_result_as_of(conn: sqlite3.Connection, game_id: int,
                       as_of_utc: str) -> dict | None:
    """The latest-revision-observed-by-as_of_utc final result for this
    game, or None if no FINAL result had been observed yet as of
    as_of_utc. A later correction (observed_at_utc after as_of_utc) is
    correctly excluded -- see tests/test_result_revision.py's "Prediction
    B" scenario, the same pattern as player_game_stats_as_of /
    goalie_game_stats_as_of."""
    row = conn.execute(
        """SELECT home_score, away_score, final_period_type, game_state,
                  effective_at_utc, observed_at_utc, revision_number
           FROM (
               SELECT home_score, away_score, final_period_type, game_state,
                      effective_at_utc, observed_at_utc, revision_number,
                      ROW_NUMBER() OVER (
                          PARTITION BY game_id
                          ORDER BY observed_at_utc DESC, revision_number DESC
                      ) AS rn
               FROM game_result_events
               WHERE game_id=? AND observed_at_utc <= ?
           ) WHERE rn = 1""",
        (game_id, as_of_utc),
    ).fetchone()
    return dict(row) if row is not None else None


# --------------------------------------------------------- stat revisions --
#
# v2.1: player_game_stats / goalie_game_stats are revision-versioned (see
# schema.sql) so a provider's later box-score correction can never
# retroactively change what a historical model update learned. These two
# functions return, per player, only the latest revision that had been
# observed by learn_time_utc -- models/combined_model.py::learn() is the
# only caller.

def player_game_stats_as_of(conn: sqlite3.Connection, game_id: int,
                             learn_time_utc: str) -> list[sqlite3.Row]:
    """Latest-revision-observed-by-learn_time_utc stat row per player for
    this game, restricted to played=1 (see player_game_stats' docstring --
    still postgame-only data, never read pregame).

    v2.1.1a: also returns `observed_at_utc` -- the specific revision's own
    observation timestamp -- so a caller (CombinedMoneylineModel.learn())
    can track exactly how far forward in time the information it actually
    consumed extends, not merely assume it matches the game's result
    first-observed time (see spec item 2 / the model-knowledge-watermark
    fix)."""
    return conn.execute(
        """SELECT player_id, goals, assists, observed_at_utc FROM (
               SELECT player_id, goals, assists, played, observed_at_utc,
                      ROW_NUMBER() OVER (
                          PARTITION BY player_id
                          ORDER BY observed_at_utc DESC, revision_number DESC
                      ) AS rn
               FROM player_game_stats
               WHERE game_id = ? AND observed_at_utc <= ?
           ) WHERE rn = 1 AND played = 1""",
        (game_id, learn_time_utc),
    ).fetchall()


def goalie_game_stats_as_of(conn: sqlite3.Connection, game_id: int,
                             learn_time_utc: str) -> list[sqlite3.Row]:
    """Latest-revision-observed-by-learn_time_utc goalie stat row per
    goalie for this game.

    v2.1.1a: also returns `observed_at_utc` -- see
    player_game_stats_as_of's docstring for why."""
    return conn.execute(
        """SELECT player_id, saves, shots_against, observed_at_utc FROM (
               SELECT player_id, saves, shots_against, observed_at_utc,
                      ROW_NUMBER() OVER (
                          PARTITION BY player_id
                          ORDER BY observed_at_utc DESC, revision_number DESC
                      ) AS rn
               FROM goalie_game_stats
               WHERE game_id = ? AND observed_at_utc <= ?
           ) WHERE rn = 1""",
        (game_id, learn_time_utc),
    ).fetchall()
