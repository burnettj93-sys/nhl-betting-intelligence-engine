"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 3: the
outcome-resolution layer the prospective ledger has always had schema
support for but never had code to drive. Resolves real prediction outcomes
from OFFICIAL, ALREADY-INGESTED NHL data -- this module is READ-ONLY over
nhl.db (games / game_result_events / player_game_stats / goalie_game_stats,
all populated by the existing, real ingest/nhl_api.py + operational/
nhl_sync.py daily cycle). It never fetches from the network itself and
never re-derives a boxscore a second, parallel way -- if the game or
player's official data isn't there yet, it fails closed rather than
guessing (Part 18/19).

`nhl.db` / `db.py` / `schema.sql` were declared a frozen production
boundary in a prior sprint (verified via `git status` there). This module
therefore ONLY READS from that schema via db.py's existing get_conn() --
it never adds a column or migrates nhl.db. Two real markets (Team SOG,
Blocked Shots) genuinely cannot be resolved from what nhl.db currently
stores, for two DIFFERENT reasons documented on each resolver function
below -- both fail closed with an explicit, distinct status rather than
silently approximating from a field that was never cross-validated
against the model's own canonical training source (Part 13's explicit
warning about the real, growing PBP-vs-boxscore blocked-shot drift).

RESOLVER_VERSION is stamped into every resolution (Part 24's audit trail)
so a future change to this module's own logic is traceable against
predictions it already resolved.
"""
from __future__ import annotations

import sqlite3

RESOLVER_VERSION = "outcome_resolver_v1"

# ---- fail-closed / terminal statuses (never a guess) ----
RESOLVED = "RESOLVED"
GAME_NOT_FINAL = "GAME_NOT_FINAL"
UNSUPPORTED_SETTLEMENT_MARKET = "UNSUPPORTED_SETTLEMENT_MARKET"
PLAYER_DID_NOT_DRESS = "PLAYER_DID_NOT_DRESS"
GOALIE_DID_NOT_PLAY = "GOALIE_DID_NOT_PLAY"
TEAM_SOG_NOT_INGESTED = "TEAM_SOG_NOT_INGESTED"
BLOCKS_NOT_INGESTED = "BLOCKS_NOT_INGESTED"
TEAM_DID_NOT_PLAY = "TEAM_DID_NOT_PLAY"
# Hits/Blocked-Shots Settlement Enablement block (2026-09-29): the boxscore's `hits` field is now
# ingested (ingest/nhl_api.py, player_game_stats.hits), but -- unlike Team SOG, whose boxscore field
# was already reconciled at 99.15% against the model's own training source before being trusted here
# -- NO reconciliation between the boxscore's `hits` and MoneyPuck's `I_F_hits` (what the validated
# Hits model actually trains on) has been done. Blocks already found a real, growing definitional
# drift (0%->9.23%) between its own boxscore field and its MoneyPuck training field; Hits could easily
# have the same problem and nobody has checked. This status exists so a caller can see the value
# without a caller mistaking it for a reconciled, trustworthy settlement -- it is NOT in
# FAIL_CLOSED_STATUSES' "nothing resolved" sense (a real number IS available) but it is also NOT
# RESOLVED (Part 25's "no data source is trusted for settlement without either being the model's own
# training source, or an explicitly characterized/bounded concordance" -- neither is true here yet).
HITS_UNVERIFIED_VS_MODEL_SOURCE = "HITS_UNVERIFIED_VS_MODEL_SOURCE"

FAIL_CLOSED_STATUSES = frozenset({
    GAME_NOT_FINAL, UNSUPPORTED_SETTLEMENT_MARKET, PLAYER_DID_NOT_DRESS,
    GOALIE_DID_NOT_PLAY, TEAM_SOG_NOT_INGESTED, BLOCKS_NOT_INGESTED,
    TEAM_DID_NOT_PLAY, HITS_UNVERIFIED_VS_MODEL_SOURCE,
})


def _result(status: str, *, actual_value=None, outcome_hit: bool | None = None,
            resolution_source: str | None = None, official_game_status: str | None = None) -> dict:
    return {
        "status": status, "actual_value": actual_value, "outcome_hit": outcome_hit,
        "resolution_source": resolution_source, "resolver_version": RESOLVER_VERSION,
        "official_game_status": official_game_status,
    }


def _game_row(conn: sqlite3.Connection, game_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()


def _is_final(conn: sqlite3.Connection, game_id) -> tuple[bool, str | None]:
    row = _game_row(conn, game_id)
    if row is None:
        return False, None
    return (row["game_state"] == "FINAL", row["game_state"])


def _latest_player_stat(conn: sqlite3.Connection, game_id, player_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM player_game_stats WHERE game_id=? AND player_id=?
           ORDER BY revision_number DESC LIMIT 1""", (game_id, str(player_id))).fetchone()


def _latest_goalie_stat(conn: sqlite3.Connection, game_id, player_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM goalie_game_stats WHERE game_id=? AND player_id=?
           ORDER BY revision_number DESC LIMIT 1""", (game_id, str(player_id))).fetchone()


def _side_hit(actual_value: int, threshold: int, side: str) -> bool:
    if side in ("OVER", "OVER_MILESTONE"):
        return actual_value >= threshold
    if side == "UNDER":
        return actual_value <= threshold - 1
    raise ValueError(f"unrecognized side {side!r}")


def resolve_player_stat_threshold(conn: sqlite3.Connection, *, market_family: str, game_id,
                                   player_id: str, threshold: int, side: str = "OVER") -> dict:
    """SOG / GOALS / ASSISTS / POINTS -- all read from the SAME official,
    already-ingested boxscore row (player_game_stats), never a second,
    parallel per-market source (Part 30's "separate concerns" principle
    applies here too: one truth source, many thresholds derived from it).

    Shootout goals are never included (Part 10): the real NHL boxscore's
    per-skater `goals` field is the official regulation/overtime goal
    total -- by NHL statistical convention, shootout goals are tracked
    as a wholly separate stat and are never added to a player's real
    goal total. This module relies on that being the boxscore field's
    real, established meaning (ingest/nhl_api.py already requires this
    exact field, `sog`/`goals`/`assists`, per its own v2.1.2a fix
    history) -- it does not re-derive or double-check shootout exclusion
    itself, since doing so would require a second, parallel goal-scoring
    source this project does not have.

    Points = goals + assists, the same official definition the NHL
    itself uses (Part 12) -- never a separately-fit number, so
    Goal<=Point coherence holds automatically, by construction, not as
    a rule enforced after the fact."""
    is_final, game_state = _is_final(conn, game_id)
    if not is_final:
        return _result(GAME_NOT_FINAL, official_game_status=game_state)

    row = _latest_player_stat(conn, game_id, player_id)
    if row is None:
        return _result(PLAYER_DID_NOT_DRESS, official_game_status=game_state)

    if market_family in ("SOG", "PLAYER_SOG"):
        actual = row["shots"]
    elif market_family in ("GOALS", "PLAYER_GOALS"):
        actual = row["goals"]
    elif market_family in ("ASSISTS", "PLAYER_ASSISTS"):
        actual = row["assists"]
    elif market_family in ("POINTS", "PLAYER_POINTS"):
        actual = row["goals"] + row["assists"]
    elif market_family in ("HITS", "PLAYER_HITS"):
        # See HITS_UNVERIFIED_VS_MODEL_SOURCE's module-level note: a real value is available
        # (ingest/nhl_api.py has captured it since this block), but it is NOT trusted for settlement
        # until reconciled against MoneyPuck's I_F_hits, the validated model's actual training source.
        if row["hits"] is None:
            return _result(HITS_UNVERIFIED_VS_MODEL_SOURCE, official_game_status=game_state)
        return _result(HITS_UNVERIFIED_VS_MODEL_SOURCE, actual_value=row["hits"],
                        official_game_status=game_state)
    else:
        return _result(UNSUPPORTED_SETTLEMENT_MARKET, official_game_status=game_state)

    return _result(RESOLVED, actual_value=actual, outcome_hit=_side_hit(actual, threshold, side),
                    resolution_source="OFFICIAL_NHL_BOXSCORE", official_game_status=game_state)


def resolve_goalie_saves(conn: sqlite3.Connection, *, game_id, goalie_player_id: str,
                          threshold: int, side: str = "OVER") -> dict:
    """Part 15/21: resolves against the SPECIFIC goalie named in the
    prediction -- never "the team's starter". A prediction conditioned on
    a projected starter who did not, in the end, appear in this game's
    goalie_game_stats at all is GOALIE_DID_NOT_PLAY -- distinct from a
    real 0-save appearance, which has an actual row with saves=0
    (a genuinely possible, if rare, real outcome: pulled after zero
    shots faced). Multi-goalie games (starter pulled, reliever finishes)
    are handled naturally: each goalie has their OWN row in
    goalie_game_stats (started=1 for the starter, started=0 for any
    reliever), and this function looks up the row for the EXACT
    player_id the prediction named, never aggregating across goalies."""
    is_final, game_state = _is_final(conn, game_id)
    if not is_final:
        return _result(GAME_NOT_FINAL, official_game_status=game_state)

    row = _latest_goalie_stat(conn, game_id, goalie_player_id)
    if row is None:
        return _result(GOALIE_DID_NOT_PLAY, official_game_status=game_state)

    actual = row["saves"]
    return _result(RESOLVED, actual_value=actual, outcome_hit=_side_hit(actual, threshold, side),
                    resolution_source="OFFICIAL_NHL_BOXSCORE_GOALIE_GAME_STATS",
                    official_game_status=game_state)


def resolve_moneyline(conn: sqlite3.Connection, *, game_id, side_team_id: str) -> dict:
    """`side_team_id`: the team the prediction favored to win. Every
    completed real NHL regular-season game has exactly one winner
    (REG/OT/SO all produce a real winner; there is no tie) -- this
    reads the official final score directly, never a derived proxy."""
    is_final, game_state = _is_final(conn, game_id)
    if not is_final:
        return _result(GAME_NOT_FINAL, official_game_status=game_state)

    row = _game_row(conn, game_id)
    winner = row["home_team"] if row["home_score"] > row["away_score"] else row["away_team"]
    return _result(RESOLVED, actual_value=winner, outcome_hit=(winner == side_team_id),
                    resolution_source="OFFICIAL_NHL_GAME_RESULT", official_game_status=game_state)


def _latest_team_stat(conn: sqlite3.Connection, game_id, team_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM team_game_stats WHERE game_id=? AND team_id=?
           ORDER BY revision_number DESC LIMIT 1""", (game_id, str(team_id))).fetchone()


def resolve_team_sog(conn: sqlite3.Connection, *, game_id, team_id: str, threshold: int,
                      side: str = "OVER") -> dict:
    """Hits/Blocked-Shots Settlement Enablement block (2026-09-29): now wired for real. This was
    always documented as safe to wire (a genuine software gap, not a methodology concern) --
    TEAM_SOG_VALIDATION_REPORT.md already established the official boxscore's team-level `sog` field
    agrees with the model's own canonical PBP-derived training source 99.15% of the time (every
    mismatch exactly +/-1, a known bounded class). Reads the SAME already-fetched boxscore's
    box[side]["sog"], now stored in team_game_stats (ingest/nhl_api.py). Still fails closed, never
    approximates, if the game isn't final or the team's row was never ingested."""
    is_final, game_state = _is_final(conn, game_id)
    if not is_final:
        return _result(GAME_NOT_FINAL, official_game_status=game_state)
    row = _latest_team_stat(conn, game_id, team_id)
    if row is None or row["sog"] is None:
        return _result(TEAM_SOG_NOT_INGESTED, official_game_status=game_state)
    actual = row["sog"]
    return _result(RESOLVED, actual_value=actual, outcome_hit=_side_hit(actual, threshold, side),
                    resolution_source="OFFICIAL_NHL_BOXSCORE", official_game_status=game_state)


def resolve_blocks(conn: sqlite3.Connection, *, game_id, player_id: str, threshold: int,
                    side: str = "OVER") -> dict:
    """STILL FAILS CLOSED (Part 13/18) -- deliberately unchanged by the Hits/Blocked-Shots Settlement
    Enablement block (2026-09-29). player_game_stats.blocked_shots now EXISTS and is ingested (from the
    boxscore's `blockedShots` field), so the "no column" half of the original gap is closed -- but the
    real reason this fails closed was never the missing column, it was a genuine METHODOLOGY concern
    that a column addition does not fix: the validated BLOCKED_SHOTS model's canonical training corpus
    is MoneyPuck-derived (research/player_blocks/player_game_blocks.jsonl's `shotsBlockedByPlayer`),
    not the boxscore's `blockedShots` field, and this project's own audit already found the OTHER known
    blocked-shot definition (PBP `blockingPlayerId`) drifts from the official boxscore by a REAL,
    GROWING margin (0% in 2022-23 to 9.23% by 2025-26), unlike Team SOG's tiny, stable, already-
    characterized +/-1 pattern. No reconciliation between the NOW-INGESTED boxscore field and the
    model's actual MoneyPuck training field has been done -- using it here without that study would
    risk exactly the methodology-mismatch this project's own prior sprint warned against. The new
    column exists so that reconciliation study can be RUN (going forward, on real games); it is not
    read here until that study exists and shows the drift is acceptable."""
    return _result(BLOCKS_NOT_INGESTED)


# Dispatch table keyed by canonical market_id PREFIX (research/player_props/
# market_registry.py's own naming convention) -- deliberately a prefix
# match, not an exact one, so e.g. "PLAYER_SOG_3PLUS" and a bare
# "PLAYER_SOG" market_id field both route the same way.
_PLAYER_STAT_PREFIXES = {
    "PLAYER_SOG": "SOG", "PLAYER_GOALS": "GOALS", "PLAYER_ASSISTS": "ASSISTS",
    "PLAYER_POINTS": "POINTS", "PLAYER_HITS": "HITS",
}


def resolve_prediction(conn: sqlite3.Connection, prediction: dict) -> dict:
    """The single entry point settle_daily_observations.py calls per
    prospective ledger row. `prediction` is a ledger row's own fields
    (market_id, threshold, side, game_id, player_id, team_id) -- never a
    second, redundant argument shape. `threshold` is parsed from the
    ledger's own "N+" string convention (Part: never re-invent a second
    threshold representation)."""
    market_id = (prediction.get("market_id") or "").upper()
    threshold_str = str(prediction.get("threshold") or "0").rstrip("+")
    try:
        threshold = int(threshold_str)
    except ValueError:
        return _result(UNSUPPORTED_SETTLEMENT_MARKET)
    side = prediction.get("side") or "OVER"
    game_id = prediction.get("game_id")

    if market_id.startswith("GOALIE_SAVES"):
        return resolve_goalie_saves(conn, game_id=game_id, goalie_player_id=prediction.get("player_id"),
                                     threshold=threshold, side=side)
    if market_id.startswith("TEAM_SOG"):
        # Hits/Blocked-Shots Settlement Enablement block (2026-09-29): this dispatch previously read
        # prediction["team_id"], a column that has never existed in the prospective ledger schema
        # (confirmed: operational/prospective_schema.sql's real column is "team") -- harmless ONLY
        # while resolve_team_sog failed closed before ever using the argument (see the MONEYLINE
        # dispatch's identical prior bug/fix above, 2026-09-24). Now that Team SOG is genuinely wired,
        # this would have silently resolved every real prediction to TEAM_SOG_NOT_INGESTED forever.
        return resolve_team_sog(conn, game_id=game_id, team_id=prediction.get("team"),
                                 threshold=threshold, side=side)
    if market_id.startswith("PLAYER_BLOCKS"):
        return resolve_blocks(conn, game_id=game_id, player_id=prediction.get("player_id"),
                               threshold=threshold, side=side)
    if market_id in ("NHL_WIN_MODEL", "MONEYLINE") or market_id.startswith("MONEYLINE"):
        # Real bug fix (Real Recommendation Pipeline block, 2026-09-24):
        # the prospective ledger schema's real column is "team" -- there
        # has never been a "team_id" column (confirmed in
        # operational/prospective_schema.sql). This read "team_id" for as
        # long as MONEYLINE dispatch has existed, always silently
        # resolving to None -- never caught because MONEYLINE settlement
        # had never actually been exercised until this block wired the
        # first real MONEYLINE predictions through it. TEAM_SOG below has
        # the identical wrong-key pattern but is harmless dead code
        # (resolve_team_sog fails closed before ever using its team_id
        # argument) -- left as-is rather than changed speculatively.
        return resolve_moneyline(conn, game_id=game_id, side_team_id=prediction.get("team"))
    for prefix, family in _PLAYER_STAT_PREFIXES.items():
        if market_id.startswith(prefix):
            return resolve_player_stat_threshold(conn, market_family=family, game_id=game_id,
                                                  player_id=prediction.get("player_id"),
                                                  threshold=threshold, side=side)
    return _result(UNSUPPORTED_SETTLEMENT_MARKET)
