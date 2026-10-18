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
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from operational import moneyline_pregame as mp
from operational import state_paths as _sp

STATE_PATH = _sp.path("keep_awake_state.json")
LEAD_BEFORE_WINDOW_MIN = 30           # assertion starts this long before the capture window opens (before T-40)
TAIL_AFTER_ANCHOR_MIN = 20            # ... and lasts this long after the T-30 decision (covers the cloud publish)
ASSERT_LEAD_MIN = 10                  # the assertion may start this much EARLIER than hold_from, so a scheduled wake that lands a
                                      # few minutes before the hold window is held immediately (no idle-sleep gap after the wake)
WAKE_BEFORE_HOLD_MIN = 5              # suggested one-time wake: hold_from - 5 min  (= T-75; 50 min before the T-35 target)
PLAN_HORIZON_H = 72
GUARD_POLL_S = 5                      # wake-guard poll period: after a wake it reacts within ~5 s, far inside the 1-minute idle timer
GUARD_MAX_AGE_H = 36
AC_BRIDGE_H = 10                      # on AC power the Mac is held awake through to the next window when that window opens within this many hours (e.g. from late evening to the 07:45 refresh)
BATTERY_FLOOR_PCT = 30                # on battery an assertion is held only inside a window and only above this charge: an empty battery shuts the Mac down, which is worse than sleeping


def windows(now: dt.datetime, starts: list, horizon_h: float = PLAN_HORIZON_H, listing_fn: Callable | None = None) -> list[dict]:
    """The moments the machine must be awake, from the day's own capture timeline (operational/capture_timeline.py), per Eastern day with games:

      prep:<day>      the 07:45 ET refresh and the 08:00 ET analysis and league pull (earlier on an early day): [refresh - 10 min, analysis + 25 min]
      wave<i>:<day>   one coordinated player-odds capture per start-time wave: [target - 10 min, last chance + 20 min], i.e. from 100 minutes before the wave's first game until after its
                      capture could still be made and published

    (Until 2026-10-17 these were built from the retired T-35 per-cluster pulls and the retired prop sweeps; nothing held the Mac awake for the waves.) `listing_fn` is accepted for
    compatibility and ignored: a wave's game list comes from the schedule, and a priced-or-not decision is the credit plan's, not the power plan's."""
    from operational import capture_timeline as ct, eastern_time as et
    out: list[dict] = []
    by_day: dict[str, dict[str, dt.datetime]] = {}
    for i, t in enumerate(sorted(starts)):
        by_day.setdefault(et.eastern_today(t), {})[f"{i}"] = t
    for day, day_starts in sorted(by_day.items()):
        prep = ct.prep_times(dt.date.fromisoformat(day), min(day_starts.values()))
        rows = [{"cluster": f"prep:{day}", "games": len(day_starts), "lo": prep["refresh_at"] - dt.timedelta(minutes=10), "hi": prep["analysis_at"] + dt.timedelta(minutes=25),
                 "capture_window": (prep["refresh_at"], prep["analysis_at"]), "anchor": prep["analysis_at"]}]
        for w in ct.waves(day_starts):
            rows.append({"cluster": f"wave{w.index}:{day}", "games": len(w.games), "lo": w.target - dt.timedelta(minutes=10), "hi": w.last_chance + dt.timedelta(minutes=TAIL_AFTER_ANCHOR_MIN),
                         "capture_window": (w.target, w.last_chance), "anchor": w.deadline})
        for r in rows:
            if r["hi"] < now or r["lo"] > now + dt.timedelta(hours=horizon_h):
                continue
            out.append({"cluster": r["cluster"], "games": r["games"], "hold_from_utc": r["lo"], "hold_until_utc": r["hi"], "assert_from_utc": r["lo"] - dt.timedelta(minutes=ASSERT_LEAD_MIN),
                        "capture_window": r["capture_window"], "anchor": r["anchor"]})
    return sorted(out, key=lambda w: w["hold_from_utc"])


def active_window(now: dt.datetime, starts: list) -> dict | None:
    for w in windows(now, starts, horizon_h=1):
        if w["assert_from_utc"] <= now <= w["hold_until_utc"]:
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


def battery_state(runner: Callable | None = None) -> dict:
    """{"on_ac": bool, "percent": int | None} from `pmset -g batt` (read-only). An unreadable answer is treated as AC with unknown charge (the permissive reading the old code had)."""
    text = (runner or _run)(["pmset", "-g", "batt"])
    if not text:
        return {"on_ac": True, "percent": None, "known": False}
    m = re.search(r"(\d{1,3})%", text)
    return {"on_ac": "AC Power" in text, "percent": int(m.group(1)) if m else None, "known": True}


def hold_target(now: dt.datetime, ws: list[dict], power: dict) -> dict | None:
    """Which window (if any) the machine should be held awake for right now, and why.
      * inside a window: hold to its end, unless on battery below BATTERY_FLOOR_PCT;
      * on AC and the next window opens within AC_BRIDGE_H: hold now, through that window (the Mac cannot be woken by software, so the bridge is what keeps it awake overnight);
      * on battery outside a window: nothing (sleeping saves the charge; the watchdog says the windows cannot be guaranteed)."""
    inside = next((w for w in ws if w["assert_from_utc"] <= now <= w["hold_until_utc"]), None)
    on_ac, pct = power.get("on_ac", True), power.get("percent")
    if inside is not None:
        if not on_ac and pct is not None and pct < BATTERY_FLOOR_PCT:
            return {"skip": "BATTERY_TOO_LOW", "window": inside}
        return {"window": inside, "why": "IN_WINDOW"}
    if on_ac:
        nxt = next((w for w in ws if w["assert_from_utc"] > now), None)
        if nxt is not None and nxt["assert_from_utc"] - now <= dt.timedelta(hours=AC_BRIDGE_H):
            return {"window": nxt, "why": "AC_BRIDGE_TO_NEXT_WINDOW"}
    return None


def ensure_holding(now: dt.datetime | None = None, *, starts_fn: Callable | None = None,
                   spawn: Callable | None = None, alive_fn: Callable = _alive, state_path: Path | None = None,
                   active_fn: Callable[[], bool] | None = None, power_fn: Callable[[], dict] | None = None) -> dict:
    """Idempotent: at most one live assertion. Held inside a hold window, and on AC power also across the gap to the next window (see hold_target). Never raises."""
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
        ws = windows(now, starts, horizon_h=PLAN_HORIZON_H)
        power = (power_fn or battery_state)()
        target = hold_target(now, ws, power)
        state = _load(state_path)
        if target is None:
            out["reason"] = "OUTSIDE_HOLD_WINDOW" + ("" if power.get("on_ac", True) else "_ON_BATTERY")
            return out
        if target.get("skip"):
            out.update(action="SKIPPED", reason=target["skip"], power=power)
            return out
        w = target["window"]
        if alive_fn(state.get("pid")) and state.get("cluster") == w["cluster"]:
            out.update(holding=True, action="ALREADY_HOLDING", pid=state.get("pid"), until=state.get("until_utc"))
            return out
        seconds = int((w["hold_until_utc"] - now).total_seconds())
        if seconds <= 0:
            out["reason"] = "WINDOW_ENDED"
            return out
        pid = spawn(seconds)
        state.update(pid=pid, cluster=w["cluster"], started_utc=now.isoformat(), until_utc=w["hold_until_utc"].isoformat(), why=target["why"])
        _save(state, state_path)
        out.update(holding=True, action="STARTED", pid=pid, until=w["hold_until_utc"].isoformat(), seconds=seconds, why=target["why"], power=power)
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


def wake_time(w: dict) -> dt.datetime:
    return w["hold_from_utc"] - dt.timedelta(minutes=WAKE_BEFORE_HOLD_MIN)


def pmset_wake_command(when_utc: dt.datetime, owner: str = "nhl-engine") -> str:
    """Syntax verified against the installed `man pmset` (SCHEDULED EVENT ARGUMENTS): one-time events are
    `pmset schedule <type> "MM/dd/yy HH:mm:ss" [owner]` with a 24-hour LOCAL time and a TWO-digit year; type `wake`
    wakes a sleeping Mac, `wakeorpoweron` also powers on a shut-down one. pmset must run as root."""
    local = when_utc.astimezone()
    return f'sudo pmset schedule wake "{local:%m/%d/%y %H:%M:%S}" {owner}'


def wake_commands(now: dt.datetime, starts: list, limit: int = 5, horizon_h: float = PLAN_HORIZON_H) -> list[str]:
    """Exact privileged commands the OWNER may run -- this code never runs them (see schedule_next_wake --apply)."""
    cmds = []
    for w in windows(now, starts, horizon_h=horizon_h)[:limit]:
        t = wake_time(w)
        if t > now:
            cmds.append(f'{pmset_wake_command(t)}   # cluster {w["cluster"]}')
    return cmds


_SCHED_RE = re.compile(r"\[(\d+)\]\s+(wake|poweron|wakepoweron|sleep|shutdown)\s+at\s+(\d\d)/(\d\d)/(\d{2,4})\s+(\d\d):(\d\d):(\d\d)\s+by\s+'([^']*)'")


def parse_sched(text: str) -> list[dict]:
    """Parse `pmset -g sched` ("Scheduled power events:" lines). Times are LOCAL."""
    out = []
    for m in _SCHED_RE.finditer(text):
        idx, kind, mo, d, y, hh, mm, ss, owner = m.groups()
        year = int(y) + (2000 if len(y) == 2 else 0)
        try:
            when = dt.datetime(year, int(mo), int(d), int(hh), int(mm), int(ss)).astimezone()
        except ValueError:
            continue
        out.append({"type": kind, "when_utc": when.astimezone(dt.timezone.utc), "owner": owner})
    return out


def scheduled_wakes(runner: Callable = _run) -> list[dict]:
    """The OS's own list of scheduled power events (read from pmset, never trusting a command's exit code)."""
    return [e for e in parse_sched(runner(["pmset", "-g", "sched"])) if e["type"] in ("wake", "poweron", "wakepoweron")]


OUR_WAKE_OWNER = "nhl-engine"


def wake_covers(window: dict, events: list[dict], ours_only: bool = False) -> dict | None:
    """An event covers a cluster if the Mac will be awake in time for the assertion to take over: the event lies in
    [assert_from - 20 min, capture window opens (T-40)] -- early enough to be ready, late enough that the wake-guard
    holds it before the idle timer can put it back to sleep."""
    lo = window["assert_from_utc"] - dt.timedelta(minutes=20)
    hi = window["capture_window"][0]
    for e in events:
        if ours_only and not str(e.get("owner", "")).startswith(OUR_WAKE_OWNER):
            continue                # a wake the OS or another app scheduled for its own reasons (a Do Not Disturb timer, an analytics task) can vanish and is not our commitment
        if lo <= e["when_utc"] <= hi:
            return e
    return None


# ---- wake guard: closes the wake -> assertion gap --------------------------------------------------------------------
def _guard_state_path() -> Path:
    return _sp.path("keep_awake_guard.json")


def guard_alive(state_path: Path | None = None, alive_fn: Callable = _alive) -> bool:
    st = _load(state_path or _guard_state_path())
    return bool(st.get("pid")) and alive_fn_guard(st["pid"], alive_fn)


def alive_fn_guard(pid, alive_fn: Callable = _alive) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def ensure_guard(now: dt.datetime | None = None, *, starts_fn: Callable | None = None, spawn: Callable | None = None,
                 alive_fn: Callable = alive_fn_guard, state_path: Path | None = None, active_fn: Callable[[], bool] | None = None) -> dict:
    """Called by the 2-minute job while the Mac is awake: if a provider-listed hold window ends within the next
    GUARD_MAX_AGE_H hours, make sure ONE tiny detached guard process exists. The guard survives system sleep
    (it is merely suspended) and, the moment the Mac wakes, notices it is inside an assertion window and starts
    `caffeinate -i -t` within ~5 s -- before the 1-minute idle timer can put the Mac back to sleep. That is the
    smallest non-root closure of the wake->assertion gap (the 2-minute launchd timer alone can be up to 2 minutes
    late after a wake). It exits by itself after the last window."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"guard": "NONE", "reason": None}
    try:
        if spawn is None:
            if _sp.under_test():
                out.update(guard="SKIPPED", reason="UNDER_TEST")
                return out
            spawn = _spawn_guard
        if active_fn is None:
            from operational import deployment_mode as dm
            active_fn = dm.is_active_scheduler
        if not active_fn():
            out.update(guard="SKIPPED", reason="STANDBY")
            return out
        starts = (starts_fn or (lambda n: mp.scheduled_starts(n, hours=GUARD_MAX_AGE_H, back_hours=1)))(now)
        ws = windows(now, starts, horizon_h=GUARD_MAX_AGE_H)
        if not ws:
            out["reason"] = "NO_LISTED_WINDOW_AHEAD"
            return out
        path = state_path or _guard_state_path()
        st = _load(path)
        if st.get("pid") and alive_fn(st["pid"]):
            out.update(guard="ALREADY_RUNNING", pid=st["pid"])
            return out
        pid = spawn()
        _save({"pid": pid, "started_utc": now.isoformat()}, path)
        out.update(guard="STARTED", pid=pid)
    except Exception as exc:  # noqa: BLE001
        out.update(guard="ERROR", reason=f"{type(exc).__name__}: {str(exc)[:100]}")
    return out


def _spawn_guard() -> int:
    proc = subprocess.Popen([sys.executable, "-m", "operational.keep_awake", "--guard"], cwd=str(Path(__file__).resolve().parent.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return proc.pid


def guard_loop(*, clock: Callable = lambda: dt.datetime.now(dt.timezone.utc), sleep: Callable = None,
               max_iterations: int | None = None, ensure: Callable | None = None, starts_fn: Callable | None = None) -> str:
    """The guard's body (injectable for tests). Polls every GUARD_POLL_S; refreshes the window list every 60 s;
    calls ensure_holding() (idempotent: one live assertion) whenever a window is active; exits when no window
    remains ahead or after GUARD_MAX_AGE_H."""
    import time as _time
    sleep = sleep or _time.sleep
    ensure = ensure or ensure_holding
    started = clock()
    ws: list[dict] = []
    last_refresh = None
    last_ensure = None
    i = 0
    while max_iterations is None or i < max_iterations:
        i += 1
        now = clock()
        if now - started > dt.timedelta(hours=GUARD_MAX_AGE_H):
            return "MAX_AGE"
        if last_refresh is None or now - last_refresh >= dt.timedelta(seconds=60) or now < last_refresh:
            starts = (starts_fn or (lambda n: mp.scheduled_starts(n, hours=GUARD_MAX_AGE_H, back_hours=1)))(now)
            ws = windows(now, starts, horizon_h=GUARD_MAX_AGE_H)
            last_refresh = now
        if not ws or all(w["hold_until_utc"] < now for w in ws):
            return "NO_WINDOW_AHEAD"
        inside = any(w["assert_from_utc"] <= now <= w["hold_until_utc"] for w in ws)
        if inside or last_ensure is None or now - last_ensure >= dt.timedelta(seconds=60) or now < last_ensure:
            ensure(now)                                 # inside a window every poll (reacts within seconds of a wake); otherwise once a minute (the AC bridge to the next window)
            last_ensure = now
        sleep(GUARD_POLL_S)
    return "MAX_ITERATIONS"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    now = dt.datetime.now(dt.timezone.utc)
    if "--guard" in argv:
        print(guard_loop())
        return 0
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
