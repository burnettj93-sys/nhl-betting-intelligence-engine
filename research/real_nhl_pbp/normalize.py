"""
Parts 3-15: canonical event-time, player-identity, goal, shot, hit,
penalty, faceoff, goalie, manpower, empty-net, and score normalization
for the real NHL play-by-play feed.

Every rule here is backed by a real, observed finding from the 30-game
pilot corpus archived under research/real_nhl_pbp/raw/20252026/ -- see
NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md for the evidence behind each one.
Nothing below is guessed from general NHL knowledge except two explicit,
documented exceptions (regulation/OT period lengths -- the feed itself
never states a period's length in seconds, see REGULATION-length note
below), both called out at the point they're used.
"""
from __future__ import annotations

from research.real_nhl_pbp.schema import PbpEvent, PbpGame

# The feed's periodDescriptor never states period length (confirmed: the
# union of periodDescriptor keys across the full 30-game pilot is exactly
# {number, periodType, maxRegulationPeriods} -- no duration field exists).
# These two constants are public NHL rule facts, not read from the feed,
# and apply ONLY to regular-season games (gameType == 2), which is this
# slice's entire scope (Part 22/23: one regular season, not playoffs).
REGULATION_PERIOD_SECONDS = 20 * 60
REGULAR_SEASON_OT_SECONDS = 5 * 60  # confirmed empirically: max OT timeInPeriod
                                      # observed across all 30 pilot games is
                                      # exactly "05:00", never exceeded.

# Confirmed empirically across the full 30-game pilot (9,505 REG + 178 OT +
# 70 SO plays): every single `eventId` sequence in every game is NOT
# monotonically increasing, while `sortOrder` is monotonic and unique with
# zero duplicates in all 30 games. This is Part 4's exact concern realized
# in practice -- eventId must never be used for ordering; sortOrder is the
# canonical event_sequence.
EVENT_SEQUENCE_FIELD = "sortOrder"


class PbpNormalizationError(RuntimeError):
    """Raised on a structurally malformed event this module cannot safely
    normalize -- Part 37: fail loudly and record, never guess."""


def seconds_elapsed(time_in_period: str) -> int:
    minutes, seconds = time_in_period.split(":")
    return int(minutes) * 60 + int(seconds)


def compute_regulation_elapsed_seconds(period_number: int, period_type: str,
                                        elapsed_in_period: int) -> int | None:
    """None for SO (Part 3: shootout has no game-clock time to speak of --
    it is attempt-indexed, not time-indexed)."""
    if period_type == "SO":
        return None
    if period_type == "REG":
        return (period_number - 1) * REGULATION_PERIOD_SECONDS + elapsed_in_period
    if period_type == "OT":
        return 3 * REGULATION_PERIOD_SECONDS + elapsed_in_period
    raise PbpNormalizationError(f"unknown periodType {period_type!r}")


# ------------------------------------------------------------- players --

def extract_players(event_type: str, details: dict) -> dict:
    """Part 18 role mapping -- exact field names confirmed present on the
    real feed for each event type (see report Section E)."""
    players: dict[str, int] = {}
    if event_type == "goal":
        if "scoringPlayerId" in details:
            players["scorer"] = details["scoringPlayerId"]
        if "assist1PlayerId" in details:
            players["assist1"] = details["assist1PlayerId"]
        if "assist2PlayerId" in details:
            players["assist2"] = details["assist2PlayerId"]
        if "goalieInNetId" in details:
            players["goalie"] = details["goalieInNetId"]
    elif event_type in ("shot-on-goal", "missed-shot", "failed-shot-attempt"):
        if "shootingPlayerId" in details:
            players["shooter"] = details["shootingPlayerId"]
        if "goalieInNetId" in details:
            players["goalie"] = details["goalieInNetId"]
    elif event_type == "blocked-shot":
        if "shootingPlayerId" in details:
            players["shooter"] = details["shootingPlayerId"]
        if "blockingPlayerId" in details:
            players["blocker"] = details["blockingPlayerId"]
    elif event_type == "hit":
        if "hittingPlayerId" in details:
            players["hitter"] = details["hittingPlayerId"]
        if "hitteePlayerId" in details:
            players["hittee"] = details["hitteePlayerId"]
    elif event_type == "penalty":
        if "committedByPlayerId" in details:
            players["committed_by"] = details["committedByPlayerId"]
        if "drawnByPlayerId" in details:
            players["drawn_by"] = details["drawnByPlayerId"]
        if "servedByPlayerId" in details:
            players["served_by"] = details["servedByPlayerId"]
    elif event_type == "faceoff":
        if "winningPlayerId" in details:
            players["winner"] = details["winningPlayerId"]
        if "losingPlayerId" in details:
            players["loser"] = details["losingPlayerId"]
    elif event_type == "giveaway":
        if "playerId" in details:
            players["giveaway_by"] = details["playerId"]
    elif event_type == "takeaway":
        if "playerId" in details:
            players["takeaway_by"] = details["playerId"]
    return players


# ------------------------------------------------------------ empty net --

def is_empty_net_context(event: PbpEvent, defending_team_is_away: bool) -> bool:
    """Part 13: empty-net state is NOT an explicit boolean flag anywhere in
    the feed. It is inferred from TWO independently-corroborating signals
    (both confirmed on all 4 real empty-net goals found in the pilot):
      1. the event's own `goalieInNetId` field is absent, AND
      2. situationCode's defending team's goalie-in-net digit is "0".
    situationCode is a 4-digit string: [awayGoalieInNet, awaySkaters,
    homeSkaters, homeGoalieInNet] (confirmed via the shootout attacker/
    defender pattern and via 5-vs-6 pulled-goalie codes like "0651"/"1560"
    in the report). `defending_team_is_away` says whether the team who
    conceded (not the scoring team) is the away team.
    """
    if event.situation_code is None or len(event.situation_code) != 4:
        return False
    goalie_digit = event.situation_code[0] if defending_team_is_away else event.situation_code[3]
    return goalie_digit == "0" and "goalie" not in event.players


# --------------------------------------------------------------- events --

def normalize_event(raw_play: dict) -> PbpEvent:
    pd = raw_play["periodDescriptor"]
    period_type = pd["periodType"]
    period_number = pd["number"]
    elapsed = seconds_elapsed(raw_play["timeInPeriod"])
    remaining = seconds_elapsed(raw_play["timeRemaining"]) if "timeRemaining" in raw_play else None
    details = raw_play.get("details", {})
    event_type = raw_play["typeDescKey"]

    if EVENT_SEQUENCE_FIELD not in raw_play:
        raise PbpNormalizationError(f"event {raw_play.get('eventId')} missing {EVENT_SEQUENCE_FIELD}")

    return PbpEvent(
        game_id=None,  # filled in by normalize_game_events()
        event_id=raw_play["eventId"],
        event_sequence=raw_play[EVENT_SEQUENCE_FIELD],
        event_type=event_type,
        type_code=raw_play["typeCode"],
        period_number=period_number,
        period_type=period_type,
        time_in_period=raw_play["timeInPeriod"],
        seconds_elapsed_in_period=elapsed,
        seconds_remaining_in_period=remaining,
        regulation_elapsed_seconds=compute_regulation_elapsed_seconds(period_number, period_type, elapsed),
        team_id=details.get("eventOwnerTeamId"),
        situation_code=raw_play.get("situationCode"),
        zone_code=details.get("zoneCode"),
        x_coord=details.get("xCoord"),
        y_coord=details.get("yCoord"),
        is_statistical=(period_type != "SO"),
        players=extract_players(event_type, details),
        raw_details=details,
    )


def normalize_game(raw: dict, *, raw_sha256: str, source_url: str, retrieved_at_utc: str) -> PbpGame:
    outcome = raw.get("gameOutcome", {}) or {}
    return PbpGame(
        game_id=raw["id"],
        season=str(raw["season"]),
        game_type=raw["gameType"],
        game_date=raw["gameDate"],
        away_team_id=raw["awayTeam"]["id"],
        away_team_abbrev=raw["awayTeam"]["abbrev"],
        home_team_id=raw["homeTeam"]["id"],
        home_team_abbrev=raw["homeTeam"]["abbrev"],
        final_period_type=outcome.get("lastPeriodType", raw["periodDescriptor"]["periodType"]),
        ot_periods=outcome.get("otPeriods", 0),
        reg_periods=raw.get("regPeriods", 3),
        raw_source_url=source_url,
        raw_sha256=raw_sha256,
        retrieved_at_utc=retrieved_at_utc,
    )


def normalize_game_events(raw: dict) -> list[PbpEvent]:
    game_id = raw["id"]
    events = []
    for play in raw["plays"]:
        ev = normalize_event(play)
        ev.game_id = game_id
        events.append(ev)
    events.sort(key=lambda e: e.event_sequence)
    return events


# ------------------------------------------------------- score rebuild --

def reconstruct_statistical_score(events: list[PbpEvent], home_team_id: int, away_team_id: int
                                   ) -> list[dict]:
    """Part 15: independently reconstructs the running score by COUNTING
    statistical goals in event_sequence order (never trusting the feed's
    own embedded awayScore/homeScore on the goal event as ground truth --
    those are cross-checked, not assumed, in reconcile.py). A shootout
    goal (is_statistical == False) must NEVER increment either counter
    (Part 6/9 invariant)."""
    home_score = 0
    away_score = 0
    timeline = []
    for ev in events:
        if ev.event_type != "goal" or not ev.is_statistical:
            continue
        if ev.team_id == home_team_id:
            home_score += 1
        elif ev.team_id == away_team_id:
            away_score += 1
        else:
            raise PbpNormalizationError(
                f"goal event {ev.event_id} eventOwnerTeamId {ev.team_id} matches neither team"
            )
        timeline.append({
            "event_sequence": ev.event_sequence,
            "event_id": ev.event_id,
            "period_number": ev.period_number,
            "period_type": ev.period_type,
            "home_score": home_score,
            "away_score": away_score,
        })
    return timeline


def shootout_winner(events: list[PbpEvent], home_team_id: int, away_team_id: int) -> int | None:
    """The team_id that scored the LAST shootout goal (real NHL shootout
    rule: sudden-death after round 3, so the final SO goal, if any,
    belongs to the winner). Returns None if the game never reached SO."""
    so_goals = [e for e in events if e.event_type == "goal" and e.period_type == "SO"]
    if not so_goals:
        return None
    return so_goals[-1].team_id
