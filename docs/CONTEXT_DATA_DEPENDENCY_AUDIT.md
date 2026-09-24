# Context Data Dependency Audit

**Date:** 2026-09-24. **Scope:** starting goalie, injury status, line assignment, PP1/PP2 assignment, and roster status — across every current model and recommendation rule.

## The headline finding

**There is currently no live, real recommendation-generation pipeline at all.** `dashboard/eligible_bets.py::all_opportunities()` — the function every real page (`21_Today.py`, `2_Game_Detail.py`, `31_Team_Intelligence.py`, `25_Player_Intelligence.py`, `paper_performance_view.py`) calls to build BET/WATCH/WAIT recommendations — unconditionally sources every input from `dashboard/demo_data.py` (`dd.build_demo_goalies()`, `dd.build_demo_opportunities()`, `dd._confidence_for()`). This is **not a hidden bug** — `21_Today.py`'s own module docstring says plainly: *"the main demo landing page... the flagship DEMO experience... Every demo price is clearly labeled SIMULATED MARKET / DEMO ONLY."* Real operational state (System Health, the real NHL slate, real odds collection) is shown separately, higher up the same page.

**The practical consequence:** every "context input" question below currently has a demo answer, not a real one, because the real recommendation path has never been built — there has never been a real market to build it against (Section 5 of the Production Readiness Audit: DraftKings has posted nothing before 2026-09-29). This audit is therefore as much a **readiness checklist for the real pipeline that doesn't exist yet** as it is a review of hidden assumptions in code that does.

## Per-market context dependency

| Market | Starting goalie | Injury status | Line assignment | PP1/PP2 | Roster status |
|---|---|---|---|---|---|
| MONEYLINE (Elo) | NOT_USED | NOT_USED | NOT_USED | NOT_USED | NOT_USED |
| PLAYER_SOG | NOT_USED (opponent goalie not a model input) | NOT_USED | NOT_USED | OPTIONAL — `PLAYER_SOG_PP_ROLE_OVERLAY` is a real, shadow-only overlay that *does* use PP role/transition state, but never affects a real probability or decision (per `research/model_registry.py`'s own `low_policy: "SHADOW ONLY"`) | NOT_USED |
| GOALS / ASSISTS / POINTS | NOT_USED directly | NOT_USED | NOT_USED (no linemate feature exists anywhere in the codebase — confirmed, see Production Readiness Audit) | NOT_USED | NOT_USED |
| BLOCKED_SHOTS | NOT_USED | NOT_USED | NOT_USED | NOT_USED | NOT_USED |
| TEAM_SOG | NOT_USED | NOT_USED | NOT_USED | NOT_USED | NOT_USED |
| GOALIE_SAVES | **REQUIRED, in principle** — `research/goalie_intelligence/features.py` is a real, honest starter-*prediction* model built from historical start-share/streak patterns (not a live confirmed feed, since none exists). It correctly returns `None` (never a fabricated default) when there's insufficient history. **But**: the live decision code that actually prices a Saves bet (`dashboard/eligible_bets.py::build_goalie_saves_opportunities()`) does not gate on starter certainty at all today — it only skips a goalie when `expected_saves is None`, and `expected_saves` currently only ever comes from demo data. There is no real code path yet where a low-certainty starter projection would downgrade or block a real Saves recommendation. | NOT_USED | NOT_USED | NOT_USED | Implicitly assumed via starter projection |
| Game Edge Parlay (any leg) | Inherits whatever its component legs use (above) | Inherits | Inherits | Inherits | Inherits |
| Fantasy (`fantasy/projections/goalie_value.py`) | REQUIRED for `start_certainty`, and handled honestly — its own comment states *"the engine's own real starter_probability -- never a live-confirmed feed"* and ships an explicit `HONEST_STARTER_DISCLOSURE` | NOT_USED | NOT_USED | NOT_USED | NOT_USED |

## Does anything silently manufacture confidence?

Checked explicitly for each of the user's named failure modes:

| Hidden-assumption pattern | Found anywhere? |
|---|---|
| Assumes healthy when injury status unknown | **No occurrence found.** No live code path reads or infers injury status at all yet (there's nothing to wrongly default). |
| Assumes rostered when roster status unknown | **No occurrence found** in decision code. `ingest/nhl_api.py::record_roster_status()` is a real, unused write path waiting for a real source. |
| Assumes prior line assignment continues indefinitely | **No occurrence found** — no line-assignment feature exists anywhere in this codebase, so nothing can carry one forward, stale or otherwise. |
| Assumes a specific goalie | **No occurrence found** in the real starter-prediction model (`goalie_intelligence/features.py`) — it honestly returns `None` on insufficient data. The **demo** path (`dashboard/demo_data.py`) hardcodes a plausible-looking `starter_probability: 0.82` — clearly synthetic, clearly labeled, but worth flagging explicitly: **when the real pipeline is built, it must not reuse or fall back to this demo constant.** |
| Converts missing context to zero | **No occurrence found.** Every real feature function reviewed (`goalie_intelligence/features.py`'s `days_since_last_start`, `recent_start_share`, `season_start_share`, etc.) returns `None` on insufficient data, never `0`. |
| Carries stale context forward indefinitely | **No occurrence found** in the reviewed model/decision code. (The 429-reliability fix from earlier in this block addresses a related but distinct concern — roster data going briefly stale on a transient API failure, which is now surfaced via `components["roster"]` status rather than silently trusted.) |

**Conclusion: no unacceptable hidden assumption exists in the code reviewed.** The real gap is absence, not false confidence — there is no live starter/injury/line pipeline to have a hidden assumption in yet. The one concrete risk to guard against going forward: whoever eventually builds the real (non-demo) opportunity-generation path must preserve the `None`-propagation discipline `goalie_intelligence/features.py` already established, and must add an explicit starter-certainty gate to `eligible_bets.py::build_goalie_saves_opportunities()` before it ever prices a real Saves bet — that gate does not exist today, demo or real.

## Recommended sources — NOT integrated, for your review only

Per instruction, no third-party dependency has been added. These are the realistic options if/when this context becomes worth paying for:

| Need | Candidate source | Reliability | Terms/access | Update frequency | Cost | Failure behavior if adopted |
|---|---|---|---|---|---|---|
| Starting goalie confirmation | Daily Faceoff (dailyfaceoff.com) | Widely used by the betting/fantasy community; no official public API — would require scraping or an unofficial data reseller | Scraping a commercial site's confirmed-lineup page carries real ToS risk; several paid data resellers package this feed legitimately | Continuous, updates as beat reporters confirm (typically hours before puck drop) | Free (scrape, ToS risk) to $$ (licensed reseller) | Must map cleanly to `record_goalie_status()`'s existing write path; on any fetch failure, feed `research/goalie_intelligence`'s own historical-projection model instead of blocking — i.e. degrade to the existing honest prediction, never `DATA_UNAVAILABLE` for the whole market if the *prediction* is still available, only for anything that requires *confirmation* |
| Injury status | No comparably reliable single public source exists league-wide; NHL's own injury reporting is inconsistent and often vague ("upper body") | Low, by nature of the underlying real-world data (teams are cagey about injuries) | Varies by provider | Daily at best | Free (NHL's own report, sparse) to $$ (commercial feeds, still imperfect) | Given the underlying real-world unreliability, `DATA_UNAVAILABLE` is likely the *permanently correct* answer for injury status more often than not — do not chase a source that promises more certainty than the sport itself provides |
| Line combinations / PP units | dailyfaceoff.com's line-combination pages; a team's own pregame morning-skate reporting via local beat writers | Medium — lines are set the morning of/near game time and do change | Same scraping/reseller consideration as goalie confirmation | Once or twice daily (morning skate), can change same-day | Free (scrape) to $$ (reseller) | Feed `PLAYER_SOG_PP_ROLE_OVERLAY`'s existing shadow-only design — it already exists and already never affects a real decision, so this is the correct, already-built landing spot rather than a new integration surface |

**None of these should be integrated before regular season without a separate, explicit decision** — none is free of real risk (ToS, cost, or fundamental unreliability of the underlying data), and per this block's own instruction ("do not add a new third-party dependency yet"), that decision is deliberately deferred here, not made.
