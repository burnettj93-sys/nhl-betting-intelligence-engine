"""
Scheduler integration for Cloud snapshot publication (Cloud live-data sprint,
2026-09-25).

Called at the END of jobs that just produced materially new operational state
(a real moneyline/prop refresh, settlement, the morning post-mortem) -- it is
NOT a scheduled job of its own, and it is deliberately NOT called from the
30-minute pregame NHL refresh.

Guarantees:
  * OPT-IN: does nothing unless NHL_ENGINE_CLOUD_PUBLISH=ON (env or .env), so
    merging this code publishes nothing until the owner decides to.
  * Never raises and never changes the calling job's outcome: publication is
    downstream. A failure is returned (and recorded in ingestion health as
    cloud_snapshot_publish) but the engine work already done is untouched.
  * Bounded: runs the publisher in a SUBPROCESS with a hard timeout, so a
    stalled `git push` can never hang a scheduled job.
  * Respects STANDBY (a STANDBY machine never publishes).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 240


def publish_after(job_name: str, *, runner=None) -> dict:
    result = {"triggered_by": job_name, "status": "SKIPPED", "reason": "DISABLED"}
    try:
        from operational import deployment_mode, publish_cloud_snapshot as pub
        from operational import state_paths
        if runner is None and state_paths.under_test():
            # found 2026-09-25: with NHL_ENGINE_CLOUD_PUBLISH=ON in the real .env, unit tests that ran a job's
            # _main() launched the REAL publisher and pushed to GitHub. A test run never publishes for real;
            # tests exercise the hook with an injected `runner`.
            result["reason"] = "UNDER_TEST"
            return result
        if not pub.publishing_enabled():
            return result
        if not deployment_mode.is_active_scheduler():
            result["reason"] = "STANDBY"
            return result
        runner = runner or subprocess.run
        proc = runner([sys.executable, "-m", "operational.publish_cloud_snapshot"], cwd=REPO_ROOT,
                      capture_output=True, text=True, timeout=TIMEOUT_S)
        parsed = None
        try:
            parsed = json.loads(proc.stdout)
        except ValueError:
            pass
        if parsed is None:
            result.update(status="FAILED", reason=f"publisher exit {proc.returncode}: {(proc.stderr or proc.stdout)[-200:]}")
        else:
            result.update(status=parsed.get("status", "FAILED"), reason=parsed.get("reason", ""),
                          content_hash=parsed.get("content_hash"))
    except Exception as exc:  # noqa: BLE001 -- publication is downstream of the engine, never fatal
        result.update(status="FAILED", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    return result
