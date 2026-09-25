"""
Production-runtime hygiene audit and safe cleanup (Live Run Reliability block, 2026-09-25).

    python3 -m operational.runtime_hygiene            # audit (read-only)
    python3 -m operational.runtime_hygiene --clean    # remove ONLY proven-synthetic records / empty stray DBs

Findings this module exists for (all seen on this machine):
  * `operational/prop_contract_candidates.jsonl` held two `fixture-*` records written by an old test run;
  * `operational/runtime/prospective_observations.db` was a 0-byte file nothing in the code refers to;
  * unit tests with NHL_ENGINE_CLOUD_PUBLISH=ON in the real .env launched the REAL publisher.
Root cause fix is `operational/state_paths.py` (tests can no longer resolve production state paths); this module
detects any recurrence (readiness: TEST_RUNTIME_ISOLATION / PROP_DISCOVERY_STATE_CLEAN) and cleans up the residue
without ever touching a real record. Every removal is appended to `operational/runtime/hygiene_removals.jsonl`.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

from operational import state_paths

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_LOG = REPO_ROOT / "operational" / "prop_contract_candidates.jsonl"
RUNTIME_DIR = REPO_ROOT / "operational" / "runtime"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
REMOVAL_LOG = RUNTIME_DIR / "hygiene_removals.jsonl"
REAL_EVENT_ID = re.compile(r"^[0-9a-f]{32}$")


def fixture_event_ids(fixtures_dir: Path | None = None) -> set[str]:
    """Every `id` used by a committed test fixture: the ONLY ids that prove a candidate record is synthetic."""
    ids: set[str] = set()
    for f in Path(fixtures_dir or FIXTURES_DIR).glob("*.json"):
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for node in (doc if isinstance(doc, list) else [doc]):
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                ids.add(node["id"])
    return ids


def is_proven_synthetic(record: dict, fixture_ids: set[str]) -> bool:
    """Synthetic = its event id is a `fixture-*` id that also exists in a committed fixture file. Anything else
    -- including a malformed line or an unknown id -- is preserved."""
    eid = str(record.get("event_id") or "")
    return eid.startswith("fixture-") and eid in fixture_ids and not REAL_EVENT_ID.match(eid)


def audit(candidate_log: Path | None = None, runtime_dir: Path | None = None, fixtures_dir: Path | None = None) -> dict:
    candidate_log = Path(candidate_log or CANDIDATE_LOG)
    runtime_dir = Path(runtime_dir or RUNTIME_DIR)
    fixtures = fixture_event_ids(fixtures_dir)
    findings, real, synthetic, unparseable = [], 0, 0, 0
    try:
        for line in candidate_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                unparseable += 1
                continue
            if is_proven_synthetic(rec, fixtures):
                synthetic += 1
            elif REAL_EVENT_ID.match(str(rec.get("event_id") or "")):
                real += 1
            else:
                findings.append(f"candidate record with unrecognized event id {rec.get('event_id')!r} (kept)")
    except OSError:
        pass
    if synthetic:
        findings.append(f"{synthetic} synthetic (fixture) record(s) in the real candidate log")
    if unparseable:
        findings.append(f"{unparseable} unparseable line(s) in the candidate log (kept)")
    empty_dbs = sorted(p.name for p in runtime_dir.glob("*.db") if p.is_file() and p.stat().st_size == 0)
    if empty_dbs:
        findings.append(f"zero-byte database file(s) in operational/runtime: {empty_dbs}")
    state_file = runtime_dir / "prop_discovery_state.json"
    if state_file.exists():
        try:
            st = json.loads(state_file.read_text())
            bad = [k for k in (st.get("swept") or {}) if not REAL_EVENT_ID.match(k.split(":", 1)[-1])]
            if bad:
                findings.append(f"prop_discovery_state.json holds non-real event ids: {bad[:3]}")
            future = [d for d in (st.get("days") or {}) if d > (dt.date.today() + dt.timedelta(days=1)).isoformat()]
            if future:
                findings.append(f"prop_discovery_state.json has future-dated spend days: {future[:3]}")
        except ValueError:
            findings.append("prop_discovery_state.json is not valid JSON")
    return {"clean": not findings, "findings": findings, "candidate_records": {"real": real, "synthetic": synthetic},
            "empty_dbs": empty_dbs}


def clean(candidate_log: Path | None = None, runtime_dir: Path | None = None, fixtures_dir: Path | None = None,
          removal_log: Path | None = None, dry_run: bool = False) -> dict:
    """Remove ONLY proven-synthetic candidate records and 0-byte .db files directly inside operational/runtime.
    Real records are rewritten byte-for-byte; every removal is logged."""
    candidate_log = Path(candidate_log or CANDIDATE_LOG)
    runtime_dir = Path(runtime_dir or RUNTIME_DIR)
    removal_log = Path(removal_log or REMOVAL_LOG)
    fixtures = fixture_event_ids(fixtures_dir)
    removed: list[dict] = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if candidate_log.exists():
        keep, drop = [], []
        for line in candidate_log.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if is_proven_synthetic(rec, fixtures):
                drop.append(rec)
            else:
                keep.append(line)
        for rec in drop:
            removed.append({"at": now, "kind": "synthetic_candidate_record", "file": candidate_log.name,
                            "market_key": rec.get("market_key"), "event_id": rec.get("event_id"),
                            "proof": "event id is a fixture id present in tests/fixtures"})
        if drop and not dry_run:
            tmp = candidate_log.with_suffix(".tmp")
            tmp.write_text("".join(l + "\n" for l in keep))
            tmp.replace(candidate_log)
    for p in sorted(runtime_dir.glob("*.db")):
        if p.is_file() and not p.is_symlink() and p.stat().st_size == 0:
            removed.append({"at": now, "kind": "empty_stray_db", "file": p.name, "bytes": 0,
                            "proof": "zero bytes; no code path references it"})
            if not dry_run:
                p.unlink()
    if removed and not dry_run:
        removal_log.parent.mkdir(parents=True, exist_ok=True)
        with open(removal_log, "a") as f:
            for r in removed:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    return {"dry_run": dry_run, "removed": removed}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--clean" in argv:
        print(json.dumps(clean(dry_run="--dry-run" in argv), indent=2))
    print(json.dumps(audit(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
