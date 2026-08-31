"""
2026-27 Continuous Learning framework, Part 22-24: classifies a single
large miss (a settled row with a large residual) into one of a fixed
taxonomy, using only real, already-recorded fields on the row -- never
guesses beyond what the row and its role-state fields actually show.
UNKNOWN is a legitimate, honest outcome, not a fallback to hide.

Part 24: this module classifies ONE miss at a time. It is deliberately
NOT a "create a new model" trigger by itself -- see
operational/challenger_registry.py's evidence-aggregation requirement
(repeated + material + broad + explainable) before any challenger is
proposed from a pattern of these classifications.
"""
from __future__ import annotations

RANDOM_VARIANCE = "RANDOM_VARIANCE"
ROLE_CHANGE = "ROLE_CHANGE"
STARTER_ERROR = "STARTER_ERROR"
ACTIVE_STATUS_ERROR = "ACTIVE_STATUS_ERROR"
MODEL_CALIBRATION = "MODEL_CALIBRATION"
FEATURE_DRIFT = "FEATURE_DRIFT"
DATA_ERROR = "DATA_ERROR"
MARKET_MAPPING_ERROR = "MARKET_MAPPING_ERROR"
UNKNOWN = "UNKNOWN"

TAXONOMY = (RANDOM_VARIANCE, ROLE_CHANGE, STARTER_ERROR, ACTIVE_STATUS_ERROR, MODEL_CALIBRATION,
            FEATURE_DRIFT, DATA_ERROR, MARKET_MAPPING_ERROR, UNKNOWN)

# A single extreme observation is never, by itself, evidence of a
# systematic problem (Part 24) -- this only labels a CANDIDATE reason
# for a large residual; the daily/weekly review decides whether a
# pattern of these labels across many rows is worth a challenger.
_ROLE_TRANSITION_STATES = frozenset({
    "PROMOTED_PP2_TO_PP1", "ADDED_TO_PP1", "ADDED_TO_PP2", "DEMOTED_PP1_TO_PP2",
    "REMOVED_FROM_PP", "PROMOTED_PK2_TO_PK1", "ADDED_TO_PK1", "ADDED_TO_PK2",
    "DEMOTED_PK1_TO_PK2", "REMOVED_FROM_PK",
})


def classify_miss(row: dict, residual: float, *, large_residual_threshold: float = 0.5) -> str:
    """`row`: a settled ledger row. `residual` = actual_hit - predicted
    (same sign convention as model_scorecard.compute_scorecard). Checks
    the most specific, most confidently-attributable causes first;
    UNKNOWN if none of the row's own real fields explain it."""
    if abs(residual) < large_residual_threshold:
        return RANDOM_VARIANCE

    notes = row.get("notes") or ""
    if row.get("result_status") == "UNRESOLVED":
        if "PLAYER_DID_NOT_DRESS" in notes:
            return ACTIVE_STATUS_ERROR
        if "GOALIE_DID_NOT_PLAY" in notes:
            return STARTER_ERROR
        if "UNSUPPORTED_SETTLEMENT_MARKET" in notes or "NOT_INGESTED" in notes:
            return MARKET_MAPPING_ERROR

    if row.get("pp_transition_state") in _ROLE_TRANSITION_STATES:
        return ROLE_CHANGE
    if row.get("pp_games_since_transition") is not None and row.get("pp_games_since_transition") <= 2:
        return ROLE_CHANGE

    if row.get("pp_role_certainty") is not None and row["pp_role_certainty"] < 0.34:
        return DATA_ERROR  # thin role evidence masquerading as a confident miss

    if row.get("confidence") == "HIGH" and abs(residual) >= large_residual_threshold:
        return MODEL_CALIBRATION

    return UNKNOWN


def summarize_misses(classified: list[str]) -> dict:
    """Part 23: a simple count-by-category rollup for the daily report's
    'large-miss review' section."""
    counts = {t: 0 for t in TAXONOMY}
    for c in classified:
        counts[c] = counts.get(c, 0) + 1
    return counts
