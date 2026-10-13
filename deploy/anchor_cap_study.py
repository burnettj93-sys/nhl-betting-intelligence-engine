"""Is the +10-point cap on a shots leg's final estimate the right size? Chronological check on the validation seasons.

The existing calibration (docs/validation/anchor_calibration.json) shows one specific weakness, the same in all three seasons: on 2+ shots legs where the cap binds (the player's last-30 record is
more than 10 points above the model), the final estimate is about 8 points too high (final 0.67, happened 0.59; standard error about 0.02). The model's own number (0.57) was closer.

This script chooses the cap on the CALIBRATION season (2024: configuration chosen and Platt fitted there) and reports it on the other seasons, 2025 being the only out-of-sample one. The estimator is
the same as the live rule, final = min(record, model + cap, 0.70 for 3+ lines), with only the cap changed. Compared on the legs the CURRENT rule would ticket (final >= 0.60), so the population does
not move with the cap; log loss and Brier on the final estimate, and the mean miss on the legs where the cap binds.

Usage: python3 deploy/anchor_cap_study.py [output.json]   (about 10 minutes: it walks three seasons)
"""
from __future__ import annotations

import json
import math
import sys

sys.path.insert(0, ".")
from operational.pricing_policy import MIN_GAMES_FOR_PRICING  # noqa: E402
from research.product_models import history, live  # noqa: E402
from research.product_models import projections as P  # noqa: E402
from research.product_models import validate as V  # noqa: E402
from research.real_market_parlay import anchor  # noqa: E402

CAPS = (0.10, 0.08, 0.06, 0.04, 0.02, 0.00)
SEASONS = {2023: "model-fitting", 2024: "calibration (cap chosen here)", 2025: "OUT OF SAMPLE"}
MIN_TOI = 12.0


def _final(emp: float, model: float, k: int, cap: float) -> float:
    return min(emp, model + cap, anchor.HIGH_LINE_CAP if k >= 3 else 1.0)


def _scores(sample: list[dict], cap: float, k: int) -> dict:
    eps = 1e-6
    n = len(sample)
    ll = br = 0.0
    capped = []
    for x in sample:
        f = min(max(_final(x["emp"], x["model"], k, cap), eps), 1 - eps)
        y = x["hit"]
        ll += -(y * math.log(f) + (1 - y) * math.log(1 - f))
        br += (f - y) ** 2
        if f < x["emp"] - 1e-9:
            capped.append((f, y))
    out = {"n": n, "log_loss": round(ll / n, 5), "brier": round(br / n, 5), "capped_n": len(capped)}
    if capped:
        out["capped_final"] = round(sum(f for f, _ in capped) / len(capped), 3)
        out["capped_happened"] = round(sum(y for _, y in capped) / len(capped), 3)
    return out


def main(out_path: str) -> dict:
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    alphas, platt = val["dispersion"], val["platt_params"]
    out: dict = {"caps_tried": list(CAPS), "live_cap": anchor.ANCHOR_CAP, "seasons": {}}
    for season, role in SEASONS.items():
        recs = V.model_probs(V.walk(cfg, (season,), rows), alphas)
        out["seasons"][str(season)] = {"role": role}
        for k in (2, 3):
            key = ("shots", k)
            a, b = platt[f"shots>={k}"]
            sample = []
            for r in recs:
                emp = r["emp"].get(key)
                if emp is None or r["n_prior"] < MIN_GAMES_FOR_PRICING or r["toi"] < MIN_TOI:
                    continue
                model = V.apply_platt(r["p"][key], a, b)
                if _final(emp, model, k, anchor.ANCHOR_CAP) < anchor.SHOTS_LEG_FLOOR:
                    continue
                sample.append({"emp": emp, "model": model, "hit": 1 if r["actual"]["shots"] >= k else 0})
            out["seasons"][str(season)][f"shots>={k}"] = {f"cap_{c:.2f}": _scores(sample, c, k) for c in CAPS}
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "docs/validation/anchor_cap_study.json")
    for season, cells in res["seasons"].items():
        print(season, cells["role"])
        for key in ("shots>=2", "shots>=3"):
            for cap, s in cells[key].items():
                print("  ", key, cap, s)
