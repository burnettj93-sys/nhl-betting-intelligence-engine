"""Reproduces docs/validation/anchor_calibration.json: is the probability a shots leg is actually ticketed at calibrated?

It runs the project's own walk-forward evaluation (research/product_models/validate.walk: every eligible player-game of a season, in date order, using only information before each game) with the
SAME configuration, dispersion and Platt parameters the live model uses (research/product_models/validation JSON), and applies the exact rule of research/real_market_parlay/anchor.py to every
player-game it would have priced:

    final estimate = min( last-30-game record shrunk toward the league rate,  Platt-calibrated model + ANCHOR_CAP_V1 (+0.10, the cap of policy c236c526e00b9f04; the corrected +0.02 is evaluated in deploy/anchor_cap_study.py),  HIGH_LINE_CAP when the line is 3+ shots )
    a leg is "supported" when the estimate is at least SHOTS_LEG_FLOOR, the player has at least MIN_GAMES_FOR_PRICING games of history and the model expects at least 12 minutes.

Three numbers are kept apart in every row: the RECENT HIT RATE (hits in the last 30 games divided by games, unshrunk), the MODEL (Platt-calibrated probability) and the FINAL ESTIMATE (what the ticket uses).

Season roles (research/product_models/validate.py): 2022-2023 TRAIN (dispersion fitted), 2024 CALIBRATION (configuration chosen and Platt fitted), 2025 FINAL EVALUATION (never used to fit anything).
Only 2025 is out of sample for the model; 2023 and 2024 are model-fitting seasons and are shown for information, labelled as such. The last-30 record has no fitted parameters in this script (window 30 and
8 pseudo-games were fixed in validate._EmpiricalBaseline before it; this repository cannot show they were not influenced by earlier seasons), so no season is fully "held out" for the combined estimate except 2025.

Limits: this tests calibration over all supported player-games, not over the subset where a sportsbook prices a leg cheaper than the estimate (no historical prices exist); games of one night and
the same player's consecutive games are correlated, so the standard errors shown (independence assumed) are too small.
Usage: python3 deploy/anchor_calibration.py [output.json]
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

sys.path.insert(0, ".")
from operational.pricing_policy import MIN_GAMES_FOR_PRICING  # noqa: E402
from research.product_models import history, live  # noqa: E402
from research.product_models import projections as P  # noqa: E402
from research.product_models import validate as V  # noqa: E402
from research.real_market_parlay import anchor  # noqa: E402

BINS = ((0.60, 0.70), (0.70, 0.80), (0.80, 1.01))
MIN_EXPECTED_TOI = 12.0
SEASON_ROLES = {2023: "model-fitting season (dispersion fitted on 2022-2023)", 2024: "model-fitting season (configuration chosen and Platt fitted on 2024)", 2025: "OUT OF SAMPLE (final evaluation; fitted nothing)"}


def raw_recent_rates(rows: list[dict], seasons: tuple[int, ...]) -> dict:
    """(player, date, k) -> (hits, games) over the player's previous 30 games, the unshrunk record the display shows."""
    hist: dict[str, list[int]] = defaultdict(list)
    out: dict = {}
    for date, day in live._by_date(rows):
        for r in day:
            if r["season"] in seasons:
                recent = hist[r["player_id"]][-live.FREQ_WINDOW:]
                for k in (2, 3):
                    out[(r["player_id"], date, k)] = (sum(1 for v in recent if v >= k), len(recent))
        for r in day:
            hist[r["player_id"]].append(r["shots"])
    return out


def main(out_path: str) -> dict:
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    alphas, platt = val["dispersion"], val["platt_params"]
    raw = raw_recent_rates(rows, tuple(SEASON_ROLES))
    out: dict = {"method": __doc__.strip().splitlines()[0], "rule": {"anchor_cap": anchor.ANCHOR_CAP_V1, "high_line_cap": anchor.HIGH_LINE_CAP, "shots_leg_floor": anchor.SHOTS_LEG_FLOOR,
                                                                    "min_games_for_pricing": MIN_GAMES_FOR_PRICING, "min_expected_toi": MIN_EXPECTED_TOI},
                 "model": {"version": val["model_version"], "config": val["chosen_config"], "platt": {k: platt[f"shots>={k}"] for k in (2, 3)}}, "seasons": {}}
    for season, role in SEASON_ROLES.items():
        recs = V.model_probs(V.walk(cfg, (season,), rows), alphas)
        out["seasons"][str(season)] = {"role": role}
        for k in (2, 3):
            key = ("shots", k)
            a, b = platt[f"shots>={k}"]
            sample = []
            for r in recs:
                emp = r["emp"].get(key)
                if emp is None or r["n_prior"] < MIN_GAMES_FOR_PRICING or r["toi"] < MIN_EXPECTED_TOI:
                    continue
                model = V.apply_platt(r["p"][key], a, b)
                final = min(emp, model + anchor.ANCHOR_CAP_V1, anchor.HIGH_LINE_CAP if k >= 3 else 1.0)
                if final < anchor.SHOTS_LEG_FLOOR:
                    continue
                hits, games = raw.get((r["pid"], r["date"], k), (0, 0))
                sample.append({"final": final, "model": model, "record": emp, "recent": hits / games if games else float("nan"), "hit": 1 if r["actual"]["shots"] >= k else 0,
                               "capped": final < emp - 1e-9})
            cell = {"supported_player_games": len(sample), "bins_by_final_estimate": []}

            def summarize(grp):
                n = len(grp)
                mean = lambda f: sum(x[f] for x in grp) / n  # noqa: E731
                happened = mean("hit")
                final = mean("final")
                se = math.sqrt(sum(x["final"] * (1 - x["final"]) for x in grp) / n / n)
                return {"n": n, "recent_hit_rate_unshrunk": round(mean("recent"), 3), "record_shrunk": round(mean("record"), 3), "model_calibrated": round(mean("model"), 3),
                        "final_estimate": round(final, 3), "happened": round(happened, 3), "final_minus_happened": round(final - happened, 3), "se_independent": round(se, 3),
                        "capped_share": round(mean("capped"), 3)}
            if sample:
                cell["all_supported"] = summarize(sample)
                for lo, hi in BINS:
                    grp = [x for x in sample if lo <= x["final"] < hi]
                    if grp:
                        cell["bins_by_final_estimate"].append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", **summarize(grp)})
                capped = [x for x in sample if x["capped"]]
                if capped:
                    cell["capped_only"] = summarize(capped)
            out["seasons"][str(season)][f"shots>={k}"] = cell
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "docs/validation/anchor_calibration.json")
    for season, cells in res["seasons"].items():
        print(season, cells["role"])
        for key in ("shots>=2", "shots>=3"):
            c = cells[key]
            print("  ", key, c.get("all_supported"))
            for b in c["bins_by_final_estimate"]:
                print("     ", b)
