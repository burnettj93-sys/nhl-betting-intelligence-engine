# NHL Betting Intelligence Engine — State of the Union
**Audit date: 2026-08-30. Verified baseline: 1,994 / 1,994 tests passing (re-confirmed at the end of this audit, unchanged).**

This is a pure audit. Nothing was refit, promoted, or redesigned. Where something was fixed, it is because leaving it would have made this report itself false — each such fix is called out explicitly in place. All facts below were independently verified against real code, real databases, and real archived API evidence — not inferred from filenames, docstrings, or report titles.

---

## 1. Executive Summary

The engine is **model-complete and software-complete for far more markets than it can currently price**, because the one thing no amount of engineering fixes is time: DraftKings has not yet posted a single NHL prop or team-total line for the 2026-27 season, verified empirically on two separate real API pulls (2026-08-27 and 2026-08-30, the second covering 30 real events across 7 real market keys — every single one returned `"bookmakers": []`). That is the headline finding of this audit: **the biggest current blocker is calendar time, not code.**

Underneath that, the picture is genuinely strong: 4 full seasons of real, verified NHL play-by-play (5,248 games, 1.66M events) and MoneyPuck data underlie 16 model families, of which 7 have at least one cleanly `VALIDATED` threshold and 3 are `SHADOW_VALIDATED` overlays layered on top. A real, live-tested Odds API connection exists, a real live special-teams role pipeline was just built and browser-verified, and a real, immutable, idempotent prospective-observation ledger (schema v3) is ready to receive real prospective predictions the moment real games start. Settlement and CLV are schema-ready but **not** operationally wired — nothing currently resolves a real outcome automatically. Only one market (Player SOG) has a real model-vs-market decision pipeline; Goals/Assists/Points/Saves have validated models but no decision wiring yet. Two real, low-severity contradictions were found in the model registry (documented in Part 94) and left uncorrected pending owner review, per this audit's own rule against quietly repairing findings.

## 2. What The Engine Is

A research-and-shadow NHL player/team prop probability engine: real historical NHL and MoneyPuck data → validated marginal probability models per market → context/role shadow overlays → joint-dependence coherence → (for one market) real sportsbook price comparison → a decision policy → an immutable prospective observation ledger, all displayed through a 31-page Streamlit dashboard that is explicit everywhere about which numbers are real and which are simulated. It has never placed or recommended a real bet with real money. It is currently in a pre-season holding pattern, waiting on two independent real-world events: the 2026-27 season starting, and DraftKings actually posting lines.

## 3. Current Architecture

```
DATA SOURCES (NHL API, MoneyPuck CSV archive, The Odds API)
  -> TEMPORAL / NORMALIZATION LAYER (ingest/timestamps.py, strict game_date < target_date PIT rules)
  -> FEATURE LAYERS (per-market feature modules, special-teams role detector)
  -> MARGINAL MODELS (SOG, Goals, Assists, Points, Blocks, Team SOG, Goalie Saves, Win Model)
  -> CONTEXT / SHADOW OVERLAYS (Context state Goals/Points overlays, SOG PP-role overlay)
  -> COHERENCE (joint dependence registry: logical containment + copula combos)
  -> JOINT MODELS (SOG+TeamSOG+GoalieSaves triple, scoring-family combos)
  -> MARKET PRICING (pricing/odds_math.py: no-vig, EV, Kelly — wired only for moneyline + SOG)
  -> DECISION POLICY (research/player_props/decision_policy.py v3, gates LOW confidence)
  -> PROSPECTIVE RECORDING (operational/prospective_ledger.py, schema v3, immutable)
  -> SETTLEMENT / CLV / LEDGER (schema-ready; NOT automated — see Part 17)
  -> DASHBOARD (31 pages: 10 OPERATIONAL, 11 RESEARCH, 7 DEMO, 2 mixed, 1 index)
```

**Four distinct paths exist, and they must not be confused:**
- **Production path**: NHL_WIN_MODEL (moneyline) is the only fully wired, `PRODUCTION_READY` path with real pricing.
- **Shadow path**: PLAYER_SOG, GOALS, POINTS, CONTEXT_OVERLAY_GOALS/POINTS, PLAYER_SOG_PP_ROLE_OVERLAY — real probabilities computed and (for SOG) recorded prospectively, never driving a real bet.
- **Demo path**: 7 dashboard pages (Player Intelligence, Player Props, Goalies, Combinations, Market Movement, Players, Team Intelligence) — real player identities and real frozen-model probabilities, but a **simulated** schedule and **simulated** sportsbook prices.
- **Research path**: 11 dashboard pages plus the majority of `research/` — real historical re-derivations, explicitly labeled "NOT YET A BETTING RECOMMENDATION."

## 4. Data Inventory

**Directory-level (verified by reading docstrings, not names):**
- `ingest/` (3 files): synthetic demo-data generator, real-but-untested-in-sandbox NHL API client, UTC timestamp normalizer.
- `operational/` (23 files): daily sync/crosscheck/readiness, live odds pull, prospective ledger + recording + settlement, special-teams live role pipeline (this session's own Sprint E work).
- `pricing/` (3 files): `odds_math.py` (conversions/no-vig/EV/Kelly), `engine.py` (moneyline BET/WAIT/PASS/DATA_UNAVAILABLE), `decision.py` (prediction+feature snapshot persistence).

**Sources** (Part 4 table):

| Source | Purpose | Hist/Live | Verified? | Coverage | Blocker |
|---|---|---|---|---|---|
| Official NHL API | Schedule/boxscore/PBP/roster | Both | YES (real corpus + real live event calls) | 4 seasons historical | None |
| NHL TOI HTML reports | Live PP/SH/EV ice time | Live | YES (parsed real 2024-25 game) | Per-game, going forward | Not yet exercised on a real 2026-27 game |
| MoneyPuck (team CSV) | Team-game advanced stats | Historical | YES | 52,480 rows, 4 seasons | Daily sync path is season-aggregate only, not per-game |
| MoneyPuck (skater CSV, `research/player_sog/raw/*.csv`) | Player-game advanced stats | Historical (one-time archive) | YES (real 169MB files/season) | 188,863 player-games | **Do not confuse with `data/raw/moneypuck/skater/`, which holds only 5-byte test-stub files** — a separate, currently-unpopulated daily-sync staging path |
| The Odds API | Live sportsbook prices | Live | YES connectivity; NO market payload yet observed | 30 real 2026-27 events retrieved | Zero DraftKings coverage posted so far (verified twice) |
| Starter/goalie labels | Goalie appearance history | Historical | YES | 10,421 labels, boxscore-derived (not pregame-confirmed) | No pregame starter confirmation source exists |
| Roster data | Current team rosters | Live | YES | Dynamic | Current roster ≠ confirmed dressed lineup |

## 5. Temporal Integrity

Strict `game_date < target_date` (never `<=`) is the uniform PIT rule, enforced independently in: `features/point_in_time.py` (general model state), `research/player_sog/features.py::history_as_of()`, `operational/special_teams_history_store.py::player_history_before()`. 177 tests across 16 files specifically exercise temporal integrity, result-revision handling, exact-start guards, and knowledge-watermark behavior. Odds timestamps use only what the provider actually returns (`bookmaker_last_update_utc`/`market_last_update_utc`) — never an invented native timestamp. Prospective predictions are snapshotted immutably at insert time (schema-trigger enforced, Part 17) — a later stat revision cannot retroactively alter what a prediction "knew."

## 6. Model Inventory

16 entries in `research/model_registry.py`, spanning 8 base marginal models, 2 joint-dependence families, 1 context-signal research family, 2 context probability overlays, and 1 special-teams role overlay. See Part 8 for full statuses; Part 94 for two registry-content contradictions found.

## 7. Model Status Matrix (Part 8 — Master Model Table)

| Model | Target | Champion | Status | Validated | Partial | Rejected/Insuff. | Prospective | Live inputs? | Live pipeline wired? | Decision-eligible? | Key limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| NHL_WIN_MODEL | Team win P | Elo-based | VALIDATED | — | — | — | N/A (game-level) | YES | YES (`pricing/engine.py`) | YES, if a real moneyline price exists | No goalie-quality adjustment |
| PLAYER_SOG | P(SOG≥k) | NB GLM headline | VALIDATED | 1-6+ | — | — | Not started | YES | YES (`research/live_sog_pricing/`) | Only once DK posts real SOG line | SOG payload shape never observed live |
| PLAYER_SOG_PERIOD | Per-period SOG | Per-period GLM | PARTIAL | 7 of 9 | P2_3+,P3_3+ | — | Not started | YES | NO | NO | No live pricing path built |
| GOALS | P(goals≥k) | Hierarchical+context | VALIDATED | 1+ | — | Insuff. 2+ | Not started | YES | NO (board cache only) | NO | No decision wiring beyond board cache |
| ASSISTS | P(assists≥k) | NB GLM | VALIDATED (registry) / **1+,2+ per source report** | 1+,2+ real; 3+ claimed but unsupported — see Part 94 | — | 3+ too rare (0.6% base rate) | Not started | YES | NO | NO | Registry overreach on 3+ |
| POINTS | P(points≥1) | Empirical baseline (GLM lost) | Champion=baseline | 1+,2+ | — | Insuff. 3+ | Not started | YES | NO | NO | No `mu` output — different residual math needed downstream |
| BLOCKED_SHOTS | P(blocks≥k) | NB GLM | VALIDATED | 1+,2+,3+ | — | — | Not started | YES | NO | NO | No live pricing path; PK-role overlay REJECTED |
| TEAM_SOG | P(team SOG≥k) | Direct Poisson GLM | VALIDATED | 20/25/30/35+ | 40+ | — | Not started | YES | NO | NO | No dashboard live pricing |
| GOALIE_SAVES | P(saves≥k) | Per-threshold champion | PARTIAL | 20+,25+,P2 | 30+,P1,P3 | 35+ REJECTED, 40+ INSUFFICIENT | Not started | YES | NO | NO | Only 2 of 5 thresholds cleanly usable |
| TEAM_GOALS_PERIOD | Team goals/period | — | ATTEMPTED_NOT_VALIDATED | — | — | — | N/A | N/A | NO | NO | Closed; needs new evidence to reopen |
| JOINT_SHOT_WORKLOAD | Joint SOG/Team/Goalie | Copula/logical | VALIDATED | 4 combos | — | — | Not started | Depends on marginals | NO real pricing | NO | Research probability math only |
| JOINT_SCORING_DEPENDENCE | Goal/Assist/Point/SOG combos | Copula/logical | VALIDATED (mostly); **2 "triple" entries mislabeled** | see Part 94 | — | — | Not started | Depends on marginals | NO | NO | Same as above |
| PLAYER_CONTEXT_STATE | Cold/hot/arena signals | — | MIXED (see Part 9) | COLD_AND_TOI_DECLINE (goals/points only) | SOG/assists PARTIAL | Blocks/arena NOT_VALIDATED | Not started | YES | Feeds 2 overlays | NO (research signal only) | Media sentiment NOT_BUILT (no legal corpus) |
| CONTEXT_OVERLAY_GOALS | Context-adjusted P(goals≥1) | Fixed logit offset | VALIDATED_OVERLAY | 1+ in COLD_AND_TOI_DECLINE | — | — | Pre-registered, not started | YES | Shadow-only | NO | Narrow state scope by design |
| CONTEXT_OVERLAY_POINTS | Context-adjusted P(points≥1) | Bayesian blend | VALIDATED_OVERLAY | 1+ in COLD_AND_TOI_DECLINE | — | — | Pre-registered, not started | YES | Shadow-only | NO | Same |
| PLAYER_SOG_PP_ROLE_OVERLAY | Role-adjusted SOG | Log-mu overlay | PARTIAL | 1+,2+,3+ | — | Insuff. 4+,5+,6+ | **Prospective pipeline built this session, not yet observing real games** | YES (live role pipeline built) | YES (shadow) | NO | Overlay itself never affects a real bet by design |

## 8. Joint Model Inventory

11 combinations in `research/joint_shot_workload/joint_dependence_registry.py`: 4 shot/workload combos (SOG↔TeamSOG, TeamSOG↔GoalieSaves, SOG↔GoalieSaves, the SOG+TeamSOG+GoalieSaves triple) plus 7 scoring combos added later (SOG↔Goal, Goal↔Point [exact logical identity], Assist↔Point [exact logical identity], SOG↔Point, SOG↔Assist, and 2 further "triple" scoring combinations). The two triple scoring combinations are `RESEARCH` status with `validated_combinations=[]` — they are **structurally redundant** reductions of already-validated pairs (e.g., SOG+Goal+Point reduces exactly to SOG+Goal because Goal⇒Point is a logical identity), never independently fitted. Fréchet-bound clipping and a Gaussian copula (`joint_scoring_dependence/joint_models.py`) are the real math underneath; redundant-leg detection (`GOAL_1_PLUS`→drops `POINT_1_PLUS`) is real, tested code, not just a UI warning.

## 9. Context / Shadow Overlays

| Overlay | Target | Status | Historical result | Operational status | Prospective requirement |
|---|---|---|---|---|---|
| CONTEXT_OVERLAY_GOALS | Goals 1+, COLD_AND_TOI_DECLINE | VALIDATED_OVERLAY | Fixed logit offset −0.180 | SHADOW_VALIDATED | 200 obs / 50 players / 30 dates (`PROSPECTIVE_VALIDATION_PROTOCOL.md`) |
| CONTEXT_OVERLAY_POINTS | Points 1+, COLD_AND_TOI_DECLINE | VALIDATED_OVERLAY | Bayesian blend shift −0.0415 | SHADOW_VALIDATED | Same table |
| PLAYER_SOG_PP_ROLE_OVERLAY | SOG 1+/2+/3+ | PARTIAL | beta_role PP1=+0.064/PP2=+0.009; transition betas direction-separated | SHADOW_VALIDATED | 300 obs / 75 players / 30 dates (added this session's addendum) |
| PLAYER_SOG_PP_TRANSITION_OVERLAY | SOG 1+/2+/3+ (transition-only) | PARTIAL | Redundant with the combined overlay above | RESEARCH | Not recommended standalone |
| PLAYER_BLOCKS_PK_REMOVAL_OVERLAY | Blocks, PK removal | **REJECTED** | frac_improved 0.0-0.016 both seasons | RESEARCH | Closed — do not reopen without new evidence |
| PLAYER_GOALS/ASSISTS/POINTS_PP_ROLE_OVERLAY | Goals/Assists/Points | PARTIAL | Inconsistent across seasons | RESEARCH | Not pursued this session (explicit instruction) |

`COLD_AND_TOI_DECLINE` is genuinely validated **only** for goals and points — it is PARTIAL for SOG/assists and NOT_VALIDATED for blocks, which is exactly why only 2 overlays exist, not 5.

## 10. Special-Teams Intelligence

Built across 3 sprints this session, all real, all verified: manpower-state parser (`situationCode`-driven, excludes shootout semantics), penalty-window reconstruction (fixed a real "duration measured to own last event, not the true next-boundary" bug), a full PP/PK opportunity corpus, a unit-rank role detector (`PP_UNIT_SIZE=5`, `MIN_MEANINGFUL_TOI_SECONDS=20`), 9-state role-transition classification, and — this session — a **live/prospective** version of that exact same detector (`operational/special_teams_roles_live.py`), parity-tested at 96.7% exact match against the historical version (100% of divergences are safety-conservative `ROLE_UNCERTAIN` downgrades for trade/re-acquisition edge cases). Feeds exactly one production consumer: the `PLAYER_SOG_PP_ROLE_OVERLAY` shadow path (Part 9). Blocks PK-removal was tried and rejected; Goals/Assists/Points role overlays remain PARTIAL/RESEARCH; Hits was never attempted (Part 24).

## 11. Event-Timing Intelligence

Period scoring splits, first-goal timing, first-team-to-score, goalie-pull-vs-delayed-penalty disambiguation (a real ~19-second gap threshold, `DELAYED_PENALTY_WINDOW_SECONDS=30`), and penalty timing are all built as a real research corpus (`research/period_event_timing/`) mapped against the 142-market canonical registry for readiness. None of this feeds a production or shadow probability yet — it remains a pure research foundation. `TEAM_GOALS_PERIOD` (the one model that tried to consume period-timing features directly) is `ATTEMPTED_NOT_VALIDATED` and explicitly closed (Part 16).

## 12. Market Registry

**142 canonical markets** (test-enforced: `test_total_market_count_is_142`), consolidated from 164 raw provider-style labels, across **17 process families** (`PLAYER_ACTIVE_ROLE_TOI` through `JOINT_DEPENDENCE_SIMULATION`) and 14 display categories. `model_status` breakdown: `NOT_BUILT=94, VALIDATED=17, RESEARCH=14, INSUFFICIENT_DATA=5, INSUFFICIENT_TAIL_DATA=4, PARTIAL=3, EMPIRICAL_BASELINE_REMAINS_CHAMPION=2, REJECTED=2, DERIVABLE_NOT_VALIDATED=1`. Sum = 142, verified. **`dk_contract_verified` is `False` for every one of the 142 entries** — no market's real payload shape has ever been confirmed.

## 13. Master Market Matrix

Full 142-row matrix delivered separately as **`ENGINE_MARKET_MATRIX.csv`** (generated programmatically from `research/player_props/market_registry.py`, not hand-typed, to guarantee it matches the registry exactly). Every row carries: market_id, category, process_family, target_variable, model_status, threshold_validation_status, historical_data_status, odds_api_support, dk_contract_verified, the four `requires_*` flags, parlay_eligibility_status, and five derived audit columns (can-calculate-true-P-today, can-calculate-no-vig-P-today, can-calculate-edge/EV-today, can-issue-decision-today, biggest-blocker). **Every row's "can calculate no-vig P today" and "can issue decision today" columns currently read NO** for the uniform, verified reason: no live sportsbook price exists for any of the 142 markets yet.

## 14. Sportsbook Support

Model support (a validated probability) and sportsbook support (a real, payload-verified market) are **completely different axes** and must not be conflated:

| | Model VALIDATED at ≥1 threshold | odds_api_support=SUPPORTED (documented key known) | dk_contract_verified (real payload seen) |
|---|---|---|---|
| Count | 17 markets | 7 markets (PLAYER_SOG 2/3/4+, ASSISTS 1+, POINTS 1+, GOALS 1+, GOALIE_SAVES 25+) | **0 markets** |

Even the 7 "SUPPORTED" markets only mean the market key is believed to exist in The Odds API's catalog — none has ever actually returned a DraftKings quote to inspect. `PLAYER_HITS_*` (all 4 thresholds) and `PLAYER_PLUS_MINUS` are explicitly `UNSUPPORTED_MARKET` (not offered by the provider at all).

## 15. Pricing Engine

Real, verified functions in `pricing/odds_math.py`: `american_to_decimal`, `american_to_prob`, `no_vig_two_way`, `prob_to_american`, `expected_value`, `max_acceptable_price` (solves the two-sided no-vig system, not a naive single-side calc), `kelly_fraction`. Conservative probability exists via two different real mechanisms depending on market type: a heuristic maturity-based confidence band for moneyline (`models/combined_model.py`, explicitly disclaimed as "NOT a statistically validated CI"), and a real normal-approximation lower bound on the Poisson rate for count props (`research/player_sog/count_models.py::conservative_mu`, `CONSERVATIVE_Z=0.84`). **Kelly is wired only for moneyline** (`pricing/engine.py`) — no player-prop pricing path applies any stake-sizing logic at all.

## 16. Decision Policy

`research/player_props/decision_policy.py`, **`POLICY_VERSION = "prop_decision_policy_v3"`**. It is a gating layer, not the primary decision computer: the base BET/WATCH/WAIT/PASS/DATA_UNAVAILABLE decision is computed by `research/live_sog_pricing/pricing.py::decide()` for SOG (the only prop market with a real decide() function) and by `pricing/engine.py` for moneyline. Decision policy v3 then narrows LOW-confidence BET/WATCH down to a market-specific ceiling (`ASSISTS/POINTS/GOALS/PLAYER_SOG_PERIOD_3 → WATCH`); SOG (full-game) and Blocks are deliberately exempt from this ceiling (their LOW-confidence skill is non-negative per historical evaluation). A separate, earlier LOW-confidence gate inside `decide()` itself downgrades BET/WATCH to **WAIT** on data-quality grounds — two distinct LOW-confidence mechanisms, not one.

## 17. Prospective Ledger

`operational/prospective_ledger.py`, **`SCHEMA_VERSION = 3`** (this session's Live Special-Teams Role Shadow sprint added 7 shadow columns). Record types: `MODEL_OBSERVATION`, `SHADOW_POLICY_OBSERVATION`, `REAL_BET`, `HISTORICAL_RESEARCH`. Immutability is a real SQL trigger (`predictions_immutability`, `BEFORE UPDATE`) that aborts on any change to ~25 prediction-time columns; only 8 settlement columns are ever mutable, and only via `settle_prediction()`. Idempotency is a SHA-256 key over `(game_id, player_id, market_id, threshold, side, model_version, prediction_cutoff_utc)` — a duplicate insert returns the existing row rather than creating a new one. `REAL_BET` rows require `stake`/`placed_odds`/`placed_at_utc`/`sportsbook` at insert time, structurally isolating real bets from every observation type. **`settle_prediction()` performs zero outcome lookup of its own** — it trusts whatever the caller passes; there is no automated resolver behind it (Part 18).

## 18. Settlement / CLV

**Settlement is NOT automated.** `operational/settle_daily_observations.py`'s own docstring states plainly: *"no real NHL per-player-prop outcome resolver is wired into this script yet... it does not resolve actual_outcome automatically."* It only lists candidate predictions past `event_start_utc`. **CLV is schema-ready but never populated by any real caller** — the formula exists (`clv = american_to_prob(closing_odds) − american_to_prob(entry_odds)`, `operational/prospective_recording.py`), but no code path anywhere supplies a real `closing_odds` value; that would require a human or a not-yet-built closing-price resolver.

## 19. Portfolio / Parlays

| Component | Exists? | Evidence |
|---|---|---|
| Fractional Kelly | YES — moneyline only | `pricing/engine.py`, `config.KELLY_FRACTION_MULTIPLIER=0.25` |
| Max single-bet exposure | YES — moneyline only | `config.MAX_SINGLE_BET_BANKROLL_PCT=0.02` |
| Correlation-aware exposure limits | **NO** | No such function anywhere |
| Daily exposure caps | **NO** | No such constant anywhere |
| Portfolio optimizer | **NO** | Does not exist |
| Real joint-probability parlay math | YES (research-grade) | `joint_scoring_dependence/joint_models.py` — real copula, real Fréchet clipping |
| Real parlay *price* | **NO** | `dashboard/pages/28_Combinations.py` explicitly labels its price "SIMULATED... for UX review only," "DEMO ONLY — NOT OPERATIONAL" |
| Parlay recommendation engine | **NO** | `decision_policy.parlay_eligible()` is metadata-only; nothing acts on it |

## 20. Simulator Readiness

| Component | Status |
|---|---|
| Team SOG process | Marginal VALIDATED; not assembled into a simulator |
| Player SOG allocation | VALIDATED marginal; no allocation-across-team-total model built |
| Goalie saves | PARTIAL; no simulator integration |
| Scoring process | Joint dependence VALIDATED for pairs; no full game-scoring simulator |
| Player attribution | Goal/Assist/Point logical identities exist; no simulator |
| Penalty process | Real research corpus exists (Part 11); not wired to any simulator |
| PP process | Real corpus + role detector; not a generative simulator |
| Score-state transitions | RESEARCH only (event-timing corpus) |
| Goalie pulls | RESEARCH only, real disambiguation logic, no simulator |
| Event timing | RESEARCH corpus only |
| Starter uncertainty | Projected-vs-confirmed distinction exists; not simulator input |
| Roster/lineup uncertainty | Not modeled at all |

**No simulator exists in any form.** Per instructions, none was built during this audit.

## 21. Dashboard / Product

31 pages (`dashboard/app.py`'s own `st.navigation`): **10 OPERATIONAL, 11 RESEARCH, 7 DEMO, 2 mixed (Game Detail, Today — both have a demo-data fallback branch alongside a real-data branch), 1 index (Research Hub)**. No page is classified LEGACY or SHADOW by direct evidence, though the shadow SOG overlay is currently only surfaced inside one DEMO-labeled page (Player Intelligence's PP Role expander, built this session) rather than a dedicated shadow-status page.

## 22. Demo vs Live

Demo path (`dashboard/demo_data.py`): real NHL player identities, real frozen-model probabilities computed against real historical data as of a fixed `SIMULATED_DATE = 2026-10-14`, but a fabricated schedule and fabricated sportsbook prices — disclosed via `DEMO_MODE_LABEL` on every demo page. Live path: `dashboard/pages/8_Live_SOG_Markets.py` is the one page with a real Odds API connection; it currently has nothing to show (zero DK coverage). `dashboard/pages/9_Data_Status.py` and `13_Play_By_Play_Status.py` read cached freshness snapshots only, never a live network call on page load.

## 23. Test Coverage

76 test files, **1,994 test functions**, categorized: Marginal models 668 (19 files), Ingestion 213 (12 files), Temporal integrity 177 (16 files), Pricing/decision 260 (8 files), Joint models 132 (2 files), Special-teams 150 (5 files), Structural-audit/meta 150 (6 files), Dashboard 133 (3 files), Operational/ledger 111 (3 files). No standalone "prospective-validation" test file exists; that behavior is tested cross-cutting inside the operational/preseason test files. Full suite re-run at the end of this audit: **1,994/1,994, 0 failures** (301.996s), unchanged from the start.

## 24. Major Bugs / Lessons

| Bug | Impact if unfixed | How caught | Regression test? |
|---|---|---|---|
| Penalty-window duration measured to own last event, not true next-boundary | Understated real PK/PP window lengths | Manual cross-check vs. known PP conversion benchmark | YES |
| `drawn_by` penalty role stamped with penalized team's ID | Wrong team attributed for drawn penalties | Direct inspection of `pbp_event_players` | YES |
| Delayed-penalty pulls conflated with genuine desperation pulls | Corrupted goalie-pull timing research | Real ~19s gap found empirically | YES |
| PSEUDOCOUNT=0.5 zeroed beta_role for Goals/Assists | False "no signal" result for low-count props | Suspicious all-zero fit result | YES |
| Per-row log-ratio regression unstable at low counts (Blocks) | Implausible +1.3-1.44 beta | Cross-check against known base rate | YES |
| Mixed-direction beta_transition fit let opposite effects cancel | Masked real promotion/demotion signal | Explicit "do not assume symmetric" instruction | YES |
| `build_game_unit_labels` unit-label/prefix mismatch | Silent role mislabeling | Direct output inspection | YES |
| Trade/re-acquisition mixed old+new team stints | Fabricated `STABLE_PP1` for a since-departed role | Real player trace (TBL→...→TBL) | YES (4 tests) |
| Demo vig construction inverted | Backwards demo edge signs | Direct sanity check | YES |
| `conservative_mu` probability-vs-count scale bug | Wrong conservative probability | Cross-check against known distribution | YES |
| Structural-audit false positive on PIT-safe list slicing (3 separate occurrences: role-transitions research script, then this session's live pipeline) | None (false positive) — but required 2 rounds of justified exceptions | Test suite itself flagged it | YES |
| Tests writing synthetic archive files into the real `data/raw/the_odds_api/live/` evidence directory | Clutters real evidence with `evt-a`/`evt-b` fixtures (distinguishable, but a hygiene risk) | **Found during this audit** — 25 of 88 files in that directory are test artifacts | **NO — open item, see Part 30** |

## 25. Rejected Research Register (DO NOT REOPEN WITHOUT NEW EVIDENCE)

| Tested | Result | Why closed | What would justify reopening |
|---|---|---|---|
| MoneyPuck special-teams features (win model) | "KEEP CURRENT MODEL" — fails 6/9 adoption conditions | Best candidate statistically indistinguishable from no-op | A materially larger/cleaner special-teams feature set |
| MoneyPuck team xG features (win model) | "KEEP CURRENT MODEL" — fails 3/8 conditions | Fails season consistency + calibration + effect size | A validated, versioned xG methodology from MoneyPuck |
| Goalie-quality win-model adjustment | "KEEP CURRENT MODEL" | One candidate makes it worse; another shows an overfitting signature | A larger, cleaner goalie-quality signal |
| Team Goals by Period | ATTEMPTED_NOT_VALIDATED (literal status) | Never cleared validation bar | New period-scoring feature architecture |
| Goalie Saves 35+ | REJECTED | Failed at that specific threshold | More seasons of tail data |
| Blocked Shots PK-removal overlay | REJECTED — frac_improved 0.0-0.016 both seasons | No real signal at any threshold | A materially different PK-role feature |
| Assists context overlay | Never built — no assists slot in `context_overlay_results.json` | COLD_AND_TOI_DECLINE only PARTIAL for assists | Assists state validation reaching VALIDATED first |

## 26. Partial / Unresolved Research Register

PP-role overlays for Goals/Assists/Points (PARTIAL, RESEARCH status, inconsistent across seasons); Goalie Saves 30+/P1/P3 (PARTIAL); Team SOG 40+ (PARTIAL); PP_POINTS (RESEARCH, no model built — Part 25 of this doc's own numbering, see below); Hits (no model, no historical validation — Part 24); first-goal-scorer family (RESEARCH corpus exists, no model).

## 27. Software Still Unfinished

- Goals/Assists/Points/Saves/Blocks/Team SOG have **no live model-vs-market decision pipeline** — only SOG has one.
- Settlement resolver (real outcome lookup) does not exist.
- CLV populator (real closing-price capture) does not exist.
- Correlation-aware exposure limits, daily exposure caps, and a portfolio optimizer do not exist.
- Parlay recommendation engine does not exist; Combinations page produces a simulated price only.
- No dedicated System Health component exists yet for the special-teams role pipeline (Part 34/54).
- Player Props PP-role filter, Today PP-role badge, Game Detail PP-role surfacing were explicitly deferred in the immediately-preceding sprint and remain undone (Part 53).
- `data/raw/the_odds_api/live/` is polluted by synthetic test fixtures from `tests/test_live_odds_daily_pull.py` — a test-isolation fix, not a research gap.

## 28. Requires Real 2026-27 Data

- Prospective validation for every SHADOW_VALIDATED model/overlay (nothing has started collecting).
- Real DraftKings SOG/Goals/Assists/Points/Saves payload shape verification (currently documented-contract-only).
- Live schedule/`game_id` mapping exercised end-to-end against a real current-season slate.
- Real CLV (needs a real closing price to exist first).
- Lineup/starter confirmation against real pregame news (out of scope this session by explicit instruction).
- Role-overlay prospective evidence for the 300/75/30 sample minimums.

## 29. Optional Polish

Dashboard PP-role filter/badge/surfacing (Part 27); a dedicated freshness/health widget for special-teams data; updating `PROJECT_DOCUMENT_INDEX.md` to include the 10 currently-unlisted root reports (Part 31); reconciling the stale operational-status table inside `CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md` (cosmetic — the live registry is already correct).

## 30. Technical Debt

- **Test/evidence directory pollution**: `tests/test_live_odds_daily_pull.py` writes synthetic `evt-a`/`evt-b` archive files into the same real `data/raw/the_odds_api/live/` directory used for genuine API evidence (25 of 88 files there are test fixtures). Distinguishable by filename today, but a real risk of confusion for a future developer scanning "real" evidence. **Found during this audit; not fixed, per the audit's own no-quiet-repair rule** — flagged for a future trivial fix (point the test at a temp directory).
- Two separate, easily-confused MoneyPuck skater-data paths (`research/player_sog/raw/*.csv` = real; `data/raw/moneypuck/skater/` = test-stub only) — Part 4.
- `research/model_registry.py` has 2 confirmed contradictions with its own cited sources (Part 94) that no existing test catches.
- Repository is a **single git commit** ("Snapshot: full history through MoneyPuck data-contract review") with 204 files currently untracked — no incremental commit history exists to bisect against if something regresses silently.

## 31. Documentation Drift

57 root-level `.md` files; only 7 are git-tracked (`README.md` and 6 early reports). `PROJECT_DOCUMENT_INDEX.md` itself is untracked and does not mention 10 files, including this session's own `LIVE_SPECIAL_TEAMS_ROLE_SHADOW_REPORT.md`, `SPECIAL_TEAMS_ROLE_OVERLAY_VALIDATION_REPORT.md`, and `SPECIAL_TEAMS_ROLE_TRANSITION_REPORT.md`. Foundational/living docs: `README.md`, `ENGINE_SYSTEM_ARCHITECTURE.md`, `PROJECT_DOCUMENT_INDEX.md`, `PROSPECTIVE_VALIDATION_PROTOCOL.md`, `PROSPECTIVE_LEDGER_SCHEMA.md`, `SIMULATION_INVARIANTS.md`, `COMPLETE_NHL_MARKET_ARCHITECTURE_REPORT.md`. The remaining ~46 are historical, per-sprint completion snapshots by this project's own convention — not living docs, and not expected to be kept in sync with current state.

## 32. Risk Register (top 15, by probability × impact × detectability)

| # | Risk | Prob. | Impact | Detectability |
|---|---|---|---|---|
| 1 | SOG market payload shape differs from documented contract on first real observation | HIGH | MEDIUM | HIGH (loud `UnrecognizedOutcomeShapeError` by design) |
| 2 | DraftKings never posts some prop markets at all for NHL (Hits/Plus-Minus already confirmed unsupported) | MEDIUM | LOW | HIGH (already known) |
| 3 | Test-evidence directory pollution masks a genuine future API-contract change | LOW | MEDIUM | LOW (currently unfixed) |
| 4 | Registry contradiction (ASSISTS 3+, joint triples) silently trusted by a future sprint | MEDIUM | MEDIUM | LOW (no test catches it) |
| 5 | Scheduler never gets installed before Sept 17 activation target | MEDIUM | HIGH | HIGH (plist exists, just not loaded) |
| 6 | Live NHL API schema drift vs. the untested-in-sandbox `ingest/nhl_api.py` | LOW | HIGH | LOW (never exercised against a live response) |
| 7 | Odds API monthly credit-cycle reset assumption (`cycle_reset_day=1`) is wrong | LOW | LOW | MEDIUM |
| 8 | Stale roster vs. real dressed lineup causes a wrong PP-role read | MEDIUM | MEDIUM | MEDIUM (ROLE_UNCERTAIN fallback exists) |
| 9 | No real starter confirmation source — wrong starting goalie assumed | MEDIUM | HIGH | LOW |
| 10 | Shadow/production contamination if a future dev wires the shadow overlay into a real decision by mistake | LOW | HIGH | MEDIUM (naming/comments are explicit) |
| 11 | Missing opposing market side breaks no-vig math silently | LOW | MEDIUM | MEDIUM |
| 12 | Player-name mapping ambiguity (duplicate names) misattributes a real prediction | LOW | MEDIUM | HIGH (tested, falls back to AMBIGUOUS) |
| 13 | Settlement never gets built before real bets are ever placed, corrupting CLV history | MEDIUM | MEDIUM | HIGH (already documented as unbuilt) |
| 14 | Single-commit repo loses bisectability if a future regression is subtle | MEDIUM | MEDIUM | HIGH (already known) |
| 15 | Confusing DEMO-mode dashboard numbers for live capability (a human error risk, not a code risk) | MEDIUM | HIGH | MEDIUM (labeling is prominent but pervasive) |

## 33. Live-Day Workflow

| Step | Status |
|---|---|
| Data sync (NHL) | BUILT (`operational/nhl_sync.py`) |
| Data sync (MoneyPuck) | BUILT, season-aggregate cadence only for daily; per-game requires the new TOI-report path |
| Schedule | BUILT |
| Roster | BUILT (dynamic, not static) |
| Role history | BUILT (this session) |
| Starters | PARTIAL — projected only, never confirmed |
| Model predictions | BUILT for SOG/Goals/Assists/Points/Blocks/TeamSOG/GoalieSaves/Win; decision-attached only for SOG + moneyline |
| Prospective observations | BUILT (ledger v3), never yet exercised on a real game |
| Odds | BUILT (`live_odds_daily_pull.py`), real connectivity, zero real coverage so far |
| Pricing | BUILT for moneyline + SOG only |
| Decision | BUILT for moneyline + SOG only |
| Pregame refresh | NOT_BUILT as a distinct checkpoint beyond `PRIMARY_DAILY`/`PRE_GAME_UPDATE` naming |
| Puck-drop lock | NOT_BUILT |
| Result ingest | BUILT (boxscore-based) |
| Settlement | NOT_BUILT (manual only) |
| CLV | NOT_BUILT (schema-ready only) |

## 34. Preseason Readiness (target 2026-09-19; odds activation target 2026-09-17)

Scheduler is **not installed anywhere on this machine** — verified directly (`launchctl list`, `~/Library/LaunchAgents/`, `/Library/LaunchDaemons/`, `crontab -l` all checked; only a template plist exists in the repo, never copied/loaded). `find_next_preseason_start()` and `should_run_today()` are real, schedule-driven gates inside `live_odds_daily_pull.py` — the code itself will correctly wait if run before the real activation window, but **nothing currently runs it automatically**. Data pipelines, models, and the ledger are ready; the single missing preseason-readiness item is installing the scheduler, and the user has separately instructed to hold off until mid-September.

## 35. Regular-Season Readiness

Same technical readiness as preseason (the code makes no calendar distinction beyond the schedule-driven gates above) — the meaningful difference is that DraftKings is far more likely to post real lines close to regular-season openers than for exhibition games, and the SOG-market payload-shape risk (Part 32, risk #1) is the main thing standing between "connected" and "actually pricing."

## 36. Readiness Scorecard (0-10, no single blended score)

| Dimension | Score | Why |
|---|---|---|
| Historical data quality | 8 | 4 full seasons, cross-validated in multiple places (PBP vs. boxscore, TOI report vs. MoneyPuck); known, bounded caveats (blocked-shot drift, xG version unknown) |
| Temporal / anti-leakage integrity | 9 | Uniform strict-`<` PIT rule, 177 dedicated tests, a dedicated structural-audit meta-test that itself caught 3 false positives this session |
| Marginal model quality | 7 | 7 families cleanly validated at ≥1 threshold; Points never beat a baseline; several families thin at tail thresholds |
| Model market coverage | 4 | 17 of 142 markets have any validated threshold; 94 have no model at all |
| Joint dependence quality | 7 | Real copula + logical-identity math, validated combos exist; 2 registry-entry mislabels found this audit |
| Live NHL data readiness | 7 | Schedule/boxscore/PBP/roster all live-capable; TOI-report path built but not yet exercised on a real 2026-27 game |
| Sportsbook data readiness | 3 | Real connectivity, zero real market payload ever observed for any of the 142 markets |
| Pricing engine readiness | 6 | Full math library real and tested; wired end-to-end for only 2 of 16 model families |
| Decision policy readiness | 6 | Real v3 gating logic; only meaningful for the 2 wired families |
| Prospective validation readiness | 7 | Schema, immutability, idempotency all real and tested; zero real observations collected yet (season hasn't started) |
| Settlement / ledger readiness | 4 | Ledger itself is solid; settlement resolver and CLV populator do not exist |
| Dashboard / UX readiness | 7 | 31 pages, consistent DEMO/RESEARCH/OPERATIONAL labeling; PP-role dashboard surfacing incomplete |
| Demo readiness | 8 | Consistently labeled, real-model-output-on-simulated-market pattern is well executed across 7 pages |
| Live straight-bet readiness | 2 | Blocked almost entirely by "no real market price exists yet," not by missing code |
| Parlay readiness | 1 | Real joint-probability math exists; pricing and recommendation layers do not |
| Simulator readiness | 1 | No simulator exists; several real component pieces could feed one later |
| **Overall preseason readiness** | **6** | Software/model readiness is genuinely ahead of what the calendar currently allows it to prove |

## 37. Value Gap Analysis

Ranked by (expected betting value × engine leverage) ÷ (implementation cost, adjusted for dependency readiness and time sensitivity):

1. **Wire Goals/Assists/Points/Saves into a real decision pipeline** (mirroring SOG) — highest leverage, models already validated, only the wiring is missing, and it was explicitly attempted once before this session and never finished.
2. **Settlement resolver** — without it, no real bet history can ever be scored, regardless of how good the models are.
3. **Fix the test-evidence pollution** — trivial cost, removes a real (if currently low-impact) data-integrity risk.
4. **Correct the 2 registry contradictions** (Part 94) — trivial cost, prevents a future sprint from building on a false "validated" claim.
5. Everything else genuinely should wait for real 2026-27 data (Part 28) — no amount of further engineering substitutes for a real market existing.

## 38. Candidate Next Slices (scored)

| Candidate | Value | Data ready | Model deps ready | Live deps ready | Research risk | Impl. cost | Time urgency | Recommended now? |
|---|---|---|---|---|---|---|---|---|
| HITS marginal model | 3 | 2 | 2 | 2 | 7 | 6 | 2 | NO |
| PP_POINTS validation | 3 | 6 | 5 | 3 | 6 | 5 | 2 | NO |
| Goal attribution / first-goal scorer | 3 | 6 | 3 | 2 | 6 | 6 | 2 | NO |
| First-team-to-score / goal timing | 3 | 7 | 3 | 2 | 5 | 6 | 2 | NO |
| Full game scoring process | 2 | 5 | 3 | 2 | 8 | 9 | 1 | NO |
| Period-event timing expansion | 3 | 7 | 4 | 2 | 6 | 6 | 2 | NO |
| Live Goals/Assists/Points odds parser + decision wiring | **8** | 9 | 9 | 6 | 3 | 4 | 7 | **YES** |
| Live goalie saves odds parser + decision wiring | 6 | 8 | 8 | 6 | 3 | 4 | 6 | YES (secondary) |
| Full live market integration (all 142) | 4 | 3 | 3 | 4 | 6 | 9 | 3 | NO |
| Confirmed special-teams unit source | 4 | 3 | 3 | 2 | 6 | 7 | 3 | NO |
| Lineup confirmation | 4 | 3 | 3 | 2 | 6 | 7 | 3 | NO |
| Generalized parlay engine | 2 | 5 | 5 | 2 | 5 | 8 | 1 | NO |
| Game simulator | 2 | 4 | 3 | 1 | 8 | 10 | 1 | NO |
| Dashboard polish | 2 | 8 | 8 | 5 | 2 | 3 | 2 | NO |
| Prospective season monitoring only | 6 | 10 | 10 | 8 | 1 | 1 | 9 | **YES** |

## 39. Top 5 Recommendations

1. **Finish the Goals/Assists/Points/Saves live decision pipeline**, reusing SOG's exact pattern — the single highest-leverage remaining software gap; models are already validated, and this was started once (Sprint A) and never finished.
2. **Build a real settlement resolver** before any real observation needs to be scored — otherwise every prospective observation this season accumulates as unresolved `PENDING` forever.
3. **Fix the test-evidence-directory pollution** (`tests/test_live_odds_daily_pull.py` → use a temp path) — a 15-minute fix that removes a real, if currently minor, evidence-integrity risk.
4. **Correct the two model-registry contradictions** (ASSISTS 3+, joint "triple" mislabeling) — trivial, prevents a future sprint from building on a false premise.
5. **Otherwise, do nothing but watch the calendar** — install the scheduler around Sept 15-17 as already planned, then let the real 2026-27 season generate the one thing no more engineering can produce: a real market to price against.

## 40. Single Next Research Slice

**None recommended right now.** Every open research question in this codebase (Hits, PP_POINTS, first-goal, full game simulation) is data-ready but not leverage-ready — the marginal value of another research family is lower than finishing the wiring on markets already validated. If forced to pick exactly one: **PP_POINTS validation**, because it reuses the just-built live special-teams role pipeline most directly and has the most existing supporting research (PLAYER_SPECIAL_TEAMS category already has 2 markets defined).

## 41. Single Next Operational Slice

**Finish the Goals/Assists/Points/Saves live-odds decision pipeline**, using `research/live_sog_pricing/` as the literal template (client, event/player mapping, market parser, pricing, observation ledger) — this is squarely operational wiring, not new research.

## 42. Single Next Product Slice

**Stop product work.** No dashboard change is higher-value right now than fixing the two software gaps above; the dashboard is already well-labeled and consistent.

## 43. What Should Wait

Everything in Part 28 (real 2026-27 data), plus: any further PP-role research on Goals/Assists/Points/Blocks (explicitly closed pending stronger evidence), any simulator work, any parlay engine work, promoting any SHADOW_VALIDATED model to production, installing the scheduler before ~Sept 15-17.

## 44. "If NHL Started Tomorrow"

**Would work**: moneyline pricing/decision (if a real price existed), SOG model probability computation, SOG shadow role-overlay computation and recording, prospective ledger recording for any market with `MODEL_OBSERVATION` fields populated.
**Would fail or be unverifiable on first contact**: the SOG market parser's assumed payload shape (never observed live — real risk, not hypothetical).
**Would WAIT**: everything gated behind decision_policy's LOW-confidence ceiling; anything requiring a starting-goalie confirmation that doesn't exist.
**Would not exist at all**: a Goals/Assists/Points/Saves real decision, any settled bet, any real CLV, any real parlay price, any simulator output.

## 45. Example — McDavid SOG 3+

| Step | Status | Note |
|---|---|---|
| Historical inputs | WORKS | Real 4-season corpus |
| Live role (PP1/PP2) | WORKS | This session's live pipeline; McDavid verified `STABLE_PP1`, HIGH certainty, live-browser-confirmed |
| Model probability | WORKS | `research.player_sog.live_projection.project_player_sog` |
| Conservative probability | WORKS | `conservative_mu`, real normal-approx bound |
| Shadow role-adjusted probability | WORKS | This session's `sog_shadow_overlay.py`, recorded 0.702 vs. raw 0.664 in a real smoke test |
| DraftKings price | **BLOCKED** | Zero DK SOG coverage observed as of 2026-08-30; payload shape unverified even when/if it appears |
| No-vig / max buy / edge | BLOCKED | Requires the above |
| Decision (BET/WATCH/WAIT/PASS) | BLOCKED | Requires the above |
| Prospective recording | WORKS (mechanically) | Ledger accepts a record with null market fields; never yet exercised on a real game |
| Settlement | NOT_BUILT | No automated resolver exists |

## 46. Example — Goalie Saves 25+

| Step | Status |
|---|---|
| Historical inputs | WORKS |
| Model probability (25+, VALIDATED) | WORKS |
| Conservative probability | WORKS (same math family as SOG) |
| DraftKings price | BLOCKED — `player_total_saves` is `odds_api_support=UNKNOWN` per market registry, and the one real broad pull (2026-08-30) requested it and got zero bookmaker coverage |
| No-vig/edge/EV | BLOCKED |
| Decision | **NOT_BUILT even in principle** — no `decide()`-equivalent function exists for Goalie Saves; only SOG has one |
| Prospective recording | Ledger *could* accept a `MODEL_OBSERVATION`, but no code path currently calls it for this market |

## 47. Example — Goals 1+

| Step | Status |
|---|---|
| Historical inputs | WORKS |
| Model probability (1+, VALIDATED) | WORKS |
| Context overlay (COLD_AND_TOI_DECLINE) | WORKS, SHADOW_VALIDATED — real fixed logit offset −0.180 |
| Confidence / LOW policy | WATCH_ONLY ceiling applies to Goals under decision_policy v3 — even with a real edge, LOW confidence caps the action at WATCH, never BET |
| DraftKings price | BLOCKED — same empirical zero-coverage finding |
| Decision | **NOT_BUILT** — no dedicated `decide()` for Goals; would need the same wiring gap closed as Saves |
| Sportsbook gap | Confirmed real and current, not assumed |

## 48. Example — Combination (SOG + Goalie Saves)

| Step | Status |
|---|---|
| Marginal support (SOG 3+, Saves 25+) | Both VALIDATED individually |
| Joint support | `PLAYER_SOG__GOALIE_SAVES` is VALIDATED in `joint_dependence_registry.py` (combos `PLAYER3_GOALIE20`, `PLAYER4_GOALIE25`) — real, tested copula math |
| Price limitation | BLOCKED — no real individual leg price exists yet, so no real parlay price can be derived even with valid joint math |
| Current recommendation limitation | The Combinations dashboard page can show this TODAY, but explicitly as a "SIMULATED PARLAY PRICE... DEMO ONLY — NOT OPERATIONAL" |

## 49. Owner-Level Summary

The engine knows a lot of real hockey, verified against real games — over a million and a half real play-by-play events across four real seasons. It has built real, tested probability models for shots, goals, assists, points, blocks, team shots, and goalie saves, and it knows which of those it can trust and at which thresholds (roughly half the thresholds it has tried have cleared a real statistical bar; the other half were honestly rejected or left as "not enough data yet," and those decisions are written down so nobody re-litigates them by accident). It just built a genuinely new capability this week: knowing, in real time, which players are on the top power-play unit right now, and using that to sharpen its shot-total predictions — carefully, and only as a shadow check that never touches a real number shown to anyone placing money.

What it cannot do yet is place a real bet, because DraftKings simply has not posted a single NHL prop price for the coming season — checked twice, most recently three days ago, across every market this engine understands. That is not a software problem; it is a calendar problem, and it will resolve itself as the season approaches. In the meantime, two small but real inconsistencies were found in the project's own "master list" of validated models (one prop threshold and one redundant combination were mislabeled as more proven than they are) — neither is dangerous, but both are written down here rather than quietly fixed, so a human can decide. The single highest-value thing to do next, once someone decides to keep building, is to give the three other validated player-prop markets (goals, assists, points) the same real pricing pipeline shots already has — everything else can reasonably wait for the season to actually start.

## 50. Technical Appendix

See `ENGINE_STATUS_SNAPSHOT.json` (machine-readable model/market/readiness snapshot) and `ENGINE_MARKET_MATRIX.csv` (full 142-row market matrix, generated directly from `research/player_props/market_registry.py`). Full model registry: `research/model_registry.py` (16 entries). Full market registry: `research/player_props/market_registry.py` (142 entries, 17 process families). Full test suite: `python3 -m unittest discover -s tests -p "test_*.py"` → 1,994/1,994.

---

## Final Questions

**HOW MANY CANONICAL MARKETS CURRENTLY EXIST?** 142.

**HOW MANY ARE PRODUCTION_VALIDATED?** 0 markets have both a validated model AND a verified live sportsbook contract. If the question means "model-validated": 17.

**HOW MANY ARE SHADOW_VALIDATED?** At the model-family level: PLAYER_SOG, GOALS, POINTS, CONTEXT_OVERLAY_GOALS, CONTEXT_OVERLAY_POINTS, PLAYER_SOG_PP_ROLE_OVERLAY = 6 model/overlay entries carry `operational_status=SHADOW_VALIDATED` in `MODEL_REGISTRY`. At the individual canonical-market level, that maps to roughly a dozen specific thresholds.

**HOW MANY ARE PARTIAL?** 3 at the market-registry `model_status` level (`PARTIAL`); at the `MODEL_REGISTRY` family level: PLAYER_SOG_PERIOD, GOALIE_SAVES, PLAYER_SOG_PP_ROLE_OVERLAY = 3.

**HOW MANY ARE DATA_READY / DERIVABLE?** 1 (`DERIVABLE_NOT_VALIDATED`) at market-registry level, plus 75 markets with `historical_data_status=AVAILABLE_UNUSED`/`AVAILABLE_UNUSED_AS_STANDALONE_TARGET` combined — data exists, no model built yet.

**HOW MANY ARE REJECTED?** 2 (market-registry `model_status=REJECTED`): includes `PLAYER_PLUS_MINUS`.

**HOW MANY ARE INSUFFICIENT_DATA?** 9 total (`INSUFFICIENT_DATA`=5 + `INSUFFICIENT_TAIL_DATA`=4).

**HOW MANY ARE NOT_BUILT?** 94.

**HOW MANY CAN PRODUCE A MODEL PROBABILITY TODAY?** 17 markets, cleanly, at their validated thresholds (plus 2 more via the empirical-baseline champion for Points).

**HOW MANY HAVE VERIFIED LIVE SPORTSBOOK SUPPORT?** 0. Zero of 142 have `dk_contract_verified=True`.

**HOW MANY CAN ACTUALLY BE PRICED END-TO-END TODAY?** 0. No market has a real sportsbook price to compare against right now.

**HOW MANY CAN ISSUE A LEGITIMATE OPERATIONAL DECISION TODAY?** 0 for real money; 0 even mechanically, since only SOG and moneyline have a `decide()` function at all, and neither has a real price to feed it right now.

**WHAT IS THE BEST CURRENT MARGINAL MODEL FAMILY?** PLAYER_SOG — validated at all 6 thresholds tried, the only market with a real, live-tested (if payload-unverified) pricing pipeline.

**WHAT IS THE WEAKEST PRODUCTION-ELIGIBLE MODEL FAMILY?** GOALIE_SAVES — only 2 of 5 thresholds cleanly usable (20+, 25+), one rejected outright (35+), one insufficient data (40+).

**WHAT IS THE MOST PROMISING SHADOW MODEL?** PLAYER_SOG_PP_ROLE_OVERLAY — the only shadow model with a live prospective pipeline built and verified this session, ready to start collecting real data the moment the season begins.

**WHAT IS THE MOST IMPORTANT REJECTED IDEA TO KEEP CLOSED?** The Blocked Shots PK-removal overlay (frac_improved 0.0-0.016 across the board, both seasons) — the cleanest, most decisive rejection in the project.

**WHAT IS THE BIGGEST CURRENT DATA GAP?** No real, confirmed pregame starting-lineup/starting-goalie source — everything downstream is "projected," never "confirmed."

**WHAT IS THE BIGGEST CURRENT MODEL GAP?** Coverage: 94 of 142 markets have no model at all; among built models, Points never beat its own empirical baseline.

**WHAT IS THE BIGGEST CURRENT LIVE-INTEGRATION GAP?** No decision pipeline exists for Goals/Assists/Points/Saves — only SOG has one, despite all four having validated models.

**WHAT IS THE BIGGEST CURRENT PRODUCT GAP?** The just-built PP-role signal is not yet surfaced anywhere except one demo-labeled expander — Player Props filter, Today badge, and Game Detail surfacing all remain undone.

**WHAT IS THE BIGGEST CURRENT OPERATIONAL RISK?** The SOG market's real payload shape has never been observed — the parser is built against a documented contract only, and could raise `UnrecognizedOutcomeShapeError` (by design, loudly) the first time a real quote appears.

**IF THE NHL STARTED TOMORROW, COULD THE ENGINE PLACE/RECOMMEND REAL STRAIGHT BETS?** NO.

**WHICH MARKET(S)?** None, today — blocked purely by the absence of a real sportsbook price, confirmed empirically as of 2026-08-30.

**WHAT WOULD STILL FAIL?** Goals/Assists/Points/Saves would have no decision pipeline even if a price appeared; the SOG parser's real-payload behavior is unverified.

**IS THE PROSPECTIVE LEDGER READY?** YES (schema, immutability, idempotency all real and tested) — but zero real observations have been recorded from an actual live game yet.

**IS SETTLEMENT READY?** NO.

**IS CLV READY?** PARTIAL — formula and schema ready, never populated by a real caller.

**IS THE PP-ROLE SOG OVERLAY READY FOR PROSPECTIVE SHADOW VALIDATION?** YES.

**IS IT READY FOR REAL BETTING DECISIONS?** NO.

**IS THE DAILY ODDS PULL BUILT?** YES.

**IS ITS SCHEDULER INSTALLED?** NO — verified directly on this machine; only a template plist exists in the repo.

**WHAT SHOULD BE INSTALLED/ACTIVATED ON SEPTEMBER 17?** The `operational/com.nhlengine.odds-daily-pull.plist` launchd job, copied into `~/Library/LaunchAgents/` and loaded via `launchctl load` — per the user's own standing instruction to wait until then.

**HOW MANY TESTS PASS?** 1,994 / 1,994.

**HISTORICAL DATA QUALITY?** 8. **TEMPORAL INTEGRITY?** 9. **MARGINAL MODEL QUALITY?** 7. **MODEL MARKET COVERAGE?** 4. **JOINT DEPENDENCE QUALITY?** 7. **LIVE NHL DATA READINESS?** 7. **SPORTSBOOK DATA READINESS?** 3. **PRICING ENGINE READINESS?** 6. **DECISION POLICY READINESS?** 6. **PROSPECTIVE VALIDATION READINESS?** 7. **DASHBOARD / UX READINESS?** 7. **LIVE STRAIGHT-BET READINESS?** 2. **PARLAY READINESS?** 1. **SIMULATOR READINESS?** 1. **OVERALL PRESEASON READINESS?** 6 (no single blended score is the headline — see Part 36 for why each dimension is scored independently).

**WHAT ARE THE TOP FIVE NEXT STEPS?** (1) Wire Goals/Assists/Points/Saves into a real decision pipeline. (2) Build a real settlement resolver. (3) Fix the test-evidence-directory pollution. (4) Correct the two registry contradictions. (5) Otherwise wait for the calendar.

**WHAT IS THE SINGLE NEXT MODEL / RESEARCH SLICE?** None recommended immediately; if forced, PP_POINTS.

**WHAT IS THE SINGLE NEXT OPERATIONAL SLICE?** Finish the Goals/Assists/Points/Saves live-odds decision pipeline using SOG's pattern.

**WHAT IS THE SINGLE NEXT PRODUCT / UX SLICE?** Stop product work for now.

**WHAT SHOULD WE ABSOLUTELY NOT BUILD YET?** A simulator, a generalized parlay engine, any further PP-role research on Goals/Assists/Points/Blocks, or promotion of any shadow model to production.

**WHAT SHOULD WE SIMPLY WAIT FOR REAL 2026-27 DATA TO ANSWER?** Every prospective-validation question in this document (Part 28) — nothing else can substitute for real games actually being played and real lines actually being posted.
