"""
PIT-safe player-level SOG features, built from
research/player_sog/player_game_sog.jsonl (see build_sog_corpus.py) plus
the real team schedule (research/real_nhl_results/) for team-game
context (back-to-back, opponent shot environment).

STRICT PRIOR-GAME-DATE, same discipline as every other research module
in this project: `player_history_as_of()` is the one gate every
player-level feature function routes through. It is PLAYER-identity
scoped (not team-scoped) so a trade never resets a player's shot-volume
history, mirroring the goalie-quality precedent
(research/goalie_intelligence/quality.py::goalie_history_as_of).

TARGET-GAME SOG NEVER APPEARS ON THE FEATURE SIDE: every function here
takes an explicit `prediction_game_date` and only ever reads rows with
`game_date < prediction_game_date`.
"""
from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "player_game_sog.jsonl"

# Effective-sample-size shrinkage constants (games-based, not shots-based
# -- H2H and home/road samples are naturally small game COUNTS, unlike
# the goalie-quality slice's shots-faced shrinkage). Documented, reused
# consistently rather than invented per-feature.
H2H_SHRINKAGE_GAMES = 10
HOME_ROAD_SHRINKAGE_GAMES = 15

# Part 3: PROJECTED ACTIVE SKATER eligibility -- appeared in at least
# this many of the team's previous N games.
ELIGIBILITY_WINDOW_TEAM_GAMES = 10
ELIGIBILITY_MIN_APPEARANCES = 4


def load_sog_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["player_id"]))
    return rows


class PlayerHistoryIndex:
    """Performance-only re-expression of player_history_as_of() as a
    per-player, date-sorted list with bisection -- this project calls
    history lookups tens of thousands of times per evaluation run (same
    pattern/rationale as research/run_goalie_quality_comparison.py's
    QualityIndex). Produces identical results to the canonical gate
    function below; tests/test_player_sog_model.py cross-checks the two
    directly."""

    def __init__(self, rows: list[dict]):
        by_player = defaultdict(list)
        for r in rows:
            by_player[r["player_id"]].append(r)
        self._by_player: dict[str, tuple[list[str], list[dict]]] = {}
        for player_id, prows in by_player.items():
            ordered = sorted(prows, key=lambda r: (r["game_date"], r["game_id"]))
            self._by_player[player_id] = ([r["game_date"] for r in ordered], ordered)

    def history_as_of(self, player_id: str, prediction_game_date: str) -> list[dict]:
        entry = self._by_player.get(player_id)
        if entry is None:
            return []
        dates, ordered = entry
        cut = bisect.bisect_left(dates, prediction_game_date)
        return ordered[:cut]


def player_history_as_of(all_rows: list[dict], player_id: str, prediction_game_date: str) -> list[dict]:
    """STRICT PRIOR-GAME-DATE, player-identity-scoped (not team-scoped --
    a trade does not reset shot-volume history). The one canonical gate
    every feature below is defined in terms of."""
    return [r for r in all_rows if r["player_id"] == player_id and r["game_date"] < prediction_game_date]


def season_scoped(history: list[dict], season: int) -> list[dict]:
    return [r for r in history if r["season"] == season]


# --------------------------------------------------------------------------
# Part 4/5/6/7: player baseline, recent form, TOI/opportunity, attempts.
# --------------------------------------------------------------------------

def rolling_mean(history: list[dict], field: str, window: int | None) -> float | None:
    """Mean of `field` over the most recent `window` games (or all
    history if window is None). None if no history at all."""
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    return statistics.fmean(r[field] for r in recent)

def season_to_date_mean(history: list[dict], field: str, season: int) -> float | None:
    scoped = season_scoped(history, season)
    if not scoped:
        return None
    return statistics.fmean(r[field] for r in scoped)


def rolling_per60(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    total_icetime = sum(r["icetime_seconds"] for r in recent)
    if total_icetime <= 0:
        return None
    return sum(r[field] for r in recent) * 3600.0 / total_icetime


def on_target_conversion_rate(history: list[dict], window: int | None) -> float | None:
    """Cumulative SOG / cumulative shot attempts over the window -- Part
    7's "historical on-target conversion rate." None if no attempts."""
    recent = history if window is None else history[-window:]
    total_attempts = sum(r["shot_attempts"] for r in recent)
    if total_attempts <= 0:
        return None
    return sum(r["sog"] for r in recent) / total_attempts


# --------------------------------------------------------------------------
# Part 8: power-play role. `pp` is a nested block that is None on games
# where the player logged zero 5-on-4 icetime -- treated as 0s for
# rolling means (a real, PIT-safe zero, not a missing value).
# --------------------------------------------------------------------------

def rolling_pp_mean(history: list[dict], field: str, window: int | None) -> float | None:
    recent = history if window is None else history[-window:]
    if not recent:
        return None
    values = [(r["pp"][field] if r["pp"] is not None else 0.0) for r in recent]
    return statistics.fmean(values)


# --------------------------------------------------------------------------
# Part 9: opponent shot environment. Built PIT-safely from team-game
# totals aggregated from the SAME player-game corpus (every skater on a
# team, summed, per game) -- no separate ingestion needed.
# --------------------------------------------------------------------------

def build_team_game_totals(all_rows: list[dict]) -> dict[tuple[str, int], dict]:
    """{(team, game_id): {"game_date", "season", "opponent", "sog_for",
    "shot_attempts_for"}} -- one row per team per real game, summed
    across that team's own skaters in the corpus."""
    totals: dict[tuple[str, int], dict] = {}
    for r in all_rows:
        key = (r["team"], r["game_id"])
        if key not in totals:
            totals[key] = {"game_date": r["game_date"], "season": r["season"], "opponent": r["opponent"],
                            "sog_for": 0.0, "shot_attempts_for": 0.0}
        totals[key]["sog_for"] += r["sog"]
        totals[key]["shot_attempts_for"] += r["shot_attempts"]
    return totals


def build_opponent_allowed_history(team_game_totals: dict[tuple[str, int], dict]) -> dict[str, list[dict]]:
    """{team: [ {game_date, season, sog_allowed, shot_attempts_allowed}, ... ]}
    sorted by date -- team T's row for a given game is the OPPOSING
    team's offensive total in that same game (what T allowed)."""
    by_team_game: dict[tuple[str, int], dict] = {}
    for (team, game_id), row in team_game_totals.items():
        by_team_game[(team, game_id)] = row

    allowed: dict[str, list[dict]] = defaultdict(list)
    for (team, game_id), row in team_game_totals.items():
        opp = row["opponent"]
        opp_offense = by_team_game.get((opp, game_id))
        if opp_offense is None:
            continue
        allowed[team].append({
            "game_date": row["game_date"], "game_id": game_id, "season": row["season"],
            "sog_allowed": opp_offense["sog_for"], "shot_attempts_allowed": opp_offense["shot_attempts_for"],
        })
    for team in allowed:
        allowed[team].sort(key=lambda r: (r["game_date"], r["game_id"]))
    return allowed


def opponent_history_as_of(opponent_allowed_history: dict[str, list[dict]], team: str,
                            prediction_game_date: str) -> list[dict]:
    rows = opponent_allowed_history.get(team, [])
    return [r for r in rows if r["game_date"] < prediction_game_date]


def rolling_opponent_sog_allowed(opponent_allowed_history: dict[str, list[dict]], team: str,
                                  prediction_game_date: str, window: int) -> float | None:
    hist = opponent_history_as_of(opponent_allowed_history, team, prediction_game_date)
    recent = hist[-window:]
    if not recent:
        return None
    return statistics.fmean(r["sog_allowed"] for r in recent)


# --------------------------------------------------------------------------
# Part 10: head-to-head, shrunk by GAME count (not shots -- H2H samples
# are inherently tiny game counts).
# --------------------------------------------------------------------------

def h2h_history(history: list[dict], opponent: str) -> list[dict]:
    """Given an already-PIT-gated `history` (player_history_as_of
    output), the subset played against `opponent`."""
    return [r for r in history if r["opponent"] == opponent]


def h2h_shrunk_sog_rate(history: list[dict], opponent: str, baseline_rate: float) -> tuple[float, int]:
    """Shrinks the player's H2H mean SOG/game toward `baseline_rate`
    (the player's own non-H2H rolling baseline) by GAME count -- Part
    10's explicit requirement that 3 H2H games must not be treated
    anywhere near as reliable as 50 recent league games. Returns
    (shrunk_rate, h2h_game_count)."""
    h2h = h2h_history(history, opponent)
    n = len(h2h)
    if n == 0:
        return baseline_rate, 0
    h2h_mean = statistics.fmean(r["sog"] for r in h2h)
    shrink = n / (n + H2H_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (h2h_mean - baseline_rate), n


# --------------------------------------------------------------------------
# Part 11: home/road, shrunk by game count.
# --------------------------------------------------------------------------

def home_road_shrunk_sog_rate(history: list[dict], is_home: bool, baseline_rate: float) -> float:
    subset = [r for r in history if (r["home_or_away"] == "HOME") == is_home]
    n = len(subset)
    if n == 0:
        return baseline_rate
    subset_mean = statistics.fmean(r["sog"] for r in subset)
    shrink = n / (n + HOME_ROAD_SHRINKAGE_GAMES)
    return baseline_rate + shrink * (subset_mean - baseline_rate)


# --------------------------------------------------------------------------
# Part 12: rest / schedule.
# --------------------------------------------------------------------------

def parse_date(date_str: str):
    import datetime as dt
    return dt.date.fromisoformat(date_str)


def played_previous_night(history: list[dict], prediction_game_date: str) -> bool:
    if not history:
        return False
    last = history[-1]
    return (parse_date(prediction_game_date) - parse_date(last["game_date"])).days == 1


def games_in_previous_n_days(history: list[dict], prediction_game_date: str, n_days: int) -> int:
    cutoff = parse_date(prediction_game_date)
    return sum(1 for r in history if 0 < (cutoff - parse_date(r["game_date"])).days <= n_days)


# --------------------------------------------------------------------------
# Part 3: PROJECTED ACTIVE SKATER eligibility (never CONFIRMED LINEUP).
# team_schedule is any date-sorted list of {"game_date"} dicts for the
# team's real games (e.g. from research.elo_comparison team schedules).
# --------------------------------------------------------------------------

def projected_active(history: list[dict], team_schedule_prior: list[dict],
                      window: int = ELIGIBILITY_WINDOW_TEAM_GAMES,
                      min_appearances: int = ELIGIBILITY_MIN_APPEARANCES) -> bool:
    """True if the player appeared in at least `min_appearances` of the
    team's most recent `window` real games (schedule games, not just the
    player's own row count -- a player who missed several recent games
    with injury will correctly show a LOW appearance rate here, unlike
    counting only the player's own history length)."""
    recent_team_games = team_schedule_prior[-window:]
    if not recent_team_games:
        return len(history) >= min_appearances  # no team-schedule context: fall back to raw appearance count
    played_dates = {r["game_date"] for r in history}
    appearances = sum(1 for g in recent_team_games if g["game_date"] in played_dates)
    return appearances >= min_appearances
