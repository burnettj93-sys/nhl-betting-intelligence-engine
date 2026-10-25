"""Which player signals add predictive information BEYOND the model that already uses them? Time-separated and read-only (MoneyPuck logs on disk; one output file).

    python3 deploy/validate_player_signals.py --out docs/validation/player_signals.json

The skater model (`player-rate-toi-v2`) is walked forward through 2025-26 and 2026-27 (its calibration was fitted on 2024-25, so every scored row is out of time). For each signal the question is the one that decides
whether the live engine should act on it: in the games where the signal was present BEFORE the game, did the player fall short of (or exceed) the MODEL'S OWN probability? mean(outcome - model probability), its
standard error and z. A signal whose residual is not distinguishable from zero is already in the model and gets no weight. A signal with a significant NEGATIVE residual carries a measured adjustment: the
conservative end of its 95% interval (mean + 1.645 se), applied only DOWNWARD in the live review (history may lower an estimate, never lift it). A positive residual is reported and never applied.

Ablations re-walk the model with one ingredient switched off (opponent environment; the recency of ice time; the recency of rate) and compare out-of-time log loss on the same rows, with a paired bootstrap.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
MARKETS = (("shots", 2), ("shots", 3), ("points", 1), ("points", 2))


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def main() -> int:
    from research.product_models import history, live, projections as P
    from research.product_models.validate import apply_platt, walk
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    alphas, platt = val["dispersion"], val["platt_params"]
    pts_recal = json.loads((REPO / "docs/validation/points_thresholds.json").read_text())
    by_pid = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["player_id"], r["date"])):
        by_pid[r["player_id"]].append(r)
    pos = {(pid, r["date"]): i for pid, g in by_pid.items() for i, r in enumerate(g)}

    def prob(w, stat, k):
        raw = P.prob_at_least(k, w["mean"][stat], alphas[stat])
        key = f"{stat}>={k}"
        if stat == "points" and k == 2:                                 # the validated recalibration the live review applies to points>=2
            rc = pts_recal["thresholds"]["2"]["recalibration"]
            return apply_platt(raw, rc["a"], rc["b"])
        return apply_platt(raw, *platt[key]) if key in platt else raw

    walked = walk(cfg, (2025, 2026), rows, with_baselines=False)
    prev_walk = {}                                                  # a player's PREVIOUS walked game: the model's expected ice time against what happened (the postmortem's role / TOI finding)
    last_seen = {}
    for w in walked:
        if w["pid"] in last_seen:
            prev_walk[(w["pid"], w["date"])] = last_seen[w["pid"]]
        last_seen[w["pid"]] = (w["toi"], w["actual_toi"], w["toi_pp"], w["actual_pp"])
    acc = defaultdict(lambda: [0, 0.0, 0.0])
    est_rows = defaultdict(list)                                    # (model p, anchored estimate, outcome) for the shots thresholds: is the anchored estimate better than the model's own probability?

    meta = {}

    def add(name, resid, flag=None):
        c = acc[name]
        c[0] += 1; c[1] += resid; c[2] += resid * resid
        if flag:
            meta[name] = flag
    for w in walked:
        i = pos.get((w["pid"], w["date"]))
        if i is None or i < 40:
            continue
        g = by_pid[w["pid"]]
        l3, l5, b10 = g[i - 3:i], g[i - 5:i], g[i - 13:i - 3]
        base_toi = sum(x["toi"] for x in b10) / 10
        dtoi = base_toi - sum(x["toi"] for x in l3) / 3
        pp_b, pp_3 = sum(x["toi_pp"] for x in b10) / 10, sum(x["toi_pp"] for x in l3) / 3
        prev_gap = (__import__("datetime").date.fromisoformat(w["date"]) - __import__("datetime").date.fromisoformat(g[i - 1]["date"])).days
        home = bool(g[i].get("home"))
        for stat, k in MARKETS:
            y = 1 if w["actual"][stat] >= k else 0
            p = prob(w, stat, k)
            r = y - p
            m = f"{stat}>={k}"
            add(f"{m} | all rows (calibration check)", r)
            if base_toi >= 12:
                if dtoi >= 3.2: add(f"{m} | ice time down 3.2+ min over 3 games", r)
                if dtoi >= 4.0: add(f"{m} | ice time down 4.0+ min over 3 games", r)
                if -dtoi >= 3.2: add(f"{m} | ice time up 3.2+ min over 3 games", r)
            if pp_b >= 1.0 and pp_b - pp_3 >= 0.75: add(f"{m} | power-play time down 0.75+ min over 3 games", r)
            if pp_3 - pp_b >= 0.75 and pp_3 >= 1.0: add(f"{m} | power-play time up 0.75+ min over 3 games", r)
            # the EXACT conditions the live flags use (operational/eggy_intelligence.py recency_overlay): the model's own expectation against the latest games
            gap_toi = w["toi"] - sum(x["toi"] for x in l3) / 3
            if gap_toi >= 3.2: add(f"{m} | LIVE FLAG TOI_DROP: model expects 3.2+ min more than his last 3 games", r, ("TOI_DROP", m))
            if gap_toi >= 4.0: add(f"{m} | LIVE FLAG TOI_DROP (high): model expects 4.0+ min more than his last 3 games", r, ("TOI_DROP_HIGH", m))
            if w["toi_pp"] >= 1.0 and w["toi_pp"] - pp_3 >= 0.75: add(f"{m} | LIVE FLAG PP_ROLE_LOST: model expects 0.75+ min more PP than his last 3 games", r, ("PP_ROLE_LOST", m))
            l6 = g[i - 6:i]
            if w["toi"] - sum(x["toi"] for x in l6) / 6 >= 2.0: add(f"{m} | LIVE FLAG ROLE_ASSUMPTION: model expects 2.0+ min more than his last 6 games", r, ("ROLE_ASSUMPTION", m))
            if stat == "points" and w["toi_pp"] - sum(x["toi_pp"] for x in l6) / 6 >= 0.75: add(f"{m} | LIVE FLAG POWER_PLAY_ASSUMPTION: model expects 0.75+ PP min more than his last 6 games", r, ("POWER_PLAY_ASSUMPTION", m))
            pw = prev_walk.get((w["pid"], w["date"]))
            if pw and pw[0] - pw[1] >= 4.0: add(f"{m} | LIVE CAUTION POSTMORTEM_ROLE: his previous game's ice time came in 4.0+ min under the model's expectation", r, ("POSTMORTEM_ROLE_CAUTION", m))
            if pw and pw[2] - pw[3] >= 1.0 and pw[2] >= 1.0: add(f"{m} | LIVE CAUTION POSTMORTEM_PP: his previous game's PP time came in 1.0+ min under the model's expectation", r)
            if stat == "shots":
                rec30 = sum(1 for x in g[i - 30:i] if x["shots"] >= k) / 30.0
                anch = min(p, rec30)
                est_rows[k].append((p, min(rec30, p + 0.02, 0.70 if k >= 3 else 1.0), y))                                        # the live shots estimate holds the record to the model (the anchor)
                if p - rec30 >= 0.05: add(f"{m} | LIVE FLAG MODEL_ABOVE_RECORD (5+ pts) vs the MODEL", r, ("MODEL_ABOVE_RECORD", m)); add(f"{m} | LIVE FLAG MODEL_ABOVE_RECORD (5+ pts) vs the ANCHORED estimate", y - anch)
                if p - rec30 >= 0.10: add(f"{m} | LIVE FLAG MODEL_ABOVE_RECORD (10+ pts) vs the MODEL", r, ("MODEL_ABOVE_RECORD_HIGH", m)); add(f"{m} | LIVE FLAG MODEL_ABOVE_RECORD (10+ pts) vs the ANCHORED estimate", y - anch)
            hits5 = sum(1 for x in l5 if x[stat] >= k)
            if p >= 0.55 and hits5 <= 1: add(f"{m} | LIVE FLAG RECENT_FORM_BELOW_MODEL: estimate 55%+, reached it in 0-1 of last 5", r, ("RECENT_FORM_BELOW_MODEL", m))
            if p >= 0.45 and hits5 <= 1: add(f"{m} | reached it in 0-1 of last 5 (estimate 45%+)", r)
            if p >= 0.30 and hits5 >= 4: add(f"{m} | reached it in 4-5 of last 5 (estimate 30%+)", r)
            add(f"{m} | {'home' if home else 'away'}", r)
            if prev_gap <= 1: add(f"{m} | played yesterday (back-to-back proxy)", r)
            elif prev_gap >= 4: add(f"{m} | 4+ days since the last game", r)
    signals = {}
    for name, (n, s, sq) in sorted(acc.items()):
        mean = s / n
        se = math.sqrt(max(sq / n - mean * mean, 1e-12) / n)
        z = mean / se
        adj = None
        if n >= 300 and z <= -3.0 and ("LIVE FLAG" in name or "LIVE CAUTION" in name):
            adj = round(min(mean + 1.645 * se, 0.0), 4)              # the conservative end of the interval, downward only
        signals[name] = {"flag": (meta.get(name) or (None,))[0], "n": n, "mean_residual_vs_model": round(mean, 4), "se": round(se, 4), "z": round(z, 2),
                         "verdict": ("ALREADY_IN_MODEL" if abs(z) < 3 else ("BEYOND_MODEL_LOWER" if z < 0 else "BEYOND_MODEL_RAISE_NOT_APPLIED")),
                         "live_adjustment": adj}
    est = {}
    for k, rs in est_rows.items():
        rng = random.Random(11)
        d = [_ll(b, y) - _ll(a, y) for a, b, y in rs]
        means = sorted(sum(rng.choice(d) for _ in range(len(d))) / len(d) for _ in range(300))
        both = lambda lo: [(a, b, y) for a, b, y in rs if a >= lo or b >= lo]
        adm_model = [y for a, b, y in rs if a >= 0.55]
        adm_anchor = [y for a, b, y in rs if b >= 0.55]
        model_only = [y for a, b, y in rs if a >= 0.55 and b < 0.55]
        est[f"shots>={k}"] = {"rows": len(rs), "log_loss_model_probability": round(sum(_ll(a, y) for a, b, y in rs) / len(rs), 5), "log_loss_anchored_estimate": round(sum(_ll(b, y) for a, b, y in rs) / len(rs), 5),
                              "paired_delta_anchored_minus_model": round(sum(d) / len(d), 5), "ci95": [round(means[7], 5), round(means[292], 5)],
                              "legs_the_model_would_admit_at_55pct": {"n": len(adm_model), "hit_rate": round(sum(adm_model) / max(len(adm_model), 1), 4)},
                              "legs_the_anchor_admits_at_55pct": {"n": len(adm_anchor), "hit_rate": round(sum(adm_anchor) / max(len(adm_anchor), 1), 4)},
                              "legs_only_the_model_admits": {"n": len(model_only), "hit_rate": round(sum(model_only) / max(len(model_only), 1), 4)}}
    flag_evidence = defaultdict(dict)
    for name, fm in meta.items():
        sg = signals[name]
        flag_evidence[fm[0]][fm[1].split(">=")[0] + ">=" + fm[1].split(">=")[1]] = {"n": sg["n"], "mean_residual_vs_model": sg["mean_residual_vs_model"], "z": sg["z"], "verdict": sg["verdict"], "live_adjustment": sg["live_adjustment"]}
    out = {"model": "player-rate-toi-v2", "rows_walked": len(walked), "flag_evidence": flag_evidence, "estimate_formulas": est, "method": __doc__.split("\n\n")[1].strip(), "signals": signals, "ablations": {}}
    # --- ablations: the model with one ingredient off, on the same rows
    base = {(w["pid"], w["date"]): w for w in walked}

    def losses(c):
        wk = walk(c, (2025, 2026), rows, with_baselines=False)
        d = {}
        for w in wk:
            if (w["pid"], w["date"]) in base:
                for stat, k in MARKETS:
                    y = 1 if w["actual"][stat] >= k else 0
                    d[(w["pid"], w["date"], stat, k)] = _ll(prob(w, stat, k), y)
        return d
    L0 = losses(cfg)
    for name, c in (("opponent environment OFF (opp_beta=0)", dataclasses.replace(cfg, opp_beta=0.0)), ("ice-time recency OFF (toi_half_life=1000 games)", dataclasses.replace(cfg, toi_half_life_games=1000.0)),
                    ("rate recency slower (rate_half_life=90 games)", dataclasses.replace(cfg, rate_half_life_games=90.0)), ("rate recency faster (rate_half_life=10 games)", dataclasses.replace(cfg, rate_half_life_games=10.0)),
                    ("rate recency slower (rate_half_life=60 games)", dataclasses.replace(cfg, rate_half_life_games=60.0)), ("rate recency much slower (rate_half_life=150 games)", dataclasses.replace(cfg, rate_half_life_games=150.0))):
        L1 = losses(c)
        per = {}
        for stat, k in MARKETS:
            keys = [x for x in L0 if x[2] == stat and x[3] == k and x in L1]
            d = [L1[x] - L0[x] for x in keys]
            rng = random.Random(7)
            means = sorted(sum(rng.choice(d) for _ in range(len(d))) / len(d) for _ in range(300))
            lo, hi = means[7], means[292]
            per[f"{stat}>={k}"] = {"n": len(d), "delta_log_loss_vs_live_model": round(sum(d) / len(d), 5), "ci95": [round(lo, 5), round(hi, 5)],
                                   "verdict": "THE INGREDIENT HELPS (removing it hurts)" if lo > 0 else ("removing it HELPS (the ingredient hurts)" if hi < 0 else "NO_DEMONSTRATED_DIFFERENCE")}
        out["ablations"][name] = per
    Path(a.out).write_text(json.dumps(out, indent=1))
    for k, v in signals.items():
        print(f"{k:78s} n {v['n']:>6} resid {v['mean_residual_vs_model']:+.4f} z {v['z']:+6.2f}  {v['verdict']}  adj {v['live_adjustment']}")
    for k, per in out["ablations"].items():
        print(k, {m: (c['delta_log_loss_vs_live_model'], c['verdict'][:22]) for m, c in per.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
