# NHL Moneyline Model — Free-Data Development Roadmap

Research/planning only, per your "STOP AFTER THE ROADMAP" instruction. No code changes, no new tests.

## Part 1 — What v2.1.2a's model actually uses today

Read directly from `models/elo_model.py`, `models/player_model.py`, `models/goalie_model.py`, `models/combined_model.py`, and `config.py`.

### Real implemented signals (used in production `compute_probability_from_features`, real-data-capable)

- **Team Elo rating** from real game win/loss only (`models/elo_model.py`) — base-expectation update rule, K=20, updates on the *base* Elo expectation (deliberately not the fully player/goalie/rest-adjusted probability, to avoid double-counting).
- **Home-ice advantage** — fixed +35 Elo points, applied to every team identically.
- **Season regression** — 30% of the distance back to 1500 reverted at each season boundary.
- **Player quality** — a real per-player exponentially-weighted moving average of *points per game* (goals+assists), from real ingested `player_game_stats`, shrunk toward a league-average prior by games-played sample size, summed across the point-in-time "available roster" (`pit.available_roster()` — organizational roster minus anyone whose latest observed status isn't ACTIVE) excluding goalies.
- **Goalie adjustment** — real per-goalie save percentage (from real ingested `goalie_game_stats`: saves/shots-against), shrunk toward a league baseline by start-count sample size, converted to an Elo delta; confirmed-vs-unconfirmed starter status widens the uncertainty band.
- **Rest/schedule-density adjustment** — back-to-back / three-in-four / four-in-six flags, computed from real ingested schedule history (`pit.rest_context()`), each subtracting a fixed Elo penalty.
- **Uncertainty band** — narrows as both teams accumulate games within the current season; widens for an unconfirmed goalie. Explicitly documented in the code itself as a *heuristic*, not a statistically validated confidence interval.

### Placeholders / heuristics (explicitly flagged in the code itself, not my characterization)

- `goalie_model.league_save_pct = 0.905` — hardcoded seed guess, code comment: "not re-estimated live."
- `player_model.league_avg = 0.35` PPG — hardcoded seed, never re-estimated from real data.
- `config.POINTS_PER_GAME_TO_ELO = 20.0` — tuned once against *synthetic* backtest data only; code comment explicitly says to re-tune against real data.
- `config.SAVE_PCT_TO_ELO = 2500.0` — no evidence of any real-data tuning.
- `BACK_TO_BACK_PENALTY` / `THREE_IN_FOUR_PENALTY` / `FOUR_IN_SIX_PENALTY` — config comment literally says "placeholders pending that test" (i.e., whether fatigue matters at all has never actually been tested).
- `ci_low`/`ci_high` — explicitly labeled in the docstring as a non-statistical heuristic band, kept under those field names only for schema/API compatibility.

### Planned but not implemented

- **RAPM/GAR/xGAR player valuation** — explicitly disclaimed in `player_model.py`'s own docstring as *not* what's built; current player signal is points-per-game only, no teammate/opponent/zone-start context.
- **Expected goals / shot quality / shot location** — `goalie_model.py`'s docstring explicitly says it's "simplified to save% since the demo data doesn't carry shot-danger location."
- **PP/PK special-teams modeling** — absent entirely from the model.
- **Lineup combinations / line chemistry** — `lineup_snapshots` table and `pit.lineup_for_game()` query exist as *infrastructure*, but nothing populates them for real games and nothing in the model reads them yet.
- **Score-adjusted / game-state-adjusted stats** — absent.
- **Team strength beyond Elo** (e.g. a possession/xG-based rating) — absent; Elo is purely win/loss.
- **Coaching/system-change tracking** — absent.
- **Real injury/scratch data** — the point-in-time *architecture* (`pit.available_roster()`, `roster_status_events` table, `record_roster_status()` write path) is fully built and tested, but **no real data source is wired to populate it** for actual NHL games — confirmed again this pass: no public NHL injury API exists.
- **Real starting-goalie confirmation data** — same story: `pit.goalie_status()` / `goalie_status_events` / `record_goalie_status()` exist and are tested, but nothing feeds them for real games (no public NHL API for this either).

This last pair matters a lot for the roadmap below: the *mechanism* for injury/goalie-confirmation-aware pricing already exists and is production-tested — what's missing is only the real-world feed, and that feed has a specific, structural limitation (see Areas 4/5/6 below).

## Part 2 — Fourteen areas evaluated

For each: **A** what it represents, **B** why it should help, **C** raw data needed, **D** source, **E** historical depth, **F** point-in-time reconstructable?, **G** leakage risk, **H** complexity, **I** predictive value, **J** now/later/not yet.

### 1. Team strength beyond Elo (shot-quality/xG-based rating)
A: A second team-strength signal built from shot quality/xG differential rather than only win/loss, blended with or supplementing Elo.
B: Win/loss Elo carries one bit of information per game and is slow/noisy; xG-based possession metrics are well-documented in public hockey analytics as more predictive of *future* results than raw outcomes, especially early in a season.
C: Per-game team-level shot attempts and/or xG values, ideally with score-state splits.
D: **MoneyPuck.com** (free CSV downloads, real per-shot xG with 124 attributes/shot) is the strongest fit; Natural Stat Trick (free, web-filter/manual export, no API) as a cross-check; the NHL Web API's own boxscore has raw `sog` only, no shot quality/location.
E: MoneyPuck: 2007-08 season through present, nightly updates in-season (~1.84M historical shots).
F: **Yes** — MoneyPuck's shot data is genuinely archived per-game historically, so a team's xG state is reconstructable as of any date using only games that had already happened, the same discipline the existing Elo/player model already use.
G: Low if built correctly (only ever aggregate games known before `prediction_time_utc`, same pattern as `learn()`); real risk if a bulk CSV import isn't given its own honest `observed_at_utc` per game.
H: MEDIUM — new ingestion adapter + a team-rating model, but follows the existing Elo-update pattern closely.
I: **HIGH** — the strongest-supported "beyond Elo" improvement in the public literature.
J: **NOW** (top candidate, see Part 3).

### 2. Expected-goals/shot-quality performance (feature-level, e.g. rolling xGF%/xGA%)
A: The specific feature construction on top of #1's data — rolling xG-for%/xG-against%, and shooting%/save% "regression to luck" adjustments.
B: Separates sustainable team quality from shooting/save percentage variance ("PDO") that raw win/loss can't distinguish.
C/D/E: Same as #1 (MoneyPuck).
F: Same as #1 — yes.
G: Same as #1 — low if disciplined.
H: MEDIUM — largely bundled with #1's ingestion, not a separate pipeline.
I: HIGH, but only meaningful once #1's ingestion exists — not independently valuable.
J: **NOW**, bundled with #1.

### 3. Special teams — PP/PK
A: Team power-play and penalty-kill performance as a distinct strength differential.
B: A real, distinct driver of goal differential not captured by an overall win/loss Elo; a team with an elite PP and weak PK is systematically mispriced by a single aggregate rating.
C: Per-game PP goals/opportunities and PK goals-against/times-shorthanded.
D: **Partially confirmed** in the real NHL boxscore we've already validated this session (per-skater `powerPlayGoals` field is present and real); team-level PP-opportunities/PK-times-shorthanded fields are **not yet confirmed** in a real payload — needs direct verification. MoneyPuck's team CSVs are the more likely practical source (need to confirm per-game vs. season-cumulative-only granularity).
E: MoneyPuck: 2007-08+ if confirmed at per-game grain.
F: Likely yes via MoneyPuck (same archival property as #1) — unverified until checked directly.
G: Low if built the same way as #1.
H: MEDIUM.
I: MEDIUM — real, but usually a smaller marginal signal once 5v5 team strength/xG (#1) is already modeled.
J: **LATER** — natural second phase once #1's ingestion infrastructure exists.

### 4. Starting-goalie quality and workload
A: Two distinct things bundled in your prompt — (a) goalie quality, **already implemented** (save% with shrinkage); (b) goalie-specific *workload* (starts in short succession), **not implemented** — today's rest adjustment is team-level only, never goalie-specific.
B: A goalie starting their 3rd game in 4 nights performs measurably worse; the team-level rest adjustment doesn't capture this at all.
C: Goalie-specific start dates — **already derivable from data already ingested** (`goalie_game_stats.started=1` rows), zero new external data required.
D: NHL Web API — already ingested, real, validated this session.
E: Unlimited — as far back as boxscores are backfilled.
F: **Yes, trivially** — pure computation over data already flowing through the point-in-time-safe pipeline.
G: Very low — same pattern as the existing `rest_context()`.
H: **LOW**.
I: MEDIUM.
J: **NOW** — arguably the single lowest-friction item on this entire list (see Part 3, recommended next slice).

*Separately:* confirmed pregame starting-goalie **identity** for real games hits the same no-official-API wall as Area 5 below (the architecture — `goalie_status_events`/`pit.goalie_status()` — exists and is tested, but nothing feeds it for real games).

### 5. Player availability / injuries
A: Feed `roster_status_events` from a real source so `available_roster()` reflects real scratches/injuries for actual games (currently empty for real data — only test/demo fixtures populate it).
B: A team missing its top line changes true win probability meaningfully; the consuming infrastructure (`player_model.team_available_quality_elo()`) already computes exactly this correctly *if* availability data existed.
C: Daily injury/scratch status per player per day.
D: **No official NHL API** (confirmed again this session). Free unofficial sources exist (Hockey-Reference's injury page, CBS/Rotowire/Covers injury pages) but none offer a formal API — all would require scraping, and none of their ToS were reviewed in this pass.
E: **Effectively zero backfilled depth** — every source found shows *current* state only.
F: **NO for the past** — genuinely not reconstructable historically from anything found. **Only prospectively**, from whichever day real ingestion starts, and only if we snapshot daily rather than query on demand.
G: HIGH if handled carelessly (a "current status" page is easy to mistake for a historical fact); LOW if we only ever capture forward with honest `observed_at_utc` timestamps, matching every other real-time-only source already in this codebase (e.g. live odds).
H: MEDIUM (scraper + ToS review + a daily snapshot job).
I: MEDIUM-HIGH long-run, **near-zero near-term** — there's nothing to backtest against until real forward-captured history accumulates for months.
J: **LATER** — worth starting the forward-capture clock reasonably soon, but it cannot power any near-term backtest, so it is not the next slice.

### 6. Lineup strength (full dressed lineup, not just injury flags)
A: Distinguishing "star player scratched for rest" from "4th-liner healthy scratch," and confirming the actual dressed 12F/6D vs. the full healthy roster.
B: `available_roster()` already approximates this reasonably well once #5's data exists; the incremental lift specific to full-lineup confirmation is real but smaller than #5 itself.
C: Confirmed pregame dressed lineup.
D: Same access gap as #5 (line-combination sites, no official API).
E/F: Same forward-only-capture limitation as #5.
G: Same as #5.
H: MEDIUM-HIGH (parsing line combinations is a harder extraction problem than a scratch list).
I: LOW-MEDIUM incremental over #5 alone.
J: **NOT YET** — subsumed by #5; revisit only after #5 is built and shown to have value.

### 7. Rest / back-to-back / travel / schedule density
A: Rest/back-to-back/game-density is **already implemented** (`pit.rest_context()`, real schedule-derived). Geographic *travel* (distance/time-zones crossed) is **not** implemented — no notion of venue geography exists today.
B: Real travel (e.g. a West-to-East back-to-back crossing three time zones) is a documented additional fatigue factor beyond simple rest-days.
C: Team home-arena latitude/longitude (a small ~32-row static table, public knowledge, no external API needed) plus each game's actual venue (already ingested via schedule).
D: Schedule/venue: already ingested, real. Lat/long table: static public data, not a live provider.
E: Full depth, no limit.
F: Yes, trivially — same pattern as `rest_context()`.
G: Very low.
H: **LOW**.
I: LOW-MEDIUM — published research on travel/jet-lag effects in the NHL is mixed/modest once back-to-back status is already controlled for, which this engine already does.
J: **LATER** — cheap, but the smallest expected marginal lift of the low-complexity candidates.

### 8. Home/away effects
A: **Already implemented** as one fixed league-wide constant (+35 Elo, identical for all 32 teams). Improvement = team-specific home advantage (altitude, notoriously loud arenas, etc.).
B: Home-ice advantage is documented to vary meaningfully by team/venue; one global constant is a known oversimplification.
C: Home/away results already ingested — pure re-modeling, no new data.
D: NHL Web API, already ingested.
E: Full existing depth.
F: Yes, trivially.
G: Very low.
H: **LOW**.
I: LOW — likely a small, second-order refinement once base team-strength signal quality is solid.
J: **LATER**.

### 9. Recent-form weighting vs. long-term team strength
A: A short-window (e.g. last-10-games) form signal blended with season-long Elo, instead of one Elo K-factor applied uniformly all season.
B: Team performance isn't stationary within a season (injuries, trades, coaching changes, streaks); a single K-factor can't tell "genuinely improved" from "one lucky week."
C: Same real game results already ingested — no new external data, purely a modeling change.
D: NHL Web API, already ingested.
E: Full existing depth.
F: Yes, trivially — same discipline as the existing Elo update, reading only `pit.completed_games_known_before()`.
G: Low, if done the same way `learn()` already is.
H: **LOW-MEDIUM**.
I: MEDIUM.
J: **NOW** (see Part 3 — a genuine zero-new-data candidate, ranked alongside #4).

### 10. Score-adjusted / game-state-adjusted performance
A: Adjusting shot/xG metrics for score effects (trailing teams shoot more, leading teams defend more — a well-documented confound in raw possession stats).
B: Without this, a possession metric built for #1/#2 would be biased by how often a team happened to be leading/trailing, not just true quality.
C: Score state at time of each shot event.
D: MoneyPuck's shot-level data already includes score-state fields — no separate source needed.
E/F: Same as #1.
G: Low, same discipline as #1.
H: MEDIUM — a refinement layered on top of #1's ingestion, not a separate pipeline.
I: MEDIUM — improves #1's signal quality but has no independent value without #1 existing first.
J: **LATER**, bundled into #1's build.

### 11. Player-level impact models (RAPM/GAR/xGAR-style)
A: A genuine on-ice-impact player valuation model (regression-based, adjusting for teammates/opponents/zone starts) replacing today's points-per-game EWMA.
B: Today's `player_model.py` is an explicit placeholder — a real impact model would meaningfully improve availability-driven line movements (e.g. losing a strong 2-way defenseman who doesn't score is currently invisible to the model).
C: Play-by-play or shift-level on-ice data across at least a full season, ideally several, to fit a stable regression.
D: The name-brand version of this (Evolving-Hockey's RAPM/GAR/xGAR) is a **paid** subscription product. Could be rebuilt from the NHL's own real play-by-play (`/v1/gamecenter/{id}/play-by-play`, already a validated real domain) plus MoneyPuck's shift data, but that's a genuine multi-week statistical research project, not a data-ingestion task.
E: Multi-season if built from scratch.
F: Architecturally yes (same discipline), but only once the model itself exists — the single highest-complexity item on this whole list.
G: Low for point-in-time *if* built correctly, but a real risk of subtle leakage in any regression fit across a season at once — needs its own walk-forward discipline beyond simple point-in-time SQL.
H: **HIGH**.
I: HIGH long-run, poor near-term ROI given the current model's much cheaper, more obvious weaknesses (unfit constants, zero xG signal).
J: **NOT YET** — revisit after #1/#2/#4/#9 are built and their value is actually measured.

### 12. Line combinations / chemistry
A: Modeling specific forward-line/defense-pair combinations' on-ice performance together, not just summed individual ratings.
B: Real chemistry effects exist, but this is a genuinely advanced, small-sample-per-combination problem.
C: Same confirmed dressed lines as #6, ideally with on-ice performance by combination.
D: Same access gap as #6, compounded by #11's gap (no free source for on-ice-combination performance).
E/F: Same forward-only-capture limitation as #6.
G: **HIGH** — smallest sample size of anything on this list, easiest to overfit.
H: **HIGH**.
I: LOW near-term (data-starved), MEDIUM-HIGH only after years of real forward-captured data.
J: **NOT YET** — lowest near-term priority on the whole list.

### 13. Coaching/system changes
A: Detecting and adjusting for a mid-season coaching change or system shift.
B: A real, documented effect, but genuinely hard to operationalize as a clean numeric feature.
C: Coaching-change dates plus enough post-change games to distinguish a real shift from noise.
D: Change **dates** are free/public (news, Wikipedia transactions pages); "measurable system change" itself isn't a data feed at all — it's really just a special case of #9 (recent-form re-weighting) triggered at a known changepoint.
E: Full depth for the dates themselves; effect size is only estimable after the fact.
F: The date is a real, point-in-time-knowable fact; the *effect* is inherently backward-looking and can't be a leak-free predictive feature beyond "reset/widen uncertainty here," similar to the existing season-boundary regression.
G: MEDIUM — easy to let hindsight ("this change helped") leak into what's supposed to be a pregame-only feature.
H: MEDIUM.
I: LOW — coaching changes are rare (a handful of teams/season), so achievable sample size for a dedicated feature is small; better captured as a targeted trigger on #9's existing mechanism than as its own feature family.
J: **NOT YET** — fold into #9 if #9 is ever built.

### 14. Season-to-season priors and early-season uncertainty
A: Instead of a flat 30% regression-to-mean for every team equally, a smarter preseason prior — blending last season's ending Elo with a roster-turnover adjustment (a team that lost its top scorer shouldn't keep last season's full rating).
B: Early-season predictions today either regress crudely or rely on the existing maturity-based uncertainty widening — a turnover-aware prior would sharpen exactly the highest-uncertainty window (October) where the model is currently weakest.
C: Off-season transactions (signings/trades/departures) plus each departing/arriving player's prior-season rating.
D: NHL Web API's current-roster sync is already real and partially built; full transaction history would need either the NHL API's own transactions feed (existence unconfirmed — needs checking) or a free public source like Wikipedia's NHL transactions pages (confirmed to exist).
E: Full historical depth for transactions.
F: Yes — offseason transactions are dated, real, point-in-time-knowable facts by construction (known before the season starts).
G: Low if built carefully (never look ahead to see how the season actually turned out).
H: MEDIUM.
I: MEDIUM — sharpens the model specifically during its currently-weakest few weeks per season.
J: **LATER** — a good candidate once player-level ratings are trustworthy enough to seed a turnover-aware prior; not urgent given the existing maturity-based band already partially covers this gap.

## Part 3 — Top 5, scored

Scored 0–10 on **PREDICTIVE VALUE (PV)**, **DATA QUALITY (DQ)**, **POINT-IN-TIME FEASIBILITY (PIT)**, **IMPLEMENTATION EFFICIENCY (IE)** — my judgment, shown so you can re-weight it yourself. Overall = simple average of the four.

| Rank | Candidate | PV | DQ | PIT | IE | Overall |
|---|---|---|---|---|---|---|
| 1 | Team shot-quality/xG strength signal (MoneyPuck, Areas 1+2) | 9 | 8 | 8 | 6 | **7.75** |
| 2 | Goalie-specific rest/workload adjustment (Area 4b) | 5 | 10 | 10 | 9 | **8.5** |
| 3 | Recent-form dynamic re-weighting of Elo (Area 9) | 6 | 10 | 9 | 7 | **8.0** |
| 4 | Special-teams PP/PK strength (Area 3) | 6 | 6 | 7 | 6 | **6.25** |
| 5 | Season-to-season roster-turnover-aware prior (Area 14) | 6 | 7 | 7 | 5 | **6.25** |

**Note the tension, deliberately surfaced rather than hidden:** by raw predictive value alone, the MoneyPuck xG signal (#1) is the clear leader. But your own decision framework also weights data quality, point-in-time feasibility, and complexity — and #2/#3 score higher *overall* than #1 precisely because they require **zero new external data source**: they're pure feature-engineering over data this engine already ingests and has already validated as real. That's not a small advantage — it means no new provider, no new ToS review, no new adapter, and an audit surface limited to a single point-in-time query plus a config constant, exactly the kind of narrow slice this project has consistently preferred. I'm ranking the list by predictive value (since that's what you asked to rank), but recommending a different slice to build *first*, and explaining why below.

## Part 4 — Recommended next slice

```
NEXT SLICE:
Goalie-specific rest/workload adjustment

WHY THIS FIRST:
Zero new external data source and therefore zero new provider-access or
ToS risk — the entire feature is a computation over goalie_game_stats
rows this engine already ingests for real games (started=1 per goalie).
It has the smallest possible audit surface of anything on the roadmap:
one new point-in-time query (mirroring the existing rest_context()
pattern exactly) plus one new small Elo-adjustment term. It's testable
and gradeable in complete isolation before any bigger, new-data-source
project (MoneyPuck/xG) is started — exactly your own stated criterion
that each slice be "testable before adding the next feature family."

DATA SOURCE:
None new. 100% derived from goalie_game_stats, already flowing through
the validated real NHL ingestion pipeline (v2.1.2a).

WHAT WOULD BE BUILT:
A goalie-specific rest feature — days since that specific goalie's own
last start, and a same-goalie-back-to-back-start flag — computed
point-in-time (reads only starts observed strictly before
prediction_time_utc, same discipline as pit.rest_context()). This feeds
a small additional adjustment to the existing goalie Elo delta (or an
extra shrinkage term) when a goalie is starting on unusually short rest,
with a new config constant (not yet fit) gating its size.

WHAT WOULD NOT BE BUILT:
No MoneyPuck/xG integration. No PP/PK. No lineup/injury ingestion. No
player-impact-model work. No coaching-change detection. No threshold or
Kelly-sizing changes. No odds-provider work of any kind.

ACCEPTANCE CRITERIA:
- New tests proving the feature reads only starts observed strictly
  before prediction_time_utc (point-in-time safety, mirroring
  tests/test_temporal_invariants.py's existing pattern).
- A test distinguishing a goalie's own back-to-back start from a team's
  back-to-back game (a backup can start while the team itself is fresh,
  and vice versa).
- The full existing test suite (322 tests) still passes, plus the new
  ones.
- A backtest.py-style calibration comparison (Brier score / log loss)
  showing this feature doesn't calibrate WORSE than the current model —
  matching this project's own established precedent for
  POINTS_PER_GAME_TO_ELO (which was itself lowered from 55 to 20 after a
  backtest showed the higher value hurt calibration). The new constant
  should go through the same scrutiny before being trusted.
```

The MoneyPuck xG signal (#1 in the ranked list) remains the clear long-run priority given raw predictive value, and is the natural *second* slice once this one is built, tested, and its calibration effect measured in isolation.

## Required final answers

```
IS v2.1.2a CORE INGESTION ACCEPTED?
YES

CURRENT VERIFIED TEST COUNT?
322

IS PAID HISTORICAL ODDS WORK ACTIVE?
NO

CAN WE CONTINUE IMPROVING THE MONEYLINE MODEL WITHOUT PAID ODDS?
YES

WHAT IS THE SINGLE BEST NEXT MODEL-DEVELOPMENT SLICE?
Goalie-specific rest/workload adjustment (Area 4b) — zero new data
source, smallest audit surface, testable in isolation. See Part 4.
```

Stopping here as instructed — no code changes, no new tests, nothing implemented.

## Sources (this pass)

- [MoneyPuck.com – Download Data](https://moneypuck.com/data.htm)
- [4 Free Data Sources for Your Hockey Analytics Projects — DataPunk Hockey](https://www.datapunkhockey.com/free-data-sources/)
- [Zmalski/NHL-API-Reference (unofficial api-web.nhle.com endpoint reference)](https://github.com/Zmalski/NHL-API-Reference)
- [NHL EDGE launches website for puck and player tracking data — NHL.com](https://www.nhl.com/news/nhl-edge-launches-website-for-puck-and-player-tracking-data)
- [NHL Injury Report | Hockey-Reference.com](https://www.hockey-reference.com/friv/injuries.cgi)
- [Daily Faceoff — Line Combinations, NHL News](https://www.dailyfaceoff.com/)
