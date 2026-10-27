"""How likely is a side to cover the puck line, given the hockey model's win probability? Validated out of time; read-only (MoneyPuck team games already on disk).

    python3 deploy/build_eggy_pick_calibration.py --out docs/validation/eggy_pick_calibration.json

For every non-preseason game 2017-2025 the production team model's PREGAME win probability (each season predicted from earlier seasons only: deploy/build_eggy_evidence.py `_expanding`) is paired with the final
goal margin. A logistic of  1{team wins by 2 or more}  on logit(team win probability)  (both orientations of every game) is fitted on 2017-2024 and scored on 2025: the puck line -1.5 covers when the
side wins by two or more goals; +1.5 covers otherwise (a shootout or one-goal game covers +1.5 for both sides). Shootouts are margin 1 in the final score and tie in MoneyPuck's goals: neither side wins by two.
Nothing here is a price, a value or a market claim: historical puck-line prices do not exist. It says only how often the HOCKEY outcome happened, so that a puck-line probability can be stated and its
agreement with the market can be MEASURED prospectively.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main(argv=None) -> int:
    from deploy import build_eggy_evidence as B
    from research.product_models import matchup as M
    import numpy as np
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    rows = M.team_games()
    F = [r for r in B.walk_features(rows) if r["season"] >= M.FIRST_FIT_SEASON]
    _, preds = B._expanding(np, F, list(M.MODEL_FEATURES), tuple(range(2017, 2026)))
    margin = {}
    for g in B._paired(rows):
        margin[g["h"]["g"]] = (g["h"]["gf"] - g["a"]["gf"], g["h"]["s"])
    obs = []                                              # (season, x = logit(p of the team), y = 1 if the team won by 2+)
    for gid, p in preds.items():
        if gid not in margin:
            continue
        m, season = margin[gid]
        p = min(max(p, 1e-4), 1 - 1e-4)
        lg = math.log(p / (1 - p))
        obs.append((season, lg, 1.0 if m >= 2 else 0.0))
        obs.append((season, -lg, 1.0 if -m >= 2 else 0.0))
    train = [o for o in obs if o[0] <= 2024]
    test = [o for o in obs if o[0] == 2025]
    X = np.array([[1.0, o[1]] for o in train]); y = np.array([o[2] for o in train])
    w = B._fit(np, X, y)
    pr = lambda rs, w_: 1 / (1 + np.exp(-np.array([[1.0, o[1]] for o in rs]) @ w_))
    yt = np.array([o[2] for o in test]); p1 = pr(test, w)
    ll = lambda p, y_: float(np.mean(B._ll(np, p, y_)))
    base = float(np.mean(y))
    bins = [(0.0, 0.40), (0.40, 0.45), (0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 1.0)]
    pw = 1 / (1 + np.exp(-np.array([o[1] for o in test])))                      # the team's win probability
    table = []
    for lo, hi in bins:
        sel = (pw >= lo) & (pw < hi)
        if sel.sum():
            table.append({"team_win_probability": f"{lo:.2f}-{hi:.2f}", "n": int(sel.sum()), "predicted_win_by_2plus": round(float(p1[sel].mean()), 4), "observed": round(float(yt[sel].mean()), 4)})
    Xa = np.array([[1.0, o[1]] for o in obs]); ya = np.array([o[2] for o in obs])
    wa = B._fit(np, Xa, ya)
    out = {"method": "python3 deploy/build_eggy_pick_calibration.py --out docs/validation/eggy_pick_calibration.json", "model": "eggy-matchup-v1 pregame probabilities, expanding window",
           "fit_seasons": "2017-2024", "test_season": "2025", "rows_fit": len(train), "rows_test": len(test), "win_by_2plus_base_rate_fit": round(base, 4),
           "coefficients_live": {"intercept": round(float(wa[0]), 5), "slope_on_logit_win_probability": round(float(wa[1]), 5), "fit": "all of 2017-2025 (the out-of-time check below used 2017-2024 only)"},
           "coefficients_out_of_time_check": {"intercept": round(float(w[0]), 5), "slope": round(float(w[1]), 5)},
           "out_of_time_2025": {"log_loss_model": round(ll(p1, yt), 5), "log_loss_constant_base_rate": round(ll(np.full(len(yt), base), yt), 5), "calibration_by_team_win_probability": table},
           "note": "Hockey outcome calibration only. No historical puck-line prices exist: nothing here is value, edge or profit. Whether the market's puck-line price disagrees with this in a way that pays is measured prospectively (operational/eggy_pick_ledger.py).",
           "elapsed_seconds": round(time.time() - t0)}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("rows_fit", "rows_test", "coefficients_live", "out_of_time_2025")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
