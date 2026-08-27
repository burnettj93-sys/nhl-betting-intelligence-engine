# Phase 1 — Real Historical Odds / Economic Backtesting Foundation

Research + data-contract design only, per your explicit "STOP AFTER PHASE 1"
instruction. **No implementation has been done in this phase** — see the one
noted exception below.

**Scope note on Section 4 (`closing_draftkings_snapshot()` fix):** your
message both said "Fix it now" (Section 4) and, in the closing block, "Do NOT
implement the entire phase immediately... Then STOP." I read the closing
instruction as the governing one for this turn and have **not** made that
code change yet. I confirmed the bug is real by reading the current source
(details below) so it's ready to fix the moment you say go — it's a small,
fully-scoped, already-tested-in-spec change, independent of the provider
research. Flagging this explicitly rather than guessing which instruction you
meant to win.

---

## A. Historical odds provider comparison

| Provider | NHL coverage | DraftKings confirmed | History depth | Granularity | Two-sided coherence | Access / pricing | API vs flat-file |
|---|---|---|---|---|---|---|---|
| **The Odds API** | Yes, retroactive to late 2020 for moneyline/spread/total | Yes, explicitly listed | From 2020-06-06 | 10-min intervals to Sept 2022, 5-min since | Not via an explicit snapshot ID, but each historical response is itself one point-in-time poll of the whole board — home/away naturally share one `timestamp` | Self-serve, publicly documented tiers ($30–$249/mo paid; free tier excludes historical); historical requests cost **10× normal credits** | REST API |
| **OpticOdds** | Hockey listed among 25+ sports; NHL not explicitly named in docs I could reach | Yes, DraftKings named repeatedly in examples | Full price-change history via `/fixtures/odds/historical` (every price change/lock/unlock/settlement with timestamps) | Event-driven (actual change events, not fixed polling interval) — technically the strongest of the group | **Yes** — explicit `grouping_key` links both sides of a market within one fixture response | **No public pricing** — enterprise/sales-contact only | REST API |
| **SportsGameOdds** | Not confirmed NHL-specific in what I read | Not confirmed DK-specific | Closing odds included; intraday depth unclear from public docs | Unclear | Unclear | Public tiers, $99–$499/mo, object-based billing; 80+ books incl. Pinnacle | REST API |
| **OddsJam** | Yes, NHL explicitly listed | **Not confirmed** — "100+ sportsbooks," DK not named on the page I read | Claims a "Full Historical Odds Database" (opening/closing/live line changes) | Not documented | Not documented | No public pricing — "contact us" | REST API |
| **sportsbookreviewsonline.com (SBR) archive** | 2007-08 through 2022-23 seasons only | **Unconfirmed / likely not** — legacy SBR panels historically tracked a small fixed set of books (Pinnacle, 5Dimes, Bookmaker, etc.), not DraftKings specifically, and the page gives no field spec | Long but **frozen** — page states archive "will not be updated," so nothing current | **Closing/opening lines only, not time-series** (unconfirmed from the page itself, but this matches the well-known structure of this archive) | No | Free | Static file download |
| **Odds Warehouse (oddswarehouse.com)** | Yes, "NHL historical odds databases" sold | Not confirmed DK-specific; DK is only referenced under a *separate* DFS-stats product, not the odds database | Multi-season, one-time purchase | **Closing-line only**, no line-movement mentioned | No | $39–$199 one-time, CSV | Static file download, no API |

*(Comparison built from provider marketing/docs pages fetched during this research pass — see Sources. None of these were confirmed against an actual paid historical pull; see Section N.)*

## B. Recommended provider and why

**The Odds API**, as the primary candidate to trial next, with **OpticOdds** noted as a stronger technical fallback if The Odds API's real historical payload turns out to be too coarse or its timestamp semantics turn out to be unusable.

Why The Odds API over the alternatives:
- It's the only candidate with **fully public, self-serve, documented pricing** — no sales call needed to find out if this is affordable before committing engineering time.
- NHL and DraftKings coverage are both explicitly confirmed in its own docs, not just implied.
- 5-minute granularity since September 2022 covers essentially every decision horizon this phase needs (T-24h down through T-5m) with acceptable staleness at the tightest horizons; the coarser 10-minute granularity for 2020–2022 is a real but bounded limitation for older seasons.
- Its snapshot model (one HTTP response = the full board at that instant) naturally gives us **coherent two-sided quotes** without needing a dedicated provider snapshot-ID field, *provided* both sides are actually present in the same response for a given bookmaker at a given historical timestamp — a claim I have not yet verified against a real payload (see Section D and Section N).

Why not the others as the *first* pick:
- **OpticOdds** looks architecturally the best fit (explicit `grouping_key`, genuine event-level change history rather than fixed-interval polling), but "contact sales, no listed price" is a real adoption risk for a solo research project — worth revisiting if The Odds API's real data disappoints.
- **OddsJam** and **SportsGameOdds** are both plausible but under-documented on the two things that matter most here (confirmed DraftKings presence, real timestamp semantics) — I'm not comfortable recommending either as primary without a sales conversation to fill those gaps.
- **SBR archive** and **Odds Warehouse** are useful only as closing-line-only, non-DraftKings-confirmed reference data — not suitable for the decision-time snapshot extraction this phase actually needs (Section 9), and SBR's archive is explicitly frozen at 2022-23. I'd keep these in mind only as a possible free cross-check on closing lines for older seasons, never as the primary source.

## C. Exact data fields available (The Odds API, per its own docs)

Per-outcome, within a historical snapshot response:
- `name` (team/selection)
- `price` (in the requested odds format, e.g. American)
- `description` (prop markets only — not relevant to moneyline)
- `point` (spread/total markets only — not relevant to moneyline)
- Market-level `last_update` (**not** per-outcome, per-bookmaker — the docs are explicit that this granularity is market-level only)

Response-level: `timestamp`, `previous_timestamp`, `next_timestamp` (lets you page through the snapshot history for an event).

**Fields our data contract (your Section 2) asks for that I could NOT confirm are present:** an explicit `market status` (ACTIVE/SUSPENDED) field, a provider-side immutable record identifier per quote, and an explicit snapshot/market-group ID. None of these appear in the documentation pages I was able to read. This needs to be checked against a real API response, not assumed either way.

## D. Both-sides coherent snapshot identifiers?

- **The Odds API:** no explicit snapshot/group ID field documented. Coherence, if it holds, comes structurally from the response shape (one historical query returns the entire board — all bookmakers, all outcomes — as it stood at the returned `timestamp`). This needs empirical verification: I have not seen a real historical payload to confirm both a moneyline's home and away outcomes are always present together in one response for a given bookmaker and timestamp, rather than potentially missing one side if that book hadn't posted it yet.
- **OpticOdds:** explicit `grouping_key` field is documented as linking related selections (e.g., an over/under pair) — this is the strongest documented answer to your Section 5 preference, but sits behind the pricing/access barrier noted above.
- **All others:** not documented in what I could read.

## E. Available historical depth

- The Odds API: 2020-06-06 onward, all sports/markets it covers; NHL specifically confirmed retroactive to "late 2020."
- OpticOdds: not time-bounded in the docs I read (frames it as "full price history").
- SBR archive: 2007-08 through 2022-23 (frozen, no newer data).
- Odds Warehouse: multi-season, exact start year not confirmed from the page.

## F. Timestamp semantics

This is the weakest-documented area across every candidate, and it directly matters for your Section 7 "two clocks" requirement.

- **The Odds API:** the docs state snapshots are "taken at" fixed intervals (10-min, then 5-min) — language that reads as **the provider's own polling/capture cadence**, not a sportsbook-published quote-change timestamp. I could not find explicit confirmation either way. My working assumption, to be verified: the returned `timestamp` should be treated as `received_at_utc`-equivalent (provider capture time) rather than a true independent `captured_at_utc` (sportsbook's own quote time) — meaning, absent further evidence, `captured_at_utc` and `received_at_utc` would effectively collapse to the same value for this provider, which is honest but weaker than genuinely independent book-side vs. archive-side timestamps.
- **OpticOdds:** its historical endpoint's own description ("every price change... with timestamps") reads as closer to genuine book-side change events, which would give real two-clock separation — again unverified against an actual response.
- No candidate's public documentation discusses a market `status` (ACTIVE/SUSPENDED) field explicitly, which the existing engine schema already tracks and depends on (`odds_snapshots.status`).

**This is the single most important thing to verify with a real trial pull before committing to a provider** — it directly determines whether Section 7's "preserve two clocks... do not fabricate a historical received_at" discipline can be honestly satisfied, or whether we'd have to document that this provider only gives us one clock.

## G. Proposed normalized odds schema additions

The current `odds_snapshots` table (schema.sql, unchanged since v2.1) already has: `game_id, sportsbook, data_provider, market, selection, event_start_utc, line, price_american, status, captured_at_utc, received_at_utc, snapshot_label`, with a unique index on `(sportsbook, game_id, market, selection, captured_at_utc)`.

Proposed additions (additive only, per your instruction not to destructively rewrite):
- `snapshot_group_id TEXT` — links the two (or more) sides of one coherent market poll. For The Odds API this would be derived at ingestion time from `(bookmaker, event, market, response_timestamp)`, since no native ID is exposed; for a provider that does expose one (OpticOdds' `grouping_key`), store it directly.
- `provider_record_id TEXT` — the provider's own immutable identifier for this quote row, if one exists, for audit/reconciliation. Nullable — not every provider will have one.
- A new **`provider_event_map`** table (Section H) rather than cramming provider event IDs into `odds_snapshots` directly, to keep the mapping auditable and queryable on its own.

I'd explicitly avoid inventing a "confidence" field on `odds_snapshots` itself — market-status semantics (`status`) already exist and should keep doing that job; a new field should only be added once we know what a real provider response actually contains.

## H. Event mapping design

New table, e.g. `provider_event_map`:
```
provider_event_map (
    id                  INTEGER PRIMARY KEY,
    data_provider       TEXT NOT NULL,      -- e.g. 'the-odds-api'
    provider_event_id   TEXT NOT NULL,      -- provider's own event/game identifier
    game_id             INTEGER,            -- our NHL game_id, NULL until matched
    home_team_at_match  TEXT,               -- provider's home-team label, as observed
    away_team_at_match  TEXT,
    event_start_utc_at_match TEXT,
    match_status        TEXT NOT NULL,      -- MATCHED / AMBIGUOUS / UNMATCHED
    match_method        TEXT,               -- e.g. 'team_abbrev+start_time_exact'
    matched_at_utc       TEXT,
    UNIQUE(data_provider, provider_event_id)
)
```
Matching would run as an explicit, auditable batch step (never inline during pricing): join on team identity (mapped through the same team-abbreviation normalization already used elsewhere) + a start-time tolerance window, producing exactly one of the three statuses. Anything not cleanly `MATCHED` is excluded from the economic backtest by construction — consistent with your "no cherry-picking, no silent fuzzy-matching" requirement. `AMBIGUOUS`/`UNMATCHED` counts would be reported, not hidden.

## I. Closing-line definition

Confirmed by reading `features/point_in_time.py` directly: `closing_draftkings_snapshot()` (lines 315–329) currently runs:

```sql
SELECT * FROM odds_snapshots
 WHERE game_id=? AND sportsbook=? AND market=? AND selection=?
   AND status='ACTIVE' AND price_american IS NOT NULL
 ORDER BY captured_at_utc DESC, id DESC LIMIT 1
```

This is the bug you described — there is genuinely **no** `captured_at_utc < event_start_utc` predicate anywhere in this query. It will happily return a live/post-puck-drop quote if one was ingested. This confirms Section 4's premise exactly; the fix (adding the pre-start predicate, plus a `received_at_utc <=` boundary parameter where a historical-knowledge cutoff applies, plus the 18:59/19:00/19:05 test cases you specified) is fully scoped and ready to implement, but per the scope note at the top I have not made this change yet.

## J. CLV formula / sign convention (proposed, not yet implemented)

Given a bet on `selection` at decision time `T`:
- `bet_no_vig_prob` = the no-vig implied probability for `selection`, computed from the two-sided quote available at `T` (using the engine's existing `pricing/odds_math.py::no_vig_two_way`, already in production use).
- `closing_no_vig_prob` = the same computation applied to `closing_draftkings_snapshot()`'s two-sided quote (once fixed).
- **`CLV = closing_no_vig_prob - bet_no_vig_prob`** for the selection actually bet.

Sign convention: **positive CLV means the market's implied probability of your selection winning ROSE by close relative to what you got** — i.e., you locked in a price when the market thought your side was less likely to win than it ultimately priced it by close, which is the standard "beat the closing line" definition. This is deliberately probability-space, not raw American-odds subtraction (which is non-linear and misleading to average across favorite/underdog prices) — matching your Section 10 requirement. Reported in probability points; log-odds/price-movement as an optional secondary view.

## K. Decision-time horizons

As specified: T-24h, T-6h, T-2h, T-60m, T-30m, T-15m, T-10m, T-5m, and CLOSE. Each resolved via the existing `latest_draftkings_two_sided()` pattern (already production code, already correctly enforcing the two-clock discipline) called at `event_start_utc - horizon`, with `DATA_UNAVAILABLE` (never interpolation, never a guessed price) whenever no eligible quote exists — this matches existing behavior of `latest_draftkings_snapshot()` exactly, so no new "missing data" policy needs to be invented, only applied at each horizon.

## L. Economic backtest output schema (proposed)

A new table, e.g. `economic_backtest_results`, one row per (game, selection, decision_horizon):
```
game_id, selection, market, decision_horizon_label, prediction_time_utc,
model_true_probability, model_conservative_probability,
market_no_vig_probability, raw_edge, conservative_edge, expected_value,
action, price_taken, actual_result, actual_return_units,
closing_no_vig_probability, clv_probability_points,
model_version, feature_version, pricing_logic_version, decision_logic_version,
odds_snapshot_id_selection, odds_snapshot_id_opponent, closing_odds_snapshot_id
```
plus the aggregate metrics report (Section 12's full list) computed from a query/report script over this table, segmented as you specified (horizon, favorite/underdog, probability bucket, edge bucket, odds bucket, season, home/away, goalie status).

## M. Exact files proposed to modify/create (none touched yet)

**Modify:**
- `features/point_in_time.py` — fix `closing_draftkings_snapshot()` (Section 4/I), no other change.
- `schema.sql` — additive: `snapshot_group_id`/`provider_record_id` on `odds_snapshots`; new `provider_event_map` table; new `economic_backtest_results` table; additive version columns on `predictions` (`pricing_logic_version`, `decision_logic_version`, `config_version`).
- `config.py` — add version constants alongside the existing `MODEL_VERSION`/`FEATURE_VERSION`.

**Create:**
- `ingest/odds_provider.py` — the `HistoricalOddsProvider` interface (`fetch_events`, `fetch_market_history`, `normalize_snapshot`) — this file is currently only referenced in a docstring elsewhere in the codebase and doesn't exist yet.
- `ingest/providers/the_odds_api.py` (or whichever provider is chosen after trial) — provider-specific adapter behind that interface.
- `ingest/event_mapping.py` — the MATCHED/AMBIGUOUS/UNMATCHED game-mapping step (Section 8).
- `pricing/clv.py` — CLV calculation (Section 10).
- `backtest_economic.py` — the economic backtest (Section 11), separate from the existing synthetic-data `backtest.py` rather than overloading it.
- Ten new test files, one per named category in your Section 17 (`tests/test_closing_line_pre_start_integrity.py` through `tests/test_decision_versioning_replay_input_integrity.py`), matching this repo's existing one-concern-per-file convention.

## N. Risks / limitations

- **Nothing here has been verified against a real paid historical pull.** Every claim above comes from provider marketing/docs pages, not from an actual API response — timestamp semantics (F) and market-status field presence (C) in particular need direct verification before any schema/ingestion code is finalized around them.
- **Cost is unconfirmed.** The Odds API's historical endpoint costs 10× normal credits; a realistic multi-season NHL DraftKings pull could be non-trivial. This needs a concrete quote from a small trial pull before committing to a season-scale ingestion.
- **This sandbox's network cannot reach arbitrary external domains** — the same restriction that blocked `api-web.nhle.com` almost certainly applies to any odds provider's API host too. Historical odds ingestion will likely need the same browser-replay-style bridge (or a properly networked environment) that the NHL ingestion work required, which affects how Phase 2 implementation would actually be exercised/tested here.
- **Licensing/ToS**: none of these providers' terms of service were reviewed in this pass. Before storing a permanent internal archive built from a provider's historical pulls, their ToS on data retention/redistribution should be read directly — I did not do this.
- **OpticOdds is the technically strongest fit but has no public price** — a real adoption risk if The Odds API's actual data quality disappoints and OpticOdds becomes the fallback.
- **SBR/Odds Warehouse-style archives are not confirmed DraftKings-specific and are closing-line-only** — useful at most as a free secondary sanity check on older-season closing lines, never as the primary decision-time data source.

## O. Go / No-Go

```
CAN WE OBTAIN SUITABLE HISTORICAL DRAFTKINGS NHL DATA?
UNKNOWN — The Odds API looks like a credible, accessible path (DraftKings
and NHL both confirmed in its own docs, transparent self-serve pricing,
5-minute granularity since Sept 2022), but this has NOT been verified
against a real paid historical response. Leaning toward YES, not confirmed.

IS THE CURRENT ODDS SCHEMA SUFFICIENT WITHOUT ADDITIONS?
NO — no event-mapping table exists at all, no snapshot/group-coherence
field exists, and the predictions table lacks pricing/decision/config
version columns needed for full decision reproducibility.

CAN WE CONSTRUCT GENUINE POINT-IN-TIME HISTORICAL MARKET STATES?
UNKNOWN — the engine's existing two-clock/status/pre-start discipline is
architecturally the right foundation and would generalize cleanly, but
whether any real provider's actual data has the granularity, market-status
field, and genuine two-clock timestamp separation this requires is
unverified.

ARE WE READY TO RUN A REAL ECONOMIC BACKTEST?
NO — no real odds data ingested yet, the closing-line bug is still
unfixed, the provider adapter/event-mapping/CLV/backtest code don't exist
yet. This is Phase 2+ work.
```

---

Stopping here per your instruction. Nothing beyond research and this report was implemented — the `closing_draftkings_snapshot()` fix from Section 4 is scoped and ready but intentionally not applied, pending your confirmation given the "STOP AFTER PHASE 1" framing (see the scope note at the top).

## Sources

- [Historical Sports Odds Data API | The Odds API](https://the-odds-api.com/historical-odds-data/)
- [NHL Odds API | The Odds API](https://the-odds-api.com/sports-odds-data/nhl-odds.html)
- [Odds API Documentation V4 | The Odds API](https://the-odds-api.com/liveapi/guides/v4/)
- [DraftKings API & Real-Time Odds | OpticOdds](https://opticodds.com/sportsbooks/draftkings-api)
- [Odds API: Getting Started Guide | OpticOdds](https://developer.opticodds.com/docs/odds-api-getting-started-guide)
- [Sports Betting Odds API Feeds | OddsJam](https://oddsjam.com/odds-api)
- [Odds API Pricing 2026: From Free to $499/mo (4 Providers Compared) | OddsPapi Blog](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/)
- [🏒 Historical NHL Scores and Odds Archives | sportsbookreviewsonline.com](https://www.sportsbookreviewsonline.com/scoresoddsarchives/nhl/nhloddsarchives.htm)
- [Odds Warehouse](https://www.oddswarehouse.com/)
