"""
Part 20: corpus-scale GWG audit across all 5,248 games. Pure computation
over the already-normalized events -- 0 network calls (Part 19 already
established there is no independent official GWG field to reconcile
against anywhere in this project's data contract).
"""
from __future__ import annotations

import json
import os

from research.real_nhl_pbp import gwg, gwg_invariants, normalize, raw_archive

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "gwg_audit_results.json")
SEASONS = ("20222023", "20232024", "20242025", "20252026")


def audit_one_game(season: str, game_id: int) -> dict:
    raw = raw_archive.load_raw_pbp(season, game_id)
    events = normalize.normalize_game_events(raw)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]

    result = gwg.derive_gwg(events, game_id, home_id, away_id)
    violations = gwg_invariants.check_all(result, events)

    is_ot_game = any(e.period_type == "OT" for e in events)
    is_so_game = any(e.period_type == "SO" for e in events)
    is_reg_game = not is_ot_game and not is_so_game

    lead_changes = 0
    if result.status == gwg.STATUS_RESOLVED:
        timeline = normalize.reconstruct_statistical_score(events, home_id, away_id)
        prev_leader = None
        for row in timeline:
            if row["home_score"] > row["away_score"]:
                leader = home_id
            elif row["away_score"] > row["home_score"]:
                leader = away_id
            else:
                leader = None
            if leader is not None and prev_leader is not None and leader != prev_leader:
                lead_changes += 1
            if leader is not None:
                prev_leader = leader

    return {
        "game_id": game_id,
        "status": result.status,
        "is_reg_game": is_reg_game,
        "is_ot_game": is_ot_game,
        "is_so_game": is_so_game,
        "empty_net_gwg": result.empty_net if result.status == gwg.STATUS_RESOLVED else False,
        "lead_changes": lead_changes,
        "violations": violations,
    }


def run(seasons: tuple[str, ...] = SEASONS) -> dict:
    per_season = {}
    for season in seasons:
        game_ids = raw_archive.archived_game_ids(season)
        results = [audit_one_game(season, gid) for gid in game_ids]

        reg_with_gwg = sum(1 for r in results if r["is_reg_game"] and r["status"] == gwg.STATUS_RESOLVED)
        ot_with_gwg = sum(1 for r in results if r["is_ot_game"] and r["status"] == gwg.STATUS_RESOLVED)
        so_games = sum(1 for r in results if r["is_so_game"])
        no_gwg = sum(1 for r in results if r["status"] != gwg.STATUS_RESOLVED)
        en_gwg = sum(1 for r in results if r["empty_net_gwg"])
        multi_lead = sum(1 for r in results if r["lead_changes"] >= 2)
        total_violations = sum(len(r["violations"]) for r in results)
        failures = [r for r in results if r["violations"]]

        per_season[season] = {
            "games": len(results),
            "regulation_games_with_gwg": reg_with_gwg,
            "ot_games_with_gwg": ot_with_gwg,
            "shootout_games_no_player_gwg": so_games,
            "games_with_no_gwg": no_gwg,
            "empty_net_gwg_cases": en_gwg,
            "multi_lead_change_games": multi_lead,
            "total_invariant_violations": total_violations,
            "derivation_failures": len(failures),
        }
        assert no_gwg == so_games, f"{season}: every non-SO game should resolve deterministically"

    manifest = {"seasons": list(seasons), "per_season": per_season}
    with open(RESULTS_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = run()
    for season, r in result["per_season"].items():
        print(season, r)
