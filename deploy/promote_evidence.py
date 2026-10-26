"""The owner's act for a MATERIAL evidence change. Routine refreshes roll forward by themselves (operational/evidence_refresh.py); this is for a candidate that changes the model's identity, moves a coefficient or a
calibration cell beyond tolerance, rests on less data, or fails its own validation.

    python3 -m deploy.promote_evidence --approve "<what you checked>"     # archive the live file, put the newest proposal's candidate in force, record the approval in the history
    python3 -m deploy.promote_evidence --init-baseline "<authority>"       # once: record the CURRENT live evidence as the history's baseline

It refuses without an approval text, or when the candidate does not match its proposal's checksum. If the evidence IDENTITY changed, the cohort policy digest changes too and the cohort stays fail-closed
until the new digest is approved with `python3 -m deploy.activate_learning_experiment --apply --evidence "..."`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv=None) -> int:
    from operational import evidence_refresh as er
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--approve", metavar="TEXT")
    ap.add_argument("--init-baseline", metavar="AUTHORITY")
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    if a.init_baseline:
        print(json.dumps(er.init_baseline(a.init_baseline, now), indent=1)[:600])
        return 0
    if not a.approve or not a.approve.strip():
        print("an approval text is required (--approve \"<what you checked>\"): a material evidence change is the owner's decision")
        return 1
    files = sorted(er.CANDIDATES.glob("PROPOSAL_*.json"))
    if not files:
        print("no proposal prepared: nothing to promote")
        return 1
    prop = json.loads(files[-1].read_text())
    cand = REPO / prop["candidate_file"]
    if not cand.exists() or hashlib.sha256(cand.read_bytes()).hexdigest() != prop["candidate_sha256"]:
        print("the candidate file is missing or does not match the proposal's checksum: refusing")
        return 1
    e = er.approve_material(cand, a.approve.strip(), now, prop.get("comparison"))
    from operational import learning_cohort as lc
    print(f"approved: history entry {e['seq']} ({e['kind']}), evidence {e['evidence_sha256'][:12]}")
    print("cohort policy digest now:", lc.policy_digest(), "(approve it with deploy.activate_learning_experiment --apply if it changed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
