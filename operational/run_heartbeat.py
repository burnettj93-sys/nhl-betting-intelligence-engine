"""
A small append-only record of when a scheduled component actually started a run, so an outage can be told apart from sleep and from a quiet day.

`beat("real_parlay_paper_trader", now)` is called at the start of every trader cycle. `ran_between(component, a, b)` answers "did it start a run inside this window?" from that record.
Kept to the last KEEP entries per component; never raises.
"""
from __future__ import annotations

import datetime as dt
import json

from operational import state_paths

NAME = "run_heartbeats.json"
KEEP = 400


def _load() -> dict:
    try:
        return json.loads(state_paths.path(NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def beat(component: str, now: dt.datetime) -> None:
    try:
        doc = _load()
        rows = doc.get(component) or []
        rows.append(now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        doc[component] = rows[-KEEP:]
        p = state_paths.path(NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc))
        tmp.replace(p)
    except OSError:
        pass


def starts(component: str) -> list[dt.datetime]:
    return [dt.datetime.fromisoformat(x.replace("Z", "+00:00")) for x in _load().get(component) or []]


def ran_between(component: str, a: dt.datetime, b: dt.datetime) -> bool | None:
    """True/False whether a run started in [a, b]; None when the record does not reach back to `a` (it began after the window, so absence proves nothing)."""
    rows = starts(component)
    if not rows or rows[0] > a:
        return None
    return any(a <= t <= b for t in rows)


def last_before(component: str, t: dt.datetime) -> dt.datetime | None:
    prior = [x for x in starts(component) if x <= t]
    return max(prior) if prior else None
