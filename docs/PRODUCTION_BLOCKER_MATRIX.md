# Production Blocker Matrix

**As of 2026-09-25** (Production Activation block, after PR #6 merged at `336cf80`). Every row below was checked against repo/machine truth in this block; none is speculative. Component states come from `python3 opening_day_readiness.py`.

Categories: **BLOCKING** (a core product function cannot work until fixed) · **WAITING_ON_EXTERNAL_EVENT** (correct fail-closed state; needs the world to change) · **OWNER_ACTION** (only the owner can do it) · **OPTIONAL_IMPROVEMENT** · **FUTURE_RESEARCH**.

## BLOCKING

| # | Item | Evidence | Resolution |
|---|---|---|---|
| B1 | **No real moneyline recommendation can be produced under the current polling schedule.** The real pipeline prices each game at puck drop − 30 min and accepts only a DraftKings quote ≤ 10 min older than that (`config.ODDS_STALENESS_TIERS`); moneyline is pulled 4×/day, so only **1.2 %** of the next 83 games ever get a valid quote. Today's run: 186 of 186 sides `DATA_UNAVAILABLE` (fail-closed, correct). | `python3 -m operational.odds_freshness_analysis`; `docs/ODDS_FRESHNESS_QUOTA_ANALYSIS.md`; readiness: `MONEYLINE RECOMMENDATION PIPELINE: PARTIAL` | Owner decision on the smallest fix: one pregame moneyline pull per start-time cluster at ≈ start − 35 min (~4 credits per game day → 97.6 % coverage). **Not implemented** (schedule was frozen for this block); no decision-policy/threshold change needed. |

## WAITING_ON_EXTERNAL_EVENT

| # | Item | Evidence |
|---|---|---|
| W1 | First DraftKings **player_shots_on_goal** (or `_alternate`) market | 0 of 1,015 archived payloads returned any `player_*` market key; `SOG MARKET CONTRACT: WAITING_FOR_LIVE_MARKET` (`PENDING_LIVE_CONTRACT`). When it appears the existing deterministic certification runs — never auto-verified. |
| W2 | First DraftKings **player_total_saves** market | same |
| W3 | A **confirmed starting-goalie source** (Saves stays `WAIT_ONLY`) | `goalie_status_events` holds only 4,236 `demo_generator` rows; no live source (`docs/STARTING_GOALIE_SOURCE_AUDIT.md`). Gate intentionally not weakened. |
| W4 | First real moneyline recommendation → first real paper bet → first settlement → CLV → first real post-mortem sample | ledger 0 predictions; `REAL_MARKET_PAPER` 0 bets; post-mortem `WAITING_FOR_SETTLED_DATA` / `NO_DATA`; settlement `NO_REAL_SAMPLE_YET` |
| W5 | First real **Game Edge Parlay** sample | needs verified prop legs; `NO_QUALIFYING_GAME_EDGE_PARLAY` is correct |
| W6 | Real prop CLV | needs a real prop bet with a closing quote |
| W7 | Real-season results extending the Elo corpus | dashboard live edges show `WAIT`: "Elo rating is 167 days stale" (30-day policy); recomputes as results ingest — verify after the first week of games |

## OWNER_ACTION

| # | Item | Notes |
|---|---|---|
| O1 | **Reload the `daily-nhl-sync` launchd job** — it has never fired (`runs = 0`). **Time-critical:** settlement defers from 09-27 07:15 if no full sync runs. | Commands in `docs/SCHEDULER_INVENTORY.md` (Re-audit, Finding 1) |
| O2 | **Streamlit settings** (secrets, viewer allow-list, main file path) and the deployed-app smoke test | `docs/STREAMLIT_COMMUNITY_CLOUD_RUNBOOK.md` §3–5. The app URL is not in the repo, so the smoke test is `OWNER_SMOKE_TEST_REQUIRED`. |
| O3 | Keep the Mac **awake and online** at pull times (or accept gaps) | 8 of 41 moneyline runs failed on network errors; the 09-24 20:00 EDT pull is missing |
| O4 | **Confirm the Odds API quota reset date** on your the-odds-api.com account | The API does not expose it; the governor assumes the 1st. 368 of 500 credits remain |
| O5 | **Approve or decline** the pregame-pull proposal (B1) and the props-pull trim (I1) | `docs/ODDS_FRESHNESS_QUOTA_ANALYSIS.md` |
| O6 | Yahoo: `OWNER_AUTH_REQUIRED` | Isolated; never blocks betting; not expanded |
| O7 | `NHL_ENGINE_CLOUD_PUBLISH=ON` in the local `.env` | **Done** in this block (gitignored, non-secret) |

## OPTIONAL_IMPROVEMENT

| # | Item | Notes |
|---|---|---|
| I1 | `daily-props-pull` spends ~30 credits/day and has only ever returned `alternate_team_totals` (no model uses it) | Pausing/narrowing it until a `player_*` market appears frees credit for B1 and the SOG/Saves sweeps |
| I2 | Dynamic `--mode=moneyline-pregame` (5-minute `StartInterval`, window-gated like the sweeps) | Design in the quota analysis; supersedes fixed extra pulls |
| I3 | Today mixes real and simulated content; the demo slate (labeled SIMULATED — DEMO ONLY) still dominates the page | Consider moving the demo below the fold/into a tab once real recommendations exist |
| I4 | `nhl_sync.py` emits `datetime.utcnow()` DeprecationWarnings into the pregame/midday stderr logs (harmless noise) | trivial cleanup |
| I5 | A zero-byte `operational/runtime/prospective_observations.db` (gitignored) was created by a probe; harmless, could not be deleted by this session's permissions | `rm` it manually if desired |
| I6 | macOS python.org builds lack a CA bundle; the reader now falls back to `certifi` (done) — no action | — |

## FUTURE_RESEARCH

| # | Item |
|---|---|
| F1 | A licensed confirmed-goalie provider (e.g. RotoWire API is commercial) — needs an owner decision on cost; no unlicensed scraping |
| F2 | Team SOG / Blocked Shots settlement (`TEAM_SOG_NOT_INGESTED`, `BLOCKS_NOT_INGESTED`; Blocks has a methodology-drift risk) |
| F3 | VPS cutover — only if free Streamlit fails (`docs/VPS_CUTOVER_RUNBOOK.md`); would also cure O3 |

## Explicitly NOT blockers (verified)

Community Cloud memory (Today ≈ 147 MB, peak ≈ 198 MB, 25-cycle 179.6 MB — no regression), snapshot delivery (cloud-data verified, hash-matched), snapshot vs market freshness, ADMIN gating, Yahoo isolation, backups, settlement/post-mortem machinery, Odds API credits (368; zero spent by dashboard/cloud reads).
