# Acceptance checklist (one list; updated 2026-10-19 08:30 ET, release 1e79d85)

Evidence classes: **FIXED+TESTED** (code and regression tests, full suite green) · **VERIFIED LIVE** (observed on production data or the hosted app) · **AWAITING OBSERVATION** (scheduled; will be observed by itself) · **EXTERNAL** (needs something only the owner can do).
Repeatable audit: `python3 -m operational.live_audit` (read-only; checks integrity, surface agreement, freshness labels, credits, jobs, recording safeguards, hosted recency).

## Monday 2026-10-19 observations (release c746d9d until 08:14 ET, then 1e79d85; no manual trigger unless stated)
| Item | State |
|---|---|
| Mac power overnight | **EXTERNAL, failed overnight**: on battery the Mac slept about 05:20 ET to 07:36 ET (trader gap in `run_heartbeats.json`). The charger went in about 07:36; the engine's own guard then took its sleep-prevention hold (07:39) and every job resumed by itself |
| 07:00 NHL sync and 07:15 settlement at wake | **FAILED/DEFERRED by network**: the Mac woke at 07:36 before DNS was up (`NameResolutionError`); the daily sync exited 1 and the settlement run deferred itself (nothing pending, no Experiment 2 tickets, so no effect today). These calendar jobs have no retry until tomorrow; the trader settles paper bets every cycle regardless |
| 07:45 ET morning refresh | **VERIFIED LIVE (unattended)**: started 07:45:05, finished 07:46:21, DONE on the first attempt, statistics OK |
| 08:00 ET league pull | **VERIFIED LIVE (unattended)**: ran 08:00:05 ET; 2 credits planned, 2 charged; provider header 179 to 177 |
| Automatic bridging after the pull | **VERIFIED LIVE (unattended)**: moneyline rows for all four games in `odds_snapshots` one second after the pull (provider quote 07:58:19 ET); the published board carried the same prices and quote times; every price equals the raw provider payload for its own game (ANA@NYR and SJS@TOR both +120/-142 in the payload itself) |
| Hosted Slate Board after the build | **VERIFIED LIVE**: "Engine update 8:00 AM ET (1 min ago)", box scores through Oct 18, moneylines quoted 7:58 AM ET, Eggy "No Eggy pick today" with the exact reason (closest combination COL + UTA +119, value -8.1%) |
| Published board has the `integrity` block on every scheduled card | **VERIFIED LIVE**: all four cards; ANA@NYR withdrew Cole Beaudoin (NYR) and Roger McQueen (ANA) as not on their official rosters |
| Today's schedule vs the NHL feed | **VERIFIED LIVE**: 4 games, teams and start times identical; waves: 17:30 ET target (ANA@NYR, COL@PHI, SJS@TOR; COL@PHI funded) and 19:30 ET target (PIT@UTA funded) |
| Sunday official results | **VERIFIED LIVE**: CBJ 1-2 MIN, CAR 2-3 CGY identical to the NHL box scores in `games`; player and goalie rows stored for both games |
| Reconciliation | **VERIFIED LIVE**: cash $500 = $500 start, 0 tickets, 0 open stake (Experiment 2) |
| Prediction scoring | **GAP FOUND, FIXED+TESTED, RELEASED (PR 164, 08:14 ET); VERIFIED LIVE**: the trader wrote the first real scorecard by itself at 08:17 ET (49 rows: 47 graded, 2 excluded as did not play): the prospective ledger had no rows after Oct 17 (the T-35/sweep writers are retired; the legacy shots corpus ends April 2026 so its orchestrators log nothing on real data, reproduced on copies). A read-only board scorecard now grades each frozen pregame card against FINAL results. On Oct 17-18: scorers 30 graded over 6 games (hit 33%, mean estimate 30%, model Brier 0.203 vs the vig-inclusive price 0.199 on the 12 priced rows), other markets under 30 (TOO_FEW). No verdict is claimed |
| Hosted health: Live Odds Scheduler STALE | **FIXED+TESTED, RELEASED** (it expected the three retired jobs); observed by the author on the hosted app at about 08:18 ET: Code 1e79d85 and the corrected wording (not independently re-checked); the health item itself is read from the next published snapshot |
| Full suite on the merged code | **FIXED+TESTED**: full suite run by the merge session on 1e79d85: 4268 tests OK (2 skipped), not independently re-run. The production model-book hash is unchanged since the 08:14 audit (`audit_ledger_hashes.json` holds one baseline, not a before/after pair) |
| Unattended waves today (17:30 and 19:30 ET targets) | **AWAITING OBSERVATION**. Manual observation: the Mac was on battery again at 08:18 ET (42%) and 39% at about 08:28 ET (no state file records this; lid state not observed). The charger and an open lid are **EXTERNAL**; on battery the engine holds the Mac awake only inside capture windows above 30%. Verify with `python3 -m operational.live_audit` (wave timeliness) and state files in `operational/runtime/` (`wave_capture_state.json`, `credit_plan_ledger.jsonl`) |
| Recording | **PAUSED**. Activation dry run (about 08:14 and again 08:30 ET, run from the release checkout, no `--apply`): `all_pass: true`, 16 of 16 checks (policy digest 1fad3ba03503cf94, cap +0.02, $10 stake, +100 to +500, EXP-2 the single active $500 experiment, EXP-1 archived, reconciliation, watchdog state fresh). Caveats: `watchdog_release_pinned` read watchdog state from before the 08:14 promotion (it still named c746d9d), so it must be re-run after a fresh watchdog cycle before it is cited; `this_code_is_the_deployed_release` is trivially true when run from the release checkout; the policy check itself reports the policy as not approved by the owner. The owner's latest instruction (2026-10-19) is to keep recording paused unless explicitly authorised; nothing was applied |

## Player and matchup integrity
| Item | State |
|---|---|
| Knies (CBJ), Coleman and Brink (MIN) in CBJ-MIN | **VERIFIED LIVE: correct.** NHL official rosters and Knies's NHL player page (6 games for Columbus in 2026-27) confirm them. No defect; the audit now proves this for every displayed selection |
| Two players with one name on a team (Vancouver: two Elias Pettersons, ids 8480012 and 8483678): the name join kept one and priced the wrong person | **FIXED+TESTED** (both withdrawn; whole-roster detection; same name on both teams of a game withdrawn; registry per day) |
| Stale rosters: 20 of 646 players with a game by Oct 21 were not on their team's current official roster (Zary CGY, Grundstrom PHI, Severson CBJ, Harkins TBL, ...), some pricing-eligible | **FIXED+TESTED** (official NHL roster check per card, builder and slip leg; mismatch withdrawn; unverifiable players shown but never recordable) |
| Accent loss in MoneyPuck names (Lafrenire, Sttzle, Slafkovsk, Bck): the provider's spelling never matched, so their prices were silently unmatched | **FIXED+TESTED** (NHL players table is the name of record; model cache bumped) |
| Provider event is the same night as the scheduled game | **FIXED+TESTED** |
| Recording gate: only players verified on a current official roster can be staked; moneyline legs are not players | **FIXED+TESTED** (a review found and fixed a bug that would have blocked every moneyline leg) |
| Official rosters on the production release | **VERIFIED LIVE** (Oct 19 board: `integrity` block on every scheduled card; `live_audit` checks it) |

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
| Recording | **PAUSED**. Earlier chat authorisation (2026-10-18) allowed paper recording once safeguards pass (one parlay per Eastern day, $10, +100 to +500, digest above); the owner's later instruction (2026-10-19) is to keep it paused unless explicitly authorised, and that later instruction governs. `python3 -m deploy.activate_recording` runs the safeguards and writes the activation log only with `--apply` |
| Settlement, reconciliation, prediction scoring and postmortem as games finish | **AWAITING OBSERVATION** (Oct 18 games: tomorrow's 07:15 settlement and 07:30 postmortem jobs; Experiment 2 has no tickets) |

## Hosted app
| Desktop and 375 px mobile, deep links | **VERIFIED LIVE** for Today, Slate Board, Tomorrow, Parlay Builder, Paper Performance and Ticket History. Deep links use file-based slugs (`/Game_Slate`, `/Player_Props` for Best Options, `/Ledger` for Ticket History, `/Paper_Performance`, `/Slate_Board`, `/Tomorrow`, `/Parlay_Builder`); `/Games` and `/Best_Options` are not routes |
