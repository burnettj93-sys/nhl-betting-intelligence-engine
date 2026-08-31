"""
Special-teams role-transition sprint, residual analysis: for every real
player-game with a classified PP/PK role-transition state
(run_special_teams_role_transitions.py's own output table), computes
`actual outcome - frozen model expectation (mu)` using the REAL,
unmodified frozen marginal engines via
research.player_context_state.marginal_provenance.ContextMarginalContext
-- never a re-fit, never a new model. PIT safety is inherited directly
from that class's own `history_as_of` usage (strictly-before game_date),
not re-implemented here.

PP states are compared against SOG/Goals/Assists/Points residuals
(Part "PP OUTCOMES"). PK states are compared against Blocked Shots
residuals (Part "BLOCKED SHOTS HYPOTHESIS", the named highest-value PK
hypothesis) plus total TOI (a plain descriptive comparison, not a
residual -- there is no frozen TOI model in this project).

Run manually (takes ~2 minutes: builds all 5 frozen engines once, then
computes residuals for the ~189k-row transition table):
    python3 -m research.run_special_teams_role_residuals
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

RESULTS_PATH = REPO_ROOT / "research" / "special_teams_role_residuals_results.json"

PP_PROPS = ("sog", "goals", "assists", "points")
PK_PROP = "blocks"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path)]


def _actual_index(rows: list[dict], field: str) -> dict[tuple, float]:
    return {(r["player_id"], r["game_id"]): r.get(field) for r in rows}


def _icetime_index(rows: list[dict]) -> dict[tuple, float]:
    return {(r["player_id"], r["game_id"]): r.get("icetime_seconds") for r in rows}


def build_residuals() -> dict:
    transitions = _load_jsonl(REPO_ROOT / "research" / "special_teams_role_transitions_table.jsonl")

    sog_rows = _load_jsonl(REPO_ROOT / "research" / "player_sog" / "player_game_sog.jsonl")
    goals_rows = _load_jsonl(REPO_ROOT / "research" / "player_goals" / "player_game_goals.jsonl")
    assists_rows = _load_jsonl(REPO_ROOT / "research" / "player_assists" / "player_game_assists.jsonl")
    points_rows = _load_jsonl(REPO_ROOT / "research" / "player_points" / "player_game_points.jsonl")
    blocks_rows = _load_jsonl(REPO_ROOT / "research" / "player_blocks" / "player_game_blocks.jsonl")

    actual = {
        "sog": _actual_index(sog_rows, "sog"), "goals": _actual_index(goals_rows, "goals"),
        "assists": _actual_index(assists_rows, "assists"), "points": _actual_index(points_rows, "points"),
        "blocks": _actual_index(blocks_rows, "blocks"),
    }
    toi_index = _icetime_index(blocks_rows)

    ctx = ContextMarginalContext()

    # {prop: {state: [residuals]}}
    pp_residuals: dict[str, dict[str, list[float]]] = {p: defaultdict(list) for p in PP_PROPS}
    pk_residuals: dict[str, list[float]] = defaultdict(list)
    pk_toi_by_state: dict[str, list[float]] = defaultdict(list)
    n_predict_none = {p: 0 for p in PP_PROPS + (PK_PROP,)}

    for row in transitions:
        key = (row["player_id"], row["game_id"])
        team, opponent, game_date, season = row.get("team"), row.get("opponent"), row["game_date"], row.get("season")
        if not opponent or season is None:
            continue

        pp_state = row.get("pp_state")
        if pp_state and pp_state != "ROLE_UNCERTAIN":
            for prop in PP_PROPS:
                act = actual[prop].get(key)
                if act is None:
                    continue
                pred = ctx.predict(prop, row["player_id"], team, opponent, game_date, season)
                if pred is None:
                    n_predict_none[prop] += 1
                    continue
                if "mu" in pred:
                    # count-model props (SOG/Goals/Assists): residual is
                    # actual count minus the model's own expected count.
                    pp_residuals[prop][pp_state].append(act - pred["mu"])
                else:
                    # Points has no count model at all -- "EMPIRICAL
                    # BASELINE REMAINS CHAMPION" (no mu exists to
                    # subtract from). Its residual is instead a
                    # PROBABILITY residual at the primary 1+ threshold:
                    # the binary outcome minus the model's own P(1+),
                    # honest to what this specific frozen model actually
                    # produces rather than forcing a count residual it
                    # cannot support.
                    p1 = pred["probs"].get(1)
                    if p1 is not None:
                        pp_residuals[prop][pp_state].append((1.0 if act >= 1 else 0.0) - p1)

        pk_state = row.get("pk_state")
        if pk_state and pk_state != "ROLE_UNCERTAIN":
            act = actual["blocks"].get(key)
            if act is not None:
                pred = ctx.predict("blocks", row["player_id"], team, opponent, game_date, season)
                if pred is None:
                    n_predict_none["blocks"] += 1
                else:
                    pk_residuals[pk_state].append(act - pred["mu"])
            toi = toi_index.get(key)
            if toi is not None:
                pk_toi_by_state[pk_state].append(toi)

    def _summ(vals):
        if not vals:
            return {"n": 0, "mean": None, "stdev": None}
        return {"n": len(vals), "mean": statistics.fmean(vals),
                "stdev": statistics.pstdev(vals) if len(vals) > 1 else 0.0}

    pp_summary = {prop: {state: _summ(vals) for state, vals in by_state.items()}
                  for prop, by_state in pp_residuals.items()}
    pk_summary = {state: _summ(vals) for state, vals in pk_residuals.items()}
    pk_toi_summary = {state: _summ(vals) for state, vals in pk_toi_by_state.items()}

    return {
        "residual_definition_by_prop": {
            "sog": "actual_count - model_mu", "goals": "actual_count - model_mu",
            "assists": "actual_count - model_mu",
            "points": "(1 if actual_points>=1 else 0) - model_P(points>=1) "
                      "-- Points has no count model (empirical baseline champion), "
                      "so this is a probability residual, not a count residual like the other three.",
            "blocks": "actual_count - model_mu",
        },
        "pp_residual_by_state_and_prop": pp_summary,
        "pk_blocked_shots_residual_by_state": pk_summary,
        "pk_total_toi_by_state": pk_toi_summary,
        "n_predict_none_by_prop": n_predict_none,
        "n_transition_rows": len(transitions),
    }


if __name__ == "__main__":
    result = build_residuals()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
