"""
Driver for the Player Context State Validation slice (Parts 1-51).

Builds, per prop (SOG, Goals, Assists, Points, Blocks), a PIT-safe
performance-state (cold/hot) classification using each player's own
recent-vs-baseline production ratio (expectation-relative, Part 3), then
tests whether that state predicts the FROZEN marginal model's own
prediction residual at the next game -- the core UNDER-bias hypothesis.

MEDIA SENTIMENT COMPONENT: NOT BUILT this slice. No legitimate,
timestamped, legally-accessible historical media/news corpus exists in
this project (verified directly -- no media/news/sentiment files exist
anywhere in the repository). Per this slice's own explicit instructions
("do not scrape protected sources", "do not fabricate publication
times", "if media corpus availability prevents ... state this clearly"),
fabricating one would violate this project's core "never fabricate data"
principle. This is disclosed as a real, first-class finding (see the
report's Section E/L/U), not silently omitted.

ARENA EFFECTS: built using each game's HOME TEAM identity as a real,
disclosed proxy for "arena" (this project has no separate venue dataset).

CRITICAL: never refits Player SOG, Goals, Assists, Points, or Blocks.
Every marginal probability comes from each model's own frozen weights
via research/player_context_state/marginal_provenance.py.
"""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.player_context_state import arena_effects as ae
from research.player_context_state import context_state as cs
from research.player_context_state import marginal_provenance as mp
from research.player_sog import features as pf

WARMUP_SEASON = 20222023
TUNING_SEASON = 20232024
EVAL_SEASONS = [20242025, 20252026]

RESULTS_PATH = REPO_ROOT / "research" / "player_context_state_results.json"

PROP_CONFIG = {
    "sog": {"field": "sog", "min_history": 10},
    "goals": {"field": "goals", "min_history": 10},
    "assists": {"field": "assists", "min_history": 10},
    "points": {"field": "points", "min_history": 10},
    "blocks": {"field": "blocks", "min_history": 10},
}

MIN_STATE_SUPPORT = 200  # Part 32: pre-specified minimum sample size per state/prop/season


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def game_clustered_bootstrap_diff(examples_a, examples_b, values_a, values_b, n_resamples=1000, seed=20242025):
    """Difference-of-means bootstrap: resamples game_ids independently
    within each group (group A = e.g. COLD, group B = e.g. NORMAL), and
    reports how often mean(A) - mean(B) < 0 (Part 21's UNDER-bias
    direction: COLD residual more negative than NORMAL)."""
    by_game_a = defaultdict(list)
    for i, ex in enumerate(examples_a):
        by_game_a[ex["game_id"]].append(i)
    by_game_b = defaultdict(list)
    for i, ex in enumerate(examples_b):
        by_game_b[ex["game_id"]].append(i)
    game_ids_a = list(by_game_a.keys())
    game_ids_b = list(by_game_b.keys())
    point_delta = statistics.fmean(values_a) - statistics.fmean(values_b)
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx_a = []
        for _ in range(len(game_ids_a)):
            idx_a.extend(by_game_a[game_ids_a[rng.randrange(len(game_ids_a))]])
        idx_b = []
        for _ in range(len(game_ids_b)):
            idx_b.extend(by_game_b[game_ids_b[rng.randrange(len(game_ids_b))]])
        mean_a = statistics.fmean(values_a[i] for i in idx_a)
        mean_b = statistics.fmean(values_b[i] for i in idx_b)
        deltas.append(mean_a - mean_b)
    deltas.sort()
    lo_i = int(0.025 * n_resamples); hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    frac_negative = sum(1 for d in deltas if d < 0) / n_resamples
    return {"point_delta": point_delta, "ci_low": deltas[lo_i], "ci_high": deltas[hi_i],
            "frac_negative": frac_negative, "n_resamples": n_resamples,
            "n_games_a": len(game_ids_a), "n_games_b": len(game_ids_b)}


def build_prop_examples(prop: str, ctx: mp.ContextMarginalContext, seasons: list[int]) -> dict[int, list[dict]]:
    cfg = PROP_CONFIG[prop]
    field = cfg["field"]
    engine = getattr(ctx, prop)
    rows = engine.rows
    index = engine.index

    out = {s: [] for s in seasons}
    for r in rows:
        if r["season"] not in seasons:
            continue
        player_id, team, opponent, date = r["player_id"], r["team"], r["opponent"], r["game_date"]
        history = index.history_as_of(player_id, date)
        if len(history) < cfg["min_history"]:
            continue
        baseline_rate = pf.rolling_mean(history, field, 20)
        recent_rate = pf.rolling_mean(history, field, 5)
        baseline_toi = pf.rolling_mean(history, "icetime_seconds", 20)
        recent_toi = pf.rolling_mean(history, "icetime_seconds", 10)
        if baseline_rate is None or baseline_rate <= 0:
            continue
        form_ratio = cs.form_log_ratio(recent_rate, baseline_rate)
        toi_ratio = cs.toi_log_ratio(recent_toi, baseline_toi)

        pred = ctx.predict(prop, player_id, team, opponent, date, r["season"])
        if pred is None:
            continue
        prob_1plus = pred["probs"].get(1)
        if prob_1plus is None:
            continue
        actual = r[field]
        actual_1plus = 1.0 if actual >= 1 else 0.0

        arena = ae.game_arena(team, opponent, r["home_or_away"])
        out[r["season"]].append({
            "game_id": r["game_id"], "game_date": date, "player_id": player_id, "team": team,
            "opponent": opponent, "arena": arena, "home_or_away": r["home_or_away"],
            "form_ratio": form_ratio, "toi_ratio": toi_ratio,
            "baseline_rate": baseline_rate, "mu": pred.get("mu"),
            "prob_1plus": prob_1plus, "actual": actual, "actual_1plus": actual_1plus,
            "prob_residual": actual_1plus - prob_1plus,
            "count_residual": (actual - pred["mu"]) if pred.get("mu") is not None else None,
        })
    return out


if __name__ == "__main__":
    print("Loading frozen marginal engines (SOG, Goals, Assists, Points, Blocks)...")
    ctx = mp.ContextMarginalContext()

    all_results = {}
    freeze_manifest_per_prop = {}

    for prop in PROP_CONFIG:
        print(f"\n=== {prop.upper()} ===")
        examples_by_season = build_prop_examples(prop, ctx, [TUNING_SEASON] + EVAL_SEASONS)
        for s, exs in examples_by_season.items():
            print(f"  season {s}: {len(exs)} eligible examples")

        tuning_examples = examples_by_season[TUNING_SEASON]
        tuning_form_ratios = [e["form_ratio"] for e in tuning_examples]
        thresholds = cs.StateThresholds(tuning_form_ratios, pct=0.20)
        tuning_toi_ratios = sorted(v for v in (e["toi_ratio"] for e in tuning_examples) if v is not None)
        toi_decline_cutoff = tuning_toi_ratios[int(len(tuning_toi_ratios) * 0.20)] if tuning_toi_ratios else -0.3
        print(f"  cold_cutoff={thresholds.cold_cutoff:.4f} hot_cutoff={thresholds.hot_cutoff:.4f} "
              f"toi_decline_cutoff={toi_decline_cutoff:.4f}")

        for s in [TUNING_SEASON] + EVAL_SEASONS:
            for e in examples_by_season[s]:
                e["state"] = thresholds.classify(e["form_ratio"])
                e["multi_state"] = cs.classify_multi_signal(e["state"], e["toi_ratio"], toi_decline_cutoff)

        season_blocks = {}
        for s in EVAL_SEASONS:
            exs = examples_by_season[s]
            by_state = defaultdict(list)
            for e in exs:
                by_state[e["state"]].append(e)

            block = {"n": len(exs)}
            state_stats = {}
            for state_name in ("COLD", "NORMAL", "HOT"):
                group = by_state.get(state_name, [])
                state_stats[state_name] = {
                    "n": len(group),
                    "mean_prob_residual": cs.mean_or_none([e["prob_residual"] for e in group]),
                    "mean_count_residual": cs.mean_or_none([e["count_residual"] for e in group]),
                    "mean_actual_1plus_rate": cs.mean_or_none([e["actual_1plus"] for e in group]),
                    "mean_predicted_1plus_rate": cs.mean_or_none([e["prob_1plus"] for e in group]),
                }
            block["by_state"] = state_stats

            cold_group = by_state.get("COLD", [])
            normal_group = by_state.get("NORMAL", [])
            hot_group = by_state.get("HOT", [])
            if len(cold_group) >= MIN_STATE_SUPPORT and len(normal_group) >= MIN_STATE_SUPPORT:
                block["cold_vs_normal_bootstrap"] = game_clustered_bootstrap_diff(
                    cold_group, normal_group,
                    [e["prob_residual"] for e in cold_group], [e["prob_residual"] for e in normal_group])
            else:
                block["cold_vs_normal_bootstrap"] = "INSUFFICIENT_DATA"
            if len(hot_group) >= MIN_STATE_SUPPORT and len(normal_group) >= MIN_STATE_SUPPORT:
                block["hot_vs_normal_bootstrap"] = game_clustered_bootstrap_diff(
                    hot_group, normal_group,
                    [e["prob_residual"] for e in hot_group], [e["prob_residual"] for e in normal_group])
            else:
                block["hot_vs_normal_bootstrap"] = "INSUFFICIENT_DATA"

            # Part 24: regression-to-mean check -- for COLD examples, does actual_1plus
            # at T come back toward (or above) the player's own pre-state baseline rate?
            if cold_group:
                cold_baseline_implied_rate = statistics.fmean(
                    [1 - math.exp(-e["baseline_rate"]) if e["baseline_rate"] < 5 else 1.0 for e in cold_group])
                cold_actual_rate_at_t = statistics.fmean([e["actual_1plus"] for e in cold_group])
                block["regression_to_mean_check"] = {
                    "cold_baseline_implied_1plus_rate": cold_baseline_implied_rate,
                    "cold_actual_1plus_rate_at_target_game": cold_actual_rate_at_t,
                    "rebounded_to_or_above_baseline": cold_actual_rate_at_t >= cold_baseline_implied_rate,
                }
            else:
                block["regression_to_mean_check"] = "INSUFFICIENT_DATA"

            # Part 26: role-change confounding -- split COLD into TOI-stable vs TOI-declining
            cold_toi_stable = [e for e in cold_group if e["toi_ratio"] is not None
                                and e["toi_ratio"] > toi_decline_cutoff]
            cold_toi_declining = [e for e in cold_group if e["toi_ratio"] is not None
                                   and e["toi_ratio"] <= toi_decline_cutoff]
            block["role_change_confounding"] = {
                "cold_toi_stable_n": len(cold_toi_stable),
                "cold_toi_stable_mean_prob_residual": cs.mean_or_none([e["prob_residual"] for e in cold_toi_stable]),
                "cold_toi_declining_n": len(cold_toi_declining),
                "cold_toi_declining_mean_prob_residual": cs.mean_or_none(
                    [e["prob_residual"] for e in cold_toi_declining]),
            }

            # Part 4/13: multi-signal state
            multi_group = [e for e in exs if e["multi_state"] == "COLD_AND_TOI_DECLINE"]
            block["multi_signal"] = {
                "n": len(multi_group),
                "mean_prob_residual": cs.mean_or_none([e["prob_residual"] for e in multi_group]),
            }
            if len(multi_group) >= MIN_STATE_SUPPORT and len(normal_group) >= MIN_STATE_SUPPORT:
                block["multi_signal_vs_normal_bootstrap"] = game_clustered_bootstrap_diff(
                    multi_group, normal_group,
                    [e["prob_residual"] for e in multi_group], [e["prob_residual"] for e in normal_group])
            else:
                block["multi_signal_vs_normal_bootstrap"] = "INSUFFICIENT_DATA"

            season_blocks[s] = block
            print(f"  {s}: COLD n={len(cold_group)} HOT n={len(hot_group)} NORMAL n={len(normal_group)} "
                  f"COLD_resid={state_stats['COLD']['mean_prob_residual']} "
                  f"NORMAL_resid={state_stats['NORMAL']['mean_prob_residual']}")

        # ---- Arena effects (descriptive, TUNING-fit, EVAL-checked) ----
        print(f"  Fitting arena effects for {prop}...")
        tuning_with_residual = [
            {"player_id": e["player_id"], "arena": e["arena"], "residual": e["prob_residual"]}
            for e in tuning_examples
        ]
        arena_rates = ae.ArenaRates(tuning_with_residual, k_arena=300, k_player_arena=20)
        arena_range = (max(arena_rates.arena_raw_mean.values()) - min(arena_rates.arena_raw_mean.values())) \
            if arena_rates.arena_raw_mean else None

        eval_pairs = []
        for s in EVAL_SEASONS:
            for e in examples_by_season[s]:
                shrunk = arena_rates.player_arena_shrunk_residual(e["player_id"], e["arena"])
                eval_pairs.append((shrunk, e["prob_residual"]))
        arena_generalization_corr = None
        if len(eval_pairs) >= 30:
            xs = [p[0] for p in eval_pairs]
            ys = [p[1] for p in eval_pairs]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
            sy = math.sqrt(sum((y - my) ** 2 for y in ys))
            arena_generalization_corr = cov / (sx * sy) if sx > 0 and sy > 0 else None

        arena_block = {
            "n_arenas": len(arena_rates.arena_n), "arena_raw_mean_range": arena_range,
            "league_mean_residual": arena_rates.league_mean_residual,
            "eval_generalization_corr": arena_generalization_corr,
            "eval_n_pairs": len(eval_pairs),
        }
        print(f"  arena_range={arena_range} eval_generalization_corr={arena_generalization_corr}")

        all_results[prop] = {
            "cold_cutoff": thresholds.cold_cutoff, "hot_cutoff": thresholds.hot_cutoff,
            "toi_decline_cutoff": toi_decline_cutoff,
            "examples_by_season_n": {s: len(examples_by_season[s]) for s in examples_by_season},
            "by_season": season_blocks,
            "arena_effects": arena_block,
        }

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    freeze_manifest = {
        "experiment_id": "player_context_state_v1",
        "cold_state_definition": "recent-5 actual rate vs baseline-20 actual rate, log-ratio, bottom 20% "
                                  "(TUNING-fit) -- expectation-relative, not raw production (Part 3)",
        "hot_state_definition": "top 20% of the same TUNING-fit form-ratio distribution -- symmetric control "
                                 "(Part 5), not assumed to create OVER value",
        "multi_signal_state": "COLD_AND_TOI_DECLINE requires BOTH the COLD production state AND a TOI "
                               "log-ratio at or below the TUNING-fit 20th percentile (Part 4)",
        "media_sentiment_component": "NOT BUILT -- no legitimate, timestamped, legally-accessible historical "
                                      "media/news corpus exists in this project (verified directly: no media/"
                                      "news/sentiment files anywhere in the repository). Fabricating one would "
                                      "violate this project's core data-integrity principle. Disclosed as a "
                                      "first-class finding, not a silent omission -- see report Section E/L/U.",
        "arena_effect_methodology": "arena = home-team identity of the game (real, disclosed proxy -- no "
                                     "separate venue dataset exists). PLAYER-ARENA -> ARENA -> 0 hierarchical "
                                     "shrinkage (k_arena=300, k_player_arena=20), TUNING-fit, EVAL-checked via "
                                     "correlation with real held-out residuals (descriptive, not a formal "
                                     "walk-forward prediction -- Part 15/16's framing is measurement, not a "
                                     "per-game predictive claim)",
        "sample_floor": MIN_STATE_SUPPORT,
        "prop_families": list(PROP_CONFIG.keys()),
        "under_direction_hypothesis": "NEGATIVE_CONTEXT -> MODEL OVERPREDICTION (mean prob_residual < 0 for "
                                       "COLD relative to NORMAL) -- tested, not assumed; Part 24's competing "
                                       "regression-to-mean hypothesis tested explicitly and not suppressed",
        "code_hashes": {
            "run_player_context_state_model.py": _sha(str(REPO_ROOT / "research" / "run_player_context_state_model.py")),
            "player_context_state/context_state.py": _sha(
                str(REPO_ROOT / "research" / "player_context_state" / "context_state.py")),
            "player_context_state/arena_effects.py": _sha(
                str(REPO_ROOT / "research" / "player_context_state" / "arena_effects.py")),
            "player_context_state/marginal_provenance.py": _sha(
                str(REPO_ROOT / "research" / "player_context_state" / "marginal_provenance.py")),
        },
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    full_results = {
        "config": {"warmup_season": WARMUP_SEASON, "tuning_season": TUNING_SEASON, "eval_seasons": EVAL_SEASONS,
                   "min_state_support": MIN_STATE_SUPPORT},
        "props": all_results,
        "freeze_manifest": freeze_manifest,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2, default=str)
    print("\nWrote", RESULTS_PATH)
