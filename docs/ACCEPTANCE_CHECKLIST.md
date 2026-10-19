# Acceptance checklist (one list; updated 2026-10-18 20:40 ET, release 2c8055b)

Evidence classes: **FIXED+TESTED** (code and regression tests, full suite green) · **VERIFIED LIVE** (observed on production data or the hosted app) · **AWAITING OBSERVATION** (scheduled; will be observed by itself) · **EXTERNAL** (needs something only the owner can do).
Repeatable audit: `python3 -m operational.live_audit` (read-only; checks integrity, surface agreement, freshness labels, credits, jobs, recording safeguards, hosted recency).

## Player and matchup integrity
| Item | State |
|---|---|
| Knies (CBJ), Coleman and Brink (MIN) in CBJ-MIN | **VERIFIED LIVE: correct.** NHL official rosters and Knies's NHL player page (6 games for Columbus in 2026-27) confirm them. No defect; the audit now proves this for every displayed selection |
| Two players with one name on a team (Vancouver: two Elias Pettersons, ids 8480012 and 8483678): the name join kept one and priced the wrong person | **FIXED+TESTED** (both withdrawn; whole-roster detection; same name on both teams of a game withdrawn; registry per day) |
| Stale rosters: 20 of 646 players with a game by Oct 21 were not on their team's current official roster (Zary CGY, Grundstrom PHI, Severson CBJ, Harkins TBL, ...), some pricing-eligible | **FIXED+TESTED** (official NHL roster check per card, builder and slip leg; mismatch withdrawn; unverifiable players shown but never recordable) |
| Accent loss in MoneyPuck names (Lafrenire, Sttzle, Slafkovsk, Bck): the provider's spelling never matched, so their prices were silently unmatched | **FIXED+TESTED** (NHL players table is the name of record; model cache bumped) |
| Provider event is the same night as the scheduled game | **FIXED+TESTED** |
| Recording gate: only players verified on a current official roster can be staked; moneyline legs are not players | **FIXED+TESTED** (a review found and fixed a bug that would have blocked every moneyline leg) |
| Official rosters on the production release | **AWAITING OBSERVATION**: the first full board built by the new release (after midnight ET) must show an `integrity` block on every scheduled card; `live_audit` checks it |

## Data propagation
| Odds bridge after a league pull (provider response to stored odds to board to published snapshot) | **FIXED+TESTED** (end-to-end test with provider timestamps, both moneyline sides and spreads); was **VERIFIED LIVE as a manual recovery only** at 10:38 and 16:39 ET. Unattended on the new release: **AWAITING OBSERVATION** (Monday's pregame pull) |
| Spreads: first real response, cost, parsing | **VERIFIED LIVE**: the 08:00-style league pull returned h2h+spreads for 2 credits (header 187 to 185; again 182 to 180 at 16:39); CBJ +1.5 -162 / MIN -1.5 +136 shown as a quoted price only (contract uncertified, model unvalidated) |
| Moneyline freshness caveat | **EXTERNAL decision**: freshness is judged by the provider's own quote time. A static line keeps an old provider time, so with one pregame league pull a moneyline can be past its limit at the T-30 decision and in a slip. Remedy options (owner): fund one extra league pull per later wave from surplus, or accept. No rule was loosened |

## Unattended scheduling
| Wave 2 (CAR at CGY, T-90 = 18:30 ET) captured at 18:41 ET without anyone triggering it (T-78, before the 19:00 deadline) | **VERIFIED LIVE (unattended)** |
| Wave 1 (CBJ at MIN, T-90 = 16:30 ET) | captured at 16:39 ET **by a manual trigger** (the previous cycle was 16:27); the unattended cycle would have run about 16:42. Not evidence of unattended success |
| Trader cadence 15 to 10 minutes so a capture lands within 10 minutes of T-90 | **FIXED+TESTED, deployed** (launchd interval 600 s; pinned release) |
| Recovery after network failure, sleep, restart | **FIXED+TESTED** (isolated tests: failed call releases and the next cycle captures inside the window; late capture marked late; after the last chance nothing is bought; restart that lost files cannot double-buy). **VERIFIED LIVE** only for the morning case: the Mac was off until 10:38 ET Oct 18; the refresh then retried components after the network returned (manual rerun, then retry logic deployed) |
| Mac power | **EXTERNAL**: the Mac is on battery; software cannot wake it or keep it awake on battery below 30% (watchdog `power` check says so). Monday's 07:45 ET prep needs the charger and lid open (optional sudo `pmset` settings in docs/ALWAYS_ON_HOST.md) |

## Recommendation correctness
| Alternate goalie line (book prices the backup) | **FIXED+TESTED**; **VERIFIED LIVE** (VGK Hill 21.5 on Oct 17); never a recommendation without a confirmed start |
| EV math on the board | **VERIFIED LIVE**: every displayed scorer's implied probability and EV recomputed from price and model and matched |
| Surfaces agree (board vs builder prices and provider times) | **VERIFIED LIVE** by `live_audit` (9 prices compared); Best Options had no entries (no qualifying slip) |
| +0.02 cap, thresholds, probabilities | unchanged; policy digest `1fad3ba03503cf94` |
| Eggy slip | none qualifies yet; the board shows the closest combination with the exact reason; an empty slip is legitimate |

## Accounting and safeguards
| Credit guard (header minus later charges), reservations, planned = charged | **VERIFIED LIVE** (balance 179 = provider header after the last capture) |
| $500 Experiment 2, archive intact, idempotent reset | **VERIFIED LIVE** |
| Tests cannot touch production ledgers | **FIXED+TESTED**; production model-book hash identical before and after a full 4,219-test run |
| Recording | **PAUSED**. Owner authorised paper recording once checks pass (one parlay per Eastern day, $10, +100 to +500, digest above); `python3 -m deploy.activate_recording` runs the safeguards and the activation log. Held until the repeated live audit on this release is clean |
| Settlement, reconciliation, prediction scoring and postmortem as games finish | **AWAITING OBSERVATION** (Oct 18 games: tomorrow's 07:15 settlement and 07:30 postmortem jobs; Experiment 2 has no tickets) |

## Hosted app
| Desktop and 375 px mobile, deep links | **VERIFIED LIVE** for Today, Slate Board, Tomorrow, Parlay Builder, Paper Performance and Ticket History. Deep links use file-based slugs (`/Game_Slate`, `/Player_Props` for Best Options, `/Ledger` for Ticket History, `/Paper_Performance`, `/Slate_Board`, `/Tomorrow`, `/Parlay_Builder`); `/Games` and `/Best_Options` are not routes |
