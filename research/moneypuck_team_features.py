"""
PIT-safe, season-scoped rolling team xG/shot-quality features, built
strictly on top of research/moneypuck_ingestion/query.py's
STRICT-PRIOR-GAME-DATE research query API -- no direct SQL query against
research_moneypuck_team_game_stats appears anywhere in this module (see
tests/test_moneypuck_team_features.py's
test_no_direct_sqlite_query_bypasses_the_query_layer, which AST-scans
this file for exactly that).

FIELDS USED (Part 1 audit, read from research/moneypuck_ingestion/schema.sql
and confirmed present in research_moneypuck.db this turn): xg_for,
xg_against (MoneyPuck's xGoalsFor/xGoalsAgainst) at situation='5on5' and
situation='all'. No other MoneyPuck column is read by this module this
slice (no shot-attempt/danger-zone/score-adjusted fields yet -- deferred,
see XG_TEAM_FEATURE_EXPERIMENT_REPORT.md Part 23 on redundancy).

MINIMUM-SAMPLE POLICY (Part 6): a rolling feature requires the FULL
requested window of eligible prior games -- no partial windows, no
shrinkage-toward-league-average, no maturity/confidence blending. Fewer
than `window` eligible games -> the feature is DATA_UNAVAILABLE (None).
Chosen for simplicity and to avoid ever silently treating a 2-game
sample as mature, per instruction.

SEASON-BOUNDARY POLICY (Part 7): NO cross-season carryover. A rolling
window only ever draws from games within the SAME season as the target
game. A team newly re-entering a season needs `window` games in that
CURRENT season before the feature matures again -- prior-season form is
discarded entirely rather than partially carried over or regressed. This
is the simplest of the three options Part 7 offered (closest to "A:
reset to league average", but stricter: DATA_UNAVAILABLE rather than a
fabricated league-average fill-in) and keeps the temporal reasoning
trivial to audit. The tradeoff (lower early-season coverage) is reported
explicitly in Part K/17 of the report rather than hidden.
"""
from __future__ import annotations

from research.moneypuck_ingestion.query import team_stats_as_of

SITUATION_5V5 = "5on5"
SITUATION_ALL = "all"


def _season_scoped_history(conn, team: str, prediction_game_date: str, season: int,
                            situation: str) -> list[dict]:
    """All STRICT-PRIOR-GAME-DATE-eligible rows for `team` at `situation`,
    further restricted to the SAME `season` as the target game (Part 7's
    no-cross-season-carryover policy) -- built entirely on top of
    team_stats_as_of(), never a direct SQL query."""
    all_eligible = team_stats_as_of(conn, team, prediction_game_date, situation=situation)
    return [r for r in all_eligible if r["season"] == season]


def rolling_xg_share(conn, team: str, prediction_game_date: str, season: int,
                      window: int, situation: str = SITUATION_5V5) -> float | None:
    """xGF / (xGF + xGA) over the most recent `window` season-scoped,
    strictly-prior games at `situation`. Rate form (Part 3) -- naturally
    comparable across teams regardless of games played, bounded in
    (0, 1). Returns None (DATA_UNAVAILABLE) if fewer than `window`
    eligible games exist this season."""
    history = _season_scoped_history(conn, team, prediction_game_date, season, situation)
    if len(history) < window:
        return None
    recent = history[-window:]
    xgf = sum(r["xg_for"] for r in recent)
    xga = sum(r["xg_against"] for r in recent)
    total = xgf + xga
    if total <= 0:
        return None
    return xgf / total


def rolling_xg_diff_per_game(conn, team: str, prediction_game_date: str, season: int,
                              window: int, situation: str = SITUATION_ALL) -> float | None:
    """(xGF - xGA) averaged per game over the most recent `window`
    season-scoped, strictly-prior games at `situation`. Rate form (Part
    3): a per-game differential rather than a raw cumulative total, so
    it's comparable regardless of how many games a team has played.
    Returns None if fewer than `window` eligible games exist this
    season."""
    history = _season_scoped_history(conn, team, prediction_game_date, season, situation)
    if len(history) < window:
        return None
    recent = history[-window:]
    diffs = [r["xg_for"] - r["xg_against"] for r in recent]
    return sum(diffs) / len(diffs)


def xg_form_delta(conn, team: str, prediction_game_date: str, season: int,
                   short_window: int, long_window: int,
                   situation: str = SITUATION_5V5) -> float | None:
    """short_window_metric - long_window_metric, both the SAME metric
    (rolling_xg_share at `situation`) -- a "recent vs. medium-term form"
    signal (Part 2 candidate C), distinct in kind from the LEVEL signals
    above (rolling_xg_share / rolling_xg_diff_per_game): positive means a
    team is trending better than its own medium-term baseline right now,
    independent of whether that medium-term baseline is itself strong or
    weak. Requires `long_window` eligible games (the stricter of the two
    requirements) -- returns None otherwise."""
    assert short_window < long_window, "short_window must be strictly shorter than long_window"
    long_history = _season_scoped_history(conn, team, prediction_game_date, season, situation)
    if len(long_history) < long_window:
        return None
    short_recent = long_history[-short_window:]
    long_recent = long_history[-long_window:]

    def _share(rows):
        xgf = sum(r["xg_for"] for r in rows)
        xga = sum(r["xg_against"] for r in rows)
        total = xgf + xga
        return xgf / total if total > 0 else None

    short_val = _share(short_recent)
    long_val = _share(long_recent)
    if short_val is None or long_val is None:
        return None
    return short_val - long_val
