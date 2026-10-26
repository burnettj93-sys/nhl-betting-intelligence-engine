"""How old may a sportsbook price be and still count as CURRENT? Derived from the capture schedule and from how prices actually moved, per market. Read-only.

    python3 deploy/analyze_price_freshness.py --runtime ~/nhl_engine_release/operational/runtime --out docs/validation/price_freshness.json

THE RULE THIS FEEDS (operational/price_freshness.py), per market:
  FRESH   age <= fresh_max: the oldest a price can be at puck drop when the capture schedule runs as designed (a game's wave capture is taken CAPTURE_TARGET_MIN before the wave's first game and a wave spans
          WAVE_MINUTES: operational/capture_timeline.py). Nothing the schedule can produce is older than this at the game's start.
  AGING   fresh_max < age <= aging_max, and no newer scheduled capture for the game is overdue: the best price that exists yet, still inside what the market's own movement allows. Usable, with a visible caution.
  STALE   anything older, or a newer scheduled capture was due and does not exist: not a current price, never a live parlay leg.
aging_max is EVIDENCE: the largest age bucket in which the 90th-percentile move of the price (between two captures that far apart) stays within one standard error of the estimate the price feeds
(the median standard error of that market's calibration cells in the cohort's own review cards), and never less than fresh_max. A market with too little history for a bucket (under MIN_PAIRS) gets no AGING
band at all: its limit is the schedule's.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import sqlite3
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BUCKETS_H = ((0, 1), (1, 2), (2, 4), (4, 8), (8, 30))
MIN_PAIRS = 100
PROP_MARKETS = {"player_shots_on_goal_alternate": "PLAYER_SOG_ALTERNATE", "player_points": "PLAYER_POINTS", "player_total_saves": "GOALIE_SAVES"}


def _p(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _imp(a: float) -> float:
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def _table(moves: dict) -> list[dict]:
    out = []
    for lo, hi in BUCKETS_H:
        v = sorted(moves.get((lo, hi), []))
        if not v:
            continue
        out.append({"gap_hours": f"{lo}-{hi}", "pairs": len(v), "median": round(statistics.median(v), 4), "p75": round(v[int(0.75 * len(v))], 4), "p90": round(v[int(0.9 * len(v))], 4), "max": round(v[-1], 4)})
    return out


def moneyline_drift(nhl) -> dict:
    g = collections.defaultdict(lambda: collections.defaultdict(dict))
    for r in nhl.execute("SELECT game_id, selection, price_american p, captured_at_utc t FROM odds_snapshots WHERE market = 'MONEYLINE'"):
        g[r["game_id"]][r["t"]][r["selection"]] = r["p"]
    moves = collections.defaultdict(list)
    for gid, ts in g.items():
        pts = []
        for t, sel in sorted(ts.items()):
            if len(sel) == 2:
                (s1, a), (s2, b) = list(sel.items())
                ia, ib = _imp(a), _imp(b)
                pts.append((_p(t), s1, ia / (ia + ib)))
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                gap = (pts[j][0] - pts[i][0]).total_seconds() / 3600
                d = abs(pts[j][2] - pts[i][2]) if pts[i][1] == pts[j][1] else abs(pts[j][2] - (1 - pts[i][2]))
                for lo, hi in BUCKETS_H:
                    if lo <= gap < hi:
                        moves[(lo, hi)].append(d)
    return moves


def prop_drift() -> dict:
    ser = collections.defaultdict(dict)
    for f in glob.glob(str(REPO / "operational" / "odds_archive" / "live" / "*.json")) + glob.glob(str(REPO / "data" / "raw" / "the_odds_api" / "live" / "*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        meta, resp = d.get("meta") or {}, d.get("response")
        if not isinstance(resp, dict) or not meta.get("retrieved_at_utc"):
            continue
        if not any(k in (meta.get("market_filter") or "") for k in PROP_MARKETS):
            continue
        t = _p(meta["retrieved_at_utc"])
        if t < dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc):
            continue
        for bm in resp.get("bookmakers", []):
            if bm.get("key") != "draftkings":
                continue
            for m in bm.get("markets", []):
                if m["key"] in PROP_MARKETS:
                    for o in m.get("outcomes", []):
                        if o.get("name") == "Over" and o.get("price") is not None:
                            ser[(resp.get("id"), m["key"], o.get("description"), o.get("point"))][t] = _imp(o["price"])
    out = {PROP_MARKETS[k]: collections.defaultdict(list) for k in PROP_MARKETS}
    for k, s in ser.items():
        ts = sorted(s)
        for i in range(len(ts) - 1):                                    # consecutive captures of the same line
            gap = (ts[i + 1] - ts[i]).total_seconds() / 3600
            for lo, hi in BUCKETS_H:
                if lo <= gap < hi:
                    out[PROP_MARKETS[k[1]]][(lo, hi)].append(abs(s[ts[i + 1]] - s[ts[i]]))
    return out


def horizon(moves: dict, se: float | None, fresh_max_min: float) -> tuple[float, str]:
    """(aging_max_min, why): the upper edge of the last bucket, reached without a break, whose 90th-percentile move is within `se`; never below fresh_max."""
    if se is None:
        return fresh_max_min, "no standard error for this market's estimates: only the schedule's limit"
    best = None
    for lo, hi in BUCKETS_H:
        v = sorted(moves.get((lo, hi), []))
        if len(v) < MIN_PAIRS:
            break
        if v[int(0.9 * len(v))] <= se:
            best = hi * 60.0
        else:
            break
    if best is None:
        return fresh_max_min, "the 90th-percentile move exceeds one standard error in every bucket with enough history: only the schedule's limit"
    return max(best, fresh_max_min), f"the 90th-percentile move stays within one standard error ({se:.4f}) up to {best / 60:.0f} hours"


def main(argv=None) -> int:
    from operational import capture_timeline as ct
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rt = Path(a.runtime).expanduser()
    nhl = sqlite3.connect(f"file:{rt / 'nhl.db'}?mode=ro", uri=True)
    nhl.row_factory = sqlite3.Row
    led = sqlite3.connect(f"file:{rt / 'learning_cohort.db'}?mode=ro", uri=True)
    led.row_factory = sqlite3.Row
    fresh_max = float(ct.CAPTURE_TARGET_MIN + ct.WAVE_MINUTES)
    ses, ages = collections.defaultdict(list), collections.defaultdict(list)
    seen = set()
    for r in led.execute("SELECT et_date, created_at_utc, legs_json FROM parlays ORDER BY created_at_utc"):
        for l in json.loads(r["legs_json"]):
            L = l["leg"]
            k = (r["et_date"], L["participant_name"], L["market_family"], L.get("threshold"))
            if k in seen:
                continue
            seen.add(k)
            ev = (l["card"].get("calibration_confidence") or {}).get("evidence") or {}
            se = ev.get("se", ev.get("standard_error"))
            if se:
                ses[L["market_family"]].append(float(se))
            obs = L.get("retrieved_at_utc") or L.get("captured_at_utc")
            if obs:
                ages[L["market_family"]].append(round((_p(r["created_at_utc"]) - _p(obs)).total_seconds() / 60.0, 1))
    drift = {"MONEYLINE": moneyline_drift(nhl), **prop_drift()}
    out = {"method": "python3 deploy/analyze_price_freshness.py --runtime <runtime> --out docs/validation/price_freshness.json",
           "rule": "FRESH age <= fresh_max_min; AGING up to aging_max_min while no newer scheduled capture is overdue; STALE beyond that or when a newer scheduled capture was due and does not exist",
           "cadence": {"capture_target_min": ct.CAPTURE_TARGET_MIN, "publish_deadline_min": ct.PUBLISH_DEADLINE_MIN, "wave_minutes": ct.WAVE_MINUTES, "overdue_tolerance_min": 10.0,
                       "fresh_max_min": fresh_max,
                       "why": "a wave's coordinated capture is taken capture_target_min before its first game and a wave spans wave_minutes, so no price the schedule produces is older than their sum at any game's start; "
                              "a later wave's moneyline is refreshed only when the plan funds a wave pull (operational/capture_timeline.py league_pull_due)"},
           "markets": {}}
    for m in ("MONEYLINE", "PLAYER_SOG_ALTERNATE", "PLAYER_POINTS", "GOALIE_SAVES"):
        se = statistics.median(ses[m]) if ses.get(m) else (statistics.median(ses["PLAYER_SOG_ALTERNATE"]) if m in ("PLAYER_POINTS", "GOALIE_SAVES") and ses.get("PLAYER_SOG_ALTERNATE") else None)
        mv = drift.get(m) or {}
        aging, why = horizon(mv, se, fresh_max)
        out["markets"][m] = {"fresh_max_min": fresh_max, "aging_max_min": aging, "standard_error_of_the_estimate": None if se is None else round(se, 4),
                             "se_source": "this market's calibration cells in the cohort's review cards" if ses.get(m) else "the shots calibration cells (this market has none yet)",
                             "drift_of_the_price_between_captures": _table(mv), "aging_limit_reason": why,
                             "recorded_legs_age_at_decision_min": None if not ages.get(m) else {"n": len(ages[m]), "min": min(ages[m]), "median": statistics.median(ages[m]), "max": max(ages[m])}}
    out["markets"]["PLAYER_GOALS"] = {**out["markets"]["PLAYER_SOG_ALTERNATE"], "note": "uses the shots limits (the same capture)"}
    out["markets"]["PLAYER_SOG"] = {**out["markets"]["PLAYER_SOG_ALTERNATE"], "note": "uses the shots limits (the same capture)"}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({m: {k: v for k, v in x.items() if k in ("fresh_max_min", "aging_max_min", "standard_error_of_the_estimate", "aging_limit_reason")} for m, x in out["markets"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
