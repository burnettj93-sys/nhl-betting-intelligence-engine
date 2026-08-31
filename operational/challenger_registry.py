"""
2026-27 Continuous Learning framework, Parts 25-29, 34: a machine-
readable registry of PROPOSED challenger experiments, stored as a
simple, append-friendly JSON file (operational/challenger_registry.json)
-- deliberately not a new SQLite table, since this is a small, human-
reviewed list (a handful of entries at most, not a high-volume
observation stream like the prospective ledger).

This registry NEVER causes a production change by itself. Nothing in
this module calls into research/model_registry.py, decision_policy, or
any production model file. Creating an entry here is the daily/weekly
review recommending a research task to a human -- see Part 33: the
owner decides.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "operational" / "challenger_registry.json"

STATUSES = ("HYPOTHESIS", "TESTING", "SHADOW", "REJECTED", "PROMOTION_CANDIDATE", "RETIRED")

REQUIRED_FIELDS = ("challenger_id", "target_model", "hypothesis", "evidence_trigger", "created_at",
                   "training_window", "validation_plan", "status")

# Part 25: a challenger may only be proposed when evidence clears ALL of
# these -- enforced by validate_evidence(), not left to caller discipline.
MIN_REPEATED_OCCURRENCES = 5
MIN_UNIQUE_GAME_DATES = 3


class ChallengerValidationError(Exception):
    pass


def load_registry(path: Path = REGISTRY_PATH) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_registry(entries: list[dict], path: Path = REGISTRY_PATH) -> None:
    with open(path, "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)


def validate_evidence(evidence: dict) -> None:
    """Part 25: evidence must be repeated, materially large, broad
    enough, and explainable -- checked structurally, not just asserted
    in prose. `evidence` is expected to carry: occurrences (int),
    unique_game_dates (int), mean_residual (float), explanation (str).
    Raises ChallengerValidationError with the specific failing
    condition, never a generic rejection."""
    occurrences = evidence.get("occurrences", 0)
    unique_dates = evidence.get("unique_game_dates", 0)
    explanation = evidence.get("explanation", "")
    if occurrences < MIN_REPEATED_OCCURRENCES:
        raise ChallengerValidationError(
            f"evidence has only {occurrences} occurrences, needs >= {MIN_REPEATED_OCCURRENCES} "
            f"(Part 24: one outlier game never creates a new model)")
    if unique_dates < MIN_UNIQUE_GAME_DATES:
        raise ChallengerValidationError(
            f"evidence spans only {unique_dates} distinct game dates, needs >= {MIN_UNIQUE_GAME_DATES} "
            f"(a repeated pattern within one slate is not yet broad evidence)")
    if not explanation:
        raise ChallengerValidationError("evidence has no explanation -- Part 25 requires the pattern "
                                         "be explainable, not just statistically present")


def propose_challenger(*, target_model: str, hypothesis: str, evidence: dict, training_window: str,
                        validation_plan: str, challenger_id: str | None = None,
                        feature_version: str | None = None, training_cutoff: str | None = None,
                        evaluation_cutoff: str | None = None, code_commit: str | None = None,
                        reason_for_change: str | None = None,
                        registry_path: Path = REGISTRY_PATH) -> dict:
    """Validates evidence (raises if it doesn't clear the bar), then
    appends a new HYPOTHESIS-status entry. Never mutates an existing
    entry -- use update_status() for lifecycle transitions.

    Part 34: feature_version/training_cutoff/evaluation_cutoff/
    code_commit/reason_for_change are optional at HYPOTHESIS time (a
    hypothesis may exist before any code is written) but REQUIRED
    (validated by require_version_control_fields()) before a challenger
    may move to TESTING -- see update_status()."""
    validate_evidence(evidence)
    entries = load_registry(registry_path)
    challenger_id = challenger_id or f"challenger_{len(entries) + 1:04d}"
    if any(e["challenger_id"] == challenger_id for e in entries):
        raise ChallengerValidationError(f"challenger_id {challenger_id!r} already exists")
    entry = {
        "challenger_id": challenger_id, "target_model": target_model, "hypothesis": hypothesis,
        "evidence_trigger": evidence, "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "training_window": training_window, "validation_plan": validation_plan, "status": "HYPOTHESIS",
        "model_version": None, "feature_version": feature_version, "training_cutoff": training_cutoff,
        "evaluation_cutoff": evaluation_cutoff, "code_commit": code_commit,
        "reason_for_change": reason_for_change,
        "status_history": [{"status": "HYPOTHESIS", "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}],
    }
    entries.append(entry)
    save_registry(entries, registry_path)
    return entry


_VERSION_CONTROL_FIELDS = ("feature_version", "training_cutoff", "evaluation_cutoff", "code_commit",
                            "reason_for_change")


def require_version_control_fields(entry: dict) -> None:
    """Part 34: every candidate model change must carry model version,
    feature version, training cutoff, evaluation cutoff, code commit,
    and a reason for change -- enforced before TESTING, not left as
    optional metadata a challenger could skip."""
    missing = [f for f in _VERSION_CONTROL_FIELDS if not entry.get(f)]
    if missing:
        raise ChallengerValidationError(
            f"challenger {entry['challenger_id']} is missing required version-control fields "
            f"before entering TESTING: {missing}")


_ALLOWED_TRANSITIONS = {
    "HYPOTHESIS": {"TESTING", "REJECTED"},
    "TESTING": {"SHADOW", "REJECTED"},
    "SHADOW": {"PROMOTION_CANDIDATE", "REJECTED", "RETIRED"},
    "PROMOTION_CANDIDATE": {"RETIRED", "REJECTED"},  # promotion ITSELF happens outside this registry
    "REJECTED": set(),
    "RETIRED": set(),
}


def update_status(challenger_id: str, new_status: str, *, reason: str = "",
                   registry_path: Path = REGISTRY_PATH) -> dict:
    if new_status not in STATUSES:
        raise ValueError(f"unknown status {new_status!r}")
    entries = load_registry(registry_path)
    for entry in entries:
        if entry["challenger_id"] == challenger_id:
            current = entry["status"]
            if new_status not in _ALLOWED_TRANSITIONS.get(current, set()):
                raise ChallengerValidationError(
                    f"cannot transition {challenger_id} from {current} to {new_status} "
                    f"(allowed: {sorted(_ALLOWED_TRANSITIONS.get(current, set()))})")
            if new_status == "TESTING":
                require_version_control_fields(entry)
            entry["status"] = new_status
            entry.setdefault("status_history", []).append(
                {"status": new_status, "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "reason": reason})
            save_registry(entries, registry_path)
            return entry
    raise ChallengerValidationError(f"no challenger with id {challenger_id!r}")


def promotion_candidates(registry_path: Path = REGISTRY_PATH) -> list[dict]:
    return [e for e in load_registry(registry_path) if e["status"] == "PROMOTION_CANDIDATE"]
