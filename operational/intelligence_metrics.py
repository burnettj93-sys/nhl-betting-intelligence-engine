"""Is the engine getting smarter? Measured, not claimed. Every number here is computed from what the engine actually recorded and what then happened (the learning-parlay ledger and the postmortem's leg
reviews), refreshed whenever the postmortem is rebuilt (operational/learning_postmortem.build), and published in the postmortem document as `intelligence_metrics`.

Rules of the road:
  * hit rates are always shown against what the engine EXPECTED (the sum of its own estimates), with n and a z score; a statement is `sufficient` only at n >= MIN_N, otherwise the numbers are shown and
    labelled too small to judge;
  * unpriced parlays have no sportsbook price, so no ROI, no expected value and no closing-line value is ever computed here (they are reported as not available, never as zero);
  * a leg is counted once per Eastern day however many tickets it sat on (its outcome is one event); ticket statistics count tickets.
Nothing here changes a decision: it is the instrument that shows whether the decisions are working.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sqlite3

MIN_N = 30                                                  # fewer settled cases than this are shown but never read as a finding
BANDS = ((0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))
SETTLED_LEG = ("WIN", "LOSS")
OVERLAY_FLAGS = ("TOI_DROP", "PP_ROLE_LOST", "RECENT_FORM_BELOW_MODEL", "POSTMORTEM_ROLE_CAUTION", "ROLE_ASSUMPTION", "POWER_PLAY_ASSUMPTION", "LOW_USAGE")


def _cell(hits: int, n: int, expected: float, variance: float) -> dict:
    z = None if n == 0 or variance <= 0 else round((hits - expected) / math.sqrt(variance), 2)
    return {"n": n, "hit": hits, "rate": None if not n else round(hits / n, 3), "expected_hit": round(expected, 2), "expected_rate": None if not n else round(expected / n, 3),
            "z": z, "sufficient": n >= MIN_N}


class _Acc:
    def __init__(self):
        self.n = self.hit = 0
        self.exp = self.var = 0.0

    def add(self, p: float, hit: bool) -> None:
        self.n += 1
        self.hit += 1 if hit else 0
        self.exp += p
        self.var += p * (1 - p)

    def cell(self) -> dict:
        return _cell(self.hit, self.n, self.exp, self.var)


def _band(p: float) -> str:
    for lo, hi in BANDS:
        if lo <= p < hi:
            return f"{lo:.0%}-{min(hi, 1.0):.0%}"
    return "other"


def _ticket_p(t: dict) -> float:
    if t.get("combined_probability") is not None:
        return float(t["combined_probability"])
    p = 1.0
    for l in t["legs"]:
        p *= float(l["estimate"])
    return p                                                 # an independence assumption: a yardstick, not a claim, and it is labelled so in the document


def compute(conn, postmortem: dict | None = None, now: dt.datetime | None = None, nhl_conn=None) -> dict:
    """The metrics document. `postmortem` supplies the leg-level decision quality and cause (its `parlays[*].leg_reviews`)."""
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM parlays ORDER BY et_date, parlay_rank").fetchall()]
    except sqlite3.OperationalError:
        rows = []
    reviews: dict[str, dict] = {}                           # leg key + date -> the postmortem's review of it
    for p in (postmortem or {}).get("parlays") or []:
        for lr in p.get("leg_reviews") or []:
            reviews[(p.get("et_date"), lr.get("key"))] = lr
    tickets, legs_by_day = [], collections.defaultdict(dict)
    uses = collections.defaultdict(collections.Counter)
    for r in rows:
        legs = json.loads(r["legs_json"])
        res = {x["key"]: x["outcome"] for x in (json.loads(r["settlement_json"]).get("leg_results") or [])} if r.get("settlement_json") else {}
        t = {"id": r["parlay_id"], "date": r["et_date"], "quality": r["construction_quality"], "status": r["result_status"], "combined_probability": r["combined_probability"],
             "legs": [{"key": l["key"], "estimate": float(l["estimate"]), "card": l.get("card") or {}, "leg": l["leg"], "outcome": res.get(l["key"])} for l in legs],
             "weakest": json.loads(r["weakest_leg_json"]).get("label"), "flagship_sort": (r["n_legs"], -json.loads(r["weakest_leg_json"])["estimate"], -(r["ranking_probability"] or 0), r["idempotency_key"]),
             "action": r["action"]}
        tickets.append(t)
        for l in t["legs"]:
            uses[r["et_date"]][l["key"]] += 1
            legs_by_day[r["et_date"]][l["key"]] = l
    out: dict = {"generated_at_utc": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "min_n_for_a_finding": MIN_N, "tickets_recorded": len(tickets),
                 "days": sorted(legs_by_day)}
    # --- legs: hit rate against expected, calibration by band, by market, flagged / unflagged, reused / not
    leg_all, by_band, by_market, by_flag, by_reuse = _Acc(), collections.defaultdict(_Acc), collections.defaultdict(_Acc), collections.defaultdict(_Acc), collections.defaultdict(_Acc)
    ml_gap = collections.defaultdict(_Acc)
    by_rubric = collections.defaultdict(_Acc)
    by_mkt_band = collections.defaultdict(lambda: collections.defaultdict(_Acc))
    ml_market, ml_model = collections.defaultdict(_Acc), collections.defaultdict(_Acc)
    for day, ls in legs_by_day.items():
        for key, l in ls.items():
            if l["outcome"] not in SETTLED_LEG:
                continue
            hit, p = l["outcome"] == "WIN", l["estimate"]
            leg_all.add(p, hit)
            by_band[_band(p)].add(p, hit)
            fam = l["leg"].get("market_family")
            thr = l["leg"].get("threshold")
            mkt = fam if fam == "MONEYLINE" else f"{fam} {thr}+"
            by_market[mkt].add(p, hit)
            by_mkt_band[mkt][_band(p)].add(p, hit)
            names = {f["name"] for f in (l["card"].get("contradiction") or {}).get("flags", [])}
            by_flag["flagged (role / opportunity flag raised)" if names & set(OVERLAY_FLAGS) else "unflagged"].add(p, hit)
            by_reuse["used on 1 ticket" if uses[day][key] == 1 else "used on 2+ tickets"].add(p, hit)
            label = (((reviews.get((day, key)) or {}).get("assessment") or {}).get("decision_quality") or {}).get("label")
            if label:
                by_rubric[label].add(p, hit)                         # the parlay-leg rubric's verdict (read from the card, never from the result) against what then happened
            if fam == "MONEYLINE":
                pr = l["card"].get("probabilities") or {}
                mp, mk = pr.get("model"), pr.get("market_no_vig")
                if mp is not None and mk is not None:
                    gap = mp - mk
                    g = "model 3+ pts ABOVE market" if gap >= 0.03 else ("model 3+ pts BELOW market" if gap <= -0.03 else "model within 3 pts of market")
                    ml_gap[g].add(mk, hit)                           # expectation = the MARKET's probability: did the side beat what the market said?
                    ml_market["market no-vig"].add(mk, hit)
                    ml_model["hockey model"].add(mp, hit)
    out["legs"] = {"all": leg_all.cell(), "calibration_by_probability_band": {k: v.cell() for k, v in sorted(by_band.items())}, "by_market_and_threshold": {k: v.cell() for k, v in sorted(by_market.items())},
                   "calibration_by_market_and_band": {m: {b: c.cell() for b, c in sorted(bs.items())} for m, bs in sorted(by_mkt_band.items())},
                   "flagged_vs_unflagged": {k: v.cell() for k, v in by_flag.items()}, "reused_vs_not": {k: v.cell() for k, v in by_reuse.items()}, "rubric": {k: v.cell() for k, v in sorted(by_rubric.items())}}
    out["moneyline_model_vs_market"] = {"note": "expected = the MARKET's no-vig probability for the side; a model edge is real only if the side beats it", "by_model_market_gap": {k: v.cell() for k, v in ml_gap.items()},
                                         "market_probability_as_predictor": ml_market["market no-vig"].cell() if ml_market else None, "hockey_model_as_predictor": ml_model["hockey model"].cell() if ml_model else None}
    # a band that has OVER-predicted by 2+ standard errors over at least MIN_N settled legs is a standing caution on legs the engine puts in that band: evidence, not three bad days
    out["calibration_cautions"] = [{"market": m, "band": b, "lo": float(b.split("%-")[0]) / 100, "hi": float(b.split("-")[1].rstrip("%")) / 100, "n": c["n"], "predicted": c["expected_rate"], "observed": c["rate"], "z": c["z"]}
                                   for m, bs in out["legs"]["calibration_by_market_and_band"].items() for b, c in bs.items() if c["sufficient"] and c["z"] is not None and c["z"] <= -2.0]
    # --- tickets: GOOD vs QUESTIONABLE, ticket probability vs result, weakest leg, flagship
    t_all, t_q = _Acc(), collections.defaultdict(_Acc)
    t_band = collections.defaultdict(_Acc)
    weak_acc, other_acc, weak_killed = _Acc(), _Acc(), collections.Counter()
    flag_days = collections.defaultdict(list)
    for t in tickets:
        if t["status"] in ("HIT", "MISS"):
            p, hit = _ticket_p(t), t["status"] == "HIT"
            t_all.add(p, hit)
            t_q[t["quality"]].add(p, hit)
            t_band[f"{int(p * 10) * 10}-{int(p * 10) * 10 + 10}%"].add(p, hit)
            for l in t["legs"]:
                if l["outcome"] in SETTLED_LEG:
                    (weak_acc if l["leg"] and t["weakest"] and t["weakest"].startswith(str(l["leg"].get("participant_name"))) else other_acc).add(l["estimate"], l["outcome"] == "WIN")
            if not hit:
                weak_killed["weakest leg was a failing leg" if any(l["outcome"] == "LOSS" and t["weakest"] and t["weakest"].startswith(str(l["leg"].get("participant_name"))) for l in t["legs"]) else "a stronger leg failed"] += 1
        if t["quality"] == "GOOD" and t["action"] == "RECORD":
            flag_days[t["date"]].append(t)
    day_acc = collections.defaultdict(_Acc)
    for t in tickets:
        if t["status"] in ("HIT", "MISS"):
            day_acc[t["date"]].add(_ticket_p(t), t["status"] == "HIT")
    out["tickets"] = {"by_day": {d: c.cell() for d, c in sorted(day_acc.items())}, "all": t_all.cell(), "good_vs_questionable": {k: v.cell() for k, v in sorted(t_q.items())}, "ticket_probability_bands": {k: v.cell() for k, v in sorted(t_band.items())},
                      "probability_basis": "the ticket's combined probability where one is given, else the product of its legs' estimates (an independence assumption)",
                      "weakest_leg_vs_other_legs": {"weakest leg": weak_acc.cell(), "other legs": other_acc.cell()}, "why_missed_tickets_missed": dict(weak_killed)}
    fl = {}
    for day, ts in sorted(flag_days.items()):
        f = sorted(ts, key=lambda t: t["flagship_sort"])[0]
        fl[day] = {"legs": [l["leg"].get("participant_name") for l in f["legs"]], "result": f["status"]}
    settled_fl = [v for v in fl.values() if v["result"] in ("HIT", "MISS")]
    out["flagship"] = {"by_day": fl, "settled": len(settled_fl), "hit": sum(v["result"] == "HIT" for v in settled_fl), "sufficient": len(settled_fl) >= MIN_N}
    # --- the postmortem's own quality: causes, decision quality against outcome, the UNKNOWN rate
    causes, grid, per_day = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    for (day, key), lr in reviews.items():
        a = lr.get("assessment") or {}
        q, o = (a.get("decision_quality") or {}).get("label"), lr.get("outcome")
        if o in SETTLED_LEG and q:
            grid[(q, o)] += 1
            per_day[day][q] += 1
        if o == "LOSS":
            causes[a.get("classification") or "UNCLASSIFIED"] += 1
    nl = sum(causes.values())
    out["postmortem"] = {"lost_legs": nl, "cause_counts": dict(causes.most_common()), "unknown_rate": None if not nl else round(causes.get("UNKNOWN", 0) / nl, 3),
                         "unexplained_or_untested_rate": None if not nl else round((causes.get("UNKNOWN", 0) + causes.get("INSUFFICIENT_FACTS", 0)) / nl, 3),
                         "decision_quality_vs_outcome": [{"decision": k[0], "outcome": k[1], "n": v} for k, v in sorted(grid.items())],
                         "decision_quality_by_day": {d: dict(c) for d, c in sorted(per_day.items())}}
    try:
        from operational import eggy_intelligence as ei
        from research.product_models import matchup
        from operational import evidence_refresh as er
        out["evidence_health"] = er.status(now or dt.datetime.now(dt.timezone.utc))
        pp = ei.points_policy()
        out["points"] = {"enabled_thresholds": sorted(pp["enabled"]), "actionable_thresholds": pp["actionable"], "research_only_thresholds": pp["research_only"], "cases_at_or_above_the_leg_floor": pp["cases_at_or_above_the_leg_floor"],
                         "recalibrated_thresholds": sorted(pp["recalibration"]), "validated_through": pp["validated_through"],
                         "live_points_legs_settled": sum(c["n"] for k, c in out["legs"]["by_market_and_threshold"].items() if k.startswith("PLAYER_POINTS"))}
    except Exception as exc:  # noqa: BLE001 - the instrument never stops the postmortem
        out["evidence_health"] = {"state": "UNAVAILABLE", "why": f"{exc.__class__.__name__}"}
    try:
        from operational import prospective_moneyline as pm_
        if nhl_conn is not None:
            out["prospective_moneyline_refresh"] = pm_.refresh(nhl_conn)
        out["moneyline_trust"] = pm_.summarise(pm_.read_ledger())
    except Exception as exc:  # noqa: BLE001 - the instrument never stops the postmortem
        out["moneyline_trust"] = {"state": "UNAVAILABLE", "why": f"{exc.__class__.__name__}"}
    out["priced_metrics"] = {"status": "NOT AVAILABLE", "why": "no combined sportsbook price exists for the learning parlays, so no ROI, expected value or closing-line value is computed (none is shown as zero)"}
    return out
