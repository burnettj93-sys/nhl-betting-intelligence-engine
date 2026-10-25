"""The ONE manual act of the evidence refresh: put the prepared candidate in force. Run only after reading the newest PROPOSAL (docs/validation/candidates/PROPOSAL_<date>.json).

    python3 -m deploy.promote_evidence            # refuses unless the proposal is recommended and the candidate's checksum matches it
    python3 -m deploy.promote_evidence --force    # promotes a proposal that has listed problems (a deliberate owner decision)

It copies the candidate over docs/validation/eggy_evidence.json and prints the new cohort policy digest. The cohort stays fail-closed until that digest is approved with
`python3 -m deploy.activate_learning_experiment --apply --evidence "..."`: the evidence is part of the digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv=None) -> int:
    from operational import evidence_refresh as er
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    files = sorted(er.CANDIDATES.glob("PROPOSAL_*.json"))
    if not files:
        print("no proposal prepared: nothing to promote")
        return 1
    prop = json.loads(files[-1].read_text())
    cand = REPO / prop["candidate_file"]
    if not cand.exists() or hashlib.sha256(cand.read_bytes()).hexdigest() != prop["candidate_sha256"]:
        print("the candidate file is missing or does not match the proposal's checksum: refusing")
        return 1
    if not prop.get("recommended") and not a.force:
        print("the proposal lists problems and is not recommended:", prop["comparison"]["problems"], "(--force to promote anyway)")
        return 1
    shutil.copyfile(cand, er.LIVE)
    from operational import learning_cohort as lc
    print(f"promoted {cand.name} -> {er.LIVE.relative_to(REPO)}")
    print("new cohort policy digest (approve it with deploy.activate_learning_experiment --apply):", lc.policy_digest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
