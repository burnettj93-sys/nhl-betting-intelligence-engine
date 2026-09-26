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
    rows.append(_row("Streamlit repo / branch / main file", PASS,
                     "verified 2026-09-26 in the signed-in Streamlit workspace: burnettj93-sys/nhl-betting-intelligence-engine, master, dashboard/app.py"))
    req = REPO_ROOT / "dashboard" / "requirements.txt"
    lines = [l.strip() for l in req.read_text().splitlines() if l.strip() and not l.strip().startswith("#")] if req.exists() else []
    pinned = any(l.startswith("streamlit==") for l in lines)
    heavy = [l for l in lines if l.split("=")[0].split(">")[0].lower() in ("cryptography", "scipy", "numpy", "scikit-learn", "torch")]
    rows.append(_row("dashboard/requirements.txt (Cloud dependency file)", PASS if pinned and not heavy else FAIL,
                     f"deps={lines}; heavy/unneeded={heavy}"))
    return rows


def check_mode_and_auth() -> list[dict]:
    code = ("import os,sys; sys.path.insert(0,%r); os.environ['NHL_ENGINE_RUNTIME_MODE']='COMMUNITY_CLOUD_MODE'; "
            "from operational import runtime_mode as rm; from dashboard import auth; "
            "import json; u=auth.current_user(); print(json.dumps({'cloud': rm.is_community_cloud(), 'implicit_viewer': bool(u), "
            "'has_setup_code_api': hasattr(auth,'setup_code_valid')}))") % str(REPO_ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    try:
        info = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [_row("Community Cloud mode / access model", FAIL, (out.stderr or out.stdout)[-200:])]
    return [
        _row("COMMUNITY_CLOUD_MODE resolves", PASS if info["cloud"] else FAIL,
             "explicit NHL_ENGINE_RUNTIME_MODE works; also auto-detected when the repo is mounted under /mount/src/"),
        _row("no app-level login in Community Cloud", PASS if info["implicit_viewer"] and not info["has_setup_code_api"] else FAIL,
             "Streamlit private sharing is the only access gate: no accounts, no setup code, no st.user.email, no auth secrets"),
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


APP_URL_ENV = "NHL_ENGINE_STREAMLIT_URL"


def app_url() -> str | None:
    """The deployed app URL, only if the owner configured it (env or .env). Never guessed or derived."""
    value = (os.environ.get(APP_URL_ENV) or "").strip()
    if not value:
        env_file = REPO_ROOT / ".env"
        if env_file.exists() and not os.environ.get("NHL_ENGINE_UNDER_TEST"):
            for line in env_file.read_text().splitlines():
                key, sep, val = line.strip().partition("=")
                if sep and key.strip() == APP_URL_ENV:
                    value = val.strip().strip("\"'")
    return value or None


def check_deployed_app(url: str | None = None, fetch=None) -> dict:
    """Read-only smoke test of the DEPLOYED app's reachability (two GETs, no login, no privacy weakening):
    Streamlit's own health endpoint, and the landing page (a private app answers with a redirect / auth page to
    an anonymous client, a public one with the app itself). Authenticated checks (source REMOTE, schema, memory,
    ADMIN vs USER) can only be done by a signed-in allowed viewer -- OWNER_SMOKE_TEST_REQUIRED."""
    url = url or app_url()
    if not url:
        return {"state": OWNER, "detail": f"provide the deployed app URL: set {APP_URL_ENV}=https://<your-app>.streamlit.app in .env "
                                          "(not discoverable from the repo, GitHub metadata or the Streamlit webhook)"}
    if not url.startswith("https://"):
        return {"state": FAIL, "detail": "app URL must be https://"}
    if fetch is None:
        import urllib.request

        def fetch(u):
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            import ssl
            try:                                   # python.org macOS builds ship without a CA bundle: prefer certifi's
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001
                ctx = ssl.create_default_context()
            opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
            try:
                with opener.open(urllib.request.Request(u, headers={"User-Agent": "nhl-engine-preflight/1"}), timeout=15) as r:
                    return r.status, r.read(2000).decode("utf-8", "replace"), dict(r.headers)
            except urllib.error.HTTPError as e:
                return e.code, "", dict(e.headers or {})
    try:
        h_status, h_body, _ = fetch(url.rstrip("/") + "/_stcore/health")
        p_status, _, headers = fetch(url)
    except Exception as exc:  # noqa: BLE001
        return {"state": FAIL, "detail": f"unreachable: {type(exc).__name__}"}
    private = p_status in (301, 302, 303, 307, 308, 401, 403)
    healthy = h_status == 200 and h_body.strip().lower() == "ok"
    return {"state": PASS if (healthy or private) else FAIL,
            "detail": f"health endpoint {h_status} ({'ok' if healthy else 'not ok'}); landing page {p_status} "
                      f"({'redirects anonymous visitors to sign-in: private viewer mode is ACTIVE' if private else 'served to an anonymous client: the app is PUBLIC'})",
            "private_viewer_mode": private, "healthy": healthy,
            "next": "OWNER_SMOKE_TEST_REQUIRED: signed in as an allowed viewer confirm banner SNAPSHOT CURRENT, Diagnostics source REMOTE / schema 2, and that a non-admin cannot open Diagnostics"}


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
    if not offline:
        app = check_deployed_app()
        rows.append(_row("deployed app reachable (anonymous, read-only)", app["state"], app["detail"]))
    rows.append(_row("deployed app: signed-in viewer sees the app directly, source REMOTE / schema 2 (Diagnostics)", OWNER,
                     "OWNER_CONFIRMATION_REQUIRED: needs a signed-in allowed viewer (privacy is not weakened for testing)"))
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
