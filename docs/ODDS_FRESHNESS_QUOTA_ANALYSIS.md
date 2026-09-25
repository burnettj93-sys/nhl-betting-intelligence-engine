# Odds Freshness × Quota Analysis

**Date:** 2026-09-25 (Production Activation block, Parts 11–13). **Method:** read-only. Launchd plists, job logs, the odds archive headers, `nhl.db` game start times and the code paths were inspected; **no Odds API credit was spent** and **no schedule was changed**. Reproduce the simulation with `python3 -m operational.odds_freshness_analysis`.

## 1. Odds API quota (Part 11)

| Fact | Value | Source |
|---|---|---|
| Plan size | **500 credits / month** (`x-requests-used` 132 + `x-requests-remaining` 368 at 2026-09-25 17:09Z) | archive response headers |
| Remaining | **368** | same |
| Reset day | **UNVERIFIED.** The API does not expose it; the governor assumes the 1st (`DEFAULT_CYCLE_RESET_DAY = 1`) → next reset 2026-10-01, ~6 days away | `operational/live_odds_daily_pull.py` |
| Burn, last 7.7 days | 77 credits ≈ **10 / day** (2026-09-18 00:07Z → 09-25 17:09Z: 445 → 368) | moneyline log ledger |
| Burn, last 3.2 days | 68 credits ≈ **21 / day** (436 → 368) | same |
| Projection to 10-01 at 21/day | ~130 more → ~**235 left at reset** (if the 1st-of-month assumption is right) | derived |
| Governor daily budget now | (368 − 20 floor) / 6 days ≈ **58 / day** | derived from `run_daily_pull` |

**Where the credits go.** Moneyline pull: **1 credit** (one sport-level call, all games). Sweeps (`prop-sweep-first/second`): 0 credits in almost every firing (windowed; nothing has matched). `daily-props-pull`: **28–31 credits per run** (2026-09-23: 28, 09-25: 31) — ~1 credit per tracked event. Scanning all **1,015** archived payloads (`operational/odds_archive/live` + `data/raw/the_odds_api/live`) shows the only market keys ever returned are `h2h` (1,125), `alternate_team_totals` (59), `spreads` (2), `totals` (2). **No `player_*` market has ever been returned** — so today the broad props pull spends ~30 credits/day to buy `alternate_team_totals`, a market no model uses.

**Zero-credit guarantees (verified).** No file under `dashboard/`, `operational/cloud_*`, or `operational/publish_cloud_snapshot.py` imports the Odds API client (`research/live_sog_pricing/client.py` is imported only for the `ApiResult` dataclass by `archive.py`, never called). `python3 -m operational.cloud_preflight` renders Today in Community Cloud mode with `socket.create_connection` instrumented: **0 network connections**. The Cloud reader's only request is a GET of the public GitHub snapshot URL. Dashboard page loads and snapshot reads therefore consume **0 credits**.

## 2. Freshness rules being measured

- **Display rule** (`cloud_snapshot_schema.classify_market_freshness`): price CURRENT if ≤ 180 min old; ≤ 90 min if the game starts within 4 h.
- **Decision-policy rule** (unchanged, `config.ODDS_STALENESS_TIERS` via `pricing/engine.py`): the real moneyline pipeline prices every game at an **anchor = puck drop − 30 min** and accepts only a DraftKings quote captured **≤ 10 min before that anchor** (tier "0.5–2 h out: 10 min"). This is far stricter than the display rule and is what actually gates a recommendation.

## 3. Simulation (Part 12)

Inputs: 83 real scheduled games in the next 14 days (12 game days) from `operational/runtime/nhl.db`; current moneyline pulls at 12:00, 17:00, 21:00, 00:00 UTC (launchd 08/13/17/20 EDT), assuming every pull succeeds.

| Metric | Current schedule (4 pulls/day) | + targeted pregame pull (proposal) |
|---|---|---|
| Games with a quote inside the **decision-policy window** (pull in [start−40 min, start−30 min]) | **1.2 %** (1 of 83) | 97.6 % |
| Games whose price is display-CURRENT at start−60 min | 79.5 % | 95.2 % |
| Games whose price is display-CURRENT at start−30 min | 66.3 % | 100 % |
| Share of the final 4 h before puck drop spent CURRENT | 43.8 % | 71.8 % |
| Median longest STALE stretch in the final 4 h | 90 min | 60 min |
| Extra credits | — | **≈ 4.2 per game day** (≈ 50 over these 12 game days) |

### Findings

1. **BLOCKING for real moneyline recommendations:** under the current cadence a game essentially never has a decision-policy-valid quote (1.2 %). `real_recommendation_orchestrator` evaluated 93 games at the 17:00Z pull today and produced `DATA_UNAVAILABLE` for all 186 sides — *correctly* (fail-closed): the newest quote is days old relative to each game's start−30 min anchor. The pipeline is healthy; the **polling cadence cannot feed it**. `opening_day_readiness.py` now reports this as `MONEYLINE RECOMMENDATION PIPELINE: PARTIAL`.
2. The display rule is satisfied for most games only by luck of the 21:00Z/00:00Z pulls (typical 7 pm ET puck drops); afternoon and 7:30/8 pm-ET games spend a long stretch STALE before puck drop.
3. Evening (00:00Z) pulls were sometimes never recorded: **8 of 41** logged moneyline runs failed with network errors (`ConnectionError` / `ReadTimeout`), and the 2026-09-24 20:00 EDT run is absent — the Mac was asleep/offline. See the blocker matrix.

### Smallest quota-efficient improvement (PROPOSAL ONLY — nothing changed)

Add **one league-wide moneyline pull at ≈ start−35 min for each start-time cluster** on a game day. One call returns every game, so games sharing a start time share one credit; ~4 clusters/day on busy days, 1–2 on light days. This lands each pull inside the 10-minute decision window, immediately followed by the existing bridge → orchestrator → cloud-publish chain, at ≈ 4 credits per game day (≈ 8 % of the remaining monthly budget per fortnight of game days). It needs **no model, threshold or decision-policy change**.

Funding it without touching SOG/Saves credits: the props pull's ~30 credits/day currently buy only `alternate_team_totals` (§1). Pausing or narrowing `daily-props-pull` until a `player_*` market first appears would free ~30 credits/day — ample for the pregame pulls **and** for the SOG/Saves sweeps when a contract finally appears. That is the owner's call; it is listed as OPTIONAL_IMPROVEMENT in `docs/PRODUCTION_BLOCKER_MATRIX.md`.

## 4. Dynamic polling review (Part 13) — proposal only

What existing code already supports:

| Need | Existing capability | Gap |
|---|---|---|
| Sparse polling far from puck drop | The 4 fixed daily pulls; `prop-sweep-first/second` already use internal windows (events 3–4.5 h / 45–75 min from puck drop) so most launchd firings are no-ops that cost 0 credits | none for props; moneyline has no window logic |
| Normal polling during the day | 08/13/17/20 EDT launchd calendar | fine |
| Targeted refresh before games | The sweep pattern: launchd `StartInterval`, the job decides internally whether to act | **no moneyline pregame mode** |
| Extra refresh only for actionable candidates | Bridge → orchestrator already runs after each moneyline pull and reports per-game status | orchestrator does not tell the poller which games are worth a refresh |

**Proposed design (not implemented):** a `--mode=moneyline-pregame` in `operational/live_odds_daily_pull.py` fired every 5 minutes via `StartInterval` (like the sweeps; most firings return instantly, 0 credits). It acts only when (a) some SCHEDULED game's anchor (start−30 min) is 30–40 min away, (b) no moneyline capture exists within the last 10 min before that anchor, and (c) the daily pregame-pull cap (e.g. 6) and the governor's budget allow it; then it performs the existing `run_moneyline_snapshot()` and the existing downstream chain unchanged. Same-cluster games share the call; idempotency keys already make a duplicate firing harmless. Optional refinement later: skip clusters whose games the Elo/goalie gates would WAIT anyway (saves credits, no policy change). Estimated cost ≈ 4 credits per game day. Because launchd only runs while the Mac is awake and online, the VPS fallback (`docs/VPS_CUTOVER_RUNBOOK.md`) remains the reliability answer if missed pulls stay frequent.
