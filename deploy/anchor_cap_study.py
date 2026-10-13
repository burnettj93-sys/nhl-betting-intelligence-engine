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
SELECTED_CAP = 0.02            # the value the 2024-only rule below picks; the script reports the rule's own choice next to it so a mismatch is visible
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


def _subset(sample: list[dict], k: int, live_cap: float, new_cap: float) -> dict:
    """The legs the LIVE cap holds down (the record is more than live_cap above the model): how many, what the model said, what each cap would have claimed, and what happened."""
    sub = [x for x in sample if x["emp"] > x["model"] + live_cap + 1e-9 and _final(x["emp"], x["model"], k, live_cap) < x["emp"] - 1e-9]
    n = len(sub)
    if not n:
        return {"n": 0}
    mean = lambda f: sum(f(x) for x in sub) / n  # noqa: E731
    happened = mean(lambda x: x["hit"])
    final_live = mean(lambda x: _final(x["emp"], x["model"], k, live_cap))
    final_new = mean(lambda x: _final(x["emp"], x["model"], k, new_cap))
    se = math.sqrt(sum(_final(x["emp"], x["model"], k, live_cap) * (1 - _final(x["emp"], x["model"], k, live_cap)) for x in sub)) / n
    return {"n": n, "record_shrunk": round(mean(lambda x: x["emp"]), 3), "model": round(mean(lambda x: x["model"]), 3), f"final_cap_{live_cap:.2f}": round(final_live, 3), f"final_cap_{new_cap:.2f}": round(final_new, 3),
            "happened": round(happened, 3), "live_minus_happened": round(final_live - happened, 3), "new_minus_happened": round(final_new - happened, 3), "se_independent": round(se, 3)}


def main(out_path: str) -> dict:
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    alphas, platt = val["dispersion"], val["platt_params"]
    out: dict = {"caps_tried": list(CAPS), "live_cap": anchor.ANCHOR_CAP_V1, "seasons": {}}
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
                if _final(emp, model, k, anchor.ANCHOR_CAP_V1) < anchor.SHOTS_LEG_FLOOR:
                    continue
                sample.append({"emp": emp, "model": model, "hit": 1 if r["actual"]["shots"] >= k else 0})
            out["seasons"][str(season)][f"shots>={k}"] = {f"cap_{c:.2f}": _scores(sample, c, k) for c in CAPS}
            out["seasons"][str(season)][f"shots>={k}"]["subset_capped_at_live_cap"] = _subset(sample, k, anchor.ANCHOR_CAP_V1, SELECTED_CAP)
    # The selection rule, fixed in advance: the cap with the lowest 2024 log loss, pooled over the 2+ and 3+ lines weighted by player-games. 2024 only; nothing from 2025 or from any ticket is read here.
    s24 = out["seasons"]["2024"]
    pooled = {}
    for c in CAPS:
        key = f"cap_{c:.2f}"
        n2, n3 = s24["shots>=2"][key]["n"], s24["shots>=3"][key]["n"]
        pooled[key] = (n2 * s24["shots>=2"][key]["log_loss"] + n3 * s24["shots>=3"][key]["log_loss"]) / (n2 + n3)
    out["selection"] = {"rule": "lowest 2024 log loss, pooled over 2+ and 3+ lines weighted by player-games", "pooled_2024_log_loss": {k: round(v, 5) for k, v in pooled.items()},
                        "selected": min(pooled, key=pooled.get), "expected": f"cap_{SELECTED_CAP:.2f}"}
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "docs/validation/anchor_cap_study.json")
    print("selection:", res["selection"])
    for season, cells in res["seasons"].items():
        print(season, cells["role"])
        for key in ("shots>=2", "shots>=3"):
            for cap, s in cells[key].items():
                print("  ", key, cap, s)
