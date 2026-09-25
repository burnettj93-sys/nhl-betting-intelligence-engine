"""
Cloud live-data sprint (2026-09-25): tests for snapshot schema/validation, the
read-only builder, the publisher (against a LOCAL bare git repo -- the real
GitHub remote is never touched), the scheduler hook, publication health, the
Community Cloud reader (against a real local HTTP server), stale/failure UI
states, the Cloud authentication model, and a memory/no-import regression guard.
"""
from __future__ import annotations

import copy
import datetime as dt
import http.server
import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from dashboard import cloud_snapshot, snapshot_source
from operational import cloud_publish_hook as hook
from operational import cloud_snapshot_schema as schema
from operational import ingestion_health
from operational import publish_cloud_snapshot as pub
from operational import runtime_mode as rm

REPO = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 9, 25, 15, 0, tzinfo=dt.timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def sample_doc(data_as_of_hours_ago: float | None = 1.0, **overrides) -> dict:
    as_of = _iso(data_as_of_hours_ago) if data_as_of_hours_ago is not None else None
    doc = {
        "schema_version": 2,
        "metadata": {
            "schema_version": 2, "generated_at": NOW.isoformat(), "generated_by": "operational.publish_cloud_snapshot",
            "source_master_commit": "abc123def456", "engine_mode": "LOCAL_MODE/ACTIVE", "data_as_of": as_of,
            "freshness": {"nhl_data": _iso(2), "odds": as_of, "recommendations": None,
                          "settlement": _iso(3), "postmortem": _iso(4)},
        },
        "demo": {"games": [], "opportunities": []},
        "live_moneyline_rows": [{"event_id": "e1", "status": "PRICED", "source": schema.PROVENANCE_LIVE}],
    }
    doc.update(overrides)
    return doc


def _bundled_backed_doc(data_as_of_hours_ago: float | None = 1.0) -> dict:
    """A v2 doc carrying the REAL bundled demo board, so Cloud pages can render from it."""
    bundled = json.loads((REPO / "dashboard" / "cloud_snapshot" / "board.json").read_text())
    doc = sample_doc(data_as_of_hours_ago)
    doc["demo"] = bundled["demo"]
    doc["live_moneyline_rows"] = bundled["live_moneyline_rows"]
    doc["metadata"]["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if data_as_of_hours_ago is not None:
        real_now = dt.datetime.now(dt.timezone.utc)
        doc["metadata"]["data_as_of"] = (real_now - dt.timedelta(hours=data_as_of_hours_ago)).isoformat()
        doc["metadata"]["freshness"]["odds"] = doc["metadata"]["data_as_of"]
    return doc


# --------------------------------------------------------------------------------------- schema
class TestValidation(unittest.TestCase):
    def test_a_well_formed_v2_snapshot_validates(self):
        schema.validate_snapshot(sample_doc(), for_publication=True)

    def test_the_bundled_v1_board_is_still_a_valid_fallback(self):
        v1 = json.loads((REPO / "dashboard" / "cloud_snapshot" / "board.json").read_text())
        schema.validate_snapshot(v1)
        self.assertEqual(schema.metadata_of(v1)["data_as_of"], v1["meta"]["newest_real_dk_capture_utc"])

    def test_unsupported_schema_version_is_rejected(self):
        for version in (0, 3, 99, "2", None):
            doc = sample_doc(); doc["schema_version"] = version
            with self.assertRaises(schema.SnapshotInvalid):
                schema.validate_snapshot(doc)

    def test_non_object_and_missing_sections_are_rejected(self):
        for bad in ([], "x", 5, None):
            with self.assertRaises(schema.SnapshotInvalid):
                schema.validate_snapshot(bad)
        for missing in ("metadata", "demo"):
            doc = sample_doc(); doc.pop(missing)
            with self.assertRaises(schema.SnapshotInvalid):
                schema.validate_snapshot(doc)

    def test_required_metadata_fields_and_timestamps_are_enforced(self):
        for field in schema.REQUIRED_METADATA_V2:
            doc = sample_doc(); doc["metadata"].pop(field)
            with self.assertRaises(schema.SnapshotInvalid, msg=field):
                schema.validate_snapshot(doc)
        doc = sample_doc(); doc["metadata"]["generated_at"] = "yesterday-ish"
        with self.assertRaises(schema.SnapshotInvalid):
            schema.validate_snapshot(doc)
        doc = sample_doc(); doc["metadata"]["freshness"]["odds"] = "not a time"
        with self.assertRaises(schema.SnapshotInvalid):
            schema.validate_snapshot(doc)

    def test_nan_and_infinity_are_rejected_everywhere(self):
        for bad in (float("nan"), float("inf"), -math.inf):
            doc = sample_doc(); doc["demo"]["x"] = [1, {"deep": bad}]
            with self.assertRaises(schema.SnapshotInvalid):
                schema.validate_snapshot(doc)
            with self.assertRaises(schema.SnapshotInvalid):
                schema.strict_dumps(doc)

    def test_secret_like_keys_are_stripped_at_publication(self):
        for key in ("api_key", "THE_ODDS_API_KEY", "password", "client_secret", "refresh_token", "Authorization",
                    "access_token", "credentials"):
            doc = sample_doc(); doc["demo"][key] = "x"
            with self.assertRaises(schema.SnapshotInvalid, msg=key):
                schema.validate_snapshot(doc, for_publication=True)

    def test_a_known_secret_value_anywhere_in_the_snapshot_blocks_publication(self):
        doc = sample_doc(); doc["demo"]["note"] = "prefix c59df7964fb69d8deda5d51d07e2dd1f suffix"
        with self.assertRaises(schema.SnapshotInvalid):
            schema.validate_snapshot(doc, known_secrets=("c59df7964fb69d8deda5d51d07e2dd1f",), for_publication=True)
        schema.validate_snapshot(doc, for_publication=True)          # not a KNOWN secret -> allowed by value

    def test_credential_looking_strings_and_absolute_paths_are_blocked(self):
        for text in ("Bearer abcdefghijklmnopqrstuvwx", "ghp_abcdefghijklmnopqrstuvwxyz0123", "sk-abcdefghijklmnopqrstuvwx",
                     "-----BEGIN RSA PRIVATE KEY-----", "https://x/y?api_key=1", "/Users/johnburnett/Downloads/x",
                     "see /home/runner/secrets", "file at /private/tmp/x"):
            doc = sample_doc(); doc["demo"]["note"] = text
            with self.assertRaises(schema.SnapshotInvalid, msg=text):
                schema.validate_snapshot(doc, for_publication=True)

    def test_yahoo_content_is_structurally_excluded(self):
        for where in ("key", "value", "nested", "case"):
            doc = sample_doc()
            if where == "key":
                doc["demo"]["yahoo_roster"] = []
            elif where == "value":
                doc["demo"]["note"] = "from Yahoo Fantasy"
            elif where == "nested":
                doc["health"] = {"items": [{"label": "YAHOO_AUTH", "status": "OWNER_AUTH_REQUIRED"}]}
            else:
                doc["demo"]["YaHoO"] = 1
            with self.assertRaises(schema.SnapshotInvalid, msg=where):
                schema.validate_snapshot(doc, for_publication=True)

    def test_sanitize_text_makes_paths_relative_or_placeholder(self):
        self.assertEqual(schema.sanitize_text("db at /repo/root/operational/x.db", "/repo/root"), "db at operational/x.db")
        self.assertEqual(schema.sanitize_text("open /Users/a/b failed", None), "open <path> failed")


class TestFreshnessAndHashing(unittest.TestCase):
    def test_classification_boundaries_use_factual_timestamps(self):
        c = lambda h: schema.classify_freshness(_iso(h), NOW)   # noqa: E731
        self.assertEqual(c(0.1), schema.CURRENT)
        self.assertEqual(c(schema.CURRENT_MAX_HOURS), schema.CURRENT)
        self.assertEqual(c(schema.CURRENT_MAX_HOURS + 0.1), schema.STALE)
        self.assertEqual(c(schema.STALE_MAX_HOURS), schema.STALE)
        self.assertEqual(c(schema.STALE_MAX_HOURS + 0.1), schema.VERY_STALE)

    def test_missing_garbage_and_future_timestamps_are_unavailable_never_current(self):
        for bad in (None, "", "garbage", 12345):
            self.assertEqual(schema.classify_freshness(bad, NOW), schema.UNAVAILABLE)
        self.assertEqual(schema.classify_freshness((NOW + dt.timedelta(hours=5)).isoformat(), NOW), schema.UNAVAILABLE)

    def test_z_suffix_and_naive_timestamps_parse(self):
        self.assertEqual(schema.classify_freshness("2026-09-25T14:00:00Z", NOW), schema.CURRENT)
        self.assertEqual(schema.classify_freshness("2026-09-25T14:00:00", NOW), schema.CURRENT)

    def test_content_hash_ignores_publication_metadata_and_compute_stamps_and_key_order(self):
        a = sample_doc(); a["morning_review"] = {"generated_at_utc": "A", "x": 1}
        b = copy.deepcopy(a)
        b["metadata"].update(generated_at=_iso(0), generated_by="other", source_master_commit="zzz",
                             content_hash="whatever", publication_seq=99)
        b["morning_review"]["generated_at_utc"] = "B"
        b = dict(reversed(list(b.items())))
        self.assertEqual(schema.content_hash(a), schema.content_hash(b))

    def test_content_hash_changes_when_substantive_content_changes(self):
        a, b = sample_doc(), sample_doc()
        b["demo"]["opportunities"].append({"x": 1})
        self.assertNotEqual(schema.content_hash(a), schema.content_hash(b))
        c = sample_doc(); c["metadata"]["data_as_of"] = _iso(0.5)
        self.assertNotEqual(schema.content_hash(a), schema.content_hash(c))


# --------------------------------------------------------------------------------------- builder
class TestBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from operational import cloud_snapshot_builder as builder
        from operational import paper_bankroll as pb, prospective_ledger as pl
        cls.tmp = tempfile.TemporaryDirectory()
        cls.absent_pb, cls.absent_pl = Path(cls.tmp.name) / "pb.db", Path(cls.tmp.name) / "pl.db"
        cls.patches = [mock.patch.object(pb, "DB_PATH", cls.absent_pb), mock.patch.object(pl, "DB_PATH", cls.absent_pl),
                       mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", Path(cls.tmp.name) / "h.json")]
        for p in cls.patches:
            p.start()
        cls.builder = builder
        cls.doc, cls.errors = builder.build_live_snapshot()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_every_section_builds_from_real_state_and_the_result_publishes_cleanly(self):
        self.assertEqual(self.errors, {})
        for section in ("demo", "live_moneyline_rows", "real_recommendations", "performance", "morning_review",
                        "model_learning", "ledger", "data_status", "health", "metadata"):
            self.assertIn(section, self.doc)
        schema.validate_snapshot(self.doc, for_publication=True)

    def test_the_builder_is_strictly_read_only(self):
        self.assertFalse(self.absent_pb.exists(), "building must never create the bankroll DB")
        self.assertFalse(self.absent_pl.exists(), "building must never create the ledger DB")

    def test_metadata_carries_every_required_field_and_component_freshness(self):
        m = self.doc["metadata"]
        for field in schema.REQUIRED_METADATA_V2:
            self.assertIn(field, m)
        self.assertEqual(set(m["freshness"]), set(schema.FRESHNESS_KEYS))
        self.assertEqual(m["schema_version"], 2)
        self.assertEqual(m["provenance"], {"demo": schema.PROVENANCE_DEMO,
                                           "live_moneyline_rows": schema.PROVENANCE_LIVE,
                                           "real_recommendations": schema.PROVENANCE_REAL_MARKET})

    def test_provenance_labels_are_preserved_and_never_flattened_together(self):
        self.assertEqual(self.doc["real_recommendations"]["provenance"], schema.PROVENANCE_REAL_MARKET)
        for row in self.doc["live_moneyline_rows"]:
            self.assertEqual(row["source"], schema.PROVENANCE_LIVE)
        for row in self.doc["demo"]["opportunities"]:
            self.assertEqual(row["source"], schema.PROVENANCE_DEMO)
            self.assertTrue(row["is_demo"])

    def test_live_rows_now_include_the_scheduler_s_sport_level_captures(self):
        newest = max(r["captured_at_utc"] for r in self.doc["live_moneyline_rows"])
        self.assertGreater(newest, "2026-09-16", "list-shaped sport-level captures must be read")

    def test_yahoo_and_user_specific_data_are_absent_structurally(self):
        blob = schema.strict_dumps(self.doc).lower()
        self.assertNotIn("yahoo", blob)
        for key in ("username", "session", "password", "email"):
            self.assertNotIn(f'"{key}"', blob)

    def test_no_absolute_filesystem_paths_leak(self):
        blob = schema.strict_dumps(self.doc)
        for prefix in ("/Users/", "/home/", "/private/", "/opt/"):
            self.assertNotIn(prefix, blob)

    def test_the_snapshot_stays_compact(self):
        self.assertLess(len(schema.strict_dumps(self.doc).encode()), 1_500_000)

    def test_one_failing_section_is_omitted_and_reported_but_never_blocks_the_rest(self):
        boom = {**self.builder._SECTION_BUILDERS, "morning_review": mock.Mock(side_effect=RuntimeError("db exploded"))}
        with mock.patch.object(self.builder, "_SECTION_BUILDERS", boom):
            doc, errors = self.builder.build_live_snapshot(sections=("morning_review", "health"))
        self.assertIn("morning_review", errors)
        self.assertNotIn("morning_review", doc)
        self.assertIn("health", doc)
        self.assertEqual(doc["metadata"]["sections_omitted"], ["morning_review"])

    def test_the_builder_never_touches_yahoo_token_storage(self):
        import fantasy.yahoo.token_store as ts
        with mock.patch.object(ts.EncryptedFileTokenStore, "get", side_effect=AssertionError("Yahoo touched")):
            doc, errors = self.builder.build_live_snapshot(sections=("health",))
        self.assertEqual(errors, {})


# --------------------------------------------------------------------------------------- publisher
def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class PublisherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = str(Path(self.tmp.name) / "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        self.patches = [
            mock.patch.object(pub, "STATE_PATH", Path(self.tmp.name) / "state.json"),
            mock.patch.object(pub, "LOCK_PATH", Path(self.tmp.name) / "lock"),
            mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", Path(self.tmp.name) / "health.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def publish(self, doc=None, errors=None, **kw):
        doc = doc if doc is not None else sample_doc()
        kw.setdefault("sleep", lambda s: None)
        return pub.publish(remote=self.remote, doc_and_errors=(copy.deepcopy(doc), errors or {}), now=NOW, **kw)

    def commits(self):
        return int(_git("--git-dir", self.remote, "rev-list", "--count", pub.DATA_BRANCH))

    def files(self):
        return sorted(_git("--git-dir", self.remote, "ls-tree", "-r", "--name-only", pub.DATA_BRANCH).split())

    def remote_snapshot(self):
        return json.loads(_git("--git-dir", self.remote, "show", f"{pub.DATA_BRANCH}:current/snapshot.json"))


class TestPublisher(PublisherCase):
    def test_first_publish_creates_a_data_only_orphan_branch(self):
        r = self.publish()
        self.assertEqual((r["status"], r["publication_seq"]), (pub.SUCCESS, 1))
        self.assertEqual(self.files(), ["README.md", "current/metadata.json", "current/snapshot.json"])
        self.assertEqual(self.commits(), 1)
        snap = self.remote_snapshot()
        schema.validate_snapshot(snap, for_publication=True)
        self.assertEqual(snap["metadata"]["content_hash"], r["content_hash"])

    def test_unchanged_content_is_no_change_and_creates_no_commit_and_no_network(self):
        self.publish()
        with mock.patch.object(pub, "_publish_via_git", side_effect=AssertionError("must not touch git")):
            r = self.publish()
        self.assertEqual((r["status"], r["reason"]), (pub.NO_CHANGE, "CONTENT_UNCHANGED"))
        self.assertEqual(self.commits(), 1)

    def test_no_change_is_detected_against_the_remote_even_if_local_state_is_lost(self):
        self.publish()
        pub.STATE_PATH.unlink()
        r = self.publish()
        self.assertEqual((r["status"], r["reason"]), (pub.NO_CHANGE, "REMOTE_ALREADY_CURRENT"))
        self.assertEqual(self.commits(), 1)

    def test_only_publication_metadata_differing_is_still_no_change(self):
        self.publish()
        doc = sample_doc(); doc["metadata"]["generated_at"] = _iso(0); doc["metadata"]["source_master_commit"] = "ffff"
        self.assertEqual(self.publish(doc, now_override=None)["status"] if False else self.publish(doc)["status"], pub.NO_CHANGE)

    def test_changed_content_publishes_a_new_commit_with_an_incremented_sequence(self):
        self.publish()
        doc = sample_doc(); doc["demo"]["opportunities"] = [{"x": 1}]
        r = self.publish(doc, force=True)
        self.assertEqual((r["status"], r["publication_seq"]), (pub.SUCCESS, 2))
        self.assertEqual(self.commits(), 2)
        self.assertEqual(self.remote_snapshot()["demo"]["opportunities"], [{"x": 1}])

    def test_omitted_sections_make_the_publication_partial_success(self):
        r = self.publish(errors={"morning_review": "RuntimeError: x"})
        self.assertEqual(r["status"], pub.PARTIAL)
        self.assertEqual(r["sections_omitted"], ["morning_review"])
        self.assertIn("morning_review", r["reason"])

    def test_validation_failure_publishes_nothing(self):
        for label, mutate in (("secret key", lambda d: d["demo"].update(api_key="x")),
                              ("yahoo", lambda d: d["demo"].update(note="Yahoo roster")),
                              ("path", lambda d: d["demo"].update(note="/Users/x/y")),
                              ("nan", lambda d: d["demo"].update(x=float("nan")))):
            doc = sample_doc(); mutate(doc)
            r = self.publish(doc)
            self.assertEqual(r["status"], pub.FAILED, label)
            self.assertIn("VALIDATION", r["reason"], label)
        proc = subprocess.run(["git", "--git-dir", self.remote, "rev-parse", "--verify", pub.DATA_BRANCH],
                              capture_output=True)
        self.assertNotEqual(proc.returncode, 0, "no branch may exist after only invalid attempts")

    def test_a_configured_secret_value_in_the_snapshot_blocks_publication(self):
        doc = sample_doc(); doc["demo"]["note"] = "leak-SECRETVALUE-123456"
        with mock.patch.object(pub, "known_secret_values", return_value=("SECRETVALUE-123456",)):
            r = self.publish(doc)
        self.assertEqual(r["status"], pub.FAILED)

    def test_transport_failure_is_failed_bounded_and_never_raises(self):
        sleeps = []
        r = pub.publish(remote=str(Path(self.tmp.name) / "no-such-remote.git"), doc_and_errors=(sample_doc(), {}),
                        now=NOW, sleep=sleeps.append)
        self.assertEqual(r["status"], pub.FAILED)
        self.assertEqual(len(sleeps), pub.MAX_ATTEMPTS - 1, "retries are bounded")

    def test_a_builder_crash_is_reported_as_failed_not_raised(self):
        with mock.patch("operational.cloud_snapshot_builder.build_live_snapshot", side_effect=RuntimeError("boom")):
            r = pub.publish(remote=self.remote, now=NOW, sleep=lambda s: None)
        self.assertEqual(r["status"], pub.FAILED)
        self.assertIn("boom", r["reason"])

    def test_rate_limit_defers_and_force_overrides(self):
        self.publish()
        doc = sample_doc(); doc["demo"]["opportunities"] = [{"y": 2}]
        self.assertEqual(self.publish(doc)["status"], pub.DEFERRED)
        self.assertEqual(self.publish(doc, force=True)["status"], pub.SUCCESS)

    def test_a_concurrent_publish_is_deferred(self):
        import fcntl
        with open(pub.LOCK_PATH, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = self.publish()
        self.assertEqual((r["status"], r["reason"]), (pub.DEFERRED, "ANOTHER_PUBLISH_IN_PROGRESS"))

    def test_dry_run_validates_but_neither_pushes_nor_records_state(self):
        r = self.publish(dry_run=True)
        self.assertEqual(r["status"], pub.SUCCESS)
        self.assertIn("DRY_RUN", r["reason"])
        self.assertFalse(pub.STATE_PATH.exists())
        self.assertNotEqual(subprocess.run(["git", "--git-dir", self.remote, "rev-parse", "--verify", pub.DATA_BRANCH],
                                           capture_output=True).returncode, 0)

    def test_history_is_bounded_by_periodic_orphan_replacement(self):
        with mock.patch.object(pub, "HISTORY_RESET_EVERY", 3), mock.patch.object(pub, "MIN_PUSH_INTERVAL_S", 0):
            for i in range(1, 5):
                doc = sample_doc(); doc["demo"]["opportunities"] = [{"n": i}]
                self.assertEqual(self.publish(doc)["status"], pub.SUCCESS)
                if i == 2:
                    self.assertEqual(self.commits(), 2)
                if i == 3:
                    self.assertEqual(self.commits(), 1, "seq 3 replaces the branch with a fresh orphan commit")
        self.assertEqual(self.remote_snapshot()["demo"]["opportunities"], [{"n": 4}])
        self.assertEqual(self.commits(), 2)

    def test_the_live_checkout_is_never_switched_or_modified_by_publishing(self):
        before = (_git("rev-parse", "HEAD", cwd=REPO), _git("branch", "--show-current", cwd=REPO),
                  _git("status", "--porcelain", cwd=REPO))
        self.publish()
        after = (_git("rev-parse", "HEAD", cwd=REPO), _git("branch", "--show-current", cwd=REPO),
                 _git("status", "--porcelain", cwd=REPO))
        self.assertEqual(before, after)

    def test_atomic_write_never_leaves_a_partial_or_temp_file_and_keeps_the_old_file_on_failure(self):
        target = Path(self.tmp.name) / "d" / "snapshot.json"
        pub._atomic_write(target, '{"a":1}')
        self.assertEqual(target.read_text(), '{"a":1}')
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pub._atomic_write(target, '{"a":2,"truncated')
        self.assertEqual(target.read_text(), '{"a":1}', "the promoted file is untouched by a failed write")

    def test_published_bytes_are_re_parsed_and_validated_not_just_the_in_memory_doc(self):
        doc = sample_doc()
        text = pub._validated_text(doc)
        self.assertEqual(json.loads(text)["schema_version"], 2)

    def test_ingestion_health_records_the_outcome_for_last_attempt_success_and_error(self):
        self.publish()
        row = ingestion_health.load_health()[pub.COMPONENT]
        self.assertEqual(row["last_status"], "SUCCESS")
        self.assertIsNotNone(row["last_success_utc"])
        bad = sample_doc(); bad["demo"]["api_key"] = "x"
        self.publish(bad, force=True)
        row = ingestion_health.load_health()[pub.COMPONENT]
        self.assertEqual(row["last_status"], "FAILED")
        self.assertIn("VALIDATION", row["last_detail"])
        self.assertIsNotNone(row["last_success_utc"], "a later failure never erases the last success")


class TestOptInAndScheduling(unittest.TestCase):
    def test_automatic_publishing_is_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(pub, "REPO_ROOT", Path("/nonexistent")):
            os.environ.pop("NHL_ENGINE_CLOUD_PUBLISH", None)
            self.assertFalse(pub.publishing_enabled())

    def test_env_and_dotenv_can_enable_it(self):
        with mock.patch.dict(os.environ, {"NHL_ENGINE_CLOUD_PUBLISH": "ON"}):
            self.assertTrue(pub.publishing_enabled())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pub, "REPO_ROOT", Path(tmp)):
            (Path(tmp) / ".env").write_text("NHL_ENGINE_CLOUD_PUBLISH=on\n")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NHL_ENGINE_CLOUD_PUBLISH", None)
                self.assertTrue(pub.publishing_enabled())

    def test_hook_does_nothing_when_disabled(self):
        runner = mock.Mock()
        with mock.patch.object(pub, "publishing_enabled", return_value=False):
            r = hook.publish_after("settlement", runner=runner)
        self.assertEqual((r["status"], r["reason"]), ("SKIPPED", "DISABLED"))
        runner.assert_not_called()

    def test_hook_skips_on_a_standby_machine(self):
        from operational import deployment_mode as dm
        runner = mock.Mock()
        with mock.patch.object(pub, "publishing_enabled", return_value=True), \
             mock.patch.object(dm, "is_active_scheduler", return_value=False):
            r = hook.publish_after("settlement", runner=runner)
        self.assertEqual(r["reason"], "STANDBY")
        runner.assert_not_called()

    def test_hook_runs_the_publisher_in_a_bounded_subprocess_and_relays_its_status(self):
        proc = mock.Mock(stdout=json.dumps({"status": "NO_CHANGE", "reason": "CONTENT_UNCHANGED", "content_hash": "h"}),
                         stderr="", returncode=0)
        runner = mock.Mock(return_value=proc)
        with mock.patch.object(pub, "publishing_enabled", return_value=True):
            r = hook.publish_after("live_odds_daily_pull:moneyline", runner=runner)
        self.assertEqual(r["status"], "NO_CHANGE")
        self.assertEqual(runner.call_args.kwargs["timeout"], hook.TIMEOUT_S)

    def test_publication_failure_never_propagates_into_the_calling_job(self):
        for boom in (subprocess.TimeoutExpired("x", 1), OSError("no python"), RuntimeError("weird")):
            with mock.patch.object(pub, "publishing_enabled", return_value=True):
                r = hook.publish_after("settlement", runner=mock.Mock(side_effect=boom))
            self.assertEqual(r["status"], "FAILED")
        garbage = mock.Mock(return_value=mock.Mock(stdout="not json", stderr="oops", returncode=2))
        with mock.patch.object(pub, "publishing_enabled", return_value=True):
            self.assertEqual(hook.publish_after("settlement", runner=garbage)["status"], "FAILED")

    def test_the_odds_job_still_prints_its_own_result_when_publishing_blows_up(self):
        from operational import live_odds_daily_pull as lop
        with mock.patch.object(lop, "run_moneyline_snapshot", return_value={"ran": True, "credits": 1}), \
             mock.patch("operational.real_odds_bridge.sync_moneyline_odds_to_snapshots", return_value={"ok": 1}), \
             mock.patch("operational.real_recommendation_orchestrator.run_real_moneyline_recommendations",
                        return_value={"ok": 2}), \
             mock.patch.object(hook, "publish_after", return_value={"status": "FAILED", "reason": "x"}), \
             mock.patch("sys.argv", ["lop", "--mode=moneyline"]), \
             mock.patch("builtins.print") as printed:
            lop._main()
        out = json.loads(printed.call_args[0][0])
        self.assertEqual(out["real_recommendation_orchestrator"], {"ok": 2})
        self.assertEqual(out["cloud_publish"]["status"], "FAILED")

    def test_the_pregame_refresh_is_not_a_publish_trigger(self):
        src = (REPO / "operational" / "nhl_sync.py").read_text()
        self.assertNotIn("cloud_publish_hook", src)
        for name in ("live_odds_daily_pull.py", "settle_daily_observations.py", "daily_postmortem.py"):
            self.assertIn("cloud_publish_hook", (REPO / "operational" / name).read_text(), name)


class TestPublisherHealth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "h.json"
        self.p = mock.patch.object(ingestion_health, "DEFAULT_CACHE_PATH", self.cache)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def health(self):
        from operational import system_health as sh
        return sh.cloud_snapshot_publish_health()

    def test_not_required_when_disabled_and_never_run(self):
        with mock.patch.object(pub, "publishing_enabled", return_value=False):
            self.assertEqual(self.health()["status"], "NOT_REQUIRED")

    def test_waiting_when_enabled_but_never_attempted(self):
        with mock.patch.object(pub, "publishing_enabled", return_value=True):
            self.assertEqual(self.health()["status"], "WAITING")

    def test_ok_after_a_success_and_after_no_change(self):
        for status in ("SUCCESS", "NO_CHANGE", "PARTIAL_SUCCESS"):
            ingestion_health.record_run(pub.COMPONENT, {"status": status})
            self.assertEqual(self.health()["status"], "OK", status)

    def test_degraded_when_the_latest_attempt_failed_but_a_recent_success_exists(self):
        ingestion_health.record_run(pub.COMPONENT, {"status": "SUCCESS"})
        ingestion_health.record_run(pub.COMPONENT, {"status": "FAILED", "error": "push rejected"})
        item = self.health()
        self.assertEqual(item["status"], "DEGRADED")
        self.assertIn("push rejected", item["message"])

    def test_error_when_it_has_only_ever_failed(self):
        ingestion_health.record_run(pub.COMPONENT, {"status": "FAILED", "error": "no remote"})
        self.assertEqual(self.health()["status"], "ERROR")

    def test_stale_when_the_last_success_is_old(self):
        ingestion_health.record_run(pub.COMPONENT, {"status": "SUCCESS"})
        rows = json.loads(self.cache.read_text())
        rows[pub.COMPONENT]["last_success_utc"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)).isoformat()
        self.cache.write_text(json.dumps(rows))
        self.assertEqual(self.health()["status"], "STALE")

    def test_publisher_health_is_part_of_the_full_health_snapshot(self):
        from operational import system_health as sh
        self.assertIn("CLOUD_SNAPSHOT_PUBLISH", sh.build_system_health())


# --------------------------------------------------------------------------------------- reader
class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        cfg = self.server.cfg
        cfg["requests"].append({"path": self.path, "auth": self.headers.get("Authorization")})
        if cfg["delay"]:
            time.sleep(cfg["delay"])
        body = cfg["body"]
        self.send_response(cfg["status"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):
        pass


class _LoopbackServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # http.server's default calls socket.getfqdn(), a DNS lookup that can hang for many seconds
        # in restricted environments; a loopback test server never needs it.
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class ReaderCase(unittest.TestCase):
    def setUp(self):
        self.server = _LoopbackServer(("127.0.0.1", 0), _Handler)
        self.server.cfg = {"body": b"", "status": 200, "delay": 0, "requests": []}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/current/snapshot.json"
        self.now = [1000.0]
        self.env = mock.patch.dict(os.environ, {"NHL_ENGINE_SNAPSHOT_URL": self.url,
                                                "NHL_ENGINE_SNAPSHOT_SOURCE": "REMOTE"})
        self.env.start()
        self.mode = mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE)
        self.mode.start()
        self.clock = mock.patch.object(snapshot_source, "_clock", lambda: self.now[0])
        self.clock.start()
        snapshot_source.reset()

    def tearDown(self):
        self.clock.stop(); self.mode.stop(); self.env.stop()
        self.server.shutdown(); self.server.server_close()
        snapshot_source.reset()

    def serve(self, doc=None, *, status=200, body=None, delay=0):
        self.server.cfg.update(status=status, delay=delay,
                               body=body if body is not None else json.dumps(doc if doc is not None else sample_doc()).encode())

    @property
    def requests(self):
        return self.server.cfg["requests"]


class TestReader(ReaderCase):
    def test_a_fresh_remote_snapshot_is_served_and_classified_from_its_own_timestamps(self):
        self.serve(sample_doc(1.0))
        with mock.patch.object(schema, "classify_freshness", wraps=schema.classify_freshness):
            st = snapshot_source.current()
        self.assertEqual((st.source, st.fetch_status), (snapshot_source.REMOTE, "OK"))
        self.assertEqual(st.schema_version, 2)
        self.assertEqual(st.data_as_of, _iso(1.0))

    def test_freshness_states_follow_the_served_data_as_of(self):
        now = dt.datetime.now(dt.timezone.utc)
        for hours, expected in ((1, schema.CURRENT), (20, schema.STALE), (100, schema.VERY_STALE)):
            snapshot_source.reset()
            doc = sample_doc(); doc["metadata"]["data_as_of"] = (now - dt.timedelta(hours=hours)).isoformat()
            self.serve(doc)
            self.assertEqual(snapshot_source.current().freshness, expected, hours)
        snapshot_source.reset()
        doc = sample_doc(None)
        self.serve(doc)
        self.assertEqual(snapshot_source.current().freshness, schema.UNAVAILABLE)

    def test_cache_ttl_means_reruns_do_not_refetch_and_expiry_does(self):
        self.serve()
        for _ in range(10):
            snapshot_source.current()
        self.assertEqual(len(self.requests), 1)
        self.now[0] += snapshot_source.TTL_S - 1
        snapshot_source.current()
        self.assertEqual(len(self.requests), 1)
        self.now[0] += 2
        snapshot_source.current()
        self.assertEqual(len(self.requests), 2)

    def test_the_ttl_is_two_to_five_minutes(self):
        self.assertTrue(120 <= snapshot_source.TTL_S <= 300)

    def test_a_stampede_of_sessions_causes_a_single_fetch(self):
        self.serve(delay=0.2)
        results = []
        threads = [threading.Thread(target=lambda: results.append(snapshot_source.current())) for _ in range(12)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(all(r.data is not None for r in results))

    def test_a_timeout_falls_back_and_says_so(self):
        self.serve(delay=1.0)
        with mock.patch.object(snapshot_source, "HTTP_TIMEOUT_S", 0.2), mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            st = snapshot_source.current()
        self.assertEqual(st.source, snapshot_source.BUNDLED_FALLBACK)
        self.assertEqual(st.fetch_status, "FAILED")
        self.assertIn("network error", st.last_error)

    def test_transient_server_errors_retry_a_bounded_number_of_times(self):
        self.serve(status=503, body=b"down")
        with mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            st = snapshot_source.current()
        self.assertEqual(len(self.requests), snapshot_source.MAX_ATTEMPTS)
        self.assertEqual(st.last_error, "HTTP 503")

    def test_a_missing_snapshot_404_is_not_retried_and_does_not_hammer(self):
        self.serve(status=404, body=b"not found")
        st = snapshot_source.current()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual((st.source, st.last_error), (snapshot_source.BUNDLED_FALLBACK, "HTTP 404"))
        snapshot_source.current(); snapshot_source.current()
        self.assertEqual(len(self.requests), 1, "no refetch inside the failure back-off window")
        self.now[0] += snapshot_source.FAILURE_RETRY_S + 1
        snapshot_source.current()
        self.assertEqual(len(self.requests), 2)

    def test_corrupt_json_and_unsupported_schema_and_missing_fields_are_rejected(self):
        bad = sample_doc(); bad["schema_version"] = 7
        no_demo = sample_doc(); no_demo.pop("demo")
        for label, kwargs in (("corrupt", {"body": b'{"schema_version": 2, "meta'}), ("schema", {"doc": bad}),
                              ("shape", {"doc": no_demo}), ("array", {"body": b"[1,2,3]"}), ("empty", {"body": b""})):
            snapshot_source.reset(); self.requests.clear()
            self.serve(**kwargs)
            st = snapshot_source.current()
            self.assertEqual(st.source, snapshot_source.BUNDLED_FALLBACK, label)
            self.assertIsNotNone(st.last_error, label)

    def test_an_oversized_response_is_rejected(self):
        self.serve(body=b"x" * (snapshot_source.MAX_BYTES + 10))
        st = snapshot_source.current()
        self.assertEqual(st.source, snapshot_source.BUNDLED_FALLBACK)
        self.assertIn("exceeds", st.last_error)

    def test_last_known_good_survives_an_outage_and_is_never_silently_treated_as_fresh(self):
        self.serve(sample_doc(1.0))
        good = snapshot_source.current()
        self.assertEqual(good.source, snapshot_source.REMOTE)
        self.serve(status=500, body=b"boom")
        self.now[0] += snapshot_source.TTL_S + 1
        with mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            st = snapshot_source.current()
        self.assertEqual(st.source, snapshot_source.REMOTE_LKG)
        self.assertEqual(st.fetch_status, "FAILED")
        self.assertEqual(st.data_as_of, good.data_as_of, "the old data is served, with its OWN timestamps")
        self.assertEqual(st.last_success_utc, good.last_success_utc)

    def test_a_corrupt_refresh_never_replaces_the_good_snapshot(self):
        self.serve(sample_doc(1.0))
        snapshot_source.current()
        self.serve(body=b"{ this is not json")
        self.now[0] += snapshot_source.TTL_S + 1
        st = snapshot_source.current()
        self.assertEqual(st.source, snapshot_source.REMOTE_LKG)
        self.assertEqual(st.schema_version, 2)
        self.assertEqual(st.last_error, "corrupt JSON")

    def test_recovery_after_an_outage_returns_to_remote(self):
        self.serve(sample_doc(1.0)); snapshot_source.current()
        self.serve(status=500, body=b"x"); self.now[0] += snapshot_source.TTL_S + 1
        with mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            snapshot_source.current()
        self.serve(sample_doc(0.5)); self.now[0] += snapshot_source.FAILURE_RETRY_S + 1
        st = snapshot_source.current()
        self.assertEqual((st.source, st.fetch_status, st.last_error), (snapshot_source.REMOTE, "OK", None))

    def test_a_stale_lkg_stays_classified_by_its_own_data_as_of(self):
        doc = sample_doc(); doc["metadata"]["data_as_of"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40)).isoformat()
        self.serve(doc); snapshot_source.current()
        self.serve(status=500, body=b"x"); self.now[0] += snapshot_source.TTL_S + 1
        with mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            st = snapshot_source.current()
        self.assertEqual((st.source, st.freshness), (snapshot_source.REMOTE_LKG, schema.VERY_STALE))

    def test_the_token_is_sent_as_a_header_and_never_appears_in_diagnostics_or_errors(self):
        secret = "tok-SHOULD-NEVER-LEAK-123456"
        self.serve(status=500, body=b"x")
        with mock.patch.dict(os.environ, {"NHL_ENGINE_SNAPSHOT_TOKEN": secret}), \
             mock.patch.object(snapshot_source, "_sleep", lambda s: None):
            snapshot_source.current()
            diag = snapshot_source.diagnostics()
        self.assertEqual(self.requests[0]["auth"], f"Bearer {secret}")
        self.assertNotIn(secret, json.dumps(diag))
        self.assertNotIn("?", diag["remote_url"] or "")

    def test_remote_is_only_used_in_community_cloud_mode_and_can_be_forced_off(self):
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            self.assertFalse(snapshot_source.remote_enabled())
        with mock.patch.dict(os.environ, {"NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED"}):
            self.assertFalse(snapshot_source.remote_enabled())
        self.assertTrue(snapshot_source.remote_enabled())

    def test_the_default_source_is_the_cloud_data_branch_raw_file(self):
        self.assertIn("/cloud-data/current/snapshot.json", snapshot_source.DEFAULT_URL)
        self.assertTrue(snapshot_source.DEFAULT_URL.startswith("https://"))

    def test_diagnostics_report_everything_the_admin_panel_needs(self):
        self.serve(sample_doc(1.0))
        d = snapshot_source.diagnostics()
        for key in ("snapshot_source", "remote_fetch_status", "last_successful_fetch_utc", "snapshot_generated_at",
                    "snapshot_data_age_hours", "schema_version", "content_hash", "freshness", "last_error"):
            self.assertIn(key, d)
        self.assertEqual(d["snapshot_source"], snapshot_source.REMOTE)

    def test_cloud_snapshot_accessors_serve_the_remote_document(self):
        doc = _bundled_backed_doc(1.0)
        doc["performance"] = {"marker": True}
        self.serve(doc)
        self.assertEqual(cloud_snapshot.performance_state(), {"marker": True})
        self.assertEqual(cloud_snapshot.freshness()["state"], schema.CURRENT)
        with self.assertRaises(cloud_snapshot.SectionUnavailable):
            cloud_snapshot.ledger_section()


# --------------------------------------------------------------------------------------- UI states
class TestBannerAndStaleBehavior(unittest.TestCase):
    def fr(self, state="CURRENT", source="REMOTE", err=None):
        return {"state": state, "data_as_of": "2026-09-25T12:00:04Z", "last_updated": "2026-09-25T12:01:00Z",
                "source": source, "last_error": err,
                "components": {"odds": "2026-09-25T12:00:04Z", "nhl_data": "2026-09-25T11:44:37+00:00"}}

    def model(self, **kw):
        from dashboard import components as comp
        return comp.cloud_banner_model(self.fr(**kw), {})

    def test_it_always_shows_data_as_of_and_last_updated(self):
        for state in ("CURRENT", "STALE", "VERY_STALE", "UNAVAILABLE"):
            text = " ".join(self.model(state=state)["lines"])
            self.assertIn("DATA AS OF: 2026-09-25T12:00:04Z", text)
            self.assertIn("LAST UPDATED: 2026-09-25T12:01:00Z", text)

    def test_stale_and_very_stale_are_prominent_and_never_look_live(self):
        for state, tone in (("STALE", "warn"), ("VERY_STALE", "bad"), ("UNAVAILABLE", "bad")):
            m = self.model(state=state)
            self.assertEqual(m["tone"], tone)
            self.assertNotIn("SNAPSHOT CURRENT", m["headline"])
        self.assertIn("STALE", self.model(state="STALE")["headline"])

    def test_current_data_is_labeled_current_in_green(self):
        m = self.model(state="CURRENT")
        self.assertEqual(m["tone"], "ok")
        self.assertIn("CURRENT", m["headline"])

    def test_remote_failure_with_last_known_good_says_remote_update_failed(self):
        m = self.model(state="CURRENT", source="REMOTE_LAST_KNOWN_GOOD", err="HTTP 503")
        self.assertIn("REMOTE UPDATE FAILED", " ".join(m["notices"]))
        self.assertIn("REMOTE UPDATE FAILED", m["headline"], "the headline itself must lead with the failure")
        self.assertNotIn("SNAPSHOT CURRENT", m["headline"])
        self.assertIn("HTTP 503", " ".join(m["notices"]))
        self.assertNotEqual(m["tone"], "ok", "a failed refresh must not look like a healthy current snapshot")

    def test_bundled_fallback_is_disclosed(self):
        m = self.model(state="VERY_STALE", source="BUNDLED_FALLBACK", err="HTTP 404")
        self.assertIn("BUNDLED", " ".join(m["notices"]))
        self.assertIn("BUNDLED FALLBACK", m["headline"])

    def test_provenance_labels_are_explained(self):
        text = " ".join(self.model()["lines"])
        for label in ("SIMULATED — DEMO ONLY", "LIVE — DRAFTKINGS", "REAL MARKET"):
            self.assertIn(label, text)

    def test_live_labels_are_downgraded_when_not_current(self):
        from dashboard import components as comp
        with mock.patch.object(comp, "live_data_state", return_value="CURRENT"):
            self.assertEqual(comp.live_label("LIVE — DRAFTKINGS"), "LIVE — DRAFTKINGS")
        for state in ("STALE", "VERY_STALE", "UNAVAILABLE"):
            with mock.patch.object(comp, "live_data_state", return_value=state):
                label = comp.live_label("LIVE — DRAFTKINGS")
                self.assertIn("NOT CURRENT", label)
                self.assertTrue(label.startswith(state.replace("_", " ")))

    def test_local_mode_is_never_downgraded(self):
        from dashboard import components as comp
        with mock.patch.object(rm, "current_mode", return_value=rm.LOCAL_MODE):
            self.assertEqual(comp.live_data_state(), "CURRENT")


class TestTodayPageStaleRendering(unittest.TestCase):
    def _today(self, state, price_age_min=30):
        from streamlit.testing.v1 import AppTest
        from operational import cloud_snapshot_schema as schema
        _orig = schema.recommendation_freshness

        def _aged(row, now=None, snapshot_generated_at=None):
            # judge every price as if it were `price_age_min` old (the bundled rows are historical)
            ts = schema.parse_utc(row.get("captured_at_utc") or row.get("odds_captured_at_utc")
                                  or row.get("created_at_utc"))
            at = ts + dt.timedelta(minutes=price_age_min) if ts else now
            return _orig(row, now=at, snapshot_generated_at=None)
        from operational import auth_store
        from dashboard import components as comp
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED"}), \
             mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "a.db"), \
             mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE), \
             mock.patch.object(schema, "recommendation_freshness", _aged), \
             mock.patch.object(comp, "live_data_state", return_value=state):
            at = AppTest.from_file(str(REPO / "dashboard" / "pages" / "21_Today.py"), default_timeout=120)
            at.session_state["_auth_username"] = "t"; at.session_state["_auth_role"] = "USER"
            at.run()
        self.assertEqual(list(at.exception), [])
        return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])

    def test_a_stale_snapshot_shows_data_stale_and_no_live_edges_heading(self):
        text = self._today("STALE")
        self.assertIn("STALE (not live)", text)
        self.assertNotIn("## Live Model Edges", text)
        self.assertIn("NOT CURRENT", text)

    def test_a_current_snapshot_keeps_the_live_heading(self):
        text = self._today("CURRENT")
        self.assertIn("Live Model Edges", text)
        self.assertNotIn("STALE (not live)", text)
        self.assertIn("MARKET FRESHNESS: CURRENT", text)

    def test_a_current_snapshot_with_an_old_price_is_not_live(self):
        """SNAPSHOT freshness must not launder a stale sportsbook price into a live one."""
        text = self._today("CURRENT", price_age_min=6 * 60)
        self.assertIn("ODDS STALE (not live)", text)
        self.assertNotIn("## Live Model Edges", text)
        self.assertIn("MARKET FRESHNESS: STALE", text)
        self.assertIn("SNAPSHOT FRESHNESS: CURRENT", text)

    def test_recorded_recommendations_carry_their_own_real_market_label_apart_from_the_demo(self):
        text = self._today("CURRENT")
        self.assertIn("Recorded Recommendations", text)
        self.assertIn("SIMULATED MARKET (DEMO ONLY)", text)


# ------------------------------------------------------------------------ market freshness
class TestMarketFreshness(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 25, 18, 0, tzinfo=dt.timezone.utc)

    def _price(self, minutes_old):
        return (self.NOW - dt.timedelta(minutes=minutes_old)).isoformat()

    def _start(self, hours_ahead):
        return (self.NOW + dt.timedelta(hours=hours_ahead)).isoformat()

    def classify(self, minutes_old, start_hours=None, generated=None):
        return schema.classify_market_freshness(
            self._price(minutes_old), None if start_hours is None else self._start(start_hours),
            self.NOW, generated)

    def test_ordinary_odds_at_or_under_three_hours_are_current(self):
        for minutes in (0, 60, 179, 180):
            self.assertEqual(self.classify(minutes, 10)["state"], "CURRENT", minutes)
        self.assertEqual(self.classify(120)["state"], "CURRENT")           # no game start known

    def test_ordinary_odds_over_three_hours_are_stale(self):
        for minutes in (181, 240, 13 * 60):
            r = self.classify(minutes, 10)
            self.assertEqual((r["state"], r["reason"]), ("STALE", "OLDER_THAN_3H"), minutes)

    def test_game_within_four_hours_price_89_minutes_is_current(self):
        r = self.classify(89, 3.5)
        self.assertEqual((r["state"], r["limit_minutes"]), ("CURRENT", 90.0))

    def test_game_within_four_hours_price_91_minutes_is_stale(self):
        r = self.classify(91, 3.5)
        self.assertEqual((r["state"], r["reason"]), ("STALE", "NEAR_GAME_OLDER_THAN_90M"))

    def test_the_four_hour_window_boundary(self):
        self.assertEqual(self.classify(120, 4.0)["state"], "STALE")        # exactly 4 h out: tight rule
        self.assertEqual(self.classify(120, 4.5)["state"], "CURRENT")      # just outside: 3 h rule

    def test_a_started_game_is_never_current(self):
        r = self.classify(5, -0.1)
        self.assertEqual((r["state"], r["reason"]), ("STALE", "GAME_STARTED"))

    def test_missing_or_unparseable_price_time_is_unavailable(self):
        for bad in (None, "", "garbage"):
            self.assertEqual(schema.classify_market_freshness(bad, None, self.NOW)["state"], "UNAVAILABLE")

    def test_a_fresh_snapshot_with_an_old_price_is_stale_at_market_level(self):
        generated = self.NOW.isoformat()                                   # published this minute
        self.assertEqual(schema.classify_freshness(generated, self.NOW), "CURRENT")
        r = self.classify(300, 10, generated)                              # price captured 5 h ago
        self.assertEqual(r["state"], "STALE")

    def test_a_price_later_than_the_snapshot_that_carries_it_is_never_current(self):
        """Stale snapshot + fresh embedded timestamp: impossible, so it is not trusted."""
        generated = (self.NOW - dt.timedelta(hours=20)).isoformat()
        r = self.classify(10, 10, generated)                               # price 10 min ago, snapshot 20 h old
        self.assertEqual((r["state"], r["reason"]), ("UNAVAILABLE", "TIMESTAMP_AFTER_SNAPSHOT"))
        self.assertEqual(schema.classify_market_freshness(self._price(-120), None, self.NOW)["state"],
                         "UNAVAILABLE")                                    # future-dated

    def test_recommendation_freshness_exposes_the_four_facts_and_ignores_the_stored_action(self):
        row = {"created_at_utc": self._price(200), "odds_captured_at_utc": self._price(20),
               "event_start_utc": self._start(8), "prospective_status": "BET"}
        before = dict(row)
        f = schema.recommendation_freshness(row, self.NOW)
        self.assertEqual(row, before)                                       # stored recommendation untouched
        self.assertEqual(f["state"], "CURRENT")                             # price time, not created_at, is judged
        self.assertEqual((f["created_at"], f["market_captured_at"], f["game_start_utc"]),
                         (row["created_at_utc"], row["odds_captured_at_utc"], row["event_start_utc"]))

    def test_parlay_inherits_its_stalest_leg(self):
        fresh = {"odds_captured_at_utc": self._price(20), "event_start_utc": self._start(8)}
        stale = {"odds_captured_at_utc": self._price(400), "event_start_utc": self._start(8)}
        self.assertEqual(schema.parlay_freshness([fresh, fresh, fresh], self.NOW)["state"], "CURRENT")
        p = schema.parlay_freshness([fresh, stale, fresh], self.NOW)
        self.assertEqual(p["state"], "STALE")
        self.assertEqual(p["limiting_leg"]["reason"], "OLDER_THAN_3H")
        self.assertEqual(schema.parlay_freshness([], self.NOW)["state"], "UNAVAILABLE")
        near = {"odds_captured_at_utc": self._price(100), "event_start_utc": self._start(2)}
        self.assertEqual(schema.parlay_freshness([fresh, near], self.NOW)["state"], "STALE")

    def test_morning_review_stays_valid_on_its_daily_cadence(self):
        """Component freshness for settlement/postmortem uses the broad snapshot classes (13 h / 36 h):
        a daily job 20 h old is STALE-but-usable, not treated as a live-odds violation, and a
        30-minute odds rule must never be applied to it."""
        ts = (self.NOW - dt.timedelta(hours=10)).isoformat()
        self.assertEqual(schema.classify_freshness(ts, self.NOW), "CURRENT")
        self.assertEqual(schema.classify_market_freshness(ts, None, self.NOW)["state"], "STALE")   # the odds rule would fail it
        from dashboard import components as comp
        text = " ".join(comp.cloud_banner_model(
            {"state": "CURRENT", "data_as_of": ts, "last_updated": ts, "source": "REMOTE",
             "components": {"postmortem": ts, "settlement": ts}}, {})["lines"])
        self.assertNotIn("MARKET FRESHNESS", text)                          # no odds/recs -> no market line
        self.assertIn("post-mortem", text)

    def test_banner_reports_market_freshness_separately(self):
        from dashboard import components as comp
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).isoformat()
        model = comp.cloud_banner_model({"state": "CURRENT", "data_as_of": old, "last_updated": "2026-09-25T00:00:00Z",
                                         "source": "REMOTE", "components": {"odds": old}}, {})
        self.assertIn("MARKET FRESHNESS (separate from snapshot freshness): odds STALE", " ".join(model["lines"]))

    def test_market_freshness_is_at_least_as_strict_as_the_snapshot_state(self):
        from dashboard import components as comp
        row = {"captured_at_utc": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)).isoformat()}
        with mock.patch.object(comp, "live_data_state", return_value="STALE"):
            self.assertEqual(comp.market_freshness(row)["state"], "STALE")
        with mock.patch.object(comp, "live_data_state", return_value="CURRENT"):
            self.assertEqual(comp.market_freshness(row)["state"], "CURRENT")


# --------------------------------------------------------------------------------------- auth
class TestCloudAuthenticationModel(unittest.TestCase):
    def _run(self, page, *, email, role_env=None, trust="ON", admins="", cloud=True):
        from streamlit.testing.v1 import AppTest
        from dashboard import auth
        from operational import auth_store
        env = {"NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED", "NHL_ENGINE_TRUST_PLATFORM_VIEWER": trust,
               "NHL_ENGINE_ADMIN_EMAILS": admins}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env), \
             mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "a.db"), \
             mock.patch.object(rm, "current_mode",
                               return_value=rm.COMMUNITY_CLOUD_MODE if cloud else rm.LOCAL_MODE), \
             mock.patch.object(auth, "platform_viewer_email", return_value=email):
            at = AppTest.from_file(str(REPO / "dashboard" / page), default_timeout=120)
            at.run()
        return at

    def test_a_trusted_platform_viewer_becomes_a_user_with_no_login_or_bootstrap_form(self):
        at = self._run("app.py", email="friend@example.com")
        self.assertEqual(list(at.exception), [])
        login_fields = {"Username", "Password", "Admin username", "Confirm password", "Setup code"}
        self.assertFalse(login_fields & {t.label for t in at.text_input}, "no login/bootstrap form is shown")
        self.assertTrue(any("friend@example.com" in c.value and "(USER)" in c.value for c in at.sidebar.caption))

    def test_only_listed_emails_become_admin(self):
        at = self._run("app.py", email="owner@example.com", admins="Owner@Example.com, other@x.com")
        self.assertTrue(any("(ADMIN)" in c.value for c in at.sidebar.caption))
        at = self._run("app.py", email="friend@example.com", admins="owner@example.com")
        self.assertTrue(any("(USER)" in c.value for c in at.sidebar.caption))

    def test_no_admin_list_means_nobody_is_admin_through_the_platform(self):
        at = self._run("app.py", email="owner@example.com", admins="")
        self.assertTrue(any("(USER)" in c.value for c in at.sidebar.caption))

    def test_without_the_explicit_trust_declaration_the_platform_identity_is_ignored(self):
        at = self._run("app.py", email="friend@example.com", trust="")
        self.assertFalse(any("friend@example.com" in c.value for c in at.sidebar.caption))
        self.assertGreater(len(at.text_input) + len(at.error), 0, "falls back to the custom gate")

    def test_a_trusted_flag_with_no_platform_email_falls_back_to_the_custom_gate_never_to_admin(self):
        at = self._run("app.py", email=None)
        self.assertFalse(any("(ADMIN)" in c.value for c in at.sidebar.caption))
        self.assertFalse(any("Signed in" in c.value for c in at.sidebar.caption))

    def test_local_mode_ignores_platform_identity_entirely(self):
        at = self._run("app.py", email="friend@example.com", cloud=False)
        self.assertFalse(any("friend@example.com" in c.value for c in at.sidebar.caption))

    def _page_as(self, page, role):
        """Run one page directly with an explicit signed-in role (tests the page's own server-side guard)."""
        from streamlit.testing.v1 import AppTest
        from operational import auth_store
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED"}), \
             mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "a.db"), \
             mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            at = AppTest.from_file(str(REPO / "dashboard" / "pages" / page), default_timeout=120)
            at.session_state["_auth_username"] = "t"; at.session_state["_auth_role"] = role
            at.run()
        return at

    def test_a_platform_user_cannot_reach_diagnostics_or_yahoo(self):
        from dashboard import page_registry
        for name in ("37_Diagnostics.py", "34_Fantasy_HQ.py", "35_Fantasy_Settings.py"):
            self.assertFalse(page_registry.is_registered(name, "USER", rm.COMMUNITY_CLOUD_MODE))
        at = self._page_as("37_Diagnostics.py", "USER")
        self.assertTrue(any("restricted to the administrator" in e.value for e in at.error))
        self.assertEqual(len(at.metric), 0)
        self.assertGreater(len(self._page_as("37_Diagnostics.py", "ADMIN").metric), 0)
        for page in ("34_Fantasy_HQ.py", "35_Fantasy_Settings.py"):
            at = self._page_as(page, "USER")
            self.assertTrue(any("restricted to the administrator" in e.value for e in at.error), page)

    def test_morning_review_and_data_status_are_admin_only_in_cloud_mode_only(self):
        from dashboard import page_registry
        for name in ("36_Morning_Review.py", "9_Data_Status.py"):
            self.assertFalse(page_registry.is_registered(name, "USER", rm.COMMUNITY_CLOUD_MODE), name)
            self.assertTrue(page_registry.is_registered(name, "ADMIN", rm.COMMUNITY_CLOUD_MODE), name)
            self.assertTrue(page_registry.is_registered(name, "USER", rm.LOCAL_MODE), f"{name}: local unchanged")
            at = self._page_as(name, "USER")
            self.assertTrue(any("restricted to the administrator" in e.value for e in at.error), name)
            self.assertNotIn("Scoreboard", " ".join(m.value for m in at.markdown))
            at = self._page_as(name, "ADMIN")
            self.assertEqual([e.value for e in at.error if "restricted" in e.value], [], name)
            self.assertEqual(list(at.exception), [], name)

    def test_a_platform_user_sees_the_betting_pages(self):
        from dashboard import page_registry
        titles = {p.title for specs in page_registry.pages_for("USER", rm.COMMUNITY_CLOUD_MODE).values() for p in specs}
        for t in ("Today", "Game Detail", "Paper Performance", "Player Props"):
            self.assertIn(t, titles)

    def test_the_setup_code_never_appears_in_the_ui(self):
        from streamlit.testing.v1 import AppTest
        from operational import auth_store
        code = "SUPER-SECRET-SETUP-CODE-9876"
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"NHL_ENGINE_ADMIN_SETUP_CODE": code, "NHL_ENGINE_SNAPSHOT_SOURCE": "BUNDLED",
                                          "NHL_ENGINE_TRUST_PLATFORM_VIEWER": ""}), \
             mock.patch.object(auth_store, "DEFAULT_DB_PATH", Path(tmp) / "a.db"), \
             mock.patch.object(rm, "current_mode", return_value=rm.COMMUNITY_CLOUD_MODE):
            at = AppTest.from_file(str(REPO / "dashboard" / "app.py"), default_timeout=60)
            at.run()
            for t in at.text_input:
                t.input("wrong")
            at.button[0].click().run()
            everything = " ".join([e.value for e in at.error] + [m.value for m in at.markdown] +
                                  [c.value for c in at.caption] + [str(t.value) for t in at.text_input])
        self.assertNotIn(code, everything)


# --------------------------------------------------------------------------------------- render / memory
_REMOTE_PROBE = r'''
import os, sys, json, sqlite3, subprocess, urllib.request, pathlib, tempfile, hashlib
REPO, URL, FORBIDDEN = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
os.environ["NHL_ENGINE_RUNTIME_MODE"] = "COMMUNITY_CLOUD_MODE"
os.environ["NHL_ENGINE_SNAPSHOT_URL"] = URL
os.environ["NHL_ENGINE_SNAPSHOT_SOURCE"] = "REMOTE"
os.chdir(REPO); sys.path.insert(0, REPO)
WRITE = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "VACUUM")
writes, urls, procs = [], [], []
_rc = sqlite3.connect
def spy(db, *a, **k):
    c = _rc(db, *a, **k); n = str(db)
    if n != ":memory:" and "probe_auth" not in n:
        c.set_trace_callback(lambda s, n=os.path.basename(n): writes.append(f"{n}:{s.strip()[:40]}") if s.strip().upper().startswith(WRITE) else None)
    return c
sqlite3.connect = spy
import requests
requests.Session.request = lambda *a, **k: (urls.append("REQUESTS-LIB"), (_ for _ in ()).throw(RuntimeError("blocked")))[1]
_ru = urllib.request.urlopen
def uo(req, *a, **k):
    urls.append(getattr(req, "full_url", str(req)))
    return _ru(req, *a, **k)
urllib.request.urlopen = uo
_rp = subprocess.Popen
def po(*a, **k):
    cmd = a[0] if a else k.get("args")
    if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["ps", "-o", "rss="]:
        return _rp(*a, **k)
    procs.append(str(cmd)[:60]); raise RuntimeError("subprocess blocked")
subprocess.Popen = po
from operational import auth_store
auth_store.DEFAULT_DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "probe_auth.db"
from streamlit.testing.v1 import AppTest
from dashboard import page_registry, diagnostics_view
def rss():
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return round(int(out.strip()) / 1024, 1)
state = {"2_Game_Detail.py": {"selected_game_id": "demo-EDM-COL"}, "25_Player_Intelligence.py": {"selected_player_id": "8478402"},
         "31_Team_Intelligence.py": {"selected_team": "EDM"}}
exceptions, per_page = {}, {}
base = rss()
for f in [p.file for specs in page_registry.pages_for("ADMIN").values() for p in specs]:
    at = AppTest.from_file(f"{REPO}/dashboard/pages/{f}", default_timeout=120)
    at.session_state["_auth_username"] = "probe"; at.session_state["_auth_role"] = "ADMIN"
    for k, v in state.get(f, {}).items(): at.session_state[k] = v
    at.run()
    if at.exception: exceptions[f] = [e.value[:100] for e in at.exception]
    per_page[f] = rss()
    if f == "21_Today.py":
        today_rss = rss(); today_html = " ".join(m.value for m in at.markdown)
print("RESULT " + json.dumps({
    "exceptions": exceptions, "writes": writes, "urls": sorted(set(urls)), "procs": procs, "base_rss": base,
    "today_rss": today_rss, "max_rss": max(per_page.values()),
    "forbidden_present": [m for m in FORBIDDEN if m in sys.modules],
    "banner": 'data-testid="cloud-snapshot-banner"' in today_html, "banner_headline_current": "SNAPSHOT CURRENT" in today_html,
    "diag": diagnostics_view.process_diagnostics()["snapshot"]}))
'''


class TestRemoteRenderAndMemoryRegression(ReaderCase):
    """Every Cloud page, reading the snapshot over REAL HTTP from a local server, in a clean process."""

    def _probe(self, doc):
        self.serve(doc)
        from tests.test_streamlit_cloud_mode import FORBIDDEN_MODULES
        env = {k: v for k, v in os.environ.items() if k != rm.ENV_VAR}
        proc = subprocess.run([sys.executable, "-c", _REMOTE_PROBE, str(REPO), self.url, json.dumps(list(FORBIDDEN_MODULES))],
                              capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO))
        line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
        if line is None:
            raise AssertionError(f"probe failed:\n{proc.stdout[-1000:]}\n{proc.stderr[-2000:]}")
        return json.loads(line[7:])

    def test_fresh_snapshot_renders_everywhere_with_the_only_network_call_being_the_snapshot(self):
        r = self._probe(_bundled_backed_doc(1.0))
        self.assertEqual(r["exceptions"], {})
        self.assertEqual(r["urls"], [self.url], "the snapshot fetch is the ONLY network call a Cloud render may make")
        self.assertEqual(r["writes"], [])
        self.assertEqual(r["procs"], [])
        self.assertEqual(r["forbidden_present"], [])
        self.assertTrue(r["banner"] and r["banner_headline_current"])
        self.assertEqual(r["diag"]["snapshot_source"], "REMOTE")
        self.assertEqual(r["diag"]["freshness"], "CURRENT")

    def test_memory_regression_guard_the_remote_reader_did_not_undo_the_memory_work(self):
        """Previous certified results: Today ~157 MB, heaviest ~205 MB (in-process AppTest ~146/205).
        Generous ceilings here catch a real regression (e.g. the model stack coming back, ~600+ MB)
        without being flaky on machine differences."""
        r = self._probe(_bundled_backed_doc(1.0))
        self.assertLess(r["today_rss"], 250, f"Today RSS regressed: {r['today_rss']} MB")
        self.assertLess(r["max_rss"], 400, f"heaviest-page RSS regressed: {r['max_rss']} MB")

    def test_a_stale_snapshot_renders_with_the_stale_banner_everywhere(self):
        r = self._probe(_bundled_backed_doc(30.0))
        self.assertEqual(r["exceptions"], {})
        self.assertEqual(r["diag"]["freshness"], "STALE")
        self.assertFalse(r["banner_headline_current"])

    def test_a_remote_outage_still_renders_from_the_bundled_fallback_and_says_so(self):
        self.serve(status=404, body=b"nope")
        from tests.test_streamlit_cloud_mode import FORBIDDEN_MODULES
        env = {k: v for k, v in os.environ.items() if k != rm.ENV_VAR}
        proc = subprocess.run([sys.executable, "-c", _REMOTE_PROBE, str(REPO), self.url, json.dumps(list(FORBIDDEN_MODULES))],
                              capture_output=True, text=True, timeout=600, env=env, cwd=str(REPO))
        r = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))[7:])
        self.assertEqual(r["exceptions"], {})
        self.assertEqual(r["diag"]["snapshot_source"], "BUNDLED_FALLBACK")
        self.assertEqual(r["diag"]["remote_fetch_status"], "FAILED")
        self.assertEqual(r["diag"]["last_error"], "HTTP 404")
        self.assertFalse(r["banner_headline_current"])


class TestCloudDoesNotRunTheEngine(unittest.TestCase):
    def test_reader_and_snapshot_modules_import_no_research_or_model_code(self):
        code = ("import sys; sys.path.insert(0, %r);"
                "import dashboard.snapshot_source, operational.cloud_snapshot_schema;"
                "bad=[m for m in sys.modules if m.startswith(('research','models','pricing','features','ingest','fantasy'))];"
                "print('BAD', bad)") % str(REPO)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO)).stdout
        self.assertIn("BAD []", out)

    def test_cloud_pages_never_import_the_publisher_or_builder(self):
        for page in (REPO / "dashboard").rglob("*.py"):
            text = page.read_text()
            self.assertNotIn("publish_cloud_snapshot", text, page.name)
            self.assertNotIn("cloud_snapshot_builder", text, page.name)


if __name__ == "__main__":
    unittest.main()
