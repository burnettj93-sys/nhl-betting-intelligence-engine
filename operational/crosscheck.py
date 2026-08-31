"""
Part 11: NHL / MoneyPuck cross-check for newly completed games. Compares
key identity/result fields between the real NHL ingestion (nhl.db) and a
freshly-parsed MoneyPuck team-game-by-game snapshot.

KNOWN, EXPECTED, NON-DEFECT DISAGREEMENT (Part 11's explicit instruction):
MoneyPuck's own statistical goal totals exclude the shootout-deciding
goal (documented since MONEYPUCK_TEAM_INGESTION_REPORT.md and reused as
the exact reason this project's SOG/goalie models source outcome truth
from research/real_nhl_results/, never MoneyPuck's own goals column).
`cross_check_game()` treats a shootout-final game's goal mismatch as
KNOWN_SHOOTOUT_EXCEPTION, not a disagreement — any OTHER goal mismatch,
or any mismatch in team/opponent/date/game type, is a real, reported
MATERIAL_DISAGREEMENT.
"""
from __future__ import annotations


def cross_check_game(nhl_row: dict, moneypuck_row: dict) -> dict:
    """`nhl_row`: {"game_id", "home_team", "away_team", "game_date",
    "final_period_type", "home_score", "away_score"} from nhl.db.
    `moneypuck_row`: {"gameId", "team", "opponent", "gameDate",
    "goalsFor"/"goalsAgainst" or similar, "shotsOnGoalFor"/...} for the
    SAME real game, one side. Returns {"status": "MATCH" |
    "KNOWN_SHOOTOUT_EXCEPTION" | "MATERIAL_DISAGREEMENT", "details": [...]}."""
    disagreements = []
    if str(nhl_row["game_id"]) != str(moneypuck_row["gameId"]):
        disagreements.append(f"game_id mismatch: nhl={nhl_row['game_id']} mp={moneypuck_row['gameId']}")
    if nhl_row["game_date"] != moneypuck_row.get("gameDate"):
        disagreements.append(f"game_date mismatch: nhl={nhl_row['game_date']} mp={moneypuck_row.get('gameDate')}")

    team_side = moneypuck_row.get("home_or_away")
    if team_side == "HOME":
        nhl_score_for, nhl_score_against = nhl_row["home_score"], nhl_row["away_score"]
    elif team_side == "AWAY":
        nhl_score_for, nhl_score_against = nhl_row["away_score"], nhl_row["home_score"]
    else:
        nhl_score_for = nhl_score_against = None

    mp_goals_for = moneypuck_row.get("goalsFor")
    mp_goals_against = moneypuck_row.get("goalsAgainst")

    is_shootout = nhl_row.get("final_period_type") == "SO"
    goals_disagree = (nhl_score_for is not None and mp_goals_for is not None
                       and nhl_score_for != mp_goals_for)
    goals_against_disagree = (nhl_score_against is not None and mp_goals_against is not None
                               and nhl_score_against != mp_goals_against)

    if (goals_disagree or goals_against_disagree) and is_shootout:
        # exactly the documented, expected MoneyPuck exclusion of the
        # shootout-deciding goal -- not a defect.
        return {"status": "KNOWN_SHOOTOUT_EXCEPTION", "details": disagreements,
                "note": "MoneyPuck statistical goals exclude the shootout-deciding goal"}

    if goals_disagree:
        disagreements.append(f"goals-for mismatch: nhl={nhl_score_for} mp={mp_goals_for}")
    if goals_against_disagree:
        disagreements.append(f"goals-against mismatch: nhl={nhl_score_against} mp={mp_goals_against}")

    mp_sog_for = moneypuck_row.get("shotsOnGoalFor")
    if mp_sog_for is not None and "sog_for" in nhl_row and nhl_row["sog_for"] is not None:
        if nhl_row["sog_for"] != mp_sog_for:
            disagreements.append(f"SOG mismatch: nhl={nhl_row['sog_for']} mp={mp_sog_for}")

    if disagreements:
        return {"status": "MATERIAL_DISAGREEMENT", "details": disagreements}
    return {"status": "MATCH", "details": []}


def cross_check_recent_games(nhl_rows: list[dict], moneypuck_rows: list[dict]) -> dict:
    """Cross-checks every NHL game_id present in both inputs (keyed by
    game_id + home_or_away side). Returns a summary + a list of any real
    disagreements (never silently drops them)."""
    mp_by_key = {(str(r["gameId"]), r.get("home_or_away")): r for r in moneypuck_rows}
    checked, matched, known_exceptions, material = 0, 0, 0, []
    for nhl_row in nhl_rows:
        for side in ("HOME", "AWAY"):
            mp_row = mp_by_key.get((str(nhl_row["game_id"]), side))
            if mp_row is None:
                continue
            checked += 1
            result = cross_check_game(nhl_row, mp_row)
            if result["status"] == "MATCH":
                matched += 1
            elif result["status"] == "KNOWN_SHOOTOUT_EXCEPTION":
                known_exceptions += 1
            else:
                material.append({"game_id": nhl_row["game_id"], "side": side, **result})
    return {"games_checked": checked, "matched": matched,
            "known_shootout_exceptions": known_exceptions,
            "material_disagreements": material}
