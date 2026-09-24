"""
DEPRECATED as of the 2026-09-24 Yahoo compliance rebuild -- do not call
run_full_sync() or either step function. They call
fantasy_store.record_league_settings_snapshot()/record_standings_snapshot(),
which now raise (see fantasy/storage/fantasy_store.py): persisting
Yahoo Fantasy Information to disk is exactly what the signed API
Access and Use Agreement's Section 2.c.vii prohibits. Confirmed via a
real audit before any change that this module was never actually
invoked by any dashboard page or scheduled job -- nothing real breaks
by it now raising. The compliant replacement is transient, per-request
fetch (fantasy/yahoo/diagnostic.py's pattern), not a "sync and store"
orchestration -- a real rebuild of this module's ORCHESTRATION idea
(without ever persisting the Yahoo response itself) is Phase 10 scope,
deliberately not done in this pass ("do not build draft/waiver/
streaming UI before the connection diagnostic passes").

Original docstring, preserved for context:

Daily fantasy sync orchestration (Part 129/130) -- a plain callable
function, NOT an installed scheduler (explicit instruction: "Do NOT
install an automatic scheduler unless explicitly authorized" -- none
was, so none exists anywhere in this module or elsewhere in this
sprint).

Sync order (Part 130): league settings -> standings -> roster ->
matchup -> available players -> NHL schedule/context refresh -> fantasy
projections -> recommendations. Each step is real and independently
callable; a failure in one step is recorded and does not silently skip
recording that it happened (Part 115/116 -- fail gracefully, never
crash the rest of the app).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from fantasy.league.settings import parse_league_settings
from fantasy.yahoo.client import ApiResult, YahooFantasyClient
from fantasy.yahoo.parser import extract_league_settings, extract_standings
from fantasy.storage import fantasy_store


@dataclass
class SyncStepResult:
    step: str
    ok: bool
    detail: str = ""


@dataclass
class SyncReport:
    user_key: str
    league_key: str
    steps: list[SyncStepResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.steps)


def sync_league_settings(client: YahooFantasyClient, store_conn: sqlite3.Connection,
                          user_key: str, league_key: str) -> SyncStepResult:
    result: ApiResult = client.get_resource(f"/league/{league_key}/settings")
    if not result.ok:
        return SyncStepResult(step="league_settings", ok=False, detail=f"{result.status}: {result.error}")
    raw = extract_league_settings(result.data)
    settings = parse_league_settings(raw, league_key)
    fantasy_store.record_league_settings_snapshot(
        store_conn, user_key, league_key, settings_json=raw, settings_hash=settings.settings_hash)
    return SyncStepResult(step="league_settings", ok=True, detail=f"settings_hash={settings.settings_hash}")


def sync_standings(client: YahooFantasyClient, store_conn: sqlite3.Connection,
                    user_key: str, league_key: str) -> SyncStepResult:
    result = client.get_resource(f"/league/{league_key}/standings")
    if not result.ok:
        return SyncStepResult(step="standings", ok=False, detail=f"{result.status}: {result.error}")
    standings = extract_standings(result.data)
    fantasy_store.record_standings_snapshot(store_conn, user_key, league_key, standings)
    return SyncStepResult(step="standings", ok=True, detail=f"{len(standings)} team(s)")


def run_full_sync(client: YahooFantasyClient, store_conn: sqlite3.Connection,
                   user_key: str, league_key: str) -> SyncReport:
    """Part 130's real sync order, for the steps this sprint actually
    implemented (league settings, standings). Roster/matchup/available-
    players/recommendation steps are NOT_IMPLEMENTED_THIS_SPRINT -- see
    the report -- and are recorded as such rather than silently skipped,
    so a caller inspecting the SyncReport always sees the true state."""
    report = SyncReport(user_key=user_key, league_key=league_key)
    report.steps.append(sync_league_settings(client, store_conn, user_key, league_key))
    report.steps.append(sync_standings(client, store_conn, user_key, league_key))
    for deferred_step in ("roster", "matchup", "available_players", "fantasy_projections", "recommendations"):
        report.steps.append(SyncStepResult(step=deferred_step, ok=False, detail="NOT_IMPLEMENTED_THIS_SPRINT"))
    return report
