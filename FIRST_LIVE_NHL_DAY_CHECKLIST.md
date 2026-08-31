# First Live NHL Day Checklist

For the first real 2026-27 slate. Follow in order; each step names the module/script that does it today, or states plainly that it does not exist yet.

1. **NHL schedule sync** — `sync_daily.py` (exists, production).
2. **Roster sync** — `sync_daily.py` (exists, production; per `operational/data_readiness_cache.json`).
3. **MoneyPuck readiness check** — `operational/moneypuck_daily.py` (exists) — confirm today's skater/team files are present and pass `research/moneypuck_ingestion`'s schema validation (Part 94: schema, season, row count, player IDs, game IDs, duplicate rows, SO-score semantics).
4. **Projected active-player generation** — per-prop `projected_active()` functions exist (Goals, Points, others) — no single cross-prop "today's active roster" generator exists yet; run per prop as needed.
5. **Starter projection** — `research/goalie_intelligence/` (RESEARCH status, Stage 1 only — historical-rotation-pattern based, never confirmed).
6. **Odds API event discovery** — `research/live_sog_pricing/client.py` (exists, SOG only).
7. **DraftKings market fetch** — same client, SOG only. **No other market family has a tested fetch path.**
8. **Raw payload archive** — `data/raw/the_odds_api/live/` (exists, small, real captures already present).
9. **Payload contract verification** — `research/live_sog_pricing/` has the only verified contract. Any new market's payload must be verified against a real capture before trusting it (Part 97).
10. **Event mapping** — exists for SOG (`research/live_sog_pricing/player_mapping.py`).
11. **Player mapping** — same module; normalized-name + team-context matching, MATCHED/AMBIGUOUS/UNMATCHED, never last-name-only.
12. **Line/outcome mapping** — exists for SOG only.
13. **Freshness check** — `dashboard/data_status_view.py` + `operational/data_readiness_cache.json` (exists).
14. **Marginal probabilities** — run each validated prop's `live_projection.py` (SOG, Goals, Assists, Points, Blocks all exist).
15. **Context overlays** — `research.context_overlay.prediction_stack.ShadowContextStack.predict(...)` (exists, this sprint) — Goals/Points only, SHADOW_VALIDATED tag.
16. **Coherence** — automatic inside `ShadowContextStack.predict()` (Part 7, this sprint).
17. **Conservative probability** — `count_models.conservative_mu` exists for props with a mu; **not yet wired into the overlay output** (documented gap, `CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md` Section AE).
18. **Market no-vig** — `pricing/odds_math.py::no_vig_two_way` (exists, stress-tested this sprint, 0 numerical failures).
19. **Edge / EV** — `pricing/odds_math.py::expected_value`, `max_acceptable_price` (exist).
20. **Policy** — `research/player_props/decision_policy.py::gate_low_confidence` (exists, v3, unchanged).
21. **Shadow ledger** — **exists now** (Preseason Closing sprint): `operational/prospective_ledger.py` (append-only SQLite, DB-trigger-enforced immutability, idempotency-key dedup), `operational/prospective_recording.py` (orchestration with the `DEMO_NOT_RECORDABLE` guard and MODEL_REGISTRY eligibility gate), and CLI entry points `operational/record_daily_predictions.py` / `operational/settle_daily_observations.py`. Each recorded prediction now carries a `prediction_checkpoint` (`PRIMARY_DAILY` / `PRE_GAME_UPDATE` / `MARKET_REFRESH`) for later CLV-style analysis. **Not yet exercised against a real live game day** — see `PROSPECTIVE_VALIDATION_PROTOCOL.md` and `PRESEASON_ENGINE_READINESS_REPORT.md` Section BC for what "exists" does and doesn't mean here.
22. **Dashboard render** — Live SOG Markets page (exists, operational, cache-only reads). Every other market family has no live-render page yet. The Today and Ledger pages now show real (currently-zero) ledger operational metrics (`operational/prospective_ledger.py::operational_summary`) — recorded-today count, pending-settlement count, last-recorded timestamp, checkpoint breakdown.
23. **Later result settlement** — **upgraded, real, tested** (Preseason Operational Readiness Closure sprint, 2026-08-30): `operational/outcome_resolver.py` resolves SOG/Goals/Assists/Points (official boxscore) and Goalie Saves (named-goalie-specific, multi-goalie-aware) and Moneyline outcomes from already-ingested `nhl.db` data; `operational/settle_daily_observations.py::run_settlement_batch()` finds PENDING predictions past their event start, resolves each, and calls `settle_prediction()` — idempotent (a settled row is never re-selected), fails closed (`GAME_NOT_FINAL`/`UNSUPPORTED_SETTLEMENT_MARKET`) rather than guessing, and distinguishes a named player/goalie who never appeared in the real stats (VOID for real money, a separate UNRESOLVED eligibility state for research-only observations). **Team SOG and Blocked Shots still fail closed** (`TEAM_SOG_NOT_INGESTED` / `BLOCKS_NOT_INGESTED`) — neither is captured in `nhl.db`'s frozen schema, and Blocks additionally has a real, unresolved methodology-drift risk (see `operational/outcome_resolver.py`'s own docstring). **Still never exercised against a real live game** — only against real-shaped test fixtures.

## Sept 17 activation checklist (Part 51 — do NOT execute any of this before then)

Per the user's own standing instruction to hold off on live activation until mid-September:

1. **Scheduler installation** — copy `operational/com.nhlengine.odds-daily-pull.plist` into `~/Library/LaunchAgents/` and `launchctl load` it. Verify with `launchctl list | grep nhlengine` that it is actually loaded (confirmed NOT loaded as of 2026-08-30's audit).
2. **First-run verification** — after the plist fires once (or a manual `python3 -m operational.live_odds_daily_pull` run), confirm: a new archive file appears under `data/raw/the_odds_api/live/` with a REAL (non-`evt-a`/`evt-b`) event id; `operational/live_multimarket_board_cache.json` updates; no exception in `operational/logs/`.
3. **First-real-market contract verification** — the moment ANY market returns a non-empty `bookmakers` array for the first time, follow Part 41's workflow below before trusting it.
4. **Settlement smoke test** — once the first real game of the season goes FINAL, run `python3 -m operational.settle_daily_observations` and confirm the reconciliation summary's counts make sense (WIN/LOSS/VOID/UNRESOLVED add up to `total_candidates`); manually spot-check one resolved prediction's `actual_outcome` against the real box score.
5. **Prospective observation smoke test** — confirm `operational/record_daily_predictions.py` actually inserts a real `PRIMARY_DAILY` row for a real upcoming game (not a demo/test DB), and that a same-day `--checkpoint PRE_GAME_UPDATE` re-run correctly requires that PRIMARY_DAILY row to already exist (Part 25/26).

## First-real-payload workflow (Part 41)

The exact, required sequence the moment ANY sportsbook market returns a real, non-empty quote for the first time (this has never happened yet for any of the 142 canonical markets):

1. **Archive the raw payload unchanged** — already automatic via `research/live_sog_pricing/archive.py::archive_result()` for anything routed through the existing client.
2. **Inspect the outcome shape** — read the real JSON by hand; do not assume it matches `market_parser.py`'s documented-contract guess.
3. **Verify player identifiers/names** — confirm the real `description`/`name` fields resolve unambiguously via `research/live_sog_pricing/player_mapping.py`.
4. **Verify threshold representation** — confirm the real `point` field means what `threshold_from_point()` assumes (Over 3.5 = P(SOG>=4)).
5. **Verify over/under sides** — confirm `outcomes[].name` really is `"Over"`/`"Under"` as documented, not something else.
6. **Verify prices** — confirm `price` is American odds, not decimal or fractional.
7. **Verify timestamps** — confirm `bookmaker_last_update`/`market_last_update` populate as expected; never invent a native timestamp if only provider observation time exists.
8. **Add a provider adapter fixture from the REAL payload** — a literal, byte-for-byte copy of the real archived response, not a hand-written approximation.
9. **Add a regression test against that real fixture** — in `tests/test_generic_prop_pricing.py` or a new market-specific test file, proving the parser produces the correct `NormalizedPropMarket`.
10. **Only then** add `(sportsbook, canonical_market_id)` to `research/generic_prop_pricing/provider_adapter.py::VERIFIED_CONTRACTS` and set that market's `dk_contract_verified=True` in `research/player_props/market_registry.py`. Never set it preemptively.

## Bottom line

Steps 1-14, 16, 18-21 (infrastructure), 22, and the upgraded step 23 are real, tested, and ready today (for the families that have live pricing — SOG only for a full model-vs-market pipeline; Goals/Assists/Points/Saves can now run marginal probability + the shared generic pricing evaluator, correctly returning `DATA_UNAVAILABLE`/`CONTRACT_NOT_VERIFIED` rather than a guess, the instant a real market appears). Steps 15, 17 are new/partial from an earlier sprint. **The ledger infrastructure in step 21 and the resolver in step 23 have never been exercised against a real live game** — that remains the one real blocker between "the pipeline exists and is tested" and "we have prospective evidence," and it is a calendar blocker (the season hasn't started), not a software one.
