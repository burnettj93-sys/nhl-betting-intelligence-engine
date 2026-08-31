# UX Audit and Redesign Report

## Per-page audit (real dashboard, 20 pages)

| # | Page | Current state | Problems | Completed improvements | Remaining work | Priority |
|---|---|---|---|---|---|---|
| 1 | Game Slate | RESEARCH, historical-only | None found | — | Low | — |
| 2 | Game Detail | RESEARCH | None found | — | Low | — |
| 3 | Team Ratings | RESEARCH | None found | — | Low | — |
| 4 | Model Performance | RESEARCH | None found | — | Low | — |
| 5 | Research Lab | RESEARCH | None found | — | Low | — |
| 6 | Goalie Intelligence | RESEARCH | None found | — | Low | — |
| 7 | Player SOG Research | RESEARCH | None found | — | Low | — |
| 8 | **Live SOG Markets** | **OPERATIONAL** | Fragile direct dict-key access on cached board rows (no `.get()`); shares nav visual weight with 17 research pages | — | Defensive key access; distinct nav tier | **High** |
| 9 | Data Status | INFRASTRUCTURE | None found | — | Low | — |
| 10 | Prop Registry | RESEARCH/reference | None found | — | Low | — |
| 11 | Player Points Research | RESEARCH | None found | — | Low | — |
| 12 | Player Goals Research | RESEARCH | None found | — | Low | — |
| 13 | Play-by-Play Status | INFRASTRUCTURE | None found | — | Low | — |
| 14 | Player SOG by Period | RESEARCH | None found | — | Low | — |
| 15 | Team Goals by Period | RESEARCH (NOT VALIDATED) | None found | — | Low | — |
| 16 | Goalie Saves Research | RESEARCH | None found | — | Low | — |
| 17 | Team SOG Research | RESEARCH | None found | — | Low | — |
| 18 | Joint Shot/Workload | RESEARCH | None found | — | Low | — |
| 19 | Joint Scoring Dependence | RESEARCH | None found | — | Low | — |
| 20 | Player Context State | RESEARCH | **Two unhandled `StopIteration` risks** (bare `next()` with no default) | **Fixed this sprint** — both now use `next(..., None)` with graceful fallback | — | Done |

**Duplicate functionality across pages**: 17 of 20 pages share a near-identical skeleton (status badge → validation metrics → live-projection tool → representative examples) with duplicated *Streamlit boilerplate* (not math — the underlying probability/statistics functions are already correctly centralized in `research/*`). **Recommended destination**: a shared "prop research page" template/component, collapsing this repetition. **Keep/merge/move**: keep all 20 pages' content; merge the *rendering skeleton* into one reusable component in a follow-up UX slice.

## Web prototype architecture

Built this sprint at `dashboard_prototype/` — self-contained static HTML/CSS/JS, no build step, no backend, no external CDN dependency (system font stacks only, per the "no external CDN unless necessary" instruction). Three files: `index.html` (shell + nav), `styles.css` (design tokens: dark quant-terminal palette, one restrained teal accent, system-font UI stack + monospace tabular-nums for every number), `app.js` (all mock data, all render functions, hash-based client-side router, filter/sort state, drawer/modal).

## Pages implemented

All 10 required: Today, Games, Player Props, Goalies, Combinations, Market Movement, Players (+ Player Detail sub-view), Bet/Observation Ledger, Model Health, Research.

## Interactions implemented

Clickable sidebar nav (hash-routed, keyboard-accessible via `role="button" tabindex="0"` + Enter/Space handler added this sprint after an accessibility pass), Today-page demo-state toggle (normal/no-games/stale), Player Props filter bar (market/confidence/decision/validated-only/overlay-only checkboxes, all wired and verified live in-browser), sort dropdown (start time/edge/EV/confidence), clickable game rows opening a detail drawer, clickable player rows opening a full Player Detail view, hover tooltips on probability-stage labels (Raw P / Adjusted P / Conservative P / Market No-Vig P) and on the context-overlay tag, `<details>` expanders for goalie model limitations, Escape-to-close on the drawer.

## Responsive behavior

Verified in-browser this sprint at 1440px (full layout, 4-column grids) and mobile width (684px, single-column stacking via `@media` breakpoints at 1200px and 900px). All grids (`grid-4`/`grid-3`/`grid-2`) collapse progressively; tables scroll horizontally in their own `.table-wrap` container rather than the page body scrolling sideways.

## Demo-data labeling

A persistent top banner ("⚠ UX PROTOTYPE — DEMO DATA ONLY") on every view. Every card/table additionally carries a `DEMO DATA` tag or an explicit "DEMO / UX ONLY" notice (Market Movement view). All player/team names are clearly fictional (e.g. "J. Fennimore," "CST"/"NOR" — no real NHL player or team names used anywhere). Model Health page is the one deliberate exception: it shows **real** current model statuses (sourced from `research/model_registry.py`), explicitly labeled as such, since that page's entire purpose in the real product is to be a status board — using fake statuses there would defeat the point of reviewing it.

## Known prototype limitations

No backend — filters/sorts operate on an in-memory array, not a real API. No persistence (localStorage was deliberately not used to keep the prototype maximally simple to launch). Player Detail's "recent games" sparkline is illustrative bar heights, not real game logs. No real accessibility audit beyond the keyboard-nav pass added this sprint (screen-reader testing not performed).

## Exact launch instructions

```bash
cd "dashboard_prototype"
python3 -m http.server 8765
```
Then open `http://localhost:8765/index.html` in a browser. A local server (rather than double-clicking `index.html` directly) is used because some browsers restrict `fetch`/module behavior under the `file://` origin — `http://` avoids that class of issue entirely, and the command above has no dependencies beyond Python 3 (already required by this project).

## Files created

`dashboard_prototype/index.html`, `dashboard_prototype/styles.css`, `dashboard_prototype/app.js`, `dashboard_prototype/assets/` (empty, reserved), `WEB_PROTOTYPE_REVIEW_CHECKLIST.md`.

## Recommended next visual changes

1. Port `opportunityCard()` into real Streamlit (`PRESEASON_ENGINE_READINESS_REPORT.md` Section BD — the single highest-leverage next step).
2. Build the shared status-banner component to replace 17 hand-written near-duplicate banners in the real dashboard.
3. De-duplicate the two repeated nav icons (🏒, 🥅) in `dashboard/app.py`.
4. Give Live SOG Markets (the one operational page) a distinct nav tier/section, matching the prototype's Operate/Track/Research grouping.

## Remaining UX priorities (ranked, top 15)

1. Port opportunity card to real Streamlit — highest daily-frequency, highest wrong-interpretation risk if raw/adjusted/conservative stages aren't shown side by side.
2. Shared status-banner component — reduces inconsistency risk across 17 pages.
3. Fix Live SOG Markets' fragile dict-key access (Section per-page audit, page 8).
4. Real `SYSTEM_HEALTH` object powering a Today-page health strip in the real dashboard.
5. Nav re-tiering (Operate / Track / Research groups, matching the prototype).
6. Real ledger page (currently prototype-only).
7. Real Combinations page with redundant-leg warning (currently only in prototype).
8. Real Player Detail consolidated view (currently scattered across per-prop research pages).
9. De-duplicate nav icons.
10. Table density audit on the real dashboard's widest tables (several research pages show many columns at once).
11. Terminology standardization pass (Raw/Adjusted/Conservative/No-Vig probability naming, consistently, everywhere).
12. Empty-state consistency pass — most pages already have one; standardize the wording/visual treatment.
13. Add the two-source freshness display (data timestamp + odds timestamp) to every page that shows a probability.
14. Confirm no page can show a real BET recommendation during the current offseason (dashboard-wide check, not yet formally tested).
15. Accessibility pass on the real Streamlit dashboard (Streamlit's own component library limits how much is controllable here — scope after the above).
