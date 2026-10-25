"""Is each PLAYER POINTS threshold (1+, 2+, 3+) estimated well enough, OUT OF TIME, to be a live parlay ingredient? Read-only: it reads the MoneyPuck game logs already on disk and writes one file.

    python3 deploy/validate_points_thresholds.py --out docs/validation/points_thresholds.json

The skater model (`player-rate-toi-v2`: rate x expected TOI x opponent environment, negative-binomial counts) is walked forward through the 2025-26 and 2026-27 games; its Platt calibration for points>=1 and
points>=2 was fitted on 2024-25, so every row scored here is out of time. points>=3 has no fitted calibration: it is scored raw, and also with a Platt fitted on the games BEFORE the split date and scored after it.

Each threshold is judged on a rule written BEFORE the numbers were seen (RULE below): enough cases, log loss no worse than the best baseline, calibration by probability band within tolerance. A threshold that
fails is NOT enabled for live parlays (operational/learning_parlays.py reads the verdict). Calibration only: no historical prices exist, so nothing here is value or profit.
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

RULE = {"min_test_rows": 5000, "log_loss_not_worse_than_best_baseline": True, "weighted_mean_abs_band_error_max": 0.02, "worst_band_error_max_when_n_at_least_500": 0.04, "band_min_n": 200,
        "text": "enabled only if (rows >= 5000) AND (model log loss <= the best baseline's) AND (case-weighted mean |observed - predicted| over bands with 200+ cases <= 0.02) AND (no band with 500+ cases is off by more than 0.04)"}
BANDS = {1: [0, .15, .25, .35, .45, .55, .65, .8, 1.01], 2: [0, .04, .08, .12, .18, .25, .35, 1.01], 3: [0, .02, .04, .07, .10, .15, .25, 1.01]}
SPLIT = "2026-02-01"


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _band(edges, v):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return (lo, hi)
    return (edges[-2], edges[-1])


def _fit_platt(pairs):
    """A two-parameter logistic recalibration by a few Newton steps on (logit p) -> y."""
    a, b = 1.0, 0.0
    xs = [(math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4))), y) for p, y in pairs]
    for _ in range(25):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in xs:
            q = 1 / (1 + math.exp(-(a * x + b)))
            r = q - y
            w = q * (1 - q)
            g0 += r * x; g1 += r; h00 += w * x * x; h01 += w * x; h11 += w
        det = h00 * h11 - h01 * h01 + 1e-12
        a -= (h11 * g0 - h01 * g1) / det
        b -= (-h01 * g0 + h00 * g1) / det
    return a, b


def main() -> int:
    from research.product_models import history, live, projections as P
    from research.product_models.validate import apply_platt, walk
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    t0 = time.time()
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    walked = walk(cfg, (2025, 2026), rows, with_baselines=True)
    alpha, platt = val["dispersion"]["points"], val["platt_params"]
    out = {"model": "player-rate-toi-v2", "scored": "2025-26 and 2026-27 to date (Platt for points>=1 and >=2 fitted on 2024-25, so out of time)", "split_date_for_points>=3_recalibration": SPLIT,
           "rule_declared_before_the_numbers": RULE, "rows_walked": len(walked), "thresholds": {}, "note": "Calibration only. No historical prices exist, so nothing here is value, edge or profit."}
    for k in (1, 2, 3):
        recs = []
        for w in walked:
            raw = P.prob_at_least(k, w["mean"]["points"], alpha)
            y = 1 if w["actual"]["points"] >= k else 0
            p = apply_platt(raw, *platt[f"points>={k}"]) if f"points>={k}" in platt else raw
            recs.append({"date": w["date"], "raw": raw, "p": p, "y": y, "grp": w["grp"], "toi": w["toi"], "pp": w["toi_pp"], "emp": (w.get("emp") or {}).get(("points", k))})
        # every variant is scored on the SAME games, those on or after the split date. "as_live" is the calibration the engine uses now (none exists for points>=3). The recalibrated variant was ADDED after the
        # first run showed the live calibration under-predicts the upper bands of points>=1 and >=2 (observed above predicted); it is fitted only on games BEFORE the split and never sees the games it is scored on.
        post = [r for r in recs if r["date"] >= SPLIT]
        train = [(r["raw"], r["y"]) for r in recs if r["date"] < SPLIT]
        pa, pb = _fit_platt(train)
        variants = {"raw_model": [(r["raw"], r["y"], r) for r in post], "recalibrated_on_games_before_split": [(apply_platt(r["raw"], pa, pb), r["y"], r) for r in post]}
        if f"points>={k}" in platt:
            variants = {"as_live": [(r["p"], r["y"], r) for r in post], **variants}
        out.setdefault("recalibration_fitted_before_split", {})[f"points>={k}"] = {"a": round(pa, 4), "b": round(pb, 4), "fit_rows": len(train), "fit_through": SPLIT}
        res = {}
        for vname, vs in variants.items():
            n = len(vs)
            base_train = [r["y"] for r in recs if r["date"] < SPLIT]
            base = sum(base_train) / max(len(base_train), 1)
            ll = sum(_ll(p, y) for p, y, _ in vs) / n
            br = sum((p - y) ** 2 for p, y, _ in vs) / n
            ll_const = sum(_ll(base, y) for _, y, _ in vs) / n
            emps = [(r["emp"], y) for _, y, r in vs if r["emp"] is not None]
            ll_emp = (sum(_ll(e, y) for e, y in emps) / len(emps)) if len(emps) >= 0.9 * n else None
            bands = defaultdict(lambda: [0, 0.0, 0.0])
            for p, y, _ in vs:
                b = bands[_band(BANDS[k], p)]
                b[0] += 1; b[1] += p; b[2] += y
            table = [{"band": f"{lo:.0%}-{min(hi, 1):.0%}", "n": c, "predicted": round(sp / c, 4), "observed": round(sy / c, 4), "gap": round(sy / c - sp / c, 4)} for (lo, hi), (c, sp, sy) in sorted(bands.items()) if c >= 30]
            big = [t for t in table if t["n"] >= RULE["band_min_n"]]
            wmae = sum(abs(t["gap"]) * t["n"] for t in big) / max(sum(t["n"] for t in big), 1)
            worst = max((abs(t["gap"]) for t in table if t["n"] >= 500), default=0.0)
            groups = {}
            for gname, keyf in (("position", lambda r: r["grp"]), ("power_play_minutes", lambda r: "pp<0.25" if (r["pp"] or 0) < 0.25 else ("pp 0.25-1" if r["pp"] < 1.0 else ("pp 1-2" if r["pp"] < 2.0 else "pp 2+"))),
                                ("expected_toi", lambda r: "toi<14" if r["toi"] < 14 else ("toi 14-18" if r["toi"] < 18 else "toi 18+"))):
                gg = defaultdict(lambda: [0, 0.0, 0.0])
                for p, y, r in vs:
                    c = gg[keyf(r)]
                    c[0] += 1; c[1] += p; c[2] += y
                groups[gname] = {g: {"n": c[0], "predicted": round(c[1] / c[0], 4), "observed": round(c[2] / c[0], 4)} for g, c in sorted(gg.items()) if c[0] >= 200}
            res[vname] = {"rows": n, "base_rate": round(sum(y for _, y, _ in vs) / n, 4), "log_loss": round(ll, 5), "brier": round(br, 5), "log_loss_constant_base_rate": round(ll_const, 5),
                          "log_loss_player_empirical_rate": None if ll_emp is None else round(ll_emp, 5), "calibration_by_band": table, "weighted_mean_abs_band_error": round(wmae, 4), "worst_band_error_n500": round(worst, 4),
                          "obs_vs_pred_by_group": groups}
            best_base = min(x for x in (ll_const, ll_emp) if x is not None)
            ok = n >= RULE["min_test_rows"] and ll <= best_base and wmae <= RULE["weighted_mean_abs_band_error_max"] and worst <= RULE["worst_band_error_max_when_n_at_least_500"]
            res[vname]["passes_rule"] = bool(ok)
            res[vname]["rule_detail"] = {"rows_ok": n >= RULE["min_test_rows"], "beats_best_baseline": bool(ll <= best_base), "best_baseline_log_loss": round(best_base, 5), "band_error_ok": wmae <= RULE["weighted_mean_abs_band_error_max"],
                                         "worst_band_ok": worst <= RULE["worst_band_error_max_when_n_at_least_500"]}
        order = (["as_live"] if "as_live" in res else []) + ["raw_model", "recalibrated_on_games_before_split"]          # the simplest variant that passes: the live calibration, then none, then a refit
        chosen = next((v for v in order if res[v]["passes_rule"]), None)
        out["thresholds"][str(k)] = {"live_variant": chosen or order[0], "variants": res,
                                     "verdict": ("ENABLED" if chosen in ("as_live", "raw_model") else ("ENABLED_WITH_RECALIBRATION" if chosen else "NOT_ENABLED")),
                                     "recalibration": ({"a": round(pa, 4), "b": round(pb, 4), "fit_through": SPLIT} if chosen == "recalibrated_on_games_before_split" else None),
                                     "why": (f"the {chosen} variant passes the pre-declared rule on games after the split" if chosen else "no variant passes the pre-declared rule: " + json.dumps(res[order[0]]["rule_detail"]))}
    out["enabled_thresholds"] = [int(k) for k, v in out["thresholds"].items() if v["verdict"].startswith("ENABLED")]
    out["elapsed_seconds"] = round(time.time() - t0)
    Path(a.out).write_text(json.dumps(out, indent=1))
    for k, v in out["thresholds"].items():
        lv = v["variants"][v["live_variant"]]
        print(f"points {k}+: {v['verdict']:28s} [{v['live_variant']}] rows {lv['rows']:>6} base {lv['base_rate']:.3f} logloss {lv['log_loss']:.4f} (best baseline {lv['rule_detail']['best_baseline_log_loss']:.4f}) band err {lv['weighted_mean_abs_band_error']:.4f} worst {lv['worst_band_error_n500']:.4f}")
    print("enabled thresholds:", out["enabled_thresholds"], f"in {out['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
