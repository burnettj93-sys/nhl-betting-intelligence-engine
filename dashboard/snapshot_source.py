"""
Community Cloud reader for the remote "current" snapshot (Cloud live-data
sprint, 2026-09-25).

The Cloud app is a READ-ONLY presentation layer. This module fetches the
snapshot the LOCAL engine published to the `cloud-data` branch and hands it to
dashboard/cloud_snapshot.py. It imports no research/model code and depends only
on the stdlib + operational/cloud_snapshot_schema.py.

Behavior
  * HTTP GET with a hard timeout, bounded retries and backoff, a response-size
    cap, then schema validation. A snapshot that is unreachable, corrupt JSON,
    an unsupported schema, oversized, or missing required fields is REJECTED --
    it never replaces a good one and is never displayed.
  * Cache: ONE entry (the parsed snapshot), TTL 180 s. GitHub is NOT fetched on
    every Streamlit rerun; after a failed fetch the next attempt waits 30 s
    (no hammering), and a stampede of sessions triggers at most one fetch.
  * Last-known-good: if a refresh fails, the last good snapshot held in process
    memory is kept and the state says so (REMOTE-refresh-failed label); it is never
    silently treated as fresh, and its factual data_as_of still drives the
    CURRENT / STALE / VERY_STALE classification.
  * Fallback: if there has never been a good remote snapshot, the git-bundled
    board.json (schema 1, frozen) is used and labeled BUNDLED_FALLBACK.
  * Freshness is computed only from timestamps in the snapshot
    (cloud_snapshot_schema.classify_freshness) -- never invented.

Configuration (env, then st.secrets; none are required)
  NHL_ENGINE_SNAPSHOT_SOURCE   REMOTE (default in Community Cloud) | BUNDLED
  NHL_ENGINE_SNAPSHOT_URL      raw URL of current/snapshot.json (default below)
  NHL_ENGINE_SNAPSHOT_TOKEN    optional read-only token, only if the data source
                               is later moved somewhere private; sent as an
                               Authorization header, never logged or displayed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import NamedTuple

from operational import cloud_snapshot_schema as schema
from operational import runtime_mode

DEFAULT_URL = ("https://raw.githubusercontent.com/burnettj93-sys/nhl-betting-intelligence-engine/"
               "cloud-data/current/snapshot.json")
SOURCE_ENV, URL_ENV, TOKEN_ENV = "NHL_ENGINE_SNAPSHOT_SOURCE", "NHL_ENGINE_SNAPSHOT_URL", "NHL_ENGINE_SNAPSHOT_TOKEN"

TTL_S = 180.0
FAILURE_RETRY_S = 30.0
HTTP_TIMEOUT_S = 8.0
MAX_ATTEMPTS = 2
BACKOFF_S = (0.5,)
MAX_BYTES = 8 * 1024 * 1024

REMOTE, REMOTE_LKG, BUNDLED_FALLBACK, NONE = "REMOTE", "REMOTE_LAST_KNOWN_GOOD", "BUNDLED_FALLBACK", "NONE"


class SnapshotState(NamedTuple):
    data: dict | None
    source: str                    # REMOTE | REMOTE_LAST_KNOWN_GOOD | BUNDLED_FALLBACK | NONE
    fetch_status: str              # OK | FAILED | NOT_ATTEMPTED
    last_error: str | None
    last_attempt_utc: str | None
    last_success_utc: str | None
    freshness: str                 # CURRENT | STALE | VERY_STALE | UNAVAILABLE
    data_as_of: str | None
    generated_at: str | None
    schema_version: int | None
    content_hash: str | None
    age_hours: float | None        # of data_as_of


_lock = threading.Lock()
_clock = time.monotonic            # patched by tests
_sleep = time.sleep                # patched by tests
_cache: dict = {}                  # the single entry; see _empty()


def _empty() -> dict:
    return {"good": None, "good_fetched_utc": None, "checked_at": None, "last_attempt_utc": None,
            "last_success_utc": None, "last_error": None, "ok": None}


_cache.update(_empty())


def reset() -> None:
    """Forget everything (tests / explicit admin refresh)."""
    with _lock:
        _cache.clear()
        _cache.update(_empty())


# ---- configuration --------------------------------------------------------------------------------------------
def _setting(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    try:
        import streamlit as st
        return (st.secrets.get(name) or "").strip() or None
    except Exception:
        return None


def remote_enabled() -> bool:
    """Remote fetching applies only in COMMUNITY_CLOUD_MODE, and can be forced off with
    NHL_ENGINE_SNAPSHOT_SOURCE=BUNDLED (tests, air-gapped runs)."""
    if not runtime_mode.is_community_cloud():
        return False
    return (_setting(SOURCE_ENV) or REMOTE).upper() != "BUNDLED"


def snapshot_url() -> str:
    return _setting(URL_ENV) or DEFAULT_URL


def public_url_label() -> str:
    """The URL without any query string -- safe to show an ADMIN."""
    return snapshot_url().split("?", 1)[0]


# ---- transport ----------------------------------------------------------------------------------------------------
def _ssl_context(url: str):
    """Some Python builds (e.g. python.org macOS) ship without a CA bundle; use certifi's when present."""
    if not url.lower().startswith("https"):
        return None
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 -- fall back to the interpreter's default trust store
        return None


def _http_get(url: str, token: str | None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nhl-engine-cloud-reader/1",
                                                   "Accept": "application/json",
                                                   "Cache-Control": "no-cache"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S, context=_ssl_context(url)) as response:  # noqa: S310 -- fixed https URL
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"snapshot exceeds {MAX_BYTES} bytes")
    return body


def _describe(exc: Exception) -> str:
    """A short error string that can never contain a token or query string."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {type(exc.reason).__name__}"
    if isinstance(exc, (TimeoutError, OSError)):
        return f"network error: {type(exc).__name__}"
    if isinstance(exc, json.JSONDecodeError):
        return "corrupt JSON"
    if isinstance(exc, schema.SnapshotInvalid):
        return f"invalid snapshot: {str(exc)[:120]}"
    return f"{type(exc).__name__}: {str(exc)[:120]}"


def fetch_remote() -> dict:
    """One bounded fetch cycle (with retries). Returns the validated snapshot or raises."""
    token = _setting(TOKEN_ENV)
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            body = _http_get(snapshot_url(), token)
            doc = json.loads(body.decode("utf-8"))
            return schema.validate_snapshot(doc)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, schema.SnapshotInvalid) as exc:
            last = exc
            # a rejected/corrupt document is not fixed by an immediate retry; only transport errors are
            transient = isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)) and not (
                isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500)
            if not transient or attempt == MAX_ATTEMPTS - 1:
                break
            _sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
    raise last  # type: ignore[misc]


# ---- state ----------------------------------------------------------------------------------------------------------------
def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _bundled() -> dict | None:
    from dashboard import cloud_snapshot
    try:
        return cloud_snapshot._load_bundled()
    except cloud_snapshot.SnapshotUnavailable:
        return None


def _state_from(source: str, data: dict | None, fetch_status: str) -> SnapshotState:
    meta = schema.metadata_of(data) if data else {}
    as_of = meta.get("data_as_of")
    content_hash = meta.get("content_hash") or (schema.content_hash(data) if data else None)
    return SnapshotState(
        data=data, source=source, fetch_status=fetch_status, last_error=_cache["last_error"],
        last_attempt_utc=_cache["last_attempt_utc"], last_success_utc=_cache["last_success_utc"],
        freshness=schema.classify_freshness(as_of) if data else schema.UNAVAILABLE,
        data_as_of=as_of, generated_at=meta.get("generated_at"),
        schema_version=data.get("schema_version") if data else None, content_hash=content_hash,
        age_hours=schema.age_hours(as_of) if data else None)


def _view() -> SnapshotState:
    if _cache["good"] is not None:
        return _state_from(REMOTE if _cache["ok"] else REMOTE_LKG, _cache["good"],
                           "OK" if _cache["ok"] else "FAILED")
    bundled = _bundled()
    if bundled is not None:
        return _state_from(BUNDLED_FALLBACK, bundled, "FAILED" if _cache["last_attempt_utc"] else "NOT_ATTEMPTED")
    return _state_from(NONE, None, "FAILED" if _cache["last_attempt_utc"] else "NOT_ATTEMPTED")


def current(*, force_refresh: bool = False) -> SnapshotState:
    """The snapshot to display right now. Cheap when within TTL."""
    now = _clock()
    checked = _cache["checked_at"]
    ttl = TTL_S if _cache["ok"] else FAILURE_RETRY_S
    if not force_refresh and checked is not None and now - checked < ttl:
        return _view()
    acquired = _lock.acquire(blocking=_cache["good"] is None and checked is None)
    if not acquired:                      # another session is already refreshing: serve what we have
        return _view()
    try:
        # re-check: the refresh we waited on may already have finished
        checked = _cache["checked_at"]
        ttl = TTL_S if _cache["ok"] else FAILURE_RETRY_S
        if not force_refresh and checked is not None and _clock() - checked < ttl:
            return _view()
        _cache["last_attempt_utc"] = _utcnow().isoformat()
        try:
            doc = fetch_remote()
        except Exception as exc:  # noqa: BLE001 -- any failure keeps last-known-good
            _cache["ok"] = False
            _cache["last_error"] = _describe(exc)
        else:
            _cache.update(good=doc, ok=True, last_error=None, last_success_utc=_utcnow().isoformat())
        _cache["checked_at"] = _clock()
        return _view()
    finally:
        _lock.release()


def diagnostics() -> dict:
    """ADMIN-only facts about the snapshot source. Never includes the token or a query string."""
    if not remote_enabled():
        state = _state_from(BUNDLED_FALLBACK, _bundled(), "NOT_ATTEMPTED")
        source_kind = "BUNDLED (remote disabled)"
    else:
        state = current()
        source_kind = state.source
    return {
        "snapshot_source": source_kind, "remote_url": public_url_label() if remote_enabled() else None,
        "remote_fetch_status": state.fetch_status, "last_error": state.last_error,
        "last_attempt_utc": state.last_attempt_utc, "last_successful_fetch_utc": state.last_success_utc,
        "snapshot_generated_at": state.generated_at, "data_as_of": state.data_as_of,
        "snapshot_data_age_hours": state.age_hours, "freshness": state.freshness,
        "schema_version": state.schema_version, "content_hash": state.content_hash,
        "ttl_seconds": TTL_S,
    }
