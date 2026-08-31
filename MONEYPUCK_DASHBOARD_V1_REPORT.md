# First Visual Research Dashboard (v1) — Report

**This turn builds UI only.** No production model, pricing, or PIT-table
code was modified. `nhl.db` is untouched (confirmed by mtime). The
dashboard is read-only with respect to every database it touches — see
Section T and `tests/test_dashboard.py`'s AST-scan tests. Full suite:
**476 / 476 passing** (439 pre-existing + 37 new).

---

## A. Dashboard framework selected and why

**Streamlit**, per the stated strong preference, and no reason surfaced
during Part 1's inspection to deviate: this is a pure-Python research
codebase (no existing frontend, no API server) with SQLite research
databases and already-tested Python analysis modules
(`research/elo_comparison.py`, `research/moneypuck_*.py`) — exactly
Streamlit's home territory. It lets every page call those modules
directly with zero new backend/API layer, matches "fast local
development... does not require prematurely building a large
frontend/backend architecture" precisely, and its multipage
`st.navigation`/`st.Page` API (available in the installed 1.50.0) gives
a clean 5-page structure without extra routing code. Altair (Streamlit's
own charting dependency) covers every chart needed — no additional
charting library was added.

## B. Files created

```
dashboard/
    app.py                      # entry point, navigation
    data_access.py               # data loading/caching, read-only guarantee
    model_view.py                 # model breakdown, team ratings, MoneyPuck context
    research_view.py              # experiment JSON parsing + status classification
    components.py                 # provenance panel, status header, labels
    pages/
        1_Game_Slate.py
        2_Game_Detail.py
        3_Team_Ratings.py
        4_Model_Performance.py
        5_Research_Lab.py
.streamlit/config.toml            # dark theme
tests/test_dashboard.py            # 37 tests
MONEYPUCK_DASHBOARD_V1_REPORT.md   # this report
```

## C. Files modified

- `README.md` — new "Dashboard" section (setup, run command, pages, data mode, guarantees)
- `requirements.txt` — added `streamlit>=1.36`
- `.gitignore` — no change needed (dashboard has no generated artifacts of its own)

**Not modified:** `models/`, `config.py`, `db.py`, `pricing/`, `nhl.db`,
`research/elo_comparison.py`, `research/moneypuck_*_features.py`,
`research/*_model_comparison.py` (all read-only reused, not touched).

## D. Dependencies added

```
streamlit>=1.36
```
Pulls in Altair (charting) and pandas (dataframes) as its own
dependencies — nothing separately pinned, per "keep dependencies
minimal."

## E. Exact start command

```bash
pip install -r requirements.txt
streamlit run dashboard/app.py
```
Opens at `http://localhost:8501` (add `--server.port <N>` for a
different port). Verified this turn running on port 8766 in a live
browser session — see Section F.

---

## F. Page-by-page verification (live-tested, not just code review)

Every page below was actually loaded in a running Streamlit instance
against the real corpus/MoneyPuck DB/experiment files and inspected via
screenshot + full page-text extraction — not assumed from source alone.

### Game Slate (`G` in the required delivery list)
Date picker over all 5,248 real games' dates; each game rendered as a
card: matchup, away/home win probability, a `Confidence` badge
(TOSS-UP/LEAN/CLEAR FAVORITE — an explicitly-labeled display heuristic,
not the production CI band), a short `Model drivers` list (Elo + home
ice only), and an explicit `Player / goalie / rest inputs: NOT AVAILABLE
IN HISTORICAL RESEARCH MODE` line. `ODDS DATA: NOT CONNECTED` shown
plainly — no fake odds, no fake BET/PASS. A "View game detail →" button
per card sets `st.session_state["selected_game_id"]` and switches to
Game Detail — **verified working end-to-end** (clicked LAK @ CGY on
2026-04-16, landed on Game Detail pre-selected to that exact game).

### Game Detail (`H`)
Matchup header with real final score; model output (both win
probabilities + confidence heuristic, with its caveat restated inline);
a "Model contribution breakdown" bar chart (Elo diff + home ice only,
labeled `PROBABILITY DRIVERS, not causal attribution`); an explicit
NOT-AVAILABLE line for player/goalie/rest; a per-team "Team context"
tabbed section showing last-5/last-10 record and an Elo history line
chart under a `MODEL INPUT` badge, and 5v5 xG share / offense-defense
xGF60/xGA60 / PP-PK rates under a `RESEARCH METRIC — NOT CURRENTLY USED
BY MODEL` badge — **the two are visually and structurally separated**,
never mixed in one list.

### Team Ratings (`I`)
Season + as-of-date pickers; a sortable table (current Elo, GP, W-L, and
optional MoneyPuck research columns, explicitly labeled per Section H's
distinction) generated via `model_view.team_ratings_table()`, driven
entirely by real data. Team-detail drill-down: Elo history chart,
record, games played, and a "model favorite-pick accuracy" stat — a
genuine bug was caught and fixed here during verification (see Section
U).

### Model Performance (`J`, `L`)
Season multiselect; headline Brier/log-loss/mean-predicted/actual-rate
metrics; a **calibration chart** (Altair scatter + dashed
perfect-calibration reference line, point size = bucket N, low-N buckets
excluded and counted); a season-by-season table; a probability
distribution histogram plus `>60%`/`>65%`/`>70%` extreme-prediction
stats. Every number computed live via `research.elo_comparison`'s
already-tested functions — verified: headline Brier 0.2417 across all
4 seasons combined matches independent computation.

### Research Lab (`K`, `M`)
All four experiments rendered with their real final decision, baseline
metrics, a per-candidate table (status, Brier/log-loss deltas, bootstrap
%), and an expandable per-candidate explanation with season-by-season
deltas — **verified against the source JSON**: e.g. `B_5v5_xg_share_25`
showed exactly `-0.195230745396%` in the live chart tooltip, matching
`XG_TEAM_FEATURE_EXPERIMENT_REPORT.md`'s reported `-0.195%` to full
precision. The comparison bar chart (`M`) was fixed mid-build to use
`resolve_scale(y="independent")` per experiment facet — without it,
every experiment's sub-chart listed all 14 candidates from all 4
experiments on its y-axis; verified after the fix that each facet shows
only its own 3-4 real candidates.

## N. Provenance / status panel

Present on every page (`components.render_provenance_panel()`), exact
required fields, sourced from `data_access.py` constants and the actual
experiment history — not hardcoded prose:
```
NHL results source: NHL Web API / real historical corpus (research/real_nhl_results/)
MoneyPuck: ARCHIVAL_RESEARCH (downloaded once, not a live feed)
MoneyPuck xG model version semantics: UNKNOWN
Current model status: RESEARCH / VALIDATION — baseline production Elo model, unmodified
Historical odds: NOT YET INTEGRATED
Goalie starter intelligence: NOT YET INTEGRATED
Data mode: HISTORICAL RESEARCH — no live current-season game feed is wired up in this project yet
```
Plus a `MODEL STATUS: RESEARCH / VALIDATION — not a proven profitable
betting model` header at the top of every page.

## O. Historical/live-mode handling

**Decision, stated plainly rather than silently made**: v1 supports
**HISTORICAL RESEARCH mode only**. There is no live current-season game
feed wired into this project (the real NHL corpus ends where its capture
session ended; live ingestion was never run against a real network in
this environment before this turn, and building that is out of scope
for a UI slice). Building a live mode would mean either fabricating
"today's games" (explicitly prohibited) or a new live-ingestion
engineering effort (explicitly out of scope: "No new feature
experiment"). `DATA MODE: HISTORICAL RESEARCH` is shown on every page,
in green, impossible to miss or mistake for a live prediction.

## P. Missing-data behavior

`data_access.py`'s `require_nhl_corpus()` / `require_moneypuck_db()`
raise a `DataAvailabilityError` with the exact missing path and the
command to build it; every page catches this and calls
`components.render_missing_data_page()` — a clear message + the error
text in a code block, never a raw traceback. `load_experiment_results()`
never raises at all; the Research Lab page renders a per-experiment
"Result file not found" warning and continues rendering the other three.
Verified via `tests/test_dashboard.py::TestMissingDataHandling` (4
tests) by pointing the module at nonexistent paths.

---

## Q. Exact tests added (`tests/test_dashboard.py`, 37 tests)

1. `TestDashboardImports` (4) — every dashboard module imports cleanly
2. `TestDataAccessReadsRealCorpus` (2) — real corpus loads (5,248 games); baseline predictions reuse `ec.run_walkforward` (AST-verified)
3. `TestMoneyPuckDbAccess` (2) — real DB opens; uses the ingestion pipeline's own connection helper
4. `TestDataModeLabeling` (2) — `DATA_MODE`/`MODEL_STATUS` constants exact
5. `TestModelInputVsResearchMetricLabels` (3) — model drivers never mention MoneyPuck/xG/PP/PK; MODEL_INPUT ≠ RESEARCH_METRIC
6. `TestNoFakeOddsFallback` (2) — no DraftKings/no-vig/fair-line strings; "NOT CONNECTED" notice present
7. `TestMissingDataHandling` (4) — clear errors for missing corpus/DB; graceful per-file experiment handling
8. `TestExperimentResultParsing` (4) — all 4 files parse; every candidate gets a valid status; **none marked ADOPTED**; Elo-shape normalization
9. `TestCalibrationDataGeneration` (1) — calibration table reuse
10. `TestTeamRatingsGeneration` (2) — sorted output; MoneyPuck context attached
11. `TestGameDetailDriverExtraction` (2) — Elo driver math matches `config.ELO_HOME_ADVANTAGE`; confidence buckets
12. `TestBaselineModelUnalteredByUi` (2) — dashboard predictions are genuinely `p_home ∈ [0,1]` from the real function; no `EloModel(` construction of its own
13. `TestDashboardCannotWriteProductionTables` (3) — no `import db`/`from db import` anywhere in `dashboard/`; no INSERT/UPDATE/DELETE SQL string literals anywhere
14. `TestProvenanceLabelsCorrect` (3) — ARCHIVAL_RESEARCH / UNKNOWN / NOT YET INTEGRATED all present

## R. Full new test result

**476 / 476 passing, 0 failed, 0 errors, 0 skipped** (439 pre-existing +
37 new). No existing test was modified or removed.

## S. Confirmation production model files were not changed

`models/elo_model.py`, `models/combined_model.py`, `models/player_model.py`,
`models/goalie_model.py`, `config.py`, `pricing/`, `features/point_in_time.py`
— all byte-identical to their pre-dashboard state (untouched this turn;
only read via import). `tests/test_dashboard.py::TestBaselineModelUnalteredByUi`
mechanically confirms `dashboard/data_access.py` never constructs its own
`EloModel(` and never imports `models.combined_model`.

## T. Confirmation production database is not mutated by the dashboard

`nhl.db` mtime unchanged before/after this entire turn (`Aug 26 16:15`).
`tests/test_dashboard.py::TestDashboardCannotWriteProductionTables`
AST-scans every file in `dashboard/` and `dashboard/pages/` for an
`import db` / `from db import` (the production `nhl.db` connection
module) and for any string literal containing `INSERT INTO`, `UPDATE `,
or `DELETE FROM` — none found. The dashboard opens exactly two SQLite
resources, both research-only: `research/moneypuck_ingestion/research_moneypuck.db`
(via that pipeline's own read/schema-apply connection helper) and
implicitly the JSONL/JSON research files (plain file reads, not a
database at all).

## U. Known dashboard limitations

- **Historical mode only** — no live current-season feed (Section O);
  deliberate, not an oversight.
- **A real bug was found and fixed during verification, not left for
  later**: Team Ratings' "model favorite-pick accuracy" originally had
  an inverted condition for away-team games (comparing `p_home >= 0.5`
  regardless of which side the selected team was on), understating
  accuracy for good teams. Fixed and reverified live (BUF: 42.7% → a
  correct 58.5%, consistent with its 50-32 record) — flagged here
  explicitly rather than silently corrected without mention.
- **"Confidence" is a display heuristic**, not the production
  uncertainty/CI band (`config.BASE_UNCERTAINTY_BAND_HALF_WIDTH`), which
  needs goalie-confirmation data this data mode doesn't have — stated on
  every page it appears, not just here.
- **Model drivers show only Elo + home ice.** Rest context is
  technically computable from the real corpus's own schedule dates
  alone (no roster data needed) but was deliberately not built this
  turn — doing so would be new feature-engineering work, out of scope
  ("No new feature experiment").
- **No live reload guarantee**: this environment's Streamlit installs
  without the optional `watchdog` package, so its file-watcher falls
  back to slower polling; a manual restart was used during development
  to guarantee picking up every edit rather than relying on it.
- **`st.dataframe` tables** (Team Ratings, Model Performance's
  season-by-season table) render as an interactive canvas grid that
  doesn't expose plain text to automated extraction — verified visually
  via screenshot instead of page-text dump for those specific tables.

## V. Recommended next single development slice

Per the fixed final answer below: **PREGAME STARTING-GOALIE INTELLIGENCE
FOUNDATION.**

---

## Final questions

```
DOES THE DASHBOARD RUN LOCALLY?
YES

DOES IT DISPLAY REAL NHL RESEARCH DATA?
YES

DOES IT DISPLAY CURRENT MODEL PROBABILITIES?
YES

DOES IT DISTINGUISH CURRENT MODEL INPUTS FROM REJECTED/RESEARCH FEATURES?
YES

DOES IT DISPLAY MODEL CALIBRATION?
YES

DOES IT DISPLAY COMPLETED EXPERIMENT RESULTS?
YES

DOES IT FABRICATE ODDS?
NO

DOES IT FABRICATE PREGAME GOALIE CONFIRMATION?
NO

DID THE DASHBOARD CHANGE PRODUCTION MODEL BEHAVIOR?
NO

DOES IT WRITE TO PRODUCTION PIT TABLES?
NO

CURRENT FULL TEST RESULT?
476 / 476

CAN THE USER NOW VISUALLY INSPECT THE NHL ENGINE?
YES

WHAT SHOULD THE NEXT SINGLE DEVELOPMENT SLICE BE?
PREGAME STARTING-GOALIE INTELLIGENCE FOUNDATION
```

---

## STOP AFTER DASHBOARD V1

Per instruction: goalie-starter intelligence, Frozen Tools, Daily
Faceoff, Goalie Post, RotoWire, The Odds API, and further MoneyPuck
features were not started, integrated, or implemented. Production model
was not changed. This report is returned for independent review; the
dashboard remains running locally at `http://localhost:8766` from this
turn's verification session for immediate inspection (restart anytime
with the Section E command). No further action was taken this turn.
