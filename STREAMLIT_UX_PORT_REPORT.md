# Streamlit UX Port Report

Scope note up front: this sprint ported the prototype's information architecture, decision hierarchy, and reusable components into **five real pages** (Today, Model Health, Ledger, Research Hub, and the Live SOG Markets fix/port) plus the shared component/formatting layer they all use. Player Props, Goalies, Combinations, Market Movement, Players, and Player Detail as **dedicated operational pages** were **not built this sprint** — building them with genuine substance (not empty shells) requires either real live-market data across more than SOG, or a materially larger effort budget than remained. The prototype (`dashboard_prototype/`) remains the visual/product reference for those six pages' eventual real build. This is stated plainly here rather than claimed as done.

## A. Visual hierarchy changes

`dashboard/components.py::render_opportunity_card()` implements the addendum's exact priority order: decision/readiness badge (top-right, highest contrast) → primary metrics row (Current Odds, Max Acceptable Price, Conservative Edge, Conservative P — via `st.metric`, Streamlit's highest-contrast numeric widget) → secondary row (Raw/Adjusted/No-Vig/Fair Odds, as captions — visually quieter) → confidence pill → compact drivers/risks (2 max shown, rest behind an expander) → compact freshness footer. Raw P is deliberately **not** the largest number on the card, per addendum Section B2.

## B. Today-page changes

`dashboard/pages/21_Today.py` (new): System Health strip using real `operational.system_health.build_system_health()` (not demo pills), today's real NHL slate (via the existing `data_access.compute_baseline_predictions()`), per-game SOG readiness via the real `live_readiness()` function, a "Top Actionable Opportunities" section that honestly shows the no-live-markets empty state rather than any fabricated price (only SOG has a live contract), and a "Waiting on Data/Confirmation" section built from real health items in WAITING/STALE state — not the prototype's mock toggle buttons.

## C. Opportunity-card changes

Ported and refined per addendum Section B — see Section A above. Context-adjustment banner uses the plain-language label `CONTEXT ADJUSTMENT ACTIVE — COLD + ROLE DECLINE` with the exact machine state name (`COLD_AND_TOI_DECLINE`) and the corrected PIT-safe wording (addendum Section AE: "PIT-safe historical TOI/role decline," not "confirmed TOI decline," to avoid implying current-game confirmation) available in the hover tooltip.

## D. Actionability sorting

**Not implemented this sprint** — no real Player Props page exists yet to sort. The `render_opportunity_card()` component itself has no opinion on sort order; that logic belongs to whichever page calls it, and is scoped into the next UX slice alongside the real Player Props page.

## E. Player Props changes

**Deferred** (see scope note). The prototype's filter bar and sort logic remain the reference design.

## F. Goalie certainty/confidence separation

**Deferred** — no real Goalies page was built this sprint. The principle (starter certainty ≠ model confidence, both shown independently) is documented here for the next slice and is already respected in `render_opportunity_card()`'s data model (`confidence` and any future `starter_status` field are separate keys, never conflated into one badge).

## G. Combinations visualization

**Deferred** — no real Combinations page was built this sprint.

## H. Redundant-leg UX

**Deferred** at the page level. The underlying logic it would visualize (`detect_redundant_leg`, `IMPLICATION_GRAPH`) is unchanged and already real (see `research/joint_scoring_dependence/logical_implication_registry.py`).

## I. Players-page enrichment

**Deferred**.

## J. Player-detail trend/change panel

**Deferred**.

## K. Market-movement changes

**Deferred** — genuinely blocked on real historical price snapshots existing (only single-point-in-time captures exist today, no movement history to show).

## L. Ledger tab architecture

`dashboard/pages/23_Ledger.py` (new): four real tabs (Real Bets, Model Observations, Shadow Observations, Historical Research) backed by `operational.prospective_ledger`. Real Bets tab shows "NO REAL BETS RECORDED" (not a hypothetical P&L) when empty — verified directly in `tests/test_operational_infrastructure.py::Test13PnlSeparation`. Shadow tab shows raw-vs-adjusted Brier per prop once observations with known outcomes exist.

## M. System/model health changes

`dashboard/pages/22_Model_Health.py` (new): one row per real `research.model_registry.MODEL_REGISTRY` entry — status badge, operational-status badge (using the new `SHADOW_VALIDATED` style), validated/partial/rejected/insufficient thresholds, and an expandable technical-detail panel (confidence behavior, PIT status, upstream/downstream, report filename, live-computed freeze hash). Verified every registry entry's display name actually renders (`tests/test_operational_infrastructure.py::Test36`).

## N. Status taxonomy

`dashboard/components.py::STATUS_BANNER_STYLES` and `DECISION_COLORS` are two **visually and semantically distinct** style families (addendum Section L): decision badges (BET/WATCH/WAIT/PASS) use one palette; model-validation badges (VALIDATED/PARTIAL/RESEARCH/REJECTED/INSUFFICIENT_DATA/SHADOW_VALIDATED/NOT_OPERATIONAL) use a separate, deliberately different-styled palette — never rendered as if they were the same kind of status.

## O. Price/status hierarchy

`format_american_odds(None)` renders `NO LIVE PRICE`, never a fake number — verified directly (`tests/test_operational_infrastructure.py::Test34`). `dashboard/formatting.py` centralizes every number format per Section 70's exact function list.

## P. Responsive QA

**Not manually verified this sprint** for the new Streamlit pages — Streamlit's own layout primitives (`st.columns`, `st.container(border=True)`) are already responsive by framework default, and no custom CSS/fixed-width layout was introduced that could break at narrower widths. A manual pass at 1440/1200/900px on the five new/changed pages is real, scoped follow-up (the HTML prototype, not these Streamlit pages, received the actual manual 1440/1200/900px visual QA this sprint — see `UX_AUDIT_AND_REDESIGN_REPORT.md`).

## Q. Screenshots reviewed / manual observations

No manual screenshots were taken of the new Streamlit pages this sprint. Instead, every new/changed page was verified via `streamlit.testing.v1.AppTest` — a real headless Streamlit runtime, not a mock — confirming zero exceptions and expected content on render (Today, Model Health, Ledger, Research Hub, Live SOG Markets, and `app.py` itself with its new sectioned navigation). This is a legitimate, real verification method, but is not the same as human visual review; recommend an actual `streamlit run` pass before treating these pages as launch-ready.

## R. Remaining UX issues

1. Player Props, Goalies, Combinations, Market Movement, Players, Player Detail still need real operational builds (this sprint's single largest remaining UX gap).
2. Actionability-based default sort doesn't exist yet anywhere real (only in the HTML prototype).
3. `FRESHNESS_TTL_HOURS` is centralized but not yet consumed by any page's display logic.
4. No real manual browser QA at 1440/1200/900px for the five new Streamlit pages.
5. `_MARKET_FAMILY_TO_MODEL_ID` in `live_readiness.py` covers 7 families; extending to the remaining 135 canonical markets is real follow-up work.
