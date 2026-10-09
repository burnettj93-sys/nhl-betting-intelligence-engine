"""
Persistent self-check (launchd, every 30 minutes; deploy/launchd/com.nhlengine.watchdog.plist): is the unattended engine actually running the
code it should, on the right data, and is the hosted app still being fed?

Checks (each OK / WARN / FAIL, with the evidence):
  jobs_loaded         every scheduled job the product depends on is loaded in launchd, and none last exited with an error;
  release_pinned      the release checkout the jobs run from is clean and equals origin/master (WARN while a deploy is pending);
  trader_recent       the 15-minute trader recorded a success in the last 45 minutes;
  publish_recent      the hosted snapshot was published in the last 60 minutes (the same limit Data Status uses);
  database_path       the resolved NHL database is the live one (exists, non-empty, has games) and the ledger file exists.

It writes operational/runtime/watchdog_state.json (read by Diagnostics and published in the snapshot's `health` section, timestamps only) and
sends one macOS notification when something turns FAIL. It never changes anything, makes no network call except `git ls-remote`, spends no credits.

Run: python3 -m operational.watchdog
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from operational import state_paths

STATE_NAME = "watchdog_state.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = Path.home() / "nhl_engine_release"
TRADER_MAX_AGE_MIN = 45.0
PUBLISH_MAX_AGE_MIN = 60.0

EXPECTED_JOBS = (
    "com.nhlengine.real-parlay-paper-trader", "com.nhlengine.manual-order-job", "com.nhlengine.moneyline-pregame", "com.nhlengine.moneyline-snapshot",
    "com.nhlengine.prop-sweep-first", "com.nhlengine.prop-sweep-second", "com.nhlengine.daily-props-pull", "com.nhlengine.daily-nhl-sync",
    "com.nhlengine.midday-schedule-refresh", "com.nhlengine.pregame-targeted-refresh", "com.nhlengine.daily-settlement", "com.nhlengine.daily-postmortem",
    "com.nhlengine.database-backup",
)
OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or f"exit {out.returncode}")
    return out.stdout


def _age_min(stamp: str | None, now: dt.datetime) -> float | None:
    if not stamp:
        return None
    t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return (now - t).total_seconds() / 60.0


def check_jobs(launchctl_text: str) -> dict:
    rows = {}
    for line in launchctl_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.nhlengine."):
            rows[parts[2]] = parts[1]
    missing = [j for j in EXPECTED_JOBS if j not in rows]
    failing = [j for j in EXPECTED_JOBS if j in rows and rows[j] not in ("0", "-")]
    if missing:
        return {"name": "jobs_loaded", "status": FAIL, "detail": f"not loaded: {', '.join(m.split('.')[-1] for m in missing)}"}
    if failing:
        return {"name": "jobs_loaded", "status": WARN, "detail": f"last exit non-zero: {', '.join(f'{j.split(chr(46))[-1]}={rows[j]}' for j in failing)}"}
    return {"name": "jobs_loaded", "status": OK, "detail": f"all {len(EXPECTED_JOBS)} jobs loaded, last exits clean"}


def check_release(release_head: str | None, dirty: bool, origin_master: str | None) -> dict:
    if release_head is None:
        return {"name": "release_pinned", "status": FAIL, "detail": "the release checkout is missing or unreadable"}
    if dirty:
        return {"name": "release_pinned", "status": FAIL, "detail": f"the release checkout at {release_head[:10]} has local changes (jobs may run unreviewed code)"}
    if origin_master is None:
        return {"name": "release_pinned", "status": WARN, "detail": f"release at {release_head[:10]}; origin/master could not be read"}
    if release_head != origin_master:
        return {"name": "release_pinned", "status": WARN, "detail": f"release {release_head[:10]} differs from origin/master {origin_master[:10]} (a deploy is pending, or master moved)"}
    return {"name": "release_pinned", "status": OK, "detail": f"release equals origin/master at {release_head[:10]}"}


def check_age(name: str, stamp: str | None, limit_min: float, now: dt.datetime, what: str) -> dict:
    age = _age_min(stamp, now)
    if age is None:
        return {"name": name, "status": FAIL, "detail": f"{what}: no success on record"}
    if age > limit_min:
        return {"name": name, "status": FAIL, "detail": f"{what}: last success {age:.0f} min ago (limit {limit_min:.0f})"}
    return {"name": name, "status": OK, "detail": f"{what}: last success {age:.0f} min ago (limit {limit_min:.0f})"}


def check_database() -> dict:
    try:
        import db
        from operational import paper_bankroll as pb
        path = Path(db.resolve_db_path())
        if not path.exists() or path.stat().st_size == 0:
            return {"name": "database_path", "status": FAIL, "detail": "the resolved NHL database is missing or empty"}
        import sqlite3
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            games = c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        finally:
            c.close()
        if not Path(pb.DB_PATH).exists():
            return {"name": "database_path", "status": FAIL, "detail": "the paper ledger file is missing"}
        return {"name": "database_path", "status": OK, "detail": f"{path.name} has {games} games; the paper ledger exists"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "database_path", "status": FAIL, "detail": f"{type(exc).__name__}: {exc}"[:200]}


def run(now: dt.datetime | None = None, *, runner=_run, notify=True) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    checks = []
    try:
        checks.append(check_jobs(runner(["launchctl", "list"])))
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "jobs_loaded", "status": FAIL, "detail": f"launchctl failed: {exc}"})
    try:
        head = runner(["git", "rev-parse", "HEAD"], RELEASE_DIR).strip()
        dirty = bool(runner(["git", "status", "--porcelain", "--untracked-files=no"], RELEASE_DIR).strip())
    except Exception:  # noqa: BLE001
        head, dirty = None, False
    try:
        origin = runner(["git", "ls-remote", "origin", "refs/heads/master"], REPO_ROOT).split()[0]
    except Exception:  # noqa: BLE001
        origin = None
    checks.append(check_release(head, dirty, origin))
    try:
        from operational import ingestion_health
        health = ingestion_health.load_health()
    except Exception:  # noqa: BLE001
        health = {}
    checks.append(check_age("trader_recent", (health.get("real_parlay_paper_trader") or {}).get("last_success_utc"), TRADER_MAX_AGE_MIN, now, "the 15-minute trader"))
    checks.append(check_age("publish_recent", (health.get("cloud_snapshot_publish") or {}).get("last_success_utc"), PUBLISH_MAX_AGE_MIN, now, "the hosted snapshot publication"))
    checks.append(check_database())
    worst = FAIL if any(c["status"] == FAIL for c in checks) else WARN if any(c["status"] == WARN for c in checks) else OK
    prior = load_state()
    state = {"checked_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": worst, "checks": checks,
             "interval_min": 30, "release_commit": head, "origin_master_commit": origin}
    p = state_paths.path(STATE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1))
    tmp.replace(p)
    if notify and worst == FAIL and (prior or {}).get("status") != FAIL:
        failing = ", ".join(c["name"] for c in checks if c["status"] == FAIL)
        try:
            from operational import notify as _n
            _n.send("NHL engine watchdog", f"Failing: {failing}")
        except Exception:  # noqa: BLE001
            pass
    return state


def load_state() -> dict | None:
    p = state_paths.path(STATE_NAME)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def view(state: dict | None, now: dt.datetime | None = None, max_age_min: float = 75.0) -> dict:
    """What a page shows: the stored result, re-aged at view time. A watchdog that has itself stopped (no check in 75 minutes) is a FAIL of its own."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if not state:
        return {"status": "UNKNOWN", "message": "The watchdog has not run yet.", "checks": [], "age_min": None}
    age = _age_min(state["checked_at_utc"], now)
    if age is not None and age > max_age_min:
        return {"status": FAIL, "message": f"The watchdog itself has not run for {age:.0f} minutes (it should run every 30).", "checks": state["checks"], "age_min": age}
    return {"status": state["status"], "message": "", "checks": state["checks"], "age_min": age}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
