"""
PIT-safe starter-inference features, built entirely from
research/goalie_intelligence/actual_starters.jsonl (the ARCHIVAL starter
LABEL corpus -- see build_starter_corpus.py) plus the real NHL schedule
(research/real_nhl_results/) for back-to-back detection.

STRICT PRIOR-GAME-DATE, same discipline as every other research module
in this project: every function here takes an explicit
`prediction_game_date` and only ever looks at rows with
`game_date < prediction_game_date` -- never the target game's own row,
never a same-day or future row. `team_history_as_of()` is the single
gate every feature function routes through (mirrors
research/moneypuck_ingestion/query.py::team_stats_as_of()'s role for the
MoneyPuck team features).

ARCHIVAL vs. LIVE, restated (Part 28): `actual_starter_goalie_id` is
POSTGAME truth, reconstructed after each game from real box-score-derived
data. It is used here ONLY to build features about EARLIER games (which
is legitimate -- who started game N-1 is a genuine, knowable-in-advance
fact by the time game N is played) -- never as a feature for the SAME
game whose starter is being predicted. See
tests/test_goalie_intelligence.py::TestNoTargetGameLeakage.
"""
from __future__ import annotations

import json
from pathlib import Path

CORPUS_PATH = Path(__file__).resolve().parent / "actual_starters.jsonl"


def load_starter_corpus(path: str | Path = CORPUS_PATH) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    return rows


def team_history_as_of(all_rows: list[dict], team: str, prediction_game_date: str) -> list[dict]:
    """STRICT PRIOR-GAME-DATE: only this team's rows strictly before
    `prediction_game_date`, in chronological order. The one gate every
    feature function below routes through."""
    return [r for r in all_rows if r["team"] == team and r["game_date"] < prediction_game_date]


def season_scoped(history: list[dict], season: int) -> list[dict]:
    """No cross-season carryover, same policy as the MoneyPuck team/
    special-teams/shot-quality feature modules."""
    return [r for r in history if r["season"] == season]


def previous_game_starter(history: list[dict]) -> str | None:
    return history[-1]["starter_goalie_id"] if history else None


def started_in_last_n_games(history: list[dict], goalie_id: str, n: int) -> bool:
    recent = history[-n:]
    return any(r["starter_goalie_id"] == goalie_id for r in recent)


def consecutive_start_count(history: list[dict]) -> int:
    """Current streak length of the most recent starter (0 if no history)."""
    if not history:
        return 0
    current = history[-1]["starter_goalie_id"]
    count = 0
    for r in reversed(history):
        if r["starter_goalie_id"] != current:
            break
        count += 1
    return count


def days_since_last_start(history: list[dict], goalie_id: str, prediction_game_date: str) -> int | None:
    for r in reversed(history):
        if r["starter_goalie_id"] == goalie_id:
            return (parse_date(prediction_game_date) - parse_date(r["game_date"])).days
    return None


def goalie_appeared_in_game(row: dict, goalie_id: str) -> bool:
    """True if `goalie_id` either started or backed up (any icetime > 0)
    in this one team-game row."""
    if row["starter_goalie_id"] == goalie_id:
        return True
    return any(a["goalie_id"] == goalie_id for a in row["other_appearances"])


def goalie_played_previous_night(history: list[dict], goalie_id: str, prediction_game_date: str) -> bool:
    """Did `goalie_id` appear at all (start OR relief) in this team's
    game exactly one calendar day before `prediction_game_date`?"""
    if not history:
        return False
    last = history[-1]
    if (parse_date(prediction_game_date) - parse_date(last["game_date"])).days != 1:
        return False
    return goalie_appeared_in_game(last, goalie_id)


def recent_start_share(history: list[dict], goalie_id: str, window: int) -> float | None:
    """Fraction of the most recent `window` team games started by
    `goalie_id`. None if fewer than `window` games of history exist."""
    if len(history) < window:
        return None
    recent = history[-window:]
    return sum(1 for r in recent if r["starter_goalie_id"] == goalie_id) / window


def season_start_share(history: list[dict], goalie_id: str, season: int) -> float | None:
    scoped = season_scoped(history, season)
    if not scoped:
        return None
    return sum(1 for r in scoped if r["starter_goalie_id"] == goalie_id) / len(scoped)


def eligible_goalies(history: list[dict], window: int = 20) -> list[str]:
    """Every goalie_id who either started or appeared as a reliever for
    this team in the most recent `window` games -- the candidate pool a
    projected-starter model scores over. Roster-size-agnostic (handles
    an emergency 3rd goalie automatically, since anyone who appeared is
    included) -- see Part 7's "support more than two goalies" requirement."""
    recent = history[-window:]
    ids: list[str] = []
    for r in recent:
        if r["starter_goalie_id"] not in ids:
            ids.append(r["starter_goalie_id"])
        for a in r["other_appearances"]:
            if a["goalie_id"] not in ids:
                ids.append(a["goalie_id"])
    return ids


def team_back_to_back(team_all_games_sorted: list[dict], prediction_game_date: str) -> bool:
    """True if this team played a game exactly one calendar day before
    `prediction_game_date`. `team_all_games_sorted` is any list of dicts
    with a `game_date` key for this team's real schedule (e.g. baseline
    Elo records from research.elo_comparison, or actual_starters rows --
    schedule-only, no roster/goalie data needed for this signal)."""
    prior = [g for g in team_all_games_sorted if g["game_date"] < prediction_game_date]
    if not prior:
        return False
    last_date = prior[-1]["game_date"]
    return (parse_date(prediction_game_date) - parse_date(last_date)).days == 1


def rest_differential_days(history: list[dict], goalie_a: str, goalie_b: str,
                            prediction_game_date: str) -> int | None:
    """days_since_last_start(goalie_b) - days_since_last_start(goalie_a)
    -- positive means goalie_a is fresher (started more recently is
    LOWER days-since, so a positive differential here means goalie_a has
    RESTED LESS recently than goalie_b, i.e. goalie_b is due for a
    rest-based rotation). None if either goalie has no prior start."""
    days_a = days_since_last_start(history, goalie_a, prediction_game_date)
    days_b = days_since_last_start(history, goalie_b, prediction_game_date)
    if days_a is None or days_b is None:
        return None
    return days_b - days_a


def parse_date(date_str: str):
    import datetime as dt
    return dt.date.fromisoformat(date_str)
