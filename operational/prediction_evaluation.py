"""
Frozen predictions against official results, one market and one estimate version at a time. A report only: nothing here changes a model, a threshold or a recommendation, and nothing here
is ever used to retune on yesterday's outcomes.

Per (market, model_version) group, on SETTLED rows only (WIN/LOSS; pending, push, void and unresolved rows are counted but never scored):
  * the model's Brier score and log loss;
  * two simple baselines, scored on the SAME rows: CLIMATOLOGY (the hit rate of every OTHER settled row of the same market, leave-one-game-out, so the baseline never sees the game it is scored on)
    and the MARKET (the book's no-vig probability at prediction time, only on rows that carried one);
  * the paired Brier difference (model minus baseline; negative means the model was better) with a cluster-bootstrap 95% interval.
Rows are not independent: one game produces several observations and one player appears in many games. The interval is therefore resampled by game AND by player and the WIDER of the two is
reported, and a group needs MIN_CLUSTERS distinct clusters before any verdict is offered. The verdict never says the model "works": it is NO_EVIDENCE_MODEL_BETTER, BETTER_THAN_BASELINE_EVIDENCE,
WORSE_THAN_BASELINE_EVIDENCE, or INSUFFICIENT_SAMPLE.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

MIN_CLUSTERS = 30
BOOTSTRAP_REPS = 2000
SEED = 20261017


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


def _log_loss(p: float, y: float, eps: float = 1e-9) -> float:
    p = min(max(p, eps), 1 - eps)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _cluster_ci(diffs: list[float], clusters: list[str], rng: random.Random, reps: int = BOOTSTRAP_REPS) -> tuple[float, float] | None:
    """Bootstrap the mean of per-row differences by resampling whole clusters."""
    groups: dict[str, list[float]] = defaultdict(list)
    for d, c in zip(diffs, clusters):
        groups[c].append(d)
    keys = list(groups)
    if len(keys) < 2:
        return None
    sums = {k: (sum(v), len(v)) for k, v in groups.items()}
    means = []
    for _ in range(reps):
        picks = [sums[rng.choice(keys)] for _ in keys]
        n = sum(c for _, c in picks)
        means.append(sum(s for s, _ in picks) / n)
    means.sort()
    return means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def _paired(model_p: list[float], base_p: list[float], y: list[float], games: list[str], players: list[str]) -> dict:
    diffs = [_brier(m, o) - _brier(b, o) for m, b, o in zip(model_p, base_p, y)]
    rng = random.Random(SEED)
    cis = [ci for ci in (_cluster_ci(diffs, games, rng), _cluster_ci(diffs, players, rng)) if ci]
    lo = min(c[0] for c in cis) if cis else None
    hi = max(c[1] for c in cis) if cis else None
    n_clusters = min(len(set(games)), len(set(players)))
    if n_clusters < MIN_CLUSTERS or lo is None:
        verdict = "INSUFFICIENT_SAMPLE"
    elif hi < 0:
        verdict = "BETTER_THAN_BASELINE_EVIDENCE"
    elif lo > 0:
        verdict = "WORSE_THAN_BASELINE_EVIDENCE"
    else:
        verdict = "NO_EVIDENCE_MODEL_BETTER"
    return {"n": len(y), "brier_model": statistics.fmean(_brier(m, o) for m, o in zip(model_p, y)), "brier_baseline": statistics.fmean(_brier(b, o) for b, o in zip(base_p, y)),
            "log_loss_model": statistics.fmean(_log_loss(m, o) for m, o in zip(model_p, y)), "log_loss_baseline": statistics.fmean(_log_loss(b, o) for b, o in zip(base_p, y)),
            "brier_diff_model_minus_baseline": statistics.fmean(diffs), "ci95": [lo, hi], "independent_clusters": n_clusters, "verdict": verdict}


def evaluate(rows: list[dict]) -> dict:
    """rows: ledger rows (dicts with market_id, model_version, raw_probability, result_status, game_id, player_id, market_no_vig_probability)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("market_id"), r.get("model_version"))].append(r)
    by_market: dict = defaultdict(list)
    for r in rows:
        if r.get("result_status") in ("WIN", "LOSS") and r.get("raw_probability") is not None:
            by_market[r.get("market_id")].append(r)
    out = []
    for (market, version), grp in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        scored = [r for r in grp if r.get("result_status") in ("WIN", "LOSS") and r.get("raw_probability") is not None]
        item = {"market": market, "model_version": version, "predictions": len(grp), "settled": len(scored), "pending_or_unscored": len(grp) - len(scored)}
        if scored:
            y = [1.0 if r["result_status"] == "WIN" else 0.0 for r in scored]
            games = [str(r.get("game_id")) for r in scored]
            players = [str(r.get("player_id") or r.get("game_id")) for r in scored]
            p = [float(r["raw_probability"]) for r in scored]
            item.update({"hit_rate": statistics.fmean(y), "mean_predicted": statistics.fmean(p), "distinct_games": len(set(games)), "distinct_players": len(set(players))})
            pool = by_market[market]
            # leave-one-game-out climatology: the hit rate of every settled row of this market from OTHER games
            tot = defaultdict(lambda: [0, 0])
            for r in pool:
                t = tot[str(r.get("game_id"))]
                t[0] += 1
                t[1] += 1 if r["result_status"] == "WIN" else 0
            n_all, w_all = sum(t[0] for t in tot.values()), sum(t[1] for t in tot.values())
            clim = []
            for g in games:
                n_o, w_o = n_all - tot[g][0], w_all - tot[g][1]
                clim.append(w_o / n_o if n_o else w_all / n_all)
            item["vs_climatology"] = _paired(p, clim, y, games, players)
            mk = [r for r in scored if r.get("market_no_vig_probability") is not None]
            if mk:
                item["vs_market"] = _paired([float(r["raw_probability"]) for r in mk], [float(r["market_no_vig_probability"]) for r in mk],
                                            [1.0 if r["result_status"] == "WIN" else 0.0 for r in mk], [str(r.get("game_id")) for r in mk],
                                            [str(r.get("player_id") or r.get("game_id")) for r in mk])
            else:
                item["vs_market"] = {"verdict": "NO_MARKET_PRICES_ON_THESE_ROWS", "n": 0}
        out.append(item)
    return {"method": "frozen predictions vs official results; per market and model version; paired Brier difference vs leave-one-game-out climatology and vs market no-vig probability; "
                      "95% interval = wider of the game-cluster and player-cluster bootstrap; no retuning.", "min_clusters_for_a_verdict": MIN_CLUSTERS, "groups": out}


def load_rows(db_path: str | Path = "operational/prospective_observations.db") -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT market_id, model_version, raw_probability, result_status, game_id, player_id, market_no_vig_probability FROM predictions")]
    finally:
        con.close()


def render(report: dict) -> str:
    lines = ["# Prediction evaluation (frozen estimates vs official results)", "", report["method"], ""]
    for g in report["groups"]:
        lines.append(f"## {g['market']} / {g['model_version']}: {g['settled']} settled of {g['predictions']}")
        if not g["settled"]:
            lines += ["No settled rows yet.", ""]
            continue
        for label, key in (("climatology", "vs_climatology"), ("market", "vs_market")):
            v = g[key]
            if v.get("n"):
                lines.append(f"* vs {label}: model Brier {v['brier_model']:.4f} vs {v['brier_baseline']:.4f}; diff {v['brier_diff_model_minus_baseline']:+.4f} (95% {v['ci95'][0]:+.4f} to {v['ci95'][1]:+.4f}, "
                             f"{v['independent_clusters']} clusters, n={v['n']}): **{v['verdict']}**" if v["ci95"][0] is not None else f"* vs {label}: {v['verdict']} (n={v['n']})")
            else:
                lines.append(f"* vs {label}: {v['verdict']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    rep = evaluate(load_rows())
    print(render(rep))
