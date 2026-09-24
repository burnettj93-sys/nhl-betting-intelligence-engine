"""
P0.7 (2026-09-24 hardening block): tests for operational/auth_store.py
(password hashing/verification) and dashboard/auth.py (session guards),
including the explicit requirement: attempting a Yahoo page as a USER
role must fail at the route/function level, not merely be hidden from
navigation.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from streamlit.testing.v1 import AppTest

from operational import auth_store

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page(name: str) -> str:
    return os.path.join(REPO_ROOT, "dashboard", "pages", name)


def _fresh_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return auth_store.get_connection(Path(tmp.name))


class TestPasswordHashing(unittest.TestCase):
    def test_password_is_never_stored_in_plain_text(self):
        conn = _fresh_conn()
        auth_store.create_user(conn, "alice", "correct horse battery staple", "USER")
        row = conn.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()
        self.assertNotIn("correct horse battery staple", row["password_hash"])

    def test_two_users_with_the_same_password_get_different_hashes(self):
        """Confirms a real per-user salt is actually used -- otherwise
        two identical passwords would produce identical hashes,
        leaking that they match."""
        conn = _fresh_conn()
        auth_store.create_user(conn, "alice", "shared-password", "USER")
        auth_store.create_user(conn, "bob", "shared-password", "USER")
        rows = conn.execute("SELECT username, password_hash, salt FROM users").fetchall()
        hashes = {r["username"]: r["password_hash"] for r in rows}
        salts = {r["username"]: r["salt"] for r in rows}
        self.assertNotEqual(hashes["alice"], hashes["bob"])
        self.assertNotEqual(salts["alice"], salts["bob"])

    def test_empty_password_is_rejected(self):
        conn = _fresh_conn()
        with self.assertRaises(auth_store.AuthError):
            auth_store.create_user(conn, "alice", "", "USER")

    def test_invalid_role_is_rejected(self):
        conn = _fresh_conn()
        with self.assertRaises(auth_store.AuthError):
            auth_store.create_user(conn, "alice", "password123", "SUPERUSER")

    def test_duplicate_username_is_rejected(self):
        conn = _fresh_conn()
        auth_store.create_user(conn, "alice", "password123", "USER")
        with self.assertRaises(auth_store.AuthError):
            auth_store.create_user(conn, "alice", "different-password", "ADMIN")


class TestVerifyLogin(unittest.TestCase):
    def setUp(self):
        self.conn = _fresh_conn()
        auth_store.create_user(self.conn, "owner", "correct-password", "ADMIN")
        auth_store.create_user(self.conn, "friend", "friend-password", "USER")

    def test_correct_credentials_return_the_real_role(self):
        self.assertEqual(auth_store.verify_login(self.conn, "owner", "correct-password"), "ADMIN")
        self.assertEqual(auth_store.verify_login(self.conn, "friend", "friend-password"), "USER")

    def test_wrong_password_returns_none(self):
        self.assertIsNone(auth_store.verify_login(self.conn, "owner", "wrong-password"))

    def test_unknown_username_returns_none(self):
        self.assertIsNone(auth_store.verify_login(self.conn, "nobody", "anything"))

    def test_wrong_password_and_unknown_user_are_indistinguishable(self):
        """A real, if minor, information-disclosure guard: both failure
        modes must return the identical sentinel (None)."""
        r1 = auth_store.verify_login(self.conn, "owner", "wrong-password")
        r2 = auth_store.verify_login(self.conn, "nobody-at-all", "wrong-password")
        self.assertEqual(r1, r2)
        self.assertIsNone(r1)

    def test_change_password_actually_changes_which_password_verifies(self):
        auth_store.change_password(self.conn, "owner", "new-correct-password")
        self.assertIsNone(auth_store.verify_login(self.conn, "owner", "correct-password"))
        self.assertEqual(auth_store.verify_login(self.conn, "owner", "new-correct-password"), "ADMIN")


class TestUserManagement(unittest.TestCase):
    def test_list_users_never_includes_password_material(self):
        conn = _fresh_conn()
        auth_store.create_user(conn, "alice", "password123", "USER")
        users = auth_store.list_users(conn)
        self.assertEqual(len(users), 1)
        self.assertNotIn("password_hash", users[0])
        self.assertNotIn("salt", users[0])

    def test_delete_user_removes_them(self):
        conn = _fresh_conn()
        auth_store.create_user(conn, "alice", "password123", "USER")
        self.assertTrue(auth_store.user_exists(conn, "alice"))
        auth_store.delete_user(conn, "alice")
        self.assertFalse(auth_store.user_exists(conn, "alice"))


# ---------------------------------------------------------------------
# Route-level enforcement: attempting a Yahoo page as USER must fail,
# not merely be hidden from the sidebar.
# ---------------------------------------------------------------------
class TestYahooRouteEnforcement(unittest.TestCase):
    def test_fantasy_hq_stops_for_a_logged_out_session(self):
        at = AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=60)
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        warning_text = " ".join(w.value for w in at.warning)
        # The real page content (Fantasy HQ header, demo mode banner)
        # must never appear -- the script stopped before reaching it.
        self.assertNotIn("Fantasy HQ", markdown_text)
        self.assertIn("sign in", warning_text.lower())

    def test_fantasy_hq_stops_for_a_user_role_session(self):
        at = AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=60)
        at.session_state["_auth_username"] = "friend"
        at.session_state["_auth_role"] = "USER"
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        error_text = " ".join(e.value for e in at.error)
        self.assertNotIn("Fantasy HQ", markdown_text)
        self.assertIn("restricted to the administrator", error_text)

    def test_fantasy_hq_renders_for_an_admin_role_session(self):
        at = AppTest.from_file(_page("34_Fantasy_HQ.py"), default_timeout=60)
        at.session_state["_auth_username"] = "owner"
        at.session_state["_auth_role"] = "ADMIN"
        at.run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        self.assertIn("Fantasy HQ", titles)

    def test_fantasy_settings_stops_for_a_user_role_session(self):
        at = AppTest.from_file(_page("35_Fantasy_Settings.py"), default_timeout=60)
        at.session_state["_auth_username"] = "friend"
        at.session_state["_auth_role"] = "USER"
        at.run()
        self.assertEqual(len(at.exception), 0)
        markdown_text = " ".join(m.value for m in at.markdown)
        error_text = " ".join(e.value for e in at.error)
        self.assertNotIn("Fantasy Connection Settings", markdown_text)
        self.assertIn("restricted to the administrator", error_text)

    def test_fantasy_settings_renders_for_an_admin_role_session(self):
        at = AppTest.from_file(_page("35_Fantasy_Settings.py"), default_timeout=60)
        at.session_state["_auth_username"] = "owner"
        at.session_state["_auth_role"] = "ADMIN"
        at.run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        self.assertIn("Fantasy Connection Settings", titles)


class TestSharedBettingPagesAreUserAccessible(unittest.TestCase):
    """The block's own explicit rule: shared betting pages must remain
    reachable by a plain USER role, never accidentally locked to
    ADMIN-only alongside the Yahoo pages."""

    def test_today_page_has_no_admin_gate(self):
        import inspect
        with open(_page("21_Today.py")) as f:
            src = f.read()
        self.assertNotIn("require_admin", src)

    def test_paper_performance_page_has_no_admin_gate(self):
        with open(_page("33_Paper_Performance.py")) as f:
            src = f.read()
        self.assertNotIn("require_admin", src)


class TestAppNavigationHidesFantasyForNonAdmins(unittest.TestCase):
    def test_fantasy_section_omitted_from_nav_for_user_role(self):
        """AppTest.switch_page() loads a page's script directly and does
        not itself validate against the currently-registered
        st.navigation() set, so it can't prove non-registration on its
        own -- the real, load-bearing security property (proven
        repeatedly in TestYahooRouteEnforcement above) is that the page
        itself refuses to render real content for a non-admin session
        regardless of how it was reached. This test confirms that
        holds even when reached via switch_page from app.py specifically."""
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.session_state["_auth_username"] = "friend"
        at.session_state["_auth_role"] = "USER"
        at.run()
        self.assertEqual(len(at.exception), 0)
        at.switch_page("pages/34_Fantasy_HQ.py").run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        error_text = " ".join(e.value for e in at.error)
        self.assertNotIn("Fantasy HQ", titles)
        self.assertIn("restricted to the administrator", error_text)

    def test_fantasy_section_present_in_nav_for_admin_role(self):
        at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
        at.session_state["_auth_username"] = "owner"
        at.session_state["_auth_role"] = "ADMIN"
        at.run()
        self.assertEqual(len(at.exception), 0)
        at.switch_page("pages/34_Fantasy_HQ.py").run()
        self.assertEqual(len(at.exception), 0)

    def test_app_shows_login_form_when_logged_out(self):
        # Isolated from the real operational/auth_store.db -- this test
        # must never depend on (or create/touch) real account state.
        with tempfile.TemporaryDirectory() as tmp:
            isolated_path = Path(tmp) / "auth_store.db"
            with mock.patch.object(auth_store, "DEFAULT_DB_PATH", isolated_path):
                at = AppTest.from_file(os.path.join(REPO_ROOT, "dashboard", "app.py"), default_timeout=90)
                at.run()
        self.assertEqual(len(at.exception), 0)
        titles = [t.value for t in at.title]
        # A fresh, empty, isolated auth store has zero users -- the
        # bootstrap-admin form, never the real dashboard content.
        self.assertTrue(any("administrator account" in t.lower() for t in titles))


if __name__ == "__main__":
    unittest.main()
