"""As-of replay of the learning cohort's candidate pool through the intelligence review (operational/eggy_intelligence.py). READ-ONLY on production: it works on a sandboxed copy.

    python3 deploy/replay_eggy_review.py --instant 2026-10-20T23:33:45Z --date 2026-10-20 --out /tmp/pool_1933.json

What it rebuilds, as of `--instant` (UTC):
  * the games' state (every game dated `--date` or later is reset to SCHEDULED with no score),
  * the odds snapshots and the raw price archive up to that instant (later captures are hidden),
  * the cohort's candidate pool exactly as `daily_tickets.collect_candidate_legs(advisory_price_age=True)` built it, every eligibility gate applied (`NHL_ENGINE_ROSTER_GATE=off`: the roster file on disk is
    today's, not that day's, so the identity gate is the one input that cannot be replayed),
  * the review of every eligible candidate, with team and skater state built from games strictly before `--date`, and the new ordering (`eggy_intelligence.rank_key`).

Nothing is written to production: the nhl.db copy, the archive view and every state file live in a temporary directory. The old weighted score no longer exists in the code, so this tool does not recompute
it; the old ranking of a recorded bet is the `selection_rank` and `selection_score` its ledger row froze.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest import mock

os.environ["NHL_ENGINE_UNDER_TEST"] = "1"                # every state file goes to a throw-away directory, never to production
os.environ["NHL_ENGINE_ROSTER_GATE"] = "off"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)


def sandbox(instant: str, date_et: str):
    import db
    from operational import real_prop_orchestrator as rpo
    from research.live_sog_pricing import archive as arch
    tmp = tempfile.mkdtemp(prefix="eggy_replay_")
    copy = os.path.join(tmp, "nhl_copy.db")
    shutil.copy(str(db.DB_PATH), copy)
    c = sqlite3.connect(copy)
    c.execute("UPDATE games SET game_state='SCHEDULED', home_score=NULL, away_score=NULL WHERE game_date>=?", (date_et,))
    c.execute("DELETE FROM odds_snapshots WHERE received_at_utc > ?", (instant.replace("Z", ""),))
    c.commit()
    c.close()
    db.DB_PATH = type(db.DB_PATH)(copy)
    src, cut = Path(arch.ARCHIVE_DIR), instant.replace("-", "").replace(":", "")[:15]
    view = Path(tmp) / "archive"
    view.mkdir()
    for f in src.iterdir():
        if f.name[:15] <= cut:
            (view / f.name).symlink_to(f)
    empty = Path(tmp) / "empty"
    empty.mkdir()
    arch.ARCHIVE_DIR, rpo.RUNTIME_ARCHIVE_DIR, rpo.LEGACY_ARCHIVE_DIR = view, view, empty
    return tmp


def replay(instant: str, date_et: str) -> dict:
    sandbox(instant, date_et)
    import db
    from operational import daily_tickets
    from operational import eggy_intelligence as ei
    from operational import learning_cohort as lc
    now = dt.datetime.fromisoformat(instant.replace("Z", "+00:00"))
    conn = db.get_conn()
    with mock.patch("operational.moneyline_model_path.record_shadow"):
        pool = daily_tickets.collect_candidate_legs(conn, now, advisory_price_age=True)
    ctx = ei.build_context(conn, now, as_of=date_et)
    ok, why = ei.usable(ctx)
    rows, ineligible = [], Counter()
    for leg in pool["legs"]:
        reasons = lc.eligibility_reasons(leg, now)
        if reasons:
            ineligible[lc._reason_label(reasons[0])] += 1
            continue
        pleg, basis = lc.prepare(leg)
        card = ei.review_leg(ctx, pleg, basis, pleg.conservative_probability)
        ecls, ereason = ei.edge_class_of(card)
        edge, _what = ei.edge_for_class(card)
        est = float(card["estimate_used"])
        key = lc.economic_key(leg)
        rows.append({"key": key, "game_id": str(leg.game_id), "market_family": leg.market_family, "participant_id": str(leg.participant_id), "participant_name": leg.participant_name, "threshold": leg.threshold,
                     "price": leg.american_price, "label": card["bet"]["label"], "action": card["action"]["decision"], "thesis": card["thesis"]["type"], "contradiction": card["contradiction"]["level"],
                     "calibration": card["calibration_confidence"]["level"], "decision_confidence": card["decision_confidence"], "estimate_used": round(est, 4), "model_probability": card.get("model_probability"),
                     "edge": round(edge, 4), "edge_class": ecls, "rank_key": [list(ei.rank_key(card, ecls, est, edge, key))[:6]], "flags": [f["name"] + ":" + f["level"] for f in card["contradiction"]["flags"]],
                     "reasons": card["action"]["reasons"], "card": ei.compact(card)})
    rows.sort(key=lambda r: tuple(r["rank_key"][0]) + (r["key"],))
    for i, r in enumerate(rows, 1):
        r["new_rank"] = i
    return {"instant": instant, "date": date_et, "context_usable": ok, "context_note": why, "legs_in_pool": len(pool["legs"]), "eligible": len(rows), "ineligible_reasons": dict(ineligible),
            "actions": dict(Counter(r["action"] for r in rows)), "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--instant", required=True, help="UTC instant, e.g. 2026-10-20T23:33:45Z")
    ap.add_argument("--date", required=True, help="the Eastern date of the games, e.g. 2026-10-20")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = replay(a.instant, a.date)
    Path(a.out).write_text(json.dumps(doc, indent=1, default=str))
    print(f"context usable: {doc['context_usable']} {doc['context_note']}")
    print(f"pool {doc['legs_in_pool']} legs, eligible {doc['eligible']}, actions {doc['actions']}, not eligible: {doc['ineligible_reasons']}")
    for r in doc["rows"][:12]:
        print(f"{r['new_rank']:>3} {r['action']:14s} {r['label'][:60]:60s} {r['thesis'][:22]:22s} {r['contradiction']:9s} {r['calibration']:12s} est {r['estimate_used']:.3f} edge {r['edge']:+.3f}")


if __name__ == "__main__":
    main()
