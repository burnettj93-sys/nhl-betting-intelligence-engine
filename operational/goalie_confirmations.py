"""
Starting-goalie confirmations: where a CONFIRMED status comes from, and when it counts.

Two sources write to `goalie_status_events` (the one table every consumer reads):
  * the automated Daily Faceoff reader (operational/dailyfaceoff.py; off unless the owner opts in): a "Confirmed" item counts when its cited source is
    identifiable (the team itself, or a recognized beat reporter, with a name and link), it is fresh, and its text names the goalie. Source, link,
    time and basis are stored in the row's `source`;
  * a person's manual confirmation (validate_and_record below): which goalie, which game, where it was seen and when.
A confirmation counts only while it is usable: no older than MAX_AGE_H, not future-dated, not from an automated feed that has stopped refreshing,
and not contradicted by a LATER report from any source naming another goalie (that is a CONFLICT and the gate stays closed). "Likely"/"Expected"
entries and unsupported "Confirmed" entries are stored as EXPECTED and are never confirmations. A later CONFIRMED record for the same game and team
supersedes an earlier one (a changed goalie).
Consumers: the moneyline gate (features/point_in_time.goalie_status), the saves gate (operational/real_prop_orchestrator), the Goalies and Game Detail
pages, and the saves price purchase (`confirmed_games`). Nothing is inferred and no model confirms anything.
"""
from __future__ import annotations

import datetime as dt
import re

from operational import quote_freshness

SOURCE_PREFIX = "manual:"
AUTO_PREFIX = "dailyfaceoff:"
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


MAX_AGE_H = 30.0                    # a confirmation older than this (by the time it was seen/posted) is stale
FUTURE_TOLERANCE_MIN = 5.0


def _z(t: str) -> str:
    return t if t.endswith("Z") else t + "Z"      # the status table stores UTC without a zone suffix


def _at(t: str):
    return dt.datetime.fromisoformat(_z(t).replace("Z", "+00:00"))


def _auto_feed_ok(now: dt.datetime) -> bool:
    try:
        from operational import dailyfaceoff
        return dailyfaceoff.feed_is_fresh(None, now)
    except Exception:  # noqa: BLE001
        return False


def _usable(row, now: dt.datetime, feed_ok: bool) -> tuple[bool, str | None]:
    """May this CONFIRMED row count right now? Stale, future-dated, or automated-with-a-dead-feed rows do not."""
    age_h = (now - _at(row["effective_at_utc"])).total_seconds() / 3600.0
    if age_h > MAX_AGE_H:
        return False, "STALE"
    if age_h < -FUTURE_TOLERANCE_MIN / 60.0:
        return False, "TIMESTAMP_IN_FUTURE"
    if row["source"].startswith(AUTO_PREFIX) and not feed_ok:
        return False, "FEED_NOT_FRESH"
    return True, None


def state_for(conn, game_id, team: str | None, now: dt.datetime | None = None) -> dict:
    """The confirmation state for one game and team: {"state": CONFIRMED|CONFLICT|NONE, "goalie_id", "row", "reason"}.
    CONFIRMED needs a usable CONFIRMED row (manual, or Daily Faceoff with an accepted basis) and no LATER report from any source naming
    a different goalie; a later report naming another goalie is a CONFLICT and the gate stays closed."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if game_id is None or team is None:
        return {"state": "NONE"}
    rows = conn.execute("SELECT id, player_id, status, effective_at_utc, observed_at_utc, source FROM goalie_status_events WHERE game_id = ? AND team_id = ? "
                        "AND (source LIKE 'manual:%' OR source LIKE 'dailyfaceoff:%') ORDER BY observed_at_utc DESC, id DESC", (int(game_id), team)).fetchall()
    feed_ok = _auto_feed_ok(now)
    for i, r in enumerate(rows):
        if r["status"] != "CONFIRMED":
            continue
        ok, why = _usable(r, now, feed_ok)
        if not ok:
            return {"state": "NONE", "reason": why, "row": r}
        later_other = [x for x in rows[:i] if str(x["player_id"]) != str(r["player_id"]) and x["status"] in ("CONFIRMED", "EXPECTED", "CHANGED")]
        if later_other:
            return {"state": "CONFLICT", "reason": f"a later report names another goalie ({later_other[0]['player_id']})", "row": r, "other": later_other[0]}
        return {"state": "CONFIRMED", "goalie_id": str(r["player_id"]), "row": r}
    return {"state": "NONE"}


def lookup(conn, game_id, team: str | None, goalie_id: str, now: dt.datetime | None = None) -> dict | None:
    """The confirmation for this game and team shaped for the pages; None when there is none (stale, unsupported, or Likely/Expected entries are
    not confirmations). A CONFLICT is returned as its own status so a page never shows a goalie as confirmed while a later report disagrees."""
    st = state_for(conn, game_id, team, now)
    if st["state"] == "NONE":
        return None
    row = st["row"]
    if row["source"].startswith(AUTO_PREFIX):
        word, basis, url, name = (row["source"][len(AUTO_PREFIX):].split("|") + ["", "", "", ""])[:4]
        basis_label = {"TEAM_POST": "the team's own post", "RECOGNIZED_REPORTER": "a recognized beat reporter", "TEAM": "the team's own post",
                       "REPORTER": "a beat reporter"}.get(basis, basis)
        where = f"Daily Faceoff, citing {basis_label} {name.strip()} ({url})"
        base = {"source": f"Daily Faceoff — {basis_label}: {name.strip()}", "source_url": url or None, "automated": True, "basis": basis}
        how = "Read automatically from"
    else:
        where = row["source"][len(SOURCE_PREFIX):].split("|")[0]
        base = {"source": f"Manual entry — {where}", "automated": False, "basis": "MANUAL_ENTRY"}
        how = "Recorded by hand: seen at"
    base.update({"checked_at_utc": _z(row["effective_at_utc"]), "recorded_at_utc": _z(row["observed_at_utc"])})
    if st["state"] == "CONFLICT":
        return {**base, "status": "CONFLICT", "note": f"{how} {where} at {base['checked_at_utc']}, but a later report names a different goalie "
                                                      f"({st['other']['player_id']}); treated as unconfirmed until it is resolved."}
    if str(row["player_id"]) == str(goalie_id):
        return {**base, "status": "CONFIRMED", "note": f"{how} {where} at {base['checked_at_utc']}."}
    return {**base, "status": "NOT_STARTING", "note": f"Another goalie ({row['player_id']}) was confirmed for this game ({where})."}


def confirmed_games(conn, now: dt.datetime | None = None) -> dict[str, list[str]]:
    """{game_id: [team,...]} for scheduled games with at least one usable, unconflicted confirmation (drives the saves price purchase)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out: dict[str, list[str]] = {}
    for g in conn.execute("SELECT game_id, home_team, away_team FROM games WHERE game_state = 'SCHEDULED'").fetchall():
        for team in (g["home_team"], g["away_team"]):
            if state_for(conn, g["game_id"], team, now)["state"] == "CONFIRMED":
                out.setdefault(str(g["game_id"]), []).append(team)
    return out


def confirmed_observations(conn, game_id, team_id: str, now: dt.datetime | None = None) -> list:
    """SourceObservation objects for the saves gate's consensus (CONFIRMED only; stale, conflicted and unsupported entries yield none)."""
    from research.goalie_intelligence import source_schema
    st = state_for(conn, game_id, team_id, now)
    if st["state"] != "CONFIRMED":
        return []
    r = st["row"]
    if r["source"].startswith(AUTO_PREFIX):
        basis = (r["source"][len(AUTO_PREFIX):].split("|") + ["", ""])[1]
        raw = f"confirmed ({basis.lower().replace('_', ' ')} via Daily Faceoff)"
    else:
        raw = "confirmed (manual entry)"
    return [source_schema.SourceObservation(game_id=int(game_id), team_id=team_id, goalie_id=str(r["player_id"]), source=r["source"].split("|")[0],
                                            source_status=source_schema.CONFIRMED, raw_status=raw,
                                            source_observed_at_utc=r["effective_at_utc"], ingested_at_utc=r["observed_at_utc"],
                                            source_reference=r["source"])]
