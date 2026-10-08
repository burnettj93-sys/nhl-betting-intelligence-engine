"""
Reader for the published data snapshot (Community Cloud is a read-only presentation layer).

The local engine publishes one validated document to the `cloud-data` branch (operational/publish_cloud_snapshot.py);
dashboard/snapshot_source.py fetches it. This module hands the pages their sections. There is no bundled or simulated
fallback: when no good snapshot has ever been fetched, a page says so, with the cause and the last successful update
(`unavailable_reason()`), instead of showing stand-in content.

Accessors raise SectionUnavailable when the snapshot in use lacks a section (e.g. one published by an older engine) and
SnapshotUnavailable when there is no snapshot at all; pages turn both into a truthful unavailable state.
"""
from __future__ import annotations

import contextlib
import copy

from operational import runtime_mode

_force_live_depth = 0


class SnapshotUnavailable(runtime_mode.HeavyFeatureUnavailable):
    pass


class SectionUnavailable(SnapshotUnavailable):
    """The snapshot in use does not carry this section."""


@contextlib.contextmanager
def live_compute():
    """Inside this block snapshot_active() is False even in COMMUNITY_CLOUD_MODE (tests / builders only)."""
    global _force_live_depth
    _force_live_depth += 1
    try:
        yield
    finally:
        _force_live_depth -= 1


def snapshot_active() -> bool:
    return _force_live_depth == 0 and runtime_mode.is_community_cloud()


def _load() -> dict:
    from dashboard import snapshot_source
    if not snapshot_source.remote_enabled():
        raise SnapshotUnavailable("the remote snapshot source is disabled in this run")
    state = snapshot_source.current()
    if state.data is None:
        raise SnapshotUnavailable(unavailable_reason())
    return state.data


def reset_cache() -> None:
    from dashboard import snapshot_source
    snapshot_source.reset()


def load_snapshot() -> dict:
    return _load()


def unavailable_reason() -> str:
    """Why there is no snapshot, and when one last arrived -- the truthful text for an unavailable page."""
    from dashboard import snapshot_source
    try:
        st = snapshot_source.current()
    except Exception as exc:  # noqa: BLE001
        return f"The data feed could not be read ({type(exc).__name__})."
    if st.data is not None:
        return "The data feed is available."
    cause = st.last_error or "no fetch has completed yet"
    last = st.last_success_utc or "never in this session"
    return f"No data snapshot could be loaded. Cause: {cause}. Last successful update: {last}."


def _optional(name: str):
    data = _load()
    if name not in data:
        raise SectionUnavailable(f"the current snapshot has no `{name}` section (published by an older engine version)")
    return copy.deepcopy(data[name])


def performance_state() -> dict:
    return _optional("performance")


def morning_review_report() -> dict:
    return _optional("morning_review")


def ledger_section() -> dict:
    return _optional("ledger")


def data_status_section() -> dict:
    return _optional("data_status")


def health_section() -> dict:
    return _optional("health")


def tickets() -> dict:
    return _optional("tickets")


def manual_orders() -> dict:
    return _optional("manual_orders")


def product_meta() -> dict:
    return _optional("product_meta")


def product_games() -> dict:
    return _optional("product_games")


def product_game_details() -> dict:
    return _optional("product_game_details")


def product_players() -> dict:
    return _optional("product_players")


def product_goalies() -> dict:
    return _optional("product_goalies")


def product_teams() -> dict:
    return _optional("product_teams")


def product_model_health() -> dict:
    return _optional("product_model_health")


def snapshot_meta() -> dict:
    """Small provenance dict for banners; never raises."""
    from operational import cloud_snapshot_schema as schema
    try:
        data = _load()
    except SnapshotUnavailable as exc:
        return {"available": False, "error": str(exc)}
    meta = schema.metadata_of(data)
    meta.setdefault("generated_at_utc", meta.get("generated_at"))
    return {"available": True, **copy.deepcopy(meta)}


def freshness() -> dict:
    """What the UI needs to label data honestly: state, the two required timestamps, and where the snapshot came from."""
    from dashboard import snapshot_source
    from operational import cloud_snapshot_schema as schema
    if not snapshot_source.remote_enabled():
        return {"state": schema.UNAVAILABLE, "data_as_of": None, "last_updated": None, "source": "NONE",
                "fetch_status": "NOT_ATTEMPTED", "last_error": "remote snapshot source disabled", "age_hours": None,
                "components": None}
    st = snapshot_source.current()
    return {"state": st.freshness, "data_as_of": st.data_as_of, "last_updated": st.generated_at, "source": st.source,
            "fetch_status": st.fetch_status, "last_error": st.last_error, "age_hours": st.age_hours,
            "components": (schema.metadata_of(st.data).get("freshness") if st.data else None)}
