"""
Targeted keep-awake for the critical pregame window (First Live Prep block, 2026-09-25).

The audit found macOS on this machine has `sleep 1` on AC power (idle-sleep after ONE minute) and stays awake
only while some app holds a power assertion. A sleeping Mac runs no launchd job, so the T-35 pull would be missed.

What this module does (no privileges, no permanent change):
  * `windows()` computes, from the existing NHL schedule clusters, the moments the machine must be awake:
        hold window = [capture window start - 30 min,  decision anchor + 20 min]
    i.e. it starts before T-40 (T-70) and lasts until after the T-30 decision and the cloud publish.
  * `ensure_holding()` (called on every 2-minute `moneyline-pregame` firing) starts ONE detached
    `caffeinate -i -t <seconds>` -- an idle-sleep assertion that ends BY ITSELF at the end of the window --
    only while inside a hold window and no assertion of ours is alive. Outside windows it does nothing.
  * It does NOT prevent explicit sleep, shutdown, restart, a lid closing (macOS ignores idle assertions for
    clamshell sleep unless on AC with an external display), or a sleep that already happened before the window: a
    sleeping Mac cannot start an assertion. Waking a sleeping Mac needs a privileged scheduled wake, which is
    an OWNER action: `python3 -m operational.keep_awake --plan` prints the exact `sudo pmset schedule wake`
    commands for the upcoming clusters.

Temporal integrity is untouched: nothing here pulls, retries or back-fills. If the Mac still slept through the
capture window the cluster is recorded MISSED_WINDOW / MACHINE_ASLEEP exactly as before.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from operational import moneyline_pregame as mp
from operational import state_paths as _sp

STATE_PATH = _sp.path("keep_awake_state.json")
LEAD_BEFORE_WINDOW_MIN = 30           # assertion starts this long before the capture window opens (before T-40)
TAIL_AFTER_ANCHOR_MIN = 20            # ... and lasts this long after the T-30 decision (covers the cloud publish)
WAKE_LEAD_MIN = 15                    # suggested privileged wake: this long before the hold window
PLAN_HORIZON_H = 72


def windows(now: dt.datetime, starts: list, horizon_h: float = PLAN_HORIZON_H,
            listing_fn: Callable | None = None) -> list[dict]:
    """Hold windows for clusters the provider LISTS (a cluster it does not list gets no pull, so the machine need
    not be held awake for it). Unknown listing (no archived events) is treated as listed -- the safe side."""
    out = []
    listing_fn = listing_fn or mp.archived_listing
    for c in mp.plan_clusters(starts):
        if listing_fn(c) is False:
            continue
        lo = c.window[0] - dt.timedelta(minutes=LEAD_BEFORE_WINDOW_MIN)
        hi = c.anchor + dt.timedelta(minutes=TAIL_AFTER_ANCHOR_MIN)
        if hi < now or lo > now + dt.timedelta(hours=horizon_h):
            continue
        out.append({"cluster": c.key, "games": c.games, "hold_from_utc": lo, "hold_until_utc": hi,
                    "capture_window": c.window, "anchor": c.anchor})
    return out


def active_window(now: dt.datetime, starts: list) -> dict | None:
    for w in windows(now, starts, horizon_h=1):
        if w["hold_from_utc"] <= now <= w["hold_until_utc"]:
            return w
    return None


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:
        cmd = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "caffeinate" in cmd


def _load(path: Path | None = None) -> dict:
    try:
        return json.loads((path or STATE_PATH).read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def ensure_holding(now: dt.datetime | None = None, *, starts_fn: Callable | None = None,
                   spawn: Callable | None = None, alive_fn: Callable = _alive, state_path: Path | None = None,
                   active_fn: Callable[[], bool] | None = None) -> dict:
    """Idempotent: at most one live assertion, only inside a hold window. Never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"holding": False, "action": "NONE", "reason": None}
    try:
        if spawn is None:
            if _sp.under_test():
                out.update(action="SKIPPED", reason="UNDER_TEST")
                return out
            spawn = _spawn_caffeinate
        if active_fn is None:
            from operational import deployment_mode as dm
            active_fn = dm.is_active_scheduler
        if not active_fn():
            out.update(action="SKIPPED", reason="STANDBY")
            return out
        starts = (starts_fn or (lambda n: mp.scheduled_starts(n, hours=PLAN_HORIZON_H, back_hours=1)))(now)
        w = active_window(now, starts)
        state = _load(state_path)
        if w is None:
            out["reason"] = "OUTSIDE_HOLD_WINDOW"
            return out
        if alive_fn(state.get("pid")) and state.get("cluster") == w["cluster"]:
            out.update(holding=True, action="ALREADY_HOLDING", pid=state.get("pid"), until=state.get("until_utc"))
            return out
        seconds = int((w["hold_until_utc"] - now).total_seconds())
        if seconds <= 0:
            out["reason"] = "WINDOW_ENDED"
            return out
        pid = spawn(seconds)
        state.update(pid=pid, cluster=w["cluster"], started_utc=now.isoformat(), until_utc=w["hold_until_utc"].isoformat())
        _save(state, state_path)
        out.update(holding=True, action="STARTED", pid=pid, until=w["hold_until_utc"].isoformat(), seconds=seconds)
    except Exception as exc:  # noqa: BLE001 -- best effort; never affects the pull or the job
        out.update(action="ERROR", reason=f"{type(exc).__name__}: {str(exc)[:120]}")
    return out


def _spawn_caffeinate(seconds: int) -> int:
    """`-i` = prevent IDLE sleep only; `-t` = self-terminating after `seconds`. Detached from the launchd job."""
    proc = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-t", str(int(seconds))], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    return proc.pid


# ---- read-only power audit ------------------------------------------------------------------------------------
def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_pmset(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("sleep", "displaysleep", "disksleep", "powernap", "womp", "lowpowermode") and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
            if parts[0] == "sleep":
                out["sleep_prevented_by"] = line.split("prevented by", 1)[1].strip(" )") if "prevented by" in line else None
    return out


def power_risk(runner: Callable = _run) -> dict:
    """Read-only. Is the machine likely to idle-sleep through a capture window? (Makes no change.)"""
    settings = parse_pmset(runner(["pmset", "-g"]))
    ps = runner(["pmset", "-g", "ps"])
    on_ac = "AC Power" in ps
    sleep_min = settings.get("sleep")
    held_by = settings.get("sleep_prevented_by")
    if sleep_min is None:
        level, why = "UNKNOWN", "could not read pmset"
    elif sleep_min == 0:
        level, why = "LOW", "idle sleep is disabled system-wide"
    elif sleep_min <= 30:
        level, why = "HIGH", (f"idle-sleep after {sleep_min} min ({'on AC' if on_ac else 'on battery'}); the machine stays "
                              f"awake only while an app holds an assertion" + (f" (currently: {held_by})" if held_by else ""))
    else:
        level, why = "MEDIUM", f"idle-sleep after {sleep_min} min"
    return {"risk": level, "detail": why, "on_ac": on_ac, "idle_sleep_minutes": sleep_min, "display_sleep_minutes": settings.get("displaysleep"),
            "power_nap": bool(settings.get("powernap")), "wake_on_lan": bool(settings.get("womp")),
            "low_power_mode": bool(settings.get("lowpowermode")), "assertion_held_by": held_by,
            "mitigation": "the moneyline-pregame job holds a self-ending idle-sleep assertion inside each hold window (started before "
                          "T-40); it cannot wake a sleeping Mac, override lid-close, or stop an explicit sleep/shutdown"}


def wake_commands(now: dt.datetime, starts: list, limit: int = 5, horizon_h: float = PLAN_HORIZON_H) -> list[str]:
    """Exact privileged commands the OWNER may run (local time, MM/dd/yyyy HH:mm:ss) -- this code never runs them."""
    cmds = []
    for w in windows(now, starts, horizon_h=horizon_h)[:limit]:
        t = (w["hold_from_utc"] - dt.timedelta(minutes=WAKE_LEAD_MIN)).astimezone()
        if t > now.astimezone():
            cmds.append(f'sudo pmset schedule wake "{t:%m/%d/%Y %H:%M:%S}"   # cluster {w["cluster"]}')
    return cmds


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    now = dt.datetime.now(dt.timezone.utc)
    horizon = 240 if "--plan" in argv else PLAN_HORIZON_H
    starts = mp.scheduled_starts(now, hours=horizon, back_hours=1)
    if "--plan" in argv:
        print(json.dumps(power_risk(), indent=2))
        for w in windows(now, starts, horizon_h=horizon)[:8]:
            print(f"hold {w['hold_from_utc']:%Y-%m-%d %H:%MZ} .. {w['hold_until_utc']:%H:%MZ}  cluster {w['cluster']} ({w['games']} game(s))")
        print("\nOptional owner wake schedule (needs sudo; optional):")
        for c in wake_commands(now, starts, horizon_h=horizon):
            print("  " + c)
        return 0
    print(json.dumps(ensure_holding(now), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
