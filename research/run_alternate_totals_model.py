"""
Alternate Game/Team Totals -- Production Hardening + Parlay Build block
(2026-09-29), Phase D. Uses ONLY the already-certified real_nhl_results corpus
(5,248 games, 2022-23..2025-26) -- no MoneyPuck file was needed for this
first pass (xG-context enrichment is a documented, disclosed follow-up, not
attempted here for time). Two candidates per target: an empirical
(shrunk-mean) baseline and a Poisson fit, evaluated walk-forward-style by
simply splitting into the same warmup/eval seasons this project uses
elsewhere (NOT a true PIT walk-forward re-fit per game -- a real, disclosed
simplification given this block's scope; see caveats in the printed output).
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec

CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"
EVAL_SEASONS = [20242025, 20252026]
GAME_TOTAL_THRESHOLDS = (3.5, 4.5, 5.5, 6.5, 7.5)
TEAM_TOTAL_THRESHOLDS = (1.5, 2.5, 3.5, 4.5)


def poisson_sf_at_least(t_half: float, mu: float, max_k: int = 25) -> float:
    """t_half is a .5 sportsbook line -- P(count >= ceil(t_half))."""
    k0 = math.ceil(t_half)
    cdf = 0.0
    p = math.exp(-mu)
    for k in range(k0):
        cdf += p
        p *= mu / (k + 1) if k + 1 <= max_k else 0
    return max(0.0, 1.0 - cdf)


def brier(probs, actuals):
    return statistics.fmean((p - a) ** 2 for p, a in zip(probs, actuals))


def evaluate(games: list[dict], league_mean_total: float, league_mean_team: float) -> dict:
    out = {"n": len(games), "game_total": {}, "team_total_home": {}}
    totals = [g["home_score"] + g["away_score"] for g in games]
    for t in GAME_TOTAL_THRESHOLDS:
        actual = [1.0 if x >= math.ceil(t) else 0.0 for x in totals]
        p_baseline = [statistics.fmean([1.0 if x >= math.ceil(t) else 0.0 for x in totals])] * len(totals)
        p_poisson = [poisson_sf_at_least(t, league_mean_total) for _ in totals]
        n_events = int(sum(actual))
        bs = ec.paired_bootstrap_delta([(b - a) ** 2 for b, a in zip(p_baseline, actual)],
                                        [(p - a) ** 2 for p, a in zip(p_poisson, actual)])
        out["game_total"][f"O{t}"] = {
            "n_events": n_events, "event_rate": round(n_events / len(totals), 4),
            "empirical_brier": round(brier(p_baseline, actual), 5), "poisson_brier": round(brier(p_poisson, actual), 5),
            "frac_resamples_poisson_improved": round(bs["frac_resamples_improved"], 4),
        }
    home_totals = [g["home_score"] for g in games]
    for t in TEAM_TOTAL_THRESHOLDS:
        actual = [1.0 if x >= math.ceil(t) else 0.0 for x in home_totals]
        n_events = int(sum(actual))
        p_baseline = [statistics.fmean(actual)] * len(actual)
        p_poisson = [poisson_sf_at_least(t, league_mean_team) for _ in actual]
        bs = ec.paired_bootstrap_delta([(b - a) ** 2 for b, a in zip(p_baseline, actual)],
                                        [(p - a) ** 2 for p, a in zip(p_poisson, actual)])
        out["team_total_home"][f"O{t}"] = {
            "n_events": n_events, "event_rate": round(n_events / len(actual), 4),
            "empirical_brier": round(brier(p_baseline, actual), 5), "poisson_brier": round(brier(p_poisson, actual), 5),
            "frac_resamples_poisson_improved": round(bs["frac_resamples_improved"], 4),
        }
    return out


def run_all() -> dict:
    games = ec.load_corpus(str(CORPUS_PATH))
    train = [g for g in games if g["season"] not in EVAL_SEASONS]
    league_mean_total = statistics.fmean(g["home_score"] + g["away_score"] for g in train)
    league_mean_team = statistics.fmean(g["home_score"] for g in train)  # home only, disclosed asymmetry (home-ice)
    result = {"corpus_n": len(games), "train_n": len(train), "league_mean_total_goals_train": round(league_mean_total, 3),
              "league_mean_home_goals_train": round(league_mean_team, 3), "by_season": {},
              "caveat": "Single league-wide Poisson mean fit ONCE on warmup seasons only (not re-fit per "
                        "game-date, not team-specific, no xG context) -- a real, disclosed simplification "
                        "given this block's scope, NOT the same rigor as the SOG/Blocks/Saves/Team-SOG "
                        "walk-forward models elsewhere in this registry. Report this as PRELIMINARY, not "
                        "on par with those."}
    for season in EVAL_SEASONS:
        result["by_season"][str(season)] = evaluate([g for g in games if g["season"] == season],
                                                     league_mean_total, league_mean_team)
    return result


if __name__ == "__main__":
    result = run_all()
    out_path = REPO_ROOT / "research" / "alternate_totals_results.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"\nwrote {out_path}")
