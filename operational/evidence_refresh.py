"""A controlled, auditable refresh of the calibration evidence, with no calendar cliff and no uncontrolled retraining.

NEW GAME DATA -> automatic. A MODEL OR POLICY CHANGE -> versioned, and approved by the owner.

Every EVIDENCE_REFRESH_DAYS (14) the daily postmortem job builds a CANDIDATE evidence file from the data on disk (the same builder, newer data), validates it, compares it with the live file, and then:

  * NOT MATERIAL -> the candidate ROLLS FORWARD by itself: the old live file is archived under docs/validation/evidence_versions/, the candidate becomes the live file, and an entry is appended to the
    append-only, hash-chained docs/validation/evidence_history.jsonl (kind AUTO_ROLL_FORWARD, with the comparison that justified it). The cohort policy digest does not change: it pins the evidence IDENTITY.
  * MATERIAL -> a PROPOSAL is written and the live evidence stays in force until the owner approves it (`python3 -m deploy.promote_evidence --approve "<what was checked>"`). If the owner has not acted by
    EVIDENCE_MAX_AGE_DAYS (45) the engine fails closed: the evidence is no longer being verified. With a working refresh and no material change that day never comes.

WHAT IS MATERIAL (any one of these):
  1. the evidence IDENTITY differs (evidence format, team model version, feature set, decay or shrink, the set of skater markets): a different model is a different policy (it also changes the policy digest);
  2. a team-model coefficient moved by more than COEF_TOLERANCE, or the skater calibration cells moved by more than CELL_TOLERANCE on average (the numbers the decisions stand on shifted enough to need a look);
  3. the candidate rests on fewer skater rows or an earlier data cut than the live file (a regression, not a refresh);
  4. the candidate fails its own validation: it must be well formed, finite, and its chronological team-model evaluation must still beat the home-rate baseline and goal-differential alone with the 95% interval
     entirely below zero (the rule the feature set was originally chosen by).
Everything else is a routine refresh.

THE ENGINE NEVER CONTINUES ON EVIDENCE IT CANNOT ACCOUNT FOR: `verify_history` requires the live file's checksum to be the newest entry of an intact hash chain. A file edited by hand, a broken chain, or a file not
in the history makes the layer unusable (the cohort records nothing and says why). Old versions stay in the archive; the chain lists every one.
Nothing here retrains a model coefficient: the candidate is the same builder on newer data, and anything that changes how the model is built is material by definition.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "docs" / "validation" / "eggy_evidence.json"
CANDIDATES = REPO / "docs" / "validation" / "candidates"
VERSIONS = REPO / "docs" / "validation" / "evidence_versions"
HISTORY = REPO / "docs" / "validation" / "evidence_history.jsonl"
PREPARE_AT_DAYS = 14                  # EVIDENCE_REFRESH_DAYS: a candidate is built, validated and compared this often
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
    from research.product_models import matchup
    out["identity_unchanged"] = matchup.evidence_identity(live) == matchup.evidence_identity(cand)
    if not out["identity_unchanged"]:
        problems.append("the evidence IDENTITY differs (format, team model, feature set, decay or the set of skater markets): a different model is a policy change")
    problems.extend(candidate_validation_problems(cand))
    out["problems"] = problems
    out["material"] = bool(problems)
    out["recommended"] = not problems
    return out


def candidate_validation_problems(cand: dict) -> list[str]:
    """The candidate's own validation: well formed, finite, and the chronological team-model evaluation still beats the home-rate baseline and goal-differential alone with the 95% interval below zero (the
    rule the feature set was chosen by). A candidate that fails is never rolled forward."""
    import math
    out = []
    m, sk = cand.get("matchup") or {}, cand.get("skater") or {}
    co = m.get("coefficients") or {}
    if not co or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in co.values()):
        out.append("the candidate's team-model coefficients are missing or not finite")
    if not (sk.get("cells") or {}):
        out.append("the candidate has no skater calibration cells")
    ev = m.get("evaluation") or {}
    for k in ("delta_vs_home_rate", "delta_vs_gd_only"):
        d = ev.get(k) or {}
        if d.get("ci_high") is None or not d["ci_high"] < 0:
            out.append(f"the candidate's chronological evaluation no longer beats the baseline ({k}: 95% interval is not entirely below zero)")
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


# ------------------------------------------------------------------------------------------------ the append-only, hash-chained history
def _entry_hash(e: dict) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in e.items() if k != "entry_sha256"}, sort_keys=True).encode()).hexdigest()


def read_history() -> list[dict]:
    try:
        return [json.loads(l) for l in HISTORY.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def verify_history(live_path: Path | None = None) -> dict:
    """{'ok', 'problems', 'entries', 'latest'}: the chain is intact (every entry's hash and its link to the one before) and the LIVE file is exactly the newest entry's evidence. A live file that is not in
    the history, or a chain that was edited, is not evidence the engine can account for."""
    live_path = live_path or LIVE
    entries = read_history()
    problems = []
    if not entries:
        return {"ok": False, "problems": ["no evidence history: the live evidence is not accounted for"], "entries": 0, "latest": None}
    prev = None
    for i, e in enumerate(entries):
        if e.get("entry_sha256") != _entry_hash(e):
            problems.append(f"history entry {i + 1} was altered")
        if e.get("prev_entry_sha256") != prev:
            problems.append(f"history entry {i + 1} does not follow the one before it")
        prev = e.get("entry_sha256")
    try:
        live_sha = _sha(live_path)
    except OSError:
        live_sha = None
    if live_sha != entries[-1].get("evidence_sha256"):
        problems.append("the live evidence file is not the one the newest history entry recorded (it was changed outside the refresh)")
    return {"ok": not problems, "problems": problems, "entries": len(entries), "latest": {k: entries[-1].get(k) for k in ("seq", "kind", "at_utc", "built_at_utc", "data_through", "evidence_sha256")}}


def _record(kind: str, doc: dict, sha: str, at: dt.datetime, comparison: dict | None, approval: str | None, archived: str | None) -> dict:
    from research.product_models import matchup
    entries = read_history()
    e = {"seq": len(entries) + 1, "at_utc": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, "evidence_sha256": sha, "built_at_utc": doc.get("built_at_utc"), "data_through": doc.get("data_through"),
         "identity": matchup.evidence_identity(doc), "archived_as": archived, "comparison": comparison, "approval": approval, "prev_entry_sha256": entries[-1]["entry_sha256"] if entries else None}
    e["entry_sha256"] = _entry_hash(e)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a") as fh:
        fh.write(json.dumps(e, sort_keys=True) + "\n")
    return e


def _archive(path: Path) -> str:
    """Keep a byte-exact copy of an evidence file, named by its build time and checksum."""
    doc = json.loads(path.read_text())
    sha = _sha(path)
    name = f"eggy_evidence_{str(doc.get('built_at_utc') or 'unknown').replace(':', '').replace('-', '')}_{sha[:8]}.json"
    VERSIONS.mkdir(parents=True, exist_ok=True)
    dest = VERSIONS / name
    if not dest.exists():
        shutil.copyfile(path, dest)
    return str(dest.relative_to(REPO)) if REPO in dest.parents else dest.name


def init_baseline(approval: str, now: dt.datetime | None = None) -> dict:
    """Record the CURRENT live evidence as the baseline of the history (once). The approval text says under what authority it is the baseline."""
    if read_history():
        return {"status": "ALREADY_INITIALISED"}
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = json.loads(LIVE.read_text())
    e = _record("BASELINE", doc, _sha(LIVE), now, None, approval, _archive(LIVE))
    return {"status": "BASELINE_RECORDED", "entry": e}


def roll_forward(cand_path: Path, cmp_: dict, now: dt.datetime) -> dict:
    """Put a validated, non-material candidate in force: archive the old live file, replace it, append the history entry."""
    old_archived = _archive(LIVE)
    shutil.copyfile(cand_path, LIVE)
    doc = json.loads(LIVE.read_text())
    return _record("AUTO_ROLL_FORWARD", doc, _sha(LIVE), now, {k: cmp_[k] for k in ("max_coefficient_change", "skater_cells", "rows", "identity_unchanged", "live_data_through", "candidate_data_through")},
                   None, old_archived)


def approve_material(cand_path: Path, approval: str, now: dt.datetime, comparison: dict | None) -> dict:
    """The owner's act for a MATERIAL change: archive, replace, and record WHO approved WHAT (the text), so the policy digest and the history agree."""
    old_archived = _archive(LIVE)
    shutil.copyfile(cand_path, LIVE)
    doc = json.loads(LIVE.read_text())
    return _record("OWNER_APPROVED", doc, _sha(LIVE), now, comparison, approval, old_archived)


def maybe_prepare(now: dt.datetime | None = None, *, force: bool = False, build=None) -> dict:
    """Build, validate and compare a candidate when the live evidence is PREPARE_AT_DAYS old. A routine refresh rolls forward by itself; a material one waits for the owner as a PROPOSAL (the live evidence stays
    in force). `build` (tests) replaces the real builder."""
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
    if not verify_history()["ok"]:
        return {"status": "HISTORY_NOT_INTACT", "problems": verify_history()["problems"], "why": "no roll-forward on evidence the history cannot account for"}
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
    if not cmp_["material"]:
        e = roll_forward(cpath, cmp_, now)
        return {"status": "ROLLED_FORWARD", "history_entry": e["seq"], "built_at_utc": cand.get("built_at_utc"), "data_through": cand.get("data_through"), "max_coefficient_change": cmp_["max_coefficient_change"]}
    proposal = {"prepared_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "candidate_file": str(cpath.relative_to(REPO)), "candidate_sha256": _sha(cpath), "candidate_built_at_utc": cand.get("built_at_utc"),
                "comparison": cmp_, "recommended": False, "material": True,
                "to_approve": ["python3 -m deploy.promote_evidence --approve \"<what you checked>\"   (archives the live file, puts this candidate in force and records your approval in the history)",
                               "if the evidence identity changed: python3 -m deploy.activate_learning_experiment --apply with the new policy digest"],
                "note": "MATERIAL: " + "; ".join(cmp_["problems"]) + f". The live evidence stays in force until it is {ei.EVIDENCE_MAX_AGE_DAYS} days old; after that the engine fails closed rather than run on evidence it has not verified."}
    (CANDIDATES / f"PROPOSAL_{stamp}.json").write_text(json.dumps(proposal, indent=1))
    return {"status": "PROPOSAL_PREPARED", "material": True, "recommended": False, "problems": cmp_["problems"], "candidate": proposal["candidate_file"]}
