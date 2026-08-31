"""
Model-facing view logic: turns a baseline prediction record (from
data_access.compute_baseline_predictions(), itself produced by the real,
unmodified production Elo model) into display-ready structures. No
probability is computed here that wasn't already computed by
research.elo_comparison / models/elo_model.py -- this module only
formats and labels.

MODEL INPUT vs. RESEARCH METRIC (mandatory distinction, per spec): every
value shown on Game Detail/Team Ratings is tagged as one or the other.
Elo rating and home-ice are MODEL INPUT (the real, current production
formula). Every MoneyPuck-derived value (5v5 xG share, xGF/60, xGA/60,
PP/PK rates) is RESEARCH METRIC -- NOT CURRENTLY USED BY MODEL, because
none of the four completed feature experiments were adopted. Player,
goalie, and rest contributions are labeled NOT AVAILABLE, not
approximated -- see module docstring in data_access.py for why.
"""
from __future__ import annotations

import config
from research import moneypuck_team_features as team_xg
from research import moneypuck_special_teams_features as st_xg
from research import moneypuck_shot_quality_features as sq_xg

CONFIDENCE_TOSS_UP = 0.05
CONFIDENCE_CLEAR = 0.15


def confidence_label(p_home: float) -> str:
    """A simple DISPLAY heuristic based on distance from 50% -- explicitly
    NOT the production uncertainty/CI band (config.BASE_UNCERTAINTY_BAND_HALF_WIDTH),
    which depends on goalie-confirmation data unavailable in historical
    research mode. Labeled as such everywhere it's shown."""
    distance = abs(p_home - 0.5)
    if distance < CONFIDENCE_TOSS_UP:
        return "TOSS-UP"
    if distance < CONFIDENCE_CLEAR:
        return "LEAN"
    return "CLEAR FAVORITE"


def elo_diff_driver(record: dict) -> dict:
    home_elo = record["rating_home_pregame"]
    away_elo = record["rating_away_pregame"]
    diff = (home_elo + config.ELO_HOME_ADVANTAGE) - away_elo
    return {
        "home_elo": home_elo,
        "away_elo": away_elo,
        "home_advantage": config.ELO_HOME_ADVANTAGE,
        "effective_diff": diff,
        "favors": record["home_team"] if diff > 0 else record["away_team"],
    }


def model_drivers(record: dict) -> list[dict]:
    """Only components genuinely computed from real data this slice --
    Elo + home ice. Player/goalie/rest are NOT included here (see module
    docstring); the UI renders them as a separate NOT AVAILABLE notice,
    not as zero-valued driver rows, so nothing here implies a component
    was computed and happened to be neutral."""
    driver = elo_diff_driver(record)
    drivers = []
    sign = "+" if driver["favors"] == record["home_team"] else "-"
    drivers.append({
        "label": f"Team strength (Elo): {driver['home_elo']:.0f} vs {driver['away_elo']:.0f}",
        "sign": sign, "input": True,
    })
    drivers.append({
        "label": f"Home ice (+{config.ELO_HOME_ADVANTAGE:.0f} Elo pts to {record['home_team']})",
        "sign": "+", "input": True,
    })
    return drivers


def moneypuck_context(conn, team: str, prediction_game_date: str, season: int,
                       window: int = 25) -> dict:
    """RESEARCH METRIC values only -- every key here must be rendered
    under a 'RESEARCH METRIC -- NOT CURRENTLY USED BY MODEL' label,
    never alongside MODEL INPUT values without that distinction. Reuses
    the exact, already-tested feature functions from the four completed
    experiments -- nothing recomputed here."""
    return {
        "xg_share_5v5": team_xg.rolling_xg_share(conn, team, prediction_game_date, season, window),
        "xg_diff_all": team_xg.rolling_xg_diff_per_game(conn, team, prediction_game_date, season, window),
        "pp_xgf60": st_xg.pp_xgf_per60(conn, team, prediction_game_date, season, window),
        "pk_xga60": st_xg.pk_xga_per60(conn, team, prediction_game_date, season, window),
        "offense_xgf60": sq_xg.offense_xgf_per60(conn, team, prediction_game_date, season, window),
        "defense_xga60": sq_xg.defense_xga_per60(conn, team, prediction_game_date, season, window),
        "window": window,
    }


def team_ratings_table(records: list[dict], conn, as_of_date: str, season: int,
                        include_moneypuck: bool = True) -> list[dict]:
    """One row per team: current Elo rating as of `as_of_date` (the
    latest rating strictly before that date, per the same PIT discipline
    every research module in this project uses), games played, plus
    optional RESEARCH METRIC MoneyPuck context."""
    teams = sorted({r["home_team"] for r in records} | {r["away_team"] for r in records})
    rows = []
    for team in teams:
        team_games = [
            r for r in records
            if (r["home_team"] == team or r["away_team"] == team)
            and r["season"] == season and r["game_date"] < as_of_date
        ]
        if not team_games:
            continue
        team_games.sort(key=lambda r: r["game_date"])
        latest = team_games[-1]
        rating = latest["rating_home_pregame"] if latest["home_team"] == team else latest["rating_away_pregame"]
        wins = sum(1 for r in team_games
                   if (r["home_team"] == team and r["actual_home_win"] == 1.0)
                   or (r["away_team"] == team and r["actual_home_win"] == 0.0))
        row = {
            "team": team, "elo_rating": rating, "games_played": len(team_games),
            "wins": wins, "losses": len(team_games) - wins,
        }
        if include_moneypuck and conn is not None:
            row["research"] = moneypuck_context(conn, team, as_of_date, season)
        rows.append(row)
    rows.sort(key=lambda r: r["elo_rating"], reverse=True)
    return rows
