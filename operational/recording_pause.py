"""
A pause switch for NEW automatic paper tickets.

While the file `recording_pause.json` (shared runtime directory) says `paused: true`, the 15-minute trader selects and records nothing new. Everything else is unaffected:
odds collection, publishing, settlement of existing tickets, the personal accounts and the Paper Parlay Builder. Existing tickets are never touched, and the $500 model book is
neither reset nor rewritten. The pause is a plain file so it takes effect on the next cycle and is easy to inspect; it is ended only by an explicit `resume`.

    python3 -m operational.recording_pause status
    python3 -m operational.recording_pause pause "reason"
    python3 -m operational.recording_pause resume
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from operational import state_paths

NAME = "recording_pause.json"


def _path():
    return state_paths.path(NAME)


def status() -> dict:
    """{"paused": bool, "since_utc", "reason", "set_by"}. A missing or unreadable file means NOT paused only when the file is absent; an unreadable file fails SAFE (paused)."""
    p = _path()
    if not p.exists():
        return {"paused": False, "since_utc": None, "reason": None, "set_by": None}
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"paused": True, "since_utc": None, "reason": "The pause file could not be read, so recording stays paused until it is fixed.", "set_by": "unreadable"}
    return {"paused": bool(doc.get("paused")), "since_utc": doc.get("since_utc"), "reason": doc.get("reason"), "set_by": doc.get("set_by")}


def is_paused() -> bool:
    return status()["paused"]


def pause(reason: str, *, now: dt.datetime | None = None, set_by: str = "owner request") -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = {"paused": True, "since_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": reason, "set_by": set_by}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(p)
    return doc


class ResumeRefused(RuntimeError):
    """Raised when someone tries to resume automatic recording before the owner has approved the active ticket policy."""


def resume(*, now: dt.datetime | None = None, require_policy_approval: bool = True) -> dict:
    """Ends the pause (removes the file). Returns the status that was in force. Refuses unless the owner has approved the ACTIVE ticket policy by its digest
    (operational/ticket_policy.py): the pause exists so that nothing restarts on rules the owner has not reviewed."""
    if require_policy_approval:
        from operational import ticket_policy
        if not ticket_policy.is_approved():
            d = ticket_policy.describe()
            raise ResumeRefused(f"automatic recording stays paused: the owner has not approved the active ticket policy ({d['policy_id']}, digest {d['digest']}). "
                                f"Review it with `python3 -m operational.ticket_policy show`.")
    prior = status()
    p = _path()
    if p.exists():
        p.unlink()
    return prior


def notice(st: dict | None = None) -> str | None:
    st = st or status()
    if not st["paused"]:
        return None
    since = f" since {st['since_utc']}" if st.get("since_utc") else ""
    return (f"Automatic paper tickets are PAUSED{since}: no new automatic ticket is being recorded while the losing-streak postmortem is reviewed. "
            f"Existing tickets still settle, prices still refresh, and personal accounts are unaffected. {st.get('reason') or ''}").strip()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "pause":
        print(json.dumps(pause(" ".join(sys.argv[2:]) or "paused"), indent=1))
    elif cmd == "resume":
        try:
            print(json.dumps({"was": resume()}, indent=1))
        except ResumeRefused as exc:
            print(str(exc))
            raise SystemExit(2)
    else:
        print(json.dumps(status(), indent=1))
