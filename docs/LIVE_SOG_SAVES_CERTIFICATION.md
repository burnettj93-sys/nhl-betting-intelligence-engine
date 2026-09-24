# Live SOG + Saves Production Certification

**Date:** 2026-09-24. **Scope:** certify the existing, already-validated PLAYER SOG and GOALIE SAVES intelligence stacks against real DraftKings market data, and wire real, eligible recommendations into the existing Game Edge Parlay / Top Conviction engines. **No new predictive model was built. No SOG/Saves coefficient was changed. No validated threshold was changed. `decision_policy` was not touched.**

## Provider market keys (Parts 2-4)

Determined by inspecting `research/player_props/registry.py`'s own `odds_api_market_key` fields (never invented):

| Market | Odds API key | Status |
|---|---|---|
| Player SOG | `player_shots_on_goal` (standard) / `player_shots_on_goal_alternate` (milestone lines) | **UNVERIFIED** |
| Goalie Saves | `player_total_saves` | **UNVERIFIED** |

## Are real DraftKings SOG/Saves markets currently available?

**No.** An exhaustive scan of every real, event-shaped archive file under `data/raw/the_odds_api/live/` (370 real files checked, spanning every real capture this project has ever made) found **zero** instances of DraftKings returning `player_shots_on_goal` or `player_total_saves` for any NHL event, ever. Every real capture that requested these markets — including a 30-event batch on 2026-09-23 that explicitly requested `player_shots_on_goal,player_goals,player_assists,player_points,player_total_saves,team_totals,alternate_team_totals` for every available event — got back only `alternate_team_totals` (a team-level market, not a player/goalie prop) or nothing at all. This matches `research/live_sog_pricing/market_parser.py`'s own pre-existing, honest disclosure ("Phase A's real smoke test found zero DraftKings markets currently posted") and `research/generic_prop_pricing/provider_adapter.py`'s `VERIFIED_CONTRACTS`, which contains exactly one entry: `("draftkings", "MONEYLINE")`.

**LIVE CONTRACT CERTIFICATION: PENDING FIRST REAL MARKET OBSERVATION** for both PLAYER_SOG and GOALIE_SAVES.

## Existing intelligence already reused, unmodified

This block discovered that nearly the entire architecture Parts 1-9 describe already existed from a prior sprint (`research/generic_prop_pricing/`), fully built and fully tested, never previously connected to real production data:

| Concern | Reused module | Status |
|---|---|---|
| Wire-format parsing | `research/live_sog_pricing/market_parser.py` | Already built; generalized (this block) to also recognize `player_total_saves`'s identical Over/Under shape, without changing SOG's own behavior |
| Contract verification gate | `research/generic_prop_pricing/provider_adapter.py::is_contract_verified()` | Already built; `VERIFIED_CONTRACTS` untouched |
| Event identity | `research/live_sog_pricing/event_mapping.py::map_event_to_game()` | Already built; fail-closed (AMBIGUOUS/UNMATCHED, never a guess) |
| Player identity | `research/live_sog_pricing/player_mapping.py::map_player()` | Already built; reused **unchanged** for goalies too via a small index-building adapter (`operational/real_prop_orchestrator.py::_build_goalie_identity_index()`) that only reshapes `research/goalie_intelligence/actual_starters.jsonl`'s rows into the same candidate shape — no new matching logic |
| SOG model | `research/player_sog/live_projection.py::project_player_sog()` | Already built |
| Saves model | `dashboard/goalie_saves_view.py::GoalieSavesEngine.project()` | Already built |
| Starter probability | `dashboard/goalie_saves_view.py::StarterProbabilityEngine.project()` | Already built |
| Pricing/decision | `research/generic_prop_pricing/evaluator.py::evaluate_prop()` | Already built; already validated for GOALIE_SAVES at exactly (20, 25) and SOG at exactly (2, 3, 4, 5) — this project's own pre-existing test suite (`tests/test_generic_prop_pricing.py`) proves it numerically identical to SOG's own `price_observation()` |

## SOG thresholds live-enabled (Part 1/7)

`SOG_VALIDATED_THRESHOLDS = (2, 3, 4, 5)` — unchanged from `research/live_sog_pricing/pricing.py::MODEL_VALIDATED_THRESHOLDS`. Line mapping (`threshold_from_point()`, unchanged, reused): Over 1.5→2+, Over 2.5→3+, Over 3.5→4+, Over 4.5→5+. Over 5.5 (6+) prices correctly (real math) but returns `NOT_MODEL_VALIDATED` — never a real decision. Verified by `tests/test_real_prop_orchestrator.py::TestSOGThresholdMapping`.

## Saves thresholds live-enabled (Part 1/8)

`SAVES_VALIDATED_THRESHOLDS = (20, 25)` — matches `GOALIE_SAVES_VALIDATION_REPORT.md` exactly. Over 19.5→20+, Over 24.5→25+ proceed to pricing; Over 29.5 (30+, PARTIAL/RESEARCH), Over 34.5 (35+, REJECTED), Over 39.5 (40+, INSUFFICIENT_DATA) all correctly return `NOT_MODEL_VALIDATED`. Verified by `tests/test_real_prop_orchestrator.py::TestSavesThresholdMapping`.

## Starter-certainty gate (Part 12/13)

This project has **no real CONFIRMED-starter source for any market** (`docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md`). `operational/real_prop_orchestrator.py::_apply_starter_certainty_gate()` therefore **always** overrides a would-be BET/WATCH to WAIT for Saves — a projected/expected/listed starter is never treated as confirmed, mirroring (not duplicating) `pricing/engine.py`'s identical MONEYLINE goalie-confirmation discipline. Proven by `tests/test_real_prop_orchestrator.py::TestSavesStarterCertaintyGate`: even with the contract artificially verified for testing, a real positive-edge Saves quote never produces a real BET.

## Real orchestration (Parts 10-11)

`operational/real_prop_orchestrator.py::run_real_sog_recommendations()` / `run_real_saves_recommendations()`: real archived DraftKings payload → `market_parser` → `event_mapping`/`player_mapping` (fail-closed) → the existing model → `evaluate_prop()` (fail-closed on `CONTRACT_NOT_VERIFIED`, which is the real, current, correct result for every quote today) → `operational/prospective_recording.py::record_observation()` (immutable snapshot, real idempotency/checkpoint reuse) → `operational/paper_bankroll.py::record_paper_bet(track="REAL_MARKET_PAPER")` iff `action == "BET"` (currently unreachable for Saves per the starter gate above, and unreachable for both markets in real production per the contract gate).

## Settlement / CLV / postmortem (Parts 25-27)

**Settlement: already fully wired**, discovered pre-existing and unmodified — `operational/outcome_resolver.py::resolve_player_stat_threshold()` (SOG, reads `player_game_stats.shots`) and `resolve_goalie_saves()` (Saves, reads `goalie_game_stats.saves`, resolved against the exact named goalie, never "the team's starter") are dispatched automatically by `market_id` prefix in `resolve_prediction()`. No code change was needed; this orchestrator's `market_id` values (`PLAYER_SOG_4PLUS`, `GOALIE_SAVES_20PLUS`) already route correctly.

**CLV: not yet wired for props** (disclosed limitation, not fixed this block) — `operational/closing_price_lookup.py` (Parts 14-16 of the prior block) reads real closing prices from `odds_snapshots`, which today only ever receives MONEYLINE rows (`operational/real_odds_bridge.py`). No SOG/Saves quote has ever been archived into that table. Extending it would require a parallel archiving path for props with zero real data to validate against — deferred until a contract is verified, consistent with Part 33's own "do not build ahead of a real observation" spirit for the parts that genuinely require one.

**Postmortem: already generic**, discovered pre-existing and unmodified — `operational/daily_model_review.py`/`operational/daily_postmortem.py` operate on `record_type`/`market_family`-agnostic ledger rows; no SOG/Saves-specific change was needed.

## Top Conviction / Game Edge Parlay (Parts 19-24)

New minimum shape adapter: `dashboard/real_recommendations_view.py::real_prop_observation_to_opportunity_shape()` converts one real ledger row into the exact opportunity-dict shape `dashboard/conviction.py::top_conviction()`/`combo_eligible_legs()` and `research/game_edge_parlay/engine.py::build_game_edge_parlay()` already consume — deriving edge/EV from the ledger's own stored probabilities via `pricing/odds_math.py`'s unmodified functions (the ledger has no separate edge/EV columns by design). Neither the Top Conviction ranking logic nor the parlay math was touched. Currently always empty in real production (zero real observations exist — no contract verified), which correctly produces `NO_QUALIFYING_GAME_EDGE_PARLAY` from the unmodified parlay engine, never a manufactured parlay. Verified by `tests/test_real_recommendations_view.py::TestRealPropOpportunityShapeAdapter`.

## Automation trigger / scheduler (Parts 15, 16, 34)

Reuses `prop-sweep-first` (every 30 min, windowed to events 3–4.5h from puck drop) and `prop-sweep-second` (every 15 min, windowed to 45–75 min from puck drop, only re-pulling events the first sweep already found a quote for) — both already scheduled, already SOG+Saves-prioritized (`FIRST_SWEEP_MARKETS = "player_shots_on_goal,player_total_saves"`), already quota-disciplined. `operational/live_odds_daily_pull.py::_main()`'s `sweep-first`/`sweep-second` branches now call `real_prop_orchestrator.run_real_sog_recommendations()`/`run_real_saves_recommendations()` immediately after a successful real sweep (`ran: True`). **No new scheduled job was added.** `daily-props-pull` (08:15, broad multi-market pull) remains separately justified: it covers every prop family this project tracks (Goals/Assists/Points/etc.), not just SOG/Saves, and writes to a different cache — the three jobs serve genuinely different purposes, none is redundant, and none was removed or altered in schedule.

## Zero-bet day is valid (Part 31)

A real production run can — and, until DraftKings posts either market, always will — record zero real SOG/Saves recommendations and zero real paper bets. This is never fabricated around.

## Opening-day readiness (Part 39)

`opening_day_readiness.py::check_real_prop_pipeline()` reports `sog_status`/`saves_status` as `READY` / `PENDING_LIVE_CONTRACT` / `NOT_READY` (never collapsed into one status with MONEYLINE), and `game_edge_parlay_status` as `PARTIAL` while `orchestration_status == HEALTHY` (the machinery works; nothing can feed it yet). `PENDING_LIVE_CONTRACT` never forces a hard failure or even a warning.

## Odds archive storage

See `docs/ODDS_ARCHIVE_STORAGE_RECOMMENDATION.md` (Part 35).

## Limitations (honestly disclosed, not fixed this block)

- SOG/Saves CLV is architecture-ready but not wired (no real archived prop price history exists to wire it against yet).
- The Saves starter-certainty gate is permanently WAIT until a real starter-confirmation source is integrated — this is correct, not a bug to "fix" by loosening it.
- The Top Conviction/Parlay adapter has never been exercised against a real BET row (none can exist yet); its correctness is proven via the sanitized fixtures and directly-constructed rows in `tests/test_real_recommendations_view.py`.
