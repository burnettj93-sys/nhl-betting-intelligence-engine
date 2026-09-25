"""
Publishes the compact "current" Cloud snapshot to the dedicated `cloud-data`
git branch (Cloud live-data sprint, 2026-09-25).

    python3 -m operational.publish_cloud_snapshot            # publish if changed
    python3 -m operational.publish_cloud_snapshot --dry-run  # build + validate, no publish
    python3 -m operational.publish_cloud_snapshot --output out.json   # write a validated file only
    python3 -m operational.publish_cloud_snapshot --force    # ignore NO_CHANGE / rate limit

Result status (printed as JSON, also the process exit code: 0 unless FAILED):
  SUCCESS          published
  PARTIAL_SUCCESS  published, but one or more optional sections failed to build
                   and were omitted (see `sections_omitted`)
  NO_CHANGE        content identical to what is already published -> NO git commit
  DEFERRED         rate limited, or another publish is running -> nothing done
  FAILED           validation or transport failed -> nothing published; the local
                   engine is completely unaffected

DESIGN NOTES (full detail in docs/CLOUD_LIVE_DATA_ARCHITECTURE.md)
  * Publishing is DOWNSTREAM of the engine: it only READS operational state
    (operational/cloud_snapshot_builder.py is read-only) and never raises into a
    caller. A failed publish never rolls back operational work.
  * The live application checkout is NEVER switched to another branch. All git
    work happens in a throwaway clone in a temp directory.
  * Atomic: the snapshot is serialized, re-parsed, validated (schema, finite
    numbers, no secrets/credentials/Yahoo/absolute paths), written to a temp file
    and os.replace()d into place inside the clone; the commit is pushed as one
    ref update. A partially written snapshot can never be published.
  * Idempotent: a stable content hash (excluding publication-only metadata)
    makes an unchanged snapshot NO_CHANGE -- no commit, no push, and if the
    local state file matches, not even a network call.
  * Bounded history: every HISTORY_RESET_EVERY-th publication replaces the
    branch with a fresh orphan commit (force-with-lease), so it keeps roughly the
    last 50 snapshots for rollback and never grows forever.
  * Credentials: whatever git already uses for `origin` (the gh credential
    helper). Nothing is hardcoded and nothing is ever placed in the snapshot.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from operational import cloud_snapshot_schema as schema

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_BRANCH = "cloud-data"
CURRENT_DIR = "current"
STATE_PATH = REPO_ROOT / "operational" / "runtime" / "cloud_publish_state.json"
LOCK_PATH = REPO_ROOT / "operational" / "runtime" / "cloud_publish.lock"
COMPONENT = "cloud_snapshot_publish"

MIN_PUSH_INTERVAL_S = 120
HISTORY_RESET_EVERY = 50
GIT_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
BACKOFF_S = (2.0, 6.0)

SUCCESS, PARTIAL, NO_CHANGE, DEFERRED, FAILED = "SUCCESS", "PARTIAL_SUCCESS", "NO_CHANGE", "DEFERRED", "FAILED"

_README = """# cloud-data

Machine-generated presentation data for the Streamlit Community Cloud app.
Contains ONLY `current/snapshot.json` and `current/metadata.json`. No source code,
no secrets, no Yahoo data. Overwritten by `python3 -m operational.publish_cloud_snapshot`;
history is deliberately bounded (see docs/CLOUD_LIVE_DATA_ARCHITECTURE.md). Do not edit by hand.
"""


# ---- helpers --------------------------------------------------------------------------------------
def publishing_enabled() -> bool:
    """Automatic (scheduler-driven) publishing is OPT-IN: NHL_ENGINE_CLOUD_PUBLISH=ON in the
    environment or .env. A manual `python3 -m operational.publish_cloud_snapshot` always works."""
    value = (os.environ.get("NHL_ENGINE_CLOUD_PUBLISH") or "").strip().upper()
    if not value:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                key, sep, val = line.strip().partition("=")
                if sep and key.strip() == "NHL_ENGINE_CLOUD_PUBLISH":
                    value = val.strip().upper()
    return value in ("ON", "1", "TRUE", "YES")


def known_secret_values() -> tuple[str, ...]:
    """Values of every configured secret, so the validator can prove none leaked into the snapshot."""
    names = ("THE_ODDS_API_KEY", "YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET", "YAHOO_TOKEN_ENCRYPTION_KEY",
             "NHL_ENGINE_ADMIN_SETUP_CODE", "NHL_ENGINE_SNAPSHOT_TOKEN")
    found = {os.environ.get(n, "") for n in names}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() in names:
                found.add(val.strip())
    return tuple(v for v in found if v and len(v) >= 6)


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, STATE_PATH)


def _git(args: list[str], cwd: Path | None = None, *, check: bool = True, timeout: int = GIT_TIMEOUT_S):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result


class GitError(RuntimeError):
    pass


def default_remote() -> str:
    return _git(["remote", "get-url", "origin"], cwd=REPO_ROOT).stdout.strip()


def _serialize(doc: dict) -> str:
    return schema.strict_dumps(doc)


def _validated_text(doc: dict) -> str:
    """Serialize, RE-PARSE and validate the exact bytes that will be published."""
    text = _serialize(doc)
    reparsed = json.loads(text)
    schema.validate_snapshot(reparsed, known_secrets=known_secret_values(), for_publication=True)
    return text


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _metadata_file(doc: dict, size: int) -> dict:
    m = doc["metadata"]
    return {"schema_version": doc["schema_version"], "generated_at": m["generated_at"],
            "data_as_of": m["data_as_of"], "content_hash": m["content_hash"],
            "publication_seq": m["publication_seq"], "bytes": size,
            "sections_omitted": m.get("sections_omitted", [])}


# ---- git transport (isolated clone; the live checkout is never touched) --------------------------------
def _clone_data_branch(remote: str, workdir: Path) -> bool:
    """Shallow-clone the data branch into `workdir`. Returns False if the branch does not exist yet
    (and leaves an initialized empty repo ready for an orphan first commit)."""
    result = _git(["clone", "--quiet", "--depth", "1", "--branch", DATA_BRANCH, "--single-branch", remote,
                   str(workdir)], check=False)
    if result.returncode == 0:
        return True
    if "not found" not in result.stderr.lower() and "remote branch" not in result.stderr.lower():
        raise GitError(f"git clone failed: {result.stderr.strip()[:300]}")
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    _git(["init", "--quiet"], cwd=workdir)
    _git(["remote", "add", "origin", remote], cwd=workdir)
    _git(["checkout", "--quiet", "--orphan", DATA_BRANCH], cwd=workdir)
    return False


def _remote_metadata(workdir: Path) -> dict:
    try:
        return json.loads((workdir / CURRENT_DIR / "metadata.json").read_text())
    except (OSError, ValueError):
        return {}


def _commit_and_push(workdir: Path, doc: dict, text: str, *, branch_existed: bool, seq: int) -> None:
    _atomic_write(workdir / CURRENT_DIR / "snapshot.json", text)
    _atomic_write(workdir / CURRENT_DIR / "metadata.json",
                  json.dumps(_metadata_file(doc, len(text.encode())), indent=1, sort_keys=True) + "\n")
    _atomic_write(workdir / "README.md", _README)
    ident = ["-c", "user.name=nhl-engine-publisher", "-c", "user.email=nhl-engine-publisher@users.noreply.github.com"]
    message = f"cloud-data #{seq} {doc['metadata']['content_hash'][:8]} data_as_of={doc['metadata']['data_as_of']}"
    reset_history = branch_existed and seq % HISTORY_RESET_EVERY == 0
    lease = None
    if reset_history:
        lease = _git(["rev-parse", "HEAD"], cwd=workdir).stdout.strip()
        _git(["checkout", "--quiet", "--orphan", "cloud-data-reset"], cwd=workdir)
    _git(["add", "-A"], cwd=workdir)
    _git([*ident, "commit", "--quiet", "-m", message], cwd=workdir)
    if reset_history:
        _git(["push", "--quiet", f"--force-with-lease={DATA_BRANCH}:{lease}", "origin",
              f"cloud-data-reset:{DATA_BRANCH}"], cwd=workdir)
    else:
        _git(["push", "--quiet", "origin", f"HEAD:{DATA_BRANCH}"], cwd=workdir)


def _publish_via_git(doc: dict, text_builder, remote: str, sleep) -> tuple[str, int]:
    """Returns (status_detail, seq). Retries the whole clone+commit+push a bounded number of times
    (a concurrent update makes the push non-fast-forward; a fresh clone resolves it)."""
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        workdir = Path(tempfile.mkdtemp(prefix="cloud-data-"))
        try:
            existed = _clone_data_branch(remote, workdir / "repo")
            remote_meta = _remote_metadata(workdir / "repo")
            if remote_meta.get("content_hash") == doc["metadata"]["content_hash"]:
                return "REMOTE_ALREADY_CURRENT", int(remote_meta.get("publication_seq") or 0)
            seq = int(remote_meta.get("publication_seq") or 0) + 1
            doc["metadata"]["publication_seq"] = seq
            _commit_and_push(workdir / "repo", doc, text_builder(doc), branch_existed=existed, seq=seq)
            return "PUBLISHED", seq
        except (GitError, subprocess.SubprocessError, OSError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    raise GitError(f"publish failed after {MAX_ATTEMPTS} attempts: {last_error}")


# ---- public entry point --------------------------------------------------------------------------------
def publish(*, remote: str | None = None, force: bool = False, dry_run: bool = False,
            now: dt.datetime | None = None, sleep=time.sleep, doc_and_errors=None, record_health: bool = True) -> dict:
    """Build, validate and (if changed) publish. Never raises: every failure is returned as FAILED."""
    now = now or dt.datetime.now(dt.timezone.utc)
    result: dict = {"status": FAILED, "reason": "", "content_hash": None, "bytes": None,
                    "publication_seq": None, "sections_omitted": [], "attempted_at": now.isoformat()}
    lock_file = None
    try:
        if not dry_run:
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(LOCK_PATH, "w")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                result.update(status=DEFERRED, reason="ANOTHER_PUBLISH_IN_PROGRESS")
                return result

        from operational import cloud_snapshot_builder as builder
        doc, errors = doc_and_errors if doc_and_errors is not None else builder.build_live_snapshot(now)
        result["sections_omitted"] = sorted(errors)
        try:
            doc["metadata"]["content_hash"] = schema.content_hash(doc)
            doc["metadata"]["publication_seq"] = _load_state().get("last_seq", 0) + 1
            result["content_hash"] = doc["metadata"]["content_hash"]
            text = _validated_text(doc)
        except schema.SnapshotInvalid as exc:
            result.update(status=FAILED, reason=f"VALIDATION: {exc}")
            return result
        result["bytes"] = len(text.encode())
        if dry_run:
            result.update(status=SUCCESS, reason="DRY_RUN (validated, not published)")
            return result

        state = _load_state()
        if not force and state.get("last_hash") == result["content_hash"]:
            result.update(status=NO_CHANGE, reason="CONTENT_UNCHANGED", publication_seq=state.get("last_seq"))
            return result
        last_push = schema.parse_utc(state.get("last_push_at"))
        if not force and last_push and (now - last_push).total_seconds() < MIN_PUSH_INTERVAL_S:
            result.update(status=DEFERRED, reason="RATE_LIMITED")
            return result

        remote = remote or default_remote()
        detail, seq = _publish_via_git(doc, lambda d: _validated_text(d), remote, sleep)
        result["publication_seq"] = seq
        state.update(last_hash=result["content_hash"], last_seq=seq)
        if detail == "REMOTE_ALREADY_CURRENT":
            result.update(status=NO_CHANGE, reason="REMOTE_ALREADY_CURRENT")
        else:
            state["last_push_at"] = now.isoformat()
            if errors:
                result.update(status=PARTIAL, reason="SECTIONS_OMITTED: " + ", ".join(sorted(errors)))
            else:
                result.update(status=SUCCESS, reason="PUBLISHED")
        state["last_success_at"] = now.isoformat()
        _save_state(state)
        return result
    except Exception as exc:  # noqa: BLE001 -- publication is downstream and must never raise into the engine
        result.update(status=FAILED, reason=f"{type(exc).__name__}: {schema.sanitize_text(str(exc), str(REPO_ROOT))[:300]}")
        return result
    finally:
        if lock_file is not None:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            finally:
                lock_file.close()
        if record_health and not dry_run:
            _record_health(result)


def _record_health(result: dict) -> None:
    """last_attempt / last_success / status / last_error via the existing ingestion-health cache."""
    try:
        from operational import ingestion_health
        ingestion_health.record_run(COMPONENT, {"status": result["status"], "reason": result["reason"],
                                                "error": result["reason"] if result["status"] == FAILED else None,
                                                "content_hash": result["content_hash"],
                                                "publication_seq": result["publication_seq"]})
    except Exception:  # noqa: BLE001
        pass


def write_output(path: Path, *, now: dt.datetime | None = None) -> dict:
    """Build + validate + atomically write the snapshot to a local file (no git)."""
    from operational import cloud_snapshot_builder as builder
    doc, errors = builder.build_live_snapshot(now)
    doc["metadata"]["content_hash"] = schema.content_hash(doc)
    doc["metadata"]["publication_seq"] = 0
    text = _validated_text(doc)
    _atomic_write(Path(path), text)
    return {"status": PARTIAL if errors else SUCCESS, "bytes": len(text.encode()),
            "content_hash": doc["metadata"]["content_hash"], "sections_omitted": sorted(errors)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--remote", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    from operational import deployment_mode as dm
    if not args.dry_run and not args.output and not dm.require_active_scheduler_or_exit("publish_cloud_snapshot"):
        return 0
    if args.output:
        result = write_output(Path(args.output))
    else:
        result = publish(remote=args.remote, force=args.force, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 1 if result["status"] == FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
