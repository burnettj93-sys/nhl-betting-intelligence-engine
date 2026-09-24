# Production Readiness Audit

**Date:** 2026-09-24
**Method:** Direct repository inspection (file structure, imports, live database queries, a full test run, `launchctl` state, and reading the actual schema/code — not assumption from prior session memory). Every classification below is backed by a command run during this audit; see the evidence noted per row.

**Baseline:** HEAD `d18a62b`, working tree clean. Full suite: **2,595 / 2,595 passing** (116.9s). Scheduler: 4 launchd jobs loaded (`moneyline-snapshot`, `daily-props-pull`, `prop-sweep-first`, `prop-sweep-second`), all Odds-API-only.

This repo is not a green-field project. It is a **very mature, single-developer research platform** (~70 dated sprint reports at the root, 113 test files, a 16-entry model registry) that has been rigorously honest with itself about what's real vs. simulated at every step. The core finding of this audit is simple and important: **almost every piece of infrastructure Phase 0–8 of the new brief asks for already exists in some form.** The gap is not missing code — it's that most of it has **never been exercised against a real, live game**, and two scheduled jobs that are load-bearing for the daily workflow (real NHL data sync, and settlement) **do not exist as scheduled jobs at all**, only as callable modules.

---

## A. Data ingestion

| Component | Status | Files | Data source | DB / tables | Scheduled? | Risk | Recommended action |
|---|---|---|---|---|---|---|---|
| NHL schedule/boxscore/roster ingestion | **WORKING** (code), **NOT SCHEDULED** (operationally) | `ingest/nhl_api.py`, `operational/nhl_sync.py` | Real NHL Web API (`api-web.nhle.com`) — confirmed live during the odds-cost-correction session (Sept 20) | `nhl.db` (root) via `db.py` — tables `games`, `player_game_stats`, `goalie_game_stats`, `roster_status_events`, `lineup_snapshots`, `pp_unit_snapshots`, `goalie_status_events`, `team_membership_events` | **NO launchd job.** `run_nhl_sync()` exists, is unit-tested, has never been installed as a scheduled job. | **P0.** `nhl.db`'s most recent game is **2025-11-27** — zero rows for any 2026-27 preseason game, including the real games played 2026-09-19 that the owner watched. The real-data half of this platform has been silently frozen since the freeze date while odds collection alone kept running. | Install a daily (or twice-daily) `operational.nhl_sync` launchd job, same pattern as the 4 odds jobs already running. |
| Injury status | **MISSING** (real), demo-only | `ingest/demo_data.py` (synthetic generator only) | None real | — | No | Phase 1's per-game validation chain and Phase 6's `LATE_SCRATCH`/`INJURY` reason codes have no real data to draw from yet. | Confirm NHL API injury-report coverage (it's typically the least-reliable public feed); if none is reliable, mark this explicitly `DATA_UNAVAILABLE` rather than silently absent. |
| Line combinations / linemates | **MISSING** | — (no file anywhere references this concept for real ingestion) | None | — | No | Points/Assists models and any future fantasy lineup logic have no real linemate signal. | Scope as P2 — the existing Points/Assists models already validate without it; don't block the season on it. |
| Play-by-play | **WORKING** (historical, regenerable) | `research/real_nhl_pbp/` | Real NHL API, 4-season historical corpus | `research/real_nhl_pbp/research_pbp.db` (612MB, gitignored) | Historical backfill only, not a live daily job | Low for now — PBP feeds research/backtesting, not the live daily loop | No change needed pre-season; revisit if any live feature needs same-day PBP. |
| MoneyPuck team/skater/goalie stats | **WORKING**, real | `operational/moneypuck_daily.py`, `research/moneypuck_ingestion/` | Real MoneyPuck CSV/ZIP archive | `research/moneypuck_ingestion/research_moneypuck.db` | Daily sync script exists (`moneypuck_daily.py`); confirm it's actually scheduled | Medium — system_health.py currently reports `moneypuck_team/skater/goalie: UNAVAILABLE` live right now (checked this audit) | Verify/install its scheduled job alongside the nhl_sync fix above. |

## B. Odds / market ingestion

| Component | Status | Files | Notes |
|---|---|---|---|
| DraftKings moneyline collection | **WORKING, real, verified** | `operational/live_odds_daily_pull.py::run_moneyline_snapshot`, `research/live_sog_pricing/client.py::get_sport_odds` | Fixed 2026-09-15 (was looping per-event, 20x overcost — see `ODDS_API_COST_OPTIMIZATION_CORRECTION_REPORT.md`). Scheduled 4x/day, ~1 credit/run, currently 440/500 monthly credits remaining. |
| Player-prop collection (SOG/Saves/Points/Assists/Goals) | **WORKING (code), zero real markets observed yet** | Same file, `run_daily_pull`, `run_targeted_prop_sweep` | Confirmed live this cycle: 0 credits spent, 0 quotes captured — DraftKings has not posted any player-prop market for any tracked event yet (earliest tracked event is 2026-09-29). This is an external-provider fact, not a bug. |
| Contract verification | **PARTIAL, by design** | `research/generic_prop_pricing/provider_adapter.py::VERIFIED_CONTRACTS` | Exactly one entry: `("draftkings", "MONEYLINE")`. No player-prop payload has ever been observed for real, so none can be verified — this is the correct, honest state, not a defect. |

## C. Probability models

| Model | Status (per `research/model_registry.py`, live-queried this audit) | Validated thresholds | Operational status |
|---|---|---|---|
| NHL_WIN_MODEL (moneyline, Elo) | VALIDATED | n/a | **PRODUCTION_READY** |
| PLAYER_SOG | VALIDATED | 2+/3+/4+/5+ | SHADOW_VALIDATED |
| GOALS | VALIDATED | 1+ | SHADOW_VALIDATED |
| ASSISTS | VALIDATED | 1+/2+ | RESEARCH |
| POINTS | EMPIRICAL_BASELINE_REMAINS_CHAMPION | 1+/2+ | SHADOW_VALIDATED |
| BLOCKED_SHOTS | VALIDATED | 1+/2+/3+ | RESEARCH |
| TEAM_SOG | VALIDATED | 20+/25+/30+/35+ | RESEARCH (no real bettable market exists) |
| GOALIE_SAVES | PARTIAL | 20+/25+ | RESEARCH |
| PLAYER_SOG_PERIOD | PARTIAL | most period/threshold combos | RESEARCH |
| TEAM_GOALS_PERIOD | ATTEMPTED_NOT_VALIDATED | none | NOT_OPERATIONAL |
| JOINT_SHOT_WORKLOAD, JOINT_SCORING_DEPENDENCE | VALIDATED | see registry | RESEARCH |
| PLAYER_CONTEXT_STATE + 2 context overlays | VALIDATED_OVERLAY (mixed) | narrow, single-state only | SHADOW_VALIDATED |
| PLAYER_SOG_PP_ROLE_OVERLAY | PARTIAL, shadow-only | 1+/2+/3+ | SHADOW_VALIDATED, never affects a real decision |

**Reading this table correctly:** "VALIDATED" here means *validated against historical data*, not *has ever priced a real bet*. Only NHL_WIN_MODEL (moneyline) has a verified real sportsbook contract to attach to at all. This is not a gap in the model layer — it's a gap in what DraftKings has posted, which the model layer correctly refuses to guess around (no invented market keys, no fabricated thresholds — confirmed in `_KNOWN_UNMODELED_MARKET_KEYS` / `NEW_CONTRACT_CANDIDATE` handling).

## D. Recommendation / decision engine

| Component | Status | Files |
|---|---|---|
| BET/WAIT/PASS/DATA_UNAVAILABLE decision logic | **WORKING** | `dashboard/eligible_bets.py`, `research/player_props/decision_policy.py` (`prop_decision_policy_v3`) |
| Game Edge Parlay construction (3-4 leg, ~70% joint P target) | **WORKING**, never exercised on a real game | `research/game_edge_parlay/engine.py` | Built 2026-09-15, 14 unit tests, correctly returns `NO_QUALIFYING_GAME_EDGE_PARLAY` for every real game so far (no real prop legs exist yet). |
| Confidence framework | **WORKING** | `research/confidence_lab/`, `CONFIDENCE_FRAMEWORK_REDESIGN_REPORT.md` |

## E. Immutable prediction snapshots (Phase 3 of the new brief)

**Status: PARTIAL — the hard part is already built, but real usage has never happened.**

`operational/prospective_ledger.py` (schema v3, `operational/prospective_schema.sql`) already implements almost exactly what was asked for:
- Append-only, **DB-trigger-enforced immutability** (`predictions_immutability` trigger aborts any UPDATE to a prediction-time field — only settlement columns are ever mutable).
- SHA-256 idempotency key, checkpoint ordering (`PRIMARY_DAILY` → `PRE_GAME_UPDATE`/`MARKET_REFRESH`).
- `model_version`, `model_hash`, `registry_version`, `registry_hash`, `decision_policy_version` — full version pinning per row.
- `data_freshness_status`, `market_no_vig_probability`, `odds_received_at_utc` vs `odds_captured_at_utc` distinction (exactly the "opening vs closing" discipline Phase 4 asks for).
- A generic `data_snapshot_references` JSON column intended to hold point-in-time feature context.

**What's unverified:** whether the specific granular fields the new brief lists by name (starting goalie state, roster state, injury state, line assignment, projected TOI/PP TOI, opponent features, rest/back-to-back status) are actually being populated into `data_snapshot_references` by any real caller today, or whether that column is currently written with a narrower payload. This needs a direct read of what `operational/record_daily_predictions.py` actually writes before either trusting or rebuilding it.

**The database this system writes to, `operational/prospective_observations.db`, does not exist on disk.** Confirmed via `ls` this audit. Zero real predictions have ever been recorded, by construction of "no real market has ever existed to record a prediction against."

## F. Settlement (Phase 5)

**Status: WORKING (code), never exercised on a real game, NOT SCHEDULED.**

`operational/outcome_resolver.py` + `operational/settle_daily_observations.py::run_settlement_batch()` — supports SOG/Goals/Assists/Points/Saves/Moneyline; Team SOG and Blocks fail closed (no real market exists for either). Idempotent by construction (only `PENDING` rows are ever touched). Reads game outcomes from `nhl.db` — **which has no rows past 2025-11-27**, so even if this were scheduled today, it would have nothing to settle against for any real 2026-27 game. This is directly downstream of the Section A gap (`nhl_sync` not scheduled).

**No launchd job exists for `operational.settle_daily_observations`.**

## G. Daily post-mortem (Phase 6)

**Status: WORKING (code), reachable only by loading the Morning Review dashboard page — NOT an automated daily job.**

`operational/daily_postmortem.py`, 34 unit tests, real 13-category failure taxonomy, `NO_ACTION`/`WATCH`/`INVESTIGATE`/`BUG_FIX`/`CHALLENGER_IDEA`/`HALT_MARKET` action classes, correctly gated so a model change is only ever *proposed*, never auto-applied. Verified live this session: it currently and correctly reports `WAITING_FOR_SETTLED_DATA` on every field, because nothing has settled (Section F). **No launchd job runs this automatically each morning** — it only runs when someone opens `dashboard/pages/36_Morning_Review.py`, and it never persists a report file from that path (deliberately, per that page's own docstring, to avoid a page-view writing to disk).

## H. Model learning / challenger discipline (Phase 7)

**Status: WORKING**, matches the brief's own philosophy closely already. `operational/challenger_registry.py` enforces `MIN_REPEATED_OCCURRENCES=5`, `MIN_UNIQUE_GAME_DATES=3`, and a strict `HYPOTHESIS → TESTING → SHADOW → PROMOTION_CANDIDATE` lifecycle with no auto-promotion path. Model versioning exists per-model in the registry (e.g. `sog_v1.4.2`-style strings are already the convention, though not literally in that exact format everywhere — worth standardizing).

## I. Model performance dashboard (Phase 8)

**Status: PARTIAL.** `operational/model_scorecard.py`, `dashboard/pages/22_Model_Health.py`, `32_Model_Learning.py`, `4_Model_Performance.py` already exist and compute ROI/hit-rate/CLV/calibration breakdowns by market, edge bucket, and confidence bucket — this is a real, built feature, not a gap. It has simply never rendered anything but zeros/waiting-states because there is no settled data yet (same root cause as D/F/G).

## J. Yahoo Fantasy integration — the most important finding of this audit

**Status: DUPLICATED across two codebases, and the more complete one is now likely NON-COMPLIANT with the just-signed agreement.**

There are **two independent, non-trivial Yahoo Fantasy integrations** that both exist right now:

1. **`fantasy/` inside this repo** (built 2026-09-01, commit `c1b60d7`) — a genuinely large, complete-looking module: `fantasy/yahoo/` (oauth.py, client.py, cache.py, contracts.py, identity.py, parser.py, sync.py), `fantasy/league/`, `fantasy/projections/` (fantasy_projection, category_value, goalie_value, schedule_value), `fantasy/recommendations/` (draft_rankings, drop_candidates, lineup_optimizer, matchup_strategy, streaming, trade_analyzer, waiver_wire — this is most of Phase 10's wish list, already written). **But `fantasy/storage/fantasy_store.db`'s `yahoo_tokens` table has 0 rows — this OAuth flow has never actually been completed for real.** It's built and tested against synthetic/demo data only.
2. **`~/yahoo-fantasy-cockpit`** — a separate repo, separately tested (its own pytest/venv), with its own README/SECURITY.md/PRIVACY_AND_COMPLIANCE.md, that **did complete a real, live-verified OAuth connection** (its own "Phase 1.5 verification workflow," committed for real on 2026-09-14).

**The compliance problem:** the newly-signed (2026-09-21) Yahoo API Access and Use Agreement §2.c.vii states plainly: *"Developer shall not store, cache or index the Yahoo Fantasy Information."* `fantasy/yahoo/cache.py` explicitly caches every Yahoo API response to disk (`fantasy_cache.db`), and `fantasy/storage/fantasy_store.py` persists roster/standings/matchup/available-player *snapshots* long-term in `fantasy_store.db`. Both modules' own docstrings already flag Streamlit Cloud's filesystem ephemerality as a *reliability* concern — but neither flags that persisting this data at all is now a *contractual* concern under the agreement actually signed. This was built three weeks before the agreement existed, so it isn't a mistake anyone made against a known rule — but it is now out of step with one.

**This directly conflicts with the new brief's own Phase 9 instructions** ("Do NOT persistently cache, store or index Yahoo Fantasy Information unless the signed agreement explicitly permits a particular implementation" — it doesn't) and is exactly the kind of ambiguity the brief says to stop and document rather than creatively interpret around.

**Recommended action (needs your decision, not mine to make silently):** pick one of:
- (a) Treat `~/yahoo-fantasy-cockpit`'s already-real, already-verified OAuth as the one to build on, and have this engine's `fantasy/` module consume it (or be replaced by a thin adapter) rather than maintain two.
- (b) Keep `fantasy/` as the one true integration (it's structurally already "a separate module," matching Phase 10's diagram), but rework its storage to fetch-on-demand per session instead of persisting to `fantasy_store.db`/`fantasy_cache.db` — likely means every recommendation feature (draft rankings, waiver wire, etc.) re-fetches from Yahoo each time rather than reading a local snapshot, which has real latency/rate-limit tradeoffs worth discussing before I build it.
- (c) Ask Yahoo, in writing, whether a specific caching implementation is permitted (the agreement itself invites this: "unless expressly approved in writing by Yahoo").

## K. Multi-user auth (Phase 11)

**Status: MISSING entirely.** Confirmed via repo-wide search — no `User`/`Auth`/`Login`/`Session` class, no role concept, anywhere in `dashboard/` or `operational/`. This is a from-scratch build, not a partial one. Streamlit's native multi-page app has no built-in auth layer, so this needs either `streamlit-authenticator`-style middleware or a reverse-proxy auth layer in front of it — worth a deliberate choice before writing code (see Implementation Plan).

## L. Deployment / reliability

| Aspect | Status | Notes |
|---|---|---|
| Local dev | WORKING | `.devcontainer/devcontainer.json` runs `streamlit run dashboard/app.py` with CORS/XSRF disabled — fine for solo local dev, **not safe as-is for a multi-user public deployment** (Phase 11 needs this hardened). |
| Cloud hosting | PARTIAL / KNOWN LIMITATION | `.streamlit/config.toml` + a "Streamlit Cloud deployment support" commit exist, but this project's own code already documents (in `fantasy/yahoo/cache.py`'s docstring) that Streamlit Community Cloud's filesystem is ephemeral — unsuitable for the SQLite-everywhere persistence model this whole platform relies on. This was also the explicit reasoning for running the odds scheduler on local `launchd` instead of in the cloud. |
| Scheduled jobs | PARTIAL | 4 real launchd jobs (odds only) exist and are loaded; `nhl_sync`, `settle_daily_observations`, `daily_postmortem` have **no scheduled job at all** — they are complete Python entry points sitting idle. |
| Health checks | PARTIAL | `operational/system_health.py` is real and already reports OK/STALE/WAITING/ERROR/NOT_REQUIRED/UNKNOWN per source, plus the live-odds-specific additions from the Sept 15 sprint. No single `/health` HTTP endpoint exists (Streamlit doesn't expose one by default) — the closest equivalent is a dashboard page render. |
| Structured logs / alerting | MISSING beyond raw launchd stdout/stderr files in `operational/logs/` | No log aggregation, no alerting (email/SMS/push) on failure. |
| Backups | MISSING for the two databases that hold real accumulating state (`operational/paper_bankroll.db`, and the not-yet-created `prospective_observations.db`) — both explicitly called out as "back up deliberately" in this project's own `.gitignore` comments, but no backup job exists yet. |

## M. Testing

**2,595 / 2,595 passing**, 113 test files. Strong coverage of: temporal-integrity/point-in-time leakage (a dedicated deliberate-leakage test), settlement idempotency, prospective-ledger immutability, live-odds cost/budget/scheduling logic (just extended this cycle), Game Edge Parlay construction rules, daily post-mortem taxonomy, Yahoo privacy isolation (`tests/test_fantasy_privacy.py` — worth re-checking against the new agreement's specific storage restriction, since a privacy-isolation test is not the same as a no-storage test). **Not yet tested, because it doesn't exist yet:** anything from Phase 11 (auth/route separation), any end-to-end real-game flow (nothing to test against).

---

## Summary classification table

| # | Component | Status |
|---|---|---|
| 1 | Odds collection (moneyline) | WORKING, real, verified |
| 2 | Odds collection (player props) | WORKING, waiting on DraftKings |
| 3 | NHL real data sync (schedule/box/roster) | **PARTIAL — code WORKING, job MISSING** |
| 4 | Injury ingestion | MISSING (real) |
| 5 | Line combinations | MISSING |
| 6 | Play-by-play / MoneyPuck historical | WORKING |
| 7 | Probability models (all families) | WORKING, historically validated; DUPLICATED nowhere, UNKNOWN real-market performance |
| 8 | Recommendation engine (BET/WAIT/PASS) | WORKING |
| 9 | Game Edge Parlay engine | WORKING, unexercised |
| 10 | Immutable prediction snapshots | PARTIAL |
| 11 | Market/odds snapshots + CLV | WORKING (`operational/clv_resolver.py`) |
| 12 | Settlement | **PARTIAL — code WORKING, job MISSING, no real data to settle** |
| 13 | Daily post-mortem | **PARTIAL — code WORKING, job MISSING** |
| 14 | Model learning / challenger discipline | WORKING |
| 15 | Model performance dashboard | PARTIAL (built, unexercised) |
| 16 | Yahoo integration | **DUPLICATED, and the more complete copy is likely NON-COMPLIANT with the signed agreement** |
| 17 | Multi-user auth | MISSING |
| 18 | Deployment hardening | PARTIAL |
| 19 | Structured logging / alerting | MISSING |
| 20 | Database backups | MISSING |
| 21 | Old root ELO/moneyline system (`db.py`, `schema.sql`, `models/`, `pricing/odds_math.py`) | **NOT DEPRECATED — still the live production DB for real NHL data** (clarification: `pricing/odds_math.py` is a shared pure-math utility used everywhere; `db.py`/`nhl.db`/`schema.sql` is the real production database for game/player/team data, not a legacy demo artifact as an earlier freeze doc once described it) |
