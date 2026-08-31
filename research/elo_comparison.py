"""
Research-only, isolated Elo candidate comparison against the real NHL
results corpus (research/real_nhl_results/normalized_regular_season_games.jsonl).
See TEAM_STRENGTH_ELO_REPORT.md for the original candidate design (A/B/C/D)
and REAL_NHL_RESULTS_CORPUS_REPORT.md for corpus provenance. This module
implements Part 20's instruction: build candidate logic in an isolated
research path first, and do not replace production Elo before this
comparison is reviewed.

FROZEN / NOT TOUCHED: models/elo_model.py, models/combined_model.py,
config.py, and every other production model/pricing/decision module are
byte-identical to their last-accepted state. This module does not import
any of them and does not open nhl.db or any bitemporal table -- it reads
only the flat real-game corpus file. `config` (this repo's root config.py)
IS imported, read-only, purely to reuse the exact numeric constants
(ELO_START, ELO_K_FACTOR, ELO_HOME_ADVANTAGE, ELO_SEASON_REGRESSION) the
production Elo model uses, so Candidate A here is mathematically identical
to production behavior rather than an independently-typed duplicate that
could quietly drift. See tests/test_elo_comparison_research.py's
production-equivalence test, which proves this directly against
models/elo_model.py::EloModel.

ISOLATION: only the Elo update-rule family is varied across candidates.
Every candidate uses the exact same win_probability() formula (logistic,
config.ELO_HOME_ADVANTAGE home-ice bump) and the exact same season-
regression rule (config.ELO_SEASON_REGRESSION toward config.ELO_START at
a season-label change) as production -- nothing here touches player
quality, goalie adjustment, rest penalties, the uncertainty band, pricing,
or thresholds (all frozen per instruction). Because the real corpus is
game-level results only (no boxscore/roster data), this comparison
necessarily evaluates the ELO-ONLY win probability (rating diff +
home-ice, via win_probability()) rather than the full combined-model
probability -- the full combined model could not be evaluated against
this corpus even if Part 2 hadn't required freezing the other components.

RESEARCH AVAILABILITY POLICY: STRICT PRIOR-GAME-DATE (see
research/real_nhl_results/README.md) -- for a target game on NHL calendar
date D, only games with game_date < D may have been learned from. This is
enforced structurally by run_walkforward()'s two-pass-per-date loop below:
every game scheduled on date D is PREDICTED (a read-only win_probability()
call) using only rating state built from strictly-earlier dates, and only
after EVERY game on D has been predicted does this function LEARN from
D's results (state.update()) -- so no game ever uses a same-day result,
and the update order among games sharing a date cannot matter for
eligibility (see tests/test_elo_comparison_research.py's same-day and
future-game exclusion tests). Ordering is entirely by `game_date`, a real
calendar field from the corpus, never by `game_id` or list position --
see tests/test_training_path_structural_audit.py, which this module is
written to satisfy even though (being outside models/ and outside a
bitemporal-table read) it is not required to.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import config

WeightFn = Callable[[str, int, int], float]


# --------------------------------------------------------------------------
# Corpus loading
# --------------------------------------------------------------------------

def load_corpus(path: str) -> list[dict]:
    """Loads research/real_nhl_results/normalized_regular_season_games.jsonl
    verbatim -- one dict per line, no filtering, no mutation."""
    games = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                games.append(json.loads(line))
    return games


def group_by_date_sorted(games: list[dict]) -> list[tuple[str, list[dict]]]:
    """Groups games by their real `game_date` calendar field and returns
    them in ascending date order -- the ONLY ordering this module ever
    uses for chronology. Never sorts or compares by game_id."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for g in games:
        by_date[g["game_date"]].append(g)
    return sorted(by_date.items(), key=lambda item: item[0])


# --------------------------------------------------------------------------
# Candidate weight functions (Part 6/7/8)
# --------------------------------------------------------------------------

def make_otso_weight_fn(otso_weight: float) -> WeightFn:
    """Candidate B family. REGULATION decisions keep full weight (1.0);
    OVERTIME and SHOOTOUT decisions (both) get a single reduced weight,
    per Part 6's instruction to keep the search small (no separate
    OT-vs-SO split this slice -- documented, not a broad exploration)."""
    def weight_fn(period_type: str, home_score: int, away_score: int) -> float:
        return 1.0 if period_type == "REG" else otso_weight
    return weight_fn


def make_mov_weight_fn(mov_cap: int) -> WeightFn:
    """Candidate C family. Log-dampened, capped margin-of-victory
    multiplier: raw_margin = |home_score - away_score|, capped at
    mov_cap, then log(1+capped)/log(2) -- normalized so a 1-goal margin
    (the modal NHL result, and the ALWAYS-exact margin for OT/SO games
    by rule) is neutral (multiplier == 1.0) relative to baseline. The log
    transform means each additional goal matters strictly less than the
    one before it, and the hard cap means margins beyond mov_cap (exactly
    where empty-net/garbage-time goals start appearing, per Part 9)
    contribute zero additional update magnitude no matter how lopsided
    the final score. Ignores period_type entirely by design -- see
    TEAM_STRENGTH_ELO_REPORT.md Step 2's Candidate C definition."""
    def weight_fn(period_type: str, home_score: int, away_score: int) -> float:
        raw_margin = abs(home_score - away_score)
        capped = min(raw_margin, mov_cap)
        return math.log(1 + capped) / math.log(2)
    return weight_fn


def make_combined_weight_fn(otso_weight: float, mov_cap: int) -> WeightFn:
    """Candidate D: both multipliers applied together, per Part 8."""
    otso_fn = make_otso_weight_fn(otso_weight)
    mov_fn = make_mov_weight_fn(mov_cap)

    def weight_fn(period_type: str, home_score: int, away_score: int) -> float:
        return (otso_fn(period_type, home_score, away_score)
                * mov_fn(period_type, home_score, away_score))
    return weight_fn


# --------------------------------------------------------------------------
# Research Elo state
# --------------------------------------------------------------------------

@dataclass
class ResearchEloState:
    """Same rating dynamics as models/elo_model.py::EloModel -- identical
    win_probability() formula and identical season-regression rule,
    reusing the exact same config.py constants -- with one injectable
    difference: `weight_fn` scales the update magnitude (Part 6/7/8's
    OT/SO and margin-of-victory multipliers). weight_fn=None reproduces
    the unmodified production update rule exactly (Candidate A); see
    tests/test_elo_comparison_research.py's production-equivalence test.

    Unlike production EloModel (which takes a fixed team list at
    construction), this auto-bootstraps any team at config.ELO_START the
    first time it's seen -- the real corpus's team universe (33
    abbreviations across 4 seasons, including the ARI->UTA relocation)
    is derived from the data itself rather than hardcoded here."""
    weight_fn: Optional[WeightFn] = None
    ratings: dict[str, float] = field(default_factory=dict)
    _current_season: object = None

    def _rating(self, team: str) -> float:
        return self.ratings.setdefault(team, config.ELO_START)

    def win_probability(self, home_team: str, away_team: str) -> float:
        home_r = self._rating(home_team) + config.ELO_HOME_ADVANTAGE
        away_r = self._rating(away_team)
        return 1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))

    def maybe_regress_new_season(self, season_label) -> None:
        if self._current_season is not None and season_label != self._current_season:
            for t in self.ratings:
                self.ratings[t] += (config.ELO_START - self.ratings[t]) * config.ELO_SEASON_REGRESSION
        self._current_season = season_label

    def update(self, home_team: str, away_team: str, home_won: bool,
               period_type: str, home_score: int, away_score: int) -> tuple[float, float, float]:
        """Returns (delta_applied, weight_applied, p_home_at_update_time)
        so callers can record the exact update without recomputing (and
        risking a drifted duplicate of) this formula themselves."""
        p_home = self.win_probability(home_team, away_team)
        actual = 1.0 if home_won else 0.0
        weight = self.weight_fn(period_type, home_score, away_score) if self.weight_fn else 1.0
        delta = config.ELO_K_FACTOR * weight * (actual - p_home)
        self.ratings[home_team] = self._rating(home_team) + delta
        self.ratings[away_team] = self._rating(away_team) - delta
        return delta, weight, p_home


# --------------------------------------------------------------------------
# Walk-forward evaluation
# --------------------------------------------------------------------------

def run_walkforward(games: list[dict], weight_fn: Optional[WeightFn] = None
                     ) -> tuple[list[dict], ResearchEloState]:
    """Runs the STRICT PRIOR-GAME-DATE walk-forward evaluation for one
    candidate weight function over the full `games` list (any
    season range -- callers slice by season for warm-up/tuning/eval).
    Pure function of (games, weight_fn): no I/O, no wall-clock, no
    randomness -- re-running it on the same input is byte-for-byte
    reproducible (Part 21 item 10 / Part 22).

    Returns (records, final_state). Each record carries the pregame
    Elo ratings, the predicted probability, the actual outcome, and the
    exact delta/weight this candidate applied -- everything needed for
    every metric/table/example in the report without recomputing the
    Elo formula a second time anywhere else.
    """
    state = ResearchEloState(weight_fn=weight_fn)
    records: list[dict] = []
    for game_date, day_games in group_by_date_sorted(games):
        state.maybe_regress_new_season(day_games[0]["season"])

        # Pass 1 (PREDICT): read-only win_probability() calls for every
        # game on this date, using only state built from strictly-earlier
        # dates -- nothing on `game_date` has been applied to `state` yet.
        day_records = []
        for g in day_games:
            p_home = state.win_probability(g["home_team"], g["away_team"])
            day_records.append({
                "game_id": g["game_id"],
                "season": g["season"],
                "game_date": g["game_date"],
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "home_score": g["home_score"],
                "away_score": g["away_score"],
                "period_type": g["period_type"],
                "rating_home_pregame": state.ratings.get(g["home_team"], config.ELO_START),
                "rating_away_pregame": state.ratings.get(g["away_team"], config.ELO_START),
                "p_home": p_home,
                "actual_home_win": 1.0 if g["home_score"] > g["away_score"] else 0.0,
            })

        # Pass 2 (LEARN): only now, after every game on this date has been
        # predicted, apply this date's results to `state`.
        for g, rec in zip(day_games, day_records):
            home_won = g["home_score"] > g["away_score"]
            delta, weight, p_home_check = state.update(
                g["home_team"], g["away_team"], home_won,
                g["period_type"], g["home_score"], g["away_score"])
            assert abs(p_home_check - rec["p_home"]) < 1e-12, (
                "state must not have changed between the predict and learn passes "
                "for the same date"
            )
            rec["weight_applied"] = weight
            rec["home_elo_delta"] = delta

        records.extend(day_records)
    return records, state


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def per_game_brier(records: list[dict]) -> list[float]:
    return [(r["p_home"] - r["actual_home_win"]) ** 2 for r in records]


def per_game_log_loss(records: list[dict], eps: float = 1e-12) -> list[float]:
    out = []
    for r in records:
        p = min(max(r["p_home"], eps), 1 - eps)
        a = r["actual_home_win"]
        out.append(-(a * math.log(p) + (1 - a) * math.log(1 - p)))
    return out


def brier_score(records: list[dict]) -> float:
    scores = per_game_brier(records)
    return sum(scores) / len(scores)


def log_loss(records: list[dict], eps: float = 1e-12) -> float:
    scores = per_game_log_loss(records, eps=eps)
    return sum(scores) / len(scores)


def mean_predicted_prob(records: list[dict]) -> float:
    return sum(r["p_home"] for r in records) / len(records)


def actual_home_win_rate(records: list[dict]) -> float:
    return sum(r["actual_home_win"] for r in records) / len(records)


def calibration_error(records: list[dict]) -> float:
    return abs(mean_predicted_prob(records) - actual_home_win_rate(records))


DEFAULT_CALIBRATION_EDGES = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]


def calibration_table(records: list[dict], edges: list[float] = None) -> list[dict]:
    edges = edges if edges is not None else DEFAULT_CALIBRATION_EDGES
    buckets = []
    for lo, hi in zip(edges, edges[1:]):
        in_bucket = [r for r in records if lo <= r["p_home"] < hi]
        n = len(in_bucket)
        if n == 0:
            buckets.append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None,
                             "actual_rate": None, "calibration_error": None, "low_n": True})
            continue
        mean_pred = mean_predicted_prob(in_bucket)
        actual_rate = actual_home_win_rate(in_bucket)
        buckets.append({
            "lo": lo, "hi": hi, "n": n, "mean_pred": mean_pred,
            "actual_rate": actual_rate, "calibration_error": abs(mean_pred - actual_rate),
            "low_n": n < 30,
        })
    return buckets


def season_breakdown(records: list[dict]) -> dict:
    seasons = sorted({r["season"] for r in records})
    out = {}
    for s in seasons:
        subset = [r for r in records if r["season"] == s]
        out[s] = {
            "n": len(subset),
            "brier": brier_score(subset),
            "log_loss": log_loss(subset),
            "mean_pred": mean_predicted_prob(subset),
            "actual_rate": actual_home_win_rate(subset),
        }
    return out


def probability_distribution_summary(records: list[dict]) -> dict:
    probs = sorted(r["p_home"] for r in records)
    n = len(probs)
    def pct(p):
        idx = min(int(p * n), n - 1)
        return probs[idx]
    return {
        "n": n,
        "min": probs[0],
        "p10": pct(0.10),
        "p50": pct(0.50),
        "p90": pct(0.90),
        "max": probs[-1],
        "frac_above_0_70": sum(1 for p in probs if p > 0.70) / n,
        "frac_below_0_30": sum(1 for p in probs if p < 0.30) / n,
    }


def team_rating_path(records: list[dict], team: str) -> list[tuple[str, float]]:
    """Pregame rating for `team` at every game it played, in date order."""
    path = []
    for r in records:
        if r["home_team"] == team:
            path.append((r["game_date"], r["rating_home_pregame"]))
        elif r["away_team"] == team:
            path.append((r["game_date"], r["rating_away_pregame"]))
    return sorted(path, key=lambda x: x[0])


def rating_stability_summary(records: list[dict], team: str) -> dict:
    path = team_rating_path(records, team)
    ratings = [r for _, r in path]
    if not ratings:
        return {"team": team, "n_games": 0}
    biggest_jump = max((abs(ratings[i] - ratings[i - 1]) for i in range(1, len(ratings))), default=0.0)
    return {
        "team": team,
        "n_games": len(ratings),
        "min_rating": min(ratings),
        "max_rating": max(ratings),
        "final_rating": ratings[-1],
        "biggest_single_game_jump": biggest_jump,
    }


# --------------------------------------------------------------------------
# Paired bootstrap uncertainty (Part 16)
# --------------------------------------------------------------------------

def paired_bootstrap_delta(baseline_scores: list[float], candidate_scores: list[float],
                            n_resamples: int = 2000, seed: int = 1337) -> dict:
    """Paired resampling over the SAME evaluated games (every candidate
    predicts the same games -- Part 16 explicitly requires paired, never
    unpaired, comparison). Resamples game INDICES with replacement and
    recomputes (mean candidate score - mean baseline score) each time;
    reports a 95% percentile interval and the fraction of resamples where
    the candidate improves (delta < 0, since both Brier and log loss are
    loss metrics where lower is better)."""
    assert len(baseline_scores) == len(candidate_scores)
    n = len(baseline_scores)
    point_delta = sum(candidate_scores) / n - sum(baseline_scores) / n
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        b = sum(baseline_scores[i] for i in idx) / n
        c = sum(candidate_scores[i] for i in idx) / n
        deltas.append(c - b)
    deltas.sort()
    lo_i = int(0.025 * n_resamples)
    hi_i = min(int(0.975 * n_resamples), n_resamples - 1)
    return {
        "point_delta": point_delta,
        "ci_low": deltas[lo_i],
        "ci_high": deltas[hi_i],
        "frac_resamples_improved": sum(1 for d in deltas if d < 0) / n_resamples,
        "n_resamples": n_resamples,
    }
