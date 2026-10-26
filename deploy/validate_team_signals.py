"""Which team signals actually improve moneyline PREDICTION? Time-separated: each season 2017-2025 is predicted from the seasons before it (the same expanding-window test the team model's evidence uses), and every
variant is scored on the same games against the model as it stands (gd + xgd + hd + b2b, half-life 30 games). Read-only on the MoneyPuck team files already on disk; writes one file.

    python3 deploy/validate_team_signals.py --out docs/validation/team_signals.json

A signal is worth adding only if the paired log-loss improvement over the current model has a 95% bootstrap interval entirely below zero; one that makes the model worse, or whose interval spans zero, is
reported as such and gets no weight. No market prices exist for these seasons, so this is about hockey prediction, not betting value.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    import numpy as np
    from deploy import build_eggy_evidence as B
    from research.product_models import matchup as M
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = M.team_games()
    base_names = M.MODEL_FEATURES
    out = {"test_seasons": f"{B.TEST_SEASONS[0]}-{B.TEST_SEASONS[-1]} (expanding window)", "current_model": {"features": list(base_names), "half_life_games": M.HALF_LIFE_GAMES}, "variants": {},
           "rule": "adopt only if the 95% bootstrap interval of (variant - current) log loss is entirely below zero; entirely above zero means it hurts"}
    F30 = [r for r in B.walk_features(rows) if r["season"] >= M.FIRST_FIT_SEASON]
    L0, _ = B._expanding(np, F30, base_names, B.TEST_SEASONS)
    out["current_model"]["log_loss"] = round(float(L0.mean()), 5)
    out["current_model"]["n_games"] = int(len(L0))

    def record(name, L, note):
        d = B._boot(np, L - L0)
        verdict = "IMPROVES" if d["ci_high"] < 0 else ("HURTS" if d["ci_low"] > 0 else "NO_DEMONSTRATED_DIFFERENCE")
        out["variants"][name] = {"log_loss": round(float(L.mean()), 5), "delta_vs_current": {k: round(v, 5) for k, v in d.items()}, "verdict": verdict, "note": note}
        print(f"{name:42s} log loss {L.mean():.5f}  delta {d['point']:+.5f} [{d['ci_low']:+.5f}, {d['ci_high']:+.5f}]  {verdict}")
    for extra, note in (("xgd5", "recent 5-game expected-goal form on top of the decayed strength"), ("cf5", "recent 5-game shot-attempt share"), ("rest", "days of rest difference (capped at 5), beyond the back-to-back flag"),
                        ("fin", "finishing (goals above expected)"), ("pp", "power-play rate"), ("pk", "penalty-kill rate")):
        L, _ = B._expanding(np, F30, tuple(base_names) + (extra,), B.TEST_SEASONS)
        record(f"+{extra}", L, note)
    for name in ("hd", "b2b"):
        L, _ = B._expanding(np, F30, tuple(n for n in base_names if n != name), B.TEST_SEASONS)
        record(f"-{name}", L, f"the model WITHOUT {name}: does the feature earn its place?")
    for hl in (10, 15, 60, 120):
        Fh = [r for r in B.walk_features(rows, half_life=hl) if r["season"] >= M.FIRST_FIT_SEASON]
        L, _ = B._expanding(np, Fh, base_names, B.TEST_SEASONS)
        record(f"half_life_{hl}_games", L, "how quickly team strength should decay (the model uses 30)")
    Path(a.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
