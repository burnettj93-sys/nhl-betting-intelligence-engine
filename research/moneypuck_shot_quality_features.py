"""
PIT-safe, season-scoped rolling OFFENSIVE (xGF/60) and DEFENSIVE (xGA/60)
5v5 shot-quality features, built strictly on top of
research/moneypuck_ingestion/query.py's STRICT-PRIOR-GAME-DATE research
query API -- same discipline as research/moneypuck_team_features.py and
research/moneypuck_special_teams_features.py (see those modules'
docstrings for the shared season-boundary/minimum-sample rationale). No
direct SQL query appears anywhere in this module
(tests/test_moneypuck_shot_quality_features.py AST-scans it).

FIELDS USED (Part 1 audit, read from research/moneypuck_ingestion/schema.sql
this turn): `xg_for` / `xg_against` (MoneyPuck's `xGoalsFor`/`xGoalsAgainst`)
and `ice_time_seconds` (MoneyPuck's `iceTime`) at `situation='5on5'`.
`ice_time_seconds` units re-verified THIS turn for 5v5 rows specifically
(not assumed carried over from the special-teams slice): a real 5v5 row
of `2485.0` is a plausible ~41.4-minute single-game 5v5 shift total (mean
across the real corpus's 5v5 rows this turn: ~49.4 minutes/game) --
confirmed SECONDS, same representation as the 5on4/4on5 rows. 0 NULL
ice_time_seconds among 10,496 real 5v5 rows.

Fields available in the normalized schema but NOT used this slice
(Part 24's optional secondary exploration target, or simply out of
scope): shots_for/against, shot_attempts_for/against,
unblocked_shot_attempts_for/against, high/medium/low_danger_shots_for/
against, high/medium/low_danger_xg_for/against, rebounds_for/against,
score_adjusted_shot_attempts_for/against,
score_venue_adjusted_xg_for/against. Rush-attempt data is NOT available
at all in this table (confirmed in MONEYPUCK_DATA_CONTRACT_REVIEW.md --
it exists only per-shot in MoneyPuck's separate, unrelated shots file).

RATE FORMULA (Part 13): POOLED rate, not an average of per-game rates --
sum the numerator and denominator separately across the window, then
divide once:

    xGF/60 = ( Σ xg_for  over the most recent `window` games ) * 3600
             / Σ ice_time_seconds over those same games

    xGA/60 = ( Σ xg_against over the most recent `window` games ) * 3600
             / Σ ice_time_seconds over those same games

MINIMUM-SAMPLE / MATURITY POLICY (Part 6): full-window-required (same
rule as the other two MoneyPuck feature modules), plus a nominal
MIN_TOTAL_TOI_SECONDS floor inherited from the special-teams precedent
for consistency. Unlike special teams, this floor is essentially never
binding in practice: 5v5 TOI is abundant (~49 minutes/game in the real
corpus, vs. ~4.6 minutes/game for a single special-teams unit), so even
`window=1` would clear a 10-minute floor -- the real maturity gate here
is the game-count requirement, not TOI scarcity. Reported explicitly
rather than silently inherited without re-justifying it for this
different data regime.

SEASON-BOUNDARY POLICY (Part 7): identical no-cross-season-carryover
policy as the other two MoneyPuck feature modules.
"""
from __future__ import annotations

from research.moneypuck_ingestion.query import team_stats_as_of

SITUATION_5V5 = "5on5"

MIN_TOTAL_TOI_SECONDS = 10 * 60  # 10 minutes -- a formality at 5v5, see module docstring


def _season_scoped_history(conn, team: str, prediction_game_date: str, season: int,
                            situation: str = SITUATION_5V5) -> list[dict]:
    all_eligible = team_stats_as_of(conn, team, prediction_game_date, situation=situation)
    return [r for r in all_eligible if r["season"] == season]


def offense_xgf_per60(conn, team: str, prediction_game_date: str, season: int,
                       window: int) -> float | None:
    """Pooled 5v5 xGF/60 over the most recent `window` season-scoped,
    strictly-prior games. None if fewer than `window` eligible games, or
    (nominally) below MIN_TOTAL_TOI_SECONDS."""
    history = _season_scoped_history(conn, team, prediction_game_date, season)
    if len(history) < window:
        return None
    recent = history[-window:]
    total_toi = sum(r["ice_time_seconds"] or 0.0 for r in recent)
    if total_toi < MIN_TOTAL_TOI_SECONDS:
        return None
    total_xgf = sum(r["xg_for"] for r in recent)
    return total_xgf * 3600.0 / total_toi


def defense_xga_per60(conn, team: str, prediction_game_date: str, season: int,
                       window: int) -> float | None:
    """Pooled 5v5 xGA/60 (xG CONCEDED per 60 -- lower is a BETTER
    defense) over the most recent `window` season-scoped, strictly-prior
    games. Same maturity policy as offense_xgf_per60."""
    history = _season_scoped_history(conn, team, prediction_game_date, season)
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
    """Part 3/12: opponent-interactive matchup terms.

        term_home = home_offense_xGF60 - away_defense_xGA60
        term_away = away_offense_xGF60 - home_defense_xGA60

    term_home is large and positive specifically when the home team's
    own offense is strong AND this particular away opponent's defense is
    weak -- a genuine matchup quantity, distinguishing "strong offense
    vs weak defense" from "weak offense vs strong defense" (Part 3's
    explicit requirement), not two independent team ratings averaged
    together. Returns None if any of the 4 underlying rates is
    immature."""
    home_off = offense_xgf_per60(conn, home_team, prediction_game_date, season, window)
    away_def = defense_xga_per60(conn, away_team, prediction_game_date, season, window)
    away_off = offense_xgf_per60(conn, away_team, prediction_game_date, season, window)
    home_def = defense_xga_per60(conn, home_team, prediction_game_date, season, window)
    if None in (home_off, away_def, away_off, home_def):
        return None
    term_home = home_off - away_def
    term_away = away_off - home_def
    return term_home, term_away
