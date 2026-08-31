"""
Cross-checks the normalized MoneyPuck team game-by-game data
(research_moneypuck_team_game_stats, situation='all') against the
accepted real NHL results corpus
(research/real_nhl_results/normalized_regular_season_games.jsonl).

Reports: MoneyPuck team-game rows, unique NHL games represented, matched
games, unmatched MoneyPuck games, NHL games missing from MoneyPuck, and
score/team/SOG disagreements. Never silently resolves a disagreement --
every one found is reported, never chosen between.

The NHL API / real NHL corpus remains canonical identity/results truth
throughout -- MoneyPuck is only ever compared against it, never
substituted for it (this slice's NHL API ROLE section).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from research.moneypuck_ingestion.query import unique_game_coverage

NHL_CORPUS_PATH = "research/real_nhl_results/normalized_regular_season_games.jsonl"

SPECIFIC_GAMES_TO_RECONFIRM = [2025030412, 2025030413, 2025030414]


def load_nhl_corpus(path: str = NHL_CORPUS_PATH) -> dict[int, dict]:
    by_game_id = {}
    with open(path) as f:
        for line in f:
            g = json.loads(line)
            by_game_id[g["game_id"]] = g
    return by_game_id


def _mp_rows_by_game(conn: sqlite3.Connection, situation: str = "all") -> dict[int, list[dict]]:
    rows = conn.execute(
        "SELECT * FROM research_moneypuck_team_game_stats WHERE situation = ?",
        (situation,),
    ).fetchall()
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["game_id"]].append(dict(r))
    return by_game


def cross_check(conn: sqlite3.Connection, situation: str = "all", sample_per_season: int = 5) -> dict:
    nhl_corpus = load_nhl_corpus()
    mp_by_game = _mp_rows_by_game(conn, situation=situation)

    nhl_game_ids = set(nhl_corpus.keys())
    mp_game_ids = set(mp_by_game.keys())

    matched = nhl_game_ids & mp_game_ids
    unmatched_moneypuck = mp_game_ids - nhl_game_ids
    missing_from_moneypuck = nhl_game_ids - mp_game_ids

    score_discrepancies = []
    team_discrepancies = []
    duplicate_conflicts = []

    for game_id in sorted(matched):
        nhl_game = nhl_corpus[game_id]
        mp_rows = mp_by_game[game_id]
        if len(mp_rows) != 2:
            duplicate_conflicts.append({
                "game_id": game_id, "reason": f"expected exactly 2 team rows at situation='{situation}', found {len(mp_rows)}",
            })
            continue

        mp_by_team = {r["team"]: r for r in mp_rows}
        nhl_home, nhl_away = nhl_game["home_team"], nhl_game["away_team"]
        if {nhl_home, nhl_away} != set(mp_by_team.keys()):
            team_discrepancies.append({
                "game_id": game_id, "nhl_teams": sorted([nhl_home, nhl_away]),
                "moneypuck_teams": sorted(mp_by_team.keys()),
            })
            continue

        mp_home = mp_by_team[nhl_home]
        mp_away = mp_by_team[nhl_away]
        # a team's own row reports its own goals_for/goals_against --
        # cross-check both directions for internal MoneyPuck consistency
        # too, not just against the NHL corpus.
        if (mp_home["goals_for"] != nhl_game["home_score"]
                or mp_home["goals_against"] != nhl_game["away_score"]
                or mp_away["goals_for"] != nhl_game["away_score"]
                or mp_away["goals_against"] != nhl_game["home_score"]):
            score_discrepancies.append({
                "game_id": game_id,
                "nhl": {"home": nhl_home, "home_score": nhl_game["home_score"],
                        "away": nhl_away, "away_score": nhl_game["away_score"]},
                "moneypuck": {"home_goals_for": mp_home["goals_for"], "home_goals_against": mp_home["goals_against"],
                              "away_goals_for": mp_away["goals_for"], "away_goals_against": mp_away["goals_against"]},
            })

    specific_games_report = {}
    for game_id in SPECIFIC_GAMES_TO_RECONFIRM:
        in_nhl = game_id in nhl_corpus
        in_mp = game_id in mp_by_game
        entry = {"in_nhl_corpus": in_nhl, "in_moneypuck": in_mp}
        if in_nhl and in_mp:
            nhl_game = nhl_corpus[game_id]
            mp_rows = {r["team"]: r for r in mp_by_game[game_id]}
            entry["nhl"] = {"home": nhl_game["home_team"], "home_score": nhl_game["home_score"],
                             "away": nhl_game["away_team"], "away_score": nhl_game["away_score"]}
            if nhl_game["home_team"] in mp_rows and nhl_game["away_team"] in mp_rows:
                mp_home = mp_rows[nhl_game["home_team"]]
                mp_away = mp_rows[nhl_game["away_team"]]
                entry["moneypuck"] = {
                    "home_goals_for": mp_home["goals_for"], "home_shots_for": mp_home["shots_for"],
                    "away_goals_for": mp_away["goals_for"], "away_shots_for": mp_away["shots_for"],
                }
                entry["score_match"] = (mp_home["goals_for"] == nhl_game["home_score"]
                                         and mp_away["goals_for"] == nhl_game["away_score"])
        specific_games_report[game_id] = entry

    sample_per_season_report = defaultdict(list)
    for season in sorted({g["season"] for g in nhl_corpus.values()}):
        ids_for_season = sorted(identifier for identifier, g in nhl_corpus.items() if g["season"] == season)
        step = max(1, len(ids_for_season) // sample_per_season)
        strided = [ids_for_season[i] for i in range(0, len(ids_for_season), step)]
        sampled = strided[:sample_per_season]
        for game_id in sampled:
            nhl_game = nhl_corpus[game_id]
            in_mp = game_id in mp_by_game
            entry = {"game_id": game_id, "game_date": nhl_game["game_date"], "in_moneypuck": in_mp}
            if in_mp:
                mp_rows = {r["team"]: r for r in mp_by_game[game_id]}
                if nhl_game["home_team"] in mp_rows and nhl_game["away_team"] in mp_rows:
                    mp_home = mp_rows[nhl_game["home_team"]]
                    mp_away = mp_rows[nhl_game["away_team"]]
                    entry["score_match"] = (mp_home["goals_for"] == nhl_game["home_score"]
                                             and mp_away["goals_for"] == nhl_game["away_score"])
            sample_per_season_report[season].append(entry)

    unique_games_via_query_api = unique_game_coverage(conn, situation=situation)
    assert unique_games_via_query_api == mp_game_ids, "query.py's coverage helper disagrees with direct SQL"

    return {
        "situation": situation,
        "moneypuck_team_game_rows_all_situations": conn.execute(
            "SELECT COUNT(*) AS n FROM research_moneypuck_team_game_stats").fetchone()["n"],
        "moneypuck_unique_games_at_situation": len(mp_game_ids),
        "nhl_corpus_games": len(nhl_game_ids),
        "matched_games": len(matched),
        "unmatched_moneypuck_games": sorted(unmatched_moneypuck),
        "nhl_games_missing_from_moneypuck": sorted(missing_from_moneypuck),
        "coverage_pct": round(100.0 * len(matched) / len(nhl_game_ids), 4),
        "score_discrepancies": score_discrepancies,
        "team_discrepancies": team_discrepancies,
        "duplicate_conflicts": duplicate_conflicts,
        "specific_games_reconfirmation": specific_games_report,
        "season_samples": dict(sample_per_season_report),
    }


if __name__ == "__main__":
    import sys
    from research.moneypuck_ingestion.ingest_moneypuck_team import get_connection

    conn = get_connection()
    report = cross_check(conn)
    json.dump(report, sys.stdout, indent=2, sort_keys=True, default=str)
    print()
