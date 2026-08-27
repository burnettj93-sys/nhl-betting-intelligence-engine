"""
v2.1.2 spec item 5: `ingest_range()` is schedule/result/boxscore
ingestion only -- it never called fetch_team_roster()/
upsert_team_membership(), so a successful ingest_range() run does not by
itself prove the canonical player-identity/current-roster layer
(players.full_name/position, team_membership_events) is populated, even
though README used to describe "real NHL core ingestion" as covering
"player identities" too.

ingest_roster_identities() is the separate, explicit core roster-IDENTITY
ingestion step (composes the already-unit-tested fetch_team_roster() +
upsert_team_membership()). This is NOT injury/availability intelligence
and NOT starting-goalie announcements -- record_roster_status()/
record_goalie_status() remain the (still-unplugged) write paths for
those; nothing here should be read as validating them.

Uses a minimal fake `session` object (a `.get(url, timeout=...)` ->
object with `.raise_for_status()`/`.json()`, matching exactly what
ingest/nhl_api.py's `_get_json()` calls) so this composes the real
fetch_team_roster() + upsert_team_membership() code path without needing
actual network access -- consistent with how every other ingest/nhl_api.py
function in this suite is tested against constructed fake payloads.
"""
import unittest

from ingest import nhl_api
from tests.helpers import make_test_db


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeSession:
    """Maps a team abbreviation to its canned roster payload -- enough to
    exercise fetch_team_roster()'s real URL-building/_get_json() path
    without a live connection."""

    def __init__(self, rosters_by_team: dict):
        self._rosters_by_team = rosters_by_team
        self.urls_requested = []

    def get(self, url, timeout=15):
        self.urls_requested.append(url)
        for team, roster in self._rosters_by_team.items():
            if f"/roster/{team}/" in url:
                return _FakeResponse(roster)
        raise AssertionError(f"no fake roster registered for URL {url}")


def _roster_payload(entries):
    """entries: list of (player_id, first, last, group)."""
    out = {"forwards": [], "defensemen": [], "goalies": []}
    for player_id, first, last, group in entries:
        out[group].append(
            {"id": player_id, "firstName": {"default": first}, "lastName": {"default": last}})
    return out


class TestIngestRosterIdentitiesPopulatesPlayersAndMembership(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.session = _FakeSession({
            "EDM": _roster_payload([
                (8478402, "Connor", "McDavid", "forwards"),
                (8477933, "Darnell", "Nurse", "defensemen"),
                (8479973, "Stuart", "Skinner", "goalies"),
            ]),
            "VGK": _roster_payload([
                (8480039, "Jack", "Eichel", "forwards"),
            ]),
        })

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_players_are_created_with_full_name_and_position(self):
        nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM"], season="20252026",
            observed_at_utc="2025-10-01T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT full_name, position FROM players WHERE player_id=?", ("8478402",)
        ).fetchone()
        self.assertEqual(row["full_name"], "Connor McDavid")
        self.assertEqual(row["position"], "F")
        goalie_row = self.conn.execute(
            "SELECT full_name, position FROM players WHERE player_id=?", ("8479973",)
        ).fetchone()
        self.assertEqual(goalie_row["position"], "G")

    def test_current_membership_resolves_through_team_membership_events(self):
        nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM"], season="20252026",
            observed_at_utc="2025-10-01T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            """SELECT team_id FROM team_membership_events WHERE player_id=?
               ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
            ("8478402",),
        ).fetchone()
        self.assertEqual(row["team_id"], "EDM")

    def test_multiple_teams_are_all_processed(self):
        result = nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM", "VGK"], season="20252026",
            observed_at_utc="2025-10-01T00:00:00")
        self.conn.commit()
        self.assertEqual(result["teams_processed"], 2)
        self.assertEqual(result["players_total"], 4)
        edm_player = self.conn.execute(
            "SELECT full_name FROM players WHERE player_id=?", ("8478402",)).fetchone()
        vgk_player = self.conn.execute(
            "SELECT full_name FROM players WHERE player_id=?", ("8480039",)).fetchone()
        self.assertEqual(edm_player["full_name"], "Connor McDavid")
        self.assertEqual(vgk_player["full_name"], "Jack Eichel")

    def test_this_does_not_write_to_roster_status_events_or_goalie_status_events(self):
        # explicit non-confusion guard: core roster IDENTITY ingestion
        # must never be mistaken for injury/availability or
        # starting-goalie status -- those tables must stay untouched.
        nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM"], season="20252026",
            observed_at_utc="2025-10-01T00:00:00")
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM roster_status_events").fetchone()["c"], 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM goalie_status_events").fetchone()["c"], 0)

    def test_reingesting_an_unchanged_roster_is_idempotent(self):
        nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM"], season="20252026",
            observed_at_utc="2025-10-01T00:00:00")
        self.conn.commit()
        nhl_api.ingest_roster_identities(
            self.conn, self.session, ["EDM"], season="20252026",
            observed_at_utc="2025-10-02T00:00:00")
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM team_membership_events WHERE player_id=?", ("8478402",)
        ).fetchall()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
