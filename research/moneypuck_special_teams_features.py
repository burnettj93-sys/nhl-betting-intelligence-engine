"""
PIT-safe, season-scoped rolling PP/PK (special-teams) features, built
strictly on top of research/moneypuck_ingestion/query.py's
STRICT-PRIOR-GAME-DATE research query API -- same discipline as
research/moneypuck_team_features.py (see that module's docstring for the
shared season-boundary/minimum-sample rationale). No direct SQL query
appears anywhere in this module
(tests/test_moneypuck_special_teams_features.py AST-scans it).

FIELDS USED (Part 1 audit, read from research/moneypuck_ingestion/schema.sql
this turn): `xg_for` / `xg_against` (MoneyPuck's `xGoalsFor`/`xGoalsAgainst`)
and `ice_time_seconds` (MoneyPuck's `iceTime`, verified real units:
SECONDS), at `situation='5on4'` (power play) and `situation='4on5'`
(penalty kill). `ice_time_seconds` was NOT part of the original team-data
ingestion foundation slice -- it exists in the raw MoneyPuck source (real,
already-archived data, re-verified this turn) but was never mapped into
the normalized schema until now. It was added as a minimal, targeted
schema/ingestion extension specifically because this slice needs a real
TOI-based rate denominator rather than a fabricated "opportunity count"
(MONEYPUCK_TEAM_INGESTION_REPORT.md / MONEYPUCK_DATA_CONTRACT_REVIEW.md
both note PP/PK "opportunity count" is not a reliable direct MoneyPuck
column) -- see MONEYPUCK_SPECIAL_TEAMS_EXPERIMENT_REPORT.md Section A.
No goals/shots/danger-zone/score-adjusted special-teams field is used
this slice (Part 3: keep the family small).

RATE FORMULAS (Part 2): both are per-60-minute rates, MoneyPuck's own
convention-free unit (a real TOI denominator, never a fabricated
opportunity count):

    PP xGF/60  = sum(xGF over window games at 5on4) / (sum(ice_time_seconds) / 3600) * 60
               = sum(xGF) * 3600 / sum(ice_time_seconds)   [simplified]
    PK xGA/60  = sum(xGA over window games at 4on5) * 3600 / sum(ice_time_seconds)

MINIMUM-SAMPLE / MATURITY POLICY (Part 4/5): same full-window-required
rule as research/moneypuck_team_features.py (no partial windows, no
shrinkage), PLUS an explicit minimum-accumulated-TOI floor
(`MIN_TOTAL_TOI_SECONDS`, 20 minutes) -- special-teams TOI is sparse
enough (mean ~4.6 minutes of PP time per game across the real corpus,
verified this turn) that even a full `window` of games could
occasionally represent very little actual PP/PK time in a
low-penalty-rate stretch; this floor exists specifically to stop that
edge case from ever being treated as mature. `window=10` accumulates
~46 minutes on average (well above the floor) -- the floor is a genuine
safety backstop, not the primary maturity gate, and rarely binds.

SEASON-BOUNDARY POLICY (Part 7): identical no-cross-season-carryover
policy as research/moneypuck_team_features.py -- chosen again for
consistency and the same simplicity rationale.
"""
from __future__ import annotations

from research.moneypuck_ingestion.query import team_stats_as_of

SITUATION_PP = "5on4"
SITUATION_PK = "4on5"

MIN_TOTAL_TOI_SECONDS = 20 * 60  # 20 minutes -- see module docstring


def _season_scoped_history(conn, team: str, prediction_game_date: str, season: int,
                            situation: str) -> list[dict]:
    all_eligible = team_stats_as_of(conn, team, prediction_game_date, situation=situation)
    return [r for r in all_eligible if r["season"] == season]


def pp_xgf_per60(conn, team: str, prediction_game_date: str, season: int,
                  window: int) -> float | None:
    """Power-play xG generation rate per 60 minutes, over the most
    recent `window` season-scoped, strictly-prior games at situation
    5on4. None if fewer than `window` eligible games exist this season,
    OR if accumulated TOI across those games is below
    MIN_TOTAL_TOI_SECONDS."""
    history = _season_scoped_history(conn, team, prediction_game_date, season, SITUATION_PP)
    if len(history) < window:
        return None
    recent = history[-window:]
    total_toi = sum(r["ice_time_seconds"] or 0.0 for r in recent)
    if total_toi < MIN_TOTAL_TOI_SECONDS:
        return None
    total_xgf = sum(r["xg_for"] for r in recent)
    return total_xgf * 3600.0 / total_toi


def pk_xga_per60(conn, team: str, prediction_game_date: str, season: int,
                  window: int) -> float | None:
    """Penalty-kill xG SUPPRESSION rate per 60 minutes (i.e. xG allowed
    per 60 while shorthanded -- lower is a BETTER penalty kill), over
    the most recent `window` season-scoped, strictly-prior games at
    situation 4on5. Same maturity policy as pp_xgf_per60."""
    history = _season_scoped_history(conn, team, prediction_game_date, season, SITUATION_PK)
    if len(history) < window:
        return None
    recent = history[-window:]
    total_toi = sum(r["ice_time_seconds"] or 0.0 for r in recent)
    if total_toi < MIN_TOTAL_TOI_SECONDS:
        return None
    total_xga = sum(r["xg_against"] for r in recent)
    return total_xga * 3600.0 / total_toi


def matchup_terms(conn, home_team: str, away_team: str, prediction_game_date: str,
                   season: int, window: int) -> tuple[float, float] | None:
    """Part 12: opponent-interactive matchup terms, not simple team-vs-
    team differencing.

        term_home = home_PP_xGF60 - away_PK_xGA60
        term_away = away_PP_xGF60 - home_PK_xGA60

    term_home is large and positive when the home team's own PP
    generates a lot of xG AND the away team's PK allows a lot of xG --
    i.e. home's power play specifically profits from this specific
    opponent's penalty kill. term_away is the symmetric quantity for the
    away team's PP against home's PK. Returns None if ANY of the four
    underlying rates (home PP, away PK, away PP, home PK) is immature --
    a matchup term is only as mature as its least-mature ingredient."""
    home_pp = pp_xgf_per60(conn, home_team, prediction_game_date, season, window)
    away_pk = pk_xga_per60(conn, away_team, prediction_game_date, season, window)
    away_pp = pp_xgf_per60(conn, away_team, prediction_game_date, season, window)
    home_pk = pk_xga_per60(conn, home_team, prediction_game_date, season, window)
    if None in (home_pp, away_pk, away_pp, home_pk):
        return None
    term_home = home_pp - away_pk
    term_away = away_pp - home_pk
    return term_home, term_away
