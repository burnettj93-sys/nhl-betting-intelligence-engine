"""
Goals Shot-Quality Refinement Cycle. PIT-safe shot-quality feature
construction on top of the ALREADY-FROZEN, unmodified incumbent Goals
model (research/run_player_goals_model.py's candidate E). This module
never touches the incumbent's own weights/rates -- it only builds NEW
candidate features to test as a small offset correction on top of the
incumbent's own prediction.

Part 1 field audit (real MoneyPuck fields, already captured in
research/player_goals/player_game_goals.jsonl -- no new corpus needed):
  individual xG            -> individual_xg (player-game grain)
  high-danger attempts     -> high_danger_shots (player-game grain)
  medium/low-danger        -> medium_danger_shots / low_danger_shots
  high-danger xG           -> high_danger_xg (player-game grain)
  shot attempts / SOG      -> shot_attempts / sog
  PP xG                    -> pp.individual_xg (nested block, player-game grain)
  rush attempts            -> NOT AVAILABLE (confirmed absent from the raw
                               MoneyPuck skater export, same finding as the
                               original Goals corpus audit -- not invented)

All metrics below are CAREER cumulative through player_history_as_of(),
shrunk by career SHOT VOLUME (n_shots/(n_shots+K), same convention as
shooting-talent shrinkage) -- never a raw small-sample rate.
"""
from __future__ import annotations

import statistics


def xg_per_shot_shrunk(history: list[dict], league_xg_per_shot: float, shrinkage_shots: int) -> tuple[float, int]:
    career_shots = sum(r["sog"] for r in history)
    if career_shots <= 0:
        return league_xg_per_shot, 0
    career_xg = sum(r["individual_xg"] for r in history)
    raw = career_xg / career_shots
    w = career_shots / (career_shots + shrinkage_shots)
    return league_xg_per_shot + w * (raw - league_xg_per_shot), int(career_shots)


def high_danger_share_shrunk(history: list[dict], league_hd_share: float, shrinkage_attempts: int) -> tuple[float, int]:
    career_attempts = sum(r["shot_attempts"] for r in history)
    if career_attempts <= 0:
        return league_hd_share, 0
    career_hd = sum(r["high_danger_shots"] for r in history)
    raw = career_hd / career_attempts
    w = career_attempts / (career_attempts + shrinkage_attempts)
    return league_hd_share + w * (raw - league_hd_share), int(career_attempts)


def finishing_above_xg_shrunk(history: list[dict], shrinkage_games: int) -> tuple[float, int]:
    """Part 6: goals - xG, per-game, heavily shrunk toward 0 (no
    persistent talent assumed by default -- the shrinkage target here is
    the LEAGUE-WIDE MEAN of goals-xG, which is ~0 by construction of xG
    itself, not an arbitrary choice)."""
    n = len(history)
    if n == 0:
        return 0.0, 0
    raw = sum(r["goals"] - r["individual_xg"] for r in history) / n
    w = n / (n + shrinkage_games)
    return w * raw, n


def pp_xg_per_shot_shrunk(history: list[dict], league_pp_xg_per_shot: float, shrinkage_shots: int) -> tuple[float, int]:
    """PP-situation xG/shot -- the "pp" block (situation="5on4") carries
    its own icetime/goals/sog/individual_xg (see build_goals_corpus.py),
    so this mirrors xg_per_shot_shrunk exactly, scoped to PP shots only."""
    pp_rows = [r for r in history if r["pp"] is not None]
    career_pp_shots = sum(r["pp"]["sog"] for r in pp_rows) if pp_rows else 0.0
    if career_pp_shots <= 0:
        return league_pp_xg_per_shot, 0
    career_pp_xg = sum(r["pp"]["individual_xg"] for r in pp_rows)
    raw = career_pp_xg / career_pp_shots
    w = career_pp_shots / (career_pp_shots + shrinkage_shots)
    return league_pp_xg_per_shot + w * (raw - league_pp_xg_per_shot), int(career_pp_shots)
