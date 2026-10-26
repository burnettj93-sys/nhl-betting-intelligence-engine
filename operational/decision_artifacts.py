"""The validation artifacts that CAN CHANGE a live decision (eligibility, a probability adjustment, a calibration or a dependence), under policy control.

Each is a pinned, versioned file whose checksum is part of the cohort policy digest (operational/learning_cohort.py POLICY): a changed file is a policy change, it changes the digest, and the cohort fails
closed until the owner approves the new digest. Ordinary runtime observations (new games, prices, settled legs) never touch these files and never need approval. The calibration evidence
(docs/validation/eggy_evidence.json) is controlled by its own refresh and hash-chained history (operational/evidence_refresh.py); the artifacts below are re-validated by their scripts, deliberately.

`docs/validation/artifact_manifest.json` says what each artifact is, what decision it affects, who reads it and how to regenerate it; a test keeps the manifest and the policy in step.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VALIDATION = REPO / "docs" / "validation"
MANIFEST = VALIDATION / "artifact_manifest.json"

# name -> file. Only files a live decision reads.
ARTIFACTS = {
    "player_signals": "player_signals.json",             # flag evidence (what a role / form flag is worth beyond the model) and the shots estimate rule
    "points_thresholds": "points_thresholds.json",       # which points thresholds are validated for live use and their recalibration
    "dependence_lift": "dependence_lift.json",           # the measured same-game dependence between legs
    "price_freshness": "price_freshness.json",           # how old a sportsbook price may be and still be current, per market (operational/price_freshness.py)
}


def sha256(name: str) -> str | None:
    try:
        return hashlib.sha256((VALIDATION / ARTIFACTS[name]).read_bytes()).hexdigest()
    except OSError:
        return None


def pinned() -> dict:
    """{name: sha256[:16]}: what the policy digest pins."""
    return {n: (sha256(n) or "MISSING")[:16] for n in sorted(ARTIFACTS)}


def manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}


def manifest_problems() -> list[str]:
    """The manifest must describe exactly the pinned artifacts, with the checksums they have now."""
    m = (manifest().get("artifacts") or {})
    out = []
    for n in ARTIFACTS:
        e = m.get(n)
        if not e:
            out.append(f"{n} is not in the manifest")
        elif e.get("sha256") != sha256(n):
            out.append(f"{n}: the manifest checksum is not the file's (the artifact changed without the manifest)")
    out.extend(f"{n} in the manifest is not a pinned artifact" for n in m if n not in ARTIFACTS)
    return out
