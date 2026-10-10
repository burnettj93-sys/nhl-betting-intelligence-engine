"""
Scores the shadow selection log (operational/shadow_selection.py) against official results. Read-only.

It answers the question nine tickets cannot: on the legs the selector actually picks (the ones where the model disagrees most with the price), is the model's probability overstated?
  * every priced leg is counted ONCE per day, at the first time it was logged (so a leg whose price was refreshed three times is one event, and nothing is chosen with hindsight);
  * legs are split into "all priced legs" and "passed the edge filter" (the ones that can reach a ticket), by market and by probability band, with the average the model said, the average the
    PRICE implied, and what happened;
  * every policy variant's tickets are counted once per day, with their expected wins at the logged probabilities and the wins that happened;
  * a plain statement of how many resolved legs there are against the number needed to see a 5-point overstatement (619), so nobody reads a trend into a small sample.

Run: python3 deploy/score_selection_shadow.py [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from operational import postmortem_math as pm  # noqa: E402

BANDS = [(0.0, 0.15), (0.15, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 1.01)]
FAMILIES = {"PLAYER_SOG_ALTERNATE": "shots on goal", "PLAYER_SOG": "shots on goal", "PLAYER_POINTS": "points", "PLAYER_GOALS": "goals"}
FINAL = ("FINAL", "OFF")


def implied(american: float) -> float:
    return 100.0 / (american + 100.0) if american > 0 else -american / (-american + 100.0)


def db_resolver(path: Path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def resolve(game_id: str, player_id: str, family: str, threshold) -> dict:
        g = conn.execute("SELECT game_state FROM games WHERE game_id = ?", (str(game_id),)).fetchone()
        if not g or g["game_state"] not in FINAL:
            return {"status": "PENDING"}
        s = conn.execute("SELECT shots, goals, assists, played FROM player_game_stats WHERE game_id = ? AND player_id = ? ORDER BY revision_number DESC LIMIT 1", (str(game_id), str(player_id))).fetchone()
        if not s or not s["played"]:
            return {"status": "NO_PARTICIPATION"}
        value = s["shots"] if "SOG" in family else (s["goals"] if "GOALS" in family else s["goals"] + s["assists"])
        return {"status": "FINAL", "hit": value >= int(threshold or 1)}
    return resolve


def _unit(l: dict) -> tuple:
    """The independent unit: one player in one market on one night. A player's 2+, 3+, 4+ and 5+ shots resolve together, so they are ONE unit however many lines were logged."""
    fam = "SOG" if "SOG" in l["family"] else l["family"]
    return (l["date"], l["game"], l["player"], fam)


def _band(p: float) -> str:
    for lo, hi in BANDS:
        if lo <= p < hi:
            return f"{lo:.2f}-{min(hi, 1.0):.2f}"
    return "n/a"


def score(records: list[dict], resolve) -> dict:
    first: dict = {}
    for r in records:
        for row in r["pool"]:
            gid, pid, fam, k, price, p, name, team, start, qt, edge = row
            key = (r["et_date"], gid, pid, fam, k)
            if key not in first:
                first[key] = {"game": gid, "player": pid, "family": fam, "threshold": k, "price": price, "p": p, "edge": bool(edge), "date": r["et_date"]}
    legs, pending, no_part = [], 0, 0
    for key, l in first.items():
        res = resolve(l["game"], l["player"], l["family"], l["threshold"])
        if res["status"] == "PENDING":
            pending += 1
        elif res["status"] == "NO_PARTICIPATION":
            no_part += 1
        else:
            legs.append({**l, "hit": res["hit"], "implied": implied(l["price"])})

    def table(sel: list[dict]) -> dict:
        out = {"all": _row(sel), "by_market": {}, "by_band": {}}
        for fam_key, label in FAMILIES.items():
            part = [l for l in sel if l["family"] == fam_key]
            if part:
                out["by_market"].setdefault(label, []).extend(part)
        out["by_market"] = {k: _row(v) for k, v in out["by_market"].items()}
        for lo, hi in BANDS:
            part = [l for l in sel if lo <= l["p"] < hi]
            if part:
                out["by_band"][f"{lo:.2f}-{min(hi, 1.0):.2f}"] = _row(part)
        return out

    edge_legs = [l for l in legs if l["edge"]]
    for l in legs:
        l["edge_size"] = l["p"] * (1.0 + (l["price"] / 100.0 if l["price"] > 0 else 100.0 / abs(l["price"]))) - 1.0
    top = sorted(edge_legs, key=lambda l: -l["edge_size"])[: len(edge_legs) // 4]
    variants: dict = {}
    seen_tickets: set = set()
    for r in records:
        for name, v in r["variants"].items():
            for t in v["tickets"]:
                tk = (r["et_date"], name, tuple(sorted(x[0] for x in t["legs"])))
                if tk in seen_tickets:
                    continue
                seen_tickets.add(tk)
                outs = []
                for lid, label, price, p in t["legs"]:
                    gid, pid, fam, k = lid.split(":")
                    outs.append(resolve(gid, pid, fam, k))
                slot = variants.setdefault(name, {"label": v["label"], "tickets_logged": 0, "tickets_resolved": 0, "wins": 0, "expected_wins_of_resolved": 0.0})
                slot["tickets_logged"] += 1
                if all(o["status"] == "FINAL" for o in outs):
                    slot["tickets_resolved"] += 1
                    slot["wins"] += int(all(o["hit"] for o in outs))
                    slot["expected_wins_of_resolved"] += t["hit_probability"]
    for v in variants.values():
        v["expected_wins_of_resolved"] = round(v["expected_wins_of_resolved"], 2)
    needed = pm.legs_needed(0.05)
    needed_clustered = (pm.legs_needed(0.05, deff=1.12), pm.legs_needed(0.05, deff=1.98))
    edge_units = len({_unit(l) for l in edge_legs})
    return {"records": len(records), "days": sorted({r["et_date"] for r in records}), "distinct_legs_logged": len(first), "resolved": len(legs), "awaiting_games": pending, "player_did_not_play": no_part,
            "all_priced_legs": table(legs), "legs_that_passed_the_edge_filter": table(edge_legs), "policy_variants": variants,
            "top_quarter_by_edge_size": table(top), "model_versus_price_information": blend_report(legs),
            "enough_to_judge": {"resolved_edge_legs": len(edge_legs), "independent_units_player_market_nights": edge_units, "needed_to_see_a_5_point_overstatement": needed,
                                "needed_allowing_for_clustering": {"light": needed_clustered[0], "heavy": needed_clustered[1]},
                                "verdict": "NOT ENOUGH YET" if edge_units < needed_clustered[0] else "enough to read, with the usual care",
                                "note": ("The count that matters is independent units, not leg rows: a player's 2+ and 3+ shots are one event. The clustering figures allow for legs in a game or night sharing luck or model error. "
                                         "This tests the edge-filtered legs (what a ticket can be built from); the chosen legs are the extreme end of that group, so the top quarter by edge is shown apart. "
                                         "Each market tested alone needs its own sample.")}}


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _solve3(a: list[list[float]], b: list[float]) -> list[float]:
    m = [row[:] + [bb] for row, bb in zip(a, b)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        if abs(m[i][i]) < 1e-12:
            raise ZeroDivisionError("singular")
        for r in range(3):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i])]
    return [m[i][3] / m[i][i] for i in range(3)]


def logistic_blend(legs: list[dict], iters: int = 25) -> list[float] | None:
    """outcome ~ logistic(b0 + b1*logit(model) + b2*logit(price-implied)), by Newton steps. b1 high, b2 near 0: the model carries the information; b1 near 0, b2 high: the price does. None if it cannot be fitted."""
    xs = [(1.0, _logit(l["p"]), _logit(l["implied"])) for l in legs]
    ys = [1.0 if l["hit"] else 0.0 for l in legs]
    if len(xs) < 30 or len(set(ys)) < 2:
        return None
    b = [0.0, 1.0, 0.0]
    try:
        for _ in range(iters):
            g = [0.0] * 3
            h = [[1e-6 if i == j else 0.0 for j in range(3)] for i in range(3)]
            for x, y in zip(xs, ys):
                z = sum(bi * xi for bi, xi in zip(b, x))
                p = 1.0 / (1.0 + math.exp(-max(min(z, 30), -30)))
                w = p * (1 - p)
                for i in range(3):
                    g[i] += (y - p) * x[i]
                    for j in range(3):
                        h[i][j] += w * x[i] * x[j]
            step = _solve3(h, g)
            b = [bi + si for bi, si in zip(b, step)]
            if max(abs(si) for si in step) < 1e-7:
                break
    except (ZeroDivisionError, OverflowError):
        return None
    return b


def blend_report(legs: list[dict], reps: int = 200, seed: int = 20261010) -> dict:
    """How much of the information is in the model and how much in the price, estimated from EVERY priced leg (not only the selected ones), with night-level resampling for the error.
    This is the more efficient way to measure the winner's curse: if the price carries information the model lacks, a leg where the model most disagrees with the price is overstated by the
    amount the weight on the price implies, and the selected legs are corrected for it without needing hundreds of SELECTED bets."""
    units = {(l["date"], l["game"], l["player"], "SOG" if "SOG" in l["family"] else l["family"]) for l in legs}
    base = logistic_blend(legs)
    if base is None:
        return {"status": "NOT ENOUGH DATA", "legs": len(legs), "independent_units": len(units)}
    nights = sorted({l["date"] for l in legs})
    rng = random.Random(seed)
    by_night = {n: [l for l in legs if l["date"] == n] for n in nights}
    draws = []
    for _ in range(reps):
        sample = [l for n in (rng.choice(nights) for _ in nights) for l in by_night[n]]
        b = logistic_blend(sample)
        if b:
            draws.append(b)
    def ci(i):
        v = sorted(d[i] for d in draws)
        return [round(v[int(0.025 * len(v))], 3), round(v[int(0.975 * len(v)) - 1], 3)] if len(v) >= 20 else None
    return {"status": "FITTED", "legs": len(legs), "independent_units": len(units), "nights": len(nights), "intercept": round(base[0], 3), "weight_on_model_logit": round(base[1], 3), "weight_on_price_logit": round(base[2], 3),
            "ci95_weight_on_model": ci(1), "ci95_weight_on_price": ci(2),
            "reading": "With one night or a few, the interval is wide and means nothing. A weight on the price clearly above zero with the model's weight well below one is the winner's-curse signature; both near (1, 0) says the model already contains what the price knows."}


def _row(legs: list[dict]) -> dict:
    n = len(legs)
    if not n:
        return {"legs": 0}
    pred, imp, obs = sum(l["p"] for l in legs) / n, sum(l["implied"] for l in legs) / n, sum(l["hit"] for l in legs) / n
    return {"legs": n, "model_said": round(pred, 3), "price_implied_before_margin": round(imp, 3), "happened": round(obs, 3), "model_minus_happened": round(pred - obs, 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--log", default=None)
    a = ap.parse_args()
    from operational import shadow_selection as sh
    from operational import state_paths
    records = [json.loads(l) for l in Path(a.log).read_text().splitlines() if l.strip()] if a.log else sh.read_all()
    rep = score(records, db_resolver(state_paths.path("nhl.db")))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1))
    e = rep["enough_to_judge"]
    print(f"{rep['records']} pool snapshot(s) over {len(rep['days'])} day(s); {rep['resolved']} legs resolved, {rep['awaiting_games']} awaiting games. "
          f"Edge-filtered legs resolved: {e['resolved_edge_legs']} of ~{e['needed_to_see_a_5_point_overstatement']} needed -> {e['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
