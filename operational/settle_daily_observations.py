"""
Daily settlement entry point (Preseason Closing sprint, Section 20-21;
upgraded to a real, working resolver in the Preseason Operational
Readiness Closure sprint, 2026-08-30, Track 3 Part 22-24).

Usage:
    python3 -m operational.settle_daily_observations

Finds PENDING observations whose event_start_utc has already passed,
resolves each against OFFICIAL, already-ingested NHL outcome data (via
operational.outcome_resolver, itself read-only over nhl.db -- Section 21:
no bookmaker-result scraping anywhere in this project), and writes ONLY
result fields via operational.prospective_recording.settle_completed_observation
(which itself only ever calls prospective_ledger.settle_prediction --
prediction fields are structurally protected by the DB immutability
trigger regardless of what this script does).

Idempotent by construction (Part 23): only PENDING rows are ever selected;
a row whose game isn't FINAL yet is left PENDING for a later run to pick
up; a row that resolves is moved OUT of PENDING, so re-running never
double-settles it, and re-running against unchanged official data would
in any case recompute the identical conclusion.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import db
from operational import clv_resolver
from operational import closing_price_lookup
from operational import ingestion_health
from operational import outcome_resolver as resolver
from operational import prospective_ledger as pl
from operational import prospective_recording as pr

# Statuses the resolver can return that mean "this specific market/game/
# player combination cannot be truthfully resolved right now" -- mapped to
# the ledger's own RESULT_STATES (PENDING/WIN/LOSS/PUSH/VOID/UNRESOLVED),
# never a new, unregistered status string (Part: reuse existing ledger
# vocabulary, never invent a parallel one).
_VOID_ON_REAL_MONEY = frozenset({resolver.PLAYER_DID_NOT_DRESS, resolver.GOALIE_DID_NOT_PLAY})
_REAL_MONEY_RECORD_TYPES = frozenset({"REAL_BET", "SHADOW_POLICY_OBSERVATION"})

# Job Sequencing Fix (Production Gap Closure sprint, 2026-09-30): every
# ingestion job that can flip a game to FINAL and so create new settlement
# work. The clock-scheduled daily-settlement job (07:15) only ever depended
# on "nhl_sync_full" being fresh within 30h -- it never noticed that the
# SAME-DAY midday/pregame refreshes (which also ingest boxscores and can
# finalize games hours after the 07:15 run) had produced a newer
# generation of results than the one settlement last consumed. This is
# the real fix for that gap: settlement now tracks which generation of
# these components it last settled against, and is retriggered by
# operational/settlement_trigger_hook.py whenever a newer one lands,
# instead of waiting for tomorrow's 07:15 slot.
GENERATION_COMPONENTS = ("nhl_sync_full", "nhl_midday_schedule_refresh", "nhl_pregame_targeted_refresh")


def current_input_generation_utc() -> str | None:
    """The freshest successful-ingestion timestamp across every component
    that can finalize a game right now -- None if none has ever succeeded."""
    return ingestion_health.latest_success_utc(list(GENERATION_COMPONENTS))


def _watermark_path() -> Path:
    from operational import state_paths as _sp
    return _sp.path("settlement_last_processed_generation.json", area="operational")


def _last_processed_generation_utc(*, path=None) -> str | None:
    """The input generation settlement itself last successfully ran
    against -- None before this fix ever ran once, or on a fresh install."""
    path = path or _watermark_path()
    try:
        return json.loads(path.read_text()).get("input_generation_utc")
    except (OSError, ValueError):
        return None


def _record_processed_generation(generation_utc: str | None, *, path=None) -> None:
    if generation_utc is None:
        return
    path = path or _watermark_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"input_generation_utc": generation_utc,
                                "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}))
    tmp.replace(path)


def find_settlement_candidates(conn) -> list[dict]:
    """PENDING observations whose game has already started -- the exact
    set a real settlement job would resolve next."""
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    rows = pl.query_observations(conn)
    return [r for r in rows if r["result_status"] == "PENDING" and r["event_start_utc"] < now_iso]


def _audit_notes(resolution: dict) -> str:
    """Part 24: resolution source, resolver version, resolved_at, official
    game status -- stored in the existing `notes` column (schema already
    permits this; no migration needed) as a small JSON blob rather than
    free text, so a future script can parse it back out reliably."""
    return json.dumps({
        "resolver_status": resolution["status"],
        "resolution_source": resolution["resolution_source"],
        "resolver_version": resolution["resolver_version"],
        "official_game_status": resolution["official_game_status"],
        "resolved_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    })


def _result_status_for(resolution: dict, record_type: str) -> str | None:
    """Returns the ledger result_status to write, or None if this
    observation should remain PENDING (game not final yet). Part 21: a
    named player/goalie who never appeared in the real, official stats
    for a market with real money attached (REAL_BET / SHADOW_POLICY_
    OBSERVATION) is VOID -- a widely-standard, uncontroversial industry
    convention for a named-player prop, not a DK-specific rule this
    project has ever observed. For MODEL_OBSERVATION (no real money),
    the SAME real-world fact is preserved as a distinct UNRESOLVED
    eligibility state instead (Part 21's explicit instruction) -- never
    silently folded into WIN/LOSS, and never labeled VOID as if money
    had been on it."""
    status = resolution["status"]
    if status == resolver.GAME_NOT_FINAL:
        return None
    if status == resolver.RESOLVED:
        return "WIN" if resolution["outcome_hit"] else "LOSS"
    if status in _VOID_ON_REAL_MONEY:
        return "VOID" if record_type in _REAL_MONEY_RECORD_TYPES else "UNRESOLVED"
    # UNSUPPORTED_SETTLEMENT_MARKET, TEAM_SOG_NOT_INGESTED, BLOCKS_NOT_INGESTED:
    # a real, current limitation of THIS system, not a fact about the game --
    # never guessed, always UNRESOLVED with the real reason in notes.
    return "UNRESOLVED"


def _resolve_real_closing_price(official_conn, obs: dict) -> tuple[float | None, str | None]:
    """Real Recommendation Pipeline block (2026-09-24), Parts 14-16: the
    automated closing-price lookup that CLOSED_LOOP_CERTIFICATION
    previously reported as PARTIAL/missing. Only attempted for MONEYLINE
    (the only market with real archived DraftKings price history in
    odds_snapshots today -- Part 6/21: never fabricate a prop close that
    doesn't exist yet). Returns (None, None) -- never raises -- whenever
    no valid real close exists (game/side never had real market data, or
    every real snapshot lands at/after event_start_utc); CLV then stays
    genuinely unavailable rather than a fabricated 0.0 (Part 16)."""
    if obs.get("market_id") != "MONEYLINE" or obs.get("odds_american") is None:
        return None, None
    selection = obs.get("side") or obs.get("team")
    if not selection or not obs.get("game_id") or not obs.get("event_start_utc"):
        return None, None
    result = closing_price_lookup.resolve_real_moneyline_closing_price(
        official_conn, game_id=obs["game_id"], selection=selection,
        event_start_utc=obs["event_start_utc"])
    if result["status"] != clv_resolver.RESOLVED:
        return None, None
    return result["closing_odds"], result["closing_captured_at_utc"]


def run_settlement_batch(ledger_conn, official_conn=None) -> dict:
    """The real batch: find eligible unresolved observations, resolve
    each against official data, call settle_prediction(), and produce a
    reconciliation summary. `official_conn` defaults to the real nhl.db
    (db.get_conn()) -- injectable for tests, never a second, parallel
    connection helper."""
    if official_conn is None:
        official_conn = db.get_conn()

    candidates = find_settlement_candidates(ledger_conn)
    summary = {"total_candidates": len(candidates), "settled_win": 0, "settled_loss": 0,
               "settled_void": 0, "settled_unresolved": 0, "still_pending_game_not_final": 0,
               "errors": []}

    for obs in candidates:
        try:
            resolution = resolver.resolve_prediction(official_conn, dict(obs))
        except Exception as exc:  # noqa: BLE001 -- one bad row must never abort the whole batch
            summary["errors"].append({"prediction_id": obs["prediction_id"], "error": str(exc)})
            continue

        result_status = _result_status_for(resolution, obs["record_type"])
        if result_status is None:
            summary["still_pending_game_not_final"] += 1
            continue

        closing_odds, closing_captured_at_utc = _resolve_real_closing_price(official_conn, obs)

        pr.settle_completed_observation(
            ledger_conn, obs["prediction_id"], actual_outcome=resolution["actual_value"],
            result_status=result_status, closing_odds=closing_odds,
            closing_captured_at_utc=closing_captured_at_utc, notes=_audit_notes(resolution))

        if result_status == "WIN":
            summary["settled_win"] += 1
        elif result_status == "LOSS":
            summary["settled_loss"] += 1
        elif result_status == "VOID":
            summary["settled_void"] += 1
        else:
            summary["settled_unresolved"] += 1

    return summary


def backlog_status(ledger_conn, official_conn=None) -> dict:
    """Read-only visibility (Production Gap Closure sprint, 2026-09-30):
    distinguishes "the job hasn't gotten to this yet" (final_awaiting_
    settlement -- the real job-sequencing backlog this fix targets) from
    "the game genuinely hasn't finished" (awaiting_game_final -- normal,
    expected, not a defect), and reports the specific resolver reason for
    every final-but-unsettled row instead of one flat count. Also reports
    the input generation settlement last consumed vs. the freshest one
    available right now, so a caller can see AT A GLANCE whether a rerun
    is due. Never writes anything -- safe to call from a dashboard page."""
    if official_conn is None:
        official_conn = db.get_conn()
    pending = [r for r in pl.query_observations(ledger_conn) if r["result_status"] == "PENDING"]
    awaiting_final = 0
    final_awaiting_settlement = 0
    reason_counts: dict[str, int] = {}
    for obs in pending:
        is_final, _ = resolver._is_final(official_conn, obs["game_id"])
        if not is_final:
            awaiting_final += 1
            continue
        final_awaiting_settlement += 1
        try:
            resolution = resolver.resolve_prediction(official_conn, dict(obs))
            reason = resolution["status"]
        except Exception as exc:  # noqa: BLE001 -- a read-only status view must never raise
            reason = f"ERROR:{type(exc).__name__}"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    current_gen = current_input_generation_utc()
    last_processed = _last_processed_generation_utc()
    return {
        "pending_total": len(pending),
        "awaiting_game_final": awaiting_final,
        "final_awaiting_settlement": final_awaiting_settlement,
        "final_awaiting_settlement_reasons": reason_counts,
        "current_input_generation_utc": current_gen,
        "last_processed_input_generation_utc": last_processed,
        "new_generation_available": bool(
            current_gen and (last_processed is None or current_gen > last_processed)),
    }


def _run_and_record(ledger_conn, official_conn, *, generation_utc: str | None) -> dict:
    """Shared by both entry points below: run the batch, print/record the
    exact same way regardless of what triggered it (clock slot vs. a
    fresh ingestion generation), and stamp the generation just consumed
    on success so the NEXT trigger (of either kind) knows not to redo it."""
    summary = run_settlement_batch(ledger_conn, official_conn)
    print(f"{summary['total_candidates']} PENDING observation(s) past their event start.")
    print(f"  WIN: {summary['settled_win']}  LOSS: {summary['settled_loss']}  "
          f"VOID: {summary['settled_void']}  UNRESOLVED: {summary['settled_unresolved']}  "
          f"still pending (game not final): {summary['still_pending_game_not_final']}")
    if summary["errors"]:
        print(f"  {len(summary['errors'])} error(s):")
        for e in summary["errors"][:20]:
            print(f"    {e['prediction_id']}: {e['error']}")
    health_status = "FAILED" if summary["errors"] else "SUCCESS"
    ingestion_health.record_run("settlement", {**summary, "status": health_status})
    if health_status == "SUCCESS":
        _record_processed_generation(generation_utc)
    return {**summary, "status": health_status, "input_generation_utc": generation_utc}


def run_if_new_generation(ledger_conn=None, official_conn=None, *, force: bool = False) -> dict:
    """Event-triggered settlement -- called by
    operational/settlement_trigger_hook.py right after an ingestion job
    that can finalize games (see GENERATION_COMPONENTS) succeeds. Runs
    the SAME idempotent run_settlement_batch() the 07:15 scheduled job
    uses -- never a second, parallel settlement path -- but is gated on
    "has a newer ingestion generation landed since I last settled"
    rather than a fixed clock time. This is the actual fix for "a
    same-day midday/pregame refresh finalizes new games, but nothing
    settles them until tomorrow's 07:15 run": that refresh's own success
    can now retrigger settlement the same day. Bounded: a component that
    fires every 15-30 minutes but hasn't produced anything new since the
    last settlement run is a fast, cheap no-op (SKIPPED), never a full
    resolver sweep for nothing. `force=True` (reconciliation / tests)
    bypasses the generation check and always runs."""
    ledger_conn = ledger_conn or pl.init_db()
    current_gen = current_input_generation_utc()
    last_processed = _last_processed_generation_utc()
    if not force and current_gen is not None and last_processed is not None and current_gen <= last_processed:
        return {"status": "SKIPPED", "reason": "NO_NEW_GENERATION",
                "current_input_generation_utc": current_gen,
                "last_processed_input_generation_utc": last_processed}
    return _run_and_record(ledger_conn, official_conn, generation_utc=current_gen)


def main() -> None:
    from operational import deployment_mode as dm
    if not dm.require_active_scheduler_or_exit("settle_daily_observations"):
        return

    # Real Recommendation Pipeline block (2026-09-24), Part 24: the
    # morning workflow (07:00 sync -> 07:15 settlement -> 07:30
    # postmortem -> 07:45 backup) was previously only clock-scheduled --
    # if the 07:00 NHL sync failed or never ran, settlement would still
    # fire at 07:15 against a stale/incomplete schedule. DEFER (never
    # retry-loop) when the upstream sync isn't confirmed healthy; the
    # next scheduled settlement run picks it up once sync recovers.
    sync_ready, sync_reason = ingestion_health.dependency_ready("nhl_sync_full", max_age_hours=30.0)
    if not sync_ready:
        print(f"DEFERRED: upstream nhl_sync_full not ready ({sync_reason}) -- skipping this settlement run.")
        ingestion_health.record_run("settlement", {"status": "DEFERRED", "reason": sync_reason})
        return

    conn = pl.init_db()
    result = _run_and_record(conn, None, generation_utc=current_input_generation_utc())
    # Cloud live-data sprint (2026-09-25): settlement changed the ledger/bankroll ->
    # publish (opt-in, downstream, never raises, only when the run did not fail).
    if result["status"] == "SUCCESS":
        from operational import cloud_publish_hook
        print("cloud snapshot:", cloud_publish_hook.publish_after("settlement"))


if __name__ == "__main__":
    main()
