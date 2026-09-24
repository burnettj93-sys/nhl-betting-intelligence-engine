# Intraday NHL Data Refresh — Proposed Schedule

**Status: PROPOSAL ONLY. No new jobs installed for this item — per instruction, documenting before adding more cron entries.** The 3 jobs from items 1–2 (`daily-nhl-sync` 07:00, `daily-settlement` 07:15, `daily-postmortem` 07:30) are already live; everything below is new and awaiting your go-ahead.

## What's actually refreshable, and what isn't

Before designing a cadence, it matters which of the brief's data categories have a real, automatable source at all:

| Data | Real automated source? | Notes |
|---|---|---|
| Schedule | **Yes** — free NHL endpoint | No rate-limit concern observed; safe to call often. |
| Boxscores | **Yes** — free NHL endpoint | Only meaningful once a game is `FINAL`; this system has no live in-game polling capability at all today (confirmed — nothing in `ingest/` or `operational/` reads a game while `LIVE`), and building one is a real feature, not a refresh-frequency tweak. Out of scope here. |
| Current rosters | **Yes**, but rate-limited | Confirmed twice for real this week: a full ~31-team sweep at 0.2s spacing reliably draws at least one 429 from the NHL API. Must be refreshed sparingly and, ideally, only for teams that actually matter that day. |
| Starting goalies | **No public API exists.** | `ingest/nhl_api.py`'s own docstring says so plainly — `record_goalie_status()` is a write path waiting for a manual or third-party feed (Daily Faceoff, etc.). No refresh schedule can fix a missing source; this needs a sourcing decision first, not a cron job. |
| Injuries | **No public API exists.** | Same situation as goalies. |
| Line combinations / deployment | **No source at all**, confirmed in the Production Readiness Audit. | Not a refresh-frequency question — nothing to refresh yet. |
| Play-by-play | **Yes**, but only useful after the fact | Feeds research/backtesting, not a same-day decision; no intraday need. |
| Odds/props | **Already scheduled** (4 jobs, Sept 15 sprint) | Not touched here — this doc is NHL-side data only. |

So the honest scope of "intraday NHL refresh" is really just **schedule + rosters**, sized to their actual constraints — everything else either has no source or has no live-polling capability to refresh in the first place.

## Proposed cadence

### A. Overnight / final-data reconciliation — ALREADY LIVE
`daily-nhl-sync` @ 07:00 → `daily-settlement` @ 07:15 → `daily-postmortem` @ 07:30. Covers yesterday's final results, today's/tomorrow's schedule, and a full current-roster reconciliation. No change proposed.

### B. Morning baseline — ALREADY COVERED
The same 07:00 run doubles as the morning baseline (today's slate + current rosters). No separate job needed.

### C. Daytime refresh — proposed, not yet installed
- **Schedule-only recheck, ~midday** (e.g. 13:00): free endpoint, catches a same-day postponement or start-time change. Cheap enough to run without a second thought.
- **No full-league roster resweep during the day.** Given the confirmed rate-limit sensitivity, resweeping all 32 teams a second time before evening would just reproduce the same 429s for no real benefit — nothing meaningfully changes roster-wise mid-afternoon that a targeted pregame check (below) wouldn't also catch.

### D. Pregame freshness verification — proposed, not yet installed
A pass timed relative to puck drop, reusing the same "hours until commence" timing utility the odds sweeps already use (`operational.live_odds_daily_pull._events_in_window`-style logic, applied to `nhl.db`'s own schedule rather than the Odds API's event list):
- **~3–4 hours before each day's first puck drop:** one more free schedule check, plus a **targeted current-roster refresh for only the teams playing today** — not all 32. For a typical preseason day (6–10 games, 12–20 teams), this is a much smaller, gentler sweep than the full-league one already done at 07:00, and running it a second time closer to game time is exactly the "pregame freshness verification" the brief asks for, without re-triggering the same rate-limit pressure a second full sweep would.
- **If this check fails or a team's roster can't be refreshed in time:** feed that into `system_health`/readiness so the recommendation engine can correctly mark that team's players `DATA_UNAVAILABLE` rather than silently pricing off stale roster data — this is a real hook to add to `dashboard/eligible_bets.py`'s freshness gate, not just a new cron job.

## What this deliberately does not include

- No live in-game polling (would be a real new feature, not a refresh-cadence change).
- No goalie/injury/line-combination refresh job of any kind — there's no source to refresh from yet. If a third-party feed is ever added, it gets its own design pass, not a slot in this schedule.
- No second full-league roster sweep — one full sweep (morning) plus one targeted, playing-teams-only sweep (pregame) covers the real need without gratuitous API traffic.

## Net new jobs if approved

Two: a midday schedule-only check, and a pregame targeted roster + schedule check. Both are cheap (schedule is free; a targeted roster sweep of ~15 teams is roughly half the load of the existing 07:00 full sweep). Waiting for a decision before installing either.
