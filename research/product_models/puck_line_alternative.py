"""
Puck line (home -1.5 covers = wins by 2+ goals; away +1.5 is the complement): an alternative to the failed Skellam margin model,
versioned `puck-line-direct-v1`.

Record to keep intact: `goalie-team-v1`'s Skellam margin model did NOT beat the base rate on 2025-26 (log loss 0.52926 vs 0.52751; it
over-predicts home -1.5 covers, mean 29.7% against 21.7% observed). That result stands in docs/validation/goalie_team_validation.json.

Alternative: model the cover event directly -- a logistic on [1, strength_diff] -- instead of building it from a Poisson goal model,
because the Poisson shape ignores that overtime and shootouts truncate margins at one goal, which is exactly where the Skellam model
over-predicted.

Evaluation discipline (fixed before any result):
  * the 2025-26 season was already looked at once, by the Skellam attempt, so it is NOT used again to judge or tune this model;
  * development folds only use earlier seasons: fold A fits on 2022 and scores 2023, fold B fits on 2023 and scores 2024. Each fold is
    scored once, with no tuning and a single declared feature;
  * the model's parameters are then frozen (fit through 2025, recorded below) and the untouched evaluation set is the 2026-27 season as
    it is played: `shadow_log` appends each game's prediction before it starts, `prospective_score` scores finished games. No
    verdict is claimed before MIN_PROSPECTIVE_GAMES finished games.
Even a good result here does not make a puck-line ticket possible: there are no puck-line prices (the `spreads` market is not
requested; adding it to the moneyline call costs one more credit per call) and no ledger/settlement mapping for the leg.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from operational import state_paths

from . import history, team_goalie as tg

VERSION = "puck-line-direct-v1"
OUT_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "validation" / "puck_line_alternative.json"
FOLDS = ((2022, 2023), (2023, 2024))
MIN_PROSPECTIVE_GAMES = 300
LOG_NAME = "puck_line_shadow.jsonl"


def _covers(g: dict) -> int:
    return int(g["home_goals"] - g["away_goals"] >= 2)


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def _score(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    return {"n": n, "log_loss": round(sum(_ll(p, y) for p, y in pairs) / n, 5), "brier": round(sum((p - y) ** 2 for p, y in pairs) / n, 5),
            "mean_pred": round(sum(p for p, _ in pairs) / n, 4), "observed": round(sum(y for _, y in pairs) / n, 4)}


def fit(games: list[dict]) -> list[float]:
    return tg.fit_logistic([([1.0, g["strength_diff"]], _covers(g)) for g in games])


def probability(coef: list[float], strength_diff: float) -> float:
    return tg._sigmoid(coef[0] + coef[1] * strength_diff)


def development_report(gmrec: list[dict]) -> dict:
    folds = []
    for fit_season, score_season in FOLDS:
        train = [g for g in gmrec if g["season"] == fit_season]
        test = [g for g in gmrec if g["season"] == score_season]
        coef = fit(train)
        base = sum(_covers(g) for g in train) / len(train)
        margin = tg.fit_margin(train)
        y = [_covers(g) for g in test]
        cand = {"base_rate": [base] * len(test), "direct_logistic": [probability(coef, g["strength_diff"]) for g in test],
                "skellam": [tg.skellam_tail(margin["b0"] + margin["b1"] * g["strength_diff"], margin["total"], 2) for g in test]}
        scores = {k: _score(list(zip(v, y))) for k, v in cand.items()}
        from research import elo_comparison as ec
        ll = {k: [_ll(p, yy) for p, yy in zip(v, y)] for k, v in cand.items()}
        folds.append({"fit_season": fit_season, "scored_season": score_season, "coefficients": [round(c, 4) for c in coef], "scores": scores,
                      "direct_vs_base_rate_log_loss_delta": ec.paired_bootstrap_delta(ll["base_rate"], ll["direct_logistic"]),
                      "direct_vs_skellam_log_loss_delta": ec.paired_bootstrap_delta(ll["skellam"], ll["direct_logistic"])})
    return {"folds": folds}


def frozen_parameters(gmrec: list[dict]) -> dict:
    """Fit through the 2025-26 season; used only for the prospective 2026-27 log, never to judge itself."""
    train = [g for g in gmrec if g["season"] <= 2025]
    return {"coefficients": [round(c, 5) for c in fit(train)], "fit_games": len(train), "fit_through_season": 2025}


def run() -> dict:
    _g, gmrec, _s = tg.walk(history.goalie_games(), (2022, 2023, 2024, 2025))
    dev = development_report(gmrec)
    supports = all(f["scores"]["direct_logistic"]["log_loss"] < f["scores"]["base_rate"]["log_loss"] for f in dev["folds"])
    significant = all(f["direct_vs_base_rate_log_loss_delta"]["ci_high"] < 0 for f in dev["folds"])
    verdict = ("DEVELOPMENT_SUPPORTS_PROSPECTIVE_TEST" if (supports and significant) else
               "DEVELOPMENT_INCONCLUSIVE" if supports else "DEVELOPMENT_DOES_NOT_SUPPORT")
    return {"version": VERSION, "record_kept": "goalie-team-v1 Skellam margin model failed on 2025-26 (see goalie_team_validation.json)",
            "development": dev, "development_verdict": verdict, "frozen": frozen_parameters(gmrec),
            "prospective": {"evaluation_set": "2026-27 regular season as played", "min_games": MIN_PROSPECTIVE_GAMES, "status": "COLLECTING"},
            "not_enabled": ["No puck-line prices are requested.", "No ledger or settlement mapping exists for a puck-line leg.",
                            "No verdict is claimed before the prospective set has enough finished games."]}


def main() -> dict:
    report = run()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=1, sort_keys=True, default=float))
    return report


# ---- prospective evaluation set -------------------------------------------------------------------

def shadow_log(live: dict, scheduled: list[dict], now_iso: str) -> int:
    """Append one prediction per scheduled game that is not yet logged (called with the live strength state before puck drop)."""
    frozen = json.loads(OUT_PATH.read_text())["frozen"]["coefficients"]
    p = state_paths.path(LOG_NAME)
    seen = set()
    if p.exists():
        seen = {json.loads(line)["game_id"] for line in p.read_text().splitlines() if line.strip()}
    added = 0
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for g in scheduled:
            if str(g["game_id"]) in seen:
                continue
            diff = tg.win_probability(live, g["home"], g["away"])["strength_diff_goals_per_game"]
            f.write(json.dumps({"game_id": str(g["game_id"]), "date": g["date_et"], "home": g["home"], "away": g["away"],
                                "p_home_covers": round(probability(frozen, diff), 5), "logged_at_utc": now_iso, "version": VERSION}) + "\n")
            added += 1
    return added


def prospective_score(conn) -> dict:
    p = state_paths.path(LOG_NAME)
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []
    scored = []
    for r in rows:
        g = conn.execute("SELECT game_state, home_score, away_score, final_period_type FROM games WHERE game_id = ?", (int(r["game_id"]),)).fetchone()
        if g is None or g["game_state"] != "FINAL" or g["home_score"] is None:
            continue
        margin = g["home_score"] - g["away_score"]
        if g["final_period_type"] in ("OT", "SO"):
            margin = max(min(margin, 1), -1)                      # official margin of an extra-time game is one goal
        scored.append((r["p_home_covers"], int(margin >= 2)))
    out = {"logged": len(rows), "scored": len(scored), "min_games": MIN_PROSPECTIVE_GAMES}
    if scored:
        base = sum(y for _, y in scored) / len(scored)
        out["log_loss"] = _score(scored)["log_loss"]
        out["base_rate_log_loss_in_sample"] = _score([(base, y) for _, y in scored])["log_loss"]
    out["verdict"] = "TOO_FEW_GAMES" if len(scored) < MIN_PROSPECTIVE_GAMES else "ENOUGH_GAMES_FOR_REVIEW"
    return out


if __name__ == "__main__":
    r = main()
    for f in r["development"]["folds"]:
        print(f["scored_season"], {k: (v["log_loss"], v["mean_pred"], v["observed"]) for k, v in f["scores"].items()})
        d = f["direct_vs_base_rate_log_loss_delta"]
        print("  direct-base delta", round(d["point_delta"], 5), "CI", round(d["ci_low"], 5), round(d["ci_high"], 5))
    print(r["development_verdict"], r["frozen"])
