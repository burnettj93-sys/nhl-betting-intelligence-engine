"""
View logic for the Goalie Quality x Starter-Probability Integration
experiment panel on the Goalie Intelligence dashboard page (Part 29 of
GOALIE_QUALITY_INTEGRATION_REPORT.md).

STATUS: RESEARCH -- NOT PRODUCTION. Every probability this module
computes is a RESEARCH re-derivation for inspection purposes only. It
NEVER touches, replaces, or is read by the production win-probability
display -- that remains driven entirely by models/ and pricing/, which
this module never imports. See the report's Section AD ("production
model unchanged").

Only the PROJECTED starter-probability distribution is ever used to
build the headline goalie-aware probability shown here -- the real
historical actual starter (available in the underlying corpus) is
intentionally never read by this module at all, so there is no code
path here that could accidentally leak it into a pregame-looking
number (mirrors goalie_view.py's own rule).
"""
from __future__ import annotations

from pathlib import Path

from dashboard.data_access import load_json_safely
from research.goalie_intelligence import features as gf
from research.goalie_intelligence import model as gm
from research.goalie_intelligence import quality as gq
from research import goalie_quality_integration as gqi
from research.run_goalie_quality_comparison import ELO_POINTS_TO_LOGIT

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "goalie_quality_integration_results.json"


def load_results() -> dict | None:
    return load_json_safely(RESULTS_PATH)


def _side_projection(all_starter_rows: list[dict], quality_rows: list[dict], weights: list[float],
                      team: str, game_date: str, season: int) -> dict | None:
    history = gf.team_history_as_of(all_starter_rows, team, game_date)
    if not history:
        return None
    candidates = gf.eligible_goalies(history, window=20)
    if not candidates:
        return None
    is_b2b = gf.team_back_to_back(history, game_date)
    feature_vectors = [gm.build_feature_vector(history, g, season, game_date, is_b2b) for g in candidates]
    probs = gm.score_candidates(weights, feature_vectors)

    rows = []
    for goalie_id, prob in zip(candidates, probs):
        quality_history = gq.goalie_history_as_of(quality_rows, goalie_id, game_date)
        delta_elo, shots_a = gq.shrunk_save_pct_production(quality_history)
        rows.append({
            "goalie_id": goalie_id, "probability": prob,
            "save_pct_quality_elo_delta": delta_elo, "save_pct_quality_sample_shots": shots_a,
            "quality_history": quality_history,
        })
    return {"candidates": rows, "is_back_to_back": is_b2b, "history_games": len(history)}


def compute_matchup_quality_view(all_starter_rows: list[dict], quality_rows: list[dict], weights: list[float],
                                  home_team: str, away_team: str, game_date: str, season: int,
                                  p_baseline: float, quality_results: dict) -> dict:
    """Builds the RESEARCH-only goalie-quality panel for one real
    historical game: projected starter distribution + BOTH quality
    candidate metrics for every candidate goalie on both sides, then the
    scenario-weighted (Sigma_h Sigma_a P(h) x P(a) x P(win|h,a)) mixture
    probability for each candidate -- the same formula and the SAME
    fitted GSAx-style scale (window/beta/mean/stdev) used by
    research/run_goalie_quality_comparison.py's headline evaluation, read
    from research/goalie_quality_integration_results.json rather than
    refit here."""
    home = _side_projection(all_starter_rows, quality_rows, weights, home_team, game_date, season)
    away = _side_projection(all_starter_rows, quality_rows, weights, away_team, game_date, season)
    if home is None or away is None:
        return {"status": "INSUFFICIENT_HISTORY"}

    sel_window_str = quality_results["gsax_selected_window"]
    spec = quality_results["gsax_window_selection"][sel_window_str]
    beta, mean, stdev = spec["beta"], spec["mean"], spec["stdev"]
    window = None if sel_window_str == "None" else int(sel_window_str)
    stdev = stdev if stdev else 1.0

    def gsax_adj(quality_history: list[dict]) -> tuple[float, float]:
        raw, shots = gq.rolling_gsax_per60(quality_history, window)
        raw = raw if raw is not None else 0.0
        return beta * (raw - mean) / stdev, shots

    for row in home["candidates"] + away["candidates"]:
        adj, shots = gsax_adj(row["quality_history"])
        row["gsax_quality_adj_logit"] = adj
        row["gsax_quality_sample_shots"] = shots
        row["save_pct_quality_adj_logit"] = row["save_pct_quality_elo_delta"] * ELO_POINTS_TO_LOGIT

    home_pairs_a = [(r["probability"], r["save_pct_quality_adj_logit"]) for r in home["candidates"]]
    away_pairs_a = [(r["probability"], r["save_pct_quality_adj_logit"]) for r in away["candidates"]]
    home_pairs_b = [(r["probability"], r["gsax_quality_adj_logit"]) for r in home["candidates"]]
    away_pairs_b = [(r["probability"], r["gsax_quality_adj_logit"]) for r in away["candidates"]]

    p_mix_a = gqi.scenario_weighted_probability(p_baseline, home_pairs_a, away_pairs_a)
    p_mix_b = gqi.scenario_weighted_probability(p_baseline, home_pairs_b, away_pairs_b)

    top_home = max(home["candidates"], key=lambda r: r["probability"])
    top_away = max(away["candidates"], key=lambda r: r["probability"])
    p_top1_a = gqi.top1_probability(p_baseline,
                                     (top_home["probability"], top_home["save_pct_quality_adj_logit"]),
                                     (top_away["probability"], top_away["save_pct_quality_adj_logit"]))

    for side in (home, away):
        for row in side["candidates"]:
            row.pop("quality_history", None)

    return {
        "status": "RESEARCH_PROJECTED", "home": home, "away": away,
        "p_baseline": p_baseline, "p_mix_a_save_pct_quality": p_mix_a, "p_mix_b_gsax_quality": p_mix_b,
        "p_top1_a_save_pct_quality": p_top1_a, "gsax_window_used": sel_window_str,
    }
