# Team-Strength Upgrade Using Result Quality — Report

**Bottom line up front:** Steps 1–3 and 7–10 are complete below. Steps 4–6
(the real-data comparison) are **NOT EXECUTED — INSUFFICIENT REAL DATA**.
This environment's only persistent database contains 100% synthetic
demo-generated games. The only genuinely real NHL games available anywhere
in this environment are 3 (three) completed games. No code was changed
this turn. Current verified baseline remains 322/322 tests passing
(re-confirmed just now via `unittest discover`).

---

## STEP 1 — Audit of the current Elo exactly (source-quoted, not assumed)

Read directly from `models/elo_model.py` and `models/combined_model.py`,
unchanged this session:

```python
def win_probability(self, home_team, away_team, extra_home_adj=0.0, extra_away_adj=0.0):
    home_r = self.ratings[home_team] + config.ELO_HOME_ADVANTAGE + extra_home_adj
    away_r = self.ratings[away_team] + extra_away_adj
    return 1.0 / (1.0 + 10 ** (-(home_r - away_r) / 400.0))

def update(self, home_team, away_team, home_won: bool) -> None:
    assert config.ELO_UPDATES_ON_BASE_EXPECTATION
    p_home = self.win_probability(home_team, away_team)   # BASE expectation only
    actual = 1.0 if home_won else 0.0
    delta = config.ELO_K_FACTOR * (actual - p_home)
    self.ratings[home_team] += delta
    self.ratings[away_team] -= delta
```

Answers to the 8 audit questions:

1. **Expected-score formula:** standard logistic Elo, `1/(1+10^(-diff/400))`, `diff` in rating points.
2. **Home-ice treatment:** a flat additive constant, `config.ELO_HOME_ADVANTAGE = 35.0`, baked into `win_probability()` before the logistic transform — not modeled separately anywhere else.
3. **K-factor:** `config.ELO_K_FACTOR = 20.0`, a single constant, no dynamic/variable K.
4. **OT/SO vs. regulation:** **not distinguished at all.** `update()`'s only inputs are `home_team`, `away_team`, `home_won` (a bool). `final_period_type` is never read by `EloModel`.
5. **Goal differential / margin of victory:** **never used.** `home_score`/`away_score` are not passed into `update()` at all — only the binary win/loss.
6. **What `update()` actually updates on:** the **base** Elo expectation (`win_probability()` called with no `extra_home_adj`/`extra_away_adj`), deliberately *not* the fully player+goalie+rest-adjusted probability the pricing engine bets on. This is intentional (see `config.ELO_UPDATES_ON_BASE_EXPECTATION`'s comment) to avoid double-counting the same signal into two different learning systems.
7. **Season regression:** `maybe_regress_new_season()` pulls every team's rating `ELO_SEASON_REGRESSION = 0.30` (30%) of the way back toward `ELO_START = 1500.0` on a season-label change.
8. **How Elo combines with the rest of the model:** in `combined_model.py`, `compute_probability_from_features()` takes `elo.win_probability(home, away, extra_home_adj=player/goalie/rest terms, extra_away_adj=...)` — i.e., Elo's *rating difference* is one additive term (already converted to point-space via `POINTS_PER_GAME_TO_ELO`/`SAVE_PCT_TO_ELO`/rest penalties) inside a single logistic transform, not a separate stacked model. `learn()` calls `self.elo.update(home, away, home_score > away_score)` — confirming score margin and `final_period_type` are already present in the fetched `result` dict at the exact same call site, just never forwarded into `elo.update()`.

**Conclusion of Step 1:** the audit confirms the user's framing exactly — Elo currently sees a single bit (`home_won`) per game and discards both the margin and how the game ended, even though both fields are already sitting in the same `result` object at the point of the call.

---

## STEP 2 — Candidate definitions (A/B/C/D), exact formulas, small parameter grid

All four candidates keep `win_probability()` **byte-identical** (nothing about the win-probability calculation, home-ice constant, or player/goalie/rest layer changes — only how much `update()` moves the ratings after a game is known). `p_home` below is the existing base expectation, `actual` is the existing 1.0/0.0 win indicator.

**Candidate A — Baseline (current, unchanged):**
```
delta = K * (actual - p_home)
```

**Candidate B — OT/SO-aware weighting.** Rationale: a game that reached overtime/shootout means regulation ended tied — a weaker signal that the winner is truly the stronger team than a regulation win is. Apply a multiplier `w` to the update magnitude when `final_period_type != 'REG'`:
```
w = 1.0                       if final_period_type == 'REG'
w = OTSO_WEIGHT                if final_period_type in ('OT', 'SO')
delta = K * w * (actual - p_home)
```
Small candidate grid: `OTSO_WEIGHT ∈ {0.50, 0.75}` (2 values; 1.0 would be identical to baseline A, so it's not a separate candidate).

**Candidate C — Capped margin-of-victory (MOV).** Rationale: bigger regulation wins carry more team-strength signal, but this must be capped so a garbage-time empty-net goal or a 7-0 blowout doesn't dominate the rating (Step 7 discusses this explicitly). Uses a log-dampened, capped multiplier normalized so the single-goal margin (the modal NHL result) is exactly neutral relative to baseline:
```
raw_margin    = abs(home_score - away_score)
capped_margin = min(raw_margin, MOV_CAP)
mov_mult      = log(1 + capped_margin) / log(2)     # = 1.0 exactly at margin == 1
delta = K * mov_mult * (actual - p_home)
```
Small candidate grid: `MOV_CAP ∈ {3, 4}` (2 values). Note this candidate does **not** look at `final_period_type` at all — for OT/SO games, `raw_margin` is 1 by rule (the shootout/OT winning goal is the only margin the box score shows), so `mov_mult == 1.0` automatically, i.e. Candidate C alone leaves OT/SO games exactly at baseline weight.

**Candidate D — Combined.** Both multipliers applied together:
```
delta = K * w(final_period_type) * mov_mult(capped_margin) * (actual - p_home)
```
Grid: the cross product of B's and C's grids → `{0.50, 0.75} × {3, 4}` = 4 combined variants.

Total candidate space: 1 (A) + 2 (B) + 2 (C) + 4 (D) = **9 parameterizations**, deliberately small, per Step 6's instruction. Per your instruction, **D is not assumed to win** — B and C are evaluated independently and D is only preferred if it beats *both* of its component candidates, not merely the baseline.

---

## STEP 3 — Point-in-time safety of the design

No new data source is required for any candidate. `final_period_type`, `home_score`, and `away_score` are **already** fetched at the correct chronological moment: `combined_model.py`'s `learn()` obtains the `result` dict via `pit.game_result_as_of()` in the exact same call that currently extracts `home_won`. Extending the call from `self.elo.update(home, away, home_won)` to `self.elo.update(home, away, home_won, final_period_type=result["final_period_type"], home_score=result["home_score"], away_score=result["away_score"])` reads no additional column, touches no additional table, and does not change *when* the result becomes visible to the model — it only widens what's read from a record that was already being read at that timestamp. `process_games()`'s chronological event-stream ordering, `completed_games_known_before()`, and `game_result_as_of()` are all unaffected. No mutable-cache shortcut, no pre-observation read, and no later-revision leakage is introduced — this is a pure widening of an existing, already-PIT-safe read.

**This confirms the candidates are safe to implement in principle.** Whether they *should* be implemented is a separate question, answered by Steps 4–6 below — which is where this slice currently stops.

---

## STEP 4 / STEP 5 — Real-data walk-forward comparison: **NOT EXECUTED — INSUFFICIENT REAL DATA**

I queried this environment's only persistent database directly rather than assuming:

```
$ python3 -c "sqlite3 nhl.db ... SELECT source, COUNT(*) FROM games GROUP BY source"
('demo_generator', 1062)

SELECT season, COUNT(*) FROM games GROUP BY season:
('2022-2023-DEMO', 264)
('2023-2024-DEMO', 264)
('2024-2025-DEMO', 264)
('2025-2026-DEMO', 270)

SELECT source, data_provider, COUNT(*) FROM game_result_events GROUP BY source, data_provider:
('demo_generator', 'demo_generator', 1056)

SELECT source, data_provider, COUNT(*) FROM game_schedule_events GROUP BY source, data_provider:
('demo_generator', 'demo_generator', 1062)
```

**Every single row in `nhl.db` — games, results, schedule events, odds
snapshots, predictions — was produced by `ingest/demo_data.py`**, a
documented synthetic generator (its own docstring: *"Synthetic
multi-season dataset so the pipeline can run end to end without network
access... The generator holds a hidden `true_strength` per team... that
the model never sees directly"*). The four "seasons" in the database are
explicitly labeled `-DEMO` for exactly this reason, and `README.md`
already states, in its own words, that `backtest.py`'s numbers on this
dataset are "not evidence of real-world edge."

The **only** genuinely real, non-synthetic NHL game data present anywhere
in this environment is 3 completed games captured via browser earlier
this session for the API-contract replay (`tmp_live_contract/`):

| game_id | result | period type |
|---|---|---|
| 2025030412 | home 4 – away 3 | OT |
| 2025030413 | home 5 – away 4 | OT (2 OT periods) |
| 2025030414 | home 3 – away 5 | REG |

These 3 games were loaded only into throwaway, ephemeral temp SQLite
databases during this session's contract-verification work — **never**
into `nhl.db` or any durable store.

Your Step 4 instruction is explicit and I am following it exactly rather
than working around it: *"Do NOT tune on synthetic data... Use REAL NHL
completed-game history."* Your Step 5 instruction requires, "where
practical," multiple completed NHL seasons, with season-by-season Brier /
log-loss / calibration comparison. **n = 3 real games (2 OT, 1 REG, all
from a single series in a single season) cannot support that
methodology under any honest reading** — it is not one order of
magnitude short of "a season" (~1,300 games), it is roughly three
orders of magnitude short, and it contains no meaningful variation to
even sanity-check a margin-of-victory multiplier (all 3 real margins are
1 or narrow).

**Result: STEP 4 and STEP 5 are NOT EXECUTED.** I have not run any
baseline-vs-candidate comparison, on real or synthetic data, and I have
not fabricated or "estimated" Brier/log-loss numbers to fill in this
section. There is nothing to report here except the blocker itself.

## STEP 6 — Overfitting control: **NOT APPLICABLE (upstream of Step 6, no comparison exists to control)**

Nothing to tune or select without Step 5's data. Applying your own
discipline from Step 6 one level up: *"If no candidate robustly improves
the baseline: KEEP THE CURRENT ELO. 'NO IMPROVEMENT' is an acceptable
result."* I'm extending that same discipline honestly — if a candidate
cannot even be *tested* against real data, it cannot be adopted. That is
not a "no improvement" finding; it is a "not evaluated" finding, and I am
not blurring the two.

---

## STEP 7 — Empty-net / blowout caveat (design-level; answerable regardless of data availability)

This is exactly why Candidate C caps the margin rather than using raw
goal differential. An empty-net goal in the final minute of a game the
losing team already couldn't tie is not additional evidence of team
strength — it's an artifact of NHL late-game strategy (pulling the
goalie), and an uncapped MOV term would let a single empty-net insurance
goal move ratings by the same amount as a "real" 5th goal earned at even
strength. The design deliberately does two things to bound this: (1) the
log transform (`log(1+margin)`) means each additional goal matters
strictly less than the one before it — a 6-1 game is not treated as "6×"
a 1-goal game; (2) the hard cap (`MOV_CAP ∈ {3,4}`) means anything beyond
a 3-4 goal margin — exactly the range where empty-net garbage-time goals
start to appear — contributes zero *additional* update magnitude no
matter how lopsided the final score gets. This slice deliberately does
**not** attempt to detect or exclude actual empty-net goals from the
underlying score differential (that would require play-by-play data this
engine doesn't ingest) — the cap is a blunt, cheap substitute for that,
not a replacement for it. If this slice is ever revisited with real
play-by-play access, a true empty-net-adjusted margin would be a strictly
better version of Candidate C, not a different idea.

---

## STEP 8 — Other model components: confirmed frozen

No file other than this report was modified this turn. `models/elo_model.py`, `models/combined_model.py`, `models/player_model.py`, goalie/rest logic, `config.py`, pricing, and thresholds are all byte-identical to the last accepted state. Full suite re-run just now: **322 tests, 322 passed, 0 failed, 0 errors** — confirming nothing drifted.

## STEP 9 — Goalie workload: confirmed still deferred

Restating exactly, per your prior instruction: **GOALIE-SPECIFIC REST/WORKLOAD ADJUSTMENT — DEFERRED — historical pregame starter identity unavailable.** Goalie workload *history* remains point-in-time reconstructable and a high-value forward feature once a defensible pregame starter-confirmation source exists (or for games collected forward from today). No boxscore-derived starter identity has been or will be used as a historical pregame feature. Untouched this slice.

## STEP 10 — MoneyPuck / xG: confirmed not integrated

Not touched, referenced, or fetched this slice. Remains next-tier, pending its own separate data-contract/licensing review as you specified.

---

## Required delivery items (A–N)

```
A. Exact Elo audit                          -- DONE, see Step 1 (source-quoted)
B. Candidate formulations (A/B/C/D)         -- DONE, see Step 2
C. Exact formulas                           -- DONE, see Step 2
D. Parameter candidates tested              -- NONE TESTED -- 9 defined,
                                                zero executed (see Step 4/5)
E. Real NHL seasons/games used              -- 0 seasons; 3 real completed
                                                games exist in this
                                                environment but were not
                                                used for any comparison
                                                (statistically unusable)
F. Walk-forward methodology                 -- DESIGNED (see Step 3), NOT
                                                EXECUTED
G. Baseline Brier/log-loss/calibration      -- NOT COMPUTED -- no real
                                                dataset to compute it on
H. Candidate Brier/log-loss/calibration     -- NOT COMPUTED (same reason)
I. Season-by-season comparison              -- NOT AVAILABLE -- 0 real
                                                seasons in this environment
J. Improvement persistence across seasons   -- CANNOT DETERMINE -- no
                                                comparison was run
K. Temporal-integrity tests added           -- NONE ADDED this slice
                                                (no implementation was
                                                justified without real-data
                                                evidence)
L. Full new test count                      -- UNCHANGED: 322/322 (no
                                                code or tests modified)
M. Recommendation                           -- KEEP CURRENT ELO (Candidate
                                                A). Not because any
                                                candidate underperformed --
                                                none could be evaluated --
                                                but because adopting an
                                                untested change to a
                                                production probability
                                                model would violate the
                                                same anti-synthetic-tuning
                                                discipline this project has
                                                enforced every prior turn.
N. Goalie-workload status                   -- DEFERRED -- historical
                                                pregame starter identity
                                                unavailable (see Step 9)
```

## Final answers

```
DID ANY CANDIDATE IMPROVE BRIER SCORE OUT OF SAMPLE?
CANNOT DETERMINE -- no real out-of-sample comparison was executed.

DID ANY CANDIDATE IMPROVE LOG LOSS OUT OF SAMPLE?
CANNOT DETERMINE -- same reason.

WAS THE IMPROVEMENT CONSISTENT ACROSS MULTIPLE SEASONS?
NOT APPLICABLE -- this environment holds 0 real NHL seasons. The
persistent database's only "seasons" are 4 synthetic RNG-generated
demo seasons (2022-2023-DEMO ... 2025-2026-DEMO), which your own Step 4
instruction excludes as evidence.

SHOULD THE CURRENT ELO BE REPLACED?
NO -- not on the basis of any finding against the candidates (none were
tested), but because there is currently no real-data basis to justify
replacing a production component, consistent with "NO IMPROVEMENT is an
acceptable result" extended to "NOT EVALUATED is not a basis for
adoption either."
```

---

## What would unblock Steps 4–6

This is a genuine resource/scope decision, not a technical dead end — the
data is public and free (unlike the historical-odds situation), the
constraint is purely that this sandbox's Python networking cannot reach
`api-web.nhle.com` (confirmed blocked in earlier turns) and the only
working access path is one-request-at-a-time browser replay. Options, in
increasing order of effort:

1. **Heavily-caveated micro-pilot on the 3 real games only** — would only
   ever function as a code/plumbing smoke-test (does the extended
   `elo.update()` signature run and stay point-in-time-safe end to end),
   explicitly labeled as producing zero statistical evidence either way.
2. **Bounded browser-replay backfill** — e.g., one real completed NHL
   season (~1,312 games) would require on the order of 1,300+ individual
   browser-fetch round trips (schedule pages plus per-game boxscores) to
   assemble into a persistent real dataset before any Brier/log-loss
   comparison could run. This is a large, slow, explicit undertaking I
   have not begun or estimated in finer detail, and would need your
   authorization given the scale.
3. **Defer this slice's real-data evaluation entirely** — keep Candidate
   A (current Elo) as-is, keep Candidates B/C/D as a documented, inert
   design (this report) ready to test the moment real bulk historical
   ingestion exists (e.g., if paid historical-odds work resumes later and
   brings a real-game backfill along with it), and move to a different
   roadmap item that doesn't depend on bulk real historical results.

I have not started any of these without your direction, consistent with
how every prior data-availability blocker in this project has been
handled.

Then STOP, per your instruction. No xG work, no goalie-workload
implementation, no paid-odds work, and no tuning of any other model
component has been started.
