"""Reproduces docs/validation/anchor_calibration.json: how often NHL skaters reached 2+ and 3+ shots, grouped by what each estimate said beforehand.

Two estimates are compared on the same held-out player-games (every game of the season, walked forward in date order, using only information before each game):
  * the validated skater model (research/product_models), Platt-calibrated for 2025 (the final held-out season, calibration fitted on 2024); for 2023 and 2024 the raw model
    probability, because those seasons are inside the model's own fitting window;
  * the player's last-30-game frequency shrunk toward the league rate by 8 pseudo-games (research/product_models/live.shot_frequencies; validate._EmpiricalBaseline).
This tests calibration over all eligible player-games, NOT over only the legs a sportsbook prices cheaply relative to the estimate, and it uses no sportsbook prices (none exist historically).
Usage: python3 deploy/anchor_calibration.py [output.json]
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")
from research.product_models import history  # noqa: E402
from research.product_models import projections as P  # noqa: E402
from research.product_models import validate as V  # noqa: E402

BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))


def main(out_path: str) -> dict:
    rows = history.skater_games()
    best = P.ModelConfig(rate_half_life_games=30, toi_half_life_games=8, k_minutes=600, opp_beta=0.5)
    train = V.walk(best, V.TRAIN_SEASONS, rows, with_baselines=False)
    alphas = {s: P.fit_dispersion([(r["mean"][s], r["actual"][s]) for r in train]) for s in P.STATS}
    calib = V.model_probs(V.walk(best, (V.CALIB_SEASON,), rows), alphas)
    platt = {k: V.fit_platt(V._pairs(calib, ("shots", k), lambda r, key=("shots", k): r["p"][key])) for k in (2, 3)}
    out: dict = {"method": __doc__.strip().splitlines()[0], "seasons": {}}
    for season in (2023, 2024, 2025):
        recs = V.model_probs(V.walk(best, (season,), rows), alphas)
        out["seasons"][str(season)] = {}
        for k in (2, 3):
            key = ("shots", k)
            a, b = platt[k]
            sample = [(V.apply_platt(r["p"][key], a, b) if season == V.FINAL_SEASON else r["p"][key], 1 if r["actual"]["shots"] >= k else 0, r["emp"][key]) for r in recs if r["emp"][key] is not None]
            cell = {"player_games": len(sample), "model_is": "platt-calibrated" if season == V.FINAL_SEASON else "raw", "bins_by_last30_estimate": []}
            for lo, hi in BINS:
                grp = [s for s in sample if lo <= s[2] < hi]
                if grp:
                    n = len(grp)
                    cell["bins_by_last30_estimate"].append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": n, "last30_said": round(sum(s[2] for s in grp) / n, 3),
                                                            "happened": round(sum(s[1] for s in grp) / n, 3), "model_said_on_same_rows": round(sum(s[0] for s in grp) / n, 3)})
            out["seasons"][str(season)][f"shots>={k}"] = cell
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "docs/validation/anchor_calibration.json")
    for season, cells in res["seasons"].items():
        for key, cell in cells.items():
            for b in cell["bins_by_last30_estimate"]:
                print(season, key, b)
