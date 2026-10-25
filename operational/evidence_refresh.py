"""A controlled, auditable refresh of the calibration evidence, so intelligence does not hit a cliff when the pinned file ages, and no one has to remember to rebuild it.

The evidence file (docs/validation/eggy_evidence.json) is part of the cohort's policy digest, so it can never be replaced silently: a changed file changes the digest and the cohort fails closed until the new
digest is approved (deploy/activate_learning_experiment.py --apply). This module automates everything BEFORE that approval:

  1. once the live evidence is PREPARE_AT_DAYS old (and no newer candidate exists) it builds a CANDIDATE from the data on disk, to docs/validation/candidates/ (never the live path);
  2. it compares the candidate with the live file (how far the skater calibration cells and the team model's coefficients moved, row counts, the data cut) and writes a PROPOSAL beside it with a recommendation
     and the exact approval steps;
  3. the daily postmortem job calls it (a cheap check on most days), and the measured record reports the evidence clock and the proposal.

The only manual act is the genuinely material one: `python3 -m deploy.promote_evidence` (copies the candidate over the live file and prints the new policy digest) and then the usual activation with that digest.
Nothing here retrains a model coefficient; the candidate is the same builder on newer data.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "docs" / "validation" / "eggy_evidence.json"
CANDIDATES = REPO / "docs" / "validation" / "candidates"
PREPARE_AT_DAYS = 14
CELL_TOLERANCE = 0.03                 # a skater cell's observed rate moving more than this (case-weighted mean) needs a look before approval
COEF_TOLERANCE = 0.15                 # a team-model coefficient moving more than this needs a look


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(live: dict, cand: dict) -> dict:
    """How far a candidate moved from the live evidence, and whether it is sane."""
    out: dict = {"live_built": live.get("built_at_utc"), "candidate_built": cand.get("built_at_utc"), "live_data_through": live.get("data_through"), "candidate_data_through": cand.get("data_through")}
    lc_, cc_ = (live.get("matchup") or {}).get("coefficients") or {}, (cand.get("matchup") or {}).get("coefficients") or {}
    out["coefficient_changes"] = {k: round(cc_[k] - lc_[k], 4) for k in lc_ if k in cc_}
    out["max_coefficient_change"] = max((abs(v) for v in out["coefficient_changes"].values()), default=None)
    out["features_unchanged"] = (live.get("matchup") or {}).get("features") == (cand.get("matchup") or {}).get("features")
    ls, cs = (live.get("skater") or {}).get("cells") or {}, (cand.get("skater") or {}).get("cells") or {}
    moved, weight, worst = 0.0, 0, 0.0
    for market, cells in ls.items():
        ccells = {(c.get("dimension"), c.get("label")): c for c in cs.get(market, [])}
        for c in cells:
            c2 = ccells.get((c.get("dimension"), c.get("label")))
            if c2 and c.get("n", 0) >= 300 and c2.get("n", 0) >= 300:
                d = abs(c2["obs"] - c["obs"])
                moved += d * c2["n"]; weight += c2["n"]; worst = max(worst, d)
    out["skater_cells"] = {"matched_weight": weight, "weighted_mean_change_in_observed_rate": None if not weight else round(moved / weight, 4), "worst_cell_change": round(worst, 4)}
    out["rows"] = {"live": (live.get("skater") or {}).get("rows"), "candidate": (cand.get("skater") or {}).get("rows")}
    problems = []
    if not out["features_unchanged"]:
        problems.append("the team model's feature set differs from the live one")
    if out["max_coefficient_change"] is not None and out["max_coefficient_change"] > COEF_TOLERANCE:
        problems.append(f"a team-model coefficient moved by {out['max_coefficient_change']:.3f} (tolerance {COEF_TOLERANCE})")
    wm = out["skater_cells"]["weighted_mean_change_in_observed_rate"]
    if wm is not None and wm > CELL_TOLERANCE:
        problems.append(f"skater cells moved by {wm:.3f} on average (tolerance {CELL_TOLERANCE})")
    if (out["rows"]["live"] or 0) > (out["rows"]["candidate"] or 0):
        problems.append("the candidate rests on FEWER skater rows than the live evidence")
    if str(cand.get("data_through") or "") < str(live.get("data_through") or ""):
        problems.append("the candidate's data ends EARLIER than the live evidence's")
    out["problems"] = problems
    out["recommended"] = not problems
    return out


def status(now: dt.datetime | None = None) -> dict:
    """The live evidence clock and the newest proposal, for the measured record."""
    from operational import eggy_intelligence as ei
    from research.product_models import matchup
    now = now or dt.datetime.now(dt.timezone.utc)
    h = ei.evidence_health(matchup.load_evidence(), now)
    prop = None
    try:
        files = sorted(CANDIDATES.glob("PROPOSAL_*.json"))
        if files:
            d = json.loads(files[-1].read_text())
            prop = {"file": files[-1].name, "candidate": d.get("candidate_file"), "recommended": d.get("recommended"), "problems": d.get("comparison", {}).get("problems"), "prepared_at_utc": d.get("prepared_at_utc"),
                    "newer_than_live": str(d.get("candidate_built_at_utc") or "") > str(h.get("built_at_utc") or "")}
    except (OSError, ValueError):
        prop = None
    h["proposal"] = prop
    return h


def maybe_prepare(now: dt.datetime | None = None, *, force: bool = False, build=None) -> dict:
    """Prepare a candidate and its proposal when the live evidence is old enough and no newer one exists. Never touches the live file or the policy. `build` (tests) replaces the real builder."""
    from operational import eggy_intelligence as ei
    from research.product_models import matchup
    now = now or dt.datetime.now(dt.timezone.utc)
    live = matchup.load_evidence()
    h = ei.evidence_health(live, now)
    if h.get("state") in ("MISSING", "UNREADABLE"):
        return {"status": "NO_LIVE_EVIDENCE", **h}
    cur = status(now)
    if not force and (h["age_days"] < PREPARE_AT_DAYS or (cur.get("proposal") or {}).get("newer_than_live")):
        return {"status": "NOT_DUE" if h["age_days"] < PREPARE_AT_DAYS else "PROPOSAL_ALREADY_PREPARED", "age_days": h["age_days"], "prepare_at_days": PREPARE_AT_DAYS}
    if build is None:
        from deploy import build_eggy_evidence as B
        build = B.build_document
    cand = build()
    if not cand:
        return {"status": "CANDIDATE_BUILD_FAILED", "why": "no team file on disk"}
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d")
    cpath = CANDIDATES / f"eggy_evidence_{stamp}.json"
    cpath.write_text(json.dumps(cand, indent=1, sort_keys=True))
    cmp_ = compare({k: v for k, v in live.items() if not k.startswith("_")}, cand)
    proposal = {"prepared_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "candidate_file": str(cpath.relative_to(REPO)), "candidate_sha256": _sha(cpath), "candidate_built_at_utc": cand.get("built_at_utc"),
                "comparison": cmp_, "recommended": cmp_["recommended"],
                "to_approve": ["python3 -m deploy.promote_evidence   (copies this candidate over the live evidence file and prints the new cohort policy digest)",
                               "python3 -m deploy.activate_learning_experiment            (dry run: confirm every check passes with the new digest)",
                               "python3 -m deploy.activate_learning_experiment --apply --evidence \"<owner approval and what was checked>\""],
                "note": "until approval the live evidence stays in force; it is valid for " + str(ei.EVIDENCE_MAX_AGE_DAYS) + " days from its build"}
    (CANDIDATES / f"PROPOSAL_{stamp}.json").write_text(json.dumps(proposal, indent=1))
    return {"status": "PROPOSAL_PREPARED", "recommended": proposal["recommended"], "problems": cmp_["problems"], "candidate": proposal["candidate_file"]}
