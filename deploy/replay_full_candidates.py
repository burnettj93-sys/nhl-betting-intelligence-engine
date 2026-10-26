"""Replay the WHOLE candidate-generation and selection process for a past day, old logic against new, from what the system had at the time. Read-only: everything runs in a sandbox copy.

    python3 deploy/replay_full_candidates.py --runtime ~/nhl_engine_release/operational/runtime --days 2026-10-22 2026-10-23 2026-10-24 2026-10-25 --out docs/validation/replay_full_candidates.json

For each day, at the moment the cohort first recorded that day (T):
  raw archived odds captured at or before T  ->  the legs the adapters build  ->  the cohort's eligibility gates  ->  the review (point-in-time skater, team and goalie state: only games before the day)
  ->  eligible parlay legs  ->  candidate tickets  ->  ranking  ->  portfolio selection, by the OLD logic (rank order, name-by-name contradiction, the earlier slot arithmetic) and the NEW logic.
The REVIEW is the current code on both sides, so the comparison isolates the selection and portfolio logic: the opportunity set is identical for old and new.

WHAT IS RECONSTRUCTED FROM THE PAST AND WHAT IS NOT (every run says so in its output): the odds payloads (archived with their retrieval times: only those at or before T), the schedule and results (games on or after the day are
reset to scheduled and their box scores removed), the skater / team / goalie state (rows before the day only), the moneyline snapshots (rows at or before T). NOT reconstructable: the official rosters (the current file is used),
the postmortem lessons (the current document is used, filtered to dates before the day), the moneyline shadow log's model columns, and the prospective moneyline record (current). A day whose reconstructed pool does not
contain the legs the cohort actually recorded is reported as NOT FAITHFUL, with the recall.
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


def _utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def old_combine(flags):
    lv = [f["level"] for f in flags]
    if "SEVERE" in lv or lv.count("HIGH") >= 2:
        return "SEVERE"
    if "HIGH" in lv or lv.count("MODERATE") >= 2:
        return "HIGH"
    if "MODERATE" in lv:
        return "MODERATE"
    return "LOW"


def as_of_database(src: Path, dst: Path, day: str, T: dt.datetime) -> sqlite3.Connection:
    shutil.copy(src, dst)
    c = sqlite3.connect(dst)
    c.row_factory = sqlite3.Row
    stamp = T.strftime("%Y-%m-%dT%H:%M:%S")
    ids = [r[0] for r in c.execute("SELECT game_id FROM games WHERE game_date >= ?", (day,))]
    q = ",".join("?" * len(ids)) or "NULL"
    for t in ("player_game_stats", "goalie_game_stats", "team_game_stats"):
        c.execute(f"DELETE FROM {t} WHERE game_id IN ({q})", ids)
    c.execute(f"UPDATE games SET game_state='SCHEDULED', home_score=NULL, away_score=NULL, final_period_type=NULL, result_observed_at_utc=NULL WHERE game_date >= ?", (day,))
    c.execute("DELETE FROM odds_snapshots WHERE captured_at_utc > ?", (stamp,))
    c.commit()
    return c


def bounded_archive(T: dt.datetime):
    """The archive readers as they were at T: nothing captured after T is visible."""
    from operational import best_bets
    from operational import real_prop_orchestrator as rpo
    orig_recent, orig_latest = rpo._recent_archive_payloads, best_bets.latest_capture

    def recent(market_key, sportsbook="draftkings", max_age_hours=24.0, now=None, **kw):
        out = []
        for resp in orig_recent(market_key, sportsbook, max_age_hours + 9999.0, T, **{**kw, "newest_per_event": False, "tag_retrieval": True}):
            r = resp.get("__retrieved_at_utc")
            if r and _utc(r) <= T and (T - _utc(r)).total_seconds() <= max_age_hours * 3600.0:
                out.append(resp)
        if kw.get("newest_per_event"):
            newest = {}
            for resp in out:
                eid = resp.get("id")
                if eid is not None and (eid not in newest or _utc(resp["__retrieved_at_utc"]) > _utc(newest[eid]["__retrieved_at_utc"])):
                    newest[eid] = resp
            out = list(newest.values())
        return out if kw.get("tag_retrieval") else [{k: v for k, v in r.items() if k != "__retrieved_at_utc"} for r in out]

    def latest(event_id, archive_dir=None, *, require_points=True, market=None):
        import glob
        best = None
        for path in glob.glob(str((archive_dir or best_bets._archive_dir()) / f"*events-{event_id}-odds*.json")):
            try:
                doc = json.loads(Path(path).read_text())
            except (OSError, ValueError):
                continue
            meta = doc.get("meta") or {}
            filt = meta.get("market_filter") or ""
            if market is not None:
                if market not in filt:
                    continue
            elif "player_shots_on_goal_alternate" not in filt or (require_points and "player_points" not in filt):
                continue
            ts = meta.get("retrieved_at_utc")
            if not ts or _utc(ts) > T:
                continue
            got = _utc(ts)
            if best is None or got > best[0]:
                best = (got, doc["response"])
        return best
    return mock.patch.object(rpo, "_recent_archive_payloads", recent), mock.patch.object(best_bets, "latest_capture", latest)


def run_day(day: str, T: dt.datetime, rt: Path, recorded_keys: set, recorded_tickets: list) -> dict:
    from operational import daily_tickets, eggy_intelligence as ei, learning_cohort as lc, learning_parlays as lp
    tmp = Path(tempfile.mkdtemp(prefix="replay_"))
    (tmp / "runtime").mkdir()
    for f in rt.iterdir():
        if f.is_file() and f.suffix != ".lock" and f.name not in ("nhl.db", "learning_cohort.db") and f.stat().st_size < 120_000_000:
            shutil.copy(f, tmp / "runtime" / f.name)
    os.environ["NHL_ENGINE_STATE_DIR"] = str(tmp)
    nhl = as_of_database(rt / "nhl.db", tmp / "nhl_asof.db", day, T)
    ledger = lc.open_ledger(":memory:")
    capture = {}
    real_build = lp.build

    def spy(cands, already=None, slots=lp.MAX_PARLAYS_PER_DAY):
        capture["cands"] = cands
        capture["slots"] = slots
        return real_build(cands, already, slots)
    p1, p2 = bounded_archive(T)
    with p1, p2, mock.patch.object(lp, "build", spy), mock.patch.object(lc, "is_authorised", return_value=True), mock.patch("operational.recording_pause.is_paused", return_value=False):
        pool = daily_tickets.collect_candidate_legs(nhl, T, advisory_price_age=True)
        ph = lc.phase(T)
        res = lc._record_cycle(ledger, nhl, T, pool, ph)
    legs = pool.get("legs") or []
    cands = capture.get("cands") or []
    out = {"decision_time_utc": T.strftime("%Y-%m-%dT%H:%M:%SZ"), "raw_candidate_legs": len(legs), "by_market": dict(collections.Counter(l.market_family for l in legs)), "reviewed_legs": len(cands),
           "pool_sources": {k: (v.get("legs") if isinstance(v, dict) else None) for k, v in (pool.get("sources") or {}).items()}}
    ident = lambda l: (str(l["game_id"]), str(l["participant_id"]), l["market_family"], l.get("threshold"))
    have = {ident(dataclasses.asdict(c["leg"])) for c in cands}
    first = {ident(l["leg"]) for r in recorded_tickets if r["created_at_utc"] <= (T + dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ") for l in json.loads(r["legs_json"])}
    allrec = {ident(l["leg"]) for r in recorded_tickets for l in json.loads(r["legs_json"])}
    out["faithfulness"] = {"legs_in_the_tickets_the_cohort_recorded_at_T": len(first), "of_which_present_in_the_reconstructed_reviewed_pool": len(first & have),
                           "recall_of_the_first_batch": None if not first else round(len(first & have) / len(first), 3), "legs_of_the_whole_day_present": f"{len(allrec & have)} of {len(allrec)}",
                           "missing_first_batch_legs": sorted(f"{g}/{p}/{m}{t}" for g, p, m, t in first - have)[:12]}
    if not cands:
        out["error"] = "no reviewed candidates were reconstructed"
        out["cycle"] = {k: res.get(k) for k in ("status", "recording")}
        return out
    fields_old = {"neutral": True}

    def build_with(neutral: bool):
        with mock.patch.object(lp, "concentration_key", (lambda v, kept: (0, 0, 0, 0, 0, 0, 0)) if neutral else lp.concentration_key), \
                mock.patch.object(ei, "combine_contradiction", old_combine if neutral else ei.combine_contradiction):
            old_slots = lp.MAX_PARLAYS_PER_DAY
            if neutral:
                return build_old(cands, old_slots)
            return real_build(cands, [], lp.MAX_PARLAYS_PER_DAY)

    def build_old(cs, slots):
        """The earlier builder: rank order, the earlier pool size (14) and the earlier slot arithmetic (a chosen ticket counted twice)."""
        with mock.patch.object(lp, "POOL_LEGS", 14):
            import itertools
            res = real_build_old(cs, slots)
        return res

    def real_build_old(cs, slots):
        # the earlier loop, reproduced on top of the same candidate and ticket construction
        ok = [c for c in cs if lp.leg_ok(c)[0]]
        ok.sort(key=lambda c: c["rank_key"])
        pool_ = ok[:14]
        import itertools
        built = []
        for combo in itertools.combinations(pool_, 3):
            p = lp.construct(list(combo))
            if p:
                built.append(p)
                for extra in pool_:
                    if extra in combo or float(extra["card"]["estimate_used"]) < max(lp.MIN_FOURTH_P, p["weakest_leg"]["estimate"]):
                        continue
                    q = lp.construct(list(combo) + [extra])
                    if q and q["construction_quality"] == p["construction_quality"] and q["action"] == "RECORD":
                        built.append(q)
        built.sort(key=lp.rank_key)
        used = collections.Counter()
        kept_sets, chosen = [], []
        for p in built:
            if len(chosen) + len(kept_sets) >= slots or p["action"] != "RECORD":
                continue
            ks = {c["key"] for c in p["cands"]}
            if any(used[k] >= lp.LEG_REUSE for k in ks) or any(len(ks & other) >= min(len(ks), len(other)) - 1 for other in kept_sets):
                continue
            chosen.append(p); kept_sets.append(ks); used.update(ks)
        return {"parlays": chosen, "eligible_legs": len(ok), "constructed": len(built)}

    new = real_build(cands, [], lp.MAX_PARLAYS_PER_DAY)
    with mock.patch.object(lp, "concentration_key", lambda v, kept: (0, 0, 0, 0, 0, 0, 0)):            # the SAME corrected builder (pool, slots, contradiction rule) with the diversification switched off: rank order
        rank_only = real_build(cands, [], lp.MAX_PARLAYS_PER_DAY)
    with mock.patch.object(ei, "combine_contradiction", old_combine):
        old = build_old(cands, lp.MAX_PARLAYS_PER_DAY)
    # the portfolio of each selection, assessed by the SAME function (the new, dependence-aware one)
    def assess(tickets):
        return lp.portfolio_summary([lp.view_of_candidates(p) for p in tickets])
    keep = ("tickets", "distinct_legs", "leg_slots", "expected_tickets_hit", "chance_no_ticket_hits", "chance_no_ticket_hits_range", "spread_inflation_from_shared_legs", "player_exposure", "team_exposure", "game_exposure",
            "most_reused_leg", "tickets_with_a_role_flagged_leg", "tickets_with_a_model_risk_leg")
    flag = lambda ts: None if not lp.flagship(ts) else [c["leg"].participant_name for c in lp.flagship(ts)["cands"]]
    sig = lambda p: tuple(sorted(c["key"] for c in p["cands"]))
    po, pn, pr = assess(old["parlays"]), assess(new["parlays"]), assess(rank_only["parlays"])
    with mock.patch.object(ei, "combine_contradiction", old_combine):
        pass
    n_eq = min(len(rank_only["parlays"]), len(new["parlays"]))                                  # the same NUMBER of tickets from each, in the order each chose them: diversification without more volume
    eq_r, eq_n = assess(rank_only["parlays"][:n_eq]), assess(new["parlays"][:n_eq])
    out.update({"equal_volume": {"tickets_each": n_eq, "rank_order_only": {k: eq_r.get(k) for k in keep}, "portfolio_aware": {k: eq_n.get(k) for k in keep}},
                "eligible_legs": new["eligible_legs"], "eligible_legs_under_the_old_contradiction_rule": old["eligible_legs"], "candidate_tickets": new["constructed"], "candidate_tickets_old": old["constructed"],
                "selected_old": len(old["parlays"]), "selected_new": len(new["parlays"]), "same_tickets": len({sig(p) for p in old["parlays"]} & {sig(p) for p in new["parlays"]}),
                "qualities_old": dict(collections.Counter(p["construction_quality"] for p in old["parlays"])), "qualities_new": dict(collections.Counter(p["construction_quality"] for p in new["parlays"])),
                "selected_rank_order_only_corrected_builder": len(rank_only["parlays"]), "same_tickets_new_vs_rank_order_only": len({sig(p) for p in rank_only["parlays"]} & {sig(p) for p in new["parlays"]}),
                "portfolio_old": {k: po.get(k) for k in keep}, "portfolio_rank_order_only": {k: pr.get(k) for k in keep}, "portfolio_new": {k: pn.get(k) for k in keep}, "flagship_rank_order_only": flag(rank_only["parlays"]),
                "qualities_rank_order_only": dict(collections.Counter(p["construction_quality"] for p in rank_only["parlays"])),
                "cycle_recording_under_the_wave_caps": {k: (res.get("recording") or {}).get(k) for k in ("recorded_this_cycle", "recorded_today")}, "flagship_old": flag(old["parlays"]), "flagship_new": flag(new["parlays"]),
                "tickets_old": [[c["card"]["bet"]["label"] for c in p["cands"]] for p in old["parlays"]], "tickets_new": [[c["card"]["bet"]["label"] for c in p["cands"]] for p in new["parlays"]],
                "recorded_that_day_by_the_live_cohort": len(recorded_tickets)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--days", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rt = Path(a.runtime).expanduser()
    led = sqlite3.connect(rt / "learning_cohort.db")
    led.row_factory = sqlite3.Row
    out = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "method": __doc__.split("WHAT IS RECONSTRUCTED")[1].split("\n\n")[0][:1200] if False else "see the module docstring", "days": {}}
    for day in a.days:
        rows = [dict(r) for r in led.execute("SELECT * FROM parlays WHERE et_date = ? ORDER BY parlay_rank", (day,))]
        T = _utc(min(r["created_at_utc"] for r in rows))
        keys = {l["key"] for r in rows for l in json.loads(r["legs_json"])}
        try:
            out["days"][day] = run_day(day, T, rt, keys, rows)
        except Exception as exc:  # noqa: BLE001
            import traceback
            out["days"][day] = {"decision_time_utc": T.strftime("%Y-%m-%dT%H:%M:%SZ"), "error": f"{exc.__class__.__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({d: {k: v for k, v in x.items() if k not in ("tickets_old", "tickets_new", "portfolio_old", "portfolio_new", "trace")} for d, x in out["days"].items()}, indent=1, default=str)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
