# Production Blocker Matrix

**As of 2026-09-25** (updated in the Live Run Reliability block; master `0902922`). Every row below was checked against repo/machine truth in this block; none is speculative. Component states come from `python3 opening_day_readiness.py`.

Categories: **BLOCKING** (a core product function cannot work until fixed) · **WAITING_ON_EXTERNAL_EVENT** (correct fail-closed state; needs the world to change) · **OWNER_ACTION** (only the owner can do it) · **OPTIONAL_IMPROVEMENT** · **FUTURE_RESEARCH**.

## BLOCKING

*None.* The T-35 architecture is armed (`MONEYLINE_T35_ARCHITECTURE = READY`); it is **not yet live-observed** — that is an external event, below.

## WAITING_ON_EXTERNAL_EVENT

| # | Item | Evidence |
|---|---|---|
| W1 | First DraftKings **player_shots_on_goal** (or `_alternate`) market | 0 of 1,015 archived payloads returned any `player_*` market key; `SOG MARKET CONTRACT: WAITING_FOR_LIVE_MARKET` (`PENDING_LIVE_CONTRACT`). When it appears the existing deterministic certification runs — never auto-verified. |
| W2 | First DraftKings **player_total_saves** market | same |
| W3 | A **confirmed starting-goalie source** (Saves stays `WAIT_ONLY`) | `goalie_status_events` holds only 4,236 `demo_generator` rows; no live source (`docs/STARTING_GOALIE_SOURCE_AUDIT.md`). Gate intentionally not weakened. |
| W0 | **`MONEYLINE_T35_LIVE_OBSERVED` = `WAITING_FOR_FIRST_REAL_CLUSTER`**: first real provider-listed cluster to complete pull → store → decide at T-30 → persist → publish (earliest 2026-09-29 ≈ 20:25Z). PASS/WAIT suffice; a BET is not required | `python3 -m operational.first_live_certification`; `docs/LIVE_MONEYLINE_CERTIFICATION.md` |
| W4 | First real moneyline recommendation → first real paper bet → first settlement → CLV → first real post-mortem sample | ledger 0 predictions; `REAL_MARKET_PAPER` 0 bets; post-mortem `WAITING_FOR_SETTLED_DATA` / `NO_DATA`; settlement `NO_REAL_SAMPLE_YET` |
| W5 | First real **Game Edge Parlay** sample | needs verified prop legs; `NO_QUALIFYING_GAME_EDGE_PARLAY` is correct |
| W6 | Real prop CLV | needs a real prop bet with a closing quote |
| W7 | Real-season results extending the Elo corpus | dashboard live edges show `WAIT`: "Elo rating is 167 days stale" (30-day policy); recomputes as results ingest — verify after the first week of games |

## OWNER_ACTION

Only what is still open (completed items removed):

| # | Item | Notes |
|---|---|---|
| O1 | **Confirm the Odds API reset day**, then set `NHL_ENGINE_ODDS_RESET_DAY=<1-28>` in `.env` | `OWNER_VERIFICATION_REQUIRED`; the provider exposes no reset date. Invalid values are rejected and treated as unset. Readiness: `ODDS_RESET_DAY` |
| O2 | **Streamlit UI** (nothing here is observable from the repo): confirm the app URL loads and shows `SNAPSHOT CURRENT`; Settings → Sharing → "Only specific people can view"; secrets `NHL_ENGINE_ADMIN_SETUP_CODE`, `NHL_ENGINE_TRUST_PLATFORM_VIEWER="ON"`, `NHL_ENGINE_ADMIN_EMAILS`; confirm `st.user.email` is populated when signed in | Evidence found: GitHub shows an **active Streamlit webhook** (created 2026-09-01, pushes delivered with HTTP 200), so the repo is connected — but the app URL is not in the repo. Readiness: `CLOUD_OWNER_CONFIGURATION`. Runbook: `docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md` |
| O3 | **Keep the Mac on, awake, online and plugged in ≈ 16:00–17:00 EDT on 2026-09-29** for the first real T-35 pull (≈ 16:25 EDT), and generally around game clusters and 07:00 | A missed window is recorded, never patched. (Changing power/sleep settings is left to you.) |
| O4 | Yahoo: `OWNER_AUTH_REQUIRED` | Isolated; not expanded |

## OPTIONAL_IMPROVEMENT

| # | Item | Notes |
|---|---|---|
| I1 | ~~props pull ~30 credits/day~~ **Done:** DISCOVERY mode, SOG/Saves keys only, ≤ 2 events, ≤ 6 credits/day, 0 while absent | `docs/PROP_DISCOVERY_BUDGET.md` |
| I2 | ~~Dynamic moneyline-pregame~~ **Done** (2-minute `StartInterval`, window-gated) | — |
| I3 | Today mixes real and simulated content; the demo slate (labeled SIMULATED — DEMO ONLY) still dominates the page | Consider moving the demo below the fold/into a tab once real recommendations exist |
| I4 | `nhl_sync.py` emits `datetime.utcnow()` DeprecationWarnings into the pregame/midday stderr logs (harmless noise) | trivial cleanup |
| I5 | ~~zero-byte `operational/runtime/prospective_observations.db`~~ **Removed** (0 bytes, nothing references it, creator not reproducible; recurrence would now show as `TEST_RUNTIME_ISOLATION: FAILED`) | — |
| I7 | ~~candidate-log / runtime pollution by tests~~ **Done (Live Run Reliability):** root cause fixed (`operational/state_paths.py`), the 2 synthetic records and the 0-byte stray DB removed with an audit trail (`operational/runtime/hygiene_removals.jsonl`) | `python3 -m operational.runtime_hygiene` |
| I6 | macOS python.org builds lack a CA bundle; the reader now falls back to `certifi` (done) — no action | — |

## FUTURE_RESEARCH

| # | Item |
|---|---|
| F1 | A licensed confirmed-goalie provider (e.g. RotoWire API is commercial) — needs an owner decision on cost; no unlicensed scraping |
| F2 | Team SOG / Blocked Shots settlement (`TEAM_SOG_NOT_INGESTED`, `BLOCKS_NOT_INGESTED`; Blocks has a methodology-drift risk) |
| F3 | VPS cutover — only if free Streamlit fails (`docs/VPS_CUTOVER_RUNBOOK.md`); would also cure O3 |

## Explicitly NOT blockers (verified)

Community Cloud memory (Today ≈ 147 MB, peak ≈ 198 MB, 25-cycle 179.6 MB — no regression), snapshot delivery (cloud-data verified, hash-matched), snapshot vs market freshness, ADMIN gating, Yahoo isolation, backups, settlement/post-mortem machinery, Odds API credits (368; zero spent by dashboard/cloud reads).


## Live Run Reliability findings (2026-09-25)

| Finding | Resolution |
|---|---|
| **Unit tests were pushing to the real GitHub `cloud-data` branch.** With `NHL_ENGINE_CLOUD_PUBLISH=ON` in the real `.env`, any test that ran a job's `_main()` launched the real publisher (11 of the day's 13 `cloud-data` commits carried the same `data_as_of`; the timestamps line up with test runs, though not every one is proven to be from a test). | Tests never read `.env`, the hook refuses under test, `publish()` refuses without an explicit remote, and the Odds API client refuses a real request under test |
| Tests wrote to `operational/ingestion_health_cache.json` and `operational/logs/nhl_api_retry_log.jsonl` (found by running the whole suite on an isolated copy of the repo and diffing every file) | Both now resolve into a throw-away directory under test |
| `daily-nhl-sync` `runs = 0` | Not a defect (Mac booted after the slot). Real exposure — a missed 07:00 sync stalling the day — closed by the bounded morning catch-up |
| Demo Today test depended on wall-clock time (failed after 3 h without a fresh pull) | made deterministic |
