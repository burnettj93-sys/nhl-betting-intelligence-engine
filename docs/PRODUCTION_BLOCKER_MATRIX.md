# Production Blocker Matrix

**As of 2026-09-25** (updated in the Final Pre-Live Ops block; master `821a339`). Every row below was checked against repo/machine truth in this block; none is speculative. Component states come from `python3 opening_day_readiness.py`.

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

Only what is still open:

| # | Item | Notes |
|---|---|---|
| O1 | **Confirm the Odds API monthly reset day** and set `NHL_ENGINE_ODDS_RESET_DAY=<1-28>` in `.env` | Nothing on this machine reveals it (no header, endpoint, log or saved account page). On the-odds-api.com log in → **Account / Usage** and read the date your quota resets (or your subscription's renewal date). Invalid values are rejected. Readiness: `ODDS_RESET_DAY` |
| O2 | **Give the deployed Streamlit app URL**: add `NHL_ENGINE_STREAMLIT_URL=https://<your-app>.streamlit.app` to `.env` | Not discoverable from the repo, README, GitHub metadata, the Streamlit webhook or deployments (verified). With it, `python3 -m operational.cloud_preflight` performs the anonymous read-only reachability/privacy check |
| O3 | ~~Streamlit secrets / app-level ADMIN~~ **Not needed (2026-09-26):** Streamlit private sharing is the only access gate; the deployed app has no login, no USER/ADMIN accounts and requires no auth secrets. Optional: invite friends (Settings → Sharing) and delete the unused `NHL_ENGINE_ADMIN_SETUP_CODE` / `NHL_ENGINE_TRUST_PLATFORM_VIEWER` / `NHL_ENGINE_ADMIN_EMAILS` lines (the setup code currently in Secrets is a placeholder). Signed-in confirmation that Diagnostics shows REMOTE / schema 2 remains a quick owner check | Runbook §2b |
| O4 | **Schedule the one-time wake** (needs your sudo; never run automatically): `sudo pmset schedule wake "09/29/26 15:45:00" nhl-engine` (or `python3 -m operational.schedule_next_wake --apply`, which fails closed and prints it if a password is needed), then confirm with `pmset -g sched` / `--verify`. On the day: **plugged in, lid OPEN, not shut down** | AC idle sleep is 1 minute; a sleeping Mac runs nothing and `caffeinate` cannot wake it. The wake-guard + caffeinate then hold it through ≈ 16:50. If the Mac is shut down or the lid is closed, the window can still be missed (recorded `MISSED_WINDOW`, never pulled late) |
| O5 | **Merge the "final pre-live ops" PR** and leave the live checkout on clean `master` | The scheduler executes whatever the checkout has; pre-flight reports NOT_READY on a dirty/feature checkout |
| O6 | Yahoo: `OWNER_AUTH_REQUIRED` | Isolated; not expanded |

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
