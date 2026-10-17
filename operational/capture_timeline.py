"""
The day's capture timeline. One place, pure functions, no network, no credits.

What the owner asked for (2026-10-17), as a schedule on the Eastern clock:

  07:45 ET   refresh schedules, results, player statistics, rosters and available participation evidence (operational/morning_refresh.py)
  08:00 ET   publish the statistical analysis of the WHOLE slate and take the planned league-wide moneyline + standard-spread snapshot
  WAVES      games are grouped into compatible start-time waves (puck drops within WAVE_MINUTES of the wave's first). Each wave's selected games get ONE coordinated
             player-odds capture (shots ladder, anytime goal, goalie saves) with target 90 minutes before the wave's first game, to be complete and published by 60 minutes
             before it. A capture that is late (machine asleep, network outage) is still made until LAST_CHANCE_MIN before the first game; after that the window is MISSED:
             it is reported and never bought late, because a price taken minutes before puck drop is not a pregame price anyone can act on.
  EARLY DAY  if the day's first game starts early, the 07:45 refresh and the 08:00 analysis move earlier automatically (prep_times), so the first capture is never waiting for them.

Nothing here decides what any model believes; it decides WHEN a credit may be spent and when a window has closed.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

REFRESH_ET = (7, 45)
ANALYSIS_ET = (8, 0)
CAPTURE_TARGET_MIN = 90         # first coordinated capture of a wave: this long before the wave's first game
PUBLISH_DEADLINE_MIN = 60       # complete and published no later than this long before it
LAST_CHANCE_MIN = 15            # a late (recovery) capture is still made until this long before the first game; later it is a missed window
WAVE_MINUTES = 60               # compatible start times: a game joins a wave when it starts within this many minutes of the wave's first game
REFRESH_LEAD_MIN = 165          # the refresh runs at least this long before the day's first game (7:45 ET for any game from 10:30 ET)
ANALYSIS_LEAD_MIN = 150         # the analysis publishes at least this long before it (8:00 ET for any game from 10:30 ET)

ON_TIME, LATE, MISSED, NOT_YET = "ON_TIME", "LATE", "MISSED", "NOT_YET"


def _localize(day: dt.date, hh: int, mm: int) -> dt.datetime:
    from zoneinfo import ZoneInfo
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)


def et_date(now: dt.datetime) -> dt.date:
    from operational import eastern_time as et
    return dt.date.fromisoformat(et.eastern_today(now))


@dataclasses.dataclass(frozen=True)
class Wave:
    index: int
    first_start: dt.datetime
    games: tuple[str, ...]

    @property
    def target(self) -> dt.datetime:
        return self.first_start - dt.timedelta(minutes=CAPTURE_TARGET_MIN)

    @property
    def deadline(self) -> dt.datetime:
        return self.first_start - dt.timedelta(minutes=PUBLISH_DEADLINE_MIN)

    @property
    def last_chance(self) -> dt.datetime:
        return self.first_start - dt.timedelta(minutes=LAST_CHANCE_MIN)


def waves(starts: dict[str, dt.datetime], minutes: float = WAVE_MINUTES) -> list[Wave]:
    """Group game ids into start-time waves, earliest first. A wave is the games whose puck drops are within `minutes` of the wave's first."""
    out: list[list[tuple[str, dt.datetime]]] = []
    first = None
    for gid, t in sorted(starts.items(), key=lambda kv: (kv[1], kv[0])):
        if first is None or (t - first).total_seconds() > minutes * 60:
            out.append([])
            first = t
        out[-1].append((gid, t))
    return [Wave(i, w[0][1], tuple(g for g, _ in w)) for i, w in enumerate(out)]


def prep_times(day: dt.date, first_start: dt.datetime | None) -> dict[str, dt.datetime]:
    """When the day's refresh and analysis are due: 07:45 and 08:00 ET, moved earlier when the first game is early."""
    refresh, analysis = _localize(day, *REFRESH_ET), _localize(day, *ANALYSIS_ET)
    if first_start is not None:
        refresh = min(refresh, first_start - dt.timedelta(minutes=REFRESH_LEAD_MIN))
        analysis = min(analysis, first_start - dt.timedelta(minutes=ANALYSIS_LEAD_MIN))
    return {"refresh_at": refresh, "analysis_at": analysis}


def wave_state(now: dt.datetime, wave: Wave, captured: bool) -> str:
    """NOT_YET before the target; ON_TIME from the target to the 60-minute publish deadline; LATE from then to LAST_CHANCE; MISSED after, if never captured. A captured wave is ON_TIME or LATE by when it happened (not judged here)."""
    if captured:
        return ON_TIME
    if now < wave.target:
        return NOT_YET
    if now <= wave.deadline:
        return ON_TIME
    if now <= wave.last_chance:
        return LATE
    return MISSED


def wave_of(game_id: str, wave_list: list[Wave]) -> Wave | None:
    return next((w for w in wave_list if game_id in w.games), None)


def league_pull_due(now: dt.datetime, *, day_first_start: dt.datetime | None, wave_list: list[Wave], pull_times: list[dt.datetime]) -> dict:
    """Is a league-wide moneyline + spread pull due now? `pull_times` are today's pulls (UTC), newest last.

    MORNING   due from the analysis time (08:00 ET, earlier on an early day) until every game has started, when no pull has been made since then (after a long sleep it still serves the waves not yet started).
    PREGAME   the day's shared pull: due from the FIRST wave's capture target (90 minutes before its first game) to LAST_CHANCE when no pull has been made since the morning.
    WAVE      a further pull for a later wave, due from that wave's target: asked for only when the caller has funded it (the plan's surplus); the decision to buy it is the caller's.
    Nothing is due once the pulls it would serve are behind us (all games started)."""
    day = et_date(now)
    prep = prep_times(day, day_first_start)
    last = max(pull_times) if pull_times else None
    if day_first_start is not None and now >= day_first_start and not any(w.first_start > now for w in wave_list):
        return {"due": False, "kind": None, "reason": "ALL_GAMES_STARTED"}
    if now < prep["analysis_at"]:
        return {"due": False, "kind": None, "reason": "BEFORE_MORNING_PULL", "next_at": prep["analysis_at"]}
    if last is None or last < prep["analysis_at"]:
        return {"due": True, "kind": "MORNING", "reason": "NO_PULL_SINCE_THE_ANALYSIS_TIME"}
    for w in wave_list:
        if w.last_chance < now or now < w.target:
            continue
        if any(t >= w.target - dt.timedelta(minutes=10) for t in pull_times):
            continue                                                  # a pull at or after this wave's target already serves it
        return {"due": True, "kind": "PREGAME" if w.index == 0 else "WAVE", "reason": "WAVE_TARGET_REACHED_WITHOUT_A_FRESH_PULL", "wave": w.index}
    nxt = min((w.target for w in wave_list if w.target > now), default=None)
    return {"due": False, "kind": None, "reason": "WITHIN_SCHEDULE", "next_at": nxt}
