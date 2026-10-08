# Product completion checklist

Each line says what the requirement is, its status, and where the evidence is. "Verified remotely" means checked on the hosted app
(https://nhl-betting-intelligence-engine.streamlit.app) after deployment. Status words: **DONE**, **DONE (blocked input)** — built, waiting for an
input only a person can supply, **BLOCKED** — cannot be completed from here, with the exact cause.

## A. Today
| Requirement | Status | Evidence |
|---|---|---|
| ET-date games, current day | DONE | `dashboard/pages/21_Today.py`; `operational/product_data.py::load_games` (ET date from the start instant; every game's derived date equals the NHL's own `game_date`); `tests/test_product_data.py::TestSchedule`; verified remotely |
| Up to 5 distinct +100 cross-game tickets with DraftKings prices and quote timestamps, legs, matchup, time, combined price, $10 stake, return, hit chance, reason, status | DONE | `ui.ticket_card`; `operational/daily_tickets.py`; `tests/test_daily_tickets.py` |
| Cash, open stakes, equity, settled P&L, ROI; shared exposure; explanation when fewer than 5 | DONE | Today header metrics, `exposure`, `empty_slot_reason`; `tests/test_product_pages.py::test_today_shows_account_games_and_empty_slot_explanation` |
| Automatic recording and settlement without manual entry; $500 / $10, history, original ticket ids, no reset or top-up | DONE | trader job every 15 minutes (`operational/real_parlay_paper_trader.py`); the 3 pre-existing tickets keep their ids (`T54F8E52D8B2D50`, `TBDAC49EFEAAEDA`, `TDD065E3FE00331`); ledger v4 migration is additive and tested (`tests/test_manual_orders.py::TestMigration`) |
| Early-recording exposure exhaustion → recording policy | DONE | wave reservation, `docs/UNIFIED_TICKET_WORKFLOW.md` §Recording windows; `tests/test_daily_tickets.py::TestRecordingWaves` |

## B. Stale games / exact game / dates
| Requirement | Status | Evidence |
|---|---|---|
| Root cause of stale and last-season games | DONE | Simulated seasons were inside the production `nhl.db` and the published snapshot's `demo` section fed the pages. Production DB rebuilt real-only (`operational/isolate_demo_history.py`, archive kept); snapshot schema now rejects `demo`/`live_moneyline_rows`; no bundled fallback (`tests/test_product_data.py::TestSnapshotRules`) |
| Defaults = current upcoming/live games; completed games by dated history; last season never default | DONE | `_default_date`; Games page season/type/date pickers; `tests/test_product_pages.py` (`defaults_to_the_current_date_and_season`, `date_picker_reaches_a_past_final`) |
| Game Detail opens the exact game; direct links; dropdowns; session state | DONE | `?game=<id>`; `test_game_detail_opens_exactly_the_requested_game`, `..._unknown_game_does_not_substitute_another`; verified remotely (URL `/Game_Detail?game=2026020056`) |
| After-midnight-UTC, Eastern dates | DONE | `tests/test_product_data.py::test_eastern_date_follows_the_start_instant_not_the_utc_date`, `tests/test_eastern_time.py` |

## C. Goalies / D. Players
| Requirement | Status | Evidence |
|---|---|---|
| Goalie: W-L-OTL, SV%, GAA, SO, form + sample size, opponent/time, start chance, confirmation status/source/time, conditional win chance, expected saves/GA, ranges | DONE | `dashboard/pages/27_Goalies.py`; `operational/nhl_goalie_stats.py` (NHL.com record, fetch time, last-good on failure); `research/product_models/team_goalie.py`; saves range coverage 83% and GA range 90% on held-out games; `tests/test_product_pages.py::test_goalies_page_shows_the_required_fields`; verified remotely |
| Starting-goalie confirmation not bypassed; no invented status | DONE (blocked input) | no automated feed can be used (`docs/STARTING_GOALIE_SOURCE_AUDIT.md`); manual confirmation with source and time (`operational/goalie_confirmations.py`) feeds the existing moneyline and saves gates; every goalie is Unconfirmed until a person records one |
| Player: season and recent TOI, PP time, line, PP unit, shots/goals/assists/hits/blocks; matchup expected values; role source + timestamp; history not relabelled | DONE | `dashboard/pages/30_Players.py`; roles inferred from recent ice time with the games used (`research/product_models/live.py::infer_roles`, `tests/test_product_data.py::TestRoles`); verified remotely |

## E. Best option per person
| Requirement | Status | Evidence |
|---|---|---|
| Single preferred, else cross-game parlay; prices, quote age, probability, return, rationale; quote vs estimate labelled; dedupe; no "redundant" cards; overlong combos omitted; rejection diagnostics admin-only | DONE | `operational/player_options.py`; `dashboard/pages/26_Player_Props.py`; `tests/test_manual_orders.py::TestOptions`; `tests/test_product_pages.py::test_best_options_page...`; diagnostics only on Diagnostics (admin). Replayed on real archived prices from 2026-10-07 (`4` options of `42` priced people) |

## F. Manual "Add to paper book — $10"
| Requirement | Status | Evidence |
|---|---|---|
| Explicit action only; never on browse/filter/refresh | DONE | `tests/test_product_pages.py::test_nothing_is_written_by_browsing_or_filtering`; `tests/test_manual_orders.py::test_browsing_writes_nothing` |
| Shared account; origin AUTOMATIC vs MANUALLY_ADDED; separate performance; does not consume automatic slots; needs cash; frozen provenance; auto-settles; durable | DONE | `operational/manual_orders.py`, `paper_bankroll.create_manual_paper_bet`, immutability trigger; Paper Performance "By origin"; tests in `tests/test_manual_orders.py` |
| No duplicates (repeat clicks, retries, concurrency); revalidate; explicit acceptance of changes | DONE | order-id and bet-identity idempotency, concurrent test, `NEEDS_ACCEPTANCE` flow (`docs/MANUAL_ORDERS.md`) |
| Implemented in the REMOTE app with durable authenticated writes | DONE (blocked input for one-click) | link path works end to end (owner-authored GitHub issue; transport verified on the live repository, issue #49: order answered REJECTED with no ledger write). One-click path needs the Streamlit secrets `PAPER_ORDER_TOKEN` and `ORDER_ALLOWED_EMAILS` (see docs/MANUAL_ORDERS.md) |
| Exposure reported across both origins | DONE | `daily_tickets.exposure`, Today "Shared exposure" |

## Demo / simulated content removal
| Requirement | Status | Evidence |
|---|---|---|
| Nav, pages, defaults, fallbacks, bundled and remote snapshot fallbacks | DONE | simulated pages deleted; `dashboard/cloud_snapshot/board.json` deleted; reader has no fallback and shows cause + last successful update; simulated harness moved to `research/demo_board/` (imported by nothing in `dashboard/`); simulated paper bets archived out of the ledger (`isolate_demo_history --paper-bets`); `tests/test_product_pages.py` bans simulated wording on every page |

## Model Health, validation, markets
| Requirement | Status | Evidence |
|---|---|---|
| Model Health describes operating models (freshness, coverage, markets, dependencies, validation, ticket-supplying) without relabelling | DONE | `dashboard/pages/22_Model_Health.py`; `product_data._model_health`; verified remotely |
| Chronological out-of-sample validation, splits, baselines, Brier/log loss/calibration, limited-history and changed-role slices, versions preserved, haircut ≠ calibration, no profitability claim | DONE | `docs/MODEL_VALIDATION.md`, `docs/validation/*.json`, `research/product_models/` |
| Shots, points, goals models | DONE | skater model beats both baselines for shots 1–5+, points 1+/2+, goals 1+ (held-out 2025-26); shots and points price tickets |
| Saves | DONE (blocked input) | validated 20+ to 35+; legs exist only for goalies with a recorded confirmation (gate unchanged) |
| Moneyline | PARTIAL (stated) | pricing path unchanged (Elo with a heuristic band, unvalidated on this corpus); the validated strength model beats the home-rate baseline and is shown, not used to price |
| Puck line | BLOCKED (specific) | spreads prices never requested, no margin model, no settlement resolver (`docs/MARKET_COVERAGE_AUDIT.md`) |
| Anytime goals pricing | BLOCKED (specific) | model validated, but `player_goal_scorer_anytime` prices are never requested (credit cost on a metered plan) and no payload exists to certify |
| Safeguards (exact $10, invalid stake/odds no writes, atomic funds, timestamps, no silent stale/future, no rewrites, alerts not refunds, supported settlement only, reconciliation) | DONE | existing tests kept and passing; `deploy/export_audit_zip.py` writes `ledger_board_reconciliation.json` |

## Ontario, credentials, postmortems
| Requirement | Status | Evidence |
|---|---|---|
| Ontario price verification flow; settlement rules not guessed; US-feed labels kept | DONE (blocked input) | `docs/ONTARIO_VERIFICATION.md`; manual spot-check form on option cards; `VOID_RULES_VERIFIED` stays False |
| Odds API key rotation | BLOCKED (person) | `python3 deploy/verify_odds_key.py` still reports the exposed key active and configured; steps in `docs/CREDENTIAL_ROTATION.md` |
| Postmortems accessible and useful; first losing ticket included | DONE | Morning Review "Postmortems for losing tickets"; `reports/daily/ticket_postmortem_TBDAC49EFEAAEDA.md`; audit ZIP `audit_evidence/postmortems/`; `operational/daily_review.py` (frozen entry, leg results, account effect, closing prices, defect vs variance, calibration, evidence-gated proposals, origins separate, no auto-retune) |

## Operations
| Requirement | Status | Evidence |
|---|---|---|
| Scheduled jobs run a clean pinned release | DONE | `~/nhl_engine_release` pinned by `deploy/release_checkout.sh`; new job `com.nhlengine.manual-order-job` (2-minute queue pass) installed |
| Where evidence appears | — | `operational/logs/*.log`, the Data Status and Diagnostics pages, `reports/daily/`, the `cloud-data` branch. Nothing monitors after this session ends |
