"""
Reads THE_ODDS_API_KEY from the environment or a local .env file (repo
root, gitignored -- see .gitignore). No third-party dotenv dependency;
this project already keeps its dependency list to `requests` +
`streamlit` (requirements.txt), so a ~15-line KEY=VALUE parser is used
instead of adding one.

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


def get_the_odds_api_key() -> str | None:
    """Returns the configured key, or None if not set anywhere (a
    missing key is a normal, expected state this early -- callers must
    treat it as DATA_UNAVAILABLE, never raise past the user)."""
    _load_dotenv_into_os_environ()
    return os.environ.get("THE_ODDS_API_KEY") or None
