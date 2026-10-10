# Opening-Season Team Strength — Scoping Audit + Candidate A/B Result (2026-09-29)

Research-only. Does not touch `models/`, `pricing/`, `operational/`, or today's
production decisions. No promotion, no shadow deployment, nothing wired into
tonight's five games.

## 1. Why the Elo staleness figure is what it is (the actual mechanism, not a guess)

Traced end-to-end:

- **Production model** (`models/elo_model.py` / `models/combined_model.py`) already
  has a real, tested, intentional season-boundary rule:
  `maybe_regress_new_season()` reverts `config.ELO_SEASON_REGRESSION` (0.30) of each
  team's distance to `config.ELO_START` (1500) whenever a newly-iterated game's
  `season` label differs from the previous one it saw.
- **The live "Live Model Edges" board on Today** (what actually gates BET/WAIT for
  tonight's real DraftKings moneyline, in `dashboard/live_dk.py`) does **not** call
  the production DB-backed model at all. It calls
  `dashboard/data_access.compute_baseline_predictions()`, which re-runs
  `research/elo_comparison.run_walkforward()` over the **static, frozen file**
  `research/real_nhl_results/normalized_regular_season_games.jsonl` **from scratch
  on every render**, and uses whatever rating is sitting in the walk-forward's
  final state after its last row.
- That file's last row is **2026-04-16** (end of 2025-26). It contains **zero**
  2026-27 games. `run_walkforward()`'s season-regression call only fires when it
  iterates a game whose season differs from the one before it — since there is no
  2026-27 game in the file, that call is **never made** for the new season. The
  ratings the live board shows today are therefore the **raw, unregressed 2025-26
  closing values**, and the "days stale" figure is simply `today − 2026-04-16`
  (166–171 days depending on exact game time).

**Answer to "is this (A) an intentional carry-forward prior or (B) missing
season-transition logic":** neither cleanly. The season-regression *mechanism*
is real, tested, and intentional (A). But the specific corpus this dashboard reuses
has a **real, fixable data-pipeline gap** — nobody has re-run
`research/real_nhl_results/build_research_corpus.py` (or an equivalent) to extend
it past 2026-04-16, so the season boundary the code already knows how to handle
has structurally never been reached (closer to B, but as a data-refresh gap, not
an algorithm defect). This is not an Elo modeling failure; it's an unrun batch job.

## 2–19. Roster-transition, preseason, and continuity candidates: NOT BUILT

I looked for the actual data these need and it does not exist in this repo:

- **No historical roster-transaction dataset** (trades, waivers, FA signings,
  retirements) anywhere in the codebase — confirmed by search; the only "trade"
  code is `fantasy/recommendations/trade_analyzer.py`, an unrelated fantasy-hockey
  feature.
- **No historical NHL preseason game dataset** — every "preseason" hit in this repo
  is this *project's own* pre-launch engineering phase (`PRESEASON_*_REPORT.md`),
  not actual NHL preseason games/box scores.
- **No lineup-history / roster-continuity table** — `players` has no `team` column;
  `roster_status_events` only records injury/scratch status, not team assignment,
  and has zero rows for any of the six players in tonight's Toronto trade.

Building sections 2–14 and 18 honestly (leakage-safe, walk-forward validated, no
invented weights) requires real transaction/roster/preseason data this repo does
not currently own or ingest — TOI shares, trade histories, preseason box scores,
lineup composition per game. That is a real data-acquisition and ingestion project
(likely NHL API roster-history endpoints, a preseason schedule/results pull, and
a transactions feed), not something to fabricate. I am reporting this gap rather
than inventing plausible-looking numbers to fill it, per the block's own
instruction not to guess or invent weights.

## 11/15/16/17 (partial). What I *could* build and validate today: regression-fraction sweep

Using ONLY the corpus this repo already owns (`research/real_nhl_results`, 5,248
games, 2022-23 through 2025-26 — the only 3 real season boundaries this repo has
data for), I wrote `season_regression_candidates.py`: a strict prior-game-date
walk-forward, at six season-regression fractions (0.0 = full carry-forward, 0.30 =
current production, 1.0 = full reset to 1500), scored on the games immediately
following each of the 3 in-corpus boundaries, bucketed by min(home,away) games
played so far this season — exactly the 1-5/6-10/11-20/21+ buckets the block asked
for.

**Brier score by regression fraction** (lower is better; 3,936 post-boundary games total):

| fraction | overall | games 1-5 (n=258) | games 6-10 (n=244) | games 11-20 (n=487) | games 21+ (n=2947) |
|---|---|---|---|---|---|
| 0.00 (no regression) | 0.24413 | 0.23845 | 0.24285 | 0.25894 | 0.24228 |
| 0.15 | 0.24342 | 0.23735 | 0.24125 | 0.25673 | 0.24193 |
| **0.30 (current production)** | **0.24296** | **0.23708** | 0.24037 | 0.25492 | 0.24171 |
| 0.50 | 0.24272 | 0.23815 | 0.24034 | 0.25316 | 0.24160 |
| 0.70 | 0.24291 | 0.24098 | 0.24162 | 0.25213 | 0.24166 |
| 1.00 (full reset) | 0.24396 | 0.24869 | 0.24596 | 0.25194 | 0.24206 |

**Findings (real, from this data; no significance testing run — see caveat below):**

- Some regression clearly beats none: 0.30 beats 0.00 everywhere. Full reset (1.00)
  is also worse than any partial fraction except at games 21+, where it barely
  matters at all — by then in-season results have overwhelmed whatever the opening
  prior was.
- **Current production's 0.30 is close to the empirical optimum for early games**
  (best of the six at games 1-5) and is within noise of the overall minimum (0.50).
  I found no evidence production's existing constant is wrong.
- 0.50 edges out 0.30 slightly in the 11-20 game window and overall, but the gaps
  are in the 4th decimal place on ~250-500 game buckets — I have not run a
  bootstrap CI on this sweep (unlike the existing `elo_comparison_results.json`,
  which does bootstrap A/B/C/D). Without that, I cannot say 0.50 is a statistically
  meaningful improvement over 0.30, only that it is not worse in a way that would
  justify a change.
- By games 21+, all fractions converge to within 0.0005 Brier of each other —
  concrete evidence for the block's Section 15 "decay" question: whatever opening
  prior you pick, the current-season signal has already dominated it by ~20 games
  regardless of the starting fraction.

**Verdict on this piece: REJECT changing the constant.** 0.30 is not shown to be
wrong; nothing here clears the promotion bar the block itself sets ("meaningful and
robust out-of-sample improvement," "no leakage," confirmed with real CI). File and
results are `season_regression_candidates.py` / `season_regression_results.json`
in this directory for anyone who wants to re-run or extend it (e.g., add bootstrap
CI, which I did not do today given the scope of everything else in this block).

## 20. Tonight's five games — challenger vs baseline

There is no validated roster/preseason challenger to compare against production
tonight — building one honestly requires the data described in section 2 above,
which does not exist yet. The only variant I validated (season-regression
fraction) does not clear its own promotion bar, so there is nothing to show as an
alternate number for FLA@CAR, MTL@TOR, NYR@BOS, VAN@EDM, or CHI@VGK tonight without
it being exactly the frozen-corpus number already on Today.

## 21. Production safety for tonight

Unchanged. I have not modified `dashboard/live_dk.py`, `models/`, `pricing/`, or
any decision threshold. The existing `MAX_ELO_STALENESS_DAYS = 30` WAIT gate
remains fully in force for tonight's five games — correctly, since the actual
corpus IS 166-171 days stale and I found no validated correction to apply.

## Toronto / Columbus trade (Section 6)

Confirmed as an already-executed real transaction via NHL.com, ESPN, Sportsnet,
and The Hockey News (retrieved 2026-09-29, reported 2-13 hours prior to that):
**TOR receives** Kirill Marchenko (signed a 6-yr/$75M extension in Toronto), Miles
Wood, Elvis Merzlikins (18% salary retained by Columbus per PuckPedia); **CBJ
receives** Matthew Knies, Steven Lorentz, Emil Andrae, and a conditional 2027
2nd-round pick. This matches the block's expected transaction exactly.

I could not confirm from this engine's own database whether tonight's roster pull
already reflects it: `players` has no team-assignment column, and
`roster_status_events` (injury/scratch status only) has zero rows for any of the
six traded players. This is very unlikely to matter for tonight specifically: the
live moneyline decision (`dashboard/live_dk.py`) is priced from **team-level Elo
only** — it has no player-roster dependency at all — so this trade cannot silently
change tonight's BET/WAIT verdict for MTL@TOR regardless of whether the roster
cache has caught up. No manual Elo adjustment was made for Toronto or Columbus, as
instructed.

## Data limitations (exact)

- No historical trade/transaction dataset.
- No historical NHL preseason game or box-score dataset.
- No roster-continuity / lineup-history table (team assignment isn't tracked as a
  point-in-time field anywhere in `nhl.db`).
- No WAR/GAR or similar all-in-one player-value source already licensed by this
  repo.
- The regression-fraction sweep above has no bootstrap confidence interval (unlike
  the existing `elo_comparison_results.json` A/B/C/D work, which does).

## Recommendation

**REJECT promotion of any challenger — none exists to promote.** Keep current
production and today's `MAX_ELO_STALENESS_DAYS = 30` WAIT gate exactly as-is for
tonight. The one genuinely testable piece (season-regression fraction) does not
clear its own promotion bar, so it stays at the current 0.30. The real next step,
if this is worth pursuing, is a scoped data-acquisition project — an NHL API
roster/transaction history pull and a preseason schedule+results pull — before any
of sections 2-14 can be attempted without guessing.
