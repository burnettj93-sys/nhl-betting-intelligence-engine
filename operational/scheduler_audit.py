"""
Read-only launchd scheduler audit (Quota + Moneyline Activation block, 2026-09-25).

    python3 -m operational.scheduler_audit            # human report
    python3 -m operational.scheduler_audit --json

Lists every ~/Library/LaunchAgents/com.nhlengine.*.plist with its cadence, target, run count and last
exit code, and flags: duplicate jobs (same command), jobs whose target module/script is missing, jobs
pointing at another working directory, and jobs that are not loaded. It also reports the machine boot
time, because launchd does NOT replay a calendar slot that passed while the Mac was off and its `runs`
counter restarts at every boot -- which is why a 07:00 job can show `runs = 0` on a morning the Mac
booted at 07:08 (the 2026-09-25 daily-nhl-sync finding).

Never modifies launchd. macOS only; on other systems it reports UNAVAILABLE.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = Path(os.path.expanduser("~/Library/LaunchAgents"))
PREFIX = "com.nhlengine."

# Jobs that spend Odds API credits, and jobs that publish the cloud snapshot (documentation of intent;
# the tests assert the real code agrees).
PAID_JOBS = {"moneyline-snapshot", "moneyline-pregame", "daily-props-pull", "prop-sweep-first", "prop-sweep-second"}


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read_plist(path: Path, runner=_run) -> dict | None:
    out = runner(["plutil", "-convert", "json", "-o", "-", str(path)])
    try:
        return json.loads(out)
    except ValueError:
        return None


def cadence_of(plist: dict) -> str:
    if "StartCalendarInterval" in plist:
        sci = plist["StartCalendarInterval"]
        sci = sci if isinstance(sci, list) else [sci]
        return ", ".join(f"{d.get('Hour', '*')}:{int(d.get('Minute', 0)):02d}" for d in sci) + " local"
    if "StartInterval" in plist:
        return f"every {plist['StartInterval'] // 60} min" if plist["StartInterval"] >= 60 else f"every {plist['StartInterval']} s"
    return "on load / manual"


def command_of(plist: dict) -> str:
    args = plist.get("ProgramArguments") or []
    return " ".join(args[1:]) if len(args) > 1 else " ".join(args)


def target_exists(plist: dict) -> bool:
    args = plist.get("ProgramArguments") or []
    wd = Path(plist.get("WorkingDirectory") or REPO_ROOT)
    if len(args) >= 3 and args[1] == "-m":
        return (wd / (args[2].replace(".", "/") + ".py")).exists()
    if len(args) >= 2:
        return (wd / args[1]).exists()
    return False


def launchctl_info(label: str, runner=_run) -> dict:
    out = runner(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
    if not out:
        return {"loaded": False, "runs": None, "last_exit": None, "state": None}
    info = {"loaded": True, "runs": None, "last_exit": None, "state": None}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("runs ="):
            info["runs"] = int(line.split("=")[1])
        elif line.startswith("last exit code ="):
            info["last_exit"] = line.split("=", 1)[1].strip()
        elif line.startswith("job state ="):
            info["state"] = line.split("=", 1)[1].strip()
    return info


def job_loaded(label: str, runner=_run) -> bool:
    return launchctl_info(label, runner)["loaded"]


def boot_time(runner=_run) -> dt.datetime | None:
    out = runner(["sysctl", "-n", "kern.boottime"])
    if "sec =" in out:
        try:
            return dt.datetime.fromtimestamp(int(out.split("sec =")[1].split(",")[0]), dt.timezone.utc)
        except ValueError:
            return None
    return None


def zero_runs_explained_by_boot(plist: dict, runs: int | None, boot: dt.datetime | None,
                                now: dt.datetime | None = None) -> bool:
    """True when a calendar job shows `runs = 0` only because today's slot fell BEFORE the machine booted
    (launchd's counter restarts at boot and does not replay a missed slot) -- the 2026-09-25 daily-nhl-sync case."""
    if runs not in (0, None) or boot is None or "StartCalendarInterval" not in plist:
        return False
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone()
    boot_local = boot.astimezone(now.tzinfo)
    sci = plist["StartCalendarInterval"]
    sci = sci if isinstance(sci, list) else [sci]
    slots = [now.replace(hour=int(d.get("Hour", 0)), minute=int(d.get("Minute", 0)), second=0, microsecond=0) for d in sci]
    fired_possible = [t for t in slots if boot_local <= t <= now]
    missed = [t for t in slots if t < boot_local and t.date() == boot_local.date()]
    return bool(missed) and not fired_possible


def find_duplicates(jobs: list[dict]) -> list[dict]:
    """Two loaded/installed jobs that run the same command are duplicates (double execution, double spend)."""
    seen: dict[str, str] = {}
    dups = []
    for j in jobs:
        key = j["command"]
        if key in seen:
            dups.append({"command": key, "jobs": [seen[key], j["label"]]})
        else:
            seen[key] = j["label"]
    return dups


def audit(agents_dir: Path | None = None, runner=_run) -> dict:
    if sys.platform != "darwin" and runner is _run:
        return {"status": "UNAVAILABLE", "reason": "launchd is macOS-only", "jobs": []}
    agents_dir = agents_dir or AGENTS_DIR
    jobs = []
    for path in sorted(glob.glob(str(agents_dir / f"{PREFIX}*.plist"))):
        plist = read_plist(Path(path), runner)
        if not plist:
            jobs.append({"label": Path(path).stem, "problem": "UNPARSEABLE_PLIST", "command": Path(path).stem})
            continue
        label = plist.get("Label", Path(path).stem)
        info = launchctl_info(label, runner)
        wd = plist.get("WorkingDirectory")
        problems = []
        if not info["loaded"]:
            problems.append("NOT_LOADED")
        if not target_exists(plist):
            problems.append("TARGET_MISSING")
        if wd and Path(wd).resolve() != REPO_ROOT.resolve():
            problems.append("OTHER_WORKING_DIRECTORY")
        short = label.replace(PREFIX, "")
        jobs.append({"label": label, "short": short, "cadence": cadence_of(plist), "command": command_of(plist),
                     "paid": short in PAID_JOBS, **info, "problems": problems})
    booted = boot_time(runner)
    dups = find_duplicates(jobs)
    problems = [f"{j['label']}: {','.join(j['problems'])}" for j in jobs if j.get("problems")]
    return {"status": "OK" if not dups and not problems else "ATTENTION", "job_count": len(jobs), "jobs": jobs,
            "duplicates": dups, "problems": problems, "boot_time_utc": booted.isoformat() if booted else None,
            "note": "launchd `runs` restarts at every boot and calendar slots missed while the Mac was off are not replayed."}


def main(argv=None) -> int:
    report = audit()
    if "--json" in (sys.argv[1:] if argv is None else argv):
        print(json.dumps(report, indent=2))
        return 0
    print(f"SCHEDULER AUDIT: {report['status']} ({report.get('job_count', 0)} jobs, boot {report.get('boot_time_utc')})")
    for j in report["jobs"]:
        print(f"  {j.get('short', j['label']):<26} {j.get('cadence', ''):<32} runs={j.get('runs')} "
              f"exit={j.get('last_exit')} paid={j.get('paid')} {','.join(j.get('problems') or [])}")
    for d in report.get("duplicates", []):
        print(f"  DUPLICATE: {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
