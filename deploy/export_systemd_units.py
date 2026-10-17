"""Generate systemd service + timer units from the launchd job definitions, for a Linux always-on host (docs/ALWAYS_ON_HOST.md).

Nothing is installed or started by this script: it only writes unit files into --out so they can be reviewed and copied to /etc/systemd/system on the new host.
The schedules are copied exactly: StartInterval -> OnUnitActiveSec (first run 2 minutes after boot), StartCalendarInterval -> OnCalendar. Calendar times are wall-clock times in the
HOST'S timezone, which must be America/Toronto (the same as this Mac): `timedatectl set-timezone America/Toronto`.

    python3 deploy/export_systemd_units.py --out /tmp/units --release-dir /opt/nhl_engine_release --python /opt/nhl-venv/bin/python
"""
from __future__ import annotations

import argparse
import glob
import plistlib
import shlex
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def definitions() -> dict[str, dict]:
    """{label: plist} from the repository copies (deploy/launchd first, then operational/)."""
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(REPO / "deploy" / "launchd" / "*.plist"))) + sorted(glob.glob(str(REPO / "operational" / "*.plist"))):
        d = plistlib.loads(Path(f).read_bytes())
        out.setdefault(d["Label"], d)
    return out


def _calendar(entry: dict) -> str:
    """One launchd calendar entry as a systemd OnCalendar expression (missing keys mean 'every')."""
    weekday = entry.get("Weekday")
    dow = "" if weekday is None else ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][int(weekday)] + " "
    month = "*" if entry.get("Month") is None else f"{int(entry['Month']):02d}"
    day = "*" if entry.get("Day") is None else f"{int(entry['Day']):02d}"
    hour = "*" if entry.get("Hour") is None else f"{int(entry['Hour']):02d}"
    minute = "00" if entry.get("Minute") is None else f"{int(entry['Minute']):02d}"
    return f"{dow}*-{month}-{day} {hour}:{minute}:00"


def units(label: str, plist: dict, *, release_dir: str, python: str, user: str, log_dir: str) -> dict[str, str]:
    name = label.removeprefix("com.nhlengine.")
    args = list(plist["ProgramArguments"])
    exe, rest = args[0], args[1:]
    exec_start = " ".join(shlex.quote(a) for a in [python] + rest)      # the interpreter path is the host's; the module and its arguments are copied unchanged
    service = (f"[Unit]\nDescription=NHL engine: {name} (from {label})\nAfter=network-online.target\nWants=network-online.target\n\n"
               f"[Service]\nType=oneshot\nUser={user}\nWorkingDirectory={release_dir}\nExecStart={exec_start}\n"
               f"StandardOutput=append:{log_dir}/{name}.log\nStandardError=append:{log_dir}/{name}.err.log\nTimeoutStartSec=1800\n")
    if plist.get("StartInterval"):
        sec = int(plist["StartInterval"])
        trigger = f"OnBootSec=120\nOnUnitActiveSec={sec}\n"
    else:
        cal = plist["StartCalendarInterval"]
        cal = cal if isinstance(cal, list) else [cal]
        trigger = "".join(f"OnCalendar={_calendar(e)}\n" for e in cal) + "Persistent=false\n"      # Persistent=false: a missed window is reported, never run late
    timer = f"[Unit]\nDescription=Timer for nhl-{name}\n\n[Timer]\n{trigger}AccuracySec=1s\nUnit=nhl-{name}.service\n\n[Install]\nWantedBy=timers.target\n"
    return {f"nhl-{name}.service": service, f"nhl-{name}.timer": timer}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--release-dir", default="/opt/nhl_engine_release")
    ap.add_argument("--python", default="/opt/nhl-venv/bin/python")
    ap.add_argument("--user", default="nhl")
    ap.add_argument("--log-dir", default="/var/log/nhl-engine")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    sys.path.insert(0, str(REPO))
    from operational import watchdog
    wanted = set(watchdog.EXPECTED_JOBS) | {"com.nhlengine.watchdog"}      # the jobs the watchdog expects to be loaded: exactly what runs on the Mac (retired definitions are not exported)
    for label, plist in sorted(definitions().items()):
        if label not in wanted:
            continue
        for fname, text in units(label, plist, release_dir=a.release_dir, python=a.python, user=a.user, log_dir=a.log_dir).items():
            (out / fname).write_text(text)
            written.append(fname)
    print(f"wrote {len(written)} unit files to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
