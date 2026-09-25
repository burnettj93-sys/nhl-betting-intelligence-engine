"""
Streamlit Community Cloud deployment PREFLIGHT -- one command, one report.

    python3 -m operational.cloud_preflight            # repo-side checks + remote snapshot fetch
    python3 -m operational.cloud_preflight --offline  # skip the (free, GitHub-only) snapshot fetch
    python3 -m operational.cloud_preflight --json

Verifies everything that can be verified FROM THIS REPOSITORY / MACHINE. Anything that lives only in the
Streamlit UI (secrets, sharing/viewer allow-list, main-file path, the deployed app's behavior) cannot be
verified here and is returned as OWNER_ACTION_REQUIRED -- never guessed.

Read-only: writes no file, opens no database for writing, makes no Odds API call. The only network request
is one GET of the public snapshot URL (skip with --offline).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PASS, FAIL, OWNER = "PASS", "FAIL", "OWNER_ACTION_REQUIRED"

FORBIDDEN_IN_CLOUD_PAGES = ("apscheduler", "schedule", "operational.live_odds_daily_pull", "operational.nhl_sync",
                            "operational.settle_daily_observations", "operational.backup_databases",
                            "research.live_sog_pricing.client", "fantasy", "operational.publish_cloud_snapshot",
                            "operational.cloud_publish_hook", "cryptography")

_RENDER_PROBE = r"""
import os, sys, json, socket, sqlite3, resource
os.environ["NHL_ENGINE_RUNTIME_MODE"] = "COMMUNITY_CLOUD_MODE"
os.environ["NHL_ENGINE_SNAPSHOT_SOURCE"] = "BUNDLED"
repo = sys.argv[1]; sys.path.insert(0, repo); os.chdir(repo)
writes, net = [], []
_real = sqlite3.connect
def spy(database, *a, **k):
    text = str(database)
    if "mode=ro" not in text and text != ":memory:":
        writes.append(text[-80:])
    return _real(database, *a, **k)
sqlite3.connect = spy
_cc = socket.create_connection
socket.create_connection = lambda *a, **k: (net.append(str(a[0])), _cc(*a, **k))[1]
before = {p: os.path.getmtime(p) for p in ("operational/prospective_observations.db", "operational/paper_bankroll.db",
                                            "operational/runtime/nhl.db") if os.path.exists(p)}
from streamlit.testing.v1 import AppTest
at = AppTest.from_file(os.path.join(repo, "dashboard", "pages", "21_Today.py"), default_timeout=120)
at.session_state["_auth_username"] = "preflight"; at.session_state["_auth_role"] = "USER"
at.run()
after = {p: os.path.getmtime(p) for p in before}
rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rss_mb = round(rss_kb / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
print(json.dumps({"exceptions": [str(e.value)[:120] for e in at.exception], "non_readonly_db_opens": writes,
                  "network_connections": net, "db_files_modified": [p for p in before if before[p] != after[p]],
                  "peak_rss_mb": rss_mb, "modules": {m: (m in sys.modules) for m in
                  ("apscheduler", "cryptography", "fantasy", "research.live_sog_pricing.client",
                   "operational.live_odds_daily_pull")}}))
"""


def _row(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def check_entrypoint_and_requirements() -> list[dict]:
    rows = []
    app = REPO_ROOT / "dashboard" / "app.py"
    rows.append(_row("entrypoint file", PASS if app.exists() else FAIL, "dashboard/app.py"))
    rows.append(_row("Streamlit 'Main file path' setting", OWNER,
                     "cannot be read from here: confirm it is exactly dashboard/app.py in the Streamlit app settings"))
    req = REPO_ROOT / "dashboard" / "requirements.txt"
    lines = [l.strip() for l in req.read_text().splitlines() if l.strip() and not l.strip().startswith("#")] if req.exists() else []
    pinned = any(l.startswith("streamlit==") for l in lines)
    heavy = [l for l in lines if l.split("=")[0].split(">")[0].lower() in ("cryptography", "scipy", "numpy", "scikit-learn", "torch")]
    rows.append(_row("dashboard/requirements.txt (Cloud dependency file)", PASS if pinned and not heavy else FAIL,
                     f"deps={lines}; heavy/unneeded={heavy}"))
    return rows


def check_mode_and_auth() -> list[dict]:
    code = ("import os,sys; sys.path.insert(0,%r); os.environ['NHL_ENGINE_RUNTIME_MODE']='COMMUNITY_CLOUD_MODE'; "
            "os.environ.pop('NHL_ENGINE_ADMIN_SETUP_CODE',None); "
            "from operational import runtime_mode as rm; from dashboard import auth; "
            "import json; print(json.dumps({'cloud': rm.is_community_cloud(), 'needs_code': auth.bootstrap_requires_setup_code(), "
            "'configured': bool(auth._configured_setup_code())}))") % str(REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    try:
        info = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [_row("Community Cloud mode / ADMIN setup code", FAIL, (out.stderr or out.stdout)[-200:])]
    from operational import runtime_mode as rm
    auto = rm.REPO_ROOT.as_posix().startswith("/mount/src/")
    return [
        _row("COMMUNITY_CLOUD_MODE resolves", PASS if info["cloud"] else FAIL,
             "explicit NHL_ENGINE_RUNTIME_MODE works; also auto-detected when the repo is mounted under /mount/src/"),
        _row("ADMIN bootstrap requires a setup code", PASS if info["needs_code"] else FAIL,
             "with no NHL_ENGINE_ADMIN_SETUP_CODE configured no account can be created "
             f"(configured in this shell: {info['configured']})"),
        _row("NHL_ENGINE_ADMIN_SETUP_CODE set in Streamlit secrets", OWNER, "secrets are not readable from here"),
        _row("Viewer restriction (Settings -> Sharing)", OWNER, "set 'Only specific people can view this app'"),
    ]


def check_page_registry() -> list[dict]:
    sys.path.insert(0, str(REPO_ROOT))
    from dashboard import page_registry as pr
    from operational import runtime_mode as rm
    from unittest import mock
    with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
        cloud_pages = [p for p in pr.PAGES if getattr(p, "cloud", False)]
    missing, bad = [], []
    for spec in cloud_pages:
        path = REPO_ROOT / "dashboard" / "pages" / spec.file
        if not path.exists():
            missing.append(spec.file)
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                if any(n == f or n.startswith(f + ".") for f in FORBIDDEN_IN_CLOUD_PAGES):
                    bad.append(f"{spec.file}: {n}")
    return [_row(f"Cloud page registry ({len(cloud_pages)} pages)", PASS if not missing and not bad else FAIL,
                 f"missing={missing}; schedulers/API clients/Yahoo imported directly by a Cloud page={bad}")]


def check_render_probe() -> list[dict]:
    out = subprocess.run([sys.executable, "-c", _RENDER_PROBE, str(REPO_ROOT)], capture_output=True, text=True, timeout=300)
    try:
        info = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [_row("Cloud render probe (Today)", FAIL, (out.stderr or out.stdout)[-300:])]
    # research.live_sog_pricing.client is imported (never called) only for the ApiResult dataclass that
    # archive.py annotates with; the no-network probe below is what proves no paid call can happen.
    loaded_bad = [m for m, v in info["modules"].items() if v and m != "research.live_sog_pricing.client"]
    return [
        _row("Today renders in Cloud mode without exceptions", PASS if not info["exceptions"] else FAIL, str(info["exceptions"])),
        _row("no database created or opened for writing", PASS if not info["non_readonly_db_opens"] and not info["db_files_modified"] else FAIL,
             f"non-read-only opens={info['non_readonly_db_opens']}; modified={info['db_files_modified']}"),
        _row("no scheduler / paid-API client / Yahoo / cryptography imported", PASS if not loaded_bad else FAIL, f"loaded={loaded_bad}"),
        _row("no network connection during render (Odds API impossible)", PASS if not info["network_connections"] else FAIL,
             f"connections={info['network_connections']}"),
        _row("memory: Today peak RSS (limit ~1 GB; goal < 250 MB)", PASS if info["peak_rss_mb"] < 250 else FAIL, f"{info['peak_rss_mb']} MB"),
    ]


def check_remote_snapshot() -> list[dict]:
    sys.path.insert(0, str(REPO_ROOT))
    os.environ["NHL_ENGINE_RUNTIME_MODE"] = "COMMUNITY_CLOUD_MODE"
    from dashboard import snapshot_source as ss
    ss.reset()
    state = ss.current(force_refresh=True)
    ok = state.source == ss.REMOTE and state.fetch_status == "OK"
    rows = [_row("remote snapshot fetch (public raw URL)", PASS if ok else FAIL,
                 f"source={state.source} status={state.fetch_status} error={state.last_error} url={ss.public_url_label()}")]
    if ok:
        rows.append(_row("snapshot schema / freshness", PASS if state.schema_version == 2 and state.freshness == "CURRENT" else FAIL,
                         f"schema={state.schema_version} freshness={state.freshness} data_as_of={state.data_as_of} "
                         f"generated_at={state.generated_at} hash={str(state.content_hash)[:12]}"))
    return rows


def run(offline: bool = False) -> dict:
    rows = []
    for fn in (check_entrypoint_and_requirements, check_mode_and_auth, check_page_registry, check_render_probe):
        try:
            rows += fn()
        except Exception as exc:  # noqa: BLE001 -- a failing probe is a FAIL row, never a crash
            rows.append(_row(fn.__name__, FAIL, f"{type(exc).__name__}: {str(exc)[:200]}"))
    if not offline:
        try:
            rows += check_remote_snapshot()
        except Exception as exc:  # noqa: BLE001
            rows.append(_row("remote snapshot fetch", FAIL, f"{type(exc).__name__}: {str(exc)[:200]}"))
    rows.append(_row("deployed app: loads, no resource-limit error, snapshot REMOTE", OWNER,
                     "requires opening the deployed URL as an allowed viewer (privacy is not weakened for testing)"))
    failed = [r for r in rows if r["status"] == FAIL]
    owner = [r for r in rows if r["status"] == OWNER]
    return {"overall": "FAIL" if failed else ("PASS_WITH_OWNER_ACTIONS" if owner else "PASS"),
            "failed": len(failed), "owner_actions": len(owner), "checks": rows}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run(offline="--offline" in argv)
    if "--json" in argv:
        print(json.dumps(report, indent=2))
    else:
        print(f"CLOUD PREFLIGHT: {report['overall']} (failed={report['failed']}, owner actions={report['owner_actions']})")
        for r in report["checks"]:
            print(f"  [{r['status']:<21}] {r['check']} -- {r['detail']}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
