"""
P0.9 (2026-09-24 hardening block): explicit LOCAL_MODE / PRODUCTION_MODE
protection against two schedulers (a local Mac and a future VPS) both
writing at once. Every scheduled job's CLI entry point calls
is_active_scheduler() first and no-ops immediately if it returns False --
never a partial run, never a race with the other machine.

How this is used across a cutover (see docs/VPS_DEPLOYMENT_PREP.md):
  1. Before cutover: only the local Mac's .env has NHL_ENGINE_DEPLOYMENT_MODE
     unset (or ACTIVE) -- it's the only real scheduler, exactly today's
     state.
  2. Cutover day: set NHL_ENGINE_DEPLOYMENT_MODE=STANDBY in the LOCAL
     Mac's .env FIRST (its jobs immediately start no-op'ing, still
     loaded but harmless), verify the VPS's own .env has ACTIVE (or is
     simply unset, since ACTIVE is the default), confirm the VPS is
     producing real output, THEN (only after confirming) unload the
     local launchd jobs entirely -- STANDBY is a safety net for the
     transition window, not a permanent state to leave the local
     machine in.

Deliberately NOT a distributed lock, a heartbeat, or a database flag --
those add real failure modes of their own (a lock file that never gets
released, a heartbeat check that adds a new network dependency) for a
problem that a single environment variable, read fresh on every job
invocation, already solves completely for this project's actual scale
(one local machine, one future VPS, never both truly concurrent).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

ACTIVE, STANDBY = "ACTIVE", "STANDBY"
_ENV_VAR = "NHL_ENGINE_DEPLOYMENT_MODE"


def _load_dotenv_into_os_environ() -> None:
    if not ENV_PATH.exists():
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def current_mode() -> str:
    """Defaults to ACTIVE when unset -- today's real, single-machine
    state (no VPS exists yet) must keep working exactly as it does now
    without requiring anyone to set anything."""
    _load_dotenv_into_os_environ()
    value = (os.environ.get(_ENV_VAR) or "").strip().upper()
    return STANDBY if value == STANDBY else ACTIVE


def is_active_scheduler() -> bool:
    return current_mode() == ACTIVE


def require_active_scheduler_or_exit(job_name: str) -> bool:
    """Call at the very top of a scheduled job's CLI entry point (main()/
    _main()). Prints a clear, honest no-op message and returns False
    when this machine is in STANDBY -- the caller should return
    immediately without doing any real work. Returns True (do real
    work) otherwise."""
    if is_active_scheduler():
        return True
    print(f"{job_name}: this machine is in STANDBY mode "
          f"({_ENV_VAR}={STANDBY}) -- skipping to avoid a duplicate run "
          f"against the ACTIVE scheduler elsewhere. See docs/VPS_DEPLOYMENT_PREP.md.")
    return False
