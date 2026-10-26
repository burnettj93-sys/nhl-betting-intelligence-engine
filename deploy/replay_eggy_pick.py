"""Replay Eggy's side-pick decision on recent slates, as the engine would have made it at the time. Read-only (a sandbox copy).

    python3 deploy/replay_eggy_pick.py --runtime ~/nhl_engine_release/operational/runtime --days 2026-10-22 2026-10-23 2026-10-24 2026-10-25 --out docs/validation/replay_eggy_pick.json

At each day's decision time (the first league pull at or after the first wave's capture target, plus two minutes) it builds the point-in-time team state, takes the archived moneyline + puck-line prices captured at or before
that moment, applies the policy, and then reads what actually happened. The moneyline TRUST used is the prospective record as it stood BEFORE that day (games of earlier dates only), never today's. NOT rewound: the official rosters
and the prices' provider quote times (the capture time is used). Shadow results are what the best-ranked sides would have done: they are not bets and not a record.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv=None) -> int:
    from deploy import replay_full_candidates as rfc
    from operational import capture_timeline as ct, eggy_intelligence as ei, eggy_pick as ep, eggy_pick_ledger as epl, learning_cohort as lc, prospective_moneyline as pm
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--days", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rt = Path(a.runtime).expanduser()
    final = sqlite3.connect(f"file:{rt / 'nhl.db'}?mode=ro", uri=True)
    final.row_factory = sqlite3.Row
    ledger_rows = [json.loads(l) for l in (rt / "prospective_moneyline.jsonl").read_text().splitlines() if l.strip()]
    out = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "policy_digest": ep.policy_digest(), "days": {}}
    for day in a.days:
        tmp = Path(tempfile.mkdtemp(prefix="replay_eggy_"))
        (tmp / "runtime").mkdir()
        for f in rt.iterdir():
            if f.is_file() and f.suffix != ".lock" and f.name not in ("nhl.db", "learning_cohort.db") and f.stat().st_size < 120_000_000:
                shutil.copy(f, tmp / "runtime" / f.name)
        os.environ["NHL_ENGINE_STATE_DIR"] = str(tmp)
        probe = rfc.as_of_database(rt / "nhl.db", tmp / "probe.db", day, dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
        starts = {str(r["game_id"]): dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00")).replace(tzinfo=dt.timezone.utc) if "+" not in r["scheduled_start_utc"] else dt.datetime.fromisoformat(r["scheduled_start_utc"])
                  for r in probe.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ?", (day,))}
        waves = ct.waves(starts)
        target = waves[0].first_start - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN)
        caps_all = ep.league_captures(day, dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
        ready = next((c for c in caps_all if c["at"] >= target - dt.timedelta(minutes=10) and c["at"] < waves[0].first_start - dt.timedelta(minutes=15)), None)
        if ready is None:
            out["days"][day] = {"decision": "PASS", "pass_reason": "no league pull at or after the first wave's capture target and before the first game: the engine would have passed for want of current prices", "captures_that_day": [c["at"].strftime("%H:%MZ") for c in caps_all]}
            continue
        T = ready["at"] + dt.timedelta(minutes=2)
        nhl = rfc.as_of_database(rt / "nhl.db", tmp / "asof.db", day, T)
        ctx = ei.build_context(nhl, T, live_overlay=True)
        ctx.moneyline_trust = pm.summarise([r for r in ledger_rows if r["date"] < day])                 # the trust record as it stood BEFORE this day
        cap = lc._capture_context(nhl, day, T)
        caps = ep.league_captures(day, T)
        games = []
        for r in nhl.execute("SELECT game_id, home_team, away_team, scheduled_start_utc FROM games WHERE game_date = ? ORDER BY scheduled_start_utc", (day,)):
            t = starts[str(r["game_id"])]
            if t > T:
                games.append({"game_id": r["game_id"], "home": r["home_team"], "away": r["away_team"], "start_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": day})
        prior = [r for r in ledger_rows if r["date"] < day]
        blend = ep.blend_estimate(prior)
        cands = []
        for g in games:
            cands.extend(ep.evaluate_game(ctx, g, caps, T, cap, {"state": "INSUFFICIENT_EVIDENCE"}, blend, {}))
        doc = ep.decide(cands, now=T, structure_state={})
        res = {}
        for r in final.execute("SELECT game_id, home_team, away_team, home_score, away_score FROM games WHERE game_date = ?", (day,)):
            res[str(r["game_id"])] = (r["home_team"], int(r["home_score"] or 0) - int(r["away_score"] or 0))
        def outcome(c):                                                # read ONLY after the decision is made: never an input to it
            home, m = res.get(str(c["game_id"]), (None, None))
            if m is None:
                return None
            sm = m if c["side"] == home else -m
            if c["market"] == "MONEYLINE":
                return "WIN" if sm > 0 else "LOSS"
            return "WIN" if (sm >= 2 if c["line"] < 0 else sm > -2) else "LOSS"
        by_key = {(c["matchup"], c["side"], c["market"]): c for c in cands}
        top3 = []
        for t in doc["top_three"]:
            full = by_key[(t["matchup"], t["side"], t["market"])]
            top3.append({**t, "result_if_bet_shadow_only": outcome(full)})
        in_region = [c for c in cands if c["price_class"] == "IN_PREFERRED_REGION"]
        out["days"][day] = {"decision_time_utc": T.strftime("%Y-%m-%dT%H:%M:%SZ"), "games_open": len(games), "candidate_sides": len(cands), "in_preferred_region": len(in_region),
                            "fresh_prices": sum(1 for c in cands if c["price_freshness"]["status"] == "FRESH"), "blend_estimate_before_the_day": blend,
                            "qualifying_sides": sum(1 for c in cands if c["qualifies"]), "structures_qualifying": dict(collections.Counter(c["structure"] for c in cands if c["qualifies"])),
                            "decision": doc["decision"], "pass_reason": doc.get("pass_reason"),
                            "pick": None if doc["decision"] != "PICK" else {k: doc["pick"][k] for k in ("matchup", "side", "market", "line", "price", "structure", "pick_basis", "supported_probability", "edge", "confidence")} | {"result_shadow_only": outcome(doc["pick"])},
                            "top_three": top3,
                            "consensus_quality_sides": [{**ep.summarize(c), "result_if_bet_shadow_only": outcome(c)} for c in cands if c["edge_thesis"]["consensus_quality"]]}
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    for d, x in out["days"].items():
        print(d, x.get("decision"), "|", (x.get("pass_reason") or "")[:260])
        for t in x.get("top_three", []):
            print("    ", t["side"], t["market"], t["price"], f"M={t['market_probability_market']:.3f} H={t['model_probability']:.3f} gap={t['gap']:+.3f} {t['trust_state']} -> {t['structure']} qualifies={t['qualifies']} families={t['edge_thesis']['families']} independent={t['edge_thesis']['independent']} {t['result_if_bet_shadow_only']}")
        for t in x.get("consensus_quality_sides", []):
            print("   [consensus-quality state]", t["side"], t["market"], t["price"], "->", t["structure"], "|", t["edge_thesis"]["thesis"][:100], "|", (t["not_qualifying_because"] or [""])[0][:150])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
