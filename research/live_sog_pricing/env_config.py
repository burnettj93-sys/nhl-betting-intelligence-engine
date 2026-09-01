"""
Reads THE_ODDS_API_KEY from the environment, a local .env file (repo
root, gitignored -- see .gitignore), or Streamlit Cloud's own Secrets
mechanism (st.secrets) -- in that priority order. No third-party dotenv
dependency; this project already keeps its dependency list to `requests`
+ `streamlit` (requirements.txt), so a ~15-line KEY=VALUE parser is used
instead of adding one.

STREAMLIT CLOUD DEPLOYMENT: a local `.env` file is gitignored (never
pushed), so a Streamlit Community Cloud deployment has no `.env` to read
-- it instead exposes whatever the app owner enters into the app's own
"Secrets" panel (TOML) as `st.secrets`. This module tries os.environ/.env
FIRST (so every existing local-dev and test code path -- see
tests/test_live_sog_pricing.py::TestApiKeyHandling -- is completely
unchanged), then falls back to `st.secrets["THE_ODDS_API_KEY"]` only if
neither the environment nor .env provided a key. The `streamlit` import
is deliberately lazy (inside the function, not at module top) and every
access is wrapped broadly: this module is also used by plain scripts and
tests that run with no Streamlit app context and no secrets.toml at all,
where touching st.secrets would otherwise raise -- that must never
surface here, since a missing key anywhere is DATA_UNAVAILABLE, not an
error.

SECURITY: this module NEVER logs, prints, or returns the key embedded in
any other string -- only the bare key value itself, for the caller to
put directly into an HTTP header. Every other module in this slice
receives the key ONLY as an opaque value passed to
requests.get(..., headers=...) or similar -- never interpolated into a
URL that could be logged, never written to a report, dashboard, or the
raw-response archive (see client.py / archive.py's own guarantees,
tested in tests/test_live_sog_pricing.py::TestApiKeyHandling).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


def _load_dotenv_into_os_environ() -> None:
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def _from_streamlit_secrets() -> str | None:
    """Best-effort only -- returns None (never raises) whenever
    Streamlit isn't installed, there's no running app context, or no
    secrets.toml/Secrets panel entry exists. All of those are normal,
    expected states outside a real Streamlit Cloud deployment."""
    try:
        import streamlit as st
        return st.secrets.get("THE_ODDS_API_KEY") or None
    except Exception:
        return None


def get_the_odds_api_key() -> str | None:
    """Returns the configured key, or None if not set anywhere (a
    missing key is a normal, expected state this early -- callers must
    treat it as DATA_UNAVAILABLE, never raise past the user). Checks
    os.environ / .env first (unchanged local-dev behavior), then
    st.secrets (Streamlit Cloud deployment)."""
    _load_dotenv_into_os_environ()
    return os.environ.get("THE_ODDS_API_KEY") or _from_streamlit_secrets()
