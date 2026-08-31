"""
Parts 5, 8, 9, 10: corpus-scale goalie-tenure and period-save audit across
the complete 5,248-game 4-season corpus. Part 8 explicitly asks for
full-game save reconciliation "for the complete four-season corpus where
possible" -- this is a NEW correctness question (does this slice's new
utility work), not a re-confirmation of an already-established pattern,
so the full corpus is fetched rather than a sample (unlike the earlier
blocked-shot re-confirmation, which was proportionate at n=30/season).
Paced identically to every other bulk fetch in this project (0.3s between
requests) -- ~26 minutes for 5,248 boxscore calls, a one-time cost for a
definitive validation, not repeated re-fetching of data already in hand.
"""
from __future__ import annotations

import json
import os
import time

import requests

from research.real_nhl_pbp import goalie_tenure, normalize, period_saves, raw_archive
from research.real_nhl_pbp.build_pbp_season import season_game_ids

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "goalie_tenure_audit_results.json")
SEASONS = (20222023, 20232024, 20242025, 20252026)


def audit_one_game(season: str, game_id: int, session) -> dict:
    raw = raw_archive.load_raw_pbp(season, game_id)
    events = normalize.normalize_game_events(raw)
    home_id, away_id = raw["homeTeam"]["id"], raw["awayTeam"]["id"]

    tenure = goalie_tenure.reconstruct_goalie_tenure(events, home_id, away_id)
    all_intervals = tenure[home_id] + tenure[away_id]
    mid_changes = (goalie_tenure.mid_period_changes(tenure[home_id])
                   + goalie_tenure.mid_period_changes(tenure[away_id]))
    distinct_goalies = {t: {iv.goalie_id for iv in ivs if iv.goalie_id is not None}
                        for t, ivs in tenure.items()}
    returns = [iv for iv in all_intervals if iv.interval_type == "RETURN_AFTER_EMPTY_NET"]
    reliefs = [iv for iv in all_intervals if iv.interval_type == "RELIEF"]

    coherence_violations = period_saves.check_period_sums_equal_full_game(events)

    box_mismatches = None
    box_error = None
    try:
        resp = session.get(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore", timeout=20)
        resp.raise_for_status()
        boxscore = resp.json()
        box_mismatches = period_saves.reconcile_full_game_saves(events, boxscore)
    except Exception as exc:  # noqa: BLE001
        box_error = str(exc)

    return {
        "game_id": game_id,
        "num_goalies_home": len(distinct_goalies[home_id]),
        "num_goalies_away": len(distinct_goalies[away_id]),
        "has_goalie_change": len(reliefs) > 0,
        "has_mid_period_change": len(mid_changes) > 0,
        "num_mid_period_changes": len(mid_changes),
        "num_returns_after_empty_net": len(returns),
        "coherence_violations": coherence_violations,
        "boxscore_mismatches": box_mismatches,
        "boxscore_error": box_error,
    }


def run(seasons: tuple[int, ...] = SEASONS, sleep_seconds: float = 0.3) -> dict:
    session = requests.Session()
    per_season = {}
    for season in seasons:
        season_str = str(season)
        game_ids = raw_archive.archived_game_ids(season_str)
        results = []
        for gid in game_ids:
            results.append(audit_one_game(season_str, gid, session))
            time.sleep(sleep_seconds)

        games_with_change = sum(1 for r in results if r["has_goalie_change"])
        games_with_mid_change = sum(1 for r in results if r["has_mid_period_change"])
        games_multi_goalie = sum(1 for r in results if r["num_goalies_home"] > 1 or r["num_goalies_away"] > 1)
        games_3plus = sum(1 for r in results if r["num_goalies_home"] >= 3 or r["num_goalies_away"] >= 3)
        total_coherence_violations = sum(len(r["coherence_violations"]) for r in results)
        box_ok = [r for r in results if r["boxscore_error"] is None]
        exact_matches = sum(1 for r in box_ok if not r["boxscore_mismatches"])
        total_mismatched_goalie_games = sum(1 for r in box_ok if r["boxscore_mismatches"])
        all_diffs = [m["diff"] for r in box_ok for m in (r["boxscore_mismatches"] or [])
                     if "diff" in m]

        per_season[season_str] = {
            "games": len(results),
            "games_with_any_goalie_change": games_with_change,
            "games_with_mid_period_change": games_with_mid_change,
            "games_multi_goalie": games_multi_goalie,
            "games_3plus_goalies": games_3plus,
            "total_returns_after_empty_net": sum(r["num_returns_after_empty_net"] for r in results),
            "total_coherence_violations": total_coherence_violations,
            "boxscore_fetch_failures": len(results) - len(box_ok),
            "games_exact_save_match": exact_matches,
            "games_save_mismatch": total_mismatched_goalie_games,
            "largest_save_discrepancy": max((abs(d) for d in all_diffs), default=0),
            "mismatch_rate_pct": round(100.0 * total_mismatched_goalie_games / len(box_ok), 3) if box_ok else None,
        }
        with open(RESULTS_PATH.replace(".json", f"_{season}_detail.json"), "w") as f:
            json.dump(results, f, indent=2)

    manifest = {"seasons": [str(s) for s in seasons], "per_season": per_season}
    with open(RESULTS_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = run()
    for season, r in result["per_season"].items():
        print(season, r)
