# Streamlit Memory Audit (BEFORE any change)

2026-09-25, Community Cloud memory sprint. Everything here was measured on master `3e3036c` (Streamlit 1.62.0, Python 3.14.7, pandas 3.0.5, macOS) **before** any optimization, then used to decide what to change. No optimization was made from a guess.

## Method

- **Process RSS** via `ps -o rss=` (no `psutil`; it is not installed and not a dependency). A per-page harness runs each page in a **fresh process** with `AppTest` (fake ADMIN session, isolated auth DB, isolated bankroll DB, `requests` blocked, `sqlite3.connect` / directory scans spied) and records RSS, newly imported modules, DB files opened, network calls and odds-archive scans.
- **`tracemalloc`** (single-frame; deeper tracing was too slow) on the heaviest page to find what the retained memory actually is.
- **A real running server** (`streamlit run` via a wrapper that seeds an isolated auth DB), driven through the built-in browser, for idle RSS, per-session cost and stacking.
- Import cost measured in isolation, one module at a time, in a fresh process.

## Headline finding

> **The Community Cloud memory failure is not caused by `nhl.db`, by page registration, by the number of pages, or by heavy libraries. It is caused by one process-wide object: the research model stack (`dashboard/demo_data.py::_demo_context()`), built on the first view of most "core" pages.**

`tracemalloc` on the Today page: **430 MB retained in `json/decoder.py` (7.27 million blocks)** — the frozen marginal corpora (`research/player_{sog,goals,points,assists,blocks}/*.jsonl`, ~70 MB on disk) decoded into Python dicts by `ShadowContextStack()`. Nothing else in the process comes close.

## Baseline numbers

### Process building blocks

| Step | RSS |
|---|---|
| bare `python3` | 20 MB |
| `import streamlit` | 53.5 MB |
| + `pandas` (first `st.dataframe`) | +70.8 MB |
| + `altair` | +35.7 MB |
| + `requests` | +5.2 MB |
| `research.context_overlay.prediction_stack` import (code only) | +1.8 MB (39 modules) |
| every other dashboard/research/operational module imported by the app | ≤ 0.5 MB each |
| **`ShadowContextStack()` construction (data)** | **≈ 430–550 MB** |

Module *imports* are cheap. What costs memory is *constructing* the stack from corpora.

### Real server (before)

| State | Server RSS |
|---|---|
| idle, nobody logged in | 71.6 MB |
| 1 USER session opened on **Today** | **594.6 MB** |
| same session then opens **Player SOG by Period** (research page) | **1,163 MB** |
| 2nd concurrent USER session on Today | 1,156 MB (≈ +0; sessions share the process-wide cache) |

### Repeated navigation (before) — no leak

Today → Game Detail → Paper Performance → Morning Review → Today, ×25 cycles, one process: cycle 1 **673 MB**, cycle 2 657, cycle 5 658, cycle 10 659, cycle 15 652, cycle 20 633, cycle 25 **633 MB**. It **plateaus**; there is no continuous growth. The problem is the *size* of the shared stack, not a leak.

### Per-page cost (before), fresh process each

See the full per-page table (before vs after) in `docs/STREAMLIT_MEMORY_CERTIFICATION.md`. The pattern:

- **420–585 MB** on every page that touches the demo stack: Today (556), Player Intelligence (551), Team Intelligence (548), Paper Performance (**585**, and it also **wrote to the DB on render**), Market Movement (486), Player Props (487), Goalies (474), Fantasy HQ (478), Combinations (420), Players (417). Once one of these has run, the others reuse the same cached stack (that is why the real server went 71 → 595 MB, not 71 → 4 GB).
- **Independent corpora, each stacked on top:** Player SOG by Period **557 MB** (68 MB JSONL), Player Points 240, Player Goals 237, Player SOG Research 170, Live SOG Markets 173.
- Game Detail with a demo game selected: **603 MB** (665 total).
- Everything else (status/registry/ledger pages): 3–8 MB.

## Findings by audit part

**Part 2 — `dashboard/app.py` import graph.** `app.py` imports `streamlit`, `dashboard.auth` (→ `operational.auth_store`) and, now, `page_registry`. It imports **no** pandas/numpy/scipy/sklearn/matplotlib/research/model code at startup. Measured: importing `app.py` and running the auth gate adds < 10 MB. No scipy/sklearn/statsmodels/matplotlib are installed or imported anywhere in the app. `pandas`/`altair`/`pyarrow` arrive lazily via Streamlit itself on the first `st.dataframe`/chart (~106 MB), unavoidably.

**Part 3 — page registration.** `st.navigation([st.Page(path), ...])` does **not** import or execute a page until it is selected. Registering all 36 pages is essentially free; only the selected page runs. (Confirmed: running `app.py` end to end, with all 36 pages registered, reached 611 MB total, versus 618 MB for running the default Today page alone -- the same within noise.) So eager page import was *not* the cause. The classification below still matters because a page that is *not registered* can never be executed.

**Part 7 — research databases.** Cloud startup never opens the 584 MB PBP DB or the MoneyPuck DB. The MoneyPuck research DB was opened by Game Detail / Team Ratings; PBP by neither.

**Part 8 — cache audit.** 38 `@st.cache_data` / `@st.cache_resource` decorators across 13 pages, **none** with `ttl` or `max_entries`. Cached objects are model engines (`cache_resource`, 4–557 MB) and loaded corpora / baseline predictions (`cache_data`, most no-arg so one entry). The four `compute_baseline_predictions` wrappers (`Game Slate`, `Game Detail`, `Team Ratings`, `Model Performance`) each cache their own ~0.7 MB pickle of the same 5,248-row list. `demo_data`'s `lru_cache(maxsize=1)` objects are bounded but process-lifetime.

**Part 9 — session state.** Small by construction: only `_auth_username`/`_auth_role`, `selected_game_id`/`selected_team`/`selected_player_id`, one market-filter id and a Yahoo OAuth `state` string. No DataFrames, models, payloads or recommendation objects. Multi-session cost measured ≈ 0.

**Part 10 — table audit.** `operational/prospective_ledger.py::operational_summary()` (called by Today and Ledger every render) did `SELECT * FROM predictions` into Python dicts and filtered/counted there. `real_recommendations_view` loads all MONEYLINE / SOG / SAVES ledger rows unbounded (but no page calls it today). `special_teams_history_store.get_connection()` executes schema DDL and *creates* the file on every open, and was used by a page render.

**Part 11 — odds archive render path.** Today's `build_live_moneyline_comparisons()` did `sorted(glob("*.json"))` + `json.load` of **all ~950 archived captures** on every rerun (and again from Paper Performance). `system_health.odds_archive_freshness_health()` `stat`-ed every file and looked only at the *legacy* directory — blind to every capture since the archive split (a bug from an earlier sprint).

**Part 12 — demo data.** The demo board *is* the recommendation UI: Today, Player Props, Goalies, Combinations, Market Movement, Players, Team/Player Intelligence, Game Detail and Paper Performance all read it. 175 demo opportunities + 420 prop rows + 20 goalie-saves rows over 47 real players.

**Part 13 — model loading.** No page loads a *production* model object. The "models" are the research engines inside the demo stack, `TeamSogEngine` (+23 MB, pulls the player-SOG corpus loaders), the Elo walk-forward (+11 MB) and the goalie-saves engine.

**Also found (not memory, but in scope for Cloud safety):**
1. **Paper Performance wrote to `paper_bankroll.db` on every render** (`ensure_*_paper_bets_created`).
2. Today/Ledger/Model Learning/Morning Review/Player Intelligence called `init_db()`/`get_connection()` on ledger, bankroll and special-teams DBs — creating the file if absent and running DDL — from a page render.
3. **Community Cloud first-visitor admin takeover:** no `auth_store.db` exists in git and the filesystem is ephemeral, so the "create the administrator account" form would let whoever reaches the public URL first (and again after every restart) become **ADMIN**.

## Page classification

| Class | Pages | In COMMUNITY_CLOUD_MODE |
|---|---|---|
| **CORE_USER** (10) | Today, Games, Game Detail, Players, Team Intelligence, Player Intelligence, Model Health, Model Learning, Paper Performance, Ledger | registered |
| **DEMO** (5) | Player Props, Goalies, Combinations, Market Movement; Fantasy HQ | first four registered (snapshot-backed); Fantasy HQ not |
| **LIGHTWEIGHT_ADMIN** (3) | Morning Review, Data Status, Diagnostics (new) | registered |
| **HEAVY_RESEARCH** (15) | Live SOG Markets, Team Ratings, Player SOG / SOG-by-Period / Goals / Points / Team SOG / Goalie Saves / Goalie Intelligence / Joint Shot-Workload / Joint Scoring / Team Goals-by-Period / Player Context State / Model Performance / Research Lab | **not registered** |
| **LEGACY** (4) | Play-by-Play Status, Research Hub, Prop Registry, Fantasy Settings | **not registered** |

Single source of truth: `dashboard/page_registry.py` (`app.py` builds `st.navigation` from it). Verified: no Cloud-registered page links (`st.switch_page` / `st.page_link`) to a page that is not registered there.
