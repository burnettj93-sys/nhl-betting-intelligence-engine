# Streamlit Memory Certification

2026-09-25, Community Cloud memory sprint. Companion to `docs/STREAMLIT_MEMORY_AUDIT.md` (the BEFORE analysis). Goal: keep using the **free** Streamlit Community Cloud tier by making the web process a thin, read-only presentation layer. No models were built or changed; `decision_policy` and validated thresholds are untouched; no VPS was deployed.

## Result

The web process no longer builds the research model stack or loads research corpora in `COMMUNITY_CLOUD_MODE`. Every measured target is met with a large margin; the heaviest realistic state is **~205 MB** (was **1,163 MB**).

| Metric | BEFORE | AFTER (`COMMUNITY_CLOUD_MODE`) |
|---|---|---|
| Server idle (before any session) | 71.6 MB | 71.6 MB |
| Cold start + first session on **Today** (real server) | **594.6 MB** | **157.3 MB** |
| After Today → Game Detail → Player Intelligence → Paper Performance (real server) | > 650 MB (Game Detail alone: 603 MB delta) | **200.5 MB** |
| Heaviest page state | **1,163 MB** (Today + Player SOG by Period) | **204.8 MB** (all 17 Cloud pages ×3 in one process) |
| 25-navigation-cycle RSS (Today → Game Detail → Paper Performance → Morning Review → Today) | cycle 1: 673, cycle 25: **633** MB | cycle 1: 197.1, cycle 25: **198.8** MB |
| Plateaus? | yes | **yes** (197.1 → 198.8 MB over 25 cycles) |
| Extra concurrent session | ≈ +0 (shared cache) | ≈ +0–10 MB (184–200 MB with 2 sessions; within GC noise) |

Engineering targets (from the task): cold startup < 800 MB ✅ (157), Today < 1.0 GB ✅ (157), steady state < 1.2 GB ✅ (~200), heaviest retained page < 1.5 GB ✅ (~205), stable plateau ✅.

### Per-page RSS, fresh process each (delta / total, MB)

| Page | BEFORE | AFTER (Cloud mode) |
|---|---|---|
| `1_Game_Slate.py` | 18 / 80 | 19 / 81 |
| `2_Game_Detail.py` | 603 / 665 | 124 / 186 |
| `3_Team_Ratings.py` | 128 / 190 | not registered |
| `4_Model_Performance.py` | 124 / 186 | not registered |
| `5_Research_Lab.py` | 113 / 175 | not registered |
| `6_Goalie_Intelligence.py` | 71 / 133 | not registered |
| `7_Player_SOG_Research.py` | 170 / 232 | not registered |
| `8_Live_SOG_Markets.py` | 173 / 235 | not registered |
| `9_Data_Status.py` | 4 / 66 | 6 / 68 |
| `10_Prop_Registry.py` | 3 / 66 | not registered |
| `11_Player_Points_Research.py` | 240 / 302 | not registered |
| `12_Player_Goals_Research.py` | 236 / 299 | not registered |
| `13_Play_By_Play_Status.py` | 4 / 66 | not registered |
| `14_Player_SOG_By_Period_Research.py` | 557 / 619 | not registered |
| `15_Team_Goals_By_Period_Research.py` | 29 / 91 | not registered |
| `16_Goalie_Saves_Research.py` | 86 / 148 | not registered |
| `17_Team_SOG_Research.py` | 37 / 99 | not registered |
| `18_Joint_Shot_Workload_Research.py` | 4 / 66 | not registered |
| `19_Joint_Scoring_Dependence_Research.py` | 4 / 66 | not registered |
| `20_Player_Context_State_Research.py` | 8 / 70 | not registered |
| `21_Today.py` | 556 / 618 | 84 / 146 |
| `22_Model_Health.py` | 4 / 66 | 6 / 69 |
| `23_Ledger.py` | 4 / 66 | 6 / 68 |
| `24_Research_Hub.py` | 3 / 66 | not registered |
| `25_Player_Intelligence.py` | 551 / 613 | 82 / 145 |
| `26_Player_Props.py` | 487 / 549 | 78 / 140 |
| `27_Goalies.py` | 474 / 536 | 6 / 68 |
| `28_Combinations.py` | 420 / 482 | 9 / 71 |
| `29_Market_Movement.py` | 486 / 548 | 77 / 140 |
| `30_Players.py` | 417 / 479 | 8 / 70 |
| `31_Team_Intelligence.py` | 548 / 610 | 80 / 142 |
| `32_Model_Learning.py` | 5 / 66 | 7 / 69 |
| `33_Paper_Performance.py` | 585 / 647 | 117 / 179 |
| `34_Fantasy_HQ.py` | 478 / 540 | not registered |
| `35_Fantasy_Settings.py` | 7 / 70 | not registered |
| `36_Morning_Review.py` | 76 / 138 | 78 / 140 |
| `37_Diagnostics.py` (new, ADMIN) | -- | 5 / 68 |

Notes: (1) the figures for pages that show ~78 MB "after" are dominated by `pandas` + `altair` being imported by Streamlit for the first `st.dataframe` — unavoidable Streamlit cost, not app data. (2) "Game Detail before" is with a demo game selected (live stack), the real user flow; the audit doc's 128 MB figure was the no-selection view. (3) Per-page numbers use in-process `AppTest`; the real-server rows above are the ground truth for the whole process.

## What changed (each optimization)

1. **`COMMUNITY_CLOUD_MODE`** (`operational/runtime_mode.py`): `NHL_ENGINE_RUNTIME_MODE` env → `.env` → `st.secrets` → auto-detect (`/mount/src/…`) → `LOCAL_MODE`. An unrecognized value falls toward the full app, never thins it silently. Independent of the ACTIVE/STANDBY scheduler axis.
2. **Compact verified snapshot** (`dashboard/cloud_snapshot/board.json`, 760 KB): the deterministic demo board (games, roster, goalies, 175 opportunities, 420 prop rows, 20 goalie-saves rows, market movement, per-player activity/history/context evidence, Team SOG projections) plus the real moneyline comparison rows, computed locally. In Cloud mode `demo_data`, `eligible_bets`, `live_dk`, `player_intelligence_view` and `game_detail_view` read it; `_demo_context()` **raises** rather than building the ~430 MB stack. Regenerate with `python3 -m dashboard.cloud_snapshot`; `--check` (and a unit test) recomputes everything live and fails if the shipped file differs, so it cannot silently go stale.
3. **Page registry** (`dashboard/page_registry.py`): single source of truth; 20 of 37 pages (15 HEAVY_RESEARCH, 4 LEGACY, Fantasy HQ) are **not registered** in Cloud mode. Nothing was deleted; LOCAL/PRODUCTION register all pages exactly as before (asserted by test).
4. **Lazy heavy imports:** `count_models`, `live_projection`, `ShadowContextStack`, `context_state` are imported inside the functions that need them; the Cloud path never imports the model stack or corpus loaders (asserted in a clean subprocess against a forbidden-module list).
5. **Read-only DB access on render:** `open_for_dashboard()` for the ledger and bankroll (`mode=ro`, or an empty in-memory schema when the file is absent, never creating a file), `open_readonly()` for the special-teams DB; used by Today, Ledger, Model Learning, Morning Review, Paper Performance, Player Intelligence and the health check. **Paper Performance no longer creates paper bets on render in Cloud mode.**
6. **Research DBs never opened in Cloud:** `require_moneypuck_db()` raises `DataAvailabilityError` (already handled by callers).
7. **Odds-archive render path:** the archive scan is memoized on a stat-only fingerprint (a rerun no longer re-parses ~950 captures unless the archive changed); in Cloud the comparison rows come from the snapshot. `odds_archive_freshness_health()` fixed to see the runtime directory and to do one `scandir` + one file open.
8. **Table/query fixes:** `operational_summary()` now uses SQL aggregates instead of `SELECT *` + Python filtering (identical results, tested against a Python reference including NULL/empty values).
9. **Caches:** all 38 `@st.cache_data`/`@st.cache_resource` now bounded (`max_entries`; data caches also `ttl=3600`). AST test prevents regressions.
10. **Freshness honesty:** a banner on every page in Cloud mode; `Database: STALE` (frozen git snapshot, file not even opened), `Last Sync` / `Live Odds Scheduler`: `NOT_REQUIRED`.
11. **ADMIN-only Diagnostics page** (RSS, peak RSS, Python/Streamlit versions, runtime mode, which libraries are loaded, whether the model stack is loaded). No env vars, secrets or paths.
12. **Cloud admin-bootstrap guard:** see "Security finding" below.
13. **Streamlit pinned** `streamlit==1.62.0` in `requirements.txt` and a new slim `dashboard/requirements.txt` (Streamlit + `requests`; no `cryptography`, which only Yahoo needs). Streamlit 1.64.0 exists; not adopted — every measurement and test ran on 1.62.0 and no win here depends on a newer release.

## Features disabled ONLY in COMMUNITY_CLOUD_MODE

- All 15 HEAVY_RESEARCH pages and 4 LEGACY pages (not registered).
- Fantasy HQ and Fantasy Settings (Yahoo): Cloud has no durable encrypted token store. Yahoo remains ADMIN-only wherever it is registered (LOCAL/PRODUCTION).
- Schedulers, NHL/Odds ingestion, settlement, post-mortem, backups, model training/replay: never started by the web process (asserted: no subprocess, no network, no DB writes during rendering of every Cloud page).
- Paper-bet creation on page render; creation of any ledger/bankroll/special-teams DB file.
- The real-corpus slate walk in Today's technical-detail expander.
- Bootstrap of the first administrator without a configured setup code.

Everything a USER needs is retained: login, Today, games, recommendations, odds/edge/confidence, model probability, Game Edge Parlay, Game Detail, Player/Team Intelligence, performance and ledger views (all render from the snapshot). ADMIN keeps Morning Review, Data Status and the new Diagnostics.

## Data freshness — what Community Cloud can and cannot show

Community Cloud receives **only what is committed to git**. Nothing pushes the laptop's live state to it. Displayed data on Cloud:

| Surface | Source in Cloud | Freshness |
|---|---|---|
| Demo board (props, goalies, combos, movement, Player/Team Intelligence, Game Detail) | `board.json` snapshot | Frozen at generation time; **simulated prices** for a **simulated slate (2026-10-14)**; model output is real but frozen. Labeled. |
| "Live Model Edges" (real DraftKings moneyline vs Elo) | snapshot rows | Newest capture in the current snapshot: **2026-09-15**; Elo ratings through **2026-04-16**. Labeled with each row's `captured_at_utc`. **Not live.** |
| System health pills | gitignored caches → absent | `Database: STALE`, `Last Sync`/`Scheduler`: `NOT_REQUIRED`; other pills reflect files that are not in git (UNKNOWN/WAITING). |
| Ledger, Paper Performance, Model Learning, Morning Review | DBs are gitignored → absent | Honest zeros / "no data". The web process never fabricates or creates them. |
| `nhl.db` | tracked repo-root file, frozen | **Frozen** (the live DB is the gitignored runtime file, `docs/RUNTIME_DB_HYGIENE.md`). Not opened at all in Cloud mode. |

**Architecture limitation, stated plainly:** Community Cloud cannot receive updated operational state from the laptop/VPS. To refresh the Cloud snapshot you must regenerate `board.json` locally and push a commit. Live, current data requires the VPS deployment (`docs/VPS_*.md`), which was out of scope here.

## Security finding (fixed in Cloud mode)

On Cloud there is no `auth_store.db` in git and the filesystem is ephemeral, so the existing "no users yet" form would let **whoever reaches the public URL first — and again after every restart — create the ADMIN account.** In Cloud mode the bootstrap now requires a setup code from the `NHL_ENGINE_ADMIN_SETUP_CODE` secret; with none configured no account can be created. **Consequences you must plan for:** (1) set that secret in the app's Cloud settings before deploying; (2) accounts live on the ephemeral filesystem and are lost when the app restarts or sleeps; (3) there is no in-app way to add a USER (only `auth_store.create_user()`), so on Cloud only the owner's ADMIN account is practical. Making the app *private* (viewer allow-list in Cloud settings) is the stronger control and is recommended.

## Verification

- **Tests:** `tests/test_streamlit_cloud_mode.py` (63 tests): mode resolution; registry completeness/classification; LOCAL/PRODUCTION register every original page; Cloud omits heavy/legacy; no dangling `switch_page`; snapshot == live computation; snapshot compact; accessors return copies; **clean-subprocess render of every Cloud page as ADMIN asserting: no exception, no DB writes (verb-level trace) and critical DB hashes unchanged, no network, no subprocess/scheduler, none of the forbidden modules imported, session state < 20 KB, banner present**; frozen-DB honesty; Paper Performance read-only; all cache decorators bounded and data caches expire; `operational_summary` equals a Python reference; archive memoization; ADMIN-only diagnostics (USER and logged-out stopped); Yahoo pages admin-only and absent from Cloud; manifests; admin-bootstrap setup code.
- **Local server test in `COMMUNITY_CLOUD_MODE`:** started, logged in as USER and ADMIN, navigated Today → Game Detail → Player Intelligence → Paper Performance → Diagnostics. Afterwards `paper_bankroll.db`, `prospective_observations.db` and `special_teams_history.db` had modification times **before** the server started, and no `auth_store.db` had been created in the repo.
- **Browser QA (widths 390, 430, 768, 900, 1200, 1440):** login (logged out), Today, Game Detail, Paper Performance, Morning Review and Data Status all rendered with **no horizontal overflow** and the correct heading at every width. *Method caveat:* the in-app browser pane was not displayed, so screenshots could not be captured; this QA was DOM-based (`documentElement.scrollWidth` vs viewport, elements extending past the viewport, heading and content present). It proves layout does not break; it is not a visual design review.

## Not verified / honest limits

- **I did not deploy to Streamlit Community Cloud**, so its real memory ceiling and its dependency-file precedence (I believe it prefers a manifest next to the entrypoint, which is why `dashboard/requirements.txt` exists; both manifests pin the same Streamlit so either works) were not observed. All measurements are local.
- Memory numbers are macOS RSS. Linux RSS is typically similar or lower for the same workload but was not measured.
- The Cloud filesystem, cold-start time and concurrent-user behavior under load were not exercised.
- `nhl.db` remains tracked (frozen); untracking it would change what Cloud ships and was left for a decision.
