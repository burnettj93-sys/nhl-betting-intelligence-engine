"""
The ticket policy in force, and the owner's approval of it.

The selector reads its rules from `research/real_market_parlay/policy.py`. The policy in the code is the PROPOSED restart policy (the one aimed at the stated objective: up to five
worthwhile +100-or-better parlays with a strong estimated chance of hitting, empty slots allowed). It is a proposal, not a validated result, and the owner has not approved it.

Nothing automatic can resume until the owner approves THIS exact policy: `recording_pause.resume()` refuses unless the approval file names this policy's digest. Approving is a
deliberate act by the owner, not by this code or by anyone acting for them:

    python3 -m operational.ticket_policy show
    python3 -m operational.ticket_policy approve <digest>      # the digest printed by `show`

Changing any number in the policy changes the digest, so an approval of an earlier policy never carries over to a changed one.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from operational import state_paths
from research.real_market_parlay import policy as P

APPROVAL_NAME = "ticket_policy_approval.json"


ENV_TEST_POLICY = "NHL_ENGINE_TICKET_POLICY"


def active() -> P.TicketPolicy:
    """The policy the selector uses: PROPOSED. Only inside a unit-test run may NHL_ENGINE_TICKET_POLICY=legacy select the earlier rules, so the existing machinery tests (which exercise
    recording, settlement and exposure with synthetic boards) keep running on the rules they were written for. It has no effect anywhere else."""
    import os
    if os.environ.get(ENV_TEST_POLICY, "").strip().lower() == "legacy" and state_paths.under_test():
        return P.LEGACY
    return P.PROPOSED


def approval() -> dict | None:
    path = state_paths.path(APPROVAL_NAME)
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def is_approved(policy: P.TicketPolicy | None = None) -> bool:
    policy = policy or active()
    a = approval()
    return bool(a and a.get("digest") == policy.digest() and a.get("policy_id") == policy.policy_id and a.get("approved_by"))


def approve(digest: str, *, approved_by: str = "owner", now: dt.datetime | None = None) -> dict:
    policy = active()
    if digest != policy.digest():
        raise ValueError(f"the digest {digest!r} is not the active policy's ({policy.digest()}); run `show` and approve the one you reviewed")
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = {"policy_id": policy.policy_id, "digest": policy.digest(), "approved_by": approved_by, "approved_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "policy": policy.as_dict(),
           "note": "Approval of a proposed, unvalidated policy: it does not claim the numbers are correct, only that the owner accepts them for a paper experiment."}
    path = state_paths.path(APPROVAL_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))
    return doc


def describe() -> dict:
    p = active()
    return {"policy_id": p.policy_id, "status": p.status, "digest": p.digest(), "summary": p.summary(), "approved": is_approved(p), "parameters": p.as_dict()}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "approve":
        print(json.dumps(approve(sys.argv[2] if len(sys.argv) > 2 else ""), indent=1))
    else:
        print(json.dumps(describe(), indent=1))
