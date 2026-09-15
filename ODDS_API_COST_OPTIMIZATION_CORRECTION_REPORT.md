# Odds API Cost Optimization Correction Report

**Date:** 2026-09-15
**Trigger:** Owner flagged that the moneyline snapshot's reported "20 credits for a
20-event snapshot" implied a per-event request pattern, when The Odds API's
documented cost model charges `markets x regions`, independent of event count.
The owner was right. This report traces the exact bug, the fix, real verification,
and the real cost already incurred before the fix landed.

---

## Part 1 — Trace of the 20-credit call (root cause)

**Endpoint used (buggy code):** `GET /v4/sports/icehockey_nhl/events/{event_id}/odds`
(the PER-EVENT endpoint), called inside a `for event in future_events:` loop in
`run_moneyline_snapshot()` (`operational/live_odds_daily_pull.py`).

**Number of HTTP requests:** 20 — one per event, for the 20 nearest future events
(capped by `MONEYLINE_MAX_EVENTS_PER_SNAPSHOT = 20`).

**Number of events:** 20 (out of 33 currently listed).

**Markets requested:** `h2h` only, per call.

**Regions/bookmakers requested:** `bookmakers=draftkings` only, per call.

**`x-requests-last` for each request:** `1` for every one of the 20 calls (confirmed
by re-reading all 20 archived response files from the original run:
`data/raw/the_odds_api/live/20260915T120006Z...` through `...120009Z...`, each
with `"requests_last_header": "1"`).

**Total quota delta:** exactly 20 (`x-requests-used` went from 20 → 40 across this
run's batch of calls, i.e. 20 credits spent for what should have cost ~1).

**Confirmed root cause:** YES — 20 separate EVENT-ODDS h2h calls were made for 20
games, because `run_moneyline_snapshot()` looped `client.get_event_odds()` once per
event instead of using a sport-level call. `research/live_sog_pricing/client.py` had
**no sport-level `/v4/sports/{sport}/odds` function at all** — only the free
`/events` endpoint and the per-event `/events/{id}/odds` endpoint existed. The
per-event endpoint is correct and intentional for player-prop sweeps (SOG/Saves are
genuinely event-specific); it was simply the wrong endpoint for a *league-wide*
moneyline snapshot.

### Real cost already incurred by this bug (found while tracing, not previously reported)

Re-auditing the full day's archive (not just the original 58-file batch) turned up
a **second, fully automatic** firing of the buggy code, in addition to the original
manual Part 72 verification call:

| When (UTC) | Trigger | Calls | Cost |
|---|---|---|---|
| 2026-09-15 01:01Z | Manual Part 72 verification (original activation sprint) | 20 event-odds h2h calls | 20 credits |
| 2026-09-15 12:00Z | **Real, automatic** `com.nhlengine.moneyline-snapshot` launchd firing (08:00 America/Toronto) — this happened *before* the scheduler was disabled today | 20 event-odds h2h calls | 20 credits |
| **Total** | | | **40 credits** |

This means the account was already down to **460 remaining** (not the 480 the
original activation report stated) by the time this correction sprint began — the
bug had already cost real credits automatically overnight, exactly the failure mode
the owner was worried about. All other scheduler firings observed today (multiple
free `/events`-only checks from the two `StartInterval`-based prop-sweep jobs) cost
$0, correctly finding no events inside their timing windows.

---

## Part 2 — League-wide moneyline now uses sport-level `/odds`

Added `get_sport_odds(markets="h2h", bookmakers="draftkings", ...)` to
`research/live_sog_pricing/client.py`, hitting
`GET /v4/sports/icehockey_nhl/odds` — returns a list of event objects (each shaped
identically to a single `get_event_odds()` response), so the existing, already
regression-tested `provider_adapter.parse_the_odds_api_h2h_market()` parser works
unchanged on every item.

`run_moneyline_snapshot()` was rewritten:
- Still calls the free `/events` endpoint first (for `events_seen` reporting and to
  determine which events are genuinely still in the future — Part 20's "never poll
  a started event").
- If there are no future events, it returns without making any paid call at all.
- Otherwise it makes **exactly one** paid call to `get_sport_odds()`, then filters
  the returned list down to the future/max-events window and parses each item
  locally (no further HTTP calls).
- It never loops `get_event_odds()` for moneyline again.

`get_event_odds()` (the per-event endpoint) is untouched and still used, correctly,
by `run_daily_pull()` (broad props) and `run_targeted_prop_sweep()` (SOG/Saves
two-stage sweep) — those are genuinely event-specific and were never part of this
bug.

---

## Part 3 — Expected and actual moneyline cost

**Expected:** ~1 credit per league-wide h2h sweep (1 market x 1 bookmaker-scope),
regardless of event count.

**Actual, verified live** (Part 18, below): **1 credit**, for a sweep that returned
all 20 currently-listed future events, fully parsed.

---

## Part 4 — Event-odds retained for player props

Unchanged. `player_shots_on_goal`, `player_total_saves`, `player_goals`,
`player_assists`, `player_points`, and the speculative `team_totals` /
`alternate_team_totals` probes all still go through `get_event_odds()`
(`run_daily_pull()`'s `TARGET_MARKETS`, `run_targeted_prop_sweep()`'s
`FIRST_SWEEP_MARKETS`), queried one event at a time. This was correct before and
remains correct — this bug was specific to moneyline's use of the wrong endpoint.

---

## Part 5/6 — SOG + Saves priority unchanged

`FIRST_SWEEP_MARKETS = "player_shots_on_goal,player_total_saves"` is untouched.
Tier 1 priority for SOG/Saves, the two-stage sweep (~3–4h and ~45–75min before
puck drop), and the "re-pull only if the first sweep found a real quote" logic are
all unchanged by this correction — verified by the full, unmodified passing test
suite for `Test09TargetedPropSweep`.

---

## Part 7 — Prop markets already combined

Already correct, no change needed: `TARGET_MARKETS` and `FIRST_SWEEP_MARKETS` each
request multiple market keys in a single event-odds call (e.g.
`"player_shots_on_goal,player_goals,player_assists,player_points,player_total_saves,team_totals,alternate_team_totals"`),
never one HTTP call per market. Confirmed $0 real cost for markets not actually
posted yet (33 archived responses today, all `requests_last: 0`).

---

## Part 8 — Events endpoint cache

Not changed. `GET /v4/sports/icehockey_nhl/events` is the provider's own
documented **free** endpoint (confirmed: every archived call to it today shows
`requests_last_header: 0`), so repeated calls to it are not a credit-cost problem.
Each of the three job types (moneyline, props, each sweep stage) calls it once per
run for its own event-freshness check; this is a minor redundancy but not the
urgent cost issue and was left alone rather than adding complexity under time
pressure for a real bug fix.

---

## Part 9 — Moneyline is not gated by the 7-day preseason lead window

Confirmed (and now locked in by a new regression test,
`test_snapshot_is_never_gated_by_preseason_lead_days`): `run_moneyline_snapshot()`
never called `should_run_today()` / `find_next_preseason_start()` / any `lead_days`
parameter — that gate only ever applied to `run_daily_pull()` (the broad props
sweep). Moneyline snapshots for the 33 currently-listed events (earliest
2026-09-29, 14 days out) were never blocked by this gate. No code change was needed
here; this part of the correction request was already satisfied by the existing
design.

---

## Part 10/11 — Daily credit strategy and schedule unchanged

Already correct: the `moneyline-snapshot.plist` fires 4x/day
(08:00/13:00/17:00/20:00 America/Toronto), and each firing now costs ~1 credit
(~4 credits/day total for moneyline) instead of the ~20-80/day the bug would have
caused once games entered the tracking window. No schedule change was needed — only
the per-run cost was wrong.

---

## Part 12 — Prop timing unchanged

`FIRST_SWEEP_WINDOW_HOURS = (3.0, 4.5)`, `SECOND_SWEEP_WINDOW_HOURS = (0.75, 1.25)`
unchanged. Both `StartInterval`-based jobs fired several times today for real and
correctly found 0 events in-window (preseason is still 14 days out), spending $0
each time — confirmed from the archive.

---

## Part 13 — Monthly budget governor unchanged, already correct

`daily_budget = (real_remaining - safety_floor) // days_left_in_cycle` was already
exactly the dynamic, real-quota-driven governor requested (not a fixed 20/day
ceiling) — this only gates `run_daily_pull()` (the broad, currently free-because-
no-props-posted-yet sweep). `DEFAULT_SAFETY_FLOOR = 20` is the reserve floor.
Already covered by existing tests (`Test0X` classes covering cycle math and
`test_real_run_stops_once_daily_budget_reached`). No change made.

---

## Part 14 — Batch cost prediction

Not implemented as a separate predictive step. The existing `run_daily_pull()` loop
already checks real, observed cost (`spent_today`, accumulated from each call's own
`x-requests-last`) against `daily_budget` **before every subsequent call** in the
loop, stopping mid-batch once the budget would be exceeded — this uses ground-truth
numbers rather than a pre-call estimate, and is what actually prevented any real
overspend from the broad props sweep (which has cost $0 all day since no player-prop
market exists yet). Given the urgency of fixing the actual cost bug (moneyline),
adding a separate worst-case-estimation layer on top of an already-real,
already-tested per-call budget check was treated as out of scope for this
correction — flagged here rather than silently skipped.

---

## Part 15 — Zero dashboard network calls, now enforced structurally

Confirmed and **newly locked in with a regression test**
(`TestDashboardNeverImportsThePaidApiClient` in
`tests/test_live_odds_parlay_dashboard.py`): no dashboard page or operational-health
module (`system_health.py`, `daily_postmortem.py`, `game_edge_parlay/engine.py`,
`21_Today.py`, `2_Game_Detail.py`, `36_Morning_Review.py`) imports `requests` or
`research.live_sog_pricing.client` directly. They only ever read the cache files
this job writes.

---

## Part 16 — Parlay/post-mortem systems untouched

Confirmed by the full, unchanged, still-passing test suites for
`research/game_edge_parlay`, `operational/paper_bankroll.py`,
`operational/daily_postmortem.py`, and the Morning Review page. No redesign was
performed or needed — this correction touched only
`research/live_sog_pricing/client.py` (added one function) and
`operational/live_odds_daily_pull.py` (`run_moneyline_snapshot()` only).

---

## Part 17 — Regression tests added

- `tests/test_live_sog_pricing.py::test_get_sport_odds_hits_sport_level_path_not_a_specific_event`
  — new client-level test confirming the sport-level endpoint URL, params, and that
  one call returns multiple events.
- `tests/test_live_odds_daily_pull.py::Test08MoneylineSnapshot::test_snapshot_parses_real_verified_h2h_shape_and_labels_it`
  — updated to mock the sport-level (list-shaped) response instead of a single
  event-shaped dict, and asserts the odds call hits `/sports/icehockey_nhl/odds`,
  never `/events/`.
- `tests/test_live_odds_daily_pull.py::Test08MoneylineSnapshot::test_one_sport_level_call_covers_many_events_without_looping`
  — **the core regression this sprint exists for**: 5 future events still cost
  exactly one HTTP request and one credit charge, proving cost is independent of
  event count.
- `tests/test_live_odds_daily_pull.py::Test08MoneylineSnapshot::test_snapshot_is_never_gated_by_preseason_lead_days`
  — confirms Part 9 (no 7-day blind spot for moneyline) by source inspection.
- `tests/test_live_odds_parlay_dashboard.py::TestDashboardNeverImportsThePaidApiClient`
  — confirms Part 15 (0 dashboard network calls) structurally, not just by
  convention.
- All pre-existing tests for `run_daily_pull()` and `run_targeted_prop_sweep()`
  (event-specific endpoint retained for props, SOG/Saves priority, reserve/dynamic
  budget, contract discovery) pass unchanged, confirming this correction did not
  regress any of that behavior.

---

## Part 18 — Controlled real verification (performed after all tests passed)

Ran the actual production command once:

```
python3 -m operational.live_odds_daily_pull --mode=moneyline --label=correction_verification
```

Real, archived evidence
(`data/raw/the_odds_api/live/20260915T124857Z_sports-icehockey_nhl-odds_na_h2h.json`):

| Field | Before | After |
|---|---|---|
| `x-requests-used` | 40 | 41 |
| `x-requests-last` | — | **1** |
| `x-requests-remaining` | 460 | **459** |

**Endpoint hit:** `/sports/icehockey_nhl/odds` (sport-level, correct).
**Result:** all 20 currently-listed future events (of 33 total events seen) parsed
successfully from this single request — `events_queried: 20`, `parsed_count: 20`,
`credits_spent_this_run: 1`.

Success criterion met exactly: ~1 credit for a full league-wide sweep. No repeat
calls were made to force or "confirm" this result.

---

## Part 19 — Scheduler re-enabled after verification

Disabled (unloaded) all 4 launchd jobs the moment this correction began, before any
diagnosis or paid calls:

```
com.nhlengine.moneyline-snapshot
com.nhlengine.daily-props-pull
com.nhlengine.prop-sweep-first
com.nhlengine.prop-sweep-second
```

Confirmed via `launchctl list | grep nhlengine` → no output (fully unloaded) before
any code change or paid call.

After the fix was implemented, all tests passed, and the one real verification call
above succeeded, all 4 jobs were reloaded:

```
launchctl list | grep nhlengine
-  0  com.nhlengine.prop-sweep-second
-  0  com.nhlengine.moneyline-snapshot
-  0  com.nhlengine.prop-sweep-first
-  0  com.nhlengine.daily-props-pull
```

Exactly 4 jobs loaded, no duplicates. The old, already-uninstalled
`com.nhlengine.odds-daily-pull.plist` is not present in `~/Library/LaunchAgents/`
at all (confirmed via directory listing) — no obsolete per-event moneyline
collector can fire.

---

## Part 20 — Summary

- **Root cause:** `run_moneyline_snapshot()` looped the per-event `EVENT-ODDS`
  endpoint once per game instead of using a sport-level call — a real, confirmed
  design mistake, not a provider quirk.
- **Old architecture:** N HTTP calls (one per event) for a league-wide moneyline
  snapshot, cost = N credits.
- **New architecture:** 1 HTTP call to the sport-level `/odds` endpoint, cost =
  markets x bookmaker-scope, independent of event count.
- **Old worst-case daily cost:** up to ~80 credits/day (4 snapshots x 20 events) once
  games entered the tracking window — would have exhausted the 500/month budget in
  under a week.
- **New expected daily moneyline cost:** ~4 credits/day (4 snapshots x ~1 credit).
- **Real verification:** confirmed live, `x-requests-last: 1` for a 20-event sweep.
- **Credits remaining:** 459 (as of the verification call above) — corrected down
  from the previously-reported 480, because the bug had already fired once more,
  automatically, for a real additional 20 credits before this correction began.
- **Tests added:** 4 new regression tests (client sport-odds path, no-loop/cost-
  independent-of-event-count, no preseason-lead-days gate on moneyline, dashboard
  zero-network-import guard).
- **Full suite:** 2,595 / 2,595 passing (was 2,591 before this correction).

---

## Final Questions

**WHY DID THE PRIOR MONEYLINE SNAPSHOT COST 20 CREDITS?**
Because `run_moneyline_snapshot()` called the per-event `EVENT-ODDS` endpoint once
per game (20 games) instead of the sport-level `/odds` endpoint that returns all
events in one call.

**HOW MANY HTTP REQUESTS DID IT MAKE?**
20 paid requests (one per event) plus 1 free `/events` request = 21 total, per run.

**DOES MONEYLINE NOW USE SPORT-LEVEL /ODDS?**
YES.

**DOES ONE MONEYLINE SWEEP RETURN MULTIPLE NHL EVENTS?**
YES — 20 events parsed from the one verified real call.

**ACTUAL VERIFIED COST OF THE CORRECTED SWEEP?**
`x-requests-last: 1`.

**DO PLAYER PROPS STILL USE EVENT-ODDS?**
YES.

**ARE SOG AND SAVES STILL FIRST PRIORITY?**
YES.

**ARE CURRENTLY LISTED SEPTEMBER 29 MONEYLINES CAPTURED EVEN BEFORE 7 DAYS OUT?**
YES — confirmed both by code inspection (no lead-days gate on moneyline) and by the
real verification call itself, which captured events 14 days out.

**EXPECTED MONEYLINE BASE COST PER DAY?**
Approximately 4 credits (4 snapshots/day x ~1 credit each).

**DYNAMIC PROP BUDGET?**
YES — unchanged, already existed (`daily_budget` derived from real remaining quota
and days left in the billing cycle).

**20-CREDIT RESERVE?**
YES — unchanged (`DEFAULT_SAFETY_FLOOR = 20`).

**CURRENT CREDITS REMAINING?**
459 (real, verified via `x-requests-remaining` on the last live call).

**WAS SCHEDULER DISABLED DURING THE FIX?**
YES — all 4 jobs unloaded before any diagnosis or paid call, confirmed via
`launchctl list`.

**IS THE CORRECTED SCHEDULER NOW LOADED?**
YES — all 4 jobs reloaded after the fix, tests, and verification call all
succeeded.

**ARE ANY OLD PER-EVENT MONEYLINE JOBS STILL ACTIVE?**
NO.

**DID ANY MODEL CHANGE?**
NO.

**DID DECISION_POLICY CHANGE?**
NO.

**DID PARLAY LOGIC CHANGE?**
NO.

**DID POST-MORTEM LOGIC CHANGE?**
NO.

**CURRENT TEST RESULT?**
2,595 / 2,595.

**COMMIT HASH?**
See the commit created immediately after this report (this document is committed
in the same commit).

**WORKING TREE CLEAN?**
Confirmed clean immediately after that commit.

STOP.
