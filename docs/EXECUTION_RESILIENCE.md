# Execution resilience (2026-10-31)

Two production failures, both execution and neither a betting rule. No probability, threshold, ranking, GOOD / QUESTIONABLE rule, reuse cap, portfolio, flagship or Eggy policy was changed.

## 1. Eggy: the pick existed; the page never got it

Oct 31: the 10 AM-class sequence ran on time. Evidence (ledger, research store, trader log, power log):

| step | time (UTC) | state |
|---|---|---|
| slate refreshed | 11:46:26 | RAN |
| league side prices (moneyline + puck line) | 12:00:05, 15:32:56 | RAN |
| injury research INITIAL / MAIN / RECHECK | 14:21:11 / 14:50:24 / 15:20:02 | RAN |
| goalie refresh | feed disabled by default | SKIPPED (DISABLED) |
| first game | 17:00 | |
| Eggy ranking + decision + ledger write | 15:43:29 (77 min before the first game) | RAN: PICK COL ML -425, LOW, BEST AVAILABLE |
| publication | every attempt from 15:43:29 | **FAILED** |
| host sleep | none on Oct 31 (last wake Oct 30 20:06 EDT; trader every ~10.5 min all day) | NOT A FACTOR |

First broken step: **publication**. The decision card carried the research credit "Associated Press (via Yahoo Sports)". The cloud validator refuses any value that names the provider (structural Yahoo tripwire), so the whole snapshot was rejected; the rejection message (also naming the provider) then entered the next snapshot through job health, so every later attempt failed too, even for unrelated content. Last good publication 15:33:29Z.

Fix (published copy only): `cloud_snapshot_schema.redact_news_credit` removes a news outlet's name from free-text VALUES of three surfaces: Eggy's decision card, job-health detail, system-health messages. The ledger keeps the true credit. The validator is untouched, so the tripwire still fires on every other value and on every key.

## 2. Eggy deadline and guard

* Decision deadline: `DECISION_DEADLINE_MIN = 30` before the first game (normal days decide ~75 min before). A late league pull is still accepted until the existing 15-minute last chance; then the day is an honest PASS. Freshness rules are unchanged.
* Unusable core hockey data at the last chance now records a PASS (technical failure) instead of leaving no row.
* Eggy's step now runs BEFORE the heavy parlay work in the trader cycle, so a slow or failing parlay step cannot delay it. Goalie, injury and movement data were already optional (their failure is "unknown", never a stop).
* Guard `EGGY PICK NOT RECORDED BY DEADLINE` (`eggy_pick_ledger.deadline_guard`): evaluated every trader cycle and by the watchdog. States NO_SLATE / WAITING / RECORDED / RECORDED_NOT_PUBLISHED / MISSING_AT_DEADLINE. Reports date, slate, deadline, latest valid side-price capture, latest trader run, decision state and reason. One local notification per (date, failing state); persisted to `eggy_pick_guard.json`; a FAIL in the watchdog (`eggy_pick`).

## 3. Parlays: the held-ticket lifecycle

Before:

1. every cycle (~10.5 min) rebuilds the pool, reviews each leg, builds up to 10 tickets (`learning_parlays.build`);
2. a ticket whose legs are all `RECORD` records now; a ticket with any `LOWER_PRIORITY` leg is only counted as "waiting" (nothing is stored: a hold is not persisted, it is recomputed each cycle);
3. it records only when its first game is within `RELAX_MIN = 25` minutes of puck drop;
4. at recording the price is not re-fetched; eligibility is re-run every cycle; composition is whatever the current pool builds (idempotent by ticket key).

The wait exists so that "no better price or candidate for THIS game can still arrive". Evidence: **75 of 75 tickets ever recorded were recorded in that last window** (lead 5 to 25 minutes), so one host asleep in it loses the whole day. The engine buys each (game, market) once per wave, so for a prop already captured nothing better arrives in that window.

After: the same ticket also records as soon as `_no_newer_price_due` is true: every leg's price is FRESH or AGING, the capture schedule has no newer capture due for it, and its game's wave is known. Anything else (a stale or missing price, a capture still to come, unknown schedule) waits exactly as before. Never after puck drop: a ticket with any leg whose game has started is refused (`_has_started`), on top of the existing `event_not_started` leg gate. Stale or missing prices are still refused at construction.

This changes WHEN a lesser ticket records, not WHICH. The timing is part of the pinned cohort policy ("behaviour"), so the cohort digest changes (`aea7cf0fbcbc632f` -> `7a0821172921b822`) and the cohort fails closed until re-approved.

Limit: a ticket recorded early cannot be re-gated by roster or lineup information that arrives in the last hour (the engine has no feed for in-window scratches; the goalie feed is off).

## 4. Whether the Mac must stay awake

Still required for Eggy's decision (it needs a trader cycle between the pregame league pull and the first game) and for each wave's capture. The parlay window shrinks from the last 25 minutes before a game to the interval after the wave capture, so one sleep in the final half hour no longer loses recorded tickets. No power setting was changed.
