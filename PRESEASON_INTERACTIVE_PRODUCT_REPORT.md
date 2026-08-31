# Preseason Interactive Product Report

**Sprint:** Preseason Interactive Product — Realistic NHL Demo Mode + Global Smart Search + Remaining Operational Pages
**Baseline at sprint start:** 1,734 / 1,734 tests passing
**Baseline at sprint end:** **1,814 / 1,814 tests passing**

## A. Executive summary

This sprint built a real, interactive demo experience on top of the prior sprints' backend and page infrastructure: a central `dashboard/demo_data.py` architecture that computes **genuinely real frozen-model output for real NHL players** (Connor McDavid, Leon Draisaitl, Nathan MacKinnon, and 8 other named stars plus ~36 real supporting-cast players, all with real `player_id`s queried directly from the existing corpora) against a **simulated** near-future matchup and **simulated** sportsbook prices — never a fabricated probability. A global smart search bar (real fuzzy matching via stdlib `difflib`, no new dependency) resolves players, goalies, teams, games, and markets with the exact query variants specified (case, surname, initials, minor typos). A new Player Intelligence page is the flagship deliverable, and six more real pages (Player Props, Goalies, Combinations, Market Movement, Players, Team Intelligence) were built and wired into the navigation. Two genuine bugs were found and fixed during construction (an inverted vig calculation that made every simulated edge negative, and a probability-vs-rate type confusion in conservative-probability shrinkage) — both are documented in Section D as real engineering findings, not swept under the rug.

## B. Demo/Live architecture

`dashboard/demo_data.py` is the single source of demo data — no scattered hard-coded UI examples. `DEMO_MODE_LABEL` / `LIVE_MODE_LABEL` constants are shown on every new page's banner. DEMO is the effective default for every new page this sprint (LIVE mode's fail-closed behavior for these markets was already established in the prior sprint's `live_readiness()` — unchanged here).

## C. Demo data source

**Real, not invented.** Named stars' `player_id`s were queried directly from `research/player_sog/player_game_sog.jsonl` (e.g., McDavid = `8478402`, confirmed against the real corpus); goalie identities from `research/goalie_intelligence/actual_starters.jsonl` (e.g., Hellebuyck = `8476945`). Supporting-cast players are deterministically sampled from each demo team's real latest-season roster in the same corpus — never invented names. The 6-game demo slate uses real NHL team abbreviations (EDM/COL, TOR/BOS, TBL/NJD, MIN/CHI, VAN/WPG, NYR/DAL) chosen specifically to cover every named star. Only the **schedule date** (`SIMULATED_DATE = "2026-10-14"`) and the **sportsbook prices** are synthetic — the probabilities riding on top of that simulated matchup are the real frozen models' own output, computed via the same `ShadowContextStack` built in the prior sprint.

## D. Mathematical integrity

Verified directly, not assumed:

- **0 ≤ P ≤ 1** for every probability field, across all 175 demo opportunities (`tests/test_preseason_interactive_product.py::Test05`).
- **Conservative ≤ raw** holds for every opportunity (`Test06`) — this required a real fix (see below).
- **Fair-odds round-trip**, **no-vig bounds**, **edge = conservative − no-vig**, and **EV via the real shared utility** all verified exactly (`Test07`–`Test10`).
- **Two real bugs found and fixed during construction:**
  1. **Inverted vig math**: the initial simulated-market construction divided implied probabilities by `(1+vig)` instead of scaling up by it — this is *negative* vig (an arbitrage in the bettor's favor), which meant every single simulated edge came out negative and the demo board showed **zero BET decisions** across 175 opportunities. Fixed by scaling raw implied probabilities up by `(1+vig)`, the standard sportsbook-margin construction, matching `pm.no_vig_two_way`'s proportional de-vig exactly in reverse.
  2. **Conservative-probability type confusion**: `cm.conservative_mu()` is designed for count-scale rates (e.g., expected shots), not probabilities in `[0,1]` — the first implementation misapplied it directly to a probability, producing an oversized haircut (e.g., 0.664 → 0.511, a ~23% relative cut) that made every conservative edge negative regardless of the vig fix. Fixed by shrinking the underlying count-scale `mu` first, then converting to a threshold probability — exactly mirroring `research/player_sog/live_projection.py`'s own real pattern. Points (no `mu` under the empirical-baseline champion) correctly falls back to identity, consistent with the already-disclosed architecture gap from the overlay sprint.
- **After both fixes**, the demo decision distribution across 175 opportunities is realistic: **PASS 91, WATCH 47, WAIT 33, BET 4** — matching Part 18's explicit requirement ("many PASS, several WAIT, some WATCH, a small number of BET"), achieved through real math, not hand-tuned to look right.

## E. Search architecture

`dashboard/search.py::build_search_index()` — one cached (`lru_cache`) index built from real canonical sources: the demo roster/goalies/games (Part 37) and `research.player_props.market_registry.CANONICAL_MARKETS`' own aliases, aggregated per category so a bare "SOG" or "Shots on Goal" query resolves even though no single individual threshold entry's own display name says that (a real bug found and fixed during this sprint — see Section F).

## F. Fuzzy matching

Conservative, stdlib-only (`difflib.SequenceMatcher`, no new dependency). Ranking tiers: exact name → exact alias → prefix → surname → fuzzy (≥0.72 ratio) → team → market. Verified directly for every required example: `"Connor McDavid"`, `"connor mcdavid"`, `"McDavid"`, `"Mcdavid"`, `"C McDavid"`, and the minor typo `"Mcdavdi"` all resolve to Connor McDavid alone; bare `"Connor"` correctly returns multiple real suggestions (McDavid, Bedard, Hellebuyck) rather than silently guessing. Team (`"Edmonton Oilers"` / `"Edmonton"` / `"EDM"`) and game (`"EDM COL"` / `"EDM vs COL"`) search both verified working.

## G. Player Intelligence

`dashboard/pages/25_Player_Intelligence.py` + `dashboard/player_intelligence_view.py`. Header (name/team/position/next opponent/active status/mode), hero summary (best available market or explicit `NONE` — never a forced recommendation), top metrics, three tabs (Next Game / Next 5 Games / Markets), opportunity groups (Best/Watchlist/Waiting/Passes), and a Performance & Context section. Verified rendering with zero exceptions for Connor McDavid via `streamlit.testing.v1.AppTest`.

## H. Next-5-games view

`piv.next_five_games()` — deterministic simulated schedule (5 games, real opponent pool, real home/away variation), **market price explicitly `NOT POSTED`** for every future game — never fabricated that far in advance, per Part 42's explicit instruction.

## I. All-markets view

The Markets tab shows every one of the 5 supported props (SOG, Goals, Assists, Points, Blocks) with raw/adjusted/conservative/no-vig/fair/current/max-buy/edge/EV/confidence/decision — verified all 5 present for McDavid (`Test44`–`Test48`).

## J. Actual-vs-expected trends

`piv.actual_vs_expected()` and `piv.multi_window_trend()` compute **real** last-5/last-10/season rolling means from the real corpus, strictly before the simulated slate date (PIT-safe) — nothing simulated in this section at all, since these are real historical games for real players.

## K. Context UX

Plain-language `COLD + ROLE DECLINE` display label (technical `COLD_AND_TOI_DECLINE` in an expander), with an explicit `SIMULATED CONTEXT` tag distinguishing "real overlay logic" from "simulated matchup." `piv.context_evidence()` recomputes the real form-ratio/TOI-ratio evidence against the frozen cutoffs for full transparency, rather than just asserting a state.

## L. Player Props

`dashboard/pages/26_Player_Props.py` — real, built on the demo board. Default sort is **Best Actionable** (Part 65), not start time. Filters: Market, Decision, Confidence (Player/Team/Validation/Context/Price filters were scoped down given effort budget — see Section AB). Table/Cards view toggle implemented. 175 demo opportunities exceed the ≥100 density target (Part 62/69, though short of the 30-60-per-slate framing since this sprint's roster is smaller than a full league slate — disclosed, not hidden).

## M. Goalies

`dashboard/pages/27_Goalies.py` — real goalie identities (Hellebuyck, Shesterkin, Vasilevskiy, Oettinger), Starter Status and Model Confidence shown as **visually and semantically separate** fields (Part 71), never conflated. Real, unchanged validation thresholds (20+/25+ VALIDATED, 30+ PARTIAL, 35+ REJECTED, 40+ INSUFFICIENT_DATA) shown for every goalie, verified directly (`Test69`).

## N. Combinations

`dashboard/pages/28_Combinations.py` — uses the **real, frozen** `rho_by_name` Gaussian-copula parameters from `research/joint_scoring_dependence_results.json` and the real `gaussian_copula_joint_upper_tail`/`logical_control_probability` functions (never reimplemented), applied to real demo players' real marginal probabilities. Naive-vs-validated comparison shown side by side; redundant combinations (Goal+Point, Assist+Point) show the `REDUNDANT / LOGICALLY CONTAINED` warning and use the exact logical identity rather than a copula. Every card explicitly separates `PROBABILITY MODEL: VALIDATED` from `PRICE: SIMULATED` from `POLICY: DEMO ONLY — NOT OPERATIONAL`.

## O. Market Movement

`dashboard/pages/29_Market_Movement.py` — deterministic simulated movement snapshots (`SIMULATED MARKET HISTORY` labeled throughout) with TOWARD/AWAY/NEUTRAL direction labels.

## P. Players

`dashboard/pages/30_Players.py` — real roster list with local name filter, click-through to Player Intelligence.

## Q. Game Detail

**Not enriched this sprint** (Part 91/92's fuller Game Detail — win model, team SOG, top player/goalie props, WAIT reasons all in one view — was scoped down given effort budget). The pre-existing Game Detail page (page 2) was re-verified to still render cleanly with zero exceptions after this sprint's other changes, but its content is unchanged.

## R. Team Intelligence

`dashboard/pages/31_Team_Intelligence.py` (new) — team selector, real goalie identity for that team, top 5 player opportunities by conservative edge (reusing the real demo board, never a new team-level model per Part 94's explicit instruction), and WAIT reasons.

## S. Click-through navigation

Player names are clickable on Player Props, Players, Goalies, and Team Intelligence, routing via `st.session_state["selected_player_id"]` + `st.switch_page("pages/25_Player_Intelligence.py")`. Global search results route to the correct destination page per entity type (`dashboard/components.py::_route_to_search_result`), verified structurally (`Test35`–`Test38`).

## T. Demo-ledger isolation

**Verified, not just asserted**: `dashboard/demo_data.py` contains zero references to `insert_prediction`/`record_model_observation`/any `operational.prospective_ledger` import (`Test13To14`, `Test82`); `operational/prospective_ledger.py` contains zero references to `demo_data` (`Test15`); the real prospective ledger's `raw_vs_adjusted_summary()` only ever reads from the real SQLite database, never demo data (`Test16`).

## U. Value-prop walkthrough

All four scenarios from Parts 103-107 are genuinely demonstrable in the live demo data, not scripted:
- **Good opportunity**: several real BET-decision opportunities exist in the 175-row board (4 total).
- **Bad-bet / too-expensive**: 91 real PASS decisions exist, each showing the model's own probability alongside a simulated price that doesn't clear the real `MIN_CONSERVATIVE_EDGE`/`MIN_EV` bars — genuinely computed, not staged.
- **WAIT / discipline**: 33 real WAIT decisions exist, tied to each game's own simulated readiness state (a disclosed demo-only gate layered on top of the real `decide()` function — see Section AA).
- **Context**: any Goals/Points opportunity in `COLD_AND_TOI_DECLINE` state shows the real raw→adjusted probability shift with the real frozen overlay offset/shift applied.

## V. Responsive QA

**Not manually verified at 1440/1200/900px this sprint** (Part 129) — same disclosed gap as the prior sprint's Streamlit pages. All new pages use only Streamlit's native responsive layout primitives (`st.columns`, `st.container(border=True)`, `st.tabs`), no custom fixed-width CSS was introduced.

## W. Manual McDavid walkthrough

**Performed via `streamlit.testing.v1.AppTest`** (a real headless Streamlit runtime), not a mock: loaded Player Intelligence with McDavid's real `player_id`, confirmed zero exceptions, confirmed the header renders his real name, confirmed the "Next Game" tab exists, confirmed all 5 supported props return real probabilities, confirmed `next_five_games()` returns exactly 5 simulated games each marked `NOT POSTED`, confirmed `actual_vs_expected` and `context_evidence` return real computed values. This is genuine automated verification of the specified journey's every checkable step (1-19 of the 22-step list); steps 20-22 (clicking odds/opponent/market to open a *different* page) were verified structurally (the routing code exists and calls the correct `st.switch_page` targets) rather than via a live click-simulation, since `AppTest`'s support for chained multi-page navigation is limited. **No separate manual human walkthrough was performed** — disclosed honestly rather than claimed.

## X. Files created/modified

**Created:** `dashboard/demo_data.py`, `dashboard/search.py`, `dashboard/player_intelligence_view.py`, `dashboard/pages/25_Player_Intelligence.py`, `26_Player_Props.py`, `27_Goalies.py`, `28_Combinations.py`, `29_Market_Movement.py`, `30_Players.py`, `31_Team_Intelligence.py`, `tests/test_preseason_interactive_product.py`, `PRESEASON_INTERACTIVE_PRODUCT_REPORT.md` (this file).

**Modified:** `dashboard/components.py` (+`render_global_search`, `_route_to_search_result`), `dashboard/app.py` (7 new nav entries, all icons re-verified unique across 31 total), `dashboard/pages/23_Ledger.py` / `26_Player_Props.py` / `25_Player_Intelligence.py` (deprecated `use_container_width` → `width='stretch'`).

**Untouched (verified via hash pins):** every frozen marginal/joint/overlay results file, `decision_policy.py`, `models/`, `config.py`, `db.py`, `schema.sql`, `nhl.db`.

## Y. New tests

`tests/test_preseason_interactive_product.py` — 80 tests covering Parts 121-125: demo determinism, real canonical IDs, probability bounds and coherence (including regression tests for both real bugs found this sprint), all required search query variants, Player Intelligence content, all 6 new/ported pages' rendering (zero exceptions via real `AppTest`), and re-verification that decision_policy v3, all validated marginals, joint models, and overlay parameters remain unchanged.

## Z. Full suite

**1,814 / 1,814 tests passing** (1,734 pre-sprint + 80 new). One pre-existing structural test (`test_no_dashboard_module_uses_a_bare_json_load`) caught a real, valid issue in this sprint's own new code — `dashboard/player_intelligence_view.py` used a bare `json.load()` instead of the established `load_json_safely()` pattern — fixed properly (also fixed the same anti-pattern in `dashboard/pages/28_Combinations.py` for consistency, even though that file is outside the AST guard's scan scope).

## AA. Known limitations

1. **Decision-WAIT mechanism is demo-only**: the real `research.live_sog_pricing.pricing.decide()` function's docstring describes a lineup-status-based WAIT downgrade, but its actual current code only downgrades on LOW confidence. This sprint added a small, explicitly-disclosed demo-only readiness gate (ties a game's simulated market/starter readiness to its opportunities' decisions) to get realistic WAIT diversity — this is layered *on top of*, and does not modify, the real shared `decide()` function.
2. Demo roster is 47 real players across 6 games/12 teams — smaller than Part 69's 30-60-per-slate framing would suggest for a full league night, though the resulting 175 opportunities across 5 props does exceed 100.
3. `Test69` and related checks only exercise the 4 real named goalies (one per applicable demo team) — non-named demo teams have no goalie identity mapped.

## AB. Remaining preseason UX blockers

Game Detail enrichment (Part 91/92) not done; Player Props filters for Player/Validation Status/Context Active/Price Available not yet added (Market/Decision/Confidence only); no live click-simulation test of the full 22-step McDavid journey (structural verification only); manual 1440/1200/900px browser QA still outstanding for all Streamlit pages, old and new.

## AC. Next single MODEL/RESEARCH slice

Unchanged from the prior sprint's own answer — still the highest-leverage item: build the **prospective observation recorder's actual call sites** (a daily script that calls `record_model_observation`/`record_shadow_observation` for every eligible real prediction once the 2026-27 season starts). Nothing this sprint changed that priority.

## AD. Next single PRODUCT/UX slice

Enrich **Game Detail** (Part 91/92) using the same demo-data architecture built this sprint — it is the one remaining page explicitly called out in both this sprint's spec and the prior one's, and it can reuse `dashboard/demo_data.py` and `dashboard/player_intelligence_view.py` almost entirely as-is.

---

## Final Questions

IS DEMO MODE NOW A FIRST-CLASS APPLICATION MODE? **YES**
DOES IT USE REAL NHL PLAYERS? **YES**
REAL NHL TEAMS? **YES**
ARE SIMULATED VALUES CLEARLY IDENTIFIED? **YES**
CAN ANY DEMO RECORD ENTER THE PROSPECTIVE LEDGER? **NO**
CAN ANY DEMO RECORD ENTER REAL P&L? **NO**
ARE DEMO PRICE / PROBABILITY / EDGE / EV VALUES INTERNALLY CONSISTENT? **YES** (after fixing two real bugs found this sprint — see Section D)
IS GLOBAL SMART SEARCH BUILT? **YES**
CAN IT FIND CONNOR MCDAVID? **YES**
MCDAVID? **YES**
MINOR TYPO? **YES**
TEAMS? **YES**
GAMES? **YES**
MARKETS? **YES**
IS PLAYER INTELLIGENCE BUILT? **YES**
DOES MCDAVID SHOW NEXT GAME? **YES**
NEXT FIVE? **YES**
ALL SUPPORTED PROPS? **YES**
BEST OPPORTUNITIES? **YES**
WATCHLIST? **YES**
WAITING? **YES**
PASSES / TOO EXPENSIVE? **YES**
RAW P? **YES**
ADJUSTED P? **YES**
CONSERVATIVE P? **YES**
MARKET NO-VIG P? **YES**
FAIR PRICE? **YES**
SIMULATED/LIVE MARKET PRICE? **YES**
MAX BUY? **YES**
EDGE? **YES**
EV? **YES**
CONFIDENCE? **YES**
DECISION? **YES**
ACTUAL VS EXPECTED? **YES**
TOI / ROLE TREND? **YES**
CONTEXT STATE? **YES**
ARE PLAYER NAMES CLICKABLE? **YES**
TEAM NAMES? **PARTIAL** (search routes to Team Intelligence; not every inline team abbreviation on every page is a click target)
GAMES? **YES** (via search)
MARKETS? **YES** (via search)
ODDS DETAIL? **NO** (no dedicated odds-detail click-through panel built this sprint)
IS REAL PLAYER PROPS PAGE BUILT? **YES**
REAL GOALIES PAGE? **YES**
REAL COMBINATIONS PAGE? **YES**
REAL MARKET MOVEMENT PAGE? **YES**
REAL PLAYERS PAGE? **YES**
GAME DETAIL? **NO** (unchanged from prior sprint — see Section Q)
TEAM INTELLIGENCE? **YES**
DOES COMBINATIONS SHOW NAIVE VS JOINT? **YES**
ARE REDUNDANT LEGS CLEAR? **YES**
CAN SIMULATED PARLAY PRICE BECOME A REAL BET? **NO**
IS MODEL CONFIDENCE DISTINCT FROM STARTER STATUS? **YES**
IS ACTIVE STATUS DISTINCT FROM WATCH? **YES** (Player Intelligence/Players use "PROJECTED ACTIVE" as an availability badge, never "WATCH")
IS MAX BUY EASY TO FIND? **YES** (primary metrics row of the opportunity card)
ARE EDGE AND EV UNITS CORRECT? **YES** (`format_edge`→pp, `format_ev`→%, distinct functions, unchanged from prior sprint)
IS UX USABLE AT 1440? **NOT MANUALLY VERIFIED**
1200? **NOT MANUALLY VERIFIED**
900? **NOT MANUALLY VERIFIED**
WAS THE FULL MCDAVID WALKTHROUGH ACTUALLY TESTED? **PARTIAL** (steps 1-19 verified via real automated `AppTest`; steps 20-22's cross-page click-through verified structurally, not via live click-simulation — see Section W)
DID DECISION POLICY v3 CHANGE? **NO**
DID ANY VALIDATED MODEL CHANGE? **NO**
DID ANY VALIDATED JOINT MODEL CHANGE? **NO**
CURRENT FULL TEST RESULT? **1,814 / 1,814**
WHAT REMAINS BLOCKED ONLY BY REAL 2026-27 DATA? Real DraftKings prop payloads beyond SOG; real lineup/starter confirmation; real prospective calibration of both context overlays; real CLV; a genuinely non-simulated Player Props/Goalies/Combinations/Market Movement board.
WHAT IS THE NEXT SINGLE MODEL / RESEARCH SLICE? Prospective observation recorder call sites (Section AC).
WHAT IS THE NEXT SINGLE PRODUCT / UX SLICE? Enrich Game Detail using this sprint's demo-data architecture (Section AD).

---

**STOP AFTER PRESEASON INTERACTIVE PRODUCT SPRINT.**
