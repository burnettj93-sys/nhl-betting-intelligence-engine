"""
Special-teams role-transition sprint: time-to-adapt analysis for the
cleanest, most robust signal found in the residual pass
(PROMOTED_PP2_TO_PP1 -> SOG, and REMOVED_FROM_PK -> Blocked Shots, its
PK mirror). For each player's FIRST occurrence of the named transition
state in their real chronological game sequence, tracks the SOG (or
Blocks) residual at that game and the player's next 4 real games, to see
whether/how fast the frozen model's own rolling features catch up to the
new role -- Part "TIME-TO-ADAPT" / "ROLE-CHANGE OVERLAY POSSIBILITY".

Research only -- builds no overlay, promotes nothing.

Run manually:
    python3 -m research.run_special_teams_time_to_adapt
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.player_context_state.marginal_provenance import ContextMarginalContext

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_time_to_adapt_results.json"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def _first_onsets(transitions: list[dict], state_key: str, target_state: str) -> dict[str, dict]:
    """One row per player: the FIRST game_id (chronologically) where
    `state_key` == target_state, provided the player's IMMEDIATELY
    PRECEDING classified state (for that same state_key) was something
    else -- i.e. a genuine onset, not a run already in progress from an
    even earlier, already-counted transition."""
    by_player = defaultdict(list)
    for r in transitions:
        by_player[r["player_id"]].append(r)
    for pid in by_player:
        by_player[pid].sort(key=lambda r: r["game_date"])

    onsets = {}
    for pid, games in by_player.items():
        prev_state = None
        for g in games:
            state = g.get(state_key)
            if state == target_state and prev_state is not None and prev_state != target_state:
                onsets[pid] = g
                break
            prev_state = state
    return onsets


def build() -> dict:
    transitions = _load_jsonl(REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl")
    by_player_all = defaultdict(list)
    for r in transitions:
        by_player_all[r["player_id"]].append(r)
    for pid in by_player_all:
        by_player_all[pid].sort(key=lambda r: r["game_date"])
    player_index = {pid: {g["game_id"]: i for i, g in enumerate(games)} for pid, games in by_player_all.items()}

    sog_rows = _load_jsonl(REPO_ROOT / "research" / "player_sog" / "player_game_sog.jsonl")
    blocks_rows = _load_jsonl(REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl")
    sog_actual = {(r["player_id"], r["game_id"]): r["sog"] for r in sog_rows}
    blocks_actual = {(r["player_id"], r["game_id"]): r["blocks"] for r in blocks_rows}

    ctx = ContextMarginalContext()

    def _residual_curve(onsets: dict, prop: str, actual_index: dict, offsets=range(5)) -> dict:
        by_offset = {o: [] for o in offsets}
        for pid, onset_row in onsets.items():
            games = by_player_all[pid]
            onset_idx = player_index[pid][onset_row["game_id"]]
            for o in offsets:
                idx = onset_idx + o
                if idx >= len(games):
                    continue
                g = games[idx]
                key = (pid, g["game_id"])
                act = actual_index.get(key)
                if act is None:
                    continue
                pred = ctx.predict(prop, pid, g["team"], g.get("opponent"), g["game_date"], g.get("season"))
                if pred is None or "mu" not in pred:
                    continue
                by_offset[o].append(act - pred["mu"])
        return {str(o): {"n": len(v), "mean": statistics.fmean(v) if v else None,
                          "stdev": statistics.pstdev(v) if len(v) > 1 else None}
                for o, v in by_offset.items()}

    pp_promo_onsets = _first_onsets(transitions, "pp_state", "PROMOTED_PP2_TO_PP1")
    pp_removed_onsets = _first_onsets(transitions, "pp_state", "REMOVED_FROM_PP")
    pk_removed_onsets = _first_onsets(transitions, "pk_state", "REMOVED_FROM_PK")
    pk_added_onsets = _first_onsets(transitions, "pk_state", "ADDED_TO_PK1")

    return {
        "n_unique_onsets": {
            "PROMOTED_PP2_TO_PP1": len(pp_promo_onsets), "REMOVED_FROM_PP": len(pp_removed_onsets),
            "REMOVED_FROM_PK": len(pk_removed_onsets), "ADDED_TO_PK1": len(pk_added_onsets),
        },
        "sog_residual_curve_after_PROMOTED_PP2_TO_PP1": _residual_curve(pp_promo_onsets, "sog", sog_actual),
        "sog_residual_curve_after_REMOVED_FROM_PP": _residual_curve(pp_removed_onsets, "sog", sog_actual),
        "blocks_residual_curve_after_REMOVED_FROM_PK": _residual_curve(pk_removed_onsets, "blocks", blocks_actual),
        "blocks_residual_curve_after_ADDED_TO_PK1": _residual_curve(pk_added_onsets, "blocks", blocks_actual),
    }


if __name__ == "__main__":
    result = build()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
