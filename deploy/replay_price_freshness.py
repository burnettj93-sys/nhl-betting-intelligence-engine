"""Replay the price-freshness rule against what the engine actually did. Read-only (a sandbox copy).

    python3 deploy/replay_price_freshness.py --runtime ~/nhl_engine_release/operational/runtime --out docs/validation/replay_price_freshness.json

  A. the stale moneyline legs the audit found: team, quote age, capture time, game start, whether a newer capture was due under the schedule, the old admission and the new one;
  B. EVERY recorded live leg judged by the rule at the moment its ticket was recorded;
  C. the candidate pool of each replayed day built twice, freshness OFF (the promoted behaviour) and ON: the hockey estimates of every leg must be identical, the eligible legs must differ only by stale prices,
     and the legs with fresh prices must come out exactly as before;
  D. representative fresh legs by market (before / after).
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _utc(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def schedule(nhl, day):
    from operational import learning_cohort as lc
    return lc._capture_context(nhl, day, None)


def pool_at(day: str, T: dt.datetime, rt: Path, freshness: bool) -> dict:
    """The reviewed candidates (cards) of `day` at T, with the freshness rule on or off."""
    from deploy import replay_full_candidates as rfc
    from operational import daily_tickets, learning_cohort as lc, learning_parlays as lp, price_freshness as pf
    tmp = Path(tempfile.mkdtemp(prefix="replay_pf_"))
    (tmp / "runtime").mkdir()
    for f in rt.iterdir():
        if f.is_file() and f.suffix != ".lock" and f.name not in ("nhl.db", "learning_cohort.db") and f.stat().st_size < 120_000_000:
            shutil.copy(f, tmp / "runtime" / f.name)
    os.environ["NHL_ENGINE_STATE_DIR"] = str(tmp)
    nhl = rfc.as_of_database(rt / "nhl.db", tmp / "nhl_asof.db", day, T)
    ledger = lc.open_ledger(":memory:")
    got = {}
    real = lp.build

    def spy(cands, already=None, slots=lp.MAX_PARLAYS_PER_DAY):
        got["cands"] = cands
        got["out"] = real(cands, already, slots)
        return got["out"]
    p1, p2 = rfc.bounded_archive(T)
    always_fresh = lambda leg, now, **k: {"status": "FRESH", "category": "MARKET_DATA_QUALITY", "market": leg.market_family, "age_min": None, "reason": "freshness rule OFF"}
    with p1, p2, mock.patch.object(lp, "build", spy), mock.patch.object(lc, "is_authorised", return_value=True), mock.patch("operational.recording_pause.is_paused", return_value=False), \
            mock.patch.object(pf, "classify", pf.classify if freshness else always_fresh):
        pool = daily_tickets.collect_candidate_legs(nhl, T, advisory_price_age=True)
        res = lc._record_cycle(ledger, nhl, T, pool, lc.phase(T))
    return {"cands": got.get("cands") or [], "out": got.get("out") or {"parlays": []}, "res": res}


def main(argv=None) -> int:
    from operational import learning_cohort as lc, learning_parlays as lp, price_freshness as pf
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rt = Path(a.runtime).expanduser()
    led = sqlite3.connect(f"file:{rt / 'learning_cohort.db'}?mode=ro", uri=True)
    led.row_factory = sqlite3.Row
    nhl_ro = sqlite3.connect(f"file:{rt / 'nhl.db'}?mode=ro", uri=True)
    nhl_ro.row_factory = sqlite3.Row
    rows = [dict(r) for r in led.execute("SELECT * FROM parlays ORDER BY et_date, created_at_utc")]
    out: dict = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "standard": {m: {k: v for k, v in x.items() if k in ("fresh_max_min", "aging_max_min")} for m, x in (pf.standard().get("markets") or {}).items()}}
    # ---- B (and A): every recorded leg at the moment its ticket was recorded
    ctx_by_day = {d: schedule(nhl_ro, d) for d in sorted({r["et_date"] for r in rows})}
    seen, recorded = set(), []
    for r in rows:
        created = _utc(r["created_at_utc"])
        for l in json.loads(r["legs_json"]):
            L = l["leg"]
            k = (r["et_date"], L["participant_name"], L["market_family"], L.get("threshold"))
            if k in seen:
                continue
            seen.add(k)
            leg = type("L", (), L)()
            c = ctx_by_day[r["et_date"]]
            res = {}
            for funded in (False, True):
                res[funded] = pf.classify(leg, created, wave_first_start=c["by_game"].get(str(L["game_id"])), first_wave_start=c["first_start"], wave_pulls_funded=funded)
            recorded.append({"day": r["et_date"], "ticket_created_utc": r["created_at_utc"], "leg": l["card"]["bet"]["label"], "team": L.get("team"), "market": L["market_family"], "hockey_estimate": round(float(l["estimate"]), 4),
                             "old_admission": "ADMITTED (the age was metadata; recorded on a ticket)", "stored_status": L["freshness_status"], "new": res[False], "new_if_wave_pulls_funded": {"status": res[True]["status"], "reason": res[True]["reason"]}})
    by = collections.Counter((x["market"], x["new"]["status"]) for x in recorded)
    stale = [x for x in recorded if x["new"]["status"] in ("STALE", "MISSING")]
    stale_keys = {(x["day"], x["leg"]) for x in stale}
    tickets_hit = sum(1 for r in rows if any((r["et_date"], l["card"]["bet"]["label"]) in stale_keys for l in json.loads(r["legs_json"])))
    out["B_all_recorded_legs"] = {"distinct_recorded_legs": len(recorded), "by_market_and_status": {f"{k[0]} {k[1]}": v for k, v in sorted(by.items())}, "rejected_for_a_stale_price": len(stale),
                                  "tickets_recorded": len(rows), "tickets_containing_a_rejected_leg": tickets_hit,
                                  "rejected_legs": [{k: v for k, v in x.items() if k in ("day", "leg", "market", "hockey_estimate", "stored_status")} | {"age_min": x["new"]["age_min"], "reason": x["new"]["reason"]} for x in stale],
                                  "would_be_aging_admitted_with_a_note": [{"day": x["day"], "leg": x["leg"], "age_min": x["new"]["age_min"]} for x in recorded if x["new"]["status"] == "AGING"],
                                  "sensitivity_wave_pulls_funded": dict(collections.Counter(x["new_if_wave_pulls_funded"]["status"] for x in recorded))}
    out["A_the_three_stale_moneyline_legs"] = [x for x in recorded if x["market"] == "MONEYLINE" and x["stored_status"] == "STALE_QUOTE" and x["new"]["age_min"] and x["new"]["age_min"] > 400]
    # ---- C and D: the candidate pool, freshness OFF against ON
    days = {}
    fresh_examples = collections.defaultdict(list)
    points = [("2026-10-24", "2026-10-24T22:54:31Z"), ("2026-10-22", None), ("2026-10-23", None), ("2026-10-24", None), ("2026-10-25", None)]
    for day, stamp in points:
        T = _utc(stamp) if stamp else _utc(min(r["created_at_utc"] for r in rows if r["et_date"] == day))
        off, on = pool_at(day, T, rt, False), pool_at(day, T, rt, True)
        co, cn = {c["key"]: c for c in off["cands"]}, {c["key"]: c for c in on["cands"]}
        common = set(co) & set(cn)
        diff = 0.0
        for k in common:
            for f in ("estimate_used", "model_probability"):
                diff = max(diff, abs(float(co[k]["card"].get(f) or 0) - float(cn[k]["card"].get(f) or 0)))
            if co[k]["card"].get("contradiction", {}).get("level") != cn[k]["card"].get("contradiction", {}).get("level") or co[k]["action"] != cn[k]["action"]:
                diff = max(diff, 1.0)
        elig_off = {k for k, c in co.items() if lp.leg_ok(c)[0]}
        elig_on = {k for k, c in cn.items() if lp.leg_ok(c)[0]}
        removed = elig_off - elig_on
        why = collections.Counter(cn[k]["card"]["market_data_quality"]["status"] for k in removed if k in cn)
        fresh = [k for k in elig_on if cn[k]["card"].get("market_data_quality", {}).get("status") == "FRESH"]
        for k in sorted(fresh)[:400]:
            m = cn[k]["leg"].market_family
            if len(fresh_examples[m]) < 2:
                fresh_examples[m].append({"day": day, "leg": cn[k]["card"]["bet"]["label"], "estimate_used": cn[k]["card"]["estimate_used"], "contradiction": cn[k]["card"]["contradiction"]["level"], "action": cn[k]["action"],
                                          "admitted_before": k in elig_off, "admitted_after": k in elig_on, "estimate_before": co[k]["card"]["estimate_used"], "flags_before": [f["name"] for f in co[k]["card"]["contradiction"]["flags"]],
                                          "flags_after": [f["name"] for f in cn[k]["card"]["contradiction"]["flags"]], "price_status": cn[k]["card"]["market_data_quality"]["status"], "age_min": cn[k]["card"]["market_data_quality"]["age_min"]})
        sel_off, sel_on = off["out"]["parlays"], on["out"]["parlays"]
        sig = lambda p: tuple(sorted(c["key"] for c in p["cands"]))
        days[f"{day} @ {T.strftime('%H:%M:%SZ')}"] = {"reviewed_legs_off": len(co), "reviewed_legs_on": len(cn), "same_legs_reviewed": len(common) == len(co) == len(cn),
                                                      "max_abs_difference_in_any_hockey_estimate_probability_contradiction_or_action_of_a_common_leg": round(diff, 6),
                                                      "eligible_off": len(elig_off), "eligible_on": len(elig_on), "legs_removed_only_for_a_stale_price": len(removed), "removed_by_status": dict(why),
                                                      "removed_legs": [{"leg": cn[k]["card"]["bet"]["label"], "hockey_estimate": cn[k]["card"]["estimate_used"], "age_min": cn[k]["card"]["market_data_quality"]["age_min"], "status": cn[k]["card"]["market_data_quality"]["status"]} for k in sorted(removed)][:8],
                                                      "eligible_fresh_legs_unchanged": all(k in elig_off for k in fresh), "aging_legs_admitted_with_a_note": sum(1 for k in elig_on if cn[k]["card"].get("market_data_quality", {}).get("status") == "AGING"),
                                                      "tickets_selected_off": len(sel_off), "tickets_selected_on": len(sel_on), "same_tickets": len({sig(p) for p in sel_off} & {sig(p) for p in sel_on}),
                                                      "tickets_containing_a_removed_leg_in_off": sum(1 for p in sel_off if any(c["key"] in removed for c in p["cands"]))}
    out["C_candidate_pool_off_vs_on"] = days
    out["D_representative_fresh_legs"] = dict(fresh_examples)
    out["points"] = "no Player Points price has been captured yet (today's plan predates the switch), so no live points leg exists to replay; the rule treats PLAYER_POINTS with the same schedule and the shots limits (tests/test_price_freshness.py)"
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: v for k, v in out.items() if k in ("B_all_recorded_legs",)}, indent=1, default=str)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
