"""
Walk-forward backtest + baseline comparison. Spec sec.44 (evaluate far more
than win rate), sec.46 (calibration), sec.52 (walk-forward, never a random
split), and this slice's explicit ask: home-rate / Elo-only / Elo+player /
Elo+goalie / combined baselines, so the combined model's calibration can be
judged against something simpler, not presented in isolation.

IMPORTANT: this runs against SYNTHETIC data. A calibration number here is
evidence the PIPELINE'S MATH is sound (probabilities track a known,
held-out ground truth) — it is NOT evidence of a profitable real-world
betting edge. That requires real NHL data and real DraftKings closing
lines, neither of which this scaffold has yet. Don't present it as such.
"""
from __future__ import annotations

import math

import config
import db
from models.combined_model import CombinedMoneylineModel, compute_probability_from_features


def brier_score(pairs) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs) -> float:
    eps = 1e-6
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(pairs)


def calibration_table(pairs, n_buckets: int = 10) -> list[tuple[str, int, float, float]]:
    buckets = [[] for _ in range(n_buckets)]
    for p, y in pairs:
        idx = min(int(p * n_buckets), n_buckets - 1)
        buckets[idx].append((p, y))
    rows = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        avg_pred = sum(x[0] for x in b) / len(b)
        actual_rate = sum(x[1] for x in b) / len(b)
        rows.append((f"{lo:.0%}-{hi:.0%}", len(b), avg_pred, actual_rate))
    return rows


def _zeroed(fs: dict, *keys: str) -> dict:
    out = dict(fs)
    for k in keys:
        out[k] = 0.0
    return out


def run(conn) -> dict:
    # v2.1.2 (spec item 2): the model universe is DB-derived
    # (db.team_ids), never ingest.demo_data.TEAMS -- so this production
    # backtest path works correctly against a real NHL database, not only
    # the synthetic 12-team demo league. See
    # tests/test_dynamic_team_universe.py.
    model = CombinedMoneylineModel(db.team_ids(conn))
    game_ids = CombinedMoneylineModel.all_final_game_ids(conn)
    predictions = model.process_games(conn, game_ids, learn=True, store_predictions=True)

    outcomes = [1 if p.home_score > p.away_score else 0 for p in predictions]

    # home-rate baseline: a simple running average of home-win-rate seen so
    # far this walk (starts at a neutral 0.5 prior, updates after each
    # game) — the lowest bar any real model needs to clear.
    home_rate_probs = []
    wins_seen, games_seen = 0, 0
    for y in outcomes:
        prior_rate = (wins_seen / games_seen) if games_seen else 0.5
        home_rate_probs.append(prior_rate)
        wins_seen += y
        games_seen += 1

    elo_only = [compute_probability_from_features(_zeroed(
        p.feature_snapshot, "player_quality_home", "player_quality_away",
        "goalie_adj_home", "goalie_adj_away", "rest_adj_home", "rest_adj_away"))["model_prob_home"]
        for p in predictions]
    elo_plus_player = [compute_probability_from_features(_zeroed(
        p.feature_snapshot, "goalie_adj_home", "goalie_adj_away",
        "rest_adj_home", "rest_adj_away"))["model_prob_home"] for p in predictions]
    elo_plus_goalie = [compute_probability_from_features(_zeroed(
        p.feature_snapshot, "player_quality_home", "player_quality_away",
        "rest_adj_home", "rest_adj_away"))["model_prob_home"] for p in predictions]
    combined = [p.model_prob_home for p in predictions]

    results = {}
    for name, probs in (
        ("home_rate_baseline", home_rate_probs),
        ("elo_only", elo_only),
        ("elo_plus_player", elo_plus_player),
        ("elo_plus_goalie", elo_plus_goalie),
        ("combined_model", combined),
    ):
        pairs = list(zip(probs, outcomes))
        results[name] = {
            "brier": brier_score(pairs),
            "log_loss": log_loss(pairs),
            "calibration": calibration_table(pairs),
        }
    results["n_games"] = len(predictions)
    results["home_win_rate"] = sum(outcomes) / len(outcomes)
    return results


def print_report(results: dict) -> None:
    print(f"Walk-forward backtest over {results['n_games']} games "
          f"(predict strictly before each game's own result)\n")
    print(f"Overall home win rate in data: {results['home_win_rate']:.1%}\n")
    for name in ("home_rate_baseline", "elo_only", "elo_plus_player", "elo_plus_goalie",
                 "combined_model"):
        r = results[name]
        print(f"--- {name} ---")
        print(f"  Brier score: {r['brier']:.4f}  (0.25 = coin flip)")
        print(f"  Log loss:    {r['log_loss']:.4f}  (0.6931 = coin flip)")
        print(f"  {'bucket':>10} {'n':>5} {'avg pred':>9} {'actual':>7}")
        for bucket, n, avg_pred, actual in r["calibration"]:
            print(f"  {bucket:>10} {n:>5} {avg_pred:>8.1%} {actual:>7.1%}")
        print()
    print("NOTE: synthetic-data calibration is evidence the pipeline's math is")
    print("sound, NOT evidence of a profitable real-world betting edge. ROI and")
    print("closing-line value require real NHL data and real DraftKings closing")
    print("lines — see README's implemented/tested/experimental/deferred table.")


if __name__ == "__main__":
    conn = db.get_conn()
    print_report(run(conn))
