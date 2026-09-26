"""Community Cloud has NO application-level auth: Streamlit private sharing is the only access gate. LOCAL and
PRODUCTION keep the account system exactly as before."""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import auth, page_registry
from operational import auth_store
from operational import runtime_mode as rm

REPO = Path(__file__).resolve().parent.parent
APP = str(REPO / "dashboard" / "app.py")
AUTH_SECRETS = ("NHL_ENGINE_ADMIN_SETUP_CODE", "NHL_ENGINE_TRUST_PLATFORM_VIEWER", "NHL_ENGINE_ADMIN_EMAILS")


def _run_app(mode, *, users=0, env=None):
    from streamlit.testing.v1 import AppTest
    base = {k: v for k, v in os.environ.items() if k not in AUTH_SECRETS}
    base.update({"NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED", **(env or {})})
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, base, clear=True), \
         mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "a.db"), mock.patch.object(rm, "current_mode", return_value=mode):
        if users:
            conn = auth_store.get_connection()
            auth_store.create_user(conn, "someone", "Some!Passw0rd-2026", "USER")
            conn.close()
        at = AppTest.from_file(APP, default_timeout=120)
        at.run()
    return at


def _text(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [t.value for t in at.title])


class TestCloudNeedsNoAppLogin(unittest.TestCase):
    def test_a_viewer_who_reaches_the_app_lands_directly_in_the_product(self):
        at = _run_app(rm.COMMUNITY_CLOUD_MODE)
        self.assertEqual(list(at.exception), [])
        text = _text(at)
        for gate in ("Sign in", "create the administrator account", "Administrator setup", "Setup code", "First-time setup"):
            self.assertNotIn(gate, text)
        labels = " ".join(t.label for t in at.text_input)                        # (the global search box is not a login field)
        for field in ("Username", "Password", "Setup code", "Admin username"):
            self.assertNotIn(field, labels)
        self.assertFalse([t for t in at.text_input if t.type == "password"])
        self.assertNotIn("Log out", " ".join(b.label for b in at.button))
        self.assertIn("Streamlit private sharing", text)
        self.assertIn("Today", text)                                             # the default page rendered

    def test_no_auth_secrets_are_required_and_stale_ones_change_nothing(self):
        bare = _text(_run_app(rm.COMMUNITY_CLOUD_MODE))
        stale = _text(_run_app(rm.COMMUNITY_CLOUD_MODE, env={"NHL_ENGINE_ADMIN_SETUP_CODE": "<placeholder>",
                                                             "NHL_ENGINE_TRUST_PLATFORM_VIEWER": "ON",
                                                             "NHL_ENGINE_ADMIN_EMAILS": "someone@example.com"}))
        self.assertEqual(bare, stale)

    def test_no_account_database_is_touched_in_cloud(self):
        with mock.patch.object(auth_store, "get_connection", side_effect=AssertionError("auth_store must not be used in Cloud")):
            at = _run_app(rm.COMMUNITY_CLOUD_MODE)
        self.assertEqual(list(at.exception), [])

    def test_the_implicit_viewer_is_the_only_identity_and_the_old_platform_paths_are_gone(self):
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            self.assertEqual(auth.current_user(), auth.CLOUD_VIEWER)
            self.assertEqual(auth.require_admin()["username"], auth.CLOUD_VIEWER["username"])
        for gone in ("platform_viewer_email", "platform_role_for", "platform_identity_trusted", "setup_code_valid",
                     "bootstrap_requires_setup_code", "_configured_setup_code", "SETUP_CODE_ENV", "TRUST_PLATFORM_ENV", "ADMIN_EMAILS_ENV"):
            self.assertFalse(hasattr(auth, gone), gone)

    def test_st_user_email_is_never_read(self):
        src = (REPO / "dashboard" / "auth.py").read_text() + (REPO / "dashboard" / "app.py").read_text()
        self.assertNotIn("experimental_user", src)
        self.assertNotIn("st.user", src.replace("st.user\n", ""))
        self.assertNotIn("NHL_ENGINE_ADMIN_EMAILS", src)

    def test_the_cloud_sidebar_offers_no_logout_or_role_label(self):
        at = _run_app(rm.COMMUNITY_CLOUD_MODE)
        self.assertNotIn("Signed in as", _text(at))


class TestCloudSurfaceHasNoRiskyControls(unittest.TestCase):
    RISKY = re.compile(r"subprocess|os\.system|requests\.|urllib|file_uploader|st\.secrets|type=\"password\"|\.execute\(|executescript|"
                       r"INSERT |DELETE |UPDATE |write_text|\.unlink|shutil|download_button|auth_store|token_store|fantasy|yahoo", re.I)

    def cloud_pages(self):
        return [p for p in page_registry.PAGES if p.cloud]

    def test_every_cloud_page_is_free_of_writes_credentials_ingestion_and_auth_management(self):
        for spec in self.cloud_pages():
            src = (REPO / "dashboard" / "pages" / spec.file).read_text()
            hits = sorted({m.group(0) for m in self.RISKY.finditer(src)})
            self.assertEqual(hits, [], f"{spec.file}: {hits}")

    def test_the_useful_product_pages_are_all_available_in_cloud_without_a_role(self):
        titles = {p.title for section in page_registry.pages_for("ADMIN", rm.COMMUNITY_CLOUD_MODE).values() for p in section}
        for wanted in ("Today", "Games", "Game Detail", "Player Intelligence", "Team Intelligence", "Paper Performance", "Ledger",
                       "Morning Review", "Data Status", "Diagnostics", "Model Health"):
            self.assertIn(wanted, titles)

    def test_yahoo_and_management_pages_are_excluded_from_cloud(self):
        cloud_files = {p.file for p in self.cloud_pages()}
        self.assertNotIn("34_Fantasy_HQ.py", cloud_files)
        self.assertNotIn("35_Fantasy_Settings.py", cloud_files)
        titles = {p.title for section in page_registry.pages_for("ADMIN", rm.COMMUNITY_CLOUD_MODE).values() for p in section}
        self.assertFalse({t for t in titles if "Fantasy" in t or "Yahoo" in t})

    def test_diagnostics_shows_no_environment_or_secrets(self):
        src = (REPO / "dashboard" / "pages" / "37_Diagnostics.py").read_text()
        self.assertIn("no environment variables", src.lower())
        self.assertNotIn("os.environ", src)
        self.assertNotIn("st.secrets", src)


class TestLocalAndProductionAuthUnchanged(unittest.TestCase):
    def test_local_mode_with_no_accounts_still_shows_the_bootstrap_form_without_a_setup_code(self):
        for mode in (rm.LOCAL_MODE, rm.PRODUCTION_MODE):
            text = _text(_run_app(mode, users=0))
            self.assertIn("create the administrator account", text, mode)
            self.assertNotIn("Setup code", text, mode)

    def test_local_and_production_with_accounts_require_sign_in(self):
        for mode in (rm.LOCAL_MODE, rm.PRODUCTION_MODE):
            at = _run_app(mode, users=1)
            self.assertIn("Sign in", _text(at), mode)
            self.assertGreaterEqual(len(at.text_input), 2, mode)

    def test_role_gating_still_applies_outside_cloud(self):
        self.assertFalse(page_registry.page_available(next(p for p in page_registry.PAGES if p.file == "37_Diagnostics.py"), "USER", rm.LOCAL_MODE))
        self.assertTrue(page_registry.page_available(next(p for p in page_registry.PAGES if p.file == "37_Diagnostics.py"), "ADMIN", rm.LOCAL_MODE))


class TestReadinessDoesNotDependOnCloudAuth(unittest.TestCase):
    def test_no_component_or_preflight_mentions_setup_codes_or_admin_emails_as_required(self):
        for f in ("opening_day_readiness.py", "operational/cloud_preflight.py", "operational/first_live_certification.py"):
            src = (REPO / f).read_text()
            for phrase in ("NHL_ENGINE_ADMIN_SETUP_CODE", "TRUST_PLATFORM_VIEWER", "NHL_ENGINE_ADMIN_EMAILS"):
                if phrase in src:
                    self.assertRegex(src.lower(), r"no longer used|no longer required|not used", (f, phrase))

if __name__ == "__main__":
    unittest.main()
