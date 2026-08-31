"""
Special-teams role-transition sprint: builds PP/PK unit labels for every
real player-game, classifies each player's PIT-safe role-transition
state heading into each of their games (recent 3 vs baseline 8, strictly
before that game), and reports sample support (Part "SAMPLE SUPPORT" /
"STAR / PLAYER CONCENTRATION"). Residual analysis against the frozen
marginal models is a separate, heavier second pass
(run_special_teams_role_residuals.py) so this script stays fast to
re-run on its own.

Run manually:
    python3 -m research.run_special_teams_role_transitions
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.period_event_timing import special_teams_roles as sr

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_transitions_results.json"
TRANSITIONS_TABLE_PATH = REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def build_player_role_timeline(labeled_rows: list[dict], unit_prefix: str) -> dict[str, list[dict]]:
    by_player = defaultdict(list)
    for r in labeled_rows:
        by_player[r["player_id"]].append(r)
    for pid in by_player:
        by_player[pid].sort(key=lambda r: r["game_date"])
    return by_player


def classify_all(by_player: dict[str, list[dict]], unit_prefix: str, team_toi_lookup: dict) -> list[dict]:
    out = []
    recent_n, baseline_n = sr.RECENT_GAMES, sr.BASELINE_GAMES
    for player_id, games in by_player.items():
        for i, g in enumerate(games):
            recent_slice = games[max(0, i - recent_n):i]
            baseline_slice = games[max(0, i - recent_n - baseline_n):max(0, i - recent_n)]
            recent_labels = [r["unit_label"] for r in recent_slice]
            baseline_labels = [r["unit_label"] for r in baseline_slice]
            result = sr.classify_role_state(recent_labels, baseline_labels, unit_prefix)

            recent_toi = [r["toi_seconds"] for r in recent_slice]
            baseline_toi = [r["toi_seconds"] for r in baseline_slice]
            recent_team_toi = [team_toi_lookup.get((r["game_id"], r["team"]), 0.0) for r in recent_slice]
            baseline_team_toi = [team_toi_lookup.get((r["game_id"], r["team"]), 0.0) for r in baseline_slice]
            magnitude = sr.role_change_magnitude(recent_toi, baseline_toi, recent_team_toi, baseline_team_toi)

            out.append({
                "player_id": player_id, "game_id": g["game_id"], "game_date": g["game_date"],
                "team": g["team"], "season": g.get("season"), "opponent": g.get("opponent"),
                f"{unit_prefix.lower()}_state": result["state"],
                f"{unit_prefix.lower()}_recent_role": result["recent_role"],
                f"{unit_prefix.lower()}_baseline_role": result["baseline_role"],
                f"{unit_prefix.lower()}_n_recent": result["n_recent"],
                f"{unit_prefix.lower()}_n_baseline": result["n_baseline"],
                f"{unit_prefix.lower()}_delta_toi_seconds": magnitude["delta_toi_seconds"],
                f"{unit_prefix.lower()}_delta_share": magnitude["delta_share"],
            })
    return out


def build_all() -> dict:
    sog_rows = _load_jsonl(REPO_ROOT / "research" / "player_sog" / "player_game_sog.jsonl")
    blocks_rows = _load_jsonl(REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl")
    team_game = _load_jsonl(REPO_ROOT / "research" / "team_game_special_teams_table.jsonl")
    team_pp_toi = {(r["game_id"], r["team"]): r["pp_seconds"] for r in team_game}
    team_pk_toi = {(r["game_id"], r["team"]): r["sh_seconds"] for r in team_game}

    pp_labeled = sr.build_game_unit_labels(sog_rows, ("pp", "icetime_seconds"), "PP")
    pk_labeled = sr.build_game_unit_labels(blocks_rows, ("pk", "icetime_seconds"), "PK")

    pp_by_player = build_player_role_timeline(pp_labeled, "PP")
    pk_by_player = build_player_role_timeline(pk_labeled, "PK")

    pp_classified = classify_all(pp_by_player, "PP", team_pp_toi)
    pk_classified = classify_all(pk_by_player, "PK", team_pk_toi)

    pp_by_key = {(r["player_id"], r["game_id"]): r for r in pp_classified}
    pk_by_key = {(r["player_id"], r["game_id"]): r for r in pk_classified}
    all_keys = set(pp_by_key) | set(pk_by_key)

    merged = []
    for key in all_keys:
        row = {"player_id": key[0], "game_id": key[1]}
        if key in pp_by_key:
            row.update(pp_by_key[key])
        if key in pk_by_key:
            row.update({k: v for k, v in pk_by_key[key].items() if k not in ("player_id", "game_id")})
        merged.append(row)

    with open(TRANSITIONS_TABLE_PATH, "w") as f:
        for row in merged:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _sample_support(rows, state_key):
        counts = defaultdict(lambda: {"n": 0, "unique_players": set(), "unique_teams": set()})
        for r in rows:
            state = r.get(state_key)
            if state is None:
                continue
            counts[state]["n"] += 1
            counts[state]["unique_players"].add(r["player_id"])
            counts[state]["unique_teams"].add(r["team"])
        return {s: {"n": v["n"], "unique_players": len(v["unique_players"]), "unique_teams": len(v["unique_teams"])}
                for s, v in counts.items()}

    def _top10_contribution(rows, state_key, target_state):
        from collections import Counter
        c = Counter(r["player_id"] for r in rows if r.get(state_key) == target_state)
        total = sum(c.values())
        top10 = sum(n for _, n in c.most_common(10))
        return {"total_instances": total, "top10_player_instances": top10,
                "top10_share": top10 / total if total else None, "unique_players": len(c)}

    pp_support = _sample_support(pp_classified, "pp_state")
    pk_support = _sample_support(pk_classified, "pk_state")

    concentration = {}
    for state in ("PROMOTED_PP2_TO_PP1", "ADDED_TO_PP1", "ADDED_TO_PP2", "DEMOTED_PP1_TO_PP2", "REMOVED_FROM_PP"):
        concentration[state] = _top10_contribution(pp_classified, "pp_state", state)
    for state in ("PROMOTED_PK2_TO_PK1", "ADDED_TO_PK1", "ADDED_TO_PK2", "DEMOTED_PK1_TO_PK2", "REMOVED_FROM_PK"):
        concentration[state] = _top10_contribution(pk_classified, "pk_state", state)

    return {
        "n_pp_labeled_player_games": len(pp_labeled), "n_pk_labeled_player_games": len(pk_labeled),
        "n_pp_classified": len(pp_classified), "n_pk_classified": len(pk_classified),
        "n_merged_rows": len(merged),
        "pp_state_sample_support": pp_support, "pk_state_sample_support": pk_support,
        "top10_player_concentration_by_state": concentration,
    }


if __name__ == "__main__":
    result = build_all()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps(result, indent=2, sort_keys=True))
