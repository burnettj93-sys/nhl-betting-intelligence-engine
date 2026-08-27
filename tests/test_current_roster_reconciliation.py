"""
v2.1.2a spec items 3/4: CURRENT team roster membership
(/v1/roster/{team}/current) is semantically different from SEASON roster
identity (/v1/roster/{team}/{season}) -- a season-roster pull retrieved
today doesn't prove today's actual membership. sync_current_team_roster()
is the sanctioned reconciliation function: given a COMPLETE current-
roster snapshot, it upserts/corrects identity for everyone present,
appends membership events for anyone new/moved onto the team, and -- the
part upsert_team_membership() never did -- appends an explicit
`team_id=NULL, event_type='ROSTER_REMOVED'` departure event for anyone
whose latest known membership pointed at this team but who is absent
from the new snapshot.

Explicitly does NOT infer injury status from a roster absence -- roster
membership and injury/availability remain separate concepts; these tests
only check team_membership_events / players, never roster_status_events.

Required scenarios (spec item 13): present -> later absent; traded ->
new team; absent -> later returns; name correction; position correction;
repeated identical snapshot = no new membership event.
"""
import unittest

from ingest.nhl_api import sync_current_team_roster, ingest_current_roster_identities
from tests.helpers import make_test_db


def _roster(entries):
    """entries: list of (player_id, first, last, group)."""
    out = {"forwards": [], "defensemen": [], "goalies": []}
    for player_id, first, last, group in entries:
        out[group].append(
            {"id": player_id, "firstName": {"default": first}, "lastName": {"default": last}})
    return out


def _latest_membership(conn, player_id):
    return conn.execute(
        """SELECT team_id, event_type FROM team_membership_events WHERE player_id=?
           ORDER BY observed_at_utc DESC, id DESC LIMIT 1""",
        (player_id,),
    ).fetchone()


class TestPresentLaterAbsent(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('EDM')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_a_player_absent_from_a_later_snapshot_gets_a_removal_event(self):
        roster1 = _roster([(8478402, "Connor", "McDavid", "forwards"),
                            (8477933, "Darnell", "Nurse", "defensemen")])
        sync_current_team_roster(self.conn, "EDM", roster1, observed_at_utc="2025-10-01T00:00:00")
        roster2 = _roster([(8478402, "Connor", "McDavid", "forwards")])   # Nurse now gone
        result = sync_current_team_roster(self.conn, "EDM", roster2,
                                           observed_at_utc="2025-10-08T00:00:00")
        self.conn.commit()
        self.assertEqual(result["players_removed"], 1)
        latest = _latest_membership(self.conn, "8477933")
        self.assertIsNone(latest["team_id"])
        self.assertEqual(latest["event_type"], "ROSTER_REMOVED")
        # McDavid, still present, is untouched
        mcdavid = _latest_membership(self.conn, "8478402")
        self.assertEqual(mcdavid["team_id"], "EDM")


class TestTradedToNewTeam(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('EDM')")
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('VGK')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_a_trade_removes_from_old_team_and_adds_to_new_team(self):
        edm_roster1 = _roster([(8480039, "Jack", "Eichel", "forwards")])
        sync_current_team_roster(self.conn, "EDM", edm_roster1,
                                  observed_at_utc="2025-10-01T00:00:00")
        # trade: Eichel leaves EDM, joins VGK
        edm_roster2 = _roster([])
        sync_current_team_roster(self.conn, "EDM", edm_roster2,
                                  observed_at_utc="2025-10-08T00:00:00")
        vgk_roster1 = _roster([(8480039, "Jack", "Eichel", "forwards")])
        sync_current_team_roster(self.conn, "VGK", vgk_roster1,
                                  observed_at_utc="2025-10-08T00:05:00")
        self.conn.commit()
        latest = _latest_membership(self.conn, "8480039")
        self.assertEqual(latest["team_id"], "VGK")
        self.assertEqual(latest["event_type"], "ROSTER_SYNC")
        # EDM's own membership history still shows the removal happened
        edm_events = self.conn.execute(
            "SELECT team_id, event_type FROM team_membership_events WHERE player_id=? "
            "ORDER BY observed_at_utc, id", ("8480039",),
        ).fetchall()
        event_types = [r["event_type"] for r in edm_events]
        self.assertIn("ROSTER_REMOVED", event_types)


class TestAbsentThenReturns(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('EDM')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_a_player_who_returns_after_a_removal_gets_a_fresh_membership_event(self):
        roster_present = _roster([(8478402, "Connor", "McDavid", "forwards")])
        sync_current_team_roster(self.conn, "EDM", roster_present,
                                  observed_at_utc="2025-10-01T00:00:00")
        roster_absent = _roster([])
        sync_current_team_roster(self.conn, "EDM", roster_absent,
                                  observed_at_utc="2025-10-08T00:00:00")
        removed = _latest_membership(self.conn, "8478402")
        self.assertEqual(removed["event_type"], "ROSTER_REMOVED")
        roster_returns = _roster([(8478402, "Connor", "McDavid", "forwards")])
        sync_current_team_roster(self.conn, "EDM", roster_returns,
                                  observed_at_utc="2025-10-15T00:00:00")
        self.conn.commit()
        returned = _latest_membership(self.conn, "8478402")
        self.assertEqual(returned["team_id"], "EDM")
        self.assertEqual(returned["event_type"], "ROSTER_SYNC")


class TestIdentityCorrection(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('EDM')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_a_later_response_corrects_full_name(self):
        roster1 = _roster([(8478402, "Conor", "McDavid", "forwards")])  # typo'd first name
        sync_current_team_roster(self.conn, "EDM", roster1, observed_at_utc="2025-10-01T00:00:00")
        row = self.conn.execute(
            "SELECT full_name FROM players WHERE player_id=?", ("8478402",)).fetchone()
        self.assertEqual(row["full_name"], "Conor McDavid")
        roster2 = _roster([(8478402, "Connor", "McDavid", "forwards")])  # corrected
        sync_current_team_roster(self.conn, "EDM", roster2, observed_at_utc="2025-10-08T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT full_name FROM players WHERE player_id=?", ("8478402",)).fetchone()
        self.assertEqual(row["full_name"], "Connor McDavid")

    def test_a_later_response_corrects_position(self):
        # e.g. a player initially miscategorized, corrected in a later pull
        roster1 = _roster([(8477933, "Darnell", "Nurse", "forwards")])   # wrong group
        sync_current_team_roster(self.conn, "EDM", roster1, observed_at_utc="2025-10-01T00:00:00")
        row = self.conn.execute(
            "SELECT position FROM players WHERE player_id=?", ("8477933",)).fetchone()
        self.assertEqual(row["position"], "F")
        roster2 = _roster([(8477933, "Darnell", "Nurse", "defensemen")])  # corrected
        sync_current_team_roster(self.conn, "EDM", roster2, observed_at_utc="2025-10-08T00:00:00")
        self.conn.commit()
        row = self.conn.execute(
            "SELECT position FROM players WHERE player_id=?", ("8477933",)).fetchone()
        self.assertEqual(row["position"], "D")


class TestRepeatedIdenticalSnapshotIsIdempotent(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_test_db()
        self.conn.execute("INSERT OR IGNORE INTO teams (team_id) VALUES ('EDM')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.path.unlink(missing_ok=True)

    def test_repeated_identical_snapshot_writes_no_new_membership_event(self):
        roster = _roster([(8478402, "Connor", "McDavid", "forwards"),
                           (8477933, "Darnell", "Nurse", "defensemen"),
                           (8479973, "Stuart", "Skinner", "goalies")])
        sync_current_team_roster(self.conn, "EDM", roster, observed_at_utc="2025-10-01T00:00:00")
        before = self.conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        result = sync_current_team_roster(self.conn, "EDM", roster,
                                           observed_at_utc="2025-10-08T00:00:00")
        self.conn.commit()
        after = self.conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(result["players_removed"], 0)

    def test_repeated_identical_snapshot_via_full_ingest_current_roster_identities(self):
        # end-to-end via the composed entry point + a fake session, proving
        # the same idempotency at the level validate_live_nhl.py exercises.
        class _FakeResponse:
            def __init__(self, data):
                self._data = data
            def raise_for_status(self):
                return None
            def json(self):
                return self._data

        class _FakeSession:
            def __init__(self, roster):
                self._roster = roster
            def get(self, url, timeout=15):
                assert "/roster/EDM/current" in url
                return _FakeResponse(self._roster)

        roster = _roster([(8478402, "Connor", "McDavid", "forwards")])
        session = _FakeSession(roster)
        ingest_current_roster_identities(self.conn, session, ["EDM"])
        before = self.conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        ingest_current_roster_identities(self.conn, session, ["EDM"])
        after = self.conn.execute(
            "SELECT COUNT(*) c FROM team_membership_events").fetchone()["c"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
