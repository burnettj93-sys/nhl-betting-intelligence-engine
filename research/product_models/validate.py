"""
Chronological out-of-sample validation of the skater projections and of the live shots model.

    python3 -m research.product_models.validate [--out docs/validation]

Design (no look-ahead anywhere: a player-game is projected using only games on EARLIER dates):
  * TRAIN      2022-23 and 2023-24: state warm-up and model-configuration search.
  * CALIBRATE  2024-25: pick the configuration by log loss, fit the count dispersion, fit a Platt calibration.
  * FINAL EVAL 2025-26: untouched by any choice above. Everything reported as "final" comes from here only.
Baselines: the live shots formula (lower of last-20/last-60 hit rates, shrunk) and a last-30 empirical hit rate.
Reported per market/threshold: sample size, base rate, Brier, log loss, calibration by predicted-probability bin,
and splits by games of history and by role change. Nothing here tunes to tickets, and nothing here is profit evidence:
no genuine historical odds are available, so profitability is only measurable prospectively (frozen predictions + prices).
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from research.product_models import history, projections as P  # noqa: E402

TRAIN_SEASONS, CALIB_SEASON, FINAL_SEASON = (2022, 2023), 2024, 2025
EVENTS = {"shots": (1, 2, 3, 4, 5), "points": (1, 2), "goals": (1,), "assists": (1,), "hits": (1, 2, 3), "blocks": (1, 2)}
MIN_PRIOR_GAMES = 5


def _clip(p: float) -> float:
    return min(max(p, 1e-4), 1 - 1e-4)


class _LiveShotsBaseline:
    """Replica of operational/best_bets.py's shots probabilities: min(shrunk last-20, shrunk last-60 hit rate)."""

    def __init__(self):
        self.hist: dict[str, list[float]] = defaultdict(list)
        self.prior: dict[tuple, float] = {}
        self._season_rows: list[dict] = []

    def set_prior(self, rows: list[dict]) -> None:
        for grp in ("F", "D"):
            sub = [r for r in rows if P.group_of(r["pos"]) == grp and r["toi"] > 10.0]
            for k in (1, 2, 3, 4, 5):
                self.prior[(grp, k)] = (sum(1 for r in sub if r["shots"] >= k) / len(sub)) if sub else 0.3

    def predict(self, pid: str, grp: str, k: int) -> float | None:
        h = self.hist.get(pid)
        if not h or len(h) < 20:
            return None
        prior = self.prior.get((grp, k), 0.3)
        def shr(vals):
            return (sum(1.0 if v >= k else 0.0 for v in vals) + 8 * prior) / (len(vals) + 8)
        return min(shr(h[-20:]), shr(h[-60:]))

    def consume(self, rows: list[dict]) -> None:
        for r in rows:
            self.hist[r["player_id"]].append(r["shots"])


class _EmpiricalBaseline:
    def __init__(self):
        self.hist: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        self.base: dict = defaultdict(lambda: [0.0, 0.0])

    def predict(self, pid: str, stat: str, k: int) -> float | None:
        h = self.hist[pid][stat]
        if len(h) < MIN_PRIOR_GAMES:
            return None
        a, b = self.base[(stat, k)]
        prior = a / b if b else 0.2
        recent = h[-30:]
        return (sum(1.0 for v in recent if v >= k) + 8 * prior) / (len(recent) + 8)

    def consume(self, rows: list[dict]) -> None:
        for r in rows:
            for stat, ks in EVENTS.items():
                self.hist[r["player_id"]][stat].append(r[stat])
                for k in ks:
                    cell = self.base[(stat, k)]
                    cell[0] += 1.0 if r[stat] >= k else 0.0
                    cell[1] += 1.0


def walk(config: P.ModelConfig, seasons: tuple[int, ...], rows: list[dict], with_baselines: bool = True) -> list[dict]:
    """Project every eligible player-game in `seasons`; state also consumes all earlier seasons."""
    model = P.SkaterModel(config)
    live, emp = _LiveShotsBaseline(), _EmpiricalBaseline()
    last_season, out = None, []
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["season"] <= max(seasons):
            by_day[r["date"]].append(r)
    for day in sorted(by_day):
        day_rows = by_day[day]
        season = day_rows[0]["season"]
        if with_baselines and season != last_season:
            prev = [r for r in rows if r["season"] == season - 1]
            live.set_prior(prev or [r for r in rows if r["season"] < season][-20000:])
            last_season = season
        if season in seasons:
            for r in day_rows:
                n_prior = model.sample_size(r["player_id"])
                if n_prior < MIN_PRIOR_GAMES:
                    continue
                proj = model.project(r["player_id"], r["opp"])
                if proj is None:
                    continue
                st = model.players[r["player_id"]]
                recent = proj["recent_toi"]
                role_change = bool(len(recent) >= 5 and st.toi / max(st.w_toi, 1e-9) > 0 and
                                   abs(sum(recent[-3:]) / 3 - sum(recent) / len(recent)) / max(sum(recent) / len(recent), 1e-9) > 0.2)
                rec = {"season": season, "pid": r["player_id"], "grp": proj["position_group"], "n_prior": n_prior,
                       "role_change": role_change, "mean": {s: proj[s] for s in P.STATS}, "toi": proj["toi"],
                       "toi_pp": proj["toi_pp"], "actual": {s: r[s] for s in P.STATS},
                       "actual_toi": r["toi"], "actual_pp": r["toi_pp"]}
                if with_baselines:
                    rec["live_shots"] = {k: live.predict(r["player_id"], proj["position_group"], k) for k in EVENTS["shots"]}
                    rec["emp"] = {(s, k): emp.predict(r["player_id"], s, k) for s, ks in EVENTS.items() for k in ks}
                out.append(rec)
        model.consume_day(day_rows)
        if with_baselines:
            live.consume(day_rows)
            emp.consume(day_rows)
    return out


def _metrics(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(y * math.log(_clip(p)) + (1 - y) * math.log(1 - _clip(p)) for p, y in pairs) / n
    return {"n": n, "base_rate": round(sum(y for _, y in pairs) / n, 4), "brier": round(brier, 5), "log_loss": round(ll, 5),
            "mean_pred": round(sum(p for p, _ in pairs) / n, 4)}


def _calibration(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    cells = defaultdict(lambda: [0, 0.0, 0])
    for p, y in pairs:
        b = min(int(p * bins), bins - 1)
        cells[b][0] += 1
        cells[b][1] += p
        cells[b][2] += y
    return [{"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "n": c[0], "mean_pred": round(c[1] / c[0], 4), "observed": round(c[2] / c[0], 4)}
            for b, c in sorted(cells.items()) if c[0] >= 30]


def fit_platt(pairs: list[tuple[float, int]], iters: int = 200) -> tuple[float, float]:
    """Logistic recalibration p' = sigmoid(a*logit(p) + b) by gradient descent on log loss."""
    a, b = 1.0, 0.0
    if len(pairs) < 500:
        return a, b
    xs = [(math.log(_clip(p) / (1 - _clip(p))), y) for p, y in pairs]
    lr = 0.05
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in xs:
            q = 1 / (1 + math.exp(-(a * x + b)))
            ga += (q - y) * x
            gb += (q - y)
        a -= lr * ga / len(xs)
        b -= lr * gb / len(xs)
    return a, b


def apply_platt(p: float, a: float, b: float) -> float:
    return 1 / (1 + math.exp(-(a * math.log(_clip(p) / (1 - _clip(p))) + b)))


def model_probs(records: list[dict], alphas: dict[str, float]) -> list[dict]:
    for rec in records:
        rec["p"] = {(s, k): P.prob_at_least(k, rec["mean"][s], alphas[s]) for s, ks in EVENTS.items() for k in ks}
    return records


def _pairs(records, key, getter):
    out = []
    for rec in records:
        p = getter(rec)
        if p is None:
            continue
        stat, k = key
        out.append((p, 1 if rec["actual"][stat] >= k else 0))
    return out


def score_config(config: P.ModelConfig, rows: list[dict]) -> float:
    """Log loss on the calibration season (dispersion fitted on the TRAIN seasons only) for the key events."""
    train = walk(config, TRAIN_SEASONS, rows, with_baselines=False)
    alphas = {s: P.fit_dispersion([(r["mean"][s], r["actual"][s]) for r in train]) for s in P.STATS}
    calib = model_probs(walk(config, (CALIB_SEASON,), rows, with_baselines=False), alphas)
    total = 0.0
    for key in (("shots", 2), ("shots", 3), ("points", 1), ("goals", 1), ("hits", 2), ("blocks", 1)):
        total += _metrics(_pairs(calib, key, lambda r, k=key: r["p"][k]))["log_loss"]
    return total


def main(out_dir: Path | None = None) -> dict:
    t0 = time.time()
    rows = history.skater_games()
    configs = [P.ModelConfig(rate_half_life_games=h, toi_half_life_games=t, k_minutes=k, opp_beta=b)
               for h, t, k, b in ((30, 8, 600, 0.5), (30, 8, 600, 0.0), (15, 8, 600, 0.5), (60, 8, 600, 0.5),
                                  (30, 5, 600, 0.5), (30, 15, 600, 0.5), (30, 8, 300, 0.5), (30, 8, 1200, 0.5))]
    grid = [(score_config(c, rows), c) for c in configs]
    grid.sort(key=lambda t: t[0])
    best = grid[0][1]

    train = walk(best, TRAIN_SEASONS, rows, with_baselines=False)
    alphas = {s: P.fit_dispersion([(r["mean"][s], r["actual"][s]) for r in train]) for s in P.STATS}
    calib = model_probs(walk(best, (CALIB_SEASON,), rows), alphas)
    platt = {}
    for stat, ks in EVENTS.items():
        for k in ks:
            platt[f"{stat}>={k}"] = fit_platt(_pairs(calib, (stat, k), lambda r, key=(stat, k): r["p"][key]))
    final = model_probs(walk(best, (FINAL_SEASON,), rows), alphas)

    report: dict = {"model_version": P.MODEL_VERSION, "chosen_config": best.__dict__ | {"opp_cap": list(best.opp_cap)},
                    "config_search_calibration_logloss_sum": [{"config": c.__dict__ | {"opp_cap": list(c.opp_cap)}, "score": round(s, 5)} for s, c in grid],
                    "dispersion": {k: round(v, 4) for k, v in alphas.items()},
                    "split": {"train": list(TRAIN_SEASONS), "calibration": CALIB_SEASON, "final_evaluation": FINAL_SEASON},
                    "rows": {"calibration": len(calib), "final": len(final)}, "markets": {}, "expected_value_accuracy": {}}
    for stat, ks in EVENTS.items():
        mae = {"model": sum(abs(r["mean"][stat] - r["actual"][stat]) for r in final) / len(final),
               "league_prior": None}
        report["expected_value_accuracy"][stat] = {"mae_model": round(mae["model"], 4), "n": len(final)}
        for k in ks:
            key = (stat, k)
            # compare every source on the SAME rows: those where all baselines produced a number
            rows_c = [r for r in final if r["emp"][key] is not None and (stat != "shots" or r["live_shots"][k] is not None)]
            raw = _pairs(rows_c, key, lambda r, key=key: r["p"][key])
            a, b = platt[f"{stat}>={k}"]
            cal = _pairs(rows_c, key, lambda r, key=key, a=a, b=b: apply_platt(r["p"][key], a, b))
            entry = {"common_rows": len(rows_c), "model_raw": _metrics(raw), "model_platt": _metrics(cal),
                     "baseline_last30_empirical": _metrics(_pairs(rows_c, key, lambda r, key=key: r["emp"][key])),
                     "calibration_raw": _calibration(raw), "platt": [round(a, 4), round(b, 4)]}
            if stat == "shots":
                entry["baseline_live_formula"] = _metrics(_pairs(rows_c, key, lambda r, k=k: r["live_shots"][k]))
                entry["calibration_live_formula"] = _calibration(_pairs(rows_c, key, lambda r, k=k: r["live_shots"][k]))
            # restrict comparison to rows where every compared source produced a number
            report["markets"][f"{stat}>={k}"] = entry
    for label, test in (("games_prior_5_to_19", lambda r: r["n_prior"] < 20), ("games_prior_20_plus", lambda r: r["n_prior"] >= 20),
                        ("role_changed", lambda r: r["role_change"]), ("role_stable", lambda r: not r["role_change"])):
        sub = [r for r in final if test(r)]
        report.setdefault("splits", {})[label] = {
            f"{s}>={k}": _metrics(_pairs(sub, (s, k), lambda r, key=(s, k): r["p"][key]))
            for s, k in (("shots", 2), ("shots", 3), ("points", 1), ("goals", 1), ("hits", 2), ("blocks", 1))}
    toi_err = [abs(r["toi"] - r["actual_toi"]) for r in final]
    pp_err = [abs(r["toi_pp"] - r["actual_pp"]) for r in final]
    report["expected_value_accuracy"]["toi"] = {"mae_model": round(sum(toi_err) / len(toi_err), 3), "n": len(final)}
    report["expected_value_accuracy"]["toi_pp"] = {"mae_model": round(sum(pp_err) / len(pp_err), 3), "n": len(final)}
    report["platt_params"] = {k: [round(v[0], 4), round(v[1], 4)] for k, v in platt.items()}
    report["elapsed_seconds"] = round(time.time() - t0, 1)
    out_dir = Path(out_dir or REPO / "docs" / "validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "skater_projection_validation.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    rep = main()
    print(json.dumps({k: rep[k] for k in ("model_version", "chosen_config", "dispersion", "rows", "elapsed_seconds")}, indent=1))
    for name, m in rep["markets"].items():
        base = m.get("baseline_live_formula") or m["baseline_last30_empirical"]
        emp = m["baseline_last30_empirical"]
        print(f"{name:10} n={m['common_rows']:6} model ll {m['model_raw']['log_loss']:.4f} (brier {m['model_raw']['brier']:.4f}) platt ll {m['model_platt']['log_loss']:.4f} | "
              f"{'live-formula' if 'baseline_live_formula' in m else 'last30'} ll {base['log_loss']:.4f} | last30 ll {emp['log_loss']:.4f}")
