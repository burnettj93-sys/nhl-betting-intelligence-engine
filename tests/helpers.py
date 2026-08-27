"""Shared test fixtures: a tiny hand-built 2-team world, so temporal-
integrity tests can construct precise scenarios without running the full
(slower, harder-to-reason-about) synthetic season generator."""
from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path

import db


def make_test_db():
    """A fresh on-disk SQLite DB (not :memory: — some tests open a second
    connection to the same file to simulate 'later ingestion'). Caller is
    responsible for deleting the returned path when done, or just let the
    OS temp dir clean it up."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    p = Path(path)
    p.unlink()  # init_db creates it fresh
    conn = db.init_db(db_path=p, wipe=False)
    return conn, p


ISO = "%Y-%m-%dT%H:%M:%S"


def t(offset_days=0, hour=12, minute=0, base=dt.date(2025, 1, 1)) -> str:
    """A deterministic ISO timestamp, `offset_days` from a fixed base date."""
    d = base + dt.timedelta(days=offset_days)
    return dt.datetime.combine(d, dt.time(hour, minute)).isoformat()


class Fixture:
    """TOR (home) vs BOS (away), game_id=1, scheduled on day 10 at 19:00.
    A couple of skaters + one goalie per team by default."""

    GAME_ID = 1
    HOME, AWAY = "TOR", "BOS"

    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("INSERT INTO teams (team_id) VALUES (?)", (self.HOME,))
        self.conn.execute("INSERT INTO teams (team_id) VALUES (?)", (self.AWAY,))
        for team, prefix in ((self.HOME, "TOR"), (self.AWAY, "BOS")):
            for i in range(1, 4):
                self.add_player(f"{prefix}_F{i}", "F", team)
            self.add_player(f"{prefix}_G1", "G", team)
            self.add_player(f"{prefix}_G2", "G", team)
        self.scheduled_start = t(10, hour=19)
        self.game_date = (dt.date(2025, 1, 1) + dt.timedelta(days=10)).isoformat()
        self.conn.execute(
            """INSERT INTO games (game_id, season, game_date, scheduled_start_utc, home_team,
                                   away_team, venue, schedule_observed_at_utc, game_state, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (self.GAME_ID, "2025-DEMO", self.game_date,
             self.scheduled_start, self.HOME, self.AWAY, "TOR Arena",
             t(-30), "SCHEDULED", "test"),
        )
        # v2.1: the append-only schedule-history row matching the `games`
        # cache row above -- required for features.point_in_time.
        # game_schedule_as_of()/rest_context() to resolve this fixture.
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (self.GAME_ID, self.game_date, self.scheduled_start, self.HOME, self.AWAY,
             "TOR Arena", t(-30), t(-30), "test", "test"),
        )
        self.conn.commit()

    def add_player(self, player_id, position, team_id, membership_at=None):
        membership_at = membership_at or t(-30)
        self.conn.execute("INSERT OR IGNORE INTO players (player_id, full_name, position) "
                           "VALUES (?,?,?)", (player_id, player_id, position))
        self.conn.execute(
            """INSERT INTO team_membership_events
               (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
               VALUES (?,?,?,?,?,?)""",
            (player_id, team_id, membership_at, membership_at, "INITIAL_ROSTER", "test"),
        )
        self.conn.commit()

    def trade_player(self, player_id, new_team_id, effective_at, observed_at=None):
        observed_at = observed_at or effective_at
        self.conn.execute(
            """INSERT INTO team_membership_events
               (player_id, team_id, effective_at_utc, observed_at_utc, event_type, source)
               VALUES (?,?,?,?,?,?)""",
            (player_id, new_team_id, effective_at, observed_at, "TRADE", "test"),
        )
        self.conn.commit()

    def set_roster_status(self, player_id, team_id, status, effective_at, observed_at=None,
                           expected_return_at=None, confidence=0.9):
        observed_at = observed_at or effective_at
        self.conn.execute(
            """INSERT INTO roster_status_events
               (player_id, team_id, status, effective_at_utc, observed_at_utc,
                expected_return_at, confidence, source)
               VALUES (?,?,?,?,?,?,?,?)""",
            (player_id, team_id, status, effective_at, observed_at,
             expected_return_at, confidence, "test"),
        )
        self.conn.commit()

    def set_goalie_status(self, game_id, team_id, player_id, status, effective_at,
                           observed_at=None):
        observed_at = observed_at or effective_at
        self.conn.execute(
            """INSERT INTO goalie_status_events
               (game_id, team_id, player_id, status, effective_at_utc, observed_at_utc, source)
               VALUES (?,?,?,?,?,?,?)""",
            (game_id, team_id, player_id, status, effective_at, observed_at, "test"),
        )
        self.conn.commit()

    def add_odds(self, game_id, selection, price, captured_at, event_start_utc=None,
                 status="ACTIVE", sportsbook="DraftKings", data_provider="test",
                 market="MONEYLINE", label="TEST", received_at=None):
        """v2.1.1a: `received_at` defaults to `captured_at` (every existing
        call site's prior behavior, unchanged) but can be set separately
        to simulate a quote this system did not ingest until after the
        book's own capture timestamp -- see
        tests/test_odds_receipt_time_integrity.py (spec item 1)."""
        self.conn.execute(
            """INSERT OR IGNORE INTO odds_snapshots
               (game_id, sportsbook, data_provider, market, selection, event_start_utc, line,
                price_american, status, captured_at_utc, received_at_utc, snapshot_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, sportsbook, data_provider, market, selection,
             event_start_utc or self.scheduled_start, None, price, status,
             captured_at, received_at if received_at is not None else captured_at, label),
        )
        self.conn.commit()

    def finalize_game(self, game_id, home_score, away_score, result_observed_at=None,
                       final_period_type="REG", effective_at=None):
        """Finalizes a game AND appends the matching revision-1
        game_result_events row (v2.1.1) -- the `games` row is a
        current-state cache only; features.point_in_time.game_result_as_of
        / completed_games_known_before() read exclusively from
        game_result_events. Use correct_result() below to simulate a
        later score correction."""
        result_observed_at = result_observed_at or self.scheduled_start
        effective_at = effective_at or result_observed_at
        self.conn.execute(
            """UPDATE games SET home_score=?, away_score=?, final_period_type=?,
                                 game_state='FINAL', result_observed_at_utc=? WHERE game_id=?""",
            (home_score, away_score, final_period_type, result_observed_at, game_id),
        )
        self.conn.execute(
            """INSERT INTO game_result_events
               (game_id, home_score, away_score, final_period_type, game_state,
                effective_at_utc, observed_at_utc, revision_number, source, data_provider)
               VALUES (?,?,?,?,'FINAL',?,?,1,?,?)""",
            (game_id, home_score, away_score, final_period_type,
             effective_at, result_observed_at, "test", "test"),
        )
        self.conn.commit()

    def correct_result(self, game_id, home_score, away_score, observed_at,
                        final_period_type="REG", effective_at=None, revision_number=None):
        """Appends a NEW game_result_events revision -- simulates a real
        score/result correction becoming known later. Does NOT touch the
        `games` cache row (matching ingest/nhl_api.py's real
        append-vs-cache split); pass an explicit revision_number if the
        caller needs to control ordering precisely, otherwise it's
        computed as one past the current latest revision for this game."""
        effective_at = effective_at or observed_at
        if revision_number is None:
            latest = self.conn.execute(
                """SELECT MAX(revision_number) AS n FROM game_result_events
                   WHERE game_id=?""", (game_id,),
            ).fetchone()
            revision_number = (latest["n"] or 0) + 1
        self.conn.execute(
            """INSERT INTO game_result_events
               (game_id, home_score, away_score, final_period_type, game_state,
                effective_at_utc, observed_at_utc, revision_number, source, data_provider)
               VALUES (?,?,?,?,'FINAL',?,?,?,?,?)""",
            (game_id, home_score, away_score, final_period_type,
             effective_at, observed_at, revision_number, "test", "test"),
        )
        self.conn.commit()

    def revise_schedule(self, game_id, effective_at, observed_at=None, game_date=None,
                         scheduled_start_utc=None, home_team=None, away_team=None, venue=None):
        """Append a NEW game_schedule_events row -- simulates a real
        schedule correction (time/venue/date/teams changed) becoming
        known. Unspecified fields default to the fixture's original
        values. Does NOT touch the `games` cache row (matching
        ingest/nhl_api.py's real append-vs-cache split)."""
        observed_at = observed_at or effective_at
        self.conn.execute(
            """INSERT INTO game_schedule_events
               (game_id, game_date, scheduled_start_utc, home_team, away_team, venue,
                effective_at_utc, observed_at_utc, source, data_provider)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (game_id, game_date or self.game_date, scheduled_start_utc or self.scheduled_start,
             home_team or self.HOME, away_team or self.AWAY, venue or "TOR Arena",
             effective_at, observed_at, "test", "test"),
        )
        self.conn.commit()

    def add_player_stat(self, game_id, player_id, team_id, goals, assists, observed_at,
                         shots=0, toi_minutes=15.0, played=1, revision_number=1):
        """Insert one revision of a player's postgame stat line. Pass an
        incrementing revision_number to simulate a later correction."""
        self.conn.execute(
            """INSERT INTO player_game_stats
               (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
                revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, player_id, team_id, toi_minutes, goals, assists, shots, played,
             revision_number, observed_at, observed_at, "test"),
        )
        self.conn.commit()

    def add_goalie_stat(self, game_id, player_id, team_id, saves, shots_against, observed_at,
                         goals_against=None, started=1, revision_number=1):
        if goals_against is None:
            goals_against = max(shots_against - saves, 0)
        self.conn.execute(
            """INSERT INTO goalie_game_stats
               (game_id, player_id, team_id, started, shots_against, saves, goals_against,
                revision_number, effective_at_utc, observed_at_utc, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, player_id, team_id, started, shots_against, saves, goals_against,
             revision_number, observed_at, observed_at, "test"),
        )
        self.conn.commit()
