"""
Real ingestion against the NHL's public API (api-web.nhle.com).

NOT RUNNABLE FROM THIS SANDBOX: this cloud workspace's outbound network is
restricted to package registries, so api-web.nhle.com is unreachable from
here (confirmed — a direct curl times out). This module is written for
real use anywhere with normal internet access. It has NOT been exercised
against a live response in this pass — treat it as a well-structured
starting point, not a tested integration, until it's actually run
somewhere with network access. See README's implemented/tested/
experimental/deferred table.

Temporal-integrity design (spec item 3):
  - Schedule data (a game exists, on this date, these teams) and result
    data (final score) are ingested and timestamped SEPARATELY — a
    schedule pull only ever calls ingest_schedule(); a result only ever
    comes from ingest_result(), called later, once game_state is OFF/FINAL.
    Never call ingest_result() from schedule data.
  - Roster ingestion writes team_membership_events, never overwrites a
    player's historical team. Re-running roster ingestion with an
    unchanged roster is a no-op (idempotent) — a new event row is written
    only when the player's latest known team actually differs.
  - Every write stamps both a source/effective time and an
    observed_at_utc (this system's ingestion time), per schema.sql.
  - v2.1.1 (spec item 6): every observed_at_utc/effective_at_utc/
    scheduled_start_utc-shaped value accepted here is passed through
    ingest/timestamps.py::normalize_utc_timestamp() before it is ever
    used in a SQL write -- a naive string, a "Z"-suffixed string, and an
    explicit-offset string that all denote the same instant are
    guaranteed to be stored as the identical canonical string, so the
    lexicographic/ISO-parse comparisons throughout
    features/point_in_time.py can never be misled by a representation
    mismatch. See ingest/timestamps.py's module docstring and
    tests/test_timestamp_normalization.py.

What this does NOT cover (see README "not yet built"):
  - Injuries / roster status: no public NHL injury-report API exists.
    record_roster_status() is the write path — plug a maintained source
    (PuckPedia, team pressers, beat reporters) into it.
  - Starting-goalie announcements: no public API for this either.
    record_goalie_status() is the write path — plug in Daily Faceoff or
    similar.
  - DraftKings odds: needs a licensed odds-data provider — see
    ingest/odds_provider.py's docstring for the DraftKings reference-book
    requirements this must satisfy.
"""
from __future__ import annotations

import datetime as dt
import time

from ingest.timestamps import normalize_utc_timestamp

BASE_URL = "https://api-web.nhle.com/v1"


class NHLApiSchemaError(RuntimeError):
    """Raised when a response is missing fields this module depends on —
    fail loudly rather than silently ingesting partial/wrong data."""


def _get_json(session, url: str) -> dict:
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise NHLApiSchemaError(f"expected a JSON object from {url}, got {type(data)}")
    return data


def _require(d: dict, *keys, context: str = ""):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            raise NHLApiSchemaError(f"missing expected field {'/'.join(keys)} {context}".strip())
        cur = cur[k]
    return cur


# ------------------------------------------------------------- schedule --

def _normalize_schedule_game(game: dict, week_date: str) -> dict:
    """v2.1.2a correctness patch (real-payload contract closure): adapts
    the REAL NHL wire format into the engine's canonical internal schedule
    record.

    The real /v1/schedule/{date} response nests each game inside a
    `gameWeek[]` entry that carries the calendar date (`week["date"]`);
    the individual game object itself has NO `gameDate` field at all (this
    was discovered via a genuine browser-replayed live API response --
    every real game object under `gameWeek[].games[]` lacks `gameDate`,
    confirmed for games 2025030412/2025030413/2025030414 on 2026-08-26).
    `ingest_schedule()`'s contract (unchanged) still requires `gameDate` on
    the game object it's given, because every existing direct caller
    (unit-test fixtures, validate_live_nhl.py's direct ingest_schedule()
    loop) already passes a canonical object shaped that way. This function
    is the ONE adapter boundary that bridges the two: it is called only
    from fetch_schedule_range(), which is the only place the real wire
    shape is ever seen.

    The parent `gameWeek.date` is authoritative -- NEVER derived from
    `startTimeUTC[:10]` or `gameCenterLink`. A game's NHL calendar date and
    its UTC start instant can legitimately differ (e.g. gameWeek.date
    "2026-06-04" with startTimeUTC "2026-06-05T00:00:00Z" -- a 7pm Eastern
    Thursday puck-drop is already Friday in UTC); deriving from
    startTimeUTC would silently shift such a game onto the wrong calendar
    date. See tests/test_real_schedule_date_contract.py.

    Returns a NEW dict (shallow copy) with `gameDate` set to the parent
    week's date -- the raw API response's game object is never mutated in
    place, so the raw payload stays exactly as received.

    If the game object ALSO already carries a `gameDate` (true of every
    existing synthetic/unit fixture, which nests its games under a
    matching week date) it must AGREE with the parent week date; a real
    disagreement is a genuine schema problem and fails loudly rather than
    silently preferring one over the other."""
    game_id = game.get("id")
    if not week_date:
        raise NHLApiSchemaError(
            f"missing expected field date in schedule gameWeek containing game {game_id}")
    existing = game.get("gameDate")
    if existing is not None and existing != week_date:
        raise NHLApiSchemaError(
            f"conflicting schedule dates for game {game_id}: "
            f"parent gameWeek.date={week_date!r} vs game.gameDate={existing!r}")
    normalized = dict(game)
    normalized["gameDate"] = week_date
    return normalized


def fetch_schedule_range(session, start_date: dt.date, end_date: dt.date) -> list[dict]:
    games: list[dict] = []
    cursor = start_date
    seen_dates = set()
    while cursor <= end_date:
        data = _get_json(session, f"{BASE_URL}/schedule/{cursor.isoformat()}")
        for week in data.get("gameWeek", []):
            raw_week_date = week.get("date")
            week_games = week.get("games", [])
            if raw_week_date is None:
                if week_games:
                    raise NHLApiSchemaError(
                        f"missing expected field date in schedule gameWeek "
                        f"containing game {week_games[0].get('id')}")
                continue   # no date and no games -- nothing to normalize
            week_date = dt.date.fromisoformat(raw_week_date)
            if week_date in seen_dates or week_date > end_date:
                continue
            seen_dates.add(week_date)
            for g in week_games:
                games.append(_normalize_schedule_game(g, raw_week_date))
        next_start = data.get("nextStartDate")
        if not next_start:
            break
        nxt = dt.date.fromisoformat(next_start)
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.2)
    return games


def _ensure_teams_exist(conn, *team_ids: str) -> None:
    """v2.1.2 spec item 1: `games.home_team`/`away_team` are FOREIGN KEY
    REFERENCES teams(team_id), and db.py enables `PRAGMA foreign_keys =
    ON`. On a completely fresh, freshly-initialized database (db.init_db()
    with no teams pre-seeded), `ingest_schedule()` used to raise
    `sqlite3.IntegrityError: FOREIGN KEY constraint failed` the moment it
    tried to insert the very first real game -- the existing test fixtures
    all manually pre-inserted TOR/BOS before calling ingest_schedule(),
    which concealed this from every test until an independent review
    reproduced it against a genuinely clean database. A caller must never
    be required to know that teams need manual pre-seeding: this makes
    ingest_schedule() safe to call directly against a clean initialized
    database, for any real NHL team abbreviation, not just the synthetic
    demo league. Bare identity rows only (team_id) -- if richer team
    metadata becomes available later it can update the identity record
    separately without touching this bootstrap path. See
    tests/test_fresh_db_ingestion.py."""
    for team_id in team_ids:
        conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES (?)", (team_id,))


def ingest_schedule(conn, game: dict, observed_at_utc: str) -> None:
    """Schedule facts ONLY — never writes a score or game_state=FINAL,
    even if the raw payload happens to carry one (a schedule pull can
    return games that already finished when back-filling a date range;
    use ingest_result for that data instead of pulling it from here).

    v2.1: in addition to the `games` table UPSERT (a latest-known
    convenience cache only, see schema.sql), this appends a row to the
    append-only game_schedule_events history -- the ONLY point-in-time-safe
    source for schedule facts (features.point_in_time.game_schedule_as_of).
    Reingesting an UNCHANGED schedule state appends nothing (idempotent);
    a genuinely different game_date/start/teams/venue appends a NEW event
    row rather than overwriting the previous one, so an earlier
    prediction's reconstruction is never affected by a later correction.

    v2.1.1: `observed_at_utc` and the payload's `startTimeUTC` are both
    normalized via ingest/timestamps.py::normalize_utc_timestamp() before
    anything is written — see this module's docstring.

    v2.1.2 (spec item 1): ensures both teams exist in `teams` (bare
    identity rows, INSERT OR IGNORE) BEFORE the INSERT into `games` --
    safe to call against a completely clean, freshly-initialized database
    with no teams pre-seeded. See _ensure_teams_exist().

    v2.1.2 (spec item 4): the ON CONFLICT UPDATE SET now also keeps
    `game_date`/`home_team`/`away_team` synchronized with the latest
    observed schedule state, not just `scheduled_start_utc`/`venue`.
    schema.sql documents these `games` columns as a latest-known
    CONVENIENCE CACHE for exactly this reason -- a real home/away swap
    reingested via this function must not leave that cache disagreeing
    with the append-only game_schedule_events history it's supposed to
    mirror. schedule_observed_at_utc is still deliberately NOT in the
    UPDATE SET list -- a re-ingest must never overwrite the
    first-observed time. See tests/test_schedule_cache_sync.py.

    v2.1.2a (spec item 6): `season`/`gameDate`/`startTimeUTC` are now
    REQUIRED fields (raise NHLApiSchemaError if missing), not silently
    defaulted via `.get()` -- hardening production parsing against a
    real NHL schedule response, same rationale as `id`/`homeTeam.abbrev`/
    `awayTeam.abbrev` already being required."""
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    game_id = _require(game, "id", context=f"in schedule payload {game}")
    season = _require(game, "season", context=f"in schedule payload for game {game_id}")
    game_date = _require(game, "gameDate", context=f"in schedule payload for game {game_id}")
    start_time_utc = _require(game, "startTimeUTC", context=f"in schedule payload for game {game_id}")
    home = _require(game, "homeTeam", "abbrev", context=f"in schedule payload for game {game_id}")
    away = _require(game, "awayTeam", "abbrev", context=f"in schedule payload for game {game_id}")
    scheduled_start_utc = normalize_utc_timestamp(start_time_utc)
    venue = game.get("venue", {}).get("default")
    _ensure_teams_exist(conn, home, away)
    conn.execute(
        """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                               away_team, venue, schedule_observed_at_utc, game_state, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
               game_date=excluded.game_date,
               scheduled_start_utc=excluded.scheduled_start_utc,
               home_team=excluded.home_team,
               away_team=excluded.away_team,
               venue=excluded.venue""",
        # schedule_observed_at_utc is deliberately NOT in the UPDATE SET
        # list — a re-ingest must not overwrite the first-observed time.
        (game_id, str(season), game_date,
         scheduled_start_utc, home, away, venue,
         observed_at_utc, "SCHEDULED", "nhl_api"),
    )
    _append_schedule_event_if_changed(conn, game_id, game_date, scheduled_start_utc,
                                       home, away, venue, observed_at_utc)


def _append_schedule_event_if_changed(conn, game_id: int, game_date, scheduled_start_utc,
                                       home_team: str, away_team: str, venue,
                                       observed_at_utc: str) -> None:
    latest = conn.execute(
        """SELECT game_date, scheduled_start_utc, home_team, away_team, venue
           FROM game_schedule_events WHERE game_id=?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (game_id,),
    ).fetchone()
    new_state = (game_date, scheduled_start_utc, home_team, away_team, venue)
    if latest is not None and tuple(latest) == new_state:
        return   # unchanged -- idempotent no-op
    conn.execute(
        """INSERT INTO game_schedule_events
           (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
            effective_at_utc, observed_at_utc, source, data_provider)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
         observed_at_utc, observed_at_utc, "nhl_api", "nhl_api"),
    )


def ingest_result(conn, game: dict, observed_at_utc: str, effective_at_utc: str | None = None
                   ) -> None:
    """Result facts for a game already in `games` (via ingest_schedule).
    Only call this once the source reports the game as finished.

    v2.1.1: in addition to the `games` table UPDATE (a current-state
    convenience cache only, see schema.sql), this appends a row to the
    append-only, revision-versioned game_result_events history -- the
    ONLY point-in-time-safe source for a game's result
    (features.point_in_time.game_result_as_of /
    game_result_first_observed_at / completed_games_known_before).
    Reingesting an UNCHANGED result appends nothing (idempotent, so a
    routine re-pull can never move the historical "first known" time); a
    genuine score/period-type correction appends a NEW revision row rather
    than overwriting the previous one, so an earlier prediction's
    reconstruction is never affected by a later correction.
    `effective_at_utc` defaults to `observed_at_utc` -- a single scoreboard
    pull doesn't distinguish "when the game actually ended" from "when we
    pulled it" any more precisely than that.

    v2.1.1: both `observed_at_utc` and `effective_at_utc` are normalized
    via ingest/timestamps.py::normalize_utc_timestamp() before anything
    is written -- see this module's docstring.

    v2.1.2a (spec item 6): `homeTeam.score`/`awayTeam.score`/
    `periodDescriptor.periodType` are now REQUIRED fields (raise
    NHLApiSchemaError if missing) for a FINAL result -- a missing score on
    a game being ingested as final is a real schema problem, not a
    legitimate zero, so it must never silently become `None`/0."""
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    effective_at_utc = normalize_utc_timestamp(effective_at_utc)
    game_id = _require(game, "id")
    home_score = _require(game, "homeTeam", "score", context=f"for FINAL game {game_id}")
    away_score = _require(game, "awayTeam", "score", context=f"for FINAL game {game_id}")
    final_period_type = _require(game, "periodDescriptor", "periodType",
                                  context=f"for FINAL game {game_id}")
    conn.execute(
        """UPDATE games SET home_score=?, away_score=?, final_period_type=?,
                             game_state='FINAL', result_observed_at_utc=?
           WHERE game_id=?""",
        (home_score, away_score, final_period_type, observed_at_utc, game_id),
    )
    _append_result_revision_if_changed(
        conn, game_id, home_score, away_score, final_period_type,
        effective_at_utc or observed_at_utc, observed_at_utc)


def _append_result_revision_if_changed(conn, game_id: int, home_score, away_score,
                                        final_period_type, effective_at_utc: str,
                                        observed_at_utc: str) -> None:
    latest = conn.execute(
        """SELECT home_score, away_score, final_period_type, revision_number
           FROM game_result_events WHERE game_id=? AND game_state='FINAL'
           ORDER BY revision_number DESC LIMIT 1""",
        (game_id,),
    ).fetchone()
    new_state = (home_score, away_score, final_period_type)
    if latest is not None and tuple(latest[:3]) == new_state:
        return   # unchanged -- idempotent no-op; never moves the first-known time
    next_revision = (latest["revision_number"] + 1) if latest is not None else 1
    conn.execute(
        """INSERT INTO game_result_events
           (game_id, home_score, away_score, final_period_type, game_state,
            effective_at_utc, observed_at_utc, revision_number, source, data_provider)
           VALUES (?,?,?,?,'FINAL',?,?,?,?,?)""",
        (game_id, home_score, away_score, final_period_type,
         effective_at_utc, observed_at_utc, next_revision, "nhl_api", "nhl_api"),
    )


# --------------------------------------------------------------- roster --
#
# v2.1.2a (spec item 3): the NHL API exposes two semantically DIFFERENT
# endpoints here -- /v1/roster/{team}/current (who is on the team RIGHT
# NOW) and /v1/roster/{team}/{season} (the roster associated with a
# specific, possibly past, season). A season-roster response retrieved
# TODAY is not proof that everyone on it currently belongs to that team
# (the season could be over, or in progress with trades since). The two
# are kept explicitly separate below:
#   - fetch_team_roster()/upsert_team_membership()/ingest_roster_identities()
#     are SEASON-scoped: safe for historical identity mapping, but must
#     NEVER be used to establish today's current membership.
#   - fetch_current_team_roster()/sync_current_team_roster()/
#     ingest_current_roster_identities() are CURRENT-scoped: the only
#     safe way to establish today's actual team_membership_events, because
#     they reconcile the COMPLETE snapshot (additions AND departures),
#     not just append whoever happens to be present.

def fetch_team_roster(session, team_abbrev: str, season: str) -> dict:
    """SEASON-scoped roster (/v1/roster/{team}/{season}) -- for historical
    identity mapping ONLY. Do not treat this response as proof of CURRENT
    membership; use fetch_current_team_roster() for that (spec item 3)."""
    return _get_json(session, f"{BASE_URL}/roster/{team_abbrev}/{season}")


def fetch_current_team_roster(session, team_abbrev: str) -> dict:
    """CURRENT roster (/v1/roster/{team}/current) -- who is actually on
    this team right now, per the NHL API. THE correct source for
    establishing current team_membership_events; see
    sync_current_team_roster()."""
    return _get_json(session, f"{BASE_URL}/roster/{team_abbrev}/current")


def upsert_team_membership(conn, team_abbrev: str, roster: dict, observed_at_utc: str) -> None:
    """SEASON-scoped identity mapping ONLY (spec item 3) -- only ever ADDS
    a membership row for a player present in `roster`; it never removes
    anyone, so it must NEVER be fed a /current response and relied on to
    reflect who has since left the team. Use sync_current_team_roster()
    for CURRENT membership, which does reconcile departures.

    Idempotent: a player already recorded on this team (as of the
    latest team_membership_events row we have) produces no new row. A
    player who's moved gets exactly one new row — their history on the
    old team is untouched.

    v2.1.1: `observed_at_utc` is normalized via
    ingest/timestamps.py::normalize_utc_timestamp() before anything is
    written — see this module's docstring."""
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    for group in ("forwards", "defensemen", "goalies"):
        for p in roster.get(group, []):
            player_id = str(_require(p, "id", context=f"in roster group {group}"))
            full_name = f"{p.get('firstName', {}).get('default', '')} " \
                        f"{p.get('lastName', {}).get('default', '')}".strip()
            position = "G" if group == "goalies" else ("D" if group == "defensemen" else "F")
            conn.execute(
                "INSERT OR IGNORE INTO players (player_id, full_name, position) VALUES (?,?,?)",
                (player_id, full_name, position),
            )
            current = conn.execute(
                """SELECT team_id FROM team_membership_events WHERE player_id=?
                   ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
                (player_id,),
            ).fetchone()
            if current is not None and current["team_id"] == team_abbrev:
                continue   # unchanged — idempotent no-op
            conn.execute(
                """INSERT INTO team_membership_events
                   (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
                   VALUES (?,?,?,?,?,?)""",
                (player_id, team_abbrev, observed_at_utc, observed_at_utc,
                 "ROSTER_SYNC", "nhl_api"),
            )


def _upsert_or_correct_player_identity(conn, player_id: str, full_name: str, position: str) -> None:
    """v2.1.2a (spec item 4): canonical players.full_name/position can now
    be CORRECTED by a later authoritative response -- the old INSERT OR
    IGNORE permanently froze whatever the first-ever roster pull said,
    even if it was wrong or a later response corrected it. No-op write
    when nothing actually changed (keeps this idempotent)."""
    existing = conn.execute(
        "SELECT full_name, position FROM players WHERE player_id=?", (player_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO players (player_id, full_name, position) VALUES (?,?,?)",
            (player_id, full_name, position),
        )
    elif (existing["full_name"], existing["position"]) != (full_name, position):
        conn.execute(
            "UPDATE players SET full_name=?, position=? WHERE player_id=?",
            (full_name, position, player_id),
        )


def sync_current_team_roster(conn, team_abbrev: str, roster: dict, observed_at_utc: str) -> dict:
    """v2.1.2a spec item 3/4: THE sanctioned way to establish CURRENT team
    membership, from a /v1/roster/{team}/current response. Unlike
    upsert_team_membership() (season-scoped, additions-only -- see its
    docstring), this treats `roster` as the COMPLETE current-roster
    snapshot and reconciles it in full:
      1. collects every player_id present in the snapshot;
      2. upserts/CORRECTS canonical identity (full_name/position) for
         each present player -- see _upsert_or_correct_player_identity();
      3. appends a new membership event for anyone newly assigned/moved
         onto this team (including a player who had previously been
         explicitly removed and has now returned);
      4. anyone whose LATEST membership currently points at this team but
         who is ABSENT from the new complete snapshot gets an explicit
         departure event: `team_id=NULL`, `event_type='ROSTER_REMOVED'` --
         never silently left on the team forever.
    This does NOT infer or imply injury/availability status from a
    roster absence -- roster membership and injury/availability remain
    separate concepts (record_roster_status() is the write path for the
    latter, still with no public NHL source plugged in).
    Idempotent: an identical repeated snapshot writes no new rows at all
    (neither additions nor a duplicate removal). Returns a summary dict.

    `observed_at_utc` is used exactly as given -- callers (e.g.
    ingest_current_roster_identities()) are responsible for capturing it
    at the correct moment (spec item 5: no earlier than this specific
    response's own receipt)."""
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    present_ids: set[str] = set()
    for group in ("forwards", "defensemen", "goalies"):
        for p in roster.get(group, []):
            player_id = str(_require(
                p, "id", context=f"in current-roster group {group} for {team_abbrev}"))
            present_ids.add(player_id)
            full_name = f"{p.get('firstName', {}).get('default', '')} " \
                        f"{p.get('lastName', {}).get('default', '')}".strip()
            position = "G" if group == "goalies" else ("D" if group == "defensemen" else "F")
            _upsert_or_correct_player_identity(conn, player_id, full_name, position)
            current = conn.execute(
                """SELECT team_id FROM team_membership_events WHERE player_id=?
                   ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
                (player_id,),
            ).fetchone()
            if current is not None and current["team_id"] == team_abbrev:
                continue   # unchanged -- idempotent no-op
            conn.execute(
                """INSERT INTO team_membership_events
                   (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
                   VALUES (?,?,?,?,?,?)""",
                (player_id, team_abbrev, observed_at_utc, observed_at_utc,
                 "ROSTER_SYNC", "nhl_api"),
            )
    currently_on_team = conn.execute(
        """SELECT player_id FROM (
               SELECT player_id, team_id,
                      ROW_NUMBER() OVER (
                          PARTITION BY player_id
                          ORDER BY observed_at_utc DESC, id DESC
                      ) AS rn
               FROM team_membership_events
           ) WHERE rn = 1 AND team_id = ?""",
        (team_abbrev,),
    ).fetchall()
    players_removed = 0
    for row in currently_on_team:
        if row["player_id"] not in present_ids:
            conn.execute(
                """INSERT INTO team_membership_events
                   (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
                   VALUES (?,NULL,?,?,'ROSTER_REMOVED',?)""",
                (row["player_id"], observed_at_utc, observed_at_utc, "nhl_api"),
            )
            players_removed += 1
    return {"players_in_snapshot": len(present_ids), "players_removed": players_removed}


def ingest_roster_identities(conn, session, teams: list[str], season: str,
                              observed_at_utc: str | None = None) -> dict:
    """v2.1.2 spec item 5, clarified v2.1.2a spec item 3: the SEASON-
    SCOPED core roster-IDENTITY ingestion step -- canonical player
    identity and historical membership mapping via the season endpoint
    (players.full_name/position, team_membership_events). NOT injury/
    availability intelligence and NOT starting-goalie announcements
    (record_roster_status()/record_goalie_status() remain the write paths
    for those). **This does NOT establish CURRENT team membership** --
    see ingest_current_roster_identities() for that; use THIS function
    only for historical/season identity mapping.

    ingest_range() deliberately does NOT call this. Preferred design
    (spec item 5): keep ingest_range() narrowly focused on schedule/
    result/boxscore/player-stat-row ingestion, and report core roster-
    identity ingestion as its own, separately-validated tier -- so a
    successful ingest_range() run is never read as having also populated
    the canonical player-identity/membership layer. See README's
    Implemented/Tested table.

    Composes fetch_team_roster() + upsert_team_membership() (both already
    idempotent/unit-tested — see tests/test_ingest_idempotency.py) for
    each of `teams`. `observed_at_utc` defaults to this call's own wall
    clock (a live roster pull -- see README's LIVE_OBSERVATION vs
    HISTORICAL_BACKFILL note); pass it explicitly when backfilling."""
    observed_at_utc = normalize_utc_timestamp(
        observed_at_utc or dt.datetime.utcnow().isoformat())
    for team in teams:
        roster = fetch_team_roster(session, team, season)
        upsert_team_membership(conn, team, roster, observed_at_utc)
        time.sleep(0.2)
    conn.commit()
    return {
        "teams_processed": len(teams),
        "players_total": conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"],
    }


def ingest_current_roster_identities(conn, session, teams: list[str],
                                      observed_at_utc: str | None = None) -> dict:
    """v2.1.2a spec item 3/4: the CURRENT-membership counterpart to
    ingest_roster_identities() (season-scoped -- see its docstring). Use
    THIS function whenever "who is currently on this team" actually
    matters (e.g. a live roster-identity smoke test): composes
    fetch_current_team_roster() + sync_current_team_roster() for each of
    `teams`, so departures are reconciled (team_id=NULL/ROSTER_REMOVED),
    not just silently missed.

    v2.1.2a spec item 5: `observed_at_utc` -- if not given explicitly --
    is captured FRESH (dt.datetime.utcnow()) for EACH team's own
    fetch_current_team_roster() response, right after that specific call
    returns, never once up front for the whole batch. Pass it explicitly
    only for deterministic tests/historical-backfill fixtures."""
    explicit_override = (normalize_utc_timestamp(observed_at_utc)
                          if observed_at_utc is not None else None)
    players_removed_total = 0
    for team in teams:
        roster = fetch_current_team_roster(session, team)
        this_call_observed_at = (
            explicit_override if explicit_override is not None
            else normalize_utc_timestamp(dt.datetime.utcnow().isoformat()))
        result = sync_current_team_roster(conn, team, roster, this_call_observed_at)
        players_removed_total += result["players_removed"]
        time.sleep(0.2)
    conn.commit()
    return {
        "teams_processed": len(teams),
        "players_total": conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"],
        "players_removed_this_pass": players_removed_total,
    }


# ------------------------------------------------------- roster status --

def record_roster_status(conn, player_id: str, team_id: str, status: str,
                          effective_at_utc: str, observed_at_utc: str,
                          expected_return_at: str | None, confidence: float, source: str) -> None:
    """Write path for injury/scratch/IR status. No public NHL API exists
    for this — feed it from whatever news/roster source you plug in.

    v2.1.1: `effective_at_utc`, `observed_at_utc`, and `expected_return_at`
    are all normalized via ingest/timestamps.py::normalize_utc_timestamp()
    before anything is written — an external news/roster feed is exactly
    the kind of source likely to hand back a mix of naive/Z/offset
    timestamp forms."""
    effective_at_utc = normalize_utc_timestamp(effective_at_utc)
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    expected_return_at = normalize_utc_timestamp(expected_return_at)
    conn.execute(
        """INSERT INTO roster_status_events
           (player_id, team_id, status, effective_at_utc, observed_at_utc,
            expected_return_at, confidence, source)
           VALUES (?,?,?,?,?,?,?,?)""",
        (player_id, team_id, status, effective_at_utc, observed_at_utc,
         expected_return_at, confidence, source),
    )


def record_goalie_status(conn, game_id: int, team_id: str, player_id: str | None, status: str,
                          effective_at_utc: str, observed_at_utc: str, source: str) -> None:
    """Write path for starting-goalie status (UNKNOWN/EXPECTED/CONFIRMED/
    CHANGED). No public NHL API for this either — plug in Daily Faceoff or
    similar.

    v2.1.1: `effective_at_utc`/`observed_at_utc` are normalized via
    ingest/timestamps.py::normalize_utc_timestamp() before anything is
    written — see this module's docstring."""
    effective_at_utc = normalize_utc_timestamp(effective_at_utc)
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    conn.execute(
        """INSERT INTO goalie_status_events
           (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?)""",
        (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source),
    )


# --------------------------------------------------------------- boxscore --

def fetch_boxscore(session, game_id: int) -> dict:
    return _get_json(session, f"{BASE_URL}/gamecenter/{game_id}/boxscore")


def upsert_player_stats_from_boxscore(conn, box: dict, observed_at_utc: str) -> None:
    """POSTGAME ONLY. Never called for pregame lineup/availability — see
    schema.sql's note on player_game_stats. Use lineup_snapshots /
    roster_status_events for that.

    v2.1: player_game_stats / goalie_game_stats are revision-versioned
    (schema.sql). A stat provider correcting a box score after the fact
    must never retroactively rewrite what a historical model update
    already learned -- so this APPENDS a new revision row when (and only
    when) the tracked values actually changed, rather than UPDATEing in
    place. Reingesting identical stats is a no-op (idempotent).

    v2.1.1: `observed_at_utc` is normalized via
    ingest/timestamps.py::normalize_utc_timestamp() before anything is
    written — see this module's docstring.

    v2.1.2a (spec item 1/6): the real NHL boxscore's per-skater shots-on-
    goal field is named `sog`, NOT `shots` -- the previous
    `p.get("shots", 0)` read a field the real API never sends, so every
    real skater's shots silently stored as 0 while every unit test (built
    against a made-up fixture shape) passed anyway. Fixed to require
    `sog` via _require() -- a missing SOG field is now a hard
    NHLApiSchemaError, never a silent 0. `playerId` is likewise now
    required (was a bare `p["playerId"]` KeyError before -- now a named,
    contextual NHLApiSchemaError). The boxscore's own top-level structure
    (`id`, `homeTeam.abbrev`/`awayTeam.abbrev`,
    `playerByGameStats.homeTeam`/`playerByGameStats.awayTeam`) is also now
    required per spec item 6's boxscore-structure list -- a structurally
    different/broken boxscore response must fail loudly here, not
    silently ingest as zero rows. See tests/test_boxscore_contract.py,
    which fixes a frozen fixture built from the REAL API's field names
    (sog/shotsAgainst/saves/goalsAgainst/starter/toi/goals/assists) rather
    than an invented shape."""
    observed_at_utc = normalize_utc_timestamp(observed_at_utc)
    game_id = _require(box, "id")
    _require(box, "homeTeam", "abbrev", context=f"for boxscore game {game_id}")
    _require(box, "awayTeam", "abbrev", context=f"for boxscore game {game_id}")
    _require(box, "playerByGameStats", "homeTeam",
             context=f"for boxscore game {game_id}")
    _require(box, "playerByGameStats", "awayTeam",
             context=f"for boxscore game {game_id}")
    for side in ("homeTeam", "awayTeam"):
        team_id = box[side]["abbrev"]
        stats = box["playerByGameStats"][side]
        for group in ("forwards", "defense"):
            for p in stats.get(group, []):
                player_id = _require(
                    p, "playerId",
                    context=f"in playerByGameStats.{side}.{group} for game {game_id}")
                sog = _require(
                    p, "sog",
                    context=f"for player {player_id} in game {game_id} "
                            f"(real NHL field is 'sog', not 'shots')")
                _append_player_stat_revision(
                    conn, game_id, str(player_id), team_id,
                    _toi_to_minutes(p.get("toi", "0:00")), p.get("goals", 0),
                    p.get("assists", 0), sog, 1, observed_at_utc,
                )
        for g in stats.get("goalies", []):
            goalie_id = _require(
                g, "playerId", context=f"in playerByGameStats.{side}.goalies for game {game_id}")
            _append_goalie_stat_revision(
                conn, game_id, str(goalie_id), team_id,
                1 if g.get("starter") else 0, g.get("shotsAgainst", 0),
                g.get("saves", 0), g.get("goalsAgainst", 0), observed_at_utc,
            )


def _append_player_stat_revision(conn, game_id, player_id, team_id, toi_minutes, goals,
                                  assists, shots, played, observed_at_utc) -> None:
    latest = conn.execute(
        """SELECT toi_minutes, goals, assists, shots, played, revision_number
           FROM player_game_stats WHERE game_id=? AND player_id=?
           ORDER BY revision_number DESC LIMIT 1""",
        (game_id, player_id),
    ).fetchone()
    new_state = (toi_minutes, goals, assists, shots, played)
    if latest is not None and tuple(latest[:5]) == new_state:
        return   # unchanged -- idempotent no-op
    next_revision = (latest["revision_number"] + 1) if latest is not None else 1
    conn.execute(
        """INSERT INTO player_game_stats
           (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
            revision_number, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
         next_revision, observed_at_utc, observed_at_utc, "nhl_api"),
    )


def _append_goalie_stat_revision(conn, game_id, player_id, team_id, started, shots_against,
                                  saves, goals_against, observed_at_utc) -> None:
    latest = conn.execute(
        """SELECT started, shots_against, saves, goals_against, revision_number
           FROM goalie_game_stats WHERE game_id=? AND player_id=?
           ORDER BY revision_number DESC LIMIT 1""",
        (game_id, player_id),
    ).fetchone()
    new_state = (started, shots_against, saves, goals_against)
    if latest is not None and tuple(latest[:4]) == new_state:
        return   # unchanged -- idempotent no-op
    next_revision = (latest["revision_number"] + 1) if latest is not None else 1
    conn.execute(
        """INSERT INTO goalie_game_stats
           (game_id, player_id, team_id, started, shots_against, saves, goals_against,
            revision_number, effective_at_utc, observed_at_utc, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (game_id, player_id, team_id, started, shots_against, saves, goals_against,
         next_revision, observed_at_utc, observed_at_utc, "nhl_api"),
    )


def _toi_to_minutes(toi: str) -> float:
    try:
        m, s = toi.split(":")
        return int(m) + int(s) / 60.0
    except Exception:
        return 0.0


# --------------------------------------------------------- orchestration --

def ingest_range(conn, start_date: dt.date, end_date: dt.date, session=None) -> dict:
    """Convenience entry point: pull schedule for a date range, ingest
    results + boxscores for anything already final. Idempotent — re-running
    over the same range updates schedule fields (never schedule_observed_at)
    and upserts (never duplicates) results/stats.
    Returns a small summary dict for validate.py's report.

    v2.1.2a (spec item 5): `session` is now an optional injectable
    parameter (defaults to a real `requests.Session()` when not given) so
    tests can pass a fake session double instead of requiring real network
    access -- see tests/test_live_observation_timestamping.py.

    v2.1.2 (spec item 5): this is SCHEDULE/RESULT/BOXSCORE ingestion
    only. It does NOT call fetch_team_roster()/upsert_team_membership() —
    the canonical player-identity/current-roster layer (players.full_name/
    position, team_membership_events) is a separate, explicit tier; see
    ingest_roster_identities(). A successful ingest_range() run proves
    schedule/result/boxscore ingestion works; it does NOT by itself prove
    the roster-identity layer is populated.

    v2.1.2 (spec item 6, LIVE_OBSERVATION vs HISTORICAL_BACKFILL): every
    observed_at_utc stamped below reflects when THIS system actually
    received each specific fact, REGARDLESS of how far in the past
    `start_date`/`end_date` are -- this is the deliberately honest choice,
    not a simplification to fix later. LIVE_OBSERVATION: for forward
    collection (today's/upcoming games), that correctly means "this
    system learned this fact right now" -- the strongest "genuinely known
    by T" guarantee. HISTORICAL_BACKFILL: calling this with a past date
    range (e.g. backfilling the 2022-23 season today) still stamps the
    real current wall-clock moment as observed_at_utc for every historical
    fact pulled -- because that really is the only moment THIS system
    actually learned it, absent a trustworthy archival source with its own
    real historical capture/publication timestamp. The alternative
    (backdating observed_at_utc to the game's own date) would FABRICATE
    historical knowledge availability and silently break the core "what
    was genuinely known at T" guarantee for any research built on that
    data -- see README's "LIVE_OBSERVATION vs. HISTORICAL_BACKFILL"
    section and tests/test_historical_backfill_knowledge_time.py, which
    proves a same-day backfill of a historical game is NOT visible to a
    point-in-time read anchored before the backfill's own ingestion
    moment, even though the game itself happened long before.

    v2.1.2a (spec item 5): PREVIOUSLY a single `now = dt.datetime.utcnow()`
    was captured ONCE, before the loop, and reused as observed_at_utc for
    EVERY schedule/result/boxscore row across the whole range -- including
    boxscore fetches, each its own separate, later-arriving HTTP response.
    That let a fact from a network call that completed minutes into the
    batch be stamped with a timestamp from before the batch even started,
    violating the core "observed_at_utc must never predate actual receipt"
    guarantee. Fixed: `schedule_observed_at` is captured once, immediately
    after fetch_schedule_range() itself returns (schedule+result share
    that one response, with no separate round trip for the result), and
    then a FRESH `boxscore_observed_at` is captured for each game
    individually, immediately after that game's own fetch_boxscore() call
    returns and before upsert_player_stats_from_boxscore() persists it --
    so a later-arriving boxscore response can never receive an earlier
    timestamp merely because the overall batch began earlier. See
    tests/test_live_observation_timestamping.py."""
    if session is None:
        import requests
        session = requests.Session()
    schedule_observed_at = dt.datetime.utcnow().isoformat()
    games = fetch_schedule_range(session, start_date, end_date)
    n_final = 0
    for g in games:
        ingest_schedule(conn, g, schedule_observed_at)
        if g.get("gameState") in ("OFF", "FINAL"):
            ingest_result(conn, g, schedule_observed_at)
            box = fetch_boxscore(session, g["id"])
            boxscore_observed_at = dt.datetime.utcnow().isoformat()
            upsert_player_stats_from_boxscore(conn, box, boxscore_observed_at)
            n_final += 1
        time.sleep(0.2)
    conn.commit()
    return {"games_seen": len(games), "games_finalized": n_final}
