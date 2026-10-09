# Settlement audit: results against official box scores and documented rules

`python3 deploy/audit_settlement.py --out docs/validation/settlement_audit.json` re-derives every recorded ticket from the NHL's own box scores (`api-web.nhle.com`, fetched at audit time) **without** the engine's resolver or its `nhl.db`,
recomputes the ticket result and profit/loss from the stored odds and stake, and compares with the ledger. The model book and every personal log are checked separately in the same report.

## Result on 2026-10-09
**11 of 11 bets agree** (leg actual values, leg hits, ticket status and profit/loss): the 9 automatic model tickets (2 wins, 7 losses), the earlier manual ticket kept in the ledger for audit, and its copy in the personal database.
No bet is waiting on a final game; no saved value differs from today's official figure (no correction found). Model book arithmetic: $500 − $10 × 9 + returns = $465.76 (reconciled by the watchdog every 30 minutes).

## What each case does (documented rules: `docs/PAPER_SETTLEMENT_RULES.md`)
| Case | Behaviour | Where proved |
|---|---|---|
| Win (every leg hit) | pays stake × (decimal − 1) | `tests/test_paper_bet_settlement_driver.py`, `tests/test_audit_settlement.py`, live audit (2 wins) |
| Loss (any leg missed) | −stake, even while other games are unfinished | same tests; live audit (7 losses) |
| Push | **cannot occur** for supported markets: ladders are "k+", points/goals are over 0.5/1.5, saves are k+, moneyline has no ties (OT/SO decides), the puck line is ±1.5 | rules table; `resolve_puck_line` test |
| Player did not dress / goalie did not play | leg is *did-not-play*. While `VOID_RULES_VERIFIED = False` the ticket stays **UNRESOLVED**, stake stays open, provisional outcome saved; a *lost* leg still loses the ticket | driver tests; `tests/test_audit_settlement.py` |
| Game not final / postponed / suspended | stays **PENDING** until the NHL reports a final; nothing is guessed or voided. A rescheduled game raises a `SCHEDULE_CHANGED` alert, and a bet on a game that never gets played would stay open (visible on My Bets / Paper Performance) | `outcome_resolver._is_final`; driver test |
| Statistical correction after settlement | The resolver reads the newest stat revision at the moment of settlement; a settled ticket is immutable. The audit re-fetches the box score and **reports** any saved value that now differs; it does not rewrite history | `tests/test_audit_settlement.py::test_a_later_correction_is_reported` |
| Unsupported market / data gap | **UNRESOLVED**, never guessed | driver tests |

## What is not established
* **Ontario's void and parlay-reduction rules are unverified** (the sportsbook's rules page returned HTTP 403; no secondary source is authoritative). Anything that depends on them stays UNRESOLVED instead of being assumed. The owner action is to read the rules and set `VOID_RULES_VERIFIED`.
* Settlement correctness for markets with no real ticket yet (puck line, goals, saves) rests on resolver unit tests and the audit's rules, not on a live example.
* A rare box-score correction after the audit would be detected only the next time the audit runs; there is no scheduled job for it yet.
