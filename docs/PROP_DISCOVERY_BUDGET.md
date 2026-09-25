# Prop Discovery Budget

**Date:** 2026-09-25 (Quota + Moneyline Activation block, Parts 9–16). Evidence comes from job logs, the odds archive headers and the code; **no credit was spent on the investigation** (one free `/events` call, `x-requests-last: 0`, confirmed the header set).

## 1. Why the old daily prop pull cost ~30 credits/day (Part 9)

`--mode=props` (08:15 launchd) ran `run_daily_pull()`: one **free** `/events` call, then **one `/events/{id}/odds` request per upcoming event — all ~33 — each asking for 7 market keys** (`player_shots_on_goal, player_goals, player_assists, player_points, player_total_saves, team_totals, alternate_team_totals`). The provider bills **one credit per market key that actually returns data** (per event, per region); a key that returns nothing costs 0.

| Date (12:15Z run) | Events queried | Quotes captured | Credits |
|---|---|---|---|
| 09-15 … 09-22 (six runs) | 33 each | 0 | **0** (33 × 7 markets requested, nothing returned → free) |
| 09-23 | 31 | 672 | **28** |
| 09-25 | 33 | 744 | **31** |

The change on 2026-09-23 is DraftKings starting to post **`alternate_team_totals`** (the only market key ever returned besides `h2h`, `spreads`, `totals`): ~1 credit per event. Scanning all 1,015 archived payloads confirms no `player_*` key has ever returned. So: **the spend was entirely `alternate_team_totals`, a market no model uses; unavailable markets do not consume credits (231 event-requests at 0 credits); all games were queried every day; the desired markets were requested together with the unwanted ones.** The governor's daily budget ((remaining − 20)/days-left ≈ 58/day) was not a brake — it only stops spending after 58 credits.

A second latent problem was found while auditing the sweeps: `prop-sweep-first/second` (every 30/15 min) re-queried every in-window event **on every firing** — ~5 requests per event per day. Free while nothing is posted, but the moment SOG/Saves appear that multiplies the cost ~5×. The sweeps have never actually queried an event (0 of 542 firings had one in the 3–4.5 h window).

## 2. What changed (Parts 10–15)

| | Before | After |
|---|---|---|
| Markets requested by the daily job | 7 keys incl. `alternate_team_totals`, `team_totals`, goals/assists/points | **only `player_shots_on_goal`, `player_shots_on_goal_alternate`, `player_total_saves`** (`TARGET_MARKETS`) |
| Events per run | all (~33) | **DISCOVERY: ≤ 2 sampled** (soonest games within 36 h; books post props closest to game day) |
| Daily prop budget | none (governor pace ≈ 58/day) | **DISCOVERY hard cap 6 credits/day**, plus the account reserve |
| On first appearance | n/a | flag `CANDIDATE_OBSERVED` (existing append-only log) and **STOP**; no expansion, **no auto-verify** |
| Sweeps | re-query the same event every firing | **once per event per stage per day**; in DISCOVERY they share the 6-credit budget and stop at the first appearance |
| Cloud publish after the props job | whenever it ran | only if it spent credits or observed a candidate |

**Feasible discovery cost while the markets are absent: 0 credits/day** (the trimmed request returns nothing, and an unposted market is free — the six 09-15…22 runs are the proof). The cap is 6/day; the plan's "≤ 4–6 credits/day" target is met by construction.

## 3. Modes and the market-state transition

```
PENDING_LIVE_CONTRACT ──(market returns real outcomes on a sampled event)──▶ CANDIDATE_OBSERVED
CANDIDATE_OBSERVED ──(human runs operational/prop_contract_certification.py and adds the
                      (sportsbook, canonical id) tuple to provider_adapter.VERIFIED_CONTRACTS)──▶ VERIFIED
```

* **DISCOVERY_MODE** — active while *no* PLAYER_SOG / GOALIE_SAVES contract is VERIFIED. Only still-`PENDING` desired keys are requested. When every desired key is a candidate the job reports `WAITING_FOR_CERTIFICATION` and makes **no calls at all**.
* **VERIFIED_PRODUCTION_MODE** — active once at least one contract is VERIFIED. The daily pull requests **verified keys only**; sweeps keep their windows and now query each event at most once per stage per day. It **cannot run while a contract is pending** (`run_props()` chooses by mode; tests assert the discovery job makes no calls in production mode and the daily pull is never used in discovery).
* State is *derived* — `provider_adapter.VERIFIED_CONTRACTS` for VERIFIED, the candidate log for CANDIDATE — never set by hand and never advanced by code. Candidate records only count for real Odds API event ids (32-hex): the real candidate log had been polluted with two `fixture-*` records by a past test run, which would otherwise have flipped both markets to CANDIDATE_OBSERVED.

**Projected cost once VERIFIED (not enabled):** SOG + Saves are 2 markets/event; with once-per-stage sweeps that is ≤ 4 credits per event per day, i.e. ≈ 15–30 credits on a 4–8 game slate — sustainable only alongside the ~8/day moneyline spend, so the governor stays in force.

## 4. Quota economics (Part 16)

Balance 368 of 500 (132 used); reserve 20 → 348 usable. **Reset date: OWNER_VERIFICATION_REQUIRED** — no response header or endpoint exposes it (the free `/events` response carries only `x-requests-used/-remaining/-last`). Scenarios instead of an assumption:

| Schedule | Credits/day | /week | Days until exhaustion (to reserve) |
|---|---|---|---|
| **OLD** — 4 moneyline pulls (4) + broad daily prop pull (~31) | ≈ 35 | ≈ 245 | **≈ 9.9** (≈ Oct 5) |
| **NEW, typical** — 4 fixed moneyline + ~4.1 pregame cluster pulls (41 clusters / 10 game days, unlisted games cost 0) + discovery 0 | ≈ 8.1 | ≈ 57 | **≈ 43** |
| **NEW, worst case** — as above with discovery at its 6/day cap | ≈ 14.1 | ≈ 99 | ≈ 24.7 |

| If the plan resets in… | OLD leaves at reset | NEW typical | NEW worst case |
|---|---|---|---|
| 6 days (Oct 1) | 158 | 319 | 283 |
| 20 days | **exhausted at day ~10** | 206 | 86 |
| 37 days (Nov 1) | **exhausted at day ~10** | 68 | exhausted near day 25 |

The old schedule only survives if the reset is within ~10 days; the new one survives every scenario in typical operation.

Set the verified reset day (owner, once checked on the provider account) with `NHL_ENGINE_ODDS_RESET_DAY=<1-28>` in `.env`; until then every projection above is labeled an assumption.
