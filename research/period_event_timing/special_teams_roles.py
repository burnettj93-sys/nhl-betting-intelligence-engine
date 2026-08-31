"""
PIT-safe PP/PK role-transition detection (special-teams role-transition
refinement sprint). Role is defined QUANTITATIVELY, never from a label
that doesn't exist historically: for every (game_id, team), players are
RANKED by their own PP (or PK) ice time that game, and the top
PP_UNIT_SIZE players are "PP1", the next PP_UNIT_SIZE are "PP2", and
everyone else (including anyone below MIN_MEANINGFUL_TOI_SECONDS) is
"NONE" -- i.e. role is relative to a player's own team-game, not an
absolute cross-league threshold, matching how real NHL PP/PK units are
actually structured (two units of ~5).

A role STATE for target game D compares:
  RECENT  = the mode single-game unit label across the player's most
            recent RECENT_GAMES games strictly before D
  BASELINE = the mode single-game unit label across the
            BASELINE_GAMES games before that (i.e. even further back,
            non-overlapping with RECENT)
against a minimum-support gate (MIN_RECENT_GAMES_WITH_DATA /
MIN_BASELINE_GAMES_WITH_DATA) -- below the gate, the state is
ROLE_UNCERTAIN rather than guessed from too little evidence.

Nothing here ever reads game D's own PP/PK ice time, deployment, or
outcome -- both windows end strictly before D (Part "TEMPORAL SAFETY").
"""
from __future__ import annotations

import statistics
from collections import defaultdict

PP_UNIT_SIZE = 5
MIN_MEANINGFUL_TOI_SECONDS = 20.0

RECENT_GAMES = 3
BASELINE_GAMES = 8
MIN_RECENT_GAMES_WITH_DATA = 2
MIN_BASELINE_GAMES_WITH_DATA = 5

_TRANSITIONS = {
    ("PP2", "PP1"): "PROMOTED_PP2_TO_PP1",
    ("NONE", "PP1"): "ADDED_TO_PP1",
    ("NONE", "PP2"): "ADDED_TO_PP2",
    ("PP1", "PP2"): "DEMOTED_PP1_TO_PP2",
    ("PP1", "NONE"): "REMOVED_FROM_PP",
    ("PP2", "NONE"): "REMOVED_FROM_PP",
    ("PK2", "PK1"): "PROMOTED_PK2_TO_PK1",
    ("NONE", "PK1"): "ADDED_TO_PK1",
    ("NONE", "PK2"): "ADDED_TO_PK2",
    ("PK1", "PK2"): "DEMOTED_PK1_TO_PK2",
    ("PK1", "NONE"): "REMOVED_FROM_PK",
    ("PK2", "NONE"): "REMOVED_FROM_PK",
}


def build_game_unit_labels(rows: list[dict], toi_path: tuple[str, ...], unit_prefix: str) -> list[dict]:
    """`rows`: player-game dicts with player_id/game_id/team/game_date/
    season and a nested TOI field reached via `toi_path` (e.g.
    ("pp","icetime_seconds")). Returns one row per input row with an
    added `unit_label` -- GENERIC "UNIT1"/"UNIT2"/"NONE" (not "PP1" etc.
    -- classify_role_state does the PP/PK relabeling itself via
    `unit_prefix`, so this stays reusable for either) -- and
    `toi_seconds` (0.0 if the nested field was None -- a player who
    genuinely never plays the man-advantage/shorthanded that game is a
    real NONE, not missing data). `unit_prefix` is accepted only for a
    consistent call signature with the PK caller; it does not affect the
    labels produced here."""
    def _toi(r):
        v = r
        for k in toi_path:
            if v is None:
                return 0.0
            v = v.get(k)
        return v or 0.0

    by_group = defaultdict(list)
    for r in rows:
        by_group[(r["game_id"], r["team"])].append({"player_id": r["player_id"], "_toi": _toi(r)})

    labels_by_key = {}
    for (game_id, team), players in by_group.items():
        ranked = sorted(players, key=lambda p: p["_toi"], reverse=True)
        for i, p in enumerate(ranked):
            if p["_toi"] < MIN_MEANINGFUL_TOI_SECONDS:
                label = "NONE"
            elif i < PP_UNIT_SIZE:
                label = "UNIT1"
            elif i < 2 * PP_UNIT_SIZE:
                label = "UNIT2"
            else:
                label = "NONE"
            labels_by_key[(game_id, team, p["player_id"])] = label

    out = []
    for r in rows:
        toi = _toi(r)
        label = labels_by_key[(r["game_id"], r["team"], r["player_id"])]
        out.append({**r, "toi_seconds": toi, "unit_label": label})
    return out


_UNIT_RANK = {"UNIT1": 0, "UNIT2": 1, "NONE": 2}  # tie-break: prefer the higher unit on an exact tie


def _mode(labels: list[str]) -> str | None:
    if not labels:
        return None
    counts = defaultdict(int)
    for l in labels:
        counts[l] += 1
    return max(counts, key=lambda k: (counts[k], -_UNIT_RANK.get(k, 3)))


def classify_role_state(recent_labels: list[str], baseline_labels: list[str], unit_prefix: str,
                         min_recent: int = MIN_RECENT_GAMES_WITH_DATA,
                         min_baseline: int = MIN_BASELINE_GAMES_WITH_DATA) -> dict:
    """Returns {"state": ..., "recent_role": ..., "baseline_role": ...,
    "n_recent": ..., "n_baseline": ...}. `unit_prefix` is "PP" or "PK" --
    used only to relabel the generic UNIT1/UNIT2 internal labels into the
    real PP1/PP2/PK1/PK2 state names for the returned transition string."""
    n_recent, n_baseline = len(recent_labels), len(baseline_labels)
    if n_recent < min_recent or n_baseline < min_baseline:
        return {"state": "ROLE_UNCERTAIN", "recent_role": None, "baseline_role": None,
                "n_recent": n_recent, "n_baseline": n_baseline}

    recent_role = _mode(recent_labels)
    baseline_role = _mode(baseline_labels)
    recent_named = recent_role.replace("UNIT", unit_prefix)
    baseline_named = baseline_role.replace("UNIT", unit_prefix)

    if recent_role == baseline_role:
        state = {"UNIT1": f"STABLE_{unit_prefix}1", "UNIT2": f"STABLE_{unit_prefix}2",
                  "NONE": f"NO_MEANINGFUL_{unit_prefix}"}[recent_role]
    else:
        state = _TRANSITIONS.get((baseline_named, recent_named), "ROLE_UNCERTAIN")

    return {"state": state, "recent_role": recent_named, "baseline_role": baseline_named,
            "n_recent": n_recent, "n_baseline": n_baseline}


def role_change_magnitude(recent_toi: list[float], baseline_toi: list[float],
                           recent_team_toi: list[float], baseline_team_toi: list[float]) -> dict:
    """Continuous companions to the categorical state (Part "ROLE CHANGE
    MAGNITUDE"): delta mean TOI and delta mean share-of-team-TOI,
    recent-window mean minus baseline-window mean. Shrinkage: with fewer
    than MIN_BASELINE_GAMES_WITH_DATA baseline games this returns None
    fields rather than a noisy estimate (same minimum-support philosophy
    as the categorical classifier, not a separate ad hoc rule)."""
    if len(baseline_toi) < MIN_BASELINE_GAMES_WITH_DATA or len(recent_toi) < MIN_RECENT_GAMES_WITH_DATA:
        return {"delta_toi_seconds": None, "delta_share": None}
    recent_mean = statistics.fmean(recent_toi)
    baseline_mean = statistics.fmean(baseline_toi)
    recent_share = [t / tt for t, tt in zip(recent_toi, recent_team_toi) if tt]
    baseline_share = [t / tt for t, tt in zip(baseline_toi, baseline_team_toi) if tt]
    delta_share = (statistics.fmean(recent_share) - statistics.fmean(baseline_share)
                   if recent_share and baseline_share else None)
    return {"delta_toi_seconds": recent_mean - baseline_mean, "delta_share": delta_share}
