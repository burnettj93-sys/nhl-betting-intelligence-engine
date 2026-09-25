# Production Blocker Matrix

**As of 2026-09-25** (updated in the Quota + Moneyline Activation block; master `cb1d492`). Every row below was checked against repo/machine truth in this block; none is speculative. Component states come from `python3 opening_day_readiness.py`.

Categories: **BLOCKING** (a core product function cannot work until fixed) · **WAITING_ON_EXTERNAL_EVENT** (correct fail-closed state; needs the world to change) · **OWNER_ACTION** (only the owner can do it) · **OPTIONAL_IMPROVEMENT** · **FUTURE_RESEARCH**.

## BLOCKING

*None.* B1 (the moneyline cadence) is resolved in code and armed: the T-35 cluster pull job is loaded, the end-to-end dry run passes, decision-window coverage is 83 of 83 scheduled games (vs 1.2 %). It becomes *observed* at the first pull for a provider-listed game (2026-09-29 21:00Z game → pull ≈ 20:25Z). Awaiting that first real cycle is listed under WAITING_ON_EXTERNAL_EVENT, not as a blocker.

## WAITING_ON_EXTERNAL_EVENT

| # | Item | Evidence |
|---|---|---|
| W1 | First DraftKings **player_shots_on_goal** (or `_alternate`) market | 0 of 1,015 archived payloads returned any `player_*` market key; `SOG MARKET CONTRACT: WAITING_FOR_LIVE_MARKET` (`PENDING_LIVE_CONTRACT`). When it appears the existing deterministic certification runs — never auto-verified. |
| W2 | First DraftKings **player_total_saves** market | same |
| W3 | A **confirmed starting-goalie source** (Saves stays `WAIT_ONLY`) | `goalie_status_events` holds only 4,236 `demo_generator` rows; no live source (`docs/STARTING_GOALIE_SOURCE_AUDIT.md`). Gate intentionally not weakened. |
| W0 | **First real T-35 pull** for a provider-listed game (earliest 2026-09-29 20:25Z) and the first `DATA_UNAVAILABLE`-free decision | `next decision cluster` in Diagnostics / readiness `NEXT_T35_CLUSTER`; `operational/runtime/moneyline_pregame_state.json` records `in_decision_window` per cluster |
| W4 | First real moneyline recommendation → first real paper bet → first settlement → CLV → first real post-mortem sample | ledger 0 predictions; `REAL_MARKET_PAPER` 0 bets; post-mortem `WAITING_FOR_SETTLED_DATA` / `NO_DATA`; settlement `NO_REAL_SAMPLE_YET` |
| W5 | First real **Game Edge Parlay** sample | needs verified prop legs; `NO_QUALIFYING_GAME_EDGE_PARLAY` is correct |
| W6 | Real prop CLV | needs a real prop bet with a closing quote |
| W7 | Real-season results extending the Elo corpus | dashboard live edges show `WAIT`: "Elo rating is 167 days stale" (30-day policy); recomputes as results ingest — verify after the first week of games |

## OWNER_ACTION

| # | Item | Notes |
|---|---|---|
| O1 | ~~Reload `daily-nhl-sync`~~ **Withdrawn — misdiagnosis.** `runs = 0` was the Mac booting at 07:08 after the 07:00 slot; a `launchctl kickstart` proved launchd runs it (`runs = 1`, exit 0). | `docs/SCHEDULER_INVENTORY.md` |
| O2 | **Streamlit settings** (secrets, viewer allow-list, main file path) and the deployed-app smoke test | `docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md` §3–5. The app URL is not in the repo, so the smoke test is `OWNER_SMOKE_TEST_REQUIRED`. |
| O3 | **Keep the Mac on, awake and online**, especially ~T-35 before each game cluster and 07:00 | launchd does not replay slots missed while the Mac was off (boot 07:08 today); 8 of 41 moneyline runs failed on network errors (client now retries connection failures ≤ 3×). A missed T-35 window cannot be recovered. |
| O4 | **Confirm the Odds API quota reset date** on your the-odds-api.com account, then set `NHL_ENGINE_ODDS_RESET_DAY=<1-28>` in `.env` | `OWNER_VERIFICATION_REQUIRED`: no header/endpoint exposes it (verified with the free `/events` call). 368 of 500 credits remain; scenarios in `docs/PROP_DISCOVERY_BUDGET.md` |
| O5 | ~~Approve pregame pull and props trim~~ **Done (approved and implemented).** Review PR, then merge | `docs/ODDS_FRESHNESS_QUOTA_ANALYSIS.md` §5, `docs/PROP_DISCOVERY_BUDGET.md` |
| O6 | Yahoo: `OWNER_AUTH_REQUIRED` | Isolated; never blocks betting; not expanded |
| O7 | `NHL_ENGINE_CLOUD_PUBLISH=ON` in the local `.env` | **Done** in this block (gitignored, non-secret) |

## OPTIONAL_IMPROVEMENT

| # | Item | Notes |
|---|---|---|
| I1 | ~~props pull ~30 credits/day~~ **Done:** DISCOVERY mode, SOG/Saves keys only, ≤ 2 events, ≤ 6 credits/day, 0 while absent | `docs/PROP_DISCOVERY_BUDGET.md` |
| I2 | ~~Dynamic moneyline-pregame~~ **Done** (2-minute `StartInterval`, window-gated) | — |
| I3 | Today mixes real and simulated content; the demo slate (labeled SIMULATED — DEMO ONLY) still dominates the page | Consider moving the demo below the fold/into a tab once real recommendations exist |
| I4 | `nhl_sync.py` emits `datetime.utcnow()` DeprecationWarnings into the pregame/midday stderr logs (harmless noise) | trivial cleanup |
| I5 | A zero-byte `operational/runtime/prospective_observations.db` (gitignored) was created by a probe; harmless, could not be deleted by this session's permissions | `rm` it manually if desired |
| I7 | The real candidate log `operational/prop_contract_candidates.jsonl` was polluted by a past test run (two `fixture-*` records). Readers now ignore non-real event ids; the polluting test could still be fixed to patch the log path | — |
| I6 | macOS python.org builds lack a CA bundle; the reader now falls back to `certifi` (done) — no action | — |

## FUTURE_RESEARCH

| # | Item |
|---|---|
| F1 | A licensed confirmed-goalie provider (e.g. RotoWire API is commercial) — needs an owner decision on cost; no unlicensed scraping |
| F2 | Team SOG / Blocked Shots settlement (`TEAM_SOG_NOT_INGESTED`, `BLOCKS_NOT_INGESTED`; Blocks has a methodology-drift risk) |
| F3 | VPS cutover — only if free Streamlit fails (`docs/VPS_CUTOVER_RUNBOOK.md`); would also cure O3 |

## Explicitly NOT blockers (verified)

Community Cloud memory (Today ≈ 147 MB, peak ≈ 198 MB, 25-cycle 179.6 MB — no regression), snapshot delivery (cloud-data verified, hash-matched), snapshot vs market freshness, ADMIN gating, Yahoo isolation, backups, settlement/post-mortem machinery, Odds API credits (368; zero spent by dashboard/cloud reads).
