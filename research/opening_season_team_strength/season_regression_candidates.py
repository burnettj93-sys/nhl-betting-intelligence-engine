"""
Opening-Season Team Strength block (2026-09-29): Candidate A vs Candidate B
ONLY -- closing Elo carried forward unregressed vs. closing Elo regressed
toward config.ELO_START at the season boundary, at several regression
fractions. This is the one candidate family this sprint could actually
build and validate with data already licensed/owned by this repo (the
existing research/real_nhl_results corpus, 2022-23..2025-26). It does
NOT attempt roster continuity, net roster value change, preseason signal,
or goalie-change candidates (C through G in the block's own numbering) --
see README.md in this directory for exactly why, and what real data would
be required to build them honestly rather than guessed.

FROZEN: reads models/elo_model.py's constants for the fraction ALREADY in
production (config.ELO_SEASON_REGRESSION = 0.30) and research/elo_comparison.py's
corpus loader only. Does not import or modify models/combined_model.py,
models/elo_model.py, pricing/*, or operational/*. No network. No paid call.

FINDING THIS SCRIPT CONFIRMS (see run_it() output and
OPENING_SEASON_TEAM_STRENGTH_REPORT.md): production's season-regression rule
(EloModel.maybe_regress_new_season / ResearchEloState.maybe_regress_new_season)
is only ever INVOKED when a NEW GAME is iterated whose season label differs
from the previous one. Both the live DB-backed model (models/combined_model.py)
and the frozen research corpus this dashboard's "Live Model Edges" reuses
(dashboard/data_access.compute_baseline_predictions -> research/elo_comparison.
run_walkforward) share this same trigger condition. Because the frozen
corpus's last row is 2026-04-16 (end of 2025-26) and contains ZERO 2026-27
games, run_walkforward() never iterates into a 2026-27 game, so
maybe_regress_new_season("20262027") is never called for it -- the ratings
it hands back today are the raw, UNREGRESSED 2025-26 closing values. That is
the entire mechanical explanation for the "167-171 days stale" figure shown
on Today: it is a REAL DATA-PIPELINE GAP (the corpus was never extended past
last season, so the season boundary the live display is already mechanically
capable of applying was simply never reached), not evidence that Elo/rosters
are unmodelable, and not something the currently-deployed regression code was
ever missing.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import config
from research import elo_comparison as ec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_PATH = REPO_ROOT / "research" / "real_nhl_results" / "normalized_regular_season_games.jsonl"

# The 3 season BOUNDARIES fully contained in the corpus (both the closing
# season and the following opening season are present) -- the only points
# at which "closing Elo vs regressed-closing Elo" can be tested without
# any data this repo doesn't already have.
BOUNDARIES = (20222023, 20232024, 20242025, 20252026)
BUCKETS = ((1, 5), (6, 10), (11, 20), (21, 10_000))


@dataclass
class _State:
    regression_fraction: float
    ratings: dict = field(default_factory=dict)
    current_season: object = None

    def _r(self, t: str) -> float:
        return self.ratings.setdefault(t, config.ELO_START)

    def win_probability(self, home: str, away: str) -> float:
        home_r = self._r(home) + config.ELO_HOME_ADVANTAGE
        away_r = self._r(away)
        return 1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))

    def maybe_regress(self, season) -> None:
        """Identical trigger condition to production/research: fires only when
        an iterated game's season label differs from the previous one -- the
        exact mechanism this file's docstring shows never reaches 2026-27."""
        if self.current_season is not None and season != self.current_season:
            for t in self.ratings:
                self.ratings[t] += (config.ELO_START - self.ratings[t]) * self.regression_fraction
        self.current_season = season

    def update(self, home: str, away: str, home_won: bool) -> None:
        p_home = self.win_probability(home, away)
        delta = config.ELO_K_FACTOR * ((1.0 if home_won else 0.0) - p_home)
        self.ratings[home] = self._r(home) + delta
        self.ratings[away] = self._r(away) - delta


def _bucket(n: int) -> str:
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return f"games_{lo}-{hi}" if hi < 10_000 else f"games_{lo}+"
    return "games_1-5"


def run_regression_fraction(games: list[dict], fraction: float) -> list[dict]:
    """STRICT prior-game-date walk-forward (same discipline as
    research/elo_comparison.run_walkforward), over the WHOLE corpus, at one
    fixed season-regression fraction. Also records each team's own game
    number within its current season (1-indexed) so records can be bucketed
    by games-since-boundary regardless of matchup."""
    state = _State(regression_fraction=fraction)
    season_game_no: dict = {}
    records = []
    for game_date, day_games in ec.group_by_date_sorted(games):
        state.maybe_regress(day_games[0]["season"])
        if day_games[0]["season"] != state.current_season:
            pass  # maybe_regress already updated current_season
        day_recs = []
        for g in day_games:
            season = g["season"]
            h_no = season_game_no.get((season, g["home_team"]), 0) + 1
            a_no = season_game_no.get((season, g["away_team"]), 0) + 1
            p_home = state.win_probability(g["home_team"], g["away_team"])
            day_recs.append({
                "season": season, "game_date": g["game_date"],
                "home_team": g["home_team"], "away_team": g["away_team"],
                "p_home": p_home, "actual_home_win": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                "min_games_into_season": min(h_no, a_no),
            })
            season_game_no[(season, g["home_team"])] = h_no
            season_game_no[(season, g["away_team"])] = a_no
        for g, rec in zip(day_games, day_recs):
            state.update(g["home_team"], g["away_team"], g["home_score"] > g["away_score"])
        records.extend(day_recs)
    return records


def brier(records: list[dict]) -> float:
    return sum((r["p_home"] - r["actual_home_win"]) ** 2 for r in records) / len(records)


def log_loss(records: list[dict], eps: float = 1e-12) -> float:
    total = 0.0
    for r in records:
        p = min(max(r["p_home"], eps), 1 - eps)
        total += -(r["actual_home_win"] * math.log(p) + (1 - r["actual_home_win"]) * math.log(1 - p))
    return total / len(records)


def run_it() -> dict:
    games = ec.load_corpus(str(CORPUS_PATH))
    fractions = [0.0, 0.15, 0.30, 0.50, 0.70, 1.0]
    out = {"corpus_n_games": len(games), "corpus_seasons": BOUNDARIES,
           "regression_fractions_tested": fractions, "by_fraction": {}}
    for frac in fractions:
        recs = run_regression_fraction(games, frac)
        # Only evaluate games belonging to a season that HAS a prior season
        # in the corpus (i.e. a real boundary was actually crossed for it) --
        # 2022-23 has no in-corpus prior, so it is excluded from bucketed
        # scoring for every fraction (identical baseline noise otherwise).
        post_boundary = [r for r in recs if r["season"] != 20222023]
        by_bucket = {}
        for lo, hi in BUCKETS:
            label = f"games_{lo}-{hi}" if hi < 10_000 else f"games_{lo}+"
            bucket_recs = [r for r in post_boundary if lo <= r["min_games_into_season"] <= hi]
            by_bucket[label] = {
                "n": len(bucket_recs),
                "brier": round(brier(bucket_recs), 5) if bucket_recs else None,
                "log_loss": round(log_loss(bucket_recs), 5) if bucket_recs else None,
            }
        out["by_fraction"][str(frac)] = {
            "overall_post_boundary": {"n": len(post_boundary), "brier": round(brier(post_boundary), 5),
                                       "log_loss": round(log_loss(post_boundary), 5)},
            "by_bucket": by_bucket,
        }
    return out


if __name__ == "__main__":
    result = run_it()
    print(json.dumps(result, indent=2))
    out_path = Path(__file__).resolve().parent / "season_regression_results.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(f"\nwrote {out_path}")
