"""
Manual starting-goalie confirmations.

No starting-goalie feed can be used automatically (see docs/STARTING_GOALIE_SOURCE_AUDIT.md), so a CONFIRMED status exists only when a
person looks at a published source (the team's or league's announcement, a broadcast, a morning-skate report) and records:
which goalie, for which game, where they saw it and when. That record is stored where the engine already reads goalie status
(`goalie_status_events`, source `manual:<where seen>`), so every consumer sees one truth:
  * the moneyline gate (features/point_in_time.goalie_status) reads it;
  * the saves gate (operational/real_prop_orchestrator._real_external_starter_observations) turns it into a CONFIRMED observation;
  * the Goalies and Game Detail pages show it with its source and time.
Nothing is inferred and nothing is confirmed by a model: the start-chance estimates never become a confirmation. A later record for
the same game and team supersedes an earlier one (a changed goalie).
"""
from __future__ import annotations

import datetime as dt
import re

from operational import quote_freshness

SOURCE_PREFIX = "manual:"
SCHEMA = 1
LABEL = "goalie-confirmation"


def validate_and_record(conn, doc: dict, *, now: dt.datetime, source_ref: str) -> dict:
    """Checks one GOALIE_CONFIRMATION document against the schedule and roster and stores it."""
    if not isinstance(doc, dict) or doc.get("type") != "GOALIE_CONFIRMATION" or doc.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "not a GOALIE_CONFIRMATION document"}
    cid = doc.get("confirmation_id")
    if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", cid):
        return {"status": "REJECTED", "reason": "confirmation_id malformed"}
    game_id, team, goalie_id = str(doc.get("game_id") or ""), str(doc.get("team") or ""), str(doc.get("goalie_id") or "")
    where = (doc.get("where_seen") or "").strip()
    if not where:
        return {"status": "REJECTED", "reason": "say where the confirmation was seen"}
    seen = quote_freshness.parse_utc(doc.get("seen_at_utc"))
    if seen is None or (seen - now).total_seconds() > quote_freshness.FUTURE_TOLERANCE_S:
        return {"status": "REJECTED", "reason": "the time seen is missing, malformed or in the future"}
    game = conn.execute("SELECT game_state, scheduled_start_utc, home_team, away_team FROM games WHERE game_id = ?", (game_id,)).fetchone()
    if game is None or team not in (game["home_team"], game["away_team"]):
        return {"status": "REJECTED", "reason": "the game and team do not match the schedule"}
    start = quote_freshness.parse_utc(game["scheduled_start_utc"])
    if game["game_state"] != "SCHEDULED" or (start is not None and start <= now):
        return {"status": "REJECTED", "reason": "the game has started or is over; a confirmation is only recorded before puck drop"}
    player = conn.execute("SELECT position FROM players WHERE player_id = ?", (goalie_id,)).fetchone()
    if player is None or player["position"] != "G":
        return {"status": "REJECTED", "reason": "that player is not a known goalie"}
    ref = f"{SOURCE_PREFIX}{where[:100]}|{cid}"
    if conn.execute("SELECT 1 FROM goalie_status_events WHERE source = ? AND game_id = ?", (ref, int(game_id))).fetchone():
        return {"status": "ALREADY_RECORDED", "confirmation_id": cid}
    from ingest.nhl_api import record_goalie_status
    record_goalie_status(conn, int(game_id), team, goalie_id, "CONFIRMED", quote_freshness.iso_z(seen), quote_freshness.iso_z(now), ref)
    conn.commit()
    return {"status": "RECORDED", "confirmation_id": cid}


def lookup(conn, game_id, team: str | None, goalie_id: str) -> dict | None:
    """The newest manual confirmation for this game and team, shaped for the pages; None when there is none."""
    if game_id is None or team is None:
        return None
    row = conn.execute("SELECT player_id, status, effective_at_utc, observed_at_utc, source FROM goalie_status_events WHERE game_id = ? AND team_id = ? "
                       "AND source LIKE 'manual:%' ORDER BY observed_at_utc DESC, id DESC LIMIT 1", (int(game_id), team)).fetchone()
    if row is None:
        return None
    where = row["source"][len(SOURCE_PREFIX):].split("|")[0]
    z = lambda t: t if t.endswith("Z") else t + "Z"      # the status table stores UTC without a zone suffix  # noqa: E731
    base = {"source": f"Manual entry — {where}", "checked_at_utc": z(row["effective_at_utc"]), "recorded_at_utc": z(row["observed_at_utc"])}
    if str(row["player_id"]) == str(goalie_id):
        return {**base, "status": "CONFIRMED", "note": f"Recorded by hand: seen at {where} at {base['checked_at_utc']}."}
    return {**base, "status": "NOT_STARTING", "note": f"Another goalie ({row['player_id']}) was confirmed for this game (seen at {where})."}


def confirmed_observations(conn, game_id, team_id: str) -> list:
    """SourceObservation objects for the saves gate's consensus (CONFIRMED only)."""
    from research.goalie_intelligence import source_schema
    rows = conn.execute("SELECT player_id, effective_at_utc, observed_at_utc, source FROM goalie_status_events WHERE game_id = ? AND team_id = ? "
                        "AND status = 'CONFIRMED' AND source LIKE 'manual:%' ORDER BY observed_at_utc DESC, id DESC LIMIT 1", (int(game_id), team_id)).fetchall()
    return [source_schema.SourceObservation(game_id=int(game_id), team_id=team_id, goalie_id=str(r["player_id"]), source=r["source"].split("|")[0],
                                            source_status=source_schema.CONFIRMED, raw_status="confirmed (manual entry)",
                                            source_observed_at_utc=r["effective_at_utc"], ingested_at_utc=r["observed_at_utc"],
                                            source_reference=r["source"]) for r in rows]
