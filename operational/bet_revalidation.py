"""
Bet Re-Validation block (2026-10-01): "I want bets to be reevaluated at
every pull" -- the user's explicit instruction, naming five concrete
real-world events that should invalidate an already-staked real-market
parlay before its games start: (1) injury, (2) a new/changed goalie,
(3) a trade, (4) a suspended player, (5) a game delay (e.g. weather).

This module re-checks every PENDING, not-yet-started REAL_MARKET_PAPER
combo bet against the CURRENT real state, using only already-real,
already-existing data sources -- it invents no new ingestion:
  - (5) game delay: research/append-only operational/nhl_sync.py already
    ingests real NHL schedule revisions into game_schedule_events (real,
    live today). A schedule event observed AFTER the bet was staked means
    puck drop genuinely moved.
  - (1)/(4) injury/suspension: reads roster_status_events (the same table
    features/point_in_time.py::roster_status() reads for point-in-time
    prediction context) -- real schema, but (same as goalie confirmation)
    no licensed live source has ever been wired to populate it for real
    games (ingest/nhl_api.py::record_roster_status()'s own
    docstring: "No public NHL API for this either"). This check is real
    and will react the instant any real status is ever recorded there; it
    simply has nothing to react to today.
  - (2) new/changed goalie: goalie_status_events' CHANGED status -- same
    "real mechanism, no live source yet" situation as above (see
    config.REQUIRE_GOALIE_CONFIRMATION's docstring).
  - (3) trade: re-resolves the player's current most-recent team from the
    SAME real SOG identity corpus every leg-builder already uses. The
    corpus is a frozen file in this environment, so this is also inert
    today, but will reflect a real trade automatically the moment the
    corpus (or whatever live roster source eventually feeds player_mapping.
    build_player_index()) is ever refreshed with the trade.

On ANY trigger firing for ANY leg, the WHOLE combo is VOIDED (refund the
stake) -- the same "a push voids the whole ticket" convention
operational/paper_bet_settlement_driver.py already uses for a real DNP leg,
never a partial-ticket reduction this project has never documented a real
sportsbook convention for. This module NEVER re-prices a bet to a new
stake/probability -- "reevaluate" here means "is the premise this bet was
placed under still true," not "adjust the bet to the new odds." A bet whose
premise is no longer true is voided, exactly like a real push; a bet
whose premise still holds is left untouched.
"""
from __future__ import annotations

import datetime as dt
import json

from features import point_in_time as pit
from operational import paper_bankroll as pb


def _schedule_changed_since(nhl_conn, game_id, since_utc: str, now_utc: str) -> dict | None:
    """tests/test_structural_reads.py requires every read of a restricted,
    point-in-time-sensitive table (game_schedule_events here) to go through
    features/point_in_time.py -- game_schedule_as_of() called at two
    different "as of" times is exactly that, reused rather than a second,
    raw-SQL read of the same table."""
    before = pit.game_schedule_as_of(nhl_conn, game_id, since_utc)
    after = pit.game_schedule_as_of(nhl_conn, game_id, now_utc)
    before_start = before["scheduled_start_utc"] if before else None
    after_start = after["scheduled_start_utc"] if after else None
    if after_start != before_start:
        return {"old_scheduled_start_utc": before_start, "new_scheduled_start_utc": after_start}
    return None


def _goalie_changed_since(nhl_conn, game_id, team_id, since_utc: str, now_utc: str) -> dict | None:
    """Reuses features/point_in_time.py::goalie_status() (same restricted-
    table rationale as _schedule_changed_since above)."""
    current = pit.goalie_status(nhl_conn, game_id, team_id, now_utc)
    if current.status == "CHANGED" and current.observed_at_utc and current.observed_at_utc > since_utc:
        return {"observed_at_utc": current.observed_at_utc}
    return None


def _roster_status_changed_since(nhl_conn, player_id, since_utc: str, now_utc: str) -> dict | None:
    """Reuses features/point_in_time.py::roster_status() (same restricted-
    table rationale as _schedule_changed_since above) -- a real NEW status
    report that changed the player's availability between the two times."""
    before = pit.roster_status(nhl_conn, player_id, since_utc)
    after = pit.roster_status(nhl_conn, player_id, now_utc)
    if after != "ACTIVE" and after != before:
        return {"status": after}
    return None


def _current_team_for_player(player_index: dict[str, list[dict]], player_id: str) -> str | None:
    for candidates in player_index.values():
        for c in candidates:
            if c["player_id"] == player_id:
                return c["most_recent_team"]
    return None


_PLAYER_LEG_FAMILIES = frozenset({"PLAYER_SOG", "PLAYER_SOG_ALTERNATE", "GOALIE_SAVES"})


def _invalidation_reasons_for_leg(nhl_conn, leg: dict, since_utc: str, now_iso: str,
                                   player_index: dict[str, list[dict]] | None) -> list[str]:
    reasons: list[str] = []
    game_id = leg.get("game_id")
    game_row = nhl_conn.execute(
        "SELECT home_team, away_team FROM games WHERE game_id = ?", (game_id,)).fetchone()
    if game_row is None:
        return reasons  # nothing real to check this leg's game against
    home_abbrev, away_abbrev = game_row["home_team"], game_row["away_team"]

    schedule_change = _schedule_changed_since(nhl_conn, game_id, since_utc, now_iso)
    if schedule_change is not None:
        reasons.append(f"SCHEDULE_CHANGED: {schedule_change['old_scheduled_start_utc']} -> "
                        f"{schedule_change['new_scheduled_start_utc']}")

    market_family = leg.get("market_family")
    if market_family == "MONEYLINE":
        for team_id in (home_abbrev, away_abbrev):
            goalie_change = _goalie_changed_since(nhl_conn, game_id, team_id, since_utc, now_iso)
            if goalie_change is not None:
                reasons.append(f"GOALIE_STATUS_CHANGED: {team_id} starter changed "
                                f"(observed {goalie_change['observed_at_utc']})")
        return reasons

    if market_family in _PLAYER_LEG_FAMILIES:
        player_id = leg.get("participant_id")
        roster_change = _roster_status_changed_since(nhl_conn, player_id, since_utc, now_iso)
        if roster_change is not None:
            reasons.append(f"ROSTER_STATUS_CHANGED: {leg.get('participant_name')} is now "
                            f"{roster_change['status']}")

        if market_family == "GOALIE_SAVES":
            current_team = _current_team_for_player(player_index, player_id) if player_index else None
            if current_team in (home_abbrev, away_abbrev):
                goalie_change = _goalie_changed_since(nhl_conn, game_id, current_team, since_utc, now_iso)
                if goalie_change is not None:
                    reasons.append(f"GOALIE_STATUS_CHANGED: {leg.get('participant_name')}'s status changed "
                                    f"(observed {goalie_change['observed_at_utc']})")

        if player_index is not None:
            current_team = _current_team_for_player(player_index, player_id)
            if current_team is not None and current_team not in (home_abbrev, away_abbrev):
                reasons.append(f"TEAM_CHANGED: {leg.get('participant_name')} is now on {current_team}, "
                                f"no longer {home_abbrev}/{away_abbrev} (trade)")

    return reasons


def _load_player_index() -> dict[str, list[dict]]:
    from research.live_sog_pricing import player_mapping
    from research.player_sog import features as pf
    sog_rows = pf.load_sog_corpus()
    return player_mapping.build_player_index(sog_rows)


def revalidate_pending_real_market_bets(bankroll_conn, nhl_conn, now: dt.datetime | None = None) -> dict:
    """The real entry point: re-checks every PENDING, not-yet-started
    REAL_MARKET_PAPER combo bet and VOIDs any whose premise no longer
    holds. Called at the start of every real pull/trader cycle (see
    operational/real_parlay_paper_trader.py::run()), so a genuine change
    is caught before the affected game starts, not only discovered after
    the fact at settlement."""
    now = now or dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat()
    summary = {"checked": 0, "voided": 0, "results": []}

    try:
        player_index = _load_player_index()
    except Exception:  # noqa: BLE001 -- revalidation must never block staking/settlement
        player_index = None

    bets = pb.find_pending_future_event_bets(bankroll_conn, track="REAL_MARKET_PAPER", is_combo=True)
    for bet in bets:
        summary["checked"] += 1
        legs = json.loads(bet.get("legs_json") or "[]")
        since_utc = bet.get("created_at_utc") or now_iso
        all_reasons: list[str] = []
        for leg in legs:
            try:
                all_reasons.extend(_invalidation_reasons_for_leg(nhl_conn, leg, since_utc, now_iso, player_index))
            except Exception as exc:  # noqa: BLE001 -- one bad leg must never block the rest of the batch
                all_reasons.append(f"REVALIDATION_CHECK_ERROR: {exc.__class__.__name__}: {exc}")

        if all_reasons:
            note = "VOIDED on re-validation: " + "; ".join(all_reasons)
            pb.settle_paper_bet(bankroll_conn, bet["paper_bet_id"], "VOID", notes=note)
            summary["voided"] += 1
            summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": "VOIDED",
                                        "reasons": all_reasons})
        else:
            summary["results"].append({"paper_bet_id": bet["paper_bet_id"], "status": "UNCHANGED"})
    return summary
