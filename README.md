# NHL Betting Intelligence Engine — Temporal-Integrity Research Platform (v2.1.2a)

This is a **research platform for NHL moneyline pricing**, not a live betting
tool. Nothing here should be used to place a real bet. See "What this is NOT"
below before doing anything else.

**Passing the temporal-integrity test suite demonstrates historical research
correctness, not the existence of a real-world betting edge.** Keep that
sentence in mind reading every table and every PASS below.

v2's job was to make historical predictions *temporally valid* — provably
built only from information that existed at the moment each prediction
claims to have been made — and reproducible. v2.1 (this pass) is a **pure
architecture/correctness hardening slice, not a feature slice**: no new
predictive features, no model-sophistication improvements (the player model
is still a simplified PPG heuristic, not RAPM/GAR/xGAR; the goalie model is
still simplified save% with regression — both intentionally, per explicit
instruction). Its one job was to close every remaining way a *later* fact —
a corrected stat, a rescheduled game, a model trained further forward in
time — could silently change what an *earlier* prediction claims it would
have known. See "v2.1 temporal-hardening pass" below for exactly what that
closed. v2.1.1 (this pass, "final temporal closure") is the same kind of
slice again — an independent review of v2.1 found three remaining
historical-reconstruction gaps (game-ID/list-position leftovers in
`run_slate.py`, a mutable/overwritable `games` result cache, and an
inconsistency in exact-timestamp-tie contamination semantics), plus
timestamp-representation risk ahead of live ingestion. See "v2.1.1 final
temporal closure" below. Still no new predictive features, no
model-sophistication improvements, no new betting markets. v2.1.1a
("correctness patch") closed five further narrow correctness gaps found
during a second independent review (odds receipt-time knowledge, the
model's true knowledge watermark, whether the displayed max acceptable
price matched the engine's own edge math, whether schedule and
model-learning identity could disagree, and whether result history was
structurally protected) — see the `claude/nhl-engine-v2.1.1a-correctness-
patch.md` project note for that slice's full write-up. **v2.1.2 (this
pass, "real NHL core ingestion readiness")** is a third, still-narrow
slice: a third independent review found six gaps specifically blocking
the first attempt at REAL (not synthetic) NHL data ingestion — a
fresh-database foreign-key bootstrap bug, a hardcoded synthetic team
universe in production paths, an incomplete v2.1.1a knowledge watermark
(missed schedule revisions), a `games` cache that could silently
disagree with schedule history on reingestion, an overclaimed "real
player identities" ingestion criterion, and undefined historical-backfill
knowledge-time semantics. See "v2.1.2 real-NHL-core-ingestion-readiness
patch" below. Still no new predictive features, no model-sophistication
improvements, no new betting markets, and no SOG development. **v2.1.2a
(this pass, "live API contract closure")** is a fourth, still-narrow
slice: a fourth independent review checked the v2.1.2 package against the
NHL Web API's *actual* real response shape and found seven live-
integration gaps — a real boxscore's per-skater shots-on-goal field is
named `sog`, not `shots` (so real skater shots were silently always
stored as zero); the live idempotency rerun never actually reingested
boxscores or roster snapshots, so it could never prove real stat/roster
idempotency; CURRENT team roster membership (`/v1/roster/{team}/current`)
was being conflated with SEASON roster identity
(`/v1/roster/{team}/{season}`); current-roster snapshots had no
reconciliation for a player who disappears (or a corrected name/
position); a single batch-start timestamp could be stamped on a
boxscore response that actually arrived much later; `validate_live_nhl.py`
wasn't yet a real contract test (no FK check, no required-field
hardening on the boxscore path, no canonical-timestamp check); and its
default date-range selection (today minus 7 days) is not robust during
the NHL offseason. See "v2.1.2a live-API-contract-closure patch" below.
Still no SOG *prediction* functionality, no odds-provider integration,
no new betting markets, and no model-weight tuning — ingestion/
validation correctness only.

```
DATA INGESTION → point-in-time queries → FEATURE SNAPSHOT (pure dict)
   → PROBABILITY MODEL (pure function) → DraftKings market comparison
   → EDGE / EV THRESHOLDS → BET / WAIT / PASS / DATA_UNAVAILABLE
```

Run `python3 validate.py` for a single command that reports test results,
data-quality counts, temporal-integrity structural checks, and a
walk-forward backtest, all in one place — treat its output as the source of
truth over this document if the two ever disagree.

## The core guarantee, and how it's enforced

Every fact that can change over time (roster membership, injury status,
lineup, starting goalie, sportsbook price) is stored as an **append-only
event** with two timestamps: `effective_at_utc` (when it became true in the
world) and `observed_at_utc` (when this system learned about it). Every
read in `features/point_in_time.py` filters strictly on
`observed_at_utc <= prediction_time_utc`. That single mechanism — not
game-ID ordering, not caller discipline — is what prevents a prediction
from seeing information from its own future. `tests/test_temporal_integrity.py`
includes a deliberate-leakage test: it mutates a *future* game's result, a
future lineup snapshot, and a future box-score row, then proves an earlier
prediction's feature snapshot and output probabilities are byte-for-byte
unchanged.

Reproducibility is a separate, complementary guarantee: `predict()` does
all its database reads through the point-in-time layer and packages every
raw input into a plain-dict "feature snapshot"; a second function,
`compute_probability_from_features()`, is a **pure function** over that
dict (no DB access, no randomness, no wall-clock). Persisting the snapshot
(`pricing/decision.py`) means any stored prediction can be replayed exactly
— `tests/test_reproducibility.py` proves original-vs-replayed equality.

## v2.1 temporal-hardening pass

v2's point-in-time layer closed leakage through raw DB reads. It did not
close four other leakage vectors, each now closed:

- **Game-ID / list-order training eligibility.** Nothing may use
  `game_id < target_id` or `games[:index]` to decide what a historical
  model may learn from — a rescheduled or late-finishing game breaks both.
  `features/point_in_time.py::completed_games_known_before()` is now the
  single sanctioned source of truth, ordered strictly by
  `result_observed_at_utc`. See `tests/test_game_id_independence.py`'s
  Game-100-vs-Game-90 scenario.
- **Mutable schedule / stat tables.** `game_schedule_events` (append-only
  schedule history) and revision-versioned `player_game_stats` /
  `goalie_game_stats` mean a later correction is a *new row*, never an
  overwrite — `game_schedule_as_of()` / `player_game_stats_as_of()` /
  `goalie_game_stats_as_of()` return only what had been observed by a given
  timestamp. See `tests/test_schedule_revision.py`, `tests/test_stat_revision.py`.
- **In-memory learned model state.** Point-in-time SQL alone doesn't stop a
  model instance that has already learned games 76-100 from being asked to
  predict game 75. `CombinedMoneylineModel` now tracks
  `trained_through_observed_at` and raises `ContaminatedModelStateError`
  rather than silently mispredicting; `build_model_state_as_of()` is the one
  authoritative way to get a correctly-scoped instance for a given
  `prediction_time_utc`. See `tests/test_model_state_integrity.py`.
- **Static odds-staleness window.** A single flat cutoff is too permissive
  close to puck drop and too strict a day out. `config.ODDS_STALENESS_TIERS`
  + `pricing/odds_math.py::dynamic_max_staleness_minutes()` scale the
  allowed quote age with time-to-puck-drop. See `tests/test_odds_staleness_policy.py`.

Also renamed (not rebuilt): the maturity-based `ci_low`/`ci_high` band is
now documented everywhere as `config.BASE_UNCERTAINTY_BAND_HALF_WIDTH` — a
**heuristic, not a statistically validated confidence interval** — the
field names stay for schema/API compatibility only; see `BetReport.format()`'s
output line and config.py's docstring for the deferred empirical-uncertainty
upgrade path. And `features/point_in_time.py` is now structurally enforced
(`tests/test_structural_reads.py`) as the *only* module permitted a raw
SELECT/JOIN against a bitemporal table — with a short, individually
justified exception list for write-path idempotency checks and pure
COUNT(*) diagnostics.

None of this changed what the models predict on identical inputs — it
changed what inputs they're allowed to see, and when. `run_slate.py` /
`backtest.py`'s numbers on the existing synthetic dataset are unchanged
before/after this pass (see `E. Validation Results` in the delivery
write-up for this slice, if you have it, or rerun `python3 validate.py`).

## v2.1.1 final temporal closure

An independent review of v2.1 found three remaining gaps outside the
existing test boundaries — all closed in this pass, purely
architecture/correctness, no new predictive features:

- **`run_slate.py` still used game-ID/list-position eligibility.**
  `[g for g in all_final if g < gid]` and `all_final[:-5]`/`all_final[-5:]`
  as a train/test split — both exactly the anti-pattern v2.1 closed
  everywhere else, just missed here. Every game `run_slate.py` prices now
  gets its own independent `build_model_state_as_of()` reconstruction keyed
  to that game's own `prediction_time_utc` — no shared/frozen model
  instance across games. See `run_slate.py::build_prediction_for_game()`,
  `tests/test_run_slate_temporal.py`.
- **Final game results were a mutable, overwritable cache.** `ingest_result()`
  used to `UPDATE` `games.home_score`/`result_observed_at_utc` in place —
  a re-pull could move the historical "first known" time, and a corrected
  score could silently rewrite what a historical model believed it knew.
  A new append-only, revision-versioned `game_result_events` table (same
  pattern as `player_game_stats`/`goalie_game_stats`) is now the sole
  authoritative source; `features/point_in_time.py::game_result_as_of()` /
  `game_result_first_observed_at()` read it, `games`' result columns are
  current-state-cache-only. See `tests/test_result_revision.py`.
- **Exact-timestamp-tie contamination semantics were inconsistent.** The
  authoritative eligibility query used strict-before (`<`) but the
  in-memory contamination guard only rejected strictly-after
  (`trained_through_observed_at > prediction_time_utc`), silently allowing
  a model trained through a result observed at exactly the prediction
  timestamp to predict at that same instant. The guard now uses `>=`,
  consistent with strict-before eligibility. See
  `tests/test_model_state_integrity.py::TestExactTimestampContaminationSemantics`.

Two more closures, ahead of the live-data pass this slice unblocks:

- **A structural audit for the higher-level anti-pattern.**
  `tests/test_structural_reads.py` catches raw SQL reads outside
  `features/point_in_time.py`, but not `game_id < x` / `games[:-5]` /
  `sorted(game_ids)` used as a training-eligibility proxy — a different
  shape of the same underlying bug. `tests/test_training_path_structural_audit.py`
  walks the AST of every production file and flags exactly that, with one
  narrowly-justified, commented exception (`run_slate.py`'s purely-cosmetic
  "which 5 games to print" selection).
- **Mixed UTC timestamp representations.** Point-in-time eligibility
  depends entirely on timestamp ordering/equality; a live NHL API response
  mixes `Z`-suffixed timestamps with whatever an external roster/news feed
  supplies. `ingest/timestamps.py::normalize_utc_timestamp()` is now the
  single gate every real ingestion write path routes through before
  persistence, converting any supported incoming form (naive, `Z`-suffixed,
  or explicit-offset) to one canonical UTC string. See
  `tests/test_timestamp_normalization.py`.

## Implemented / Tested / Experimental / Deferred

Five categories (spec item 23): **IMPLEMENTED + TESTED** (unit/integration
tested, including the relevant temporal-integrity guarantees where
applicable), **IMPLEMENTED + SYNTHETICALLY TESTED** (tested, but only ever
against `ingest/demo_data.py`'s synthetic league — behavior against real
NHL data is unverified), **IMPLEMENTED + NOT LIVE VERIFIED** (code exists
and is unit-tested against constructed fake inputs, but has never run
against a real external system), **EXPERIMENTAL** (a deliberate, documented
heuristic placeholder, not a validated model), **DEFERRED** (not built this
slice, by explicit scope decision).

| Piece | Status | Notes |
|---|---|---|
| Bitemporal schema (`schema.sql`) | **IMPLEMENTED + TESTED** | Append-only event tables throughout, including v2.1's `game_schedule_events` and revision-versioned `player_game_stats`/`goalie_game_stats`; unique indexes make duplicate/identical inserts a no-op. |
| Point-in-time query layer (`features/point_in_time.py`) | **IMPLEMENTED + TESTED** | `tests/test_point_in_time.py`, `tests/test_temporal_integrity.py`, `tests/test_temporal_invariants.py`; exclusivity mechanically enforced by `tests/test_structural_reads.py`. |
| Training-eligibility / game-ID independence (`completed_games_known_before`) | **IMPLEMENTED + TESTED** | `tests/test_game_id_independence.py`. |
| Model-state contamination guard (`ContaminatedModelStateError`, `build_model_state_as_of`) | **IMPLEMENTED + TESTED** | `tests/test_model_state_integrity.py`. |
| Schedule revision history (`game_schedule_events`) | **IMPLEMENTED + TESTED** | `tests/test_schedule_revision.py`. |
| Player/goalie postgame stat revisions | **IMPLEMENTED + TESTED** | `tests/test_stat_revision.py`. |
| Dynamic odds-staleness policy | **IMPLEMENTED + TESTED** | `tests/test_odds_staleness_policy.py`. |
| Elo team-strength model (`models/elo_model.py`) | **IMPLEMENTED + TESTED** | Updates on the *base* Elo expectation only, deliberately — see `config.ELO_UPDATES_ON_BASE_EXPECTATION`'s docstring and `tests/test_elo_update_rule.py`. |
| Player-quality model (`models/player_model.py`) | **IMPLEMENTED + SYNTHETICALLY TESTED** | v1 heuristic (rolling PPG vs. league average → Elo points), explicitly **not** RAPM/GAR/xGAR, and explicitly not made more sophisticated this slice. `POINTS_PER_GAME_TO_ELO` was lowered from an initial 55 to 20 after `backtest.py` showed 55 calibrated *worse* than Elo-only on synthetic data. Needs retuning against real results. |
| Goalie model (`models/goalie_model.py`) | **IMPLEMENTED + SYNTHETICALLY TESTED** | Save-percentage only (no shot-danger location), explicitly not made more sophisticated this slice. Shrinks toward league average by start count; widens uncertainty when unconfirmed. |
| Rest/schedule-congestion features | **IMPLEMENTED + TESTED** | 3/4/5/6/7/10-day windows, back-to-back, 3-in-4, 4-in-6; now reads the point-in-time schedule history (`game_schedule_as_of`), including for the target game's own date — a real latent leakage gap closed in v2.1. |
| Combined moneyline model (`models/combined_model.py`) | **IMPLEMENTED + SYNTHETICALLY TESTED** | DB-reads/pure-function split for reproducibility; season-boundary reset; chronologically-merged `process_games()` walk-forward event stream (v2.1) instead of a naive per-game predict-then-learn loop. |
| Heuristic uncertainty band (`config.BASE_UNCERTAINTY_BAND_HALF_WIDTH`, `ci_low`/`ci_high`) | **EXPERIMENTAL** | Maturity-based heuristic, explicitly **not** a statistically validated confidence interval (v2.1 rename + disclosure — see `BetReport.format()`'s output line). Empirical replacement deferred; see config.py's TODO. |
| DraftKings-only reference pricing (`pricing/engine.py`, `pricing/decision.py`) | **IMPLEMENTED + TESTED** | No silent fallback to another book; missing/stale/suspended/post-start DK data returns `DATA_UNAVAILABLE`, never a guess. |
| Goalie-confirmation WAIT policy | **IMPLEMENTED + TESTED** | Defaults to refusing to price a game unless *both* teams' starters are `CONFIRMED`; `config.ALLOW_BETTING_ON_EXPECTED_STARTER` is an explicit, human-set override. |
| Edge/EV/max-price threshold separation | **IMPLEMENTED + TESTED** | `BET` requires `conservative_edge >= MIN_CONSERVATIVE_EDGE` **and** `expected_value >= MIN_EV` — two different quantities, checked separately (`tests/test_thresholds.py`). |
| Reproducibility (`pricing/decision.py::reproduce`) | **IMPLEMENTED + TESTED** | `tests/test_reproducibility.py`. **v2.1.2 terminology note (spec item 12):** this is precisely **MODEL-PREDICTION REPRODUCIBILITY** — re-deriving the same `model_prob_home`/`conservative_prob_home`/`ci_low`/`ci_high` from a stored `feature_snapshot_json`. It is NOT **FULL DECISION REPRODUCIBILITY** — it does not independently recompute the no-vig market probability, EV, maximum acceptable price, or the BET/WAIT/PASS action from immutable stored pricing inputs, so replaying an old *decision* after `pricing/`/`config.py` code changes isn't guaranteed exact yet. That would need a pricing-model version, a threshold/config version, and stored odds inputs alongside a decision-logic version — deferred to the real-odds slice (see "Remaining real-odds blockers" below). |
| Real NHL CORE ingestion — SCHEDULE/RESULT/BOXSCORE ingestion (`ingest/nhl_api.py::ingest_range`) | **IMPLEMENTED + NOT LIVE VERIFIED** | This sandbox's outbound network cannot reach `api-web.nhle.com` (confirmed via direct connection attempts — see `validate_live_nhl.py`, which reports `LIVE NHL CORE INGESTION: NOT EXECUTED -- NETWORK UNAVAILABLE` from here). Idempotency logic — including v2.1's append-only schedule/stat-revision write paths and v2.1.2's fresh-database team-FK bootstrap (spec item 1) — is proven with constructed fake payloads shaped like the NHL API's documented response format (`tests/test_ingest_idempotency.py`, `tests/test_schedule_revision.py`, `tests/test_fresh_db_ingestion.py`), but the module has never actually parsed a real response. **v2.1.2 (spec item 5): this row is SCHEDULE/RESULT/BOXSCORE ingestion only** — `ingest_range()` does NOT call `fetch_team_roster()`/`upsert_team_membership()`; it never did, despite earlier wording implying it covered "player identities" too. See the CORE ROSTER IDENTITY rows directly below, which are the separate, explicit tier for that. **v2.1.2a (spec item 1):** the boxscore leg of this path now stores the real API's per-skater `sog` field as `player_game_stats.shots` — the pre-v2.1.2a code read a field name (`shots`) the real API never sends and silently stored every real skater's shots as 0; a missing `sog` now raises `NHLApiSchemaError`. See `tests/test_boxscore_contract.py`, built against a frozen fixture using the real API's own field names. **v2.1.2a (spec item 5):** `ingest_range()` now takes an optional injectable `session=` parameter and stamps a freshly-captured `observed_at_utc` per network response (one shared timestamp for schedule+result, a separate fresh one per boxscore) instead of one timestamp for the whole batch — see "Live observation timestamp policy" below and `tests/test_live_observation_timestamping.py`. |
| Real CORE ROSTER IDENTITY ingestion — SEASON-scoped (`ingest/nhl_api.py::ingest_roster_identities`) | **IMPLEMENTED + NOT LIVE VERIFIED** | v2.1.2 spec item 5: composes the already-unit-tested `fetch_team_roster()` + `upsert_team_membership()` (`/v1/roster/{team}/{season}`) for a given list of teams, populating `players.full_name`/`position` and `team_membership_events` for HISTORICAL identity mapping. NOT called from `ingest_range()` and NOT injury/availability or starting-goalie data. **v2.1.2a (spec item 3): this function must NEVER be used to establish today's CURRENT team membership** — a season-roster response retrieved today doesn't prove who's actually on the team right now (the season could be over, or mid-season with trades since). See the CURRENT roster row directly below for that. `tests/test_core_roster_identity.py` proves this contract against constructed fake roster payloads; never run against a live response. |
| Real CURRENT team roster membership (`ingest/nhl_api.py::ingest_current_roster_identities`) | **IMPLEMENTED + NOT LIVE VERIFIED** | v2.1.2a spec item 3/4: THE sanctioned way to establish today's actual team membership, via the real `/v1/roster/{team}/current` endpoint (distinct from the SEASON roster endpoint above) — composes `fetch_current_team_roster()` + `sync_current_team_roster()`. Unlike the season-scoped path, this reconciles a COMPLETE current-roster snapshot: it upserts/**corrects** `players.full_name`/`position` when a later authoritative response disagrees with the first-ever value (no longer frozen via `INSERT OR IGNORE`), appends a membership event for anyone new/moved onto the team, and — the gap the season-scoped path never closed — appends an explicit `team_id=NULL, event_type='ROSTER_REMOVED'` departure event for anyone whose latest known membership pointed at this team but is absent from the new snapshot. Does **not** infer injury/availability from a roster absence; that remains `record_roster_status()`'s separate, still-unplugged concern. See `tests/test_current_roster_reconciliation.py` (present→later-absent, traded→new-team, absent→later-returns, name correction, position correction, repeated-identical-snapshot idempotency). |
| Real ROSTER/AVAILABILITY SOURCE (injury/scratch/IR status) | **DEFERRED / NOT LIVE VERIFIED** | No public NHL API exists for injury reports. `record_roster_status()` in `ingest/nhl_api.py` is ready to receive data from a source you plug in (PuckPedia, beat reporters, etc.) — nothing calls it yet outside tests/demo data. Tracked separately from core ingestion and from both roster-identity ingestion tiers above (v2.1.1a spec item 8, reaffirmed v2.1.2a spec item 4) so a successful ingestion run of any kind is never mistaken for having validated this too. |
| Real STARTING-GOALIE SOURCE | **DEFERRED / NOT LIVE VERIFIED** | No public NHL API exists for starting-goalie announcements. `record_goalie_status()` in `ingest/nhl_api.py` is ready to receive data from a source you plug in (Daily Faceoff or similar) — nothing calls it yet outside tests/demo data. Tracked separately from core/roster-identity ingestion for the same reason as the row above. |
| Dynamic production team universe (`db.team_ids`) | **IMPLEMENTED + TESTED** | v2.1.2 spec item 2: `run_slate.py`/`backtest.py` now derive the model team universe from the database (`SELECT team_id FROM teams`), never from `ingest.demo_data.TEAMS` (the synthetic demo league only) — proven for non-demo teams (EDM/VGK) in `tests/test_dynamic_team_universe.py`, which also mechanically guards against any production module reimporting the synthetic list. |
| Live NHL core ingestion smoke test (`validate_live_nhl.py`) | **IMPLEMENTED + LIVE VERIFIED** | v2.1.2 spec item 7/8, substantially strengthened v2.1.2a (spec item 2/6/7/8): a SEPARATE command from `validate.py` — selects a date range (backwards-searching in 7-day windows for ≥3 finalized games, or a pinned `--start`/`--end` range), ingests schedule/result/boxscore against a fresh temporary database, then checks `PRAGMA foreign_key_check`, required-field presence on every game/result/boxscore-derived row, non-empty `player_game_stats`/`goalie_game_stats` for both teams on every finalized game, canonical-timestamp compliance, and (where the boxscore reports it) a team-SOG cross-check; reruns the SAME range including boxscores a second time to prove real idempotency, and runs the CURRENT-roster sync twice to prove reconciliation idempotency. Fails loudly (never silently) on a live schema mismatch — and the top-level error classifier now only labels a genuine network/connectivity failure as `NOT EXECUTED -- NETWORK UNAVAILABLE`; anything else is reported as a real `LIVE NHL CORE INGESTION: FAIL` with the actual exception. **Run for real against the live NHL API (`api-web.nhle.com`, reachable from this environment as of the Daily Operational Sync slice) on 2026-08-27**: backwards search selected 2026-06-04..2026-06-11 (4 finalized games, the most recent completed games before the current off-season gap), ingested cleanly, idempotency and current-roster reconciliation both stable on rerun — `LIVE NHL CORE INGESTION: PASS`. See `DAILY_OPERATIONAL_SYNC_REPORT.md`. |
| Market-intelligence sportsbook schema placeholder (`config.MARKET_INTELLIGENCE_SPORTSBOOKS`) | **DEFERRED** | v2.1 architecture-only prep (spec item 17): schema/config can later distinguish DraftKings (execution/reference) from other books used only as signals — consensus, lead/lag, sharp movement. Empty and unused; no destructive rewrite needed later. |
| Licensed DraftKings odds-data ingestion | **DEFERRED** | Needs a licensed odds-data provider (no direct DraftKings scraping). `odds_snapshots`' schema and the point-in-time query layer are ready for it; nothing pulls live prices yet. |
| Synthetic demo dataset (`ingest/demo_data.py`) | **IMPLEMENTED + TESTED** | Deterministic (`tests/test_demo_data.py` proves same-seed → identical DB), a *deliberately shortened* round-robin (`demo_data.SEASON_GAMES_NOTE` — currently 44 games/team/season, **not** 82), one-game-per-team-per-day guaranteed by construction, OT/SO games always resolve to exactly one winner, and a player can be injured, recover, and be injured again. |
| Walk-forward backtest + baselines (`backtest.py`) | **IMPLEMENTED + SYNTHETICALLY TESTED** | Produces home-rate / Elo-only / Elo+player / Elo+goalie / combined-model calibration on synthetic data, strictly walk-forward (v2.1: reconstructs model state per prediction, never precomputes full-season state). The *numbers* it produces are not evidence of real-world edge. |
| `validate.py` master validation report | **IMPLEMENTED + TESTED** | Runs the full test suite, ingestion/data-quality counts, temporal-integrity structural checks, the twenty-nine-category temporal-hardening report (ten from v2.1, five from v2.1.1, five from v2.1.1a, six from v2.1.2, three from v2.1.2a), and the backtest, then checks the v2, v2.1, v2.1.1, v2.1.1a, v2.1.2, and v2.1.2a completion criteria. |
| Player SOG props, PP/PK deployment modeling, injury-cascade effects, line-promotion tracking, news/media intelligence, market-movement signals | **DEFERRED** | Not started this slice, and explicitly not to be started until the v2.1 Go/No-Go answer is YES. |
| Live recommendations / bankroll deployment | **DEFERRED** | This is a research/backtesting platform right now, not a live tool. |

## What this is NOT

- **Not evidence of a profitable edge.** Every backtest number in this repo
  comes from `ingest/demo_data.py`'s synthetic league. A clean calibration
  curve there proves the *pipeline's math* is internally sound (a model
  that never sees a game's own result before pricing it still tracks a
  known ground truth) — it says nothing about whether this approach beats
  a real DraftKings line. `backtest.py` and `validate.py` both print this
  caveat every time they run; don't strip it out when sharing results.
- **Not connected to any live odds feed.** `pricing/engine.py` only ever
  reads DraftKings prices already sitting in `odds_snapshots` — usually put
  there by `ingest/demo_data.py` in this environment. There is no live
  pull.
- **Not validated against real NHL identities.** All player/team/game IDs
  in the demo data are synthetic. `ingest/nhl_api.py` is written to the
  real API's documented shape but has not ingested a real response (see
  the table above) — real players, real rosters, and real schedules are
  untested here.
- **Not a parlay tool.** This engine prices and tracks *straight* bets
  only, deliberately — see the earlier research note on why parlaying
  heavy favorites doesn't reduce risk the way it can feel like it should.

## Running it

```bash
pip install -r requirements.txt

python3 validate.py          # the one command to run first: tests + data
                              # quality + temporal-integrity checks + backtest
                              # + completion-criteria checklist, all in one report

python3 demo_setup.py        # (optional, for interactive use) builds nhl.db,
                              # loads 4 synthetic seasons + an upcoming slate
python3 backtest.py          # walk-forward backtest against nhl.db directly
python3 run_slate.py         # prices held-out historical games + the
                              # upcoming SCHEDULED slate using real stored
                              # DraftKings snapshots (not ad hoc prices)
python3 -m unittest discover tests -v   # the test suite alone

python3 validate_live_nhl.py            # v2.1.2/v2.1.2a: SEPARATE from validate.py
                                         # -- hits the REAL NHL API against a fresh
                                         # temporary DB (never nhl.db); run only
                                         # from an environment with normal internet
                                         # access. Default: searches backwards from
                                         # today in 7-day windows for a window with
                                         # >=3 finalized games (offseason-safe).
python3 validate_live_nhl.py --start 2026-01-05 --end 2026-01-12
                                         # v2.1.2a spec item 7: pin an exact
                                         # smoke-test date range instead of the
                                         # backwards search.
```

`validate.py` builds its own throwaway database rather than touching
`nhl.db`, so it's safe and repeatable to run at any time; `demo_setup.py` /
`backtest.py` / `run_slate.py` share the persistent `nhl.db` for poking
around interactively. `validate_live_nhl.py` always uses its own separate
fresh temporary database too, and is never folded into `validate.py`'s
report — see "LIVE_OBSERVATION vs. HISTORICAL_BACKFILL" above.

## Dashboard (research / model visibility UI)

`dashboard/` is a local Streamlit app (v1) that makes the engine's real
data and current model visible and auditable — a **MODEL RESEARCH / NHL
INTELLIGENCE DASHBOARD**, explicitly **not** a "trust this bet" product
and **not** a claim of proven betting profitability (every page carries
a `MODEL STATUS: RESEARCH / VALIDATION` header). See
`MONEYPUCK_DASHBOARD_V1_REPORT.md` for the full design rationale and
verification.

**Setup:**
```bash
pip install -r requirements.txt
```

**Run:**
```bash
streamlit run dashboard/app.py
```
Opens at `http://localhost:8501` by default. Add `--server.port <N>` to
use a different port.

**Required local data** (all already present if you've followed this
README's earlier steps): `research/real_nhl_results/normalized_regular_season_games.jsonl`
(the real NHL corpus), `research/moneypuck_ingestion/research_moneypuck.db`
(build via `research/moneypuck_ingestion/ingest_moneypuck_team.py` — see
`MONEYPUCK_TEAM_INGESTION_REPORT.md`), and the four
`research/*_comparison_results.json` experiment result files (each
produced by its own `research/run_*_comparison.py` script). Any missing
file is reported clearly in the app itself (e.g. `REAL NHL CORPUS: NOT
FOUND`, with the exact command to build it) — it never fabricates data
or crashes with a raw traceback.

**Deploying to Streamlit Community Cloud:**

1. Push this repo to GitHub (a `.env` containing `THE_ODDS_API_KEY` is
   gitignored and will never be pushed — see `research/live_sog_pricing/env_config.py`).
2. On [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this GitHub repo, branch `master`, main file path
   `dashboard/app.py`.
3. (Optional — only needed for the *live* DraftKings re-check under
   "Live Model Edges" to work; everything else on the dashboard works
   without it.) In the app's own **Settings → Secrets** panel, add:
   ```toml
   THE_ODDS_API_KEY = "your-real-key-here"
   ```
   `env_config.py` checks the environment/`.env` first, then falls back
   to `st.secrets["THE_ODDS_API_KEY"]` — this is the only place that
   fallback is read from, matching the same never-log/never-hardcode
   guarantee `tests/test_live_sog_pricing.py::TestApiKeyHandling` and
   `TestStreamlitCloudSecretsFallback` already test for the local path.
4. `.streamlit/config.toml` (dark theme, headless server) is already
   tracked and takes effect automatically — no extra setup.

**What's different about a fresh Cloud deploy vs. your local clone:**

The raw MoneyPuck/play-by-play/team-goals CSV dumps and the large
special-teams role tables are intentionally gitignored (large,
regenerable — see `.gitignore`'s own comments) and won't exist on a
fresh checkout. Pages that depend on them (MoneyPuck research-context
panels, play-by-play status, the special-teams role panel on Player
Intelligence) show their existing "not found / not available" state
rather than fail — verified directly (see below), not assumed.

The demo engine's own per-game corpora
(`research/player_sog/player_game_sog.jsonl` and the four sibling
files for blocks/assists/points/goals, plus the goalie-saves and
period-SOG corpora) are **tracked**, but as a real, unmodified-content
**subset** — every row for the ~47 real players and 4 goalies the demo
roster (`dashboard/demo_data.py`) actually uses, not the full league.
Two of the five (SOG, Goals) are individually over GitHub's 100MB
per-file limit at full size anyway. If you're doing actual model
research/refitting rather than running the demo, regenerate the full
corpus locally with each file's own `build_*_corpus.py` script (see
`research/player_sog/build_sog_corpus.py` and its siblings) — do not
commit the full version back over the tracked subset.

`nhl.db` (the real historical corpus + Elo ratings the core pages
need) and the archived real DraftKings evidence under
`data/raw/the_odds_api/live/` are tracked in full and come along fine.
The operational ledgers (`operational/paper_bankroll.db`,
`operational/prospective_observations.db`) are gitignored and start
empty on a fresh deploy, same as a fresh local clone — by design,
since this is public demo/research code, not a store of real recorded
bets.

**How this was actually verified, not assumed:** a real deploy of this
repo to Streamlit Community Cloud (2026-09-01) crashed with a raw
`FileNotFoundError` on nearly every page — the paragraph above
describing graceful degradation used to be true only for the MoneyPuck/
PBP pages, not the demo engine's own corpora. The fix was found and
verified by simulating a fresh clone locally (temporarily hiding every
path that's still gitignored today, then running all 33 dashboard
pages through Streamlit's own `AppTest` harness) until zero pages threw
an exception — not by guesswork. `tests/test_deploy_readiness.py` runs
that same simulation as a permanent regression test, so a future page
or data dependency that reintroduces this failure mode gets caught
before it reaches a real deployment again.

**Pages:**
1. **Game Slate** — browse any real historical NHL date; current (Elo-only)
   model win probability per game, confidence heuristic, model drivers.
2. **Game Detail** — full probability breakdown for one game, plus team
   context (last 5/10 record, Elo history, MoneyPuck research metrics).
3. **Team Ratings** — sortable current Elo ratings + optional MoneyPuck
   research context, as of any real date.
4. **Model Performance** — real-data Brier score, log loss, calibration
   curve, season-by-season breakdown, probability distribution.
5. **Research Lab** — all four completed feature experiments (Elo, team
   xG, special teams, offense/defense decomposition), parsed
   programmatically from their result files, with status labels and a
   Brier-delta comparison chart.
6. **Goalie Intelligence (Research)** — the Stage 1 pregame starting-goalie
   projection model vs. naive baselines, the empirical back-to-back
   finding, and an interactive real-historical-date projection tool.
   Labeled `STARTER INTELLIGENCE: RESEARCH / HISTORICAL INFERENCE`
   throughout — every probability is PROJECTED, never CONFIRMED (no live
   external source is integrated — see
   `GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md`). Below that, a **Goalie
   Quality x Starter Probability Integration** panel lets you inspect any
   real historical game's scenario-weighted goalie-quality adjustment
   (projected starter distribution x save%/GSAx-style quality, never the
   actual historical starter) against the baseline probability, labeled
   `RESEARCH — NOT PRODUCTION` throughout — see
   `GOALIE_QUALITY_INTEGRATION_REPORT.md`; the current recommendation from
   that experiment is **KEEP CURRENT MODEL**.
7. **Player SOG Research** — the engine's first player-prop probability
   model (shots on goal). Pick a real historical player/game and see the
   expected SOG, P(1+)..P(6+) with a conservative lower-bound
   counterpart, and a confidence label with its specific drivers/risks —
   all built from real MoneyPuck skater data, strictly point-in-time
   safe, labeled `RESEARCH — NOT YET A BETTING RECOMMENDATION` throughout
   (no sportsbook odds appear anywhere on the page). See
   `PLAYER_SOG_FOUNDATION_REPORT.md`; validated (`SOG MODEL PASSES
   VALIDATION`) — head-to-head history and player TOI/role both showed
   real predictive value, recent form and opponent context did not.
8. **Live SOG Markets** — real DraftKings SOG prices (via The Odds API)
   compared against the validated SOG model: no-vig market probability,
   model/conservative fair price, raw/conservative edge and EV, and a
   BET/WATCH/WAIT/PASS/DATA_UNAVAILABLE research decision label. Labeled
   `LIVE MODEL VS MARKET` and `NO AUTOMATIC BETTING` throughout — this
   page never makes a network call itself, only reads a cached snapshot
   refreshed by `python3 -m research.live_sog_pricing.refresh`. As of
   this writing the real board is empty: The Odds API confirms zero
   DraftKings SOG markets currently posted (soonest real NHL event is
   over a month out) — see `PLAYER_SOG_LIVE_PRICING_REPORT.md`.
9. **Data Status** — answers "do I have all of today's required hockey
   data, and exactly when did I obtain it?" Seven independent source
   statuses (NHL schedule/results, MoneyPuck team/skater/goalie, odds,
   starter intelligence), read from a cached snapshot only — refresh
   explicitly with `python3 sync_daily.py`. See
   `DAILY_OPERATIONAL_SYNC_REPORT.md`.
10. **Prop Registry** — one status per player-prop family (VALIDATED /
    PARTIAL / PROMISING / RESEARCH / REJECTED / UNSUPPORTED_MARKET), so
    the dashboard never implies every prop is equally mature. As of this
    writing: **3 validated** (SOG, Blocked Shots, Assists — the latter
    two built on the same reusable `research/player_props/` framework
    the SOG model helped generalize), **1 partial** (Points — beats 3 of
    4 naive baselines but loses to a simple per-player empirical-
    distribution baseline at every threshold; see
    `PLAYER_POINTS_VALIDATION_REPORT.md`), the rest RESEARCH or deferred.
    See `MULTI_PROP_RESEARCH_REPORT.md`.
11. **Player Points Research** — the fourth player-prop model (total
    points), and the first built under an explicit tuning/lock/freeze
    discipline: a machine-readable freeze manifest
    (`research/player_points_freeze_manifest.json`) is written before any
    2024-25/2025-26 outcome is scored. Status: **PARTIAL**, reported
    plainly on the page itself. See `PLAYER_POINTS_VALIDATION_REPORT.md`.

**Data mode**: the dashboard operates in **HISTORICAL RESEARCH** mode
only — there's no live current-season game feed wired into this project
yet, so "today's games" would mean either fabricating data or new
live-ingestion work out of scope for v1. Every page browses real
historical dates instead, labeled `DATA MODE: HISTORICAL RESEARCH`
throughout, never presented as a live/current prediction.

**MODEL INPUT vs. RESEARCH METRIC**: Elo rating and home-ice are the only
values labeled `MODEL INPUT` (the real, unmodified production Elo
formula — see `models/elo_model.py`). Every MoneyPuck-derived value (5v5
xG share, xGF/60, xGA/60, PP/PK rates) is labeled `RESEARCH METRIC — NOT
CURRENTLY USED BY MODEL`, since none of the four completed feature
experiments were adopted (Research Lab page explains why for each).
Player, goalie, and rest contributions are labeled `NOT AVAILABLE IN
HISTORICAL RESEARCH MODE` rather than approximated, because the real
corpus has no real roster or schedule-event data for these real games.

**Read-only guarantee**: the dashboard never opens, reads, or writes
`nhl.db` or any production PIT table, and never imports `db.py` or
`models/combined_model.py` — see `tests/test_dashboard.py`'s AST-scan
tests. It computes "current model" predictions by directly calling
`research.elo_comparison.run_walkforward()`, which itself drives the
real, unmodified `models/elo_model.py::EloModel` — not a reimplementation.

## Completion criteria for this development slice

### v2 criteria

The v2 slice ("convert the synthetic proof of concept into a temporally
valid NHL moneyline research platform") is complete only when:

1. All tests pass. **Verified** — `python3 -m unittest discover tests`
   reports 0 failures/errors (172 tests as of the v2 slice; 223 as of
   v2.1.1; 252 as of v2.1.1a; 285 as of v2.1.2; 309 as of v2.1.2a, this
   writing); `validate.py` section 1 reruns this every time.
2. Real NHL ingestion successfully loads at least one complete season.
   **Not verified from this environment** — no outbound network access to
   the NHL API here. `ingest/nhl_api.py` is written and unit-tested against
   fake payloads but unexercised live. Run it somewhere with normal
   internet access to actually clear this criterion. **v2.1.1a
   clarification (spec item 8), corrected v2.1.2a (spec item 10):** this
   criterion, and "real NHL ingestion" generally, refers ONLY to what
   `ingest_range()` actually supplies — schedule, results, and boxscore
   POSTGAME STAT ROWS keyed by the NHL's own `playerId` (**REAL NHL CORE
   INGESTION**). Parsing a boxscore's `playerId` into `player_game_stats`/
   `goalie_game_stats` is NOT the same as establishing canonical player
   identity (`players.full_name`/`position`) or current team membership —
   that is `ingest_roster_identities()`'s job (**REAL CORE ROSTER
   IDENTITY INGESTION**, tracked separately). Neither of those covers real
   injury/availability status or real starting-goalie announcements
   (**REAL ROSTER/AVAILABILITY SOURCE** and **REAL STARTING-GOALIE
   SOURCE**, tracked separately below) — those need a source you plug in
   yourself (see row below), and successfully running `ingest_range()`
   and/or `ingest_roster_identities()` must never be read as having
   validated them too.
3. No historical prediction can access information observed after its
   prediction timestamp. **Verified** — enforced structurally by
   `features/point_in_time.py`, proven by `tests/test_temporal_integrity.py`'s
   deliberate-leakage test and `tests/test_point_in_time.py`.
4. Scheduled games receive valid rest features. **Verified** —
   `features/point_in_time.py::rest_context` never reads a result;
   `tests/test_point_in_time.py::TestRestContext` covers `SCHEDULED` games
   explicitly.
5. Player and goalie features work with real ingested identities. **Not
   verified from this environment** — same network limitation as #2; the
   models operate correctly against synthetic identities (tested), but
   "real" identities specifically requires a live ingestion run. Note
   this is about player/goalie IDENTITY (do the right rows exist and
   join correctly), which **REAL NHL CORE INGESTION** alone would cover
   — it is a separate question from whether real availability/starting-
   goalie STATUS is flowing in, which additionally needs **REAL
   ROSTER/AVAILABILITY SOURCE** and **REAL STARTING-GOALIE SOURCE**
   (see item 2's clarification above).
6. WAIT uses stored goalie status rather than a hardcoded argument.
   **Verified** — `pricing/engine.py::evaluate_moneyline_for_game` reads
   `pred.home_goalie_status` / `pred.away_goalie_status`, themselves sourced
   from `features/point_in_time.py::goalie_status`;
   `tests/test_thresholds.py::TestGoalieWaitPolicy` covers both-confirmed,
   one-confirmed, and the explicit policy override.
7. Historical predictions are reproducible. **Verified** —
   `tests/test_reproducibility.py`.
8. This README accurately distinguishes implemented, tested, experimental,
   and deferred functionality. **This document, as of this rewrite.**
   `validate.py` does a lightweight mechanical check that these four words
   actually appear here.

### v2.1 temporal-hardening criteria

The v2.1 slice ("prove every historical prediction can only be influenced
by information and learned model state that genuinely existed at that
prediction's recorded timestamp") is complete only when ALL of the
following are true (spec item 24; `validate.py` section 8 checks each
mechanically on every run):

1. No historical training path uses game_id ordering as the temporal gate.
2. Training eligibility is based on `result_observed_at_utc`.
3. Schedule revisions are append-only and historically reconstructable.
4. Player postgame statistics are observation/revision timestamped.
5. Goalie postgame statistics are observation/revision timestamped.
6. Later stat corrections cannot alter earlier predictions.
7. A future-trained model cannot silently contaminate a historical
   prediction.
8. Historical model state can be reconstructed as of `prediction_time_utc`.
9. Stored feature-snapshot reproduction remains exact.
10. Odds staleness varies appropriately with time to puck drop.
11. All existing (pre-v2.1) tests still pass.
12. New temporal-hardening tests pass.
13. `validate.py` reports every item above explicitly.
14. This README accurately distinguishes tested architecture from
    unverified real-data behavior.

All 14 are **PASS** as of this writing (`python3 validate.py` section 8,
"v2.1 temporal-hardening criteria").

### v2.1.1 final-temporal-closure criteria

The v2.1.1 slice ("close the remaining historical-reconstruction gaps
found during independent review of v2.1") is complete only when ALL of
the following are true (`validate.py` section 8 checks each mechanically
on every run):

1. `run_slate.py` contains no game-ID-based training eligibility.
2. Historical multi-game pricing learns every result genuinely available
   before each individual prediction timestamp.
3. Final game results are append-only/revision-safe.
4. Identical result reingestion cannot move the historical first-known
   time.
5. A later score/result correction cannot alter an earlier model state.
6. Model learning uses the result revision actually available at learn
   time.
7. Exact prediction/result timestamp ties obey strict-before semantics.
8. UTC timestamp representations are normalized before comparison/storage.
9. A structural test guards against reintroducing game-ID/list-position
   temporal proxies.
10. All previous 172 tests (the count immediately before this slice)
    still pass.
11. All new v2.1.1 tests pass.
12. `validate.py` reports all five new v2.1.1 categories (`RESULT
    REVISION INTEGRITY`, `RUN_SLATE TEMPORAL INTEGRITY`,
    `EXACT-TIMESTAMP ORDERING`, `UTC TIMESTAMP NORMALIZATION`,
    `TRAINING-PATH STRUCTURAL AUDIT`) PASS.

All 12 are **PASS** as of this writing — the full suite now stands at 223
tests (up from 172 immediately before this slice; `python3 -m unittest
discover tests` reports 0 failures/errors). See the delivery write-up for
this slice for the full A-G breakdown, including the explicit Go/No-Go
answer.

Per explicit instruction, across all three slices: **do not build SOG
props, other new intelligence features, live recommendations, or
bankroll deployment until these criteria are independently
re-confirmed** — in particular v2's #2 and #5, which this sandbox
structurally cannot verify itself, and v2.1.1's Go/No-Go answer, which
gates moving to real NHL ingestion validation at all.

### v2.1.1a correctness-patch criteria

The v2.1.1a slice ("close five specific correctness gaps found during a
second independent review, before historical real-odds backtesting or
actionable betting output is trusted") is complete — 14/14 criteria
**PASS** as of that slice; see the `claude/nhl-engine-v2.1.1a-correctness-
patch.md` project note for the full A-I delivery write-up, including its
three separate Go/No-Go answers. `validate.py` section 8 continues to
check all 14 on every run.

### v2.1.2 real-NHL-core-ingestion-readiness criteria

The v2.1.2 slice ("make sure the platform can actually ingest and operate
on a real NHL database, not only the synthetic demo environment") is
complete only when ALL of the following are true (`validate.py` section 8
checks each mechanically on every run):

1. Fresh DB + `ingest_schedule()` works with no pre-seeded teams.
2. Unknown/non-demo NHL teams auto-bootstrap.
3. Production model universe is DB-derived, not demo-data-derived.
4. Non-demo teams (e.g. EDM/VGK) can be modeled.
5. `run_slate.py`'s production path does not require
   `ingest.demo_data.TEAMS`.
6. `backtest.py`'s production path does not require
   `ingest.demo_data.TEAMS`.
7. A schedule revision consumed via an explicit `learn_time_utc` moves
   the knowledge watermark.
8. Backward prediction across that schedule revision raises
   `ContaminatedModelStateError`.
9. Fresh reconstruction before that schedule revision remains valid.
10. Real `ingest_schedule()` reingestion keeps the `games` cache
    synchronized with schedule history.
11. `schedule_observed_at_utc` remains the first-known time after a cache
    update.
12. Historical data ingested today is not visible to a prediction
    timestamped before ingestion.
13. Core roster-identity ingestion populates `players`/membership without
    implying injury data.
14. All previous 252 tests (the count immediately before this slice)
    still pass.

All 14 are **PASS** as of this writing — the full suite now stands at 285
tests (up from 252 immediately before this slice). `validate.py` reports
all six new v2.1.2 categories (`FRESH-DB INGESTION BOOTSTRAP`, `DYNAMIC
TEAM UNIVERSE`, `SCHEDULE WATERMARK INTEGRITY`, `SCHEDULE CACHE
CONSISTENCY`, `CORE PLAYER IDENTITY CONTRACT`, `HISTORICAL BACKFILL
KNOWLEDGE-TIME POLICY`) PASS. These criteria gate attempting the first
**live** NHL core ingestion smoke test (`python3 validate_live_nhl.py`,
from an environment with normal internet access) — not the same thing as
that smoke test having actually succeeded, which remains **NOT EXECUTED
— NETWORK UNAVAILABLE** from this sandbox.

### v2.1.2a live-API-contract-closure criteria

The v2.1.2a slice ("close seven specific live-integration gaps found by
checking the actual NHL Web API response shape, before attempting the
first real live ingestion smoke test") is complete only when ALL of the
following are true (`validate.py` section 8 checks each mechanically on
every run):

1. A real boxscore's per-skater SOG field (`sog`) is correctly stored as
   `player_game_stats.shots`.
2. A missing required SOG field raises `NHLApiSchemaError`, never a
   silent 0.
3. Missing required boxscore-structure fields (`id`, `homeTeam.abbrev`,
   `awayTeam.abbrev`, `playerByGameStats.homeTeam`/`awayTeam`) raise
   `NHLApiSchemaError`.
4. A multi-skater real-shape boxscore fixture populates every skater on
   both teams.
5. Reingesting an identical boxscore is idempotent; a real correction
   appends a new revision.
6. `observed_at_utc` for a boxscore fetch is captured after that fetch's
   own response, never inherited from an earlier batch-start time.
7. A later-arriving boxscore response cannot receive an earlier
   timestamp merely because the batch began earlier.
8. Schedule and result facts correctly share one timestamp from their
   single shared response.
9. `ingest_range()` accepts an injectable `session=` for testing without
   real network access.
10. A player absent from a later current-roster snapshot receives an
    explicit `ROSTER_REMOVED` departure event.
11. A traded player is removed from the old team and added to the new
    team.
12. A player who returns after a removal receives a fresh membership
    event.
13. A later authoritative response can correct `full_name`/`position`
    instead of freezing the first-ever value.
14. All previous 285 tests (the count immediately before this slice)
    still pass.

All 14 are **PASS** as of this writing — the full suite now stands at 309
tests (up from 285 immediately before this slice). `validate.py` reports
all three new v2.1.2a categories (`BOXSCORE CONTRACT INTEGRITY`, `LIVE
OBSERVATION TIMESTAMP INTEGRITY`, `CURRENT ROSTER RECONCILIATION`) PASS.
These criteria, together with the v2.1.2 criteria above, gate attempting
the first **live** NHL core ingestion smoke test — not the same thing as
that smoke test having actually succeeded, which remains **NOT EXECUTED
— NETWORK UNAVAILABLE** from this sandbox (no outbound network to
`api-web.nhle.com`).

Per explicit instruction, this slice does not add SOG *prediction*
functionality, other new intelligence features, odds-provider
integration, new betting markets, model-weight tuning, live
recommendations, or bankroll deployment — ingestion/validation
correctness against the real API's contract only.

## DraftKings reference-sportsbook policy

DraftKings is the engine's exclusive reference sportsbook for every
supported market. In brief (see `pricing/engine.py` and
`features/point_in_time.py` module docstrings for the full policy):

- Every report is labeled "DraftKings reference pricing", never
  "consensus" or "best market".
- `sportsbook` (book of record) and `data_provider` (who supplied the row)
  are separate columns, alongside `captured_at_utc` (the provider's
  timestamp) and `received_at_utc` (this system's ingestion timestamp).
- Missing, stale, suspended, incomplete, or post-event-start prices are
  rejected outright — the result is `DATA_UNAVAILABLE`, never a silent
  substitution. "Stale" is now (v2.1) a **dynamic, time-to-puck-drop-sensitive**
  policy, not one flat cutoff: `config.ODDS_STALENESS_TIERS` scales the
  max allowed quote age from 60 minutes more than 6 hours out down to 1
  minute inside the final 10 minutes before puck drop — see
  `pricing/odds_math.py::dynamic_max_staleness_minutes` and
  `tests/test_odds_staleness_policy.py`. `config.MAX_ODDS_STALENESS_MINUTES`
  (180) survives only as an explicit caller override / synthetic-data
  fallback, never the default policy.
- `odds_snapshots` is append-only (a unique index on
  `(sportsbook, game_id, market, selection, captured_at_utc)` makes a true
  duplicate a no-op, never an overwrite), so opening/intermediate/closing
  prices are all preserved for later closing-line-value analysis.
- Real ingestion must come from a licensed odds-data provider — direct
  DraftKings scraping is out of scope by design.

## LIVE_OBSERVATION vs. HISTORICAL_BACKFILL, and the live observation timestamp policy (v2.1.2 spec item 6, v2.1.2a spec item 5)

`ingest_range()` (and `ingest_schedule()`/`ingest_result()`/
`upsert_player_stats_from_boxscore()`/`ingest_roster_identities()`/
`ingest_current_roster_identities()` individually) always stamp
`observed_at_utc` as the moment **this system** actually ingests a fact —
never the moment the underlying NHL event itself happened, and never the
game's own date. Two explicit modes:

**v2.1.2a live observation timestamp policy (spec item 5):** the core
guarantee is that `observed_at_utc` must never predate the actual receipt
of the fact it timestamps. Previously `ingest_range()` captured a single
`now` once, before its loop, and stamped every schedule/result/boxscore
row across the whole batch with it — including boxscore fetches, each its
own separate, later-arriving HTTP response; a boxscore that physically
arrived minutes into a long batch could end up stamped with a timestamp
from before the batch even started. Fixed: `schedule_observed_at` is
captured once, immediately after `fetch_schedule_range()` itself returns
(schedule and result arrive embedded in that one response, with no
separate round trip for the result); a FRESH `boxscore_observed_at` is
captured for each game individually, immediately after that game's own
`fetch_boxscore()` call returns and before it is persisted; and
`ingest_current_roster_identities()` likewise captures a fresh timestamp
per team, right after that team's own `fetch_current_team_roster()`
response returns. A timestamp slightly LATER than actual receipt (for
convenience) remains acceptable/conservative — only EARLIER is not. The
existing explicit-timestamp-override behavior used by deterministic/
backfill tests and callers is preserved throughout (`ingest_range()`
still accepts pass-through explicit timestamps via its individual
`ingest_schedule()`/`ingest_result()`/`upsert_player_stats_from_boxscore()`
calls; `ingest_current_roster_identities()` still accepts an explicit
`observed_at_utc=` override). See `tests/test_live_observation_timestamping.py`,
which proves a later-arriving response cannot receive an earlier
timestamp merely because the batch began earlier, using a real (short)
`time.sleep()` inside a fake session rather than any datetime
monkeypatching.

- **LIVE_OBSERVATION**: forward collection of current/upcoming games.
  `observed_at_utc = now` correctly means "this system learned this fact
  right now" — the strongest "genuinely known by T" guarantee the
  point-in-time layer can offer.
- **HISTORICAL_BACKFILL**: pulling a past date range (e.g. backfilling the
  2022-23 season today) still stamps `observed_at_utc = now` for every
  historical fact pulled, because that really is the only moment this
  system actually learned it — absent a trustworthy archival source with
  its own genuine historical capture/publication timestamp.

**The current NHL API can provide historical event truth, but a
present-day pull does not reconstruct the historical information-arrival
timeline. Point-in-time backtesting requiring "what was genuinely known
at T" needs archived observations or another source that supplies
trustworthy historical capture/publication timestamps.** Backdating
`observed_at_utc` to the game's own date instead would fabricate
historical knowledge availability and silently break every point-in-time
guarantee in `features/point_in_time.py` — this was deliberately NOT
done. `tests/test_historical_backfill_knowledge_time.py` proves a
same-day backfill of a historical game is not visible to a point-in-time
read anchored before the backfill's own ingestion moment, even though the
game itself happened long before.

## Remaining real-odds blockers (v2.1.2 spec item 11 — documented, not built this slice)

Two issues were found during the v2.1.2 review that are **not** blockers
to real NHL core ingestion, but **are** blockers to future historical
real-odds ROI/CLV analysis. Documented here as explicit next-phase
blockers; no code changes were made for either in this slice, and neither
should be treated as fixed:

- **`closing_draftkings_snapshot()` does not yet enforce pre-puck-drop
  selection.** Its docstring says "LAST DraftKings price captured before
  the event started," but the SQL currently selects the latest `ACTIVE`
  row without checking `captured_at_utc < event_start_utc` — a live price
  captured minutes into the game could be returned as the "closing" line.
  This MUST be fixed and tested before CLV analysis is built on top of it.
- **Two-sided market snapshot coherence is undefined.**
  `latest_draftkings_two_sided()` independently chooses the latest
  eligible quote for each selection, which could combine a home-side
  quote from one moment with an away-side quote from a different moment.
  Whether that's acceptable depends on the licensed odds provider's own
  snapshot semantics — before odds-data integration, define a coherent
  two-sided market state explicitly, preferring a provider-supported
  atomic capture identifier (e.g. `snapshot_group_id`) if one exists.
  Do not design this blindly ahead of choosing the provider.

Also carried forward from the reproducibility-terminology clarification
above (spec item 12): full **DECISION** reproducibility (recomputing the
no-vig probability/EV/max-price/action from immutable stored inputs,
across pricing-code changes) does not exist yet — only **MODEL-PREDICTION**
reproducibility does. Building the former is deferred to the real-odds
slice.

## TODO / future design note: knowledge-time vs. effective-time (not resolved this slice)

v2.1.1a spec item 7, deliberately NOT redesigned this slice: the current
point-in-time architecture is fundamentally a *knowledge-time* system —
every PIT query filters on `observed_at_utc <= T` ("what had this system
learned by T"). That's the correct question for the facts this engine
currently ingests (a roster move, a stat line, a schedule slot, a
result) because for all of them, "learned about it" and "it's true"
happen close enough together that the distinction hasn't mattered yet.

It will start to matter once genuinely FUTURE-EFFECTIVE facts become
model inputs — e.g. a trade or suspension announced today that only
takes effect tomorrow, or a projected lineup posted well ahead of puck
drop. For those, "known by T" and "effective by T" are different
questions, and every future PIT query touching such a fact will need to
decide explicitly which one it means: "the latest fact known by T"
(today's `*_as_of()` pattern, appropriate for e.g. "what did the market
believe at T") vs. "a fact that is BOTH known and effective by T"
(needed for e.g. "was this player actually eligible to play in this
game," where a not-yet-effective trade must not count even if it was
already announced).

A related consequence: this codebase currently treats
`observed_at_utc < effective_at_utc` as impossible/invalid everywhere it
checks it (see `validate.py`'s temporal-integrity structural checks) —
but an advance announcement legitimately produces exactly that ordering
(announced today, effective tomorrow means `observed_at_utc` <
`effective_at_utc`). That check will need to be reconsidered, likely
per-table rather than universally, before any future-effective feed is
wired in.

**No code changes were made for this in v2.1.1a** — this section exists
so the next slice that adds a future-effective input (trades,
suspensions, projected lineups, or similar) starts from an explicit
design decision instead of silently inheriting knowledge-time semantics
that may not fit.

## Known gaps / next steps (deferred, in no particular priority)

- Live DraftKings odds ingestion via a licensed provider.
- A real roster-status / starting-goalie news source wired into
  `record_roster_status()` / `record_goalie_status()`.
- Running `ingest/nhl_api.py` against the live NHL API from an environment
  with network access, to actually close completion criteria #2/#5.
- Player SOG props, PP/PK deployment modeling, injury-cascade effects, line
  promotions, media/news intelligence, market-movement signals — all
  explicitly out of scope for this slice and the v2.1 slice, and explicitly
  not to be started until the v2.1 Go/No-Go answer is YES.
- Retuning `config.py`'s feature weights (especially
  `POINTS_PER_GAME_TO_ELO`, `SAVE_PCT_TO_ELO`, the rest-penalty constants)
  against real results once real data exists; the current values are
  synthetic-data-informed guesses, not calibrated truth.
- Replacing the heuristic maturity-based uncertainty band
  (`config.BASE_UNCERTAINTY_BAND_HALF_WIDTH`) with an empirical
  out-of-sample uncertainty model once real forecast-error data exists —
  deferred by explicit instruction this slice; see config.py's TODO for the
  intended factors (historical forecast error by probability bucket, season
  maturity, goalie/lineup/player-data certainty, model/market disagreement).
- Wiring actual market-intelligence sportsbook signals (consensus, lead/lag,
  sharp movement, DraftKings-staleness-relative-to-market) into
  `config.MARKET_INTELLIGENCE_SPORTSBOOKS` — the placeholder exists
  (v2.1 architecture prep), nothing reads it yet.
- Deciding knowledge-time vs. effective-time semantics before any
  future-effective fact (trades, suspensions, projected lineups) becomes
  a model input — see "TODO / future design note" above (v2.1.1a spec
  item 7; deliberately not resolved this slice).
- Fixing `closing_draftkings_snapshot()`'s missing pre-puck-drop-start
  check, and defining coherent two-sided market snapshot semantics —
  both explicit real-odds-slice blockers, see "Remaining real-odds
  blockers" above (v2.1.2 spec item 11; deliberately not built this
  slice).
- Building full DECISION reproducibility (vs. today's MODEL-PREDICTION-
  only reproducibility) — see the reproducibility-terminology note above
  (v2.1.2 spec item 12).
- Running `python3 validate_live_nhl.py` from an environment with network
  access — the actual first live NHL core ingestion attempt this slice
  exists to prepare for (v2.1.2 spec item 7); not executable from this
  sandbox.
