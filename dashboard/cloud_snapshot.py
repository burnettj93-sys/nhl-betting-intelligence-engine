"""
Compact, verifiable snapshot of the dashboard's demo board (Community Cloud
memory sprint, 2026-09-25).

WHY: the demo/"recommendation" pages were built by constructing the entire
research model stack (dashboard/demo_data.py::_demo_context -- every frozen
marginal corpus loaded into Python objects, ~430 MB retained) on the first
page view. Streamlit Community Cloud's free tier cannot hold that. The demo
board is fully DETERMINISTIC (fixed seed, fixed simulated date, fixed roster),
so its outputs are computed once, locally, and shipped as a few-hundred-KB
JSON file; COMMUNITY_CLOUD_MODE reads that instead of loading the stack.
This is the "local engine -> compact outputs -> read-only UI" architecture.

HONESTY (Part 15): the snapshot is a frozen artifact, never live state. It
records when it was generated and what real inputs it reflects, and the UI
says so (dashboard/components.py::render_cloud_snapshot_banner). It contains
NO numbers that a live computation would not produce:
`python3 -m dashboard.cloud_snapshot --check` (and tests/test_cloud_snapshot.py)
recompute everything live and fail if the shipped file differs -- so it
cannot silently go stale relative to the code that defines the demo board.

Regenerate after any change to demo_data / eligible_bets / the engines:

    python3 -m dashboard.cloud_snapshot

LOCAL_MODE / PRODUCTION_MODE never read this file; they compute live exactly
as before.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import datetime as dt
import json
import sys
from functools import lru_cache
from pathlib import Path

from operational import runtime_mode

SNAPSHOT_PATH = Path(__file__).resolve().parent / "cloud_snapshot" / "board.json"
SCHEMA_VERSION = 1   # the BUNDLED board.json (v1); the published snapshot is v2 (operational/cloud_snapshot_schema.py)
HISTORY_PROPS = ("sog", "goals", "assists", "points", "blocks")
TREND_PROPS = HISTORY_PROPS + ("toi",)


class SnapshotUnavailable(runtime_mode.HeavyFeatureUnavailable):
    pass


_force_live_depth = 0


@contextlib.contextmanager
def live_compute():
    """Inside this block snapshot_active() is False even in
    COMMUNITY_CLOUD_MODE -- only the snapshot BUILDER/checker uses it."""
    global _force_live_depth
    _force_live_depth += 1
    try:
        yield
    finally:
        _force_live_depth -= 1


def snapshot_active() -> bool:
    return _force_live_depth == 0 and runtime_mode.is_community_cloud()


@lru_cache(maxsize=1)
def _load_bundled() -> dict:
    """The git-bundled board.json (schema 1): the emergency/bootstrap FALLBACK
    only. In Community Cloud the normal source is the remote current snapshot."""
    try:
        data = json.loads(SNAPSHOT_PATH.read_text())
    except FileNotFoundError:
        raise SnapshotUnavailable(
            f"{SNAPSHOT_PATH.name} is missing -- generate it locally with "
            f"`python3 -m dashboard.cloud_snapshot` and commit it.") from None
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotUnavailable("snapshot schema_version mismatch -- regenerate it.")
    return data


def _load() -> dict:
    """The snapshot to serve: the remote current snapshot (last-known-good on a
    failed refresh) in Community Cloud, else the bundled file."""
    from dashboard import snapshot_source
    if snapshot_source.remote_enabled():
        state = snapshot_source.current()
        if state.data is None:
            raise SnapshotUnavailable("no snapshot is available: the remote fetch failed and the "
                                      "bundled fallback is missing")
        return state.data
    return _load_bundled()


def reset_cache() -> None:
    _load_bundled.cache_clear()
    from dashboard import snapshot_source
    snapshot_source.reset()


def load_snapshot() -> dict:
    """The parsed snapshot. Callers must treat it as read-only; the
    accessors below hand out deep copies."""
    return _load()


def _section(*path):
    node = _load()
    for key in path:
        node = node[key]
    return copy.deepcopy(node)


# ---- accessors used by demo_data / eligible_bets / live_dk / player_intelligence_view
def demo_games():
    from dashboard.demo_data import DemoGame
    return [DemoGame(**g) for g in _section("demo", "games")]


def demo_roster():
    from dashboard.demo_data import DemoPlayer
    return [DemoPlayer(**p) for p in _section("demo", "roster")]


def demo_goalies() -> list[dict]:
    return _section("demo", "goalies")


def demo_opportunities() -> list[dict]:
    return _section("demo", "opportunities")


def prop_opportunities() -> list[dict]:
    return _section("demo", "prop_opportunities")


def goalie_saves_opportunities() -> list[dict]:
    return _section("demo", "goalie_saves_opportunities")


def market_movement() -> list[dict]:
    return _section("demo", "market_movement")


def activity_status(player_id: str, team: str, opponent: str) -> dict:
    key = f"{player_id}|{team}|{opponent}"
    table = _load()["demo"]["activity_status"]
    if key not in table:
        raise SnapshotUnavailable(f"no snapshotted activity status for {key}")
    return copy.deepcopy(table[key])


def team_sog_projection(team: str, is_home: bool) -> dict | None:
    table = _load()["demo"]["team_sog_projection"]
    key = f"{team}|{'home' if is_home else 'away'}"
    if key not in table:
        raise SnapshotUnavailable(f"no snapshotted team SOG projection for {key}")
    return copy.deepcopy(table[key])


def player_history(player_id: str) -> dict:
    table = _load()["demo"]["player_history"]
    if player_id not in table:
        raise SnapshotUnavailable(f"no snapshotted history for player {player_id}")
    return copy.deepcopy(table[player_id])


def live_moneyline_rows() -> list[dict]:
    return _section("live_moneyline_rows")


class SectionUnavailable(SnapshotUnavailable):
    """The snapshot in use does not carry this section (e.g. the bundled v1 fallback)."""


def _optional(name: str):
    data = _load()
    if name not in data:
        raise SectionUnavailable(f"the current snapshot has no `{name}` section")
    return copy.deepcopy(data[name])


def performance_state() -> dict:
    return _optional("performance")


def morning_review_report() -> dict:
    return _optional("morning_review")


def model_learning_result() -> dict:
    return _optional("model_learning")


def ledger_section() -> dict:
    return _optional("ledger")


def data_status_section() -> dict:
    return _optional("data_status")


def health_section() -> dict:
    return _optional("health")


def real_recommendations() -> dict:
    return _optional("real_recommendations")


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
    """What the UI needs to label data honestly: the state, the two required
    timestamps, and where the snapshot came from."""
    from dashboard import snapshot_source
    from operational import cloud_snapshot_schema as schema
    if snapshot_source.remote_enabled():
        st = snapshot_source.current()
        return {"state": st.freshness, "data_as_of": st.data_as_of, "last_updated": st.generated_at,
                "source": st.source, "fetch_status": st.fetch_status, "last_error": st.last_error,
                "age_hours": st.age_hours, "components": (schema.metadata_of(st.data).get("freshness")
                                                          if st.data else None)}
    meta = snapshot_meta()
    as_of = meta.get("data_as_of") or meta.get("newest_real_dk_capture_utc")
    return {"state": schema.classify_freshness(as_of) if meta.get("available") else schema.UNAVAILABLE,
            "data_as_of": as_of, "last_updated": meta.get("generated_at"), "source": "BUNDLED",
            "fetch_status": "NOT_ATTEMPTED", "last_error": None,
            "age_hours": schema.age_hours(as_of), "components": meta.get("freshness")}


# ---- building / verifying (local only; imports the heavy stack lazily)
def _jsonable(obj):
    """Strict JSON round-trip (tuples -> lists, dataclasses -> dicts); raises
    on NaN/inf or any non-serializable value rather than storing a lie."""
    def default(o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        raise TypeError(f"not JSON-serializable: {type(o).__name__}")
    return json.loads(json.dumps(obj, default=default, allow_nan=False))


def build_snapshot(now: dt.datetime | None = None) -> dict:
    runtime_mode.require_not_community_cloud("building the cloud snapshot (needs the full model stack)")
    with live_compute():
        from dashboard import data_access as da
        from dashboard import demo_data as dd
        from dashboard import eligible_bets as eb
        from dashboard import game_detail_view as gdv
        from dashboard import live_dk as ldk
        from dashboard import player_intelligence_view as piv

        games = dd.build_demo_games()
        roster = dd.build_demo_roster()
        goalies = dd.build_demo_goalies()
        demo_opps = dd.build_demo_opportunities()
        prop_opps = eb.build_all_player_prop_opportunities()
        goalie_opps = eb.build_goalie_saves_opportunities()
        movement = dd.build_demo_market_movement(demo_opps)
        activity = {f"{p.player_id}|{p.team}|{p.opponent}": dd.player_activity_status(p.player_id, p.team, p.opponent)
                    for p in roster}
        history = {}
        for p in roster:
            history[p.player_id] = {
                "actual_vs_expected": {prop: piv.actual_vs_expected(p.player_id, prop, 5) for prop in HISTORY_PROPS},
                "multi_window_trend": {prop: piv.multi_window_trend(p.player_id, prop) for prop in TREND_PROPS},
                "context_evidence": piv.context_evidence(p.player_id, p.team, p.opponent),
            }
        team_sog = {f"{team}|{side}": gdv.team_sog_projection(team, side == "home")
                    for team in dd.DEMO_TEAMS for side in ("home", "away")}
        live_rows = ldk.build_live_moneyline_comparisons()
        try:
            elo_last = max(r["game_date"] for r in da.compute_baseline_predictions())
        except da.DataAvailabilityError:
            elo_last = None

    captured = [r["captured_at_utc"] for r in live_rows if r.get("captured_at_utc")]
    now = now or dt.datetime.now(dt.timezone.utc)
    return _jsonable({
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "generated_at_utc": now.isoformat(),
            "simulated_slate_date": dd.SIMULATED_DATE,
            "demo_seed": dd.DEMO_SEED,
            "elo_corpus_last_game_date": elo_last,
            "newest_real_dk_capture_utc": max(captured) if captured else None,
            "live_moneyline_row_count": len(live_rows),
        },
        "demo": {
            "games": games, "roster": roster, "goalies": goalies, "opportunities": demo_opps,
            "prop_opportunities": prop_opps, "goalie_saves_opportunities": goalie_opps,
            "market_movement": movement, "activity_status": activity, "player_history": history,
            "team_sog_projection": team_sog,
        },
        "live_moneyline_rows": live_rows,
    })


# Only the DETERMINISTIC part of the snapshot is checked for equality with a
# live recompute. live_moneyline_rows reflect real DraftKings captures that
# change every scheduler cycle, so they are labeled with their own capture
# timestamps (and the banner says how old they are) rather than compared.
_DETERMINISTIC_META = ("simulated_slate_date", "demo_seed")


def _deterministic_view(snapshot: dict) -> dict:
    return {"schema_version": snapshot.get("schema_version"),
            "meta": {k: snapshot["meta"].get(k) for k in _DETERMINISTIC_META},
            "demo": snapshot.get("demo")}


def verify_against_live() -> list[str]:
    """Empty list == the shipped snapshot's deterministic demo board equals
    a fresh live computation."""
    live = _deterministic_view(build_snapshot())
    stored = _deterministic_view(json.loads(SNAPSHOT_PATH.read_text()))
    if live == stored:
        return []
    problems = [k for k in ("schema_version", "meta") if live[k] != stored[k]]
    problems += [f"demo.{k}" for k in sorted(set(live["demo"]) | set(stored["demo"]))
                 if live["demo"].get(k) != stored["demo"].get(k)]
    return problems


def write_snapshot(path: Path | None = None) -> Path:
    path = path or SNAPSHOT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_snapshot(), indent=1, sort_keys=True) + "\n")
    reset_cache()
    return path


def main(argv: list[str]) -> int:
    if "--check" in argv:
        problems = verify_against_live()
        if problems:
            print("STALE: snapshot differs from live computation in: " + ", ".join(problems))
            print("Run: python3 -m dashboard.cloud_snapshot")
            return 1
        print("OK: snapshot matches live computation.")
        return 0
    path = write_snapshot()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
