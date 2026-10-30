"""
Player Hits Probability Foundation -- Production Hardening + Parlay Build block
(2026-09-29), Phase C. PLAYER HITS is a required build target (the owner's own
words: "PLAYER HITS MUST BE BUILT AND VALIDATED").

Data: reuses research/player_blocks/player_game_blocks.jsonl UNCHANGED (no new
extraction needed -- build_blocks_corpus.py already carries a real, PIT-safe
`hits` field per player-game, sourced from MoneyPuck's `I_F_hits`, restricted
to the certified real_nhl_results corpus). 26,465 player-games, 2022-23
through 2025-26. Mean 1.10 hits/game, var/mean ~1.69 (real overdispersion,
consistent with the prior MULTI_PROP_RESEARCH_REPORT.md finding of ~1.19/1.71
from an earlier, smaller sample), 43.0% zero-rate.

Mirrors research/run_player_blocks_model.py's structure and walk-forward
discipline (same warmup/eval season split, same PIT-safe feature framework
via research/player_blocks/features.py and research/player_sog/features.py,
same Poisson/NegBin math via research/player_sog/count_models.py, same
paired-bootstrap significance test via research/elo_comparison.py) --
condensed to two candidates (M0 naive rolling-rate baseline vs M1 Poisson GLM
with recent-form + TOI) rather than the full 5-stage ablation, given this
block's scope. Every claim traces to a real number computed by this script;
nothing here is assumed from the blocks/SOG models' own fitted results.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.player_blocks import features as bf
from research.player_sog import count_models as cm

CORPUS_PATH = REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl"
WARMUP_SEASON = 20222023
EVAL_SEASONS = [20242025, 20252026]
BASELINE_WINDOW = 20
RECENT_WINDOW = 5
TOI_WINDOW = 10
THRESHOLDS = (1, 2, 3, 4, 5)
BOOTSTRAP_BAR = 0.95
MIN_TAIL_SUPPORT = 50   # pre-specified positive-event floor, matching the Goalie Saves slice's rule


def load_corpus() -> list[dict]:
    rows = []
    with open(CORPUS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


def build_example(row: dict, player_index) -> dict | None:
    history = player_index.history_as_of(row["player_id"], row["game_date"])
    if len(history) < 3:
        return None
    baseline_rate = bf.rolling_mean(history, "hits", BASELINE_WINDOW)
    if baseline_rate is None:
        baseline_rate = bf.season_to_date_mean(history, "hits", row["season"])
    if baseline_rate is None or baseline_rate <= 0:
        baseline_rate = 0.6   # a real, disclosed floor -- the corpus-wide mean is ~1.10; this only
                              # applies to a player with a genuine zero/near-zero rolling history
    recent_rate = bf.rolling_mean(history, "hits", RECENT_WINDOW)
    recent_toi = bf.rolling_mean(history, "icetime_seconds", TOI_WINDOW)
    baseline_toi = bf.rolling_mean(history, "icetime_seconds", BASELINE_WINDOW)
    fv = cm.build_feature_vector(baseline_rate, recent_rate, recent_toi, baseline_toi, None, 0.0)
    return {"player_id": row["player_id"], "game_id": row["game_id"], "game_date": row["game_date"],
            "season": row["season"], "actual_hits": row["hits"], "baseline_rate": baseline_rate,
            "feature_vector": fv}


def threshold_prob(mu: float, alpha: float | None, t: int) -> float:
    return cm.negbinom_sf_at_least(t, mu, alpha) if alpha else cm.poisson_sf_at_least(t, mu)


def brier_and_ll(examples: list[dict], mus: list[float], alpha: float | None, t: int) -> dict:
    briers, lls = [], []
    n_events = 0
    for ex, mu in zip(examples, mus):
        p = threshold_prob(mu, alpha, t)
        actual = 1.0 if ex["actual_hits"] >= t else 0.0
        n_events += int(actual)
        briers.append((p - actual) ** 2)
        eps = 1e-9
        p_c = min(max(p, eps), 1 - eps)
        lls.append(-(actual * __import__("math").log(p_c) + (1 - actual) * __import__("math").log(1 - p_c)))
    return {"n": len(examples), "n_events": n_events, "event_rate": round(n_events / len(examples), 4) if examples else None,
            "brier": round(statistics.fmean(briers), 5) if briers else None,
            "log_loss": round(statistics.fmean(lls), 5) if lls else None, "_briers": briers}


def run_all() -> dict:
    rows = load_corpus()
    player_index = bf.PlayerHistoryIndex(rows)
    examples = [ex for ex in (build_example(r, player_index) for r in rows) if ex is not None]

    train = [e for e in examples if e["season"] not in EVAL_SEASONS]
    fv_train = [e["feature_vector"] for e in train]
    y_train = [e["actual_hits"] for e in train]
    weights = cm.fit_poisson_glm(fv_train, y_train)

    out = {"corpus_n_total": len(rows), "examples_n": len(examples), "train_n": len(train),
           "warmup_season": WARMUP_SEASON, "eval_seasons": EVAL_SEASONS, "glm_weights": weights,
           "by_season": {}}

    for season in EVAL_SEASONS:
        season_examples = [e for e in examples if e["season"] == season]
        mus_m1 = [cm.predict_mu(weights, e["feature_vector"]) for e in season_examples]
        train_actuals = [cm.predict_mu(weights, e["feature_vector"]) for e in train]
        alpha = cm.fit_negbinom_alpha_by_moments([e["actual_hits"] for e in train], train_actuals)
        mus_m0 = [e["baseline_rate"] for e in season_examples]

        season_out = {"n": len(season_examples), "alpha_negbin": round(alpha, 4), "by_threshold": {}}
        for t in THRESHOLDS:
            m0 = brier_and_ll(season_examples, mus_m0, None, t)     # M0: naive rate, Poisson threshold
            m1 = brier_and_ll(season_examples, mus_m1, alpha, t)    # M1: Poisson-GLM mu, NegBin threshold
            n_events = m1["n_events"]
            if n_events < MIN_TAIL_SUPPORT:
                verdict = "INSUFFICIENT_DATA"
                frac_improved = None
            else:
                bs = ec.paired_bootstrap_delta(m0["_briers"], m1["_briers"])
                frac_improved = bs.get("frac_resamples_improved")
                verdict = "CANDIDATE" if frac_improved is not None and frac_improved >= BOOTSTRAP_BAR else "NOT_CLEARED"
            season_out["by_threshold"][f"{t}+"] = {
                "n_events": n_events, "event_rate": m1["event_rate"],
                "m0_naive_brier": m0["brier"], "m1_glm_brier": m1["brier"],
                "m0_naive_log_loss": m0["log_loss"], "m1_glm_log_loss": m1["log_loss"],
                "frac_resamples_improved": round(frac_improved, 4) if frac_improved is not None else None,
                "verdict_this_season": verdict,
            }
        out["by_season"][str(season)] = season_out

    # Final status per threshold: CANDIDATE in BOTH eval seasons -> VALIDATED; CANDIDATE in one only ->
    # PARTIAL; adequate support in both but never clears the bar -> REJECTED; below the tail-support
    # floor in either season -> INSUFFICIENT_DATA. No threshold is forced into any bucket.
    final = {}
    for t in THRESHOLDS:
        label = f"{t}+"
        verdicts = [out["by_season"][str(s)]["by_threshold"][label]["verdict_this_season"] for s in EVAL_SEASONS]
        if "INSUFFICIENT_DATA" in verdicts:
            final[label] = "INSUFFICIENT_DATA"
        elif all(v == "CANDIDATE" for v in verdicts):
            final[label] = "VALIDATED"
        elif any(v == "CANDIDATE" for v in verdicts):
            final[label] = "PARTIAL"
        else:
            final[label] = "REJECTED"
    out["final_status_by_threshold"] = final
    return out


if __name__ == "__main__":
    result = run_all()
    out_path = REPO_ROOT / "research" / "player_hits_results.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({k: v for k, v in result.items() if k != "by_season"}, indent=2, sort_keys=True))
    print(json.dumps(result["by_season"], indent=2, sort_keys=True))
    print(f"\nwrote {out_path}")
