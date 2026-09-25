"""
Owner-approved scheduler for the NEXT one-time wake (Final Pre-Live Ops block, 2026-09-25).

    python3 -m operational.schedule_next_wake              # plan only: shows the next wake and the exact command
    python3 -m operational.schedule_next_wake --verify     # reads `pmset -g sched` and reports whether it is scheduled
    python3 -m operational.schedule_next_wake --apply      # runs it -- ONLY on the owner's explicit invocation

Why this exists: `caffeinate` (PR #10) keeps an AWAKE Mac awake; it cannot wake a sleeping one, and this Mac idle-sleeps
after ONE minute on AC. A one-time `pmset schedule wake` is the macOS-native way to be awake before the first
provider-listed T-35 window. `pmset` needs root, so:

  * the default is READ-ONLY (plan / verify);
  * `--apply` uses `sudo -n` (non-interactive). If sudo would ask for a password it FAILS CLOSED and prints the exact
    command for the owner to run in a terminal. This code never asks for, reads or stores a password;
  * success is judged from the OS's own scheduled-event listing, never from an exit code;
  * it never changes any pmset power setting (no global sleep/display changes), only adds one scheduled event.

Not covered (macOS limits, not code): a Mac that is SHUT DOWN (`wake` does nothing; `wakeorpoweron` is documented by
pmset but power-on-from-shutdown is not verifiable here and unreliable on Apple silicon), and LID-CLOSED operation --
a scheduled wake with the lid closed and no external display normally only "dark wakes" and drops back to sleep, so
the lid must be OPEN (or an external display attached) for the T-35 window.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from typing import Callable

from operational import keep_awake as ka
from operational import moneyline_pregame as mp

LID_NOTE = ("Leave the lid OPEN (or an external display attached) and the Mac on AC power: a scheduled wake with the lid closed "
            "normally only dark-wakes and returns to sleep, and neither pmset nor caffeinate can override closed-lid sleep. "
            "A Mac that is shut down is not woken by `pmset schedule wake`.")


def next_wake(now: dt.datetime | None = None, starts_fn: Callable | None = None, listing_fn: Callable | None = None) -> dict | None:
    now = now or dt.datetime.now(dt.timezone.utc)
    starts = (starts_fn or (lambda n: mp.scheduled_starts(n, hours=240, back_hours=0)))(now)
    for w in ka.windows(now, starts, horizon_h=240, listing_fn=listing_fn):
        t = ka.wake_time(w)
        if t > now:
            return {"window": w, "wake_utc": t, "command": ka.pmset_wake_command(t), "wake_local": t.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")}
    return None


def verify(now: dt.datetime | None = None, *, nxt: dict | None = None, runner: Callable = ka._run) -> dict:
    """Is a wake event that covers the next opportunity actually in the OS list?"""
    now = now or dt.datetime.now(dt.timezone.utc)
    nxt = nxt if nxt is not None else next_wake(now)
    if not nxt:
        return {"state": "WAITING_FOR_EVENT", "detail": "no provider-listed cluster ahead", "scheduled": None}
    events = ka.scheduled_wakes(runner)
    hit = ka.wake_covers(nxt["window"], events)
    if hit:
        return {"state": "SCHEDULED", "scheduled": True, "when_utc": hit["when_utc"].isoformat(), "type": hit["type"], "owner": hit["owner"],
                "covers_cluster": nxt["window"]["cluster"],
                "detail": f"{hit['type']} event {hit['when_utc'].astimezone():%Y-%m-%d %H:%M:%S %Z} (owner '{hit['owner']}') covers {nxt['window']['cluster']}"}
    return {"state": "OWNER_ACTION_REQUIRED", "scheduled": False, "command": nxt["command"],
            "detail": f"no scheduled wake covers {nxt['window']['cluster']}; run: {nxt['command']}"}


def apply(now: dt.datetime | None = None, *, runner: Callable | None = None) -> dict:
    """Owner-invoked only. `sudo -n` = never prompts; without cached/passwordless sudo it fails closed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    nxt = next_wake(now)
    if not nxt:
        return {"applied": False, "reason": "no provider-listed cluster ahead"}
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=30))
    cmd = ["sudo", "-n", "pmset", "schedule", "wake", nxt["wake_utc"].astimezone().strftime("%m/%d/%y %H:%M:%S"), "nhl-engine"]
    try:
        proc = run(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"applied": False, "reason": f"could not run sudo: {type(exc).__name__}", "run_this": nxt["command"]}
    if proc.returncode != 0:
        return {"applied": False, "reason": "root privileges are required and sudo needs a password (this tool never asks for one)",
                "run_this": nxt["command"]}
    v = verify(now, nxt=nxt)
    return {"applied": v["state"] == "SCHEDULED", "verified_in_os_listing": v["state"] == "SCHEDULED", "verification": v}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    now = dt.datetime.now(dt.timezone.utc)
    nxt = next_wake(now)
    if "--apply" in argv:
        print(json.dumps(apply(now), indent=2, default=str))
        return 0
    if not nxt:
        print("No provider-listed cluster within 10 days: nothing to schedule.")
        return 0
    w = nxt["window"]
    print(f"Next provider-listed cluster: {w['cluster']} ({w['games']} game(s))")
    print(f"  T-40 window opens {w['capture_window'][0].astimezone():%Y-%m-%d %H:%M %Z}  ·  T-35 target {(w['anchor'] - dt.timedelta(minutes=5)).astimezone():%H:%M %Z}"
          f"  ·  T-30 decision {w['anchor'].astimezone():%H:%M %Z}")
    print(f"  keep-awake assertion {w['assert_from_utc'].astimezone():%H:%M} .. {w['hold_until_utc'].astimezone():%H:%M %Z}")
    print(f"  proposed wake:        {nxt['wake_local']}  ({int((w['capture_window'][0] - nxt['wake_utc']).total_seconds() // 60)} min before the window opens)")
    print("\nRun this yourself (needs your password):\n  " + nxt["command"])
    print("\nOr let this tool try it non-interactively (fails closed if a password is needed):\n  python3 -m operational.schedule_next_wake --apply")
    print("\nConfirm it took effect (reads the OS's own list):\n  pmset -g sched\n  python3 -m operational.schedule_next_wake --verify")
    print("\n" + LID_NOTE)
    if "--verify" in argv:
        print(json.dumps(verify(now, nxt=nxt), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
