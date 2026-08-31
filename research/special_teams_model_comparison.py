"""
Candidate feature construction for the special-teams experiment. Pure
functions over a MoneyPuck research connection + a baseline game record
-- no I/O beyond the injected `conn`, no randomness, fully reproducible.
See research/moneypuck_special_teams_features.py for the underlying
rate formulas and MONEYPUCK_SPECIAL_TEAMS_EXPERIMENT_REPORT.md Section G
for why each candidate is defined the way it is.

CANDIDATE DEFINITIONS (Part 3):
  B (PP only, team-level, non-matchup):
      z_B = home_PP_xGF60 - away_PP_xGF60
  C (PK only, team-level, non-matchup):
      z_C = away_PK_xGA60 - home_PK_xGA60   [positive favors home -- a
            LOWER xGA60 is a BETTER penalty kill, so home's PK being
            tighter than away's produces a positive value]
  D (PP+PK matchup differential, Part 11/12, single combined scalar):
      z_D = (home_PP_xGF60 - away_PK_xGA60) - (away_PP_xGF60 - home_PK_xGA60)
          = term_home - term_away  (see moneypuck_special_teams_features
            .matchup_terms())
  E (compact composite, Part 3's "compact special-teams composite"):
      a 2-feature candidate, (term_home, term_away) fit as two SEPARATE
      standardized coefficients rather than pre-combined into one scalar
      like D -- lets the logistic fit learn different weights for "home
      PP vs away PK" and "away PP vs home PK" instead of assuming they
      trade off symmetrically.
"""
from __future__ import annotations

from research import moneypuck_special_teams_features as stf

WINDOW_GRID = [10, 25]


def compute_pp_diff(conn, record: dict, window: int) -> float | None:
    home = stf.pp_xgf_per60(conn, record["home_team"], record["game_date"], record["season"], window)
    away = stf.pp_xgf_per60(conn, record["away_team"], record["game_date"], record["season"], window)
    if home is None or away is None:
        return None
    return home - away


def compute_pk_diff(conn, record: dict, window: int) -> float | None:
    home = stf.pk_xga_per60(conn, record["home_team"], record["game_date"], record["season"], window)
    away = stf.pk_xga_per60(conn, record["away_team"], record["game_date"], record["season"], window)
    if home is None or away is None:
        return None
    return away - home  # positive favors home (lower xGA60 = tighter PK)


def compute_matchup(conn, record: dict, window: int) -> tuple[float, float] | None:
    return stf.matchup_terms(conn, record["home_team"], record["away_team"],
                              record["game_date"], record["season"], window)


def compute_matchup_diff(conn, record: dict, window: int) -> float | None:
    """The single-scalar Candidate D value: term_home - term_away."""
    terms = compute_matchup(conn, record, window)
    if terms is None:
        return None
    term_home, term_away = terms
    return term_home - term_away


SINGLE_FEATURE_SPECS = {}
for _w in WINDOW_GRID:
    SINGLE_FEATURE_SPECS[f"pp_diff_{_w}"] = dict(fn=compute_pp_diff, window=_w)
    SINGLE_FEATURE_SPECS[f"pk_diff_{_w}"] = dict(fn=compute_pk_diff, window=_w)
    SINGLE_FEATURE_SPECS[f"matchup_diff_{_w}"] = dict(fn=compute_matchup_diff, window=_w)


def compute_all_single_features(conn, records: list[dict]) -> dict[str, dict[int, float | None]]:
    """{feature_name: {game_id: value_or_None}} for every scalar feature
    (B/C/D candidates) in SINGLE_FEATURE_SPECS."""
    out = {name: {} for name in SINGLE_FEATURE_SPECS}
    for rec in records:
        for name, spec in SINGLE_FEATURE_SPECS.items():
            out[name][rec["game_id"]] = spec["fn"](conn, rec, spec["window"])
    return out


def compute_matchup_pair_features(conn, records: list[dict], window: int
                                   ) -> dict[int, tuple[float, float] | None]:
    """{game_id: (term_home, term_away) or None} for Candidate E's
    2-feature composite, at a given window."""
    out = {}
    for rec in records:
        out[rec["game_id"]] = compute_matchup(conn, rec, window)
    return out
