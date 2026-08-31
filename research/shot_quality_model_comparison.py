"""
Candidate feature construction for the offense/defense shot-quality
decomposition experiment. Pure functions over a MoneyPuck research
connection + a baseline game record. See
research/moneypuck_shot_quality_features.py for the underlying pooled
xGF/60 / xGA/60 formulas and
MONEYPUCK_SHOT_QUALITY_DECOMPOSITION_REPORT.md Section G for the
rationale behind each candidate's exact construction.

CANDIDATE DEFINITIONS (Part 4):
  B (offense only, team-level, non-matchup, 1 feature):
      z_B = home_offense_xGF60 - away_offense_xGF60
  C (defense only, team-level, non-matchup, 1 feature):
      z_C = away_defense_xGA60 - home_defense_xGA60   [positive favors
            home -- a LOWER xGA60 is a BETTER defense]
  D (separate offense + defense, non-matchup, 2 features fit JOINTLY):
      (z_D1, z_D2) = (home_off - away_off, away_def - home_def)
      -- NOT mathematically redundant with B or C: a joint 2-parameter
      fit can assign different relative weight to offense vs. defense
      than either single-feature fit would, directly answering "does
      combining add value beyond either alone" (Part 21) rather than
      just re-deriving B+C's individual coefficients.
  E (matchup-aware, 2 features fit jointly, Part 3/12):
      (term_home, term_away) from moneypuck_shot_quality_features
      .matchup_terms() -- genuinely different from D: uses each team's
      offense against the SPECIFIC opponent's defense, not the team-level
      offense/defense differential in isolation.

D and E are the two-feature candidates; both are fit via the same
generic N-dimensional research.xg_model_comparison.fit_logistic_weights
already used for the E candidates in the prior two experiments.
"""
from __future__ import annotations

from research import moneypuck_shot_quality_features as sqf

WINDOW_GRID = [10, 25]


def compute_offense_diff(conn, record: dict, window: int) -> float | None:
    home = sqf.offense_xgf_per60(conn, record["home_team"], record["game_date"], record["season"], window)
    away = sqf.offense_xgf_per60(conn, record["away_team"], record["game_date"], record["season"], window)
    if home is None or away is None:
        return None
    return home - away


def compute_defense_diff(conn, record: dict, window: int) -> float | None:
    home = sqf.defense_xga_per60(conn, record["home_team"], record["game_date"], record["season"], window)
    away = sqf.defense_xga_per60(conn, record["away_team"], record["game_date"], record["season"], window)
    if home is None or away is None:
        return None
    return away - home  # positive favors home (lower xGA60 = better defense)


def compute_offense_defense_pair(conn, record: dict, window: int) -> tuple[float, float] | None:
    """Candidate D's raw (offense_diff, defense_diff) pair -- non-matchup."""
    off = compute_offense_diff(conn, record, window)
    dfn = compute_defense_diff(conn, record, window)
    if off is None or dfn is None:
        return None
    return off, dfn


def compute_matchup(conn, record: dict, window: int) -> tuple[float, float] | None:
    return sqf.matchup_terms(conn, record["home_team"], record["away_team"],
                              record["game_date"], record["season"], window)


SINGLE_FEATURE_SPECS = {}
for _w in WINDOW_GRID:
    SINGLE_FEATURE_SPECS[f"offense_diff_{_w}"] = dict(fn=compute_offense_diff, window=_w)
    SINGLE_FEATURE_SPECS[f"defense_diff_{_w}"] = dict(fn=compute_defense_diff, window=_w)


def compute_all_single_features(conn, records: list[dict]) -> dict[str, dict[int, float | None]]:
    out = {name: {} for name in SINGLE_FEATURE_SPECS}
    for rec in records:
        for name, spec in SINGLE_FEATURE_SPECS.items():
            out[name][rec["game_id"]] = spec["fn"](conn, rec, spec["window"])
    return out


def compute_offense_defense_pair_features(conn, records: list[dict], window: int
                                           ) -> dict[int, tuple[float, float] | None]:
    return {rec["game_id"]: compute_offense_defense_pair(conn, rec, window) for rec in records}


def compute_matchup_pair_features(conn, records: list[dict], window: int
                                   ) -> dict[int, tuple[float, float] | None]:
    return {rec["game_id"]: compute_matchup(conn, rec, window) for rec in records}
