"""
Game Detail business logic (Preseason Closing sprint, Track 2). Powers
the DEMO-mode game-intelligence view for a simulated matchup between two
real teams. The win-model probability reuses the REAL, unmodified Elo
logistic formula (models/elo_model.py::EloModel.win_probability) applied
to each team's REAL most-recent rating from the real historical corpus
-- the same "real math, simulated matchup" pattern established for
player props in the prior sprint, never a new team model.
"""
from __future__ import annotations

import config
from dashboard import data_access as da
from dashboard import demo_data as dd
from dashboard import player_intelligence_view as piv
from research.joint_scoring_dependence.joint_models import (
    gaussian_copula_joint_upper_tail, logical_control_probability,
)

_COMBO_SPECS = [
    ("sog", "goals", "SOG3_GOAL", "structural (Gaussian copula)", False),
    ("sog", "assists", "SOG3_ASSIST", "structural (Gaussian copula)", False),
    ("sog", "points", "SOG3_POINT", "structural (Gaussian copula)", False),
    ("goals", "points", None, "exact logical identity", True),
    ("assists", "points", None, "exact logical identity", True),
]


def find_demo_game(game_id: str):
    for g in dd.build_demo_games():
        if g.game_id == game_id:
            return g
    return None


def _latest_real_rating(team: str) -> float | None:
    try:
        predictions = da.compute_baseline_predictions()
    except da.DataAvailabilityError:
        return None
    team_games = sorted(
        [r for r in predictions if r["home_team"] == team or r["away_team"] == team],
        key=lambda r: r["game_date"])
    if not team_games:
        return None
    latest = team_games[-1]
    return latest["rating_home_pregame"] if latest["home_team"] == team else latest["rating_away_pregame"]


def demo_win_model(away: str, home: str) -> dict | None:
    """Real Elo ratings (as of the end of the real historical corpus),
    real logistic formula, applied to a SIMULATED future matchup."""
    home_r = _latest_real_rating(home)
    away_r = _latest_real_rating(away)
    if home_r is None or away_r is None:
        return None
    home_r_adj = home_r + config.ELO_HOME_ADVANTAGE
    home_win_p = 1.0 / (1.0 + 10 ** (-(home_r_adj - away_r) / 400.0))
    return {"home_win_p": home_win_p, "away_win_p": 1 - home_win_p,
            "home_rating": home_r, "away_rating": away_r}


def team_sog_projection(team: str, is_home: bool) -> dict | None:
    """Section 29: real Team SOG model, reused directly -- NOT
    reimplemented. Falls back to None (never fabricated) if the frozen
    engine or a real recent history window isn't available for this
    team in the demo context."""
    try:
        from dashboard.team_sog_view import TeamSogEngine, load_results
    except ImportError:
        return None
    results = load_results()
    if results is None:
        return None
    opponent = dd._opponent_for(team)
    if opponent is None:
        return None
    engine = TeamSogEngine(results)
    return engine.project(team, opponent, "home" if is_home else "away", 0,
                           dd.SIMULATED_DATE, dd.SIMULATED_SEASON)


def game_context_players(game_id: str) -> list[dict]:
    """Section 34: players on either roster in this game with an active
    context state (COLD_AND_TOI_DECLINE), for the compact CONTEXT ACTIVE
    section."""
    game = find_demo_game(game_id)
    if game is None:
        return []
    opps = [o for o in dd.build_demo_opportunities()
            if o["team"] in (game.away, game.home) and o["context_state"]]
    seen = set()
    out = []
    for o in opps:
        if o["player_id"] in seen:
            continue
        seen.add(o["player_id"])
        out.append(o)
    return out


def game_combinations(game_id: str, rho_by_name: dict, limit: int = 6) -> list[dict]:
    """Track 2: same-game combinations scoped to this game's two rosters,
    reusing the exact COMBO_SPECS / joint-model logic from the standalone
    Combinations page (dashboard/pages/28_Combinations.py) -- never a
    separate reimplementation of the dependence math."""
    game = find_demo_game(game_id)
    if game is None:
        return []
    opportunities = {(o["player_id"], o["prop"]): o for o in dd.build_demo_opportunities()}
    roster = [p for p in dd.build_demo_roster() if p.team in (game.away, game.home)]
    out = []
    for player in roster:
        if len(out) >= limit:
            break
        for leg_a, leg_b, rho_key, dependence_name, redundant in _COMBO_SPECS:
            oa = opportunities.get((player.player_id, leg_a))
            ob = opportunities.get((player.player_id, leg_b))
            if oa is None or ob is None:
                continue
            p_a, p_b = oa["raw_probability"], ob["raw_probability"]
            naive = p_a * p_b
            if redundant:
                validated = logical_control_probability(min(p_a, p_b))
            else:
                rho = rho_by_name.get(rho_key, 0.0)
                validated = gaussian_copula_joint_upper_tail(p_a, p_b, rho)
            out.append({
                "player": player.name, "leg_a": oa, "leg_b": ob, "naive": naive,
                "validated": validated, "dependence_name": dependence_name, "redundant": redundant,
            })
            if len(out) >= limit:
                break
    return out


def game_wait_reasons(game_id: str) -> list[str]:
    game = find_demo_game(game_id)
    if game is None:
        return []
    reasons = list(game.warnings)
    opps = [o for o in dd.build_demo_opportunities() if o["team"] in (game.away, game.home)
            and o["decision"] == "WAIT"]
    unmapped = {o["player"] for o in opps}
    if unmapped:
        reasons.append(f"{len(unmapped)} player market(s) WAITING on simulated data readiness")
    return reasons
