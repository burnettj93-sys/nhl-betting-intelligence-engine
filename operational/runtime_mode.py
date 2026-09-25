"""
Streamlit runtime mode (Community Cloud memory sprint, 2026-09-25).

Three modes, resolved in ONE place:

  LOCAL_MODE            (default) -- the full app, exactly as before: model
                        stack, research pages, demo builders computed live.
  PRODUCTION_MODE       -- a VPS/production deployment (docs/VPS_*.md);
                        behaves like LOCAL_MODE for the UI.
  COMMUNITY_CLOUD_MODE  -- Streamlit Community Cloud's free tier: a THIN,
                        READ-ONLY presentation layer. The web process never
                        loads the research model stack or research corpora,
                        never runs schedulers/ingestion/settlement, never
                        writes critical DB state, and never makes paid API
                        calls from a page render. Heavy pages are simply not
                        registered (dashboard/page_registry.py) and the demo
                        board is read from a compact verified snapshot
                        (dashboard/cloud_snapshot.py).

Resolution order: NHL_ENGINE_RUNTIME_MODE env var -> a line in .env ->
st.secrets -> auto-detection of Community Cloud (the app is mounted under
/mount/src/) -> LOCAL_MODE. An unrecognized value falls back to LOCAL_MODE
(the full, safe-for-development behavior) rather than silently thinning the
app. This is a different axis from operational/deployment_mode.py
(ACTIVE/STANDBY = "which machine is the one running schedulers"); the two
are independent and neither reads the other.
"""
from __future__ import annotations

import os
from pathlib import Path

LOCAL_MODE = "LOCAL_MODE"
PRODUCTION_MODE = "PRODUCTION_MODE"
COMMUNITY_CLOUD_MODE = "COMMUNITY_CLOUD_MODE"
VALID_MODES = (LOCAL_MODE, PRODUCTION_MODE, COMMUNITY_CLOUD_MODE)
ENV_VAR = "NHL_ENGINE_RUNTIME_MODE"

REPO_ROOT = Path(__file__).resolve().parent.parent


class HeavyFeatureUnavailable(RuntimeError):
    """Raised when code in COMMUNITY_CLOUD_MODE tries to build something
    the thin presentation layer deliberately never builds (the research
    model stack, research databases, a live scheduler action). Failing
    loudly beats silently loading ~500 MB into a 1 GB free-tier process."""


def _normalize(value: str | None) -> str | None:
    value = (value or "").strip().upper()
    return value if value in VALID_MODES else None


def _from_dotenv() -> str | None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        key, sep, val = line.strip().partition("=")
        if sep and key.strip() == ENV_VAR:
            return val.strip()
    return None


def _from_streamlit_secrets() -> str | None:
    try:
        import streamlit as st
        return st.secrets.get(ENV_VAR)
    except Exception:
        return None


def looks_like_community_cloud() -> bool:
    """Community Cloud mounts the repo under /mount/src/<repo>."""
    return REPO_ROOT.as_posix().startswith("/mount/src/")


def current_mode() -> str:
    for source in (os.environ.get(ENV_VAR), _from_dotenv(), _from_streamlit_secrets()):
        mode = _normalize(source)
        if mode:
            return mode
        if (source or "").strip():
            return LOCAL_MODE  # explicit but unrecognized: fail toward the full app, never thin it by accident
    return COMMUNITY_CLOUD_MODE if looks_like_community_cloud() else LOCAL_MODE


def is_community_cloud() -> bool:
    return current_mode() == COMMUNITY_CLOUD_MODE


def require_not_community_cloud(what: str) -> None:
    """Guard for anything the thin presentation layer must never do."""
    if is_community_cloud():
        raise HeavyFeatureUnavailable(
            f"{what} is disabled in {COMMUNITY_CLOUD_MODE}: this deployment is a thin, read-only "
            f"presentation layer (see docs/STREAMLIT_MEMORY_CERTIFICATION.md).")
