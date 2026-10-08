"""
Moneyline model comparison, versioned `moneyline-compare-v1`: the validated strength model (`goalie-team-v1`) against the Elo path
that prices tickets today (`moneyline-t35-v1`, the unmodified production Elo update) and a home-rate baseline.

Why this exists: the Model Health page validates the strength model, while the ticket path still uses Elo. Promoting a model because
it is validated is only honest if it is also compared with the model it would replace on the same games. This is that comparison.

Design (fixed before looking at any result):
  * rolling-origin, chronological: fold A fits the strength model's logistic on 2023 and scores 2024; fold B fits on 2024 and scores
    2025. Each fold is scored once. Nothing is tuned: the Elo constants are production's (config.py), the strength model's state
    parameters are goalie-team-v1's, and only the two logistic coefficients are fitted, on the season before the scored one.
  * the same games for every candidate, paired by game id;
  * two outcome definitions: regulation/overtime decisions only (the definition the strength model was validated on), and ALL
    games including shootouts (what a moneyline actually settles on);
  * paired bootstrap on per-game log loss.
What it cannot show: whether either model beats the MARKET. There are no historical closing moneylines in this corpus, so no edge
claim is made here; a lower log loss than another model is not an edge against a sportsbook price.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from research import elo_comparison as ec

from . import history, team_goalie as tg

OUT_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "validation" / "moneyline_model_comparison.json"
CORPUS = Path(__file__).resolve().parent.parent / "real_nhl_results" / "normalized_regular_season_games.jsonl"
VERSION = "moneyline-compare-v1"
FOLDS = ((2023, 2024), (2024, 2025))          # (fit season, scored season)


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def _score(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    return {"n": n, "log_loss": round(sum(_ll(p, y) for p, y in pairs) / n, 5), "brier": round(sum((p - y) ** 2 for p, y in pairs) / n, 5),
            "mean_pred": round(sum(p for p, _ in pairs) / n, 4), "home_win_rate": round(sum(y for _, y in pairs) / n, 4)}


def _season_year(label: int) -> int:
    return int(str(label)[:4])


def run() -> dict:
    games = ec.load_corpus(str(CORPUS))
    elo_records, _ = ec.run_walkforward(games)                       # production Elo, unmodified, strict prior-date walk
    elo_p = {r["game_id"]: r["p_home"] for r in elo_records}
    winner = {g["game_id"]: int(g["home_score"] > g["away_score"]) for g in games}
    _grec, gmrec, _state = tg.walk(history.goalie_games(), (2023, 2024, 2025))
    by_id = {int(r["game_id"]): r for r in gmrec}
    folds = []
    for fit_season, score_season in FOLDS:
        fit = [r for r in gmrec if r["season"] == fit_season and r["home_win"] is not None]
        coef = tg.fit_logistic([([1.0, r["strength_diff"]], r["home_win"]) for r in fit])
        home_rate = sum(r["home_win"] for r in fit) / len(fit)
        scored = [r for r in gmrec if r["season"] == score_season and int(r["game_id"]) in elo_p]
        out = {"fit_season": fit_season, "scored_season": score_season, "strength_coefficients": [round(c, 4) for c in coef],
               "fit_home_rate": round(home_rate, 4)}
        for label, only_decided in (("decided_in_play_only", True), ("all_games_incl_shootouts", False)):
            rows = [r for r in scored if (r["home_win"] is not None or not only_decided)]
            y = [r["home_win"] if (only_decided or r["home_win"] is not None) else winner[int(r["game_id"])] for r in rows]
            cand = {"home_rate_baseline": [home_rate] * len(rows),
                    "elo_production": [elo_p[int(r["game_id"])] for r in rows],
                    "strength_model": [tg._sigmoid(coef[0] + coef[1] * r["strength_diff"]) for r in rows]}
            scores = {k: _score(list(zip(v, y))) for k, v in cand.items()}
            ll = {k: [_ll(p, yy) for p, yy in zip(v, y)] for k, v in cand.items()}
            out[label] = {"scores": scores,
                          "strength_vs_elo_log_loss_delta": ec.paired_bootstrap_delta(ll["elo_production"], ll["strength_model"]),
                          "strength_vs_home_rate_log_loss_delta": ec.paired_bootstrap_delta(ll["home_rate_baseline"], ll["strength_model"]),
                          "elo_vs_home_rate_log_loss_delta": ec.paired_bootstrap_delta(ll["home_rate_baseline"], ll["elo_production"])}
        folds.append(out)
    return {"version": VERSION, "design": __doc__.strip().split("\n\n")[1], "folds": folds,
            "limits": ["No historical sportsbook prices are available, so neither model is shown to beat the market.",
                       "Elo here is the unmodified production update rule; the ticket path additionally subtracts a conservative band that "
                       "is a policy margin, not a calibration."]}


def main() -> dict:
    report = run()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=1, sort_keys=True, default=float))
    return report


if __name__ == "__main__":
    r = main()
    for f in r["folds"]:
        print(f["scored_season"])
        for k in ("decided_in_play_only", "all_games_incl_shootouts"):
            print(" ", k, {n: (s["log_loss"], s["n"]) for n, s in f[k]["scores"].items()})
            d = f[k]["strength_vs_elo_log_loss_delta"]
            print("   strength-elo delta", round(d["point_delta"], 5), "CI", round(d["ci_low"], 5), round(d["ci_high"], 5), "P(improves)", d["frac_resamples_improved"])
