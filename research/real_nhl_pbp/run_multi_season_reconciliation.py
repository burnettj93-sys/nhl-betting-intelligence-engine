"""
Parts 16-19: per-season reconciliation sample. Reuses the exact single-
season pilot's stratified-sample methodology (build_pbp_pilot.select_
pilot_games(), already parametrized by season) rather than reconciling the
full 5,248-game corpus against /boxscore -- a full-corpus boxscore fetch
would be another 5,248 live API calls purely for a re-confirmation of a
pattern the single-season pilot already established at n=30; Part 6's
"do not hammer the NHL API" and Part 16/17's "where boxscore comparison is
available / feasible" wording both point at a proportionate sample, not
full coverage. 30 games/season (120 total) matches the original pilot's own
scale exactly.
"""
from __future__ import annotations

import json
import os

import requests

from research.real_nhl_pbp import invariants as inv
from research.real_nhl_pbp import normalize, raw_archive, reconcile
from research.real_nhl_pbp.build_pbp_pilot import select_pilot_games

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "multi_season_reconciliation_results.json")

SEASONS = (20222023, 20232024, 20242025, 20252026)


def reconcile_one_game(season: str, game_id: int, session) -> dict:
    raw = raw_archive.load_raw_pbp(season, game_id)
    game = normalize.normalize_game(raw, raw_sha256="", source_url="", retrieved_at_utc="")
    events = normalize.normalize_game_events(raw)

    violations = {}
    for check_name in inv.ALL_CHECKS:
        fn = getattr(inv, check_name)
        if check_name == "check_player_goals_reconcile_with_team_goals":
            result = fn(events, game.home_team_id, game.away_team_id)
        elif check_name == "check_final_score_reconciles":
            result = fn(events, game.home_team_id, game.away_team_id,
                        raw["homeTeam"]["score"], raw["awayTeam"]["score"], game.final_period_type)
        else:
            result = fn(events)
        if result:
            violations[check_name] = result

    boxscore = reconcile.fetch_boxscore(session, game_id)
    mismatches = reconcile.reconcile_game(events, boxscore)

    pbp_blocks = sum(1 for e in events if e.event_type == "blocked-shot")
    box_blocks = sum(
        p.get("blockedShots", 0)
        for side in ("awayTeam", "homeTeam")
        for grp in ("forwards", "defense")
        for p in boxscore["playerByGameStats"][side][grp]
    )

    return {
        "game_id": game_id,
        "invariant_violations": violations,
        "boxscore_mismatches": mismatches,
        "unexplained_mismatches": reconcile.unexplained_mismatches(mismatches),
        "pbp_blocks": pbp_blocks,
        "boxscore_blocks": box_blocks,
    }


def run(seasons: tuple[int, ...] = SEASONS) -> dict:
    session = requests.Session()
    per_season = {}
    for season in seasons:
        games = select_pilot_games(season)
        results = [reconcile_one_game(str(season), g["game_id"], session) for g in games]

        total_pbp_blocks = sum(r["pbp_blocks"] for r in results)
        total_box_blocks = sum(r["boxscore_blocks"] for r in results)
        total_unexplained = sum(len(r["unexplained_mismatches"]) for r in results)
        total_invariant_violations = sum(len(r["invariant_violations"]) for r in results)

        per_season[str(season)] = {
            "games_sampled": len(results),
            "invariant_violations_total": total_invariant_violations,
            "unexplained_mismatches_total": total_unexplained,
            "pbp_blocks": total_pbp_blocks,
            "boxscore_blocks": total_box_blocks,
            "block_gap_absolute": total_pbp_blocks - total_box_blocks,
            "block_gap_relative_pct": round(100.0 * (total_pbp_blocks - total_box_blocks) / total_box_blocks, 2)
            if total_box_blocks else None,
            "per_game": results,
        }

    manifest = {
        "seasons": list(seasons),
        "per_season": per_season,
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = run()
    for season, r in result["per_season"].items():
        print(season, {k: v for k, v in r.items() if k != "per_game"})
