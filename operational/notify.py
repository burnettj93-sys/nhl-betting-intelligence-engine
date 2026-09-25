"""
Zero-cost local notifications for the first live clusters (Final Pre-Live Ops block, 2026-09-25).

macOS Notification Center via `osascript` -- no email, no SMS, no paid service, no network. Strictly DOWNSTREAM and
NON-BLOCKING: the notification is a detached process the job never waits for; any failure is swallowed; nothing here
can change a pull, a decision or an audit record. Off under tests and when NHL_ENGINE_NOTIFY=OFF. Only clusters the
provider LISTS are announced (an unlisted preseason cluster is not news).
"""
from __future__ import annotations

import os
import subprocess

from operational import state_paths as _sp


def enabled() -> bool:
    return os.environ.get("NHL_ENGINE_NOTIFY", "ON").strip().upper() not in ("OFF", "0", "FALSE", "NO") and not _sp.under_test()


def _escape(text: str) -> str:
    return str(text).replace("\\\\", " ").replace('"', "'")[:220]


def send(title: str, message: str, *, spawn=None) -> bool:
    """Fire-and-forget. Returns True if a notification process was launched."""
    try:
        if spawn is None:
            if not enabled():
                return False
            spawn = lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        spawn(["osascript", "-e", f'display notification "{_escape(message)}" with title "{_escape(title)}"'])
        return True
    except Exception:  # noqa: BLE001 -- a notification must never affect the engine
        return False


def messages_for(result: dict, audited: list[dict]) -> list[tuple[str, str]]:
    """(title, message) pairs for the events worth telling the owner about: a T-35 pull SUCCESS, a pull FAILED,
    and MISSED_WINDOW -- for provider-listed clusters only."""
    out = []
    for a in audited or []:
        if a.get("provider_listed") is False:
            continue
        outcome, key = a.get("outcome"), a.get("cluster_id")
        if outcome in ("DECISION_SUCCESS", "DECISION_DATA_UNAVAILABLE"):
            out.append(("NHL engine: T-35 pull SUCCESS", f"{key}: {a.get('recommendations_evaluated')} decision(s) evaluated "
                                                        f"(BET {a.get('bet_count')} / WAIT {a.get('wait_count')} / PASS {a.get('pass_count')}), "
                                                        f"credits {a.get('credits_spent')}. Run first_live_certification."))
        elif outcome == "MISSED_WINDOW":
            out.append(("NHL engine: MISSED_WINDOW", f"{key}: the T-40..T-30 window passed without a pull "
                                                     f"({'Mac asleep' if 'MACHINE_ASLEEP' in (a.get('tags') or []) else 'not attempted'}). Never pulled late."))
        elif outcome in ("NETWORK_FAILED", "API_FAILED", "EMPTY_RESPONSE", "QUOTA_DEFERRED"):
            out.append((f"NHL engine: T-35 pull FAILED ({outcome})", f"{key}: {a.get('api_status')}"))
    if result.get("status") == "FAILED" and not out and result.get("cluster"):
        out.append(("NHL engine: T-35 pull FAILED", f"{result.get('cluster')}: {str(result.get('reason'))[:120]}"))
    return out
