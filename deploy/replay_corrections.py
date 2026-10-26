"""Replay every settled learning parlay and leg BEFORE and AFTER the audit corrections. Read-only: it copies the ledger, never writes to the live files.

    python3 deploy/replay_corrections.py --runtime ~/nhl_engine_release/operational/runtime --out docs/validation/replay_corrections.json

BEFORE is what the promoted release stored (its cards, its assessments, its probabilities); AFTER is the corrected code applied to the same stored cards and results:
  1. contradiction severity and leg admission (the old name-by-name rule against the causal-family rule), on every recorded leg and on every card the cohort reviewed today;
  2. the leg decision-quality grid (the straight-bet rubric against the parlay-leg rubric), by outcome;
  3. the loss causes (the postmortem's classification rebuilt with the new assessments);
  4. ticket probabilities (stored: none for same-game tickets; now: the measured pairwise lifts);
  5. the day's portfolio selection: the same recorded legs, built by pure rank order against the portfolio-aware order: which tickets, how concentrated, the flagship.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def old_combine(flags):
    """The rule before the correction: every flag counted by itself."""
    lv = [f["level"] for f in flags]
    if "SEVERE" in lv or lv.count("HIGH") >= 2:
        return "SEVERE"
    if "HIGH" in lv or lv.count("MODERATE") >= 2:
        return "HIGH"
    if "MODERATE" in lv:
        return "MODERATE"
    return "LOW"


def main(argv=None) -> int:
    from operational import eggy_decisions as ed
    from operational import eggy_intelligence as ei
    from operational import learning_cohort as lc
    from operational import learning_parlays as lp
    from operational import learning_postmortem as lpm
    from research.real_market_parlay.engine import ParlayLeg
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rt = Path(a.runtime).expanduser()
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(rt / "learning_cohort.db", tmp / "ledger.db")
    conn = lc.open_ledger(tmp / "ledger.db")
    rows = [dict(r) for r in conn.execute("SELECT * FROM parlays ORDER BY et_date, parlay_rank")]
    stored_pm = json.loads((rt / "learning_experiment_postmortem.json").read_text())
    status = json.loads((rt / "learning_experiment_status.json").read_text())
    out: dict = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ledger_tickets": len(rows), "days": sorted({r["et_date"] for r in rows})}
    fields = ParlayLeg.__dataclass_fields__

    # ---- 1. contradiction severity and admission
    def level_of(card, combine):
        return combine(ei.counted_flags((card.get("contradiction") or {}).get("flags") or []))
    legs_by_key = {}
    for r in rows:
        for l in json.loads(r["legs_json"]):
            legs_by_key[(r["et_date"], l["key"])] = l
    changed, sev = [], collections.Counter()
    for (day, key), l in legs_by_key.items():
        o, n = level_of(l["card"], old_combine), level_of(l["card"], ei.combine_contradiction)
        sev[(o, n)] += 1
        if o != n:
            changed.append({"day": day, "leg": l["card"]["bet"]["label"], "before": o, "after": n})
    out["1a_recorded_legs"] = {"distinct_legs": len(legs_by_key), "severity_before_after": {f"{k[0]} -> {k[1]}": v for k, v in sorted(sev.items())}, "legs_whose_severity_changed": changed,
                                  "limit": "only legs that were RECORDED are stored with their cards: a leg the old rule rejected (two detectors of one role change adding to HIGH) was never recorded, so this replay can show that no recorded leg got stricter, not how many rejected legs would now be admitted"}

    def admitted(card, combine):
        with_old = ei.combine_contradiction
        ei.combine_contradiction = combine
        try:
            return lp.leg_ok({"leg": None, "card": card, "action": (card.get("action") or {}).get("decision")})
        finally:
            ei.combine_contradiction = with_old
    cards = status.get("cards") or {}
    flip = []
    adm = collections.Counter()
    for k, c in cards.items():
        b, n = admitted(c, old_combine), admitted(c, ei.combine_contradiction)
        adm[(b[0], n[0])] += 1
        if b[0] != n[0]:
            flip.append({"leg": c.get("bet", {}).get("label"), "before": b[0], "after": n[0], "why_before": b[1], "families": [f["family"] + ":" + f["level"] for f in ei.families_of(ei.counted_flags((c.get("contradiction") or {}).get("flags") or []))]})
    out["1b_todays_reviewed_cards"] = {"cards": len(cards), "admitted_before_after": {f"{k[0]} -> {k[1]}": v for k, v in sorted(adm.items(), key=str)}, "legs_whose_admission_changed": flip,
                                       "note": "the cards were reviewed before the corrections; only the contradiction rule is re-applied here (a probability or flag the corrections also change is not re-derived)"}

    # ---- 2. the decision-quality grid
    before = collections.Counter((((lr.get("assessment") or {}).get("decision_quality") or {}).get("label"), lr.get("outcome")) for p in stored_pm.get("parlays") or [] for lr in p.get("leg_reviews") or []
                                 if lr.get("outcome") in ("WIN", "LOSS"))
    seen, after = set(), collections.Counter()
    uses = collections.Counter((r["et_date"], l["key"]) for r in rows for l in json.loads(r["legs_json"]))
    per_leg, trig, ever = [], collections.Counter(), collections.Counter()
    for r in rows:
        res = {x["key"]: x["outcome"] for x in (json.loads(r["settlement_json"]).get("leg_results") or [])} if r.get("settlement_json") else {}
        for l in json.loads(r["legs_json"]):
            if (r["et_date"], l["key"]) in seen or res.get(l["key"]) not in ("WIN", "LOSS"):
                continue
            seen.add((r["et_date"], l["key"]))
            q = ed.parlay_leg_quality(l["card"], {"leg": l["leg"], "date": r["et_date"], "reuse_count": uses[(r["et_date"], l["key"])]})
            after[(q["label"], res[l["key"]])] += 1
            for c in q.get("criteria") or []:
                trig[c] += 1
            per_leg.append({"day": r["et_date"], "leg": l["card"]["bet"]["label"], "estimate": round(float(l["estimate"]), 3), "decision": q["label"], "outcome": res[l["key"]], "criteria": q.get("criteria"), "reasons": q.get("reasons")})
    seen_b = set()
    before_distinct = collections.Counter()
    for p in stored_pm.get("parlays") or []:
        for lr in p.get("leg_reviews") or []:
            if (p["et_date"], lr["key"]) in seen_b or lr.get("outcome") not in ("WIN", "LOSS"):
                continue
            seen_b.add((p["et_date"], lr["key"]))
            before_distinct[(((lr.get("assessment") or {}).get("decision_quality") or {}).get("label"), lr["outcome"])] += 1
    fmt = lambda c: {f"{k[0]} / {k[1]}": v for k, v in sorted(c.items(), key=str)}
    out["2_leg_decision_quality"] = {"settled_distinct_legs": sum(after.values()), "before_straight_bet_rubric": fmt(before_distinct), "after_parlay_leg_rubric": fmt(after),
                                      "criteria_that_fired_after": dict(trig), "per_leg": per_leg,
                                      "independence_of_the_outcome": "the rubric takes the card, the leg as recorded and the day's reuse; it has no outcome parameter (tests/test_audit_corrections.py)"}

    # ---- 3. loss causes (the postmortem rebuilt)
    try:
        nhl = sqlite3.connect(f"file:{rt / 'nhl.db'}?mode=ro", uri=True)
        nhl.row_factory = sqlite3.Row
        now = dt.datetime.now(dt.timezone.utc)
        rebuilt = lpm.parlay_summaries(conn, nhl, now, None)
        def causes(parlays):
            seen_c, c = set(), collections.Counter()
            for p in parlays:
                for lr in p.get("leg_reviews") or []:
                    if lr.get("outcome") == "LOSS" and (p["et_date"], lr["key"]) not in seen_c:
                        seen_c.add((p["et_date"], lr["key"]))
                        c[(lr.get("assessment") or {}).get("classification") or "UNCLASSIFIED"] += 1
            return c
        cb, ca = causes(stored_pm.get("parlays") or []), causes(rebuilt)
        out["3_loss_causes"] = {"lost_legs_before": sum(cb.values()), "before": dict(cb.most_common()), "lost_legs_after": sum(ca.values()), "after": dict(ca.most_common()),
                                "unknown_before": cb.get("UNKNOWN", 0), "unknown_after": ca.get("UNKNOWN", 0)}
        out["3_loss_causes"]["note"] = ("the cause taxonomy reads the review's flags and the game facts, which did not change: specific causes (TOI_PROJECTION_ERROR, MODEL_CALIBRATION) and UNKNOWN are identical. The two 'chance' categories "
                                        "move because they depend on the decision label: a GOOD leg that lost to variance is NORMAL_VARIANCE (earned), a QUESTIONABLE leg that lost within its estimate is OUTCOME_WITHIN_ESTIMATE")
    except Exception as exc:  # noqa: BLE001
        out["3_loss_causes"] = {"unavailable": f"{exc.__class__.__name__}: {exc}"}

    # ---- 4. ticket probabilities
    tp = []
    for r in rows:
        views = lp.view_of_row(r)
        tp.append({"id": r["parlay_id"][:9], "date": r["et_date"], "quality": r["construction_quality"], "stored_combined": r["combined_probability"], "dependence_level_stored": r["dependence_level"],
                   "product_of_estimates": round(lp._product([float(l["estimate"]) for l in views["legs"]]), 4), "with_measured_lifts": round(views["probability"], 4)})
    out["4_ticket_probabilities"] = {"tickets": len(tp), "stored_without_a_probability": sum(1 for t in tp if t["stored_combined"] is None), "mean_abs_change_from_the_product": round(sum(abs(t["with_measured_lifts"] - t["product_of_estimates"]) for t in tp) / max(len(tp), 1), 4),
                                     "largest_changes": sorted(tp, key=lambda t: -abs(t["with_measured_lifts"] - t["product_of_estimates"]))[:5]}

    # ---- 5. the day's portfolio: rank order against the portfolio-aware order, same recorded legs
    from operational import eggy_intelligence as ei2
    days = {}
    for day in out["days"]:
        legs = {}
        for r in rows:
            if r["et_date"] != day:
                continue
            for l in json.loads(r["legs_json"]):
                legs[l["key"]] = l
        cands = []
        for k, l in legs.items():
            leg = ParlayLeg(**{kk: v for kk, v in l["leg"].items() if kk in fields})
            card = l["card"]
            est = float(l["estimate"])
            ecls, _ = ei2.edge_class_of(card) if card.get("probabilities") is not None else ("NEUTRAL_UNCERTAIN_EDGE", "")
            edge, _w = ei2.edge_for_class(card)
            cands.append({"leg": leg, "key": k, "card": card, "action": (card.get("action") or {}).get("decision"), "rank_key": ei2.rank_key(card, ecls, est, edge, k)})
        def run(neutral):
            import unittest.mock as mock
            with mock.patch.object(lp, "concentration_key", (lambda v, kept: (0, 0, 0, 0, 0, 0, 0)) if neutral else lp.concentration_key), \
                    mock.patch.object(ei, "combine_contradiction", old_combine if neutral else ei.combine_contradiction):
                res = lp.build(cands, slots=10)
            ts = res["parlays"]
            fl = lp.flagship(ts)
            return {"tickets": [sorted(c["key"][-24:] for c in p["cands"]) for p in ts], "n": len(ts), "flagship": None if not fl else [c["leg"].participant_name for c in fl["cands"]], "portfolio": res["portfolio"],
                    "qualities": dict(collections.Counter(p["construction_quality"] for p in ts))}
        b, n = run(True), run(False)
        keys = lambda x: {tuple(t) for t in x["tickets"]}
        keep = ("tickets", "distinct_legs", "leg_slots", "most_reused_leg", "team_exposure", "game_exposure", "expected_tickets_hit", "chance_no_ticket_hits", "chance_no_ticket_hits_range", "spread_inflation_from_shared_legs",
                "tickets_with_a_role_flagged_leg", "tickets_with_a_model_risk_leg")
        days[day] = {"recorded_legs_available": len(cands), "before": {"n": b["n"], "flagship": b["flagship"], "qualities": b["qualities"], "portfolio": {k: b["portfolio"].get(k) for k in keep}},
                     "after": {"n": n["n"], "flagship": n["flagship"], "qualities": n["qualities"], "portfolio": {k: n["portfolio"].get(k) for k in keep}},
                     "same_tickets": len(keys(b) & keys(n)), "tickets_only_before": len(keys(b) - keys(n)), "tickets_only_after": len(keys(n) - keys(b))}
    out["5_portfolio_selection"] = {"method": "the day's RECORDED legs are the pool (the full reviewed pool of earlier days was not stored); built once by pure rank order with the old contradiction rule, and once by the portfolio-aware order", "days": days}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
