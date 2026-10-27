"""Replay Eggy's side-pick decision on recent slates, as the engine would have made it at the time. Read-only (a sandbox copy).

    python3 deploy/replay_eggy_pick.py --runtime ~/nhl_engine_release/operational/runtime --days 2026-10-22 2026-10-23 2026-10-24 2026-10-25 --out docs/validation/replay_eggy_pick.json

PHASE 1 freezes every decision (no result is opened); PHASE 2, only after the decisions are hashed, reads what happened. At each day's decision time (the first league pull at or after the first wave's capture target, plus two minutes) it builds the point-in-time team state, takes the archived moneyline + puck-line prices captured at or before
that moment, applies the policy, and then reads what actually happened. The moneyline TRUST used is the prospective record as it stood BEFORE that day (games of earlier dates only), never today's. NOT rewound: the official rosters
and the prices' provider quote times (the capture time is used). Shadow results are what the best-ranked sides would have done: they are not bets and not a record.
"""
from __future__ import annotations

import argparse
import hashlib
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


def decide_day(rt: Path, day: str, ledger_rows: list[dict]) -> tuple[dict | None, dict]:
    """PHASE 1 for one day: the frozen decision. Takes NO connection to final results: the point-in-time database is rewound to the decision moment (`rfc.as_of_database`)."""
    from deploy import replay_full_candidates as rfc
    from operational import capture_timeline as ct, eggy_intelligence as ei, eggy_pick as ep, learning_cohort as lc, prospective_moneyline as pm
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
        return None, {"decision": "PASS", "pass_kind": "NO_VALID_FRESH_MARKET", "pass_reason": "no league pull at or after the first wave's capture target and before the first game", "captures_that_day": [c["at"].strftime("%H:%MZ") for c in caps_all]}
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
    blend = ep.blend_estimate([r for r in ledger_rows if r["date"] < day])
    cands = []
    for g in games:
        cands.extend(ep.evaluate_game(ctx, g, caps, T, cap, {"state": "INSUFFICIENT_EVIDENCE"}, blend, {}))
    doc = ep.decide(cands, now=T, structure_state={})
    frozen = {"decision_time_utc": T.strftime("%Y-%m-%dT%H:%M:%SZ"), "games_open": len(games), "candidate_sides": len(cands), "fresh_prices": sum(1 for c in cands if c["price_freshness"]["status"] == "FRESH"),
              "blend_estimate_before_the_day": blend, "edge_thesis_candidates": sum(1 for c in cands if c["qualifies"]), "decision": doc["decision"], "pass_reason": doc.get("pass_reason"),
              "pick": None if doc["decision"] != "PICK" else {**{k: doc["pick"][k] for k in ("matchup", "side", "market", "line", "price", "structure", "pick_basis", "label", "confidence", "supported_probability", "edge")},
                                                              "bet": doc["pick"]["card"]["bet"], "why_number_one": doc["pick"]["card"]["why_number_one"], "why_this_market": doc["pick"]["card"]["why_this_market"]},
              "top_three": doc["top_three"]}
    return {c["game_id"]: c for c in cands}, frozen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--days", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    from operational import eggy_pick as ep
    rt = Path(a.runtime).expanduser()
    ledger_rows = [json.loads(l) for l in (rt / "prospective_moneyline.jsonl").read_text().splitlines() if l.strip()]
    # ---- PHASE 1: every decision is made and frozen before any result is read
    decisions = {}
    for day in a.days:
        _, decisions[day] = decide_day(rt, day, ledger_rows)
    frozen_blob = json.dumps(decisions, sort_keys=True, default=str)
    digest = hashlib.sha256(frozen_blob.encode()).hexdigest()
    # ---- PHASE 2: only now are results read, as shadow information that cannot change a decision
    final = sqlite3.connect(f"file:{rt / 'nhl.db'}?mode=ro", uri=True)
    final.row_factory = sqlite3.Row
    def outcome(day, row):
        r = final.execute("SELECT home_team, home_score, away_score FROM games WHERE game_date = ? AND (home_team = ? OR away_team = ?) AND (home_team = ? OR away_team = ?)", (day, row["side"], row["side"], row["matchup"].split(" @ ")[1], row["matchup"].split(" @ ")[0])).fetchone()
        if not r or r["home_score"] is None:
            return None
        m = int(r["home_score"]) - int(r["away_score"])
        sm = m if r["home_team"] == row["side"] else -m
        if row["market"] == "MONEYLINE":
            return "WIN" if sm > 0 else "LOSS"
        return "WIN" if (sm >= 2 if row["line"] < 0 else sm > -2) else "LOSS"
    out = {"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "policy_digest": ep.policy_digest(), "decisions_frozen_sha256": digest,
           "protocol": "phase 1 made and hashed every decision with no results connection; phase 2 read results afterwards", "days": {}}
    for day, d in decisions.items():
        x = dict(d)
        if x.get("pick"):
            x["pick"] = {**x["pick"], "result_shadow_only_after_freeze": outcome(day, x["pick"])}
        x["top_three"] = [{**t, "result_shadow_only_after_freeze": outcome(day, t)} for t in x.get("top_three", [])]
        out["days"][day] = x
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print("decisions sha256:", digest)
    for day, x in out["days"].items():
        pk = x.get("pick")
        print(day, x["decision"], "|", pk["bet"] if pk else x.get("pass_reason"), "|", pk["label"] if pk else "", "|", pk["confidence"] if pk else "")
        for t in x.get("top_three", []):
            print(f"   #{t['rank']} {t['side']} {t['market']} {t['price']:+.0f} M={t['market_probability_market']:.3f} H={t['model_probability']:.3f} gap={t['gap']:+.3f} {t['trust_state']} move={t['movement_classification']}"
                  f" fam={t['evidence_families']} thesis={t['structure']} Q={t['quality']['total']:+.2f} conf={t['confidence_if_chosen']} :: {t['why_ranked_above_the_next']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
