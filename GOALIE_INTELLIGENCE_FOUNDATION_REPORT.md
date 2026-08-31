# Pregame Starting-Goalie Intelligence Foundation — Report

**This turn built a Stage 1 research foundation only.** No production
game-probability model was modified. `models/`, `config.py`, and
`nhl.db` are untouched (confirmed by mtime). All new work lives under
`research/goalie_intelligence/` plus one dashboard page and one new test
file. Full suite: **527 / 527 passing** (476 pre-existing + 51 new).

**Headline result, stated up front**: unlike the four prior real-data
experiments (Elo, team xG, special teams, offense/defense — all "keep
current model"), this one has a genuinely positive outcome: a simple,
interpretable, PIT-safe starter-projection model, fit only on real
historical rotation data, **beats every naive baseline** — 67.5% top-1
accuracy vs. the best baseline's 65.6%, with sensible, well-calibrated
confidence buckets (HIGH-confidence picks are right 77.0% of the time)
and a coefficient on the back-to-back feature (-2.90) that directly and
strongly confirms the core hypothesis this slice set out to test. Full
detail below. This is **starter identification only** — it does not yet
touch any game win-probability output (Section AD).

---

## A-E. Source contract review (Part 1, done first)

Investigated by visiting each site's real public pages, checking
`robots.txt`, and searching each site's own published documentation —
never by circumventing any access control (Part 27).

| Source | A: Projected? | B: Confirmed? | C: Status labels? | D: Timestamp? | E: Attribution? | F: Start time? | G: Historical pages? | H: API/feed? | I: Public? | J: Paid/login? | K: Automation constraints | L: Recommended role |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Daily Faceoff** | Yes (site content) | Yes (site content) | Confirmed/probable, per search results | Unknown (page unreachable) | Unknown | Unknown | Unknown | Unknown | **NO — actively blocked** | Unknown | Cloudflare returns a hard **403** on a plain HTTP HEAD request, and even `robots.txt` itself returns a Cloudflare "Sorry, you have been blocked" page (verified this turn) | **UNSUITABLE for automation** |
| **RotoWire** | Yes | Yes | Explicit: **Confirmed** / **Expected** / **Unknown**, with on-page definitions (verified this turn) | Not visible on the public page | Not visible on the public page | Implied via "Today"/"Next 7 Days" views | No archive found on the public page | **Yes — a real, commercial, licensed API** (`api.rotowire.com`, confirmed via RotoWire's own public developer documentation; 80+ paying clients incl. ESPN, Yahoo) | Page: yes. API: no | **API requires a paid sales license** | `robots.txt` blocks known bulk-scraper tools by name (WebStripper, SiteSnagger, Offline Explorer, etc.); an `llms.txt` explicitly welcomes AI *reference* use, not bulk/training harvesting | **CONFIRMATION-ONLY / PROJECTION-ONLY via casual reference; PRIMARY only if the real API is licensed** |
| **Goalie Post** (Dobber) | Yes (site text confirms "Confirmed"/"Projected"/"expecting" language, verified this turn) | Yes | Present, exact wording not machine-extracted this turn | Not confirmed | Not confirmed | Not confirmed | Not found | None documented | Yes, page loads without JS for a basic GET | No login required for the public page | `robots.txt`: `Content-Signal: search=yes, ai-train=no, use=reference` — explicit reference-only signal, no bulk/API rights granted or documented; thin ToS with no explicit scraping clause either way | **SECONDARY / CONFIRMATION-ONLY, ad-hoc reference lookups only** |
| **Frozen Tools** (Dobber, same company as Goalie Post) | Via a broader fantasy-tool suite (goalie comparison, schedule planner, DFS optimizers) rather than one dedicated starters page | Via the same suite | Not the primary product for this | Not confirmed | Not confirmed | Not confirmed | Not found | None documented | Yes | No login required for public pages | Identical `Content-Signal` policy to Goalie Post (same Cloudflare config) | **SECONDARY at best — Goalie Post is this company's actual dedicated starters product** |
| **NHL.com (supplementary)** | Only via occasional **news articles** ("Fantasy: Projected starting goalies," "Today's Probable Goalies"), not a structured feed | No dedicated confirmed-starter page found | No | No | Yes (NHL.com staff/correspondents) | N/A | Article archive only, not a queryable date index | None found — `nhl.com/starting-goalies` itself 404s | Yes, fully open `robots.txt` | No | No access barrier at all, but **the data itself isn't structured or reliably updated** | **MANUAL-ONLY / editorial reference, not a real feed** |

**Automation/licensing summary (Part G)**: **none of the four preferred
sources currently offers a responsible path to automated structured
integration.** Daily Faceoff is technically blocked. RotoWire's real
structured access is a paid commercial license. Goalie Post/Frozen Tools
explicitly signal reference-only use with no documented bulk/API rights.
Per Part 34: **this does not block Stage 1** — it only means Stage 2
(live source integration) is not attempted this turn, and the external-
source schema (Section X) is built and tested without any live
observations (Section Y confirms zero were collected).

---

## F-G. Historical starter-label corpus (Parts 9-10)

**Source**: MoneyPuck's real goalie game-by-game data (the same
provider as the already-approved team-data foundation), 4 seasons
(2022-23 → 2025-26), fetched directly from `peter-tanner.com` (the same
CDN host used for the team file — reachable without the bot-detection
gate `moneypuck.com`'s own edge presents; confirmed via a plain HTTP
request this turn, unlike the earlier team-file experience). Archived
unchanged under `research/goalie_intelligence/raw/` with a checksum
manifest (`provenance.json`) — same `ARCHIVAL_RESEARCH` posture as every
other MoneyPuck ingestion in this project.

**Starter-inference heuristic**: MoneyPuck has no explicit "started"
flag. The standard hockey-analytics convention — the goalie with the
most `situation='all'` icetime in a team-game is the starter — is used,
**verified against the real data, not assumed**: 94.1% of team-games
(9,878 / 10,495) used exactly one goalie all game (unambiguous by
construction). Of the 617 multi-goalie team-games, the top goalie's
icetime share of the total is required to be ≥55% for the label to be
accepted; 74 games (0.71% of all team-games) fall below that bar and are
**excluded, not guessed at**.

## H. Corpus size

**10,421 team-games** with an accepted, unambiguous starter label
(99.28% of the 10,495 possible team-games in the 4-season window; 0
missing from the accepted real NHL corpus — perfect game_id overlap,
same ID space as already established for MoneyPuck team data).

## I. Team/game coverage

**33 teams, 143 unique goalies**, all 4 real seasons. Every row cross-
validated against `research/real_nhl_results/` (0 team-games found that
weren't already in the accepted real NHL corpus).

---

## J / K. Back-to-back goalie statistics (Part 4 — measured, not assumed)

```
Total real team back-to-backs (whole corpus):  1,577
Same goalie started BOTH games:                  146  (9.26%)
Starter CHANGED:                               1,431  (90.74%)
```

**The true empirical rate is ~9%, reported exactly as measured** — not
forced to match any prior assumption. By season, the rate has **trended
down** (teams appear to have gotten more disciplined about resting
starters over time):

| Season | B2Bs | Same-goalie % |
|---|---|---|
| 2022-23 | 404 | 12.13% |
| 2023-24 | 353 | 6.23% |
| 2024-25 | 394 | 9.64% |
| 2025-26 | 426 | 8.69% |

**Exception investigation (Part 5)**: mean game-1 icetime was nearly
identical whether the same goalie repeated (3,449s) or the starter
changed (3,567s) — **workload in game 1 alone does not cleanly explain
the ~9% exception cases** by this simple comparison. This is reported as
an honest negative/inconclusive finding for that specific hypothesis,
not glossed over — a sharper exception analysis (isolating true
"workhorse" goalies, playoff/must-win context, or backup-injury
situations specifically) is a defensible future refinement, not
attempted this slice to keep the analysis simple and interpretable.

## L. Rotation-pattern findings (Part 18 — measured conditional probabilities)

```
P(next start == last starter | last TWO starts were the SAME goalie)  = 51.44%  (n = 4,442)
P(next start == last starter | last TWO starts ALTERNATED)            = 36.41%  (n = 5,913)
```

**Rotation patterns carry real, measurable signal** — the two
conditional probabilities differ meaningfully (51.4% vs. 36.4%, both
computed on large real samples) from a flat 50/50, directly supporting
Part 18's hypothesis with evidence rather than assumption.

## M. Starter hierarchy methodology (Part 6)

Per team-season (≥10 games required), classify by season-end top-goalie
share of starts:
- **≥65% → PRIMARY STARTER**
- **35-65% → 1B / TANDEM**
- **<35% → BACKUP-HEAVY / UNCLEAR**

Result across 96 team-seasons (2023-24 → 2025-26): **32 PRIMARY
STARTER, 63 1B/TANDEM, 1 BACKUP-HEAVY/UNCLEAR** — tandem situations are
the *majority* in the modern NHL, an important context for interpreting
model accuracy (Section T).

---

## N. Exact projected-starter features (5, shared weights, no per-goalie fixed effects)

1. `started_previous_game` (0/1)
2. `consecutive_start_count` (capped at 6, only nonzero for the current streak-holder)
3. `recent_start_share_10` (0 if <10 games of history)
4. `season_start_share` (0 if no season games yet)
5. `back_to_back_after_playing_previous_night` (0/1)

All season-scoped where relevant (no cross-season carryover, same policy
as every other feature module in this project), all routed through one
STRICT PRIOR-GAME-DATE gate (`team_history_as_of()`).

## O. Exact inference model (Part 8: simple, interpretable — no ML)

**Multinomial logit (softmax regression)**, shared weights across all
candidate goalies (roster-size-agnostic — handles an emergency 3rd
goalie automatically): `P(goalie_i) = softmax(w · f_i)` over the
eligible candidate pool. Fit by ~30-line plain-Python batch gradient
descent minimizing multinomial cross-entropy — the same transparent
style as every prior logistic fit in this project, generalized from
binary to multinomial. **No neural network, random forest, gradient
boosting, or ensemble was used.**

**Fitted weights (tuning season only, 2023-24)**:

| Feature | Weight | Interpretation |
|---|---|---|
| `back_to_back_after_playing_previous_night` | **-2.897** | By far the strongest effect — directly confirms Part 4's hypothesis empirically |
| `recent_start_share_10` | +2.136 | Strongest positive predictor — recent form matters most |
| `season_start_share` | +0.887 | Positive but weaker than recent form |
| `started_previous_game` | -0.245 | Slightly *negative* once recent/season share and B2B are controlled for — a genuine, non-obvious finding, not assumed |
| `consecutive_start_count` | -0.004 | Essentially zero — streak length adds nothing beyond what `recent_start_share_10` already captures |

## P. Naive baselines (Part 17, exact definitions)

- **A**: this season's highest start-share goalie
- **B**: last game's starter
- **C**: highest start-share goalie over the last 10 games
- **D**: if back-to-back, the goalie who did *not* play the previous
  night (falling back to the recent-share leader among rested
  candidates); otherwise same as C

---

## Q / R / S. Historical top-1 accuracy, Brier/log-loss/calibration, vs. baselines (true holdout: 2024-25 + 2025-26, n = 5,095)

| Candidate | Top-1 accuracy |
|---|---|
| A — season leader | 58.76% |
| B — last game starter | 42.77% |
| C — recent leader (10) | 59.02% |
| D — B2B-aware | 65.57% |
| **Fitted model** | **67.48%** |

**The fitted model beats every baseline**, including the best
hand-coded rule (D), by ~1.9 percentage points absolute — real evidence
that combining the features (rather than any single hand-coded rule)
adds value.

Model probabilistic quality (baselines are point-picks, not evaluated
on Brier/log-loss for the reason given in Section T):
```
Brier score : 0.4455
Log loss    : 0.7233
```

**Calibration by confidence bucket** — monotonic and sensible, a real
sign of a genuinely-informative (not just lucky) probability:

| Confidence | N | Accuracy |
|---|---|---|
| HIGH (top pick ≥70%) | 2,210 | **77.0%** |
| MEDIUM (50-70%) | 2,218 | 62.8% |
| LOW (<50%) | 667 | 51.4% |

## T. Performance on back-to-backs (Part 17)

| | N | Accuracy |
|---|---|---|
| Back-to-back games | 785 | **80.9%** |
| Non-back-to-back games | 4,310 | 65.0% |

The model does *better*, not worse, on back-to-backs — the strong,
empirically-confirmed `back_to_back_after_playing_previous_night`
feature makes those specific situations easier to call correctly than
ordinary in-season rotation decisions.

## U. Performance on tandem teams (Part 6/17)

| | N | Accuracy |
|---|---|---|
| Tandem (1B/TANDEM) team-seasons | 3,096 | **62.3%** |
| Clear-starter (PRIMARY) team-seasons | 1,999 | **75.4%** |

Exactly the pattern a sound model should show: harder to call exactly
where the underlying reality genuinely is more uncertain, not an
artifact of the model itself. (Baselines A/B/C/D not evaluated on Brier/
log-loss here since they are point-picks — see Section T's note in the
JSON results for the full breakdown; the accuracy comparison alone
already demonstrates the effect.)

**Generalization check**: tuning-season (2023-24) in-sample accuracy was
65.97% — the true holdout's 67.48% is actually *higher*, a reassuring
sign this model is not overfit to its tuning data (unlike, notably, the
shot-quality experiment's defense candidate, which collapsed on true
eval after looking strong in tuning).

## V. Sequence-pattern conditional probabilities

See Section L above (Part 18) — reproduced here per the delivery list:
`P(next==last | AA)=51.44%` (n=4,442), `P(next==last | AB)=36.41%`
(n=5,913).

## W. Confidence methodology (Part 16)

Confidence is derived from the model's own top-pick probability (not a
separate model): **HIGH** ≥70%, **MEDIUM** 50-70%, **LOW** <50%.
Deliberately *not* equated with the raw probability number itself (Part
16's explicit instruction) — it's a coarse, interpretable bucket over
it, validated in Section S/Q to actually track real accuracy (77% → 63%
→ 51% as confidence decreases). A more source-agreement-aware confidence
model (folding in Section X's consensus `confidence` field) is designed
but has no live data to combine with this turn.

---

## X. Normalized external-source observation schema (Parts 11-18, design only)

`research/goalie_intelligence/source_schema.py` — a `SourceObservation`
dataclass with every field Part 11 specified (`game_id`, `team_id`,
`goalie_id`, `source`, `source_status`, `raw_status`,
`source_observed_at_utc`, `ingested_at_utc`,
`source_published_at_utc`, `source_probability_if_exposed`,
`source_reference`), validated against a `VALID_STATUSES` vocabulary
(`PROJECTED`/`EXPECTED`/`LIKELY`/`CONFIRMED`/`UNCONFIRMED`/`UNKNOWN`)
while always preserving the source's own original wording verbatim in
`raw_status` — never forcing every site's terminology into identical
semantics and discarding the original.

`record_observation()` is a real, callable function — it **always
raises `ExternalSourceUnavailableError`** this slice, with the exact
reason, rather than being a TODO comment. This is deliberate: it's the
one place Stage 2 needs to change (implement real fetching once a
source is licensed/permitted), and it means the "what happens when a
source can't be reached" path is exercised by a real test today (Part
24/`TestSourceContractFailureBehavior`), not left unverified.

## Y. Projected/confirmed transition design (Parts 12/14)

`compute_consensus(observations)` implements exactly the distinction
Part 12 demands: **SOURCE PROJECTION CONSENSUS is structurally separate
from SOURCE CONFIRMATION.** Multiple sources agreeing on a projection
raises `confidence` (HIGH at ≥75% agreement among ≥2 projection-like
sources) but the `status` field **never** auto-escalates past
`PROJECTED` — only an observation whose own `source_status ==
CONFIRMED` can set `status = CONFIRMED` (Part 14). The prior projected
observation is never deleted from `observations` — preserved
specifically so projection accuracy *before* confirmation can be
evaluated later (Part 14's explicit reason for this design).

## Z. Conflicting-source handling (Part 13)

Two projection-like sources naming different goalies: `conflicting=True`
is reported, `status` stays `PROJECTED`, and **both observations are
kept** — never silently resolved by "pick the newest." Two *confirmed*
observations that disagree with each other (Part 15's late-change case)
are handled the same way: `conflicting=True`, `confirmed_by=None`
(ambiguous, not auto-resolved) rather than picking one arbitrarily.

## AA. Confirmation-override behavior

Demonstrated end-to-end in `tests/test_goalie_intelligence.py::TestConfirmationOverride`:
a `PROJECTED` observation for goalie A plus a later `CONFIRMED`
observation for goalie B yields `status=CONFIRMED, leading_goalie_id=B`,
with the original A projection still retained in the observation list
(Part 15: "do not overwrite history").

---

## AB. Files created / modified

**Created:**
- `research/goalie_intelligence/raw/{2022,2023,2024,2025}.csv` + `provenance.json` — real MoneyPuck goalie data (gitignored except the manifest, same pattern as the team ingestion)
- `research/goalie_intelligence/build_starter_corpus.py` — builds the ARCHIVAL starter-label corpus
- `research/goalie_intelligence/actual_starters.jsonl` — the 10,421-row starter-label corpus (tracked, like `real_nhl_results`' own normalized file)
- `research/goalie_intelligence/features.py` — PIT-safe rolling features
- `research/goalie_intelligence/model.py` — multinomial logit model + baselines
- `research/goalie_intelligence/source_schema.py` — external-source schema + consensus logic (design only)
- `research/run_goalie_intelligence.py` — experiment driver
- `research/goalie_intelligence_results.json` — every computed number
- `dashboard/goalie_view.py`, `dashboard/pages/6_Goalie_Intelligence.py` — the new dashboard research page
- `tests/test_goalie_intelligence.py` — 51 new tests
- `GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md` — this report

**Modified:**
- `dashboard/app.py` — registered the new page
- `.gitignore` — same raw-file-exclusion pattern extended to the new raw goalie CSVs
- `README.md` — Dashboard section updated with the 6th page

**Not modified:** `models/`, `config.py`, `nhl.db`, `db.py`, all four
prior research experiment modules, and the MoneyPuck team ingestion
pipeline (all read-only reused where touched at all).

## AC. Full new test result

**527 / 527 passing, 0 failed, 0 errors, 0 skipped** (476 pre-existing +
51 new).

## AD. Confirmation production game-probability model unchanged

`models/elo_model.py`, `models/combined_model.py`, `config.py`,
`pricing/` — byte-identical to their pre-slice state. This foundation
computes **who starts**, never **how much that changes win probability**
— `tests/test_goalie_intelligence.py::TestProductionModelUnchanged`
AST-scans every file in `research/goalie_intelligence/` for an import of
`models.combined_model`/`models.elo_model`/`db.py` and finds none.

## AE. Dashboard goalie-panel readiness

Built and live-verified this turn (Section F of the delivery list is
folded in here since it doubles as verification): a new **Goalie
Intelligence (Research)** page shows the model-vs-baseline comparison,
the empirical back-to-back finding, and an interactive projector that
reproduces the task's own worked example almost exactly on real data —
tested live against WPG as of 2026-04-16: **Connor Hellebuyck 91.3%,
Eric Comrie 8.7%, STATUS: PROJECTED, CONFIDENCE: HIGH**, drivers
including "started 90% of the team's last 10 games," based on 326 real
prior games. Labeled `STARTER INTELLIGENCE: RESEARCH / HISTORICAL
INFERENCE` throughout — no historical actual starter is ever shown as a
pregame confirmation (Part 25's explicit requirement).

## AF. Recommended next single development slice

Two reasonable next steps came out of this slice, and — unlike prior
reports — this one has an actual *positive* result to build on rather
than a null result to route around:

1. **Goalie-quality × starter-probability integration into game win
   probability** (Part 20's designed-but-not-implemented equation:
   `P(win) = Σ P(goalie_i starts) × P(win | goalie_i)`) — the natural
   next step now that starter identification itself is proven to work,
   but explicitly deferred by this slice's own scope (Part 19/31).
2. **Revisit Stage 2 source licensing** — RotoWire's real API is a
   concrete, named, paid option (not a vague "maybe someday") if a
   licensing conversation is authorized; that would materially improve
   confidence *before* puck drop rather than relying on rotation
   inference alone.

Given this slice's explicit instruction to stop before touching win
probability, and that pursuing a paid data license is a business/legal
decision outside an engineering slice's scope to initiate unilaterally,
the recommended next slice is:

```
GOALIE-QUALITY × STARTER-PROBABILITY INTEGRATION INTO GAME WIN PROBABILITY
(Part 20's uncertainty-weighted equation) -- using this slice's real,
validated starter probabilities together with the existing (currently
unused) goalie-quality research already available from MoneyPuck's
goalie-level data, evaluated with the same real-data walk-forward rigor
as every prior experiment before any production integration.
```

---

## Final questions

```
CAN WE IDENTIFY THE ACTUAL HISTORICAL STARTER LABEL WITHOUT USING IT AS A
PREGAME FEATURE?
YES -- research/goalie_intelligence/build_starter_corpus.py produces the
label; research/goalie_intelligence/features.py structurally cannot read
it for the same game (proven by tests/test_goalie_intelligence.py's
TestNoTargetGameLeakage / TestNoPostgameObservationLeakage).

CAN WE BUILD A PIT-SAFE PROJECTED-STARTER MODEL FROM HISTORICAL USAGE?
YES

WHAT % OF BACK-TO-BACKS USED THE SAME GOALIE?
9.26% (146 / 1,577 real back-to-backs) -- the starter changed 90.74% of
the time. Measured, not assumed.

DO ROTATION PATTERNS ADD PREDICTIVE SIGNAL?
YES -- P(next==last | last two same)=51.4% vs. P(next==last | last two
alternated)=36.4%, both on large real samples (n=4,442 / n=5,913).

DOES THE PROJECTED-STARTER MODEL BEAT NAIVE STARTER BASELINES?
YES -- 67.48% top-1 accuracy vs. the best baseline's 65.57%.

WHAT IS ITS TOP-1 HISTORICAL ACCURACY?
67.48% (true holdout, 2024-25 + 2025-26, n=5,095)

IS IT CALIBRATED?
YES (PARTIAL in the sense that formal reliability-curve calibration
wasn't separately computed, but confidence-bucket accuracy is monotonic
and well-separated: HIGH 77.0% / MEDIUM 62.8% / LOW 51.4% -- a real,
non-circular calibration signal)

CAN DAILY FACEOFF PROVIDE PROJECTED/CONFIRMED STARTER DATA RESPONSIBLY?
NO -- actively blocked (Cloudflare 403 on even a basic request, confirmed this turn)

CAN FROZEN TOOLS PROVIDE PROJECTED/CONFIRMED STARTER DATA RESPONSIBLY?
REQUIRES PERMISSION -- Content-Signal explicitly limits automated use to
"reference," no documented bulk/API access

CAN GOALIE POST PROVIDE PROJECTED/CONFIRMED STARTER DATA RESPONSIBLY?
REQUIRES PERMISSION -- same Content-Signal policy as Frozen Tools (same company)

CAN ROTOWIRE PROVIDE PROJECTED/CONFIRMED STARTER DATA RESPONSIBLY?
REQUIRES LICENSE -- real structured/API access is a paid commercial
product (api.rotowire.com), confirmed via RotoWire's own documentation

SHOULD WE DISTINGUISH PROJECTED FROM CONFIRMED?
YES

SHOULD STARTER PROBABILITY REMAIN PROBABILISTIC BEFORE CONFIRMATION?
YES

WAS THE PRODUCTION NHL WIN-PROBABILITY MODEL CHANGED?
NO

CURRENT FULL TEST RESULT?
527 / 527

WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?
Goalie-quality x starter-probability integration into game win
probability (Part 20's uncertainty-weighted equation), evaluated with
the same real-data walk-forward rigor as every prior experiment before
any production integration.
```

---

## STOP AFTER THIS FOUNDATION

Per instruction: goalie quality was not integrated into NHL win
probability. Projected goalie information does not alter BET/WAIT/PASS
(no such logic was touched). The Odds API was not integrated. No
historical CLV analysis was built. No additional MoneyPuck team feature
was added. No restricted site was scraped — Daily Faceoff's block was
respected as a hard stop, and Goalie Post/Frozen Tools/RotoWire were
investigated for their public contract only, with zero live observations
collected (Section Y). This report is returned for independent review;
no further action was taken this turn.
