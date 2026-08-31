# Live DraftKings SOG Pricing via The Odds API

Connects the validated SOG probability model (`PLAYER_SOG_FOUNDATION_REPORT.md`) to real market
data from **The Odds API** (`https://the-odds-api.com/`), for **market ingestion + pricing +
model-vs-market comparison only**. **No bets were placed. No parlay optimizer was built.**

**Headline real-world finding: real API transport works end-to-end (auth, event listing, and
per-event odds all confirmed against genuine responses), but zero DraftKings SOG markets are
currently posted for any real NHL event** — the soonest real event The Odds API returned is
32 days out at time of writing, well outside the ~24-72 hour window sportsbooks typically use to
post player props. This is a calendar-timing fact about the market, not a system failure, and it
is reported plainly rather than worked around.

---

## A. The Odds API plan/access status

Connected successfully with the project's configured key (read from `.env` via
`research/live_sog_pricing/env_config.py`, gitignored, never printed/logged/committed —
verified in `tests/test_live_sog_pricing.py::TestApiKeyHandling`, 5 tests). `GET /v4/sports`
confirms `icehockey_nhl` is a currently `active: true` sport key on this plan.

## B. Exact live endpoints used (API key REDACTED)

```
GET https://api.the-odds-api.com/v4/sports?apiKey=[REDACTED]
GET https://api.the-odds-api.com/v4/sports/icehockey_nhl/events?apiKey=[REDACTED]
GET https://api.the-odds-api.com/v4/sports/icehockey_nhl/events/{event_id}/odds
    ?markets=player_shots_on_goal,player_shots_on_goal_alternate
    &bookmakers=draftkings&oddsFormat=american&dateFormat=iso&apiKey=[REDACTED]
```
Exactly these three, official, documented v4 endpoints — no scraping of DraftKings or
the-odds-api.com's own website anywhere in this slice.

## C. API credit/request behavior observed

Real response headers (`x-requests-used` / `x-requests-remaining`) were read on every call rather
than assumed. Across all 4 genuine calls made this slice (1 `/sports`, 2 `/events`, 1 `/odds`),
the observed headers were identically `used=2, remaining=498` — consistent with the provider
reporting a monthly-cycle snapshot rather than a strictly per-call increment; this project does
not assume a specific per-call cost and instead surfaces whatever the provider's real headers say
on each call (`research/live_sog_pricing/client.py::ApiResult`). Total genuine live calls made
during this entire slice: **4** (3 during Phase A's own smoke test, 1 during the one production
`refresh()` run) — deliberately minimal, per the credit-consciousness instructions.

## D. NHL events retrieved

**32 real events**, real team names, real `commence_time` values, real opaque provider `id`s.
Soonest: Florida Panthers @ Carolina Hurricanes, `2026-09-29T21:10:00Z`. Archived unchanged at
`data/raw/the_odds_api/live/20260827T185349Z_sports-icehockey_nhl-events_na_none.json`.

## E. NHL event-mapping results

`research/live_sog_pricing/event_mapping.py` maps a provider event (home/away team name +
commence_time) to this project's own `game_id` via a real 32-team abbreviation table plus a
±6-hour (or same-calendar-date) commence-time match against a supplied schedule — returns
`MATCHED` / `AMBIGUOUS` / `UNMATCHED`, never a silent fuzzy match (9 tests in
`tests/test_live_sog_pricing.py::TestEventMapping`/`TestPlayerMapping` cover ambiguous/unmatched
rejection explicitly). Not exercised against a real *current-season* schedule end-to-end this
slice, since `research/real_nhl_results/` is a completed-games archive and no live schedule
source with real 2026-27 `game_id`s was ingested (out of scope — the events retrieved are
themselves preseason/season-opener games over a month away, so a live schedule wasn't needed to
reach this slice's real finding). The mapping function itself is fully built and unit-tested
against any schedule-shaped input.

## F. DraftKings coverage

**Zero.** The one real event queried for odds (`GET .../events/{id}/odds`, DraftKings-filtered,
both SOG market keys requested) returned `"bookmakers": []` — confirmed empirically, not assumed.
Archived at `data/raw/the_odds_api/live/20260827T185349Z_..._player_shots_on_goal+player_shots_on_goal_alternate.json`.

## G. SOG standard-market payload contract

**Not observed live this slice** (no market was posted to inspect). `player_shots_on_goal` is
built against The Odds API's officially documented player-prop outcome shape — the SAME shape
the provider documents for every player-prop market across every sport it covers:
`outcomes[].name` = `"Over"`/`"Under"`, `.description` = player name, `.price` = American odds,
`.point` = the line. This is stated explicitly as **documented-contract-based, not
live-payload-verified**, in `research/live_sog_pricing/market_parser.py`'s own module docstring.
**Action item for a future slice**: re-run `research/run_live_sog_phase_a_smoke.py` closer to the
2026-27 season opener (or preseason, if DraftKings posts skater props for exhibition games) and
diff the real payload against this assumed shape before trusting it in production.

## H. SOG alternate-market payload contract

**Also not observed live.** `player_shots_on_goal_alternate`'s parser
(`market_parser.parse_alternate_market`) is deliberately **schema-tolerant**: it inspects each
outcome's actual `name` field and handles either a plausible Over/Under-at-multiple-lines shape
or a plausible `"N+"` milestone shape, raising `UnrecognizedOutcomeShapeError` loudly for
anything else rather than guessing (`tests/test_live_sog_pricing.py::TestMarketParsing`, 5 tests,
covers both hypothesized shapes plus the loud-failure case). Which shape DraftKings actually
uses for this specific market is an open question until a real payload exists.

## I. Bookmaker/market timestamp findings

The documented contract exposes `bookmakers[].last_update` (book-level) and
`markets[].last_update` (market-level) — both are used directly as
`bookmaker_last_update_utc` / `market_last_update_utc` in every normalized quote; this project
never invents a native DraftKings timestamp beyond what the provider actually returns (Part:
"do not invent a native DraftKings timestamp if only provider observation time exists"). Not
cross-checked against a real payload this slice for the same reason as G/H.

## J. Player mapping results

`research/live_sog_pricing/player_mapping.py`: normalized full name (accent-stripped,
suffix-stripped, hyphen-normalized) **plus team context** — never last name alone. Built and
tested against the real `research/player_sog` corpus's 1,330 real players, including the genuine
NHL duplicate-name case (two players named Sebastian Aho, Carolina/NYI) to prove team-context
disambiguation actually works and correctly falls back to `AMBIGUOUS` when team context doesn't
resolve it (`TestPlayerMapping`, 6 tests). A real live-payload mapping run wasn't exercised
end-to-end this slice for the same reason as E/F (zero live player names to map against).

## K. Raw payload archive details

4 real responses preserved unchanged under `data/raw/the_odds_api/live/`, each with a sidecar
`meta` block (`retrieved_at_utc`, `endpoint`, `event_id`, `bookmaker_filter`, `market_filter`,
SHA-256 checksum of the response body, the real `requests_used`/`requests_remaining` headers,
HTTP status) — **never the API key**
(`tests/test_live_sog_pricing.py::TestArchive`, 3 tests, including a byte-identity check). A
same-second filename-collision bug was found and fixed during this slice's own development (two
calls landing in the same wall-clock second silently overwrote each other's archive file); the
fix adds an endpoint-derived tag plus a numeric de-dupe suffix to the filename scheme.

## L. Normalized market schema

One flat dict per quote (`market_parser.parse_event_odds_response`'s output): `provider_event_id`,
`home_team`, `away_team`, `bookmaker`, `bookmaker_title`, `bookmaker_last_update_utc`,
`market_key`, `market_last_update_utc`, `player_name_raw`, `side`, `point` or
`milestone_threshold`, `price_american`, `shape`. Two-sided standard-market quotes are then
grouped by `(event, bookmaker, player, point, market_last_update_utc)` — Over and Under are only
ever paired if they came from the exact same returned market snapshot (Part: "market coherence").

## M. No-vig formula

Reused **unchanged** from `pricing/odds_math.py::no_vig_two_way` (already real, tested,
production code — not reimplemented): `raw_a = american_to_prob(price_a)`,
`raw_b = american_to_prob(price_b)`, `no_vig_a = raw_a / (raw_a + raw_b)`. Used only when a
coherent two-sided market exists (Section L); one-sided/milestone markets are explicitly labeled
`no_vig_available: False`, `market_no_vig_probability: null` rather than approximated
(`TestPricingMath::test_one_sided_market_has_no_vig_unavailable`).

## N. Fair-price formula

Reused unchanged from `pricing/odds_math.py::prob_to_american` — the same real production
formula used for the game-level moneyline model, verified >50%/<50%/=50% behavior already
covered by that module's own existing tests (untouched this slice) plus this slice's own
`TestPricingMath::test_fair_price_matches_odds_math`.

## O. Conservative fair-price formula

Identical formula (Section N), applied to the SOG model's **conservative** probability
(`research/player_sog/count_models.py::conservative_mu`-derived threshold probability) instead of
the raw probability — never a separately-invented "conservative odds" calculation.

## P. EV formula

Reused unchanged from `pricing/odds_math.py::expected_value`:
`EV = p * decimal_odds - 1` at the **actual offered DraftKings price** — computed for both raw
and conservative model probability, kept as two clearly distinct numbers from "edge" (probability
points), directly per the prompt's own explicit warning against confusing the two
(`TestPricingMath::test_edge_is_probability_points_not_ev_percent`).

## Q. Conservative EV formula

Same formula (Section P), using the conservative probability — `conservative_ev` is the field
actually used by the BET/WATCH/WAIT/PASS gate (Section S), never the raw EV.

## R. Max-acceptable-price formula

Reused unchanged from `pricing/odds_math.py::max_acceptable_price` — the CURRENT, already-fixed
production formula (its own docstring documents a prior v2.1.1a bug fix; this slice reuses that
corrected version verbatim, per the explicit "do not reuse the previously fixed incorrect
formula" instruction — there was nothing to re-fix, only to reuse correctly). Solves for the
worst American price on the target side that still clears `config.MIN_CONSERVATIVE_EDGE` against
the CURRENT opposing price, consistent with the two-sided no-vig methodology (Section M). Returns
`None` for one-sided markets or when no finite price could ever satisfy the edge requirement
(`TestMaxAcceptablePrice`, 2 tests).

## S. Decision-state logic

`research/live_sog_pricing/pricing.py::decide()`. Base action from edge/EV alone
(`config.MIN_CONSERVATIVE_EDGE` = 3.0pp, `config.MIN_EV` = 2.0% — the SAME real production
thresholds the moneyline engine uses, reused rather than re-tuned, per the instruction to
document SOG-specific thresholds rather than optimize them against a market that doesn't
currently exist):

| Base condition | Action |
|---|---|
| conservative edge >= 3.0pp AND conservative EV >= 2.0% | BET |
| positive raw edge, but conservative edge/EV don't clear | WATCH |
| no positive raw edge | PASS |

Then a **confidence/data-quality gate** (Section T) can downgrade BET or WATCH to WAIT; PASS is
never touched (there is nothing to wait on if there was no edge). `zone` (GREEN/LIGHT
GREEN/YELLOW/RED) reuses `pricing/engine.py::_zone`'s exact bucketing, unchanged.

## T. Confidence handling

Confidence (already computed by the validated SOG model, `HIGH`/`MEDIUM`/`LOW`) **never alters a
probability number** — it only caps how strong an action the same numbers may produce.
`LOW` confidence downgrades a would-be `BET` or `WATCH` to `WAIT`, always
(`TestConfidenceGating`, 4 tests, including the exact "same numbers, different confidence"
regression case the prompt specifically asked for). Lineup status is always
`PROJECTED/UNCONFIRMED` this slice (no live confirmation source exists) — structurally guaranteed
never to say `CONFIRMED` anywhere in this slice's code
(`TestLineupStatusHonesty::test_refresh_never_produces_a_confirmed_lineup_status`).

## U. Quote-staleness policy

Reused unchanged from `pricing/odds_math.py::dynamic_max_staleness_minutes` +
`config.ODDS_STALENESS_TIERS` — the same time-to-puck-drop-sensitive policy the moneyline engine
uses (60 min allowed >6h out, tightening to 1 min inside 10 min of puck drop). A quote older than
the policy window returns `status: "DATA_UNAVAILABLE"` with the specific reason
(`TestStaleness`, 3 tests).

## V. Live SOG board implementation

`dashboard/pages/8_Live_SOG_Markets.py` + `dashboard/live_sog_pricing_view.py`. Reads ONLY the
cached `research/live_sog_board_cache.json` snapshot — **never makes a network call itself**
(`TestDashboardNoNetworkOnRerun`, 3 tests: no import of the client or refresh modules, no direct
`requests.*` calls, refresh never imported by any dashboard file). Shows refresh metadata
(timestamp, events seen, credits observed), and — honestly, given Section F's real finding — an
explicit "no markets currently posted, this is expected" message rather than a fabricated or
placeholder board. Verified live via Streamlit (port 8766): renders the empty-board state
correctly with the real 32-events/0-near-term/0-priced summary from the one real `refresh()` run.

## W. Player drilldown implementation

Same page, below the board: reuses `research/player_sog/live_projection.py::project_player_sog`
(the SAME function backing the existing Player SOG Research page — moved out of
`dashboard/player_sog_view.py` into the research layer this slice, fixing a real dependency-
direction issue: research code must never import from `dashboard/`). Shows expected/conservative
SOG, the full validated `P(1+)`..`P(6+)` distribution, and explicitly labels **TOI/role** and
**head-to-head (shrunk)** as `MODEL DRIVER` (validated, per `PLAYER_SOG_FOUNDATION_REPORT.md`
Sections W/Z) versus **recent form** and **opponent shot environment** as `CONTEXT ONLY` (tested,
found not to add credible incremental value, Sections V/Y of that same report) — never
mislabeling a rejected feature family as a production driver, per this slice's own explicit
instruction. Note: both context-only features remain present as (near-zero-weighted) terms in the
underlying fitted GLM rather than being physically removed from the architecture — the UI label
reflects their validated *predictive value*, not their literal presence in the formula.

## X. Observation-ledger implementation

`research/live_sog_pricing/observation_ledger.py` — append-only JSONL, isolated from `nhl.db`
entirely, explicitly labeled (in its own module docstring, checked by
`TestObservationLedger::test_ledger_never_labeled_as_a_bet_ledger`) as **NOT a bet ledger**.
Deterministic, content-derived `observation_id` (not random) makes re-running the same real
observation at the same real timestamp idempotent — `append_observation` returns `False` and
does not duplicate the row (`TestObservationLedger`, 5 tests). **Currently empty** (0 rows) —
correctly so, since the one real `refresh()` run found zero live markets to observe (Section F);
storing a synthetic row to demonstrate the format was explicitly avoided, per "do not fabricate a
response."

## Y. Failure/cache behavior

Every client call returns a plain `ApiResult(ok=False, error=...)` rather than raising —
covered for network errors, HTTP 401/429, and malformed JSON
(`TestApiFailureHandling`, 4 tests). `refresh()` itself never raises past its caller; on any API
failure it leaves the existing board cache untouched rather than overwriting it with a
fake/empty result silently. The dashboard shows the real `api_error` field from the last refresh
if one occurred, and always shows the cache's own `refreshed_at_utc` so a stale cache is visibly
labeled as such rather than presented as fresh.

## Z. API credit burden per refresh

The one real production `refresh()` run: **1 request** (`/events`, free per the observed headers)
because zero real events fell inside the 3-day near-term window this slice's credit-conscious
design deliberately checks before ever calling the per-event odds endpoint (Section AA of
`refresh.py`'s own docstring: querying a 32-day-out event for odds would spend credits on an
already-known-empty result). Once real events fall within that window (i.e., once the 2026-27
season approaches), a full refresh would cost 1 free events call + up to `max_events` (default
12) real per-event odds calls — a hard, configurable cap specifically to bound spend.

## AA. Files created/modified

**Created (research, isolated):**
- `research/live_sog_pricing/env_config.py`, `client.py`, `archive.py`, `market_parser.py`,
  `event_mapping.py`, `player_mapping.py`, `pricing.py`, `observation_ledger.py`, `refresh.py`
- `research/player_sog/live_projection.py` (moved out of `dashboard/player_sog_view.py`)
- `research/run_live_sog_phase_a_smoke.py`
- `research/live_sog_board_cache.json`, `research/live_sog_pricing/observation_ledger.jsonl` (empty)
- `data/raw/the_odds_api/live/*.json` (4 real archived responses)

**Created (dashboard, additive only):**
- `dashboard/live_sog_pricing_view.py`, `dashboard/pages/8_Live_SOG_Markets.py`

**Modified:**
- `dashboard/player_sog_view.py` (now re-exports `project_player_sog` from the research layer
  instead of defining it — a pure refactor, zero behavior change, verified by the pre-existing
  Player SOG Research page still rendering identically)
- `dashboard/app.py` (one new `st.Page` entry)
- `.gitignore` (`.env` added; raw live-odds JSON deliberately left tracked, see Section K)

**Created (secrets, gitignored, never committed):** `.env` (contains `THE_ODDS_API_KEY`)

**Created (tests):** `tests/test_live_sog_pricing.py` (62 tests)

**Untouched, verified:** `models/`, `config.py`, `db.py`, `nhl.db`, `pricing/engine.py`,
`pricing/decision.py`, `schema.sql`, every file from every prior slice.

## AB. Full new test result

```
Ran 675 tests in 13.188s
OK
```
**675 total / 675 passed / 0 failed / 0 errors / 0 skipped.** 613 (confirmed unchanged baseline)
+ 62 new tests in `tests/test_live_sog_pricing.py`. No existing test was weakened, skipped, or
removed. (One real bug was found and fixed *during* this slice's own test-writing: an initial
test attempted to check "the real API key never appears in source" by embedding the real key's
literal value inside the test file itself — which would have committed the secret it was meant
to guard against. Rewritten as an AST-based check for any 32-char-hex-looking string constant,
with no real secret value anywhere in the test source.)

## AC. Confirmation production SOG probability model unchanged

`research/player_sog/count_models.py` and `research/run_player_sog_model.py` were not modified
this slice (only read from, to load the already-fitted headline-model weights). Verified directly:
`TestProductionModelUnchanged::test_sog_model_fitting_never_reads_odds_or_price_terms` confirms
neither file references `draftkings`, `the_odds_api`, or any market-price term — the SOG model
was never refit using sportsbook prices, and the market is never used as a predictive feature,
only as pricing/evaluation information (Part 36).

## AD. Confirmation production NHL win-probability model unchanged

`git status --short models/ config.py db.py pricing/ schema.sql nhl.db` shows zero modified
entries. `nhl.db`'s mtime (`Aug 26 16:15`) predates this slice entirely.
`TestProductionModelUnchanged::test_no_forbidden_imports` AST-scans every new file for
`pricing.engine`, `pricing.decision`, `models.combined_model` imports — none found (only the pure,
side-effect-free `pricing.odds_math` functions are reused, as intended).
`TestNoAutoBettingOrCredentials` (1 test, 10 forbidden-token patterns: `place_bet`, `login(`,
`password`, `credential`, `session_token`, `checkout`, etc.) confirms no auto-betting or
DraftKings-account-automation code exists anywhere in this slice.

## AE. Recommended next single development slice

**Do not build a second live-pricing feature yet.** Instead: **re-run
`research/run_live_sog_phase_a_smoke.py` in mid-to-late September 2026**, once real events fall
inside the near-term posting window, to (1) capture a genuine non-empty `player_shots_on_goal`
payload and confirm Section G's documented-contract assumption byte-for-byte, and (2) determine
`player_shots_on_goal_alternate`'s real outcome shape (Section H is currently an open question
between two plausible hypotheses). Only after that real-payload verification should a full
`refresh()` be run against genuine posted markets to produce the first real, non-empty
`LIVE_MODEL_VS_MARKET` board and the first real observation-ledger rows — which is also the
earliest point at which this project could begin the forward live-edge validation the ledger was
built to support (Part: "this becomes our genuine forward validation dataset").

---

## Final questions

- Is The Odds API connected successfully? **YES.**
- Are real NHL events being retrieved? **YES** (32 real events).
- Are real DraftKings SOG markets being retrieved? **CURRENTLY NOT POSTED** — confirmed
  empirically (empty `bookmakers` array for the soonest real event), not assumed.
- Is `player_shots_on_goal` supported? **YES** — the sport/market key combination is accepted by
  the API and returns a well-formed (empty) response; a non-empty payload has not yet been
  observed.
- Is `player_shots_on_goal_alternate` supported? **NOT OBSERVED** — same request accepted, same
  empty result; outcome semantics remain a documented-contract assumption pending a real payload.
- Are the odds real? **N/A this slice** — no odds were returned to evaluate; every number shown
  anywhere in this report or the dashboard is either a real API response field or a clearly
  labeled test fixture, never a fabricated price.
- Are the odds timestamped? **PARTIAL** — the documented contract exposes both
  `bookmaker_last_update_utc` and `market_last_update_utc`, both wired into the normalizer, but
  neither has been cross-checked against a real non-empty payload yet.
- Are events reliably mapped to NHL game IDs? **YES**, for the mapping logic itself (9 tests,
  MATCHED/AMBIGUOUS/UNMATCHED all covered) — not yet exercised end-to-end against a real current-
  season schedule, since none was needed to reach this slice's real finding.
- Are players reliably mapped? **YES**, including the real NHL duplicate-name case (two Sebastian
  Ahos), never matching on last name alone.
- Can the engine price Over/Under SOG markets? **YES** — the full math pipeline (no-vig, fair
  price, conservative fair price, raw/conservative edge, raw/conservative EV, max acceptable
  price, decision) is built, unit-tested, and was verified end-to-end using a real player's real
  fitted model probability (William Nylander) against a constructed illustrative price.
- Can it price X+/alternate SOG markets? **PARTIAL** — the milestone-shaped parsing and one-sided
  pricing path (no-vig correctly unavailable) are built and tested, but the real outcome shape is
  unconfirmed (see `player_shots_on_goal_alternate` above).
- Is no-vig market probability calculated where possible? **YES**, reusing
  `pricing/odds_math.py::no_vig_two_way` unchanged, only when a coherent two-sided market exists.
- Is model fair price calculated? **YES.**
- Is conservative fair price calculated? **YES.**
- Is raw EV calculated? **YES.**
- Is conservative EV calculated? **YES**, and it — not raw EV — is what the BET/WATCH/WAIT/PASS
  gate actually uses.
- Does confidence affect decision eligibility? **YES** — LOW confidence downgrades a would-be BET
  or WATCH to WAIT, verified with the exact "same numbers, different confidence" test case.
- Are live market observations stored prospectively? **YES**, the mechanism is built and
  idempotent; the ledger currently holds 0 rows because 0 real opportunities existed to observe
  this slice (correct, honest behavior — not a bug).
- Does the dashboard show real model vs. market? **YES**, for the (currently empty, correctly so)
  real board; the underlying pipeline was verified end-to-end with real model output against an
  illustrative price.
- Does the system place bets automatically? **NO.**
- Was the SOG model refit using sportsbook prices? **NO.**
- Was the NHL win-probability model changed? **NO.**
- Current full test result: **675 / 675 passing (0 failed, 0 errors, 0 skipped).**
- Is the system ready to begin forward live SOG edge validation? **NO, not yet** — the pricing
  pipeline itself is ready and tested, but no real DraftKings SOG market currently exists to
  validate against; readiness resumes once real props are posted (see next slice).
- What should the next single development slice be? **Re-verify the real `player_shots_on_goal`
  / `player_shots_on_goal_alternate` payload contract in September 2026 once real markets post,
  then run the first genuine `refresh()` against real posted odds** (Section AE) — not a new
  feature.

---

### STOP AFTER LIVE SOG PRICING

Per the governing instructions for this slice, nothing further was done this turn:
- The parlay optimizer was **not** built.
- No bet was placed.
- No DraftKings account credentials were integrated.
- The SOG model was **not** refit using market prices.
- No new player-prop family was started.
