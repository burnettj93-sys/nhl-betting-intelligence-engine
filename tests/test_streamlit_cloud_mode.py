"""
Community Cloud memory sprint (2026-09-25): tests for COMMUNITY_CLOUD_MODE --
mode resolution, the page registry/classification, the compact snapshot,
lazy heavy imports, "no writes / no network / no schedulers on render",
bounded caches and queries, ADMIN-only diagnostics, Yahoo isolation, and
freshness honesty.

Render-safety checks run in a SUBPROCESS so sys.modules / sqlite / network
state is genuinely clean (an in-process test would inherit everything other
test modules already imported).
"""
from __future__ import annotations

import ast
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard import cloud_snapshot, page_registry
from operational import runtime_mode as rm

REPO = Path(__file__).resolve().parent.parent

# Titles that existed in the nav BEFORE this sprint (36 pages) -- LOCAL_MODE
# must keep registering every one of them.
ORIGINAL_36 = {
    "Today", "Live SOG Markets", "Player Props", "Goalies", "Combinations", "Games", "Game Detail",
    "Market Movement", "Players", "Team Intelligence", "Player Intelligence", "Model Health",
    "Model Learning", "Paper Performance", "Morning Review", "Ledger", "Data Status",
    "Play-by-Play Status", "Team Ratings", "Research Hub", "Prop Registry", "Player SOG Research",
    "Player SOG by Period", "Player Goals Research", "Player Points Research", "Team SOG Research",
    "Goalie Saves Research", "Goalie Intelligence", "Joint Shot/Workload Research",
    "Joint Scoring Dependence", "Team Goals by Period", "Player Context State", "Model Performance",
    "Research Lab", "Fantasy HQ", "Fantasy Settings",
}

# Things that must NEVER be constructed/imported by the Community Cloud path.
FORBIDDEN_MODULES = (
    "research.context_overlay.prediction_stack",   # the ~430 MB model stack
    "research.player_sog.features",                # corpus loaders
    "research.player_sog.live_projection",
    "research.player_goals.features",
    "research.player_points.features",
    "research.player_assists.features",
    "research.player_blocks.features",
    "dashboard.goalie_saves_view",
    "dashboard.player_sog_period_view",
    "fantasy.yahoo.oauth",
    "fantasy.yahoo.client",
    "fantasy.yahoo.crypto",                       # pulls in `cryptography`, absent from the Cloud manifest
    "cryptography",
)


class TestModeResolution(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(rm.ENV_VAR, None)
        self._dotenv = mock.patch.object(rm, "_from_dotenv", return_value=None)
        self._secrets = mock.patch.object(rm, "_from_streamlit_secrets", return_value=None)
        self._dotenv.start(); self._secrets.start()

    def tearDown(self):
        self._secrets.stop(); self._dotenv.stop(); self._env.stop()

    def test_defaults_to_local_mode(self):
        self.assertEqual(rm.current_mode(), rm.LOCAL_MODE)

    def test_env_var_selects_each_valid_mode_case_insensitively(self):
        for value in ("community_cloud_mode", "PRODUCTION_MODE", "local_mode"):
            os.environ[rm.ENV_VAR] = value
            self.assertEqual(rm.current_mode(), value.upper())

    def test_unrecognized_value_fails_toward_the_full_app_not_the_thin_one(self):
        os.environ[rm.ENV_VAR] = "cloud"
        self.assertEqual(rm.current_mode(), rm.LOCAL_MODE)

    def test_dotenv_and_secrets_are_honored_in_order(self):
        with mock.patch.object(rm, "_from_dotenv", return_value="PRODUCTION_MODE"):
            self.assertEqual(rm.current_mode(), rm.PRODUCTION_MODE)
        with mock.patch.object(rm, "_from_streamlit_secrets", return_value="COMMUNITY_CLOUD_MODE"):
            self.assertEqual(rm.current_mode(), rm.COMMUNITY_CLOUD_MODE)

    def test_autodetects_community_cloud_mount_path_unless_overridden(self):
        with mock.patch.object(rm, "REPO_ROOT", Path("/mount/src/nhl-betting-intelligence-engine")):
            self.assertEqual(rm.current_mode(), rm.COMMUNITY_CLOUD_MODE)
            os.environ[rm.ENV_VAR] = "LOCAL_MODE"
            self.assertEqual(rm.current_mode(), rm.LOCAL_MODE)

    def test_guard_raises_only_in_community_cloud(self):
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            rm.require_not_community_cloud("x")
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            with self.assertRaises(rm.HeavyFeatureUnavailable):
                rm.require_not_community_cloud("x")

    def test_deployment_mode_axis_is_independent(self):
        """ACTIVE/STANDBY (which machine runs schedulers) neither reads nor
        sets the UI runtime mode."""
        from operational import deployment_mode as dm
        with mock.patch.dict(os.environ, {"NHL_ENGINE_DEPLOYMENT_MODE": "STANDBY"}):
            self.assertEqual(rm.current_mode(), rm.LOCAL_MODE)
            self.assertEqual(dm.current_mode(), dm.STANDBY)


class TestPageRegistry(unittest.TestCase):
    def test_every_page_file_is_registered_exactly_once_and_vice_versa(self):
        on_disk = {Path(p).name for p in glob.glob(str(REPO / "dashboard" / "pages" / "*.py"))}
        registered = [p.file for p in page_registry.PAGES]
        self.assertEqual(len(registered), len(set(registered)), "duplicate registry entry")
        self.assertEqual(on_disk, set(registered))

    def test_every_page_has_a_valid_classification(self):
        valid = {page_registry.CORE_USER, page_registry.LIGHTWEIGHT_ADMIN, page_registry.DEMO,
                 page_registry.HEAVY_RESEARCH, page_registry.LEGACY}
        for p in page_registry.PAGES:
            self.assertIn(p.klass, valid, p.file)

    def test_exactly_one_default_page_and_it_is_today(self):
        defaults = [p for p in page_registry.PAGES if p.default]
        self.assertEqual([p.title for p in defaults], ["Today"])

    def test_local_and_production_modes_register_every_original_page_for_admin(self):
        for mode in (rm.LOCAL_MODE, rm.PRODUCTION_MODE):
            titles = {p.title for specs in page_registry.pages_for("ADMIN", mode).values() for p in specs}
            self.assertTrue(ORIGINAL_36 <= titles, f"{mode} lost pages: {ORIGINAL_36 - titles}")
            self.assertIn("Diagnostics", titles)

    def test_local_mode_registers_no_more_than_before_for_a_user_plus_nothing_admin_only(self):
        user_titles = {p.title for specs in page_registry.pages_for("USER", rm.LOCAL_MODE).values() for p in specs}
        self.assertEqual(user_titles, ORIGINAL_36 - {"Fantasy HQ", "Fantasy Settings"})

    def test_heavy_and_legacy_pages_are_not_registered_in_community_cloud(self):
        for role in ("ADMIN", "USER"):
            for specs in page_registry.pages_for(role, rm.COMMUNITY_CLOUD_MODE).values():
                for p in specs:
                    self.assertNotIn(p.klass, (page_registry.HEAVY_RESEARCH, page_registry.LEGACY), p.file)

    def test_every_core_user_page_stays_available_in_community_cloud(self):
        cloud_user = {p.title for specs in page_registry.pages_for("USER", rm.COMMUNITY_CLOUD_MODE).values()
                      for p in specs}
        for title in ("Today", "Games", "Game Detail", "Player Props", "Goalies", "Combinations",
                      "Market Movement", "Team Intelligence", "Player Intelligence", "Players",
                      "Paper Performance", "Ledger", "Model Health", "Model Learning"):
            self.assertIn(title, cloud_user)

    def test_admin_lightweight_pages_survive_in_community_cloud(self):
        admin = {p.title for specs in page_registry.pages_for("ADMIN", rm.COMMUNITY_CLOUD_MODE).values()
                 for p in specs}
        for title in ("Morning Review", "Data Status", "Diagnostics"):
            self.assertIn(title, admin)

    def test_yahoo_pages_are_admin_only_and_absent_from_community_cloud(self):
        for name in ("34_Fantasy_HQ.py", "35_Fantasy_Settings.py"):
            spec = page_registry.spec_for_file(name)
            self.assertTrue(spec.admin_only)
            self.assertFalse(page_registry.is_registered(name, "USER", rm.LOCAL_MODE))
            self.assertFalse(page_registry.is_registered(name, "ADMIN", rm.COMMUNITY_CLOUD_MODE))
            self.assertTrue(page_registry.is_registered(name, "ADMIN", rm.LOCAL_MODE))

    def test_diagnostics_is_admin_only_in_every_mode(self):
        for mode in rm.VALID_MODES:
            self.assertFalse(page_registry.is_registered("37_Diagnostics.py", "USER", mode))
            self.assertTrue(page_registry.is_registered("37_Diagnostics.py", "ADMIN", mode))

    def test_no_cloud_registered_page_links_to_a_page_that_is_not_registered_there(self):
        """A dangling st.switch_page target would crash the page in Cloud."""
        cloud_files = {p.file for p in page_registry.PAGES if p.cloud}
        sources = glob.glob(str(REPO / "dashboard" / "pages" / "*.py")) + [str(REPO / "dashboard" / "components.py")]
        for src in sources:
            name = Path(src).name
            if "/pages/" in src and name not in cloud_files:
                continue
            for m in re.finditer(r'(?:switch_page|page_link)\(\s*["\']([^"\']+)["\']', Path(src).read_text()):
                self.assertIn(Path(m.group(1)).name, cloud_files, f"{name} links to {m.group(1)}")


class TestSnapshot(unittest.TestCase):
    def test_shipped_snapshot_equals_a_fresh_live_computation(self):
        """The guard against silent staleness (also `python3 -m dashboard.cloud_snapshot --check`)."""
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            self.assertEqual(cloud_snapshot.verify_against_live(), [])

    def test_snapshot_is_compact_and_carries_provenance(self):
        size = cloud_snapshot.SNAPSHOT_PATH.stat().st_size
        self.assertLess(size, 2 * 1024 * 1024, "snapshot should stay a compact artifact")
        meta = cloud_snapshot.snapshot_meta()
        for key in ("generated_at_utc", "simulated_slate_date", "newest_real_dk_capture_utc",
                    "elo_corpus_last_game_date"):
            self.assertIn(key, meta)
        self.assertTrue(meta["available"])

    def test_accessors_hand_out_copies_so_a_session_cannot_corrupt_the_shared_snapshot(self):
        a = cloud_snapshot.demo_opportunities()
        a[0]["decision"] = "TAMPERED"
        a.clear()
        b = cloud_snapshot.demo_opportunities()
        self.assertTrue(b)
        self.assertNotEqual(b[0]["decision"], "TAMPERED")

    def test_missing_snapshot_fails_loudly_never_silently_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cloud_snapshot, "SNAPSHOT_PATH", Path(tmp) / "nope.json"):
                cloud_snapshot.reset_cache()
                try:
                    with self.assertRaises(cloud_snapshot.SnapshotUnavailable):
                        cloud_snapshot.demo_opportunities()
                    self.assertFalse(cloud_snapshot.snapshot_meta()["available"])
                finally:
                    cloud_snapshot.reset_cache()

    def test_builder_refuses_to_run_in_community_cloud_mode(self):
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            with self.assertRaises(rm.HeavyFeatureUnavailable):
                cloud_snapshot.build_snapshot()

    def test_demo_builders_read_the_snapshot_and_never_build_the_stack_in_cloud_mode(self):
        from dashboard import demo_data as dd
        from dashboard import eligible_bets as eb
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            self.assertEqual(len(dd.build_demo_games()), 6)
            self.assertTrue(dd.build_demo_roster())
            self.assertTrue(eb.all_opportunities())
            with self.assertRaises(rm.HeavyFeatureUnavailable):
                dd._demo_context()

    def test_cloud_and_local_demo_board_are_identical(self):
        from dashboard import eligible_bets as eb
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            live = json.loads(json.dumps(eb.all_opportunities()))
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            snap = eb.all_opportunities()
        self.assertEqual(live, json.loads(json.dumps(snap)))


# Runs in a clean subprocess: renders EVERY Community Cloud page as ADMIN with
# sqlite writes, network, subprocesses recorded, then reports what happened.
_RENDER_PROBE = r'''
import os, sys, json, sqlite3, tempfile, pathlib, subprocess, hashlib
REPO = sys.argv[1]
os.environ["NHL_ENGINE_RUNTIME_MODE"] = "COMMUNITY_CLOUD_MODE"
os.chdir(REPO); sys.path.insert(0, REPO)

WRITE = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "VACUUM")
writes, net, procs = [], [], []
_real_connect = sqlite3.connect
def spy(database, *a, **k):
    conn = _real_connect(database, *a, **k)
    name = str(database)
    if name != ":memory:" and "probe_auth" not in name:
        def cb(stmt, n=os.path.basename(name)):
            if stmt.strip().upper().startswith(WRITE):
                writes.append(f"{n}: {stmt.strip()[:60]}")
        conn.set_trace_callback(cb)
    return conn
sqlite3.connect = spy

import requests
def _blocked(*a, **k):
    net.append("requests"); raise RuntimeError("network blocked")
requests.Session.request = _blocked
import urllib.request
urllib.request.urlopen = lambda *a, **k: (net.append("urlopen"), (_ for _ in ()).throw(RuntimeError("blocked")))[1]
_real_popen = subprocess.Popen
def _popen(*a, **k):
    cmd = a[0] if a else k.get("args")
    if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["ps", "-o", "rss="]:
        return _real_popen(*a, **k)   # read-only RSS of our own pid (Diagnostics page on macOS; Linux uses /proc)
    procs.append(str(a[:1])[:80]); raise RuntimeError("subprocess blocked")
subprocess.Popen = _popen

def digest(p):
    p = pathlib.Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
from db import resolve_db_path
critical = {"nhl": resolve_db_path(), "ledger": f"{REPO}/operational/prospective_observations.db",
            "bankroll": f"{REPO}/operational/paper_bankroll.db"}
before = {k: digest(v) for k, v in critical.items()}
snapshot_dir_before = sorted(os.listdir(f"{REPO}/operational"))

from operational import auth_store
auth_store.DEFAULT_DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "probe_auth.db"
from streamlit.testing.v1 import AppTest
from dashboard import page_registry

state_by_page = {"2_Game_Detail.py": {"selected_game_id": "demo-EDM-COL"},
                 "25_Player_Intelligence.py": {"selected_player_id": "8478402"},
                 "31_Team_Intelligence.py": {"selected_team": "EDM"}}
exceptions, sessions = {}, {}
files = [p.file for specs in page_registry.pages_for("ADMIN").values() for p in specs]
import pickle
for f in files:
    at = AppTest.from_file(f"{REPO}/dashboard/pages/{f}", default_timeout=120)
    at.session_state["_auth_username"] = "probe"; at.session_state["_auth_role"] = "ADMIN"
    for k, v in state_by_page.get(f, {}).items():
        at.session_state[k] = v
    at.run()
    if at.exception:
        exceptions[f] = [e.value[:120] for e in at.exception]
    sessions[f] = len(pickle.dumps({k: v for k, v in at.session_state.filtered_state.items()}))
    if f == "21_Today.py":
        today_text = " ".join(m.value for m in at.markdown)

print("RESULT " + json.dumps({
    "files": files, "exceptions": exceptions, "writes": writes, "net": net, "procs": procs,
    "forbidden_present": [m for m in json.loads(sys.argv[2]) if m in sys.modules],
    "critical_unchanged": {k: before[k] == digest(v) for k, v in critical.items()},
    "operational_dir_unchanged": snapshot_dir_before == sorted(os.listdir(f"{REPO}/operational")),
    "max_session_state_bytes": max(sessions.values()),
    "today_banner": "COMMUNITY CLOUD SNAPSHOT" in today_text,
}))
'''


class TestCommunityCloudRenderSafety(unittest.TestCase):
    """One clean subprocess renders every Cloud-registered page."""

    @classmethod
    def setUpClass(cls):
        env = {k: v for k, v in os.environ.items() if k != rm.ENV_VAR}
        proc = subprocess.run(
            [sys.executable, "-c", _RENDER_PROBE, str(REPO), json.dumps(list(FORBIDDEN_MODULES))],
            capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO))
        line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
        if line is None:
            raise AssertionError(f"render probe failed:\nSTDOUT:{proc.stdout[-1500:]}\nSTDERR:{proc.stderr[-2500:]}")
        cls.result = json.loads(line[len("RESULT "):])

    def test_every_cloud_page_renders_without_an_exception(self):
        self.assertEqual(self.result["exceptions"], {})
        self.assertGreaterEqual(len(self.result["files"]), 16)

    def test_no_writes_to_any_database_during_rendering(self):
        self.assertEqual(self.result["writes"], [])
        self.assertTrue(all(self.result["critical_unchanged"].values()), self.result["critical_unchanged"])

    def test_no_network_or_paid_api_calls_on_render(self):
        self.assertEqual(self.result["net"], [])

    def test_no_scheduler_or_subprocess_is_started_by_a_page_render(self):
        self.assertEqual(self.result["procs"], [])

    def test_heavy_research_modules_and_the_model_stack_are_never_imported(self):
        self.assertEqual(self.result["forbidden_present"], [])

    def test_no_new_files_appear_in_operational_during_rendering(self):
        self.assertTrue(self.result["operational_dir_unchanged"])

    def test_session_state_stays_tiny(self):
        self.assertLess(self.result["max_session_state_bytes"], 20_000)

    def test_freshness_banner_is_shown_on_today(self):
        self.assertTrue(self.result["today_banner"])


class TestFreshnessHonesty(unittest.TestCase):
    def test_banner_says_snapshot_is_not_live_and_names_the_frozen_inputs(self):
        from dashboard import components as comp
        lines = " ".join(comp.cloud_freshness_lines(cloud_snapshot.snapshot_meta()))
        self.assertIn("frozen", lines.lower())
        self.assertIn("cannot receive live operational state", lines)
        self.assertIn("Newest real DraftKings moneyline capture", lines)

    def test_banner_reports_an_unavailable_snapshot_honestly(self):
        from dashboard import components as comp
        text = comp.cloud_freshness_lines({"available": False, "error": "board.json is missing"})[0]
        self.assertIn("unavailable", text)

    def test_frozen_nhl_db_is_never_reported_as_current_state_in_cloud_mode(self):
        from operational import system_health as sh
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), \
             mock.patch("sqlite3.connect", side_effect=AssertionError("must not open nhl.db")):
            item = sh.database_health()
        self.assertEqual(item["status"], "STALE")
        self.assertIn("frozen", item["message"])
        self.assertNotEqual(item["status"], "OK")

    def test_last_sync_and_scheduler_are_not_required_and_spawn_nothing_in_cloud_mode(self):
        from operational import system_health as sh
        with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), \
             mock.patch("subprocess.run", side_effect=AssertionError("must not query launchctl/systemctl")):
            self.assertEqual(sh.last_sync_health()["status"], "NOT_REQUIRED")
            self.assertEqual(sh.live_odds_scheduler_health()["status"], "NOT_REQUIRED")

    def test_local_mode_health_behavior_is_unchanged(self):
        from operational import system_health as sh
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            self.assertIn(sh.database_health()["status"], ("OK", "ERROR"))


class TestPaperPerformanceReadOnlyInCloud(unittest.TestCase):
    def test_cloud_mode_creates_no_file_and_no_bets_when_the_bankroll_db_is_absent(self):
        from dashboard import paper_performance_view as ppv
        from operational import paper_bankroll as pb
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "paper_bankroll.db"
            with mock.patch.object(pb, "DB_PATH", absent), \
                 mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
                state = ppv.full_dashboard_state()
            self.assertFalse(absent.exists(), "the web process must never create the bankroll DB")
        for track in pb.TRACKS:
            self.assertEqual(state[track]["summary"]["wins"] + state[track]["summary"]["losses"], 0)
            self.assertEqual(state[track]["bets"], [])

    def test_cloud_mode_leaves_an_existing_bankroll_db_byte_identical(self):
        import hashlib
        from dashboard import paper_performance_view as ppv
        from operational import paper_bankroll as pb
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_bankroll.db"
            pb.init_db(path).close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(pb, "DB_PATH", path), \
                 mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
                ppv.full_dashboard_state()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


class TestBoundedCachesAndQueries(unittest.TestCase):
    def test_every_streamlit_cache_decorator_is_bounded(self):
        unbounded = []
        for src in glob.glob(str(REPO / "dashboard" / "**" / "*.py"), recursive=True):
            tree = ast.parse(Path(src).read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                                and dec.func.attr in ("cache_data", "cache_resource")):
                            if "max_entries" not in {kw.arg for kw in dec.keywords}:
                                unbounded.append(f"{Path(src).name}:{node.name}")
                        elif (isinstance(dec, ast.Attribute) and dec.attr in ("cache_data", "cache_resource")):
                            unbounded.append(f"{Path(src).name}:{node.name} (bare decorator)")
        self.assertEqual(unbounded, [])

    def test_data_caches_also_expire(self):
        for src in glob.glob(str(REPO / "dashboard" / "**" / "*.py"), recursive=True):
            tree = ast.parse(Path(src).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for dec in node.decorator_list:
                        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                                and dec.func.attr == "cache_data"):
                            self.assertIn("ttl", {kw.arg for kw in dec.keywords}, f"{Path(src).name}:{node.name}")

    def test_operational_summary_no_longer_materializes_the_whole_ledger(self):
        import inspect
        from operational import prospective_ledger as pl
        source = inspect.getsource(pl.operational_summary)
        self.assertNotIn("SELECT * FROM predictions", source)
        self.assertNotIn("fetchall()", source)

    def test_operational_summary_matches_a_python_reference_including_null_and_empty_fields(self):
        import datetime as dt
        from operational import prospective_ledger as pl
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE predictions (created_at_utc TEXT, result_status TEXT, "
                     "event_start_utc TEXT, prediction_checkpoint TEXT)")
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        rows = [
            (f"{today}T01:00:00Z", "PENDING", "2000-01-01T00:00:00Z", "PRIMARY_DAILY"),
            (f"{today}T02:00:00Z", "PENDING", "2999-01-01T00:00:00Z", "MARKET_REFRESH"),
            ("2020-01-01T00:00:00Z", "PENDING", None, None),
            ("2020-01-02T00:00:00Z", "WIN", "", ""),
            (None, "PENDING", "2000-01-01T00:00:00Z", "PRIMARY_DAILY"),
            ("", "LOSS", "2000-01-01T00:00:00Z", "MARKET_REFRESH"),
        ]
        conn.executemany("INSERT INTO predictions VALUES (?, ?, ?, ?)", rows)
        now_iso = pl._utcnow_iso()
        dict_rows = [dict(r) for r in conn.execute("SELECT * FROM predictions")]
        expected = {
            "total": len(dict_rows),
            "recorded_today": len([r for r in dict_rows if (r.get("created_at_utc") or "").startswith(today)]),
            "pending_settlement": len([r for r in dict_rows if r.get("result_status") == "PENDING"
                                       and (r.get("event_start_utc") or "") < now_iso]),
            "last_recorded_at_utc": max((r["created_at_utc"] for r in dict_rows if r.get("created_at_utc")),
                                        default=None),
        }
        checkpoints = {}
        for r in dict_rows:
            key = r.get("prediction_checkpoint") or "UNKNOWN"
            checkpoints[key] = checkpoints.get(key, 0) + 1
        expected["by_checkpoint"] = checkpoints
        self.assertEqual(pl.operational_summary(conn), expected)

    def test_empty_ledger_reports_honest_zeros(self):
        from operational import prospective_ledger as pl
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE predictions (created_at_utc TEXT, result_status TEXT, "
                     "event_start_utc TEXT, prediction_checkpoint TEXT)")
        self.assertEqual(pl.operational_summary(conn),
                         {"total": 0, "recorded_today": 0, "pending_settlement": 0,
                          "last_recorded_at_utc": None, "by_checkpoint": {}})


class TestOddsArchiveRenderPath(unittest.TestCase):
    def test_repeated_page_reruns_do_not_reread_the_archive_unless_it_changed(self):
        from dashboard import live_dk as ldk
        from research.live_sog_pricing import archive
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "20260101T000000Z_a.json").write_text(json.dumps({"meta": {}, "response": {"id": "x"}}))
            with mock.patch.object(ldk, "ARCHIVE_DIR", d), \
                 mock.patch.object(ldk, "LEGACY_ARCHIVE_DIR", d / "none"):
                ldk._latest_markets_memo = None
                calls = []
                real = archive.load_archived
                with mock.patch.object(archive, "load_archived", side_effect=lambda p: (calls.append(p), real(p))[1]):
                    ldk.load_latest_verified_moneyline_markets()
                    first = len(calls)
                    ldk.load_latest_verified_moneyline_markets()
                    ldk.load_latest_verified_moneyline_markets()
                    self.assertEqual(len(calls), first, "unchanged archive was re-read on rerun")
                    (d / "20260102T000000Z_b.json").write_text(json.dumps({"meta": {}, "response": {"id": "y"}}))
                    ldk.load_latest_verified_moneyline_markets()
                    self.assertGreater(len(calls), first, "a new capture must invalidate the memo")
                ldk._latest_markets_memo = None

    def test_odds_archive_health_sees_the_runtime_directory_not_only_the_legacy_one(self):
        """Pre-existing bug fixed this sprint: it only looked in the legacy
        git-tracked directory, blind to every capture since the archive split."""
        from operational import system_health as sh
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "operational" / "odds_archive" / "live"
            runtime.mkdir(parents=True)
            (runtime / "20260925T120000Z_x.json").write_text(
                json.dumps({"meta": {"retrieved_at_utc": "2026-09-25T12:00:00Z"}}))
            with mock.patch.object(sh, "REPO_ROOT", root):
                item = sh.odds_archive_freshness_health()
        self.assertEqual(item["status"], "OK")
        self.assertEqual(item["last_updated_utc"], "2026-09-25T12:00:00Z")


class TestDiagnosticsAdminOnly(unittest.TestCase):
    def _run(self, role):
        from streamlit.testing.v1 import AppTest
        from operational import auth_store
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "auth.db"):
            at = AppTest.from_file(str(REPO / "dashboard" / "pages" / "37_Diagnostics.py"), default_timeout=60)
            if role:
                at.session_state["_auth_username"] = "t"
                at.session_state["_auth_role"] = role
            at.run()
        return at

    def test_a_user_session_is_stopped_before_anything_renders(self):
        at = self._run("USER")
        self.assertTrue(any("restricted to the administrator" in e.value for e in at.error))
        self.assertEqual(len(at.metric), 0)

    def test_a_logged_out_session_is_stopped(self):
        at = self._run(None)
        self.assertEqual(len(at.metric), 0)

    def test_an_admin_sees_process_facts(self):
        at = self._run("ADMIN")
        self.assertEqual(list(at.exception), [])
        labels = {m.label for m in at.metric}
        self.assertTrue({"Process RSS (MB)", "Peak RSS (MB)", "Runtime mode", "Model stack loaded"} <= labels)

    def test_diagnostics_expose_no_environment_or_secret_material(self):
        from dashboard import diagnostics_view as dv
        d = dv.process_diagnostics()
        self.assertEqual(set(d), {"runtime_mode", "rss_mb", "peak_rss_mb", "python_version",
                                  "streamlit_version", "libraries_loaded", "research_modules_loaded",
                                  "model_stack_loaded"})
        blob = json.dumps(d).lower()
        for needle in ("api_key", "secret", "token", "password", "/users/"):
            self.assertNotIn(needle, blob)


class TestCloudAdminBootstrapNeedsASetupCode(unittest.TestCase):
    """On Community Cloud the first-visit "create the administrator" form would
    hand ADMIN to whoever reaches the public URL first (and again after every
    restart of the ephemeral filesystem). Cloud mode requires a secret setup code."""

    def _app(self, tmp, *, cloud, code):
        from streamlit.testing.v1 import AppTest
        from operational import auth_store
        env = {"NHL_ENGINE_ADMIN_SETUP_CODE": code} if code else {}
        self._patches = [
            mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "auth.db"),
            mock.patch.object(rm, "current_mode",
                              return_value=rm.COMMUNITY_CLOUD_MODE if cloud else rm.LOCAL_MODE),
            mock.patch.dict(os.environ, env, clear=False),
        ]
        for p in self._patches:
            p.start()
        if not code:
            os.environ.pop("NHL_ENGINE_ADMIN_SETUP_CODE", None)
        at = AppTest.from_file(str(REPO / "dashboard" / "app.py"), default_timeout=60)
        at.run()
        return at

    def tearDown(self):
        for p in getattr(self, "_patches", []):
            p.stop()

    def _users(self, tmp):
        from operational import auth_store
        conn = auth_store.get_connection(Path(tmp) / "auth.db")
        try:
            return auth_store.list_users(conn)
        finally:
            conn.close()

    def test_cloud_without_a_configured_code_offers_no_way_to_create_an_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            at = self._app(tmp, cloud=True, code=None)
            self.assertEqual(len(at.text_input), 0)
            self.assertTrue(any("setup is disabled" in e.value for e in at.error))
            self.assertEqual(self._users(tmp), [])

    def test_cloud_with_a_code_rejects_a_wrong_code_and_creates_nobody(self):
        with tempfile.TemporaryDirectory() as tmp:
            at = self._app(tmp, cloud=True, code="right-code-123")
            labels = [t.label for t in at.text_input]
            self.assertIn("Setup code", labels)
            for t in at.text_input:
                t.input({"Admin username": "owner", "Password": "pw-12345", "Confirm password": "pw-12345",
                         "Setup code": "WRONG"}[t.label])
            at.button[0].click().run()
            self.assertTrue(any("Invalid setup code" in e.value for e in at.error))
            self.assertEqual(self._users(tmp), [])

    def test_cloud_with_the_right_code_creates_the_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            at = self._app(tmp, cloud=True, code="right-code-123")
            for t in at.text_input:
                t.input({"Admin username": "owner", "Password": "pw-12345", "Confirm password": "pw-12345",
                         "Setup code": "right-code-123"}[t.label])
            at.button[0].click().run()
            self.assertEqual([(u["username"], u["role"]) for u in self._users(tmp)], [("owner", "ADMIN")])

    def test_local_mode_bootstrap_is_unchanged_no_code_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            at = self._app(tmp, cloud=False, code=None)
            self.assertEqual([t.label for t in at.text_input], ["Admin username", "Password", "Confirm password"])

    def test_setup_code_comparison_is_constant_time_and_rejects_empty(self):
        from dashboard import auth
        with mock.patch.dict(os.environ, {"NHL_ENGINE_ADMIN_SETUP_CODE": "abc"}):
            self.assertTrue(auth.setup_code_valid("abc"))
            self.assertFalse(auth.setup_code_valid("abd"))
            self.assertFalse(auth.setup_code_valid(""))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NHL_ENGINE_ADMIN_SETUP_CODE", None)
            with mock.patch.object(auth, "_configured_setup_code", return_value=None):
                self.assertFalse(auth.setup_code_valid(""))


class TestRequirementsManifests(unittest.TestCase):
    def _pins(self, path):
        return {line.split("==")[0].strip(): line.split("==")[1].strip()
                for line in path.read_text().splitlines() if "==" in line and not line.lstrip().startswith("#")}

    def test_streamlit_is_pinned_identically_in_both_manifests_to_the_installed_version(self):
        import streamlit
        root = self._pins(REPO / "requirements.txt")
        cloud = self._pins(REPO / "dashboard" / "requirements.txt")
        self.assertEqual(root["streamlit"], cloud["streamlit"])
        self.assertEqual(root["streamlit"], streamlit.__version__)

    def test_cloud_manifest_omits_what_the_thin_path_never_imports(self):
        text = (REPO / "dashboard" / "requirements.txt").read_text()
        active = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertFalse(any(l.lower().startswith("cryptography") for l in active))
        self.assertTrue(any(l.lower().startswith("requests") for l in active))


class TestReadOnlyOpeners(unittest.TestCase):
    """open_for_dashboard(): unchanged in LOCAL_MODE, strictly read-only in Cloud."""

    def test_local_mode_still_initializes_exactly_like_init_db(self):
        from operational import paper_bankroll as pb, prospective_ledger as pl
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            for mod, name in ((pb, "pb.db"), (pl, "pl.db")):
                path = Path(tmp) / name
                mod.open_for_dashboard(path).close()
                self.assertTrue(path.exists())

    def test_cloud_mode_never_creates_a_missing_database_file(self):
        from operational import paper_bankroll as pb, prospective_ledger as pl
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            for mod, name in ((pb, "pb.db"), (pl, "pl.db")):
                path = Path(tmp) / name
                conn = mod.open_for_dashboard(path)
                conn.close()
                self.assertFalse(path.exists(), f"{name} must not be created in Community Cloud mode")

    def test_cloud_mode_opens_an_existing_database_read_only(self):
        from operational import paper_bankroll as pb, prospective_ledger as pl
        with tempfile.TemporaryDirectory() as tmp:
            for mod, name, table in ((pb, "pb.db", "paper_bets"), (pl, "pl.db", "predictions")):
                path = Path(tmp) / name
                mod.init_db(path).close()
                with mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
                    conn = mod.open_for_dashboard(path)
                    with self.assertRaises(sqlite3.OperationalError):
                        conn.execute(f"DELETE FROM {table}")
                    conn.close()

    def test_special_teams_open_readonly_never_creates_or_writes(self):
        from operational import special_teams_history_store as sths
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "sth.db"
            with self.assertRaises(FileNotFoundError):
                sths.open_readonly(missing)
            self.assertFalse(missing.exists())
            sths.get_connection(missing).close()
            conn = sths.open_readonly(missing)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM special_teams_history")


class TestAppNavigationUsesTheRegistry(unittest.TestCase):
    def test_app_py_has_no_hardcoded_page_list_any_more(self):
        src = (REPO / "dashboard" / "app.py").read_text()
        self.assertNotIn('_p("21_Today.py"', src)
        self.assertIn("page_registry.pages_for", src)
        self.assertIn("auth.render_auth_gate()", src)
        # the auth gate must still run before navigation is built
        self.assertLess(src.index("render_auth_gate"), src.index("page_registry.pages_for"))


if __name__ == "__main__":
    unittest.main()
