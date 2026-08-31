"""
2026-27 Continuous Learning framework, Parts 18-21, 38-40, 53: engine-
level status determination (NORMAL/WATCH/INVESTIGATE/HALT) plus the
drift-monitoring checks that feed it. Every check here either (a)
inspects real, already-available state (settlement completeness, data
freshness via operational/system_health.py, contract-verification
count) or (b) honestly reports INSUFFICIENT_DATA when no real 2026-27
season data exists yet to compare against -- never a synthetic
placeholder presented as a real reading.
"""
from __future__ import annotations

NORMAL = "NORMAL"
WATCH = "WATCH"
INVESTIGATE = "INVESTIGATE"
HALT = "HALT"

_SEVERITY_ORDER = {NORMAL: 0, WATCH: 1, INVESTIGATE: 2, HALT: 3}


def combine_status(statuses: list[str]) -> str:
    """The overall status is the MOST SEVERE of its inputs -- never
    averaged or hidden by a majority of NORMAL readings."""
    if not statuses:
        return NORMAL
    return max(statuses, key=lambda s: _SEVERITY_ORDER.get(s, 0))


def check_settlement_completeness(summary: dict) -> dict:
    """Part 53: if settlement has unresolved critical errors, the review
    must flag incomplete results rather than silently score partial data
    as though it were complete."""
    errors = summary.get("errors", [])
    if errors:
        return {"status": INVESTIGATE, "reason": f"{len(errors)} settlement error(s) this run -- "
                                                   f"downstream metrics for affected predictions are incomplete"}
    return {"status": NORMAL, "reason": "settlement completed cleanly"}


def check_run_order(inputs_ready: dict) -> dict:
    """Part 1/52: the daily review must not run before results are
    ingested and settlement has completed. `inputs_ready` is a dict of
    {"results_ingested": bool, "settlement_completed": bool}."""
    if not inputs_ready.get("results_ingested"):
        return {"status": HALT, "reason": "official result ingestion has not completed -- refusing to score"}
    if not inputs_ready.get("settlement_completed"):
        return {"status": HALT, "reason": "settlement has not completed -- refusing to score unsettled predictions"}
    return {"status": NORMAL, "reason": "run order satisfied"}


def check_contract_status() -> dict:
    """Part 21: while no market's payload has ever been observed, there
    is nothing to drift-check -- this is an honest, expected WATCH-free
    state, not silently skipped."""
    from research.generic_prop_pricing.provider_adapter import VERIFIED_CONTRACTS
    if not VERIFIED_CONTRACTS:
        return {"status": NORMAL, "reason": "no verified contracts yet -- nothing to drift-check",
                "verified_contracts": 0}
    return {"status": WATCH, "reason": f"{len(VERIFIED_CONTRACTS)} verified contract(s) exist -- "
                                        f"drift monitoring against them is not yet implemented",
            "verified_contracts": len(VERIFIED_CONTRACTS)}


def check_input_drift(historical_rate: float | None, current_rate: float | None, *,
                       label: str, relative_threshold: float = 0.20) -> dict:
    """Part 18: compares a real historical per-game rate (from the
    frozen research corpus) against a real current-season rate (once
    one exists). INSUFFICIENT_DATA -- never a fabricated 0% drift --
    when either side is missing."""
    if historical_rate is None or current_rate is None:
        return {"status": "INSUFFICIENT_DATA", "label": label,
                "reason": "no current-season observations yet to compare against the historical rate"}
    if historical_rate == 0:
        return {"status": "INSUFFICIENT_DATA", "label": label, "reason": "historical rate is 0, cannot compute relative drift"}
    relative_change = (current_rate - historical_rate) / historical_rate
    flagged = abs(relative_change) >= relative_threshold
    return {"status": WATCH if flagged else NORMAL, "label": label,
            "historical_rate": historical_rate, "current_rate": current_rate,
            "relative_change": relative_change,
            "reason": (f"{label} moved {relative_change:+.1%} vs. the historical rate" if flagged
                       else f"{label} within {relative_threshold:.0%} of the historical rate")}


def check_league_environment_flags(drift_checks: list[dict]) -> dict:
    """Part 19: aggregates input-drift WATCH flags into a league-
    environment observation -- explicitly a FLAG, never an automatic
    adjustment (this function returns no coefficient, no correction,
    only a labeled observation for a human to review)."""
    flagged = [c for c in drift_checks if c.get("status") == WATCH]
    if not flagged:
        return {"status": NORMAL, "flagged_dimensions": []}
    return {"status": WATCH, "flagged_dimensions": [c["label"] for c in flagged],
            "note": "flagged only -- no automatic model adjustment was made"}


HALT_CONDITION_LABELS = (
    "major_data_contract_break", "stale_critical_input", "settlement_inconsistency",
    "player_mapping_failure_at_scale", "materially_impossible_probability",
    "unreliable_provider_timestamps", "production_shadow_contamination",
)


def check_halt_conditions(flags: dict) -> dict:
    """Part 39. `flags`: {label: bool} for each of HALT_CONDITION_LABELS
    -- the caller supplies real, already-computed booleans; this
    function only aggregates them into the HALT decision, it does not
    itself detect any of them (those checks live where the relevant
    real data already is: system_health.py, outcome_resolver.py, etc.)."""
    triggered = [label for label in HALT_CONDITION_LABELS if flags.get(label)]
    if triggered:
        return {"status": HALT, "triggered": triggered}
    return {"status": NORMAL, "triggered": []}


WATCH_CONDITION_LABELS = (
    "worsening_calibration", "starter_misses", "market_drift", "unexpected_feature_shift",
    "low_sample_size",
)


def check_watch_conditions(flags: dict) -> dict:
    """Part 40. Same aggregation pattern as check_halt_conditions."""
    triggered = [label for label in WATCH_CONDITION_LABELS if flags.get(label)]
    if triggered:
        return {"status": WATCH, "triggered": triggered}
    return {"status": NORMAL, "triggered": []}
