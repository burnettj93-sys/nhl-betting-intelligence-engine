"""
PIT-safe, GOALIE-SCOPED (not team-scoped -- Part 17) quality features,
built from research/goalie_intelligence/goalie_appearances.jsonl (see
build_quality_corpus.py). Same STRICT PRIOR-GAME-DATE discipline as
every other research module: `goalie_history_as_of()` is the one gate
every function below routes through.

NOT SEASON-SCOPED, DELIBERATELY (Part 16): unlike the team-level
MoneyPuck features (which reset each season by design), goalie quality
here is CUMULATIVE across a goalie's entire real appearance history
(2022-23 onward) with no season boundary -- so a goalie's career
evidence is never thrown away just because a new season started. Part
16's "regressed prior, current-season evidence gradually takes over" is
achieved automatically by the shrinkage formulas below: a goalie with
a small cumulative sample is shrunk hard toward the league-average
baseline; a goalie with a large cumulative sample is shrunk much less.
No separate two-stage prior/current blend is needed -- the shrinkage
formula IS the regressed-prior mechanism.

CANDIDATE A (existing production quality) reuses config.py's REAL,
UNCHANGED constants and models/goalie_model.py's EXACT shrinkage
FORMULA (Part 18: baseline frozen) -- see shrunk_save_pct_production(),
whose result is a direct sum-based reimplementation of
GoalieRatingModel.rating_adjustment_elo(), proven identical by
tests/test_goalie_quality_integration.py::TestSavePctShrinkageCorrectness
(cross-checked against the real production class object, not just
independently re-derived).

CANDIDATE B (MoneyPuck shot-quality) is a NEW metric
(goals-saved-above-expected-STYLE, deliberately never called plain
"GSAx" -- Part 13 -- since this project has not verified this exact
formula matches any publicly-published GSAx definition) -- see
rolling_gsax_per60().
"""
from __future__ import annotations

import json
from pathlib import Path

import config
from models.goalie_model import GoalieRatingModel

CORPUS_PATH = Path(__file__).resolve().parent / "goalie_appearances.jsonl"

# Read once from the real, unmodified production class -- never
# hand-typed as a second copy of the same constant.
LEAGUE_AVG_SAVE_PCT = GoalieRatingModel().league_save_pct


def load_appearance_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["goalie_id"]))
    return rows


def goalie_history_as_of(all_rows: list[dict], goalie_id: str, prediction_game_date: str) -> list[dict]:
    """STRICT PRIOR-GAME-DATE, goalie-scoped (NOT team-scoped -- a trade
    does not reset this). Only this goalie's appearances strictly before
    `prediction_game_date`, chronological."""
    return [r for r in all_rows if r["goalie_id"] == goalie_id and r["game_date"] < prediction_game_date]


def shrunk_save_pct_production(history: list[dict]) -> tuple[float, float]:
    """The exact production shrinkage FORMULA
    (models/goalie_model.py::rating_adjustment_elo), evaluated over
    real, PIT-safe, cumulative (all-history, per production's own
    running-total design) shots-against/saves. Returns
    (elo_delta_in_production_units, cumulative_shots_against_sample).
    No new coefficient is fit for this candidate -- it reuses
    production's own Elo-point scale (config.SAVE_PCT_TO_ELO), per Part
    18's freeze."""
    if not history:
        return 0.0, 0.0
    cum_shots = sum(r["shots_against"] for r in history)
    if cum_shots == 0:
        return 0.0, 0.0
    cum_saves = sum(r["saves"] for r in history)
    shrink = cum_shots / (cum_shots + config.GOALIE_SHRINKAGE_STARTS * 25)
    save_pct = cum_saves / cum_shots
    delta = (save_pct - LEAGUE_AVG_SAVE_PCT) * shrink * config.SAVE_PCT_TO_ELO
    return delta, cum_shots


def rolling_gsax_per60(history: list[dict], window: int | None) -> tuple[float | None, float]:
    """(xGA - GA) per 60 minutes, pooled (Part 13's real
    shots-faced-weighted approach, not an average of per-game rates)
    over the most recent `window` appearances (or ALL history if
    `window` is None), shrunk toward 0 (the league-average-equivalent
    baseline for a differential metric) by the SAME shots-faced-based
    shrinkage SHAPE as the production save% formula -- reusing
    config.GOALIE_SHRINKAGE_STARTS for consistency rather than
    inventing an unrelated new constant. Returns
    (shrunk_gsax_per60_or_None_if_no_history, shots_against_sample)."""
    recent = history if window is None else history[-window:]
    if not recent:
        return None, 0.0
    total_shots = sum(r["shots_against"] for r in recent)
    total_icetime = sum(r["icetime_seconds"] for r in recent)
    if total_icetime <= 0:
        return None, 0.0
    total_xga = sum(r["xg_against"] for r in recent)
    total_ga = sum(r["goals_against"] for r in recent)
    raw_gsax_per60 = (total_xga - total_ga) * 3600.0 / total_icetime
    shrink = total_shots / (total_shots + config.GOALIE_SHRINKAGE_STARTS * 25)
    return raw_gsax_per60 * shrink, total_shots


def has_prior_appearance(history: list[dict]) -> bool:
    return len(history) > 0
