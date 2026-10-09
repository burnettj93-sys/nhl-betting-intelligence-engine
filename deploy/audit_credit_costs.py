"""
Credit-cost audit: does the cost model behind the service plan (operational/service_plan.py) match what the provider actually charged?

For every archived provider call (operational/odds_archive/live) it compares
  * the charge the provider reported (`x-requests-last`)                          with  the model:  per-game props = markets that RETURNED data (one bookmaker, one region);
                                                                                                    league-wide moneyline = markets requested (1); events listing = 0
  * the change of the account counter (`x-requests-used`) between consecutive calls with the charge of the later call (is there any spend the headers do not show?)
and measures how many markets DraftKings had actually posted at different hours before puck drop, which is what the morning/daytime/pregame cost estimates assume.

Read-only, archive only: no network call, nothing is spent. Run: python3 deploy/audit_credit_costs.py --out docs/validation/credit_cost_audit.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def load_calls() -> list[dict]:
    from operational import best_bets as bb
    rows = []
    for p in glob.glob(str(bb._archive_dir() / "*.json")):
        try:
            doc = json.loads(Path(p).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        if meta.get("requests_last_header") is None or not meta.get("retrieved_at_utc"):
            continue
        if "evt-" in Path(p).name and "-test" in Path(p).name:
            continue                                  # a test fixture written by a worktree test run, not a provider call
        rows.append({"file": Path(p).name, "at": meta["retrieved_at_utc"], "endpoint": meta.get("endpoint", ""), "markets": (meta.get("market_filter") or ""),
                     "charged": int(meta["requests_last_header"]), "used": None if meta.get("requests_used_header") is None else int(meta["requests_used_header"]),
                     "response": doc.get("response")})
    rows.sort(key=lambda r: r["at"])
    return rows


def posted_markets(response, requested: list[str]) -> int:
    n = set()
    if isinstance(response, dict):
        for bm in response.get("bookmakers") or []:
            if bm.get("key") == "draftkings":
                for m in bm.get("markets") or []:
                    if m.get("key") in requested and m.get("outcomes"):
                        n.add(m["key"])
    return len(n)


def expected_cost(call: dict) -> int | None:
    ep = call["endpoint"]
    requested = [m for m in call["markets"].split(",") if m]
    if ep.endswith("/events"):
        return 0
    if ep.endswith("/odds") and "/events/" in ep:
        return posted_markets(call["response"], requested)
    if ep.endswith("/odds"):
        return len(requested)              # league-wide: markets requested x regions (one bookmaker group)
    return None


def run() -> dict:
    calls = load_calls()
    kinds = defaultdict(lambda: {"calls": 0, "model_matches_charge": 0, "charged": 0, "modelled": 0, "mismatches": []})
    for c in calls:
        kind = "events_listing" if c["endpoint"].endswith("/events") else ("event_props" if "/events/" in c["endpoint"] else ("league_moneyline" if c["endpoint"].endswith("/odds") else "other"))
        e = expected_cost(c)
        k = kinds[kind]
        k["calls"] += 1
        k["charged"] += c["charged"]
        if e is not None:
            k["modelled"] += e
            if e == c["charged"]:
                k["model_matches_charge"] += 1
            elif len(k["mismatches"]) < 12:
                k["mismatches"].append({"file": c["file"], "at": c["at"], "markets": c["markets"], "charged": c["charged"], "model": e})
    # counter continuity: used(n) - used(n-1) vs charge(n), over consecutive calls that both carry the counter
    gaps, matched, checked = [], 0, 0
    prev = None
    for c in calls:
        if c["used"] is not None and prev is not None and prev["used"] is not None and c["used"] >= prev["used"]:
            checked += 1
            delta = c["used"] - prev["used"]
            if delta == c["charged"]:
                matched += 1
            elif len(gaps) < 25:
                gaps.append({"at": c["at"], "counter_delta": delta, "charged_header": c["charged"], "unexplained": delta - c["charged"], "previous_call_at": prev["at"]})
        if c["used"] is not None:
            prev = c
    # what DraftKings had posted, by hours before puck drop, for the three markets the morning/daytime/pregame passes ask for
    buckets = defaultdict(lambda: {"calls": 0, "markets_requested": 0, "markets_posted": 0})
    for c in calls:
        if "/events/" not in c["endpoint"] or not isinstance(c["response"], dict):
            continue
        ct = _t(c["response"].get("commence_time"))
        if ct is None:
            continue
        hours = (ct - _t(c["at"])).total_seconds() / 3600.0
        requested = [m for m in c["markets"].split(",") if m in ("player_shots_on_goal_alternate", "player_points", "player_goal_scorer_anytime")]
        if not requested or hours <= 0:
            continue
        key = "over 24h before" if hours >= 24 else ("6-24h before" if hours >= 6 else ("3-6h before" if hours >= 3 else "under 3h before"))
        b = buckets[key]
        b["calls"] += 1
        b["markets_requested"] += len(requested)
        b["markets_posted"] += posted_markets(c["response"], requested)
    # the account cycle: from the last time the counter went DOWN (a reset) to the newest call, every credit the headers charged against the counter's own total
    reset_at, last_used, first_after = None, None, None
    cyc_charged, cyc_start_used = 0, None
    for c in calls:
        if c["used"] is None:
            continue
        if last_used is not None and c["used"] < last_used - 20:          # a reset, not two concurrent calls answering out of order
            reset_at, cyc_charged, cyc_start_used = c["at"], 0, c["used"] - c["charged"]
        if cyc_start_used is None:
            cyc_start_used = c["used"] - c["charged"]
        cyc_charged += c["charged"]
        last_used = c["used"]
    cycle = {"since_utc": reset_at or (calls[0]["at"] if calls else None), "counter_now": last_used, "counter_at_cycle_start": cyc_start_used, "sum_of_header_charges": cyc_charged,
             "counter_change": None if last_used is None else last_used - (cyc_start_used or 0),
             "every_credit_accounted": last_used is not None and (last_used - (cyc_start_used or 0)) == cyc_charged}
    return {"cycle_reconciliation": cycle, "calls_audited": len(calls), "first_call_utc": calls[0]["at"] if calls else None, "last_call_utc": calls[-1]["at"] if calls else None,
            "by_kind": {k: v for k, v in kinds.items()}, "counter_continuity": {"pairs_checked": checked, "delta_equals_charge": matched, "unexplained": gaps},
            "posted_by_hours_before_puck_drop": {k: {**v, "avg_posted_per_call": round(v["markets_posted"] / v["calls"], 2) if v["calls"] else None} for k, v in buckets.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = run()
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    for k, v in rep["by_kind"].items():
        print(f"{k:<18} calls {v['calls']:>4}  model = charge on {v['model_matches_charge']:>4}  charged {v['charged']:>4} credits, model {v['modelled']:>4}  mismatches {v['calls'] - v['model_matches_charge'] if k != 'other' else '-'}")
    cy = rep["cycle_reconciliation"]
    print(f"cycle since {cy['since_utc']}: counter {cy['counter_at_cycle_start']} -> {cy['counter_now']} (change {cy['counter_change']}) vs {cy['sum_of_header_charges']} credits charged in the headers: "
          f"{'every credit accounted' if cy['every_credit_accounted'] else 'DIFFERENCE'}")
    cc = rep["counter_continuity"]
    print(f"counter continuity: {cc['delta_equals_charge']} of {cc['pairs_checked']} consecutive pairs: counter change == header charge; {len(cc['unexplained'])} listed unexplained")
    for k, v in rep["posted_by_hours_before_puck_drop"].items():
        print(f"  {k:<18} {v['calls']:>3} calls, {v['markets_posted']} of {v['markets_requested']} requested markets had data (avg {v['avg_posted_per_call']} per call)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
