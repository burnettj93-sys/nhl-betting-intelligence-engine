# Starting-Goalie Source Audit

**Date:** 2026-09-24 (Starting Goalie Certainty + Prop Contract Watch block). **Method:** this block did not re-research external providers from scratch — a prior sprint (`GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md`, Sections A-E) already performed a real, dated source-contract review (visiting each site's real public pages, checking `robots.txt`, reading each site's own published documentation — never circumventing an access control). This document synthesizes that real, existing research into the format this block requires, adds the NHL API / MoneyPuck / internal-model classification Part 1 asks for, and does not repeat live checks that would just re-confirm the same, recent findings. **No paid provider was integrated. No new external HTTP request was made to any of these sites this block.**

## Part 1 — Every existing input, classified

| Input | Module | Classification | Why |
|---|---|---|---|
| NHL API schedule (`ingest/nhl_api.py::fetch_schedule_range`) | Real, live, free | **NOT_USABLE_FOR_PREGAME_CERTAINTY** | No starter field of any kind — game time/venue/teams only. |
| NHL API roster (`fetch_team_roster`/`fetch_current_team_roster`) | Real, live, free | **NOT_USABLE_FOR_PREGAME_CERTAINTY** | Lists every goalie on the roster, not who plays tonight. |
| NHL API boxscore (`fetch_boxscore`, `starter` field) | Real, live, free | **HISTORICAL_ONLY** | Only available AFTER the game ends — this is exactly what already, correctly, feeds `player_game_stats`/`goalie_game_stats` for settlement (Part 25 of the prior block), never a pregame signal. |
| `goalie_status_events` table + `features/point_in_time.py::goalie_status()` (read) | Real, already-built infrastructure | **Infrastructure only — currently empty of real data** | The exact mechanism `pricing/engine.py`'s MONEYLINE goalie-confirmation gate already reads from. Structurally ready; nothing writes real rows to it. |
| `ingest/nhl_api.py::record_goalie_status()` (write) | Real, already-built, supports UNKNOWN/EXPECTED/CONFIRMED/CHANGED | **Ready, unused** | Its own docstring already states the real constraint: *"No public NHL API for this either — plug in Daily Faceoff or similar."* Zero real callers exist anywhere in this codebase (confirmed by search) — only `ingest/demo_data.py` writes synthetic rows directly, bypassing this function entirely. |
| `research/goalie_intelligence/model.py::StarterProbabilityEngine` | Real, historical-data-fitted statistical model | **PROJECTED_STARTER** | Real, validated (67.5% top-1 accuracy vs. 65.6% best naive baseline, true holdout n=5,095 games — `GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md` Section Q/R/S), but a statistical projection is never a confirmation, by the project's own explicit design (Section Y). |
| `research/goalie_intelligence/actual_starters.jsonl` | Real, archival, MoneyPuck-derived | **HISTORICAL_ONLY** | Real starter labels for real past games — the training/identity corpus this and other modules use, never a live pregame source. |
| `research/goalie_intelligence/source_schema.py` (external-source consensus engine) | Real, already-built and already-tested (51 tests) | **Infrastructure only — zero real observations ever collected** | `record_observation()` always raises `ExternalSourceUnavailableError` today, by design (Stage 1) — see Part 2 below for why. |
| Daily Faceoff / RotoWire / Goalie Post / Frozen Tools / NHL.com editorial | External, real sites | **NOT_USABLE_FOR_PREGAME_CERTAINTY today** | See Part 2. |

**Conclusion: this project has no real pregame starting-goalie signal of ANY kind in production today** — only a real, validated, but honestly-labeled statistical *projection*, and a complete, tested, but entirely unfed consensus/confirmation mechanism waiting for Stage 2.

## Part 2 — Real candidate sources (synthesized from the existing Foundation report)

| Source | Confirmed/probable labels available? | Update frequency / lead time | API/feed | Access requirement | Cost | Rate limits | Terms/licensing | Historical reliability | Failure mode | Recommended role |
|---|---|---|---|---|---|---|---|---|---|---|
| **Daily Faceoff** | Yes, on-page (per search results) | Unknown — page itself is unreachable to automation | None found | N/A | N/A | N/A | Actively blocks automated access | Unknown (never machine-verified) | Hard Cloudflare **403** on a plain HTTP request, and even `robots.txt` itself returns a Cloudflare block page (verified live during the prior sprint) | **UNSUITABLE for automation** |
| **RotoWire** | Yes — explicit **Confirmed**/**Expected**/**Unknown** labels, with on-page definitions | Implied via "Today"/"Next 7 Days" views (no precise lead-time published) | **Yes — a real, commercial, licensed API** (`api.rotowire.com`, confirmed via RotoWire's own public developer documentation; 80+ paying clients incl. ESPN, Yahoo) | Paid commercial sales license for API access; casual page access has no automation rights | Not published (sales-negotiated) | Not published (API-tier dependent) | `robots.txt` blocks named bulk-scraper tools explicitly; `llms.txt` explicitly permits AI *reference* use, not bulk/training harvesting | N/A (never integrated) | N/A | **PRIMARY candidate if/when the real API is licensed; CONFIRMATION-ONLY/PROJECTION-ONLY via casual reference otherwise** |
| **Goalie Post** (Dobber) | Yes — page text confirms "Confirmed"/"Projected"/"expecting" language | Not confirmed | None documented | Public GET, no login, no JS required | Free to view | Not documented | `Content-Signal: search=yes, ai-train=no, use=reference` — explicit reference-only signal, no bulk/API rights granted | N/A (never integrated) | N/A | **SECONDARY / CONFIRMATION-ONLY, ad-hoc reference lookups only** |
| **Frozen Tools** (Dobber, same company) | Via a broader fantasy-tool suite, not a dedicated starters page | Not confirmed | None documented | Public, no login | Free to view | Not documented | Identical `Content-Signal` policy to Goalie Post | N/A | N/A | **SECONDARY at best** — Goalie Post is this company's actual dedicated starters product |
| **NHL.com editorial** | Only via occasional news articles ("Today's Probable Goalies") | Irregular, not a structured feed | None (`nhl.com/starting-goalies` 404s) | Fully open `robots.txt` | Free | None documented | No barrier, but data isn't structured or reliably updated | N/A | N/A | **MANUAL-ONLY / editorial reference, not a real feed** |

**Best CONFIRMED-source candidate:** RotoWire's real, licensed API — the only one of the four preferred sources that offers a documented, sanctioned path to structured `Confirmed`/`Expected` data at all. Requires a paid sales-negotiated license; not integrated this block, per the explicit instruction.

**Best PROBABLE/PROJECTED-source candidate today:** this project's own `research/goalie_intelligence/model.py::StarterProbabilityEngine` — already real, already validated, already wired into `operational/real_prop_orchestrator.py`'s Saves path. It is the correct source for a `PROJECTED` label; it is deliberately never permitted to produce a `CONFIRMED` one (see Part 4).

**Cost/access implications:** every external option that offers real `Confirmed`/`Probable` semantics is either technically blocked (Daily Faceoff) or requires a paid license/negotiated access RotoWire has never granted this project (confirmed via their own developer documentation, not assumed). Goalie Post/Frozen Tools' own `Content-Signal` policy is an explicit, self-declared refusal of bulk/automated use — respecting it is a licensing/terms decision, not merely a technical one. **No integration is recommended in this block**, consistent with the explicit stop condition.

## Part 3 — Starter certainty states (reused, not reinvented)

Per the explicit instruction to reuse existing terminology, this block adopts `research/goalie_intelligence/source_schema.py`'s own, already-built, already-tested vocabulary — **richer than, and a strict superset of**, the task's own fallback suggestion (`UNKNOWN`/`PROJECTED`/`PROBABLE`/`CONFIRMED`):

| State | Evidence required |
|---|---|
| `CONFIRMED` | At least one real external-source observation whose own `source_status == CONFIRMED` (Part 14's confirmation-override rule: confirmation always wins over any number of disagreeing projections). |
| `PROJECTED` | One or more `PROJECTED`/`EXPECTED`/`LIKELY`-status observations (`PROJECTION_LIKE_STATUSES`) and no confirmation; `agreement_fraction`/`confidence` reflect how many independent sources agree. |
| `UNCONFIRMED` | A real source explicitly reports "not yet decided" (raw wording preserved verbatim in `raw_status`; normalized separately from `UNKNOWN`, which means "no observation exists at all"). |
| `UNKNOWN` | No real external observation exists for this game/team at all — **today's permanent real state**, since Stage 2 has never been integrated. |

No new, parallel status system was created. `operational/real_prop_orchestrator.py::_apply_starter_certainty_gate()` now calls `source_schema.compute_consensus()` directly (see Part 4/Live SOG + Saves Certification's own prior WAIT-only gate, now upgraded to route through this real mechanism).

## Part 4 — Saves actionability policy (audited, not weakened)

| Consensus status | BET | WATCH | WAIT | DATA_UNAVAILABLE |
|---|---|---|---|---|
| `CONFIRMED` (matched goalie) | **Allowed** — passes through to whatever `evaluate_prop()` already decided | **Allowed** | — | — |
| `PROJECTED` / `EXPECTED` / `LIKELY` | Never | Never | **Always** (real edge exists, identity not certain enough) | — |
| `UNCONFIRMED` | Never | Never | **Always** | — |
| `UNKNOWN` (today's real, permanent state) | Never | Never | **Always** | — |
| No real market/model data at all | — | — | — | (unchanged; `evaluate_prop()`'s own existing gate, untouched) |

**CONFIRMED is required for a real BET/WATCH — preserved, not weakened.** This mirrors `pricing/engine.py`'s own real, already-validated MONEYLINE goalie-confirmation policy exactly (CONFIRMED required unless `config.ALLOW_BETTING_ON_EXPECTED_STARTER` is explicitly set, which it is not). No statistical justification was invented for treating `PROJECTED` (even the model's own 67.5%-accurate top pick) as sufficient — the Foundation report's own real accuracy numbers (77% right even at HIGH confidence) directly demonstrate why: roughly 1-in-4 wrong is not an acceptable identity-certainty bar for a market decision, real-money-adjacent or not.

## Part 5 — SOG independence from goalie-state gating (verified)

`research/player_sog/live_projection.py::project_player_sog()` conditions SOG probability on the **opponent team's** real, historical SOG-allowed rate (`opponent_allowed_history`) — never a specific opposing goalie's identity, confirmation state, or presence in `goalie_status_events` at all. Confirmed two ways:
1. **Structural**: `operational/real_prop_orchestrator.py::_price_and_record_sog_pair()` never references `_apply_starter_certainty_gate`, `goalie_status_events`, or `StarterProbabilityEngine` anywhere in its source (asserted directly in `tests/test_real_prop_orchestrator.py::TestSOGNeverCallsTheGoalieStarterGate`).
2. **Behavioral**: the identical real SOG quote produces the byte-for-byte identical action whether `goalie_status_events` has zero rows or a real `CONFIRMED` row for the opposing goalie (`tests/test_real_prop_orchestrator.py::test_sog_outcome_is_identical_with_and_without_goalie_confirmation_data`).

**No fix was needed** — SOG was never incorrectly gated by goalie-state data in the first place.
