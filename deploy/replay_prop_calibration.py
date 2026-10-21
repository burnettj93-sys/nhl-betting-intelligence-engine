"""Does the review's recalibration of a skater probability help OUT OF TIME? (Replay of the player-prop intelligence on stored data.)

The review (operational/eggy_intelligence.py `review_player`) replaces the skater model's calibrated probability with `model x factor` when history says the model ran high or low for players like this (one
cell, 300+ cases, |z| >= 2). The evidence file's cells use all of 2025-26 and 2026-27 to date, so scoring the adjustment on those same rows would flatter it. This replay trains the cells on the games BEFORE a
split date and scores raw against adjusted on the games AFTER it, for every market the review reads, with the same code (`analogue_cells`, `best_adjustment`) the review runs.

    python3 deploy/replay_prop_calibration.py --split 2026-02-01 --out docs/validation/eggy_prop_calibration_replay.json

It reads the MoneyPuck files already on disk and writes only the output file. It measures calibration (Brier, log loss, observed against predicted), never profit: no historical prices exist.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def main() -> int:
    from deploy import build_eggy_evidence as B
    from operational import eggy_intelligence as ei
    from research.product_models import history, live, matchup as M, projections as P
    from research.product_models.validate import apply_platt, walk
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", default="2026-02-01")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = time.time()
    team_rows = M.team_games()
    matchup_ev, env_preds = B.build_matchup(team_rows, log=lambda *x: None)
    train = B.build_skater(env_preds, team_rows, log=lambda *x: None, before=a.split)
    ctx = ei.Context(now=None, as_of=a.split, evidence={"skater": train, "matchup": matchup_ev})
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    walked = walk(cfg, (2025, 2026), rows, with_baselines=False)
    idx = {(r["player_id"], r["date"]): r for r in rows}
    alphas, platt = val["dispersion"], val["platt_params"]
    VARIANTS = {"full": lambda adj, p: min(max(p * adj["factor"], 0.001), 0.99), "half": lambda adj, p: min(max(p * (1 + 0.5 * (adj["factor"] - 1)), 0.001), 0.99),
                "z3": lambda adj, p: (min(max(p * adj["factor"], 0.001), 0.99) if abs(adj["z"]) >= 3 else p)}
    vb = {m: {v: 0.0 for v in VARIANTS} for m in B.P_BANDS}
    agg = {m: {"n": 0, "adjusted": 0, "up": 0, "down": 0, "raw_brier": 0.0, "adj_brier": 0.0, "raw_ll": 0.0, "adj_ll": 0.0, "y": 0.0, "raw_p": 0.0, "adj_p": 0.0, "adjusted_rows": {"n": 0, "y": 0.0, "raw_p": 0.0, "adj_p": 0.0, "raw_brier": 0.0, "adj_brier": 0.0}} for m in B.P_BANDS}
    bands: dict = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for w in walked:
        if w["date"] < a.split:
            continue
        r = idx.get((w["pid"], w["date"]))
        if r is None:
            continue
        p_home = env_preds.get(str(r["game_id"]))
        team_p = None if p_home is None else (p_home if r["home"] else 1.0 - p_home)
        for market in B.P_BANDS:
            stat, k = market.split(">=")
            k = int(k)
            al, be = platt[f"{stat}>={k}"]
            p = apply_platt(P.prob_at_least(k, w["mean"][stat], alphas[stat]), al, be)
            y = 1 if w["actual"][stat] >= k else 0
            cells = ei.analogue_cells(ctx, market, p, w["grp"], w["toi"], w["toi_pp"], team_p)
            adj = ei.best_adjustment(cells)
            p2 = p if adj is None else min(max(p * adj["factor"], 0.001), 0.99)
            for vname, fn in VARIANTS.items():
                vb[market][vname] += ((p if adj is None else fn(adj, p)) - y) ** 2
            g = agg[market]
            g["n"] += 1
            g["y"] += y
            g["raw_p"] += p
            g["adj_p"] += p2
            g["raw_brier"] += (p - y) ** 2
            g["adj_brier"] += (p2 - y) ** 2
            g["raw_ll"] += _ll(p, y)
            g["adj_ll"] += _ll(p2, y)
            if adj is not None:
                g["adjusted"] += 1
                g["up" if adj["factor"] > 1 else "down"] += 1
                s = g["adjusted_rows"]
                s["n"] += 1
                s["y"] += y
                s["raw_p"] += p
                s["adj_p"] += p2
                s["raw_brier"] += (p - y) ** 2
                s["adj_brier"] += (p2 - y) ** 2
            b = ei._band_of(B.P_BANDS[market], p)
            cell = bands[(market, b[0], b[1])]
            cell[0] += 1
            cell[1] += p
            cell[2] += p2
            cell[3] += y
    out = {"split": a.split, "trained_on": f"games before {a.split} (2025-26 and 2026-27 to date)", "tested_on": f"games on or after {a.split}", "markets": {}, "note": "Calibration only. No historical prices exist, so nothing here is profit."}
    for market, g in agg.items():
        n = g["n"]
        if not n:
            continue
        s = g["adjusted_rows"]
        out["markets"][market] = {
            "test_rows": n, "adjusted_rows": g["adjusted"], "adjusted_share": round(g["adjusted"] / n, 4), "adjusted_up": g["up"], "adjusted_down": g["down"], "observed_rate": round(g["y"] / n, 5),
            "mean_raw": round(g["raw_p"] / n, 5), "mean_adjusted": round(g["adj_p"] / n, 5), "brier_raw": round(g["raw_brier"] / n, 6), "brier_adjusted": round(g["adj_brier"] / n, 6),
            "log_loss_raw": round(g["raw_ll"] / n, 6), "log_loss_adjusted": round(g["adj_ll"] / n, 6),
            "on_adjusted_rows": ({"n": s["n"], "observed_rate": round(s["y"] / s["n"], 5), "mean_raw": round(s["raw_p"] / s["n"], 5), "mean_adjusted": round(s["adj_p"] / s["n"], 5),
                                  "brier_raw": round(s["raw_brier"] / s["n"], 6), "brier_adjusted": round(s["adj_brier"] / s["n"], 6)} if s["n"] else None)}
    out["variants_brier"] = {m: {**{v: round(vb[m][v] / agg[m]["n"], 6) for v in VARIANTS}, "raw": round(agg[m]["raw_brier"] / agg[m]["n"], 6)} for m in agg if agg[m]["n"]}
    out["bands"] = [{"market": k[0], "p_lo": k[1], "p_hi": k[2], "n": v[0], "mean_raw": round(v[1] / v[0], 4), "mean_adjusted": round(v[2] / v[0], 4), "observed": round(v[3] / v[0], 4)} for k, v in sorted(bands.items()) if v[0] >= 200]
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"replayed in {time.time() - t0:.0f}s")
    for m, v in out["markets"].items():
        ar = v["on_adjusted_rows"]
        print(f"{m:11s} test {v['test_rows']:>6} adjusted {v['adjusted_share']:.1%} (up {v['adjusted_up']}, down {v['adjusted_down']}) | all rows Brier raw {v['brier_raw']:.5f} -> adj {v['brier_adjusted']:.5f} | "
              + (f"adjusted rows n={ar['n']}: observed {ar['observed_rate']:.4f}, raw {ar['mean_raw']:.4f}, adj {ar['mean_adjusted']:.4f}, Brier {ar['brier_raw']:.5f} -> {ar['brier_adjusted']:.5f}" if ar else "none adjusted"))
    for m, v in out["variants_brier"].items():
        print(f"{m:11s} Brier raw {v['raw']:.6f} | full {v['full']:.6f} | half-shrunk {v['half']:.6f} | |z|>=3 only {v['z3']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
