# Postmortem: the model book's nine consecutive losses (2026-10-10)

Everything below is reproducible: `python3 deploy/postmortem_losing_streak.py --use-extract --out docs/validation/postmortem_2026-10-10.json` (read-only; uses the frozen ledger extract, the official
box scores and the audited candidate pools committed under `docs/validation/`; 34 tests in `tests/test_postmortem.py`). Automatic recording is **paused** while this is reviewed; nothing in the ledger was
changed, reset or rewritten, and personal accounts are untouched.

## 1. Bottom line

* **The streak is real, but it is 8 parlay tickets and 1 single bet, not nine tickets.** The ninth is a CGY +195 moneyline single recorded by a different job (the legacy win model), not by the ticket selector.
  The model book is 2 wins, 10 losses, **−$64.24** ($500 → $435.76). The nine losses cost $90 ($80 on tickets, $10 on the moneyline single); the book peaked at $525.76, a 17.1% drawdown. The personal manual
  ticket (−$10) is not in any of these numbers.
* **No settlement error, no stale price, no scratched or missing player.** All 12 settled bets agree with the official NHL box scores; every quote was under a minute old when each Oct 8 and Oct 9 ticket was
  recorded; all 13 players on the 11 tickets dressed and played normal minutes except one (Bourque, 8:15, left the play after 11:07 of the second period, which nobody could know at entry).
* **On the model's own numbers the streak is ordinary variance, and it was 1.9 times likelier than the usual arithmetic says.** Treating tickets as independent gives a 9.1% chance that all eight tickets lose.
  Tickets that share legs lose together, and the exact figure is **17.1%**. Including the moneyline single the nine-bet streak had a 9–12% chance. Over the whole record the model expected 3.8 wins (±1.9) and got 2.
* **That does not show the probabilities are right.** Fifteen resolved legs cannot tell a calibrated model from one that is 5 points too high (likelihood ratio 1.1; about 620 resolved legs would be needed).
  Two things in the data are worth watching and are **hypotheses, not findings**: the positive-edge *points* legs did far worse than the model said across two evenings (4 hits against 9.1 expected, 25 legs), and
  the legacy moneyline model's probabilities are squeezed toward 50%, so it "finds" an edge on big underdogs by construction.
* **The selector is delivering "+100 or better, positive modelled edge". It is not enforcing "a strong chance of hitting".** There is no hit-chance floor and the margin over the price can be a fraction of a point
  (Oct 8: +0.05% and +0.99% after the 3-point haircut; Oct 9: +0.42%). Of 11 parlay tickets, 5 had an estimated hit chance under 25%. Filling slots added shared exposure and variance for almost no margin.
* **Demonstrated defects found and repaired:** the pause did not exist and, once added, did not cover the moneyline and prop jobs (both fixed and tested); there was no way to test the model on the legs the
  selector picks (a shadow log and scorer now collect that, forward, with nothing staked). **Demonstrated weaknesses left for you to decide, not tuned on nine tickets:** the hit-chance floor, the edge margin,
  one ticket per player, three tickets a day, and whether the legacy moneyline single belongs in the model book.

## 2. What exactly lost

| | Count | Notes |
|---|---|---|
| Rows in the ledger | 13 | 12 automatic, 1 personal (manually added, moved out of model accounting earlier) |
| Parlay tickets (model book) | 11 | 3 on Oct 7, 5 on Oct 8, 3 on Oct 9; 2 wins, 9 losses |
| Single bets (model book) | 1 | the CGY +195 moneyline single (Oct 8 evening), lost |
| Personal bets | 1 | `ME3D7C508EBFDD3`, Arttu Hyry 1+ point, lost; excluded from everything here |
| **The run at the end of the record** | **9 losses** | recorded Oct 8 → Oct 9: five Oct 8 tickets, the moneyline single, three Oct 9 tickets; the last win was an Oct 7 ticket |

Tickets are not legs. The eight losing tickets contain 16 leg slots but only **11 distinct legs on 9 distinct players**; four of those legs hit (Coleman 2+ and 3+ shots, Rust 1+ point, Kakko 1+ point) and
seven missed. A ticket needs all its legs, and the missed legs were shared across tickets.

## 3. Every ticket, from its frozen entry data (all checked against the official box scores)

| Ticket | Recorded (ET) | Legs and prices | Est. hit | EV at recorded p | EV after 3-pt haircut | Result | What decided it |
|---|---|---|---|---|---|---|---|
| T72833… | Oct 8 2:07 PM | Seguin 1+ pt (+145), Lee 2+ SOG (−140) | 27.3% | +14.8% | +1.9% | L | Seguin 0 pts, Lee 1 shot |
| T758AB… | Oct 8 2:07 PM | Coleman 2+ SOG (−135), Lee 2+ SOG (−140) | 38.7% | +15.5% | +4.7% | L | Lee 1 shot (Coleman had 6) |
| T14E80… | Oct 8 2:07 PM | Coleman 2+ SOG, Bourque 2+ SOG (−125) | 35.4% | +11.0% | **+0.05%** | L | Bourque 0 shots in 8:15 (Coleman had 6) |
| T91EE6… | Oct 8 2:07 PM | Coleman 3+ SOG (+210), Bourque 2+ SOG | 20.8% | +16.1% | +1.0% | L | Bourque 0 shots (Coleman had 6) |
| TD58DA… | Oct 8 2:07 PM | Bourque 1+ pt (+165), Dvorak 2+ SOG (+120) | 20.2% | +17.7% | +2.5% | L | Bourque 0 pts, Dvorak 0 shots in 16:02 |
| *CGY ML single* | Oct 8 8:21 PM | CGY to win +195 | 49.4% raw / 37.3% cons. | +10.1% | n/a | L | COL won 7–3 |
| T10D31… | Oct 9 5:16 PM | Protas 1+ pt (+165), Rust 1+ pt (−125) | 24.6% | +17.2% | +3.4% | L | Protas 0 pts (Rust scored) |
| TDCB72… | Oct 9 5:16 PM | Protas 1+ pt, Kakko 1+ pt (+130) | 20.3% | +23.5% | +7.6% | L | Protas 0 pts (Kakko had an assist) |
| TED38A… | Oct 9 5:16 PM | Laba 1+ pt (+295), Rust 1+ pt | 16.6% | +18.0% | **+0.42%** | L | Laba 0 pts (Rust scored) |

(The three Oct 7 tickets, 2 wins and 1 loss, were recorded under an experimental earlier model by an uncommitted ("dirty") code tree and cannot be replayed; they are in the ledger and in
`docs/validation/postmortem_2026-10-10.json`.) Oct 8 tickets were recorded 4 h 53 min before puck drop, from quotes about a minute old; Oct 9 tickets 1 h 44 min before.

## 4. Settlement was checked independently

Each leg's official shots, goals, assists and time on ice come from the NHL's own box score (`docs/validation/postmortem_boxscores_2026-10-10.json`, 16 games, 396 skater rows). All 12 settled model
bets (11 tickets and the moneyline single) agree with the ledger's result. The stored player rows also match the box scores (for example Landeskog 15:16 official vs 15.27 stored minutes). Rust's goal
on Oct 9 came in a game decided in a shootout; it counts and the ticket result does not depend on it. **No settlement error.**

## 5. Participation, role, inputs

* **Participation:** all 13 players on the 11 tickets dressed and played (none scratched). Ice time on the missed legs: Seguin 15:40, Lee 15:19, Dvorak 16:02, Protas 15:35, Laba 13:42, Malkin 17:22: in line with their recent games.
* **Bourque (3 of the 8 tickets): 8:15 on ice against 16.5–19.8 in his previous four games.** His last recorded play-by-play event is at 11:07 of the second period (11 shifts in total). Cause unknown (in-game injury or
  benching); not knowable at entry; the model prices players conditional on dressing and does not model injuries. This one night sank three tickets because he was on three tickets.
* **Quotes:** every Oct 8 and Oct 9 leg was under one minute old at entry (provider time). Oct 7 legs carry only retrieval times (older code path).
* **Model data lag (considered, not a cause):** the Today page, opened at 5:5x PM ET on Oct 9, said player logs ran through Oct 7, so the Oct 9 tickets were built one day behind. With a 30-game rate
  half-life and an 8-game ice-time half-life, one missing game moves a projection by well under a point; not a demonstrated cause, noted as a limit.
* **Low-sample players:** none. Every leg's player had at least 60 prior games (the 40-game pricing floor applies; Hyry, who was below it, was the personal bet).

## 6. Causes assessed: demonstrated, not supported, or hypothesis

| Candidate cause | Verdict | Evidence |
|---|---|---|
| **Settlement error** | **Not supported** | 12 of 12 agree with official box scores. |
| **Stale or wrong inputs at entry** | **Not supported** (one limit noted) | Quotes < 1 min old; all players played; no low-sample player; model data ran about a day behind (small effect). |
| **Ordinary variance** | **Supported, and larger than the usual arithmetic shows** | P(all 8 tickets lose) = 17.1% exact (9.1% if wrongly treated as independent); with the moneyline single 8.7–11.5%; over the 12-bet record P(≤2 wins) ≈ 26%, P(a 9-loss run somewhere) ≈ 13%. Expected wins 3.8 ± 1.9, observed 2. |
| **Excessive shared exposure** | **Demonstrated** | Oct 8: 5 tickets on 5 players, Coleman and Bourque each on 3 tickets (rules then had no per-player limit; the replay reproduces the recorded tickets only without one). P(no ticket wins) was 29.9% against the 18.2% a naive product gives. Oct 9: 3 tickets on 4 players, 57.2% against 50.2%. The per-player limit (2) was added on Oct 9 and is in force; it still allows two tickets on one player. |
| **Weak ticket acceptance** | **Demonstrated** | No hit-chance floor; acceptance needs only EV ≥ 5% at the recorded probabilities and EV ≥ 0 after a 3-point haircut. Five of the eight losing tickets had under 3% margin after the haircut; two had under half a point. For the eight tickets the modelled expected profit was +$13.38 on $80, +$2.15 after the haircut. Under a 5-point haircut the Oct 8 portfolio goes from +$7.51 to −$3.11 and Oct 9 from +$5.88 to −$1.84: the edge is inside the model's measured player-level error (8–10 points). |
| **Overstated probabilities** | **Hypothesis (not demonstrated)** | Held-out 2025-26 validation (46,622 player-games) shows the calibrated model within about 1 point overall and slightly *under*-predicting in the bands used. The 15 recorded legs: 8.0 expected hits, 7 observed. The wider candidate pool (41 legs): 14.0 expected, 11 observed; points legs 4 of 25 (9.1 expected), shots in line. Six slices were looked at, so this is a signal to watch. The question that matters (the legs where the model most disagrees with the price) has no historical prices to test on: hence the shadow log below. |
| **Legacy moneyline single** | **Weak acceptance; evidence suggestive, small** | 76 side-observations over 38 games: log loss 0.666 vs market 0.648; spread 0.063 vs market 0.100; on 12 underdog sides the model said 44%, the market 34%, and 2 won. The CGY bet (model 49.4%, conservative 37.3%, market 32.5%) is that pattern. |
| **Reporting** | **Defect (minor), fixed** | Today's "Tickets 12" counted the moneyline single as a ticket; its help text now says what it counts. |

## 7. Predicted versus actual, by market and probability band

| Evidence | Market / band | Model said | Happened | n |
|---|---|---|---|---|
| Held-out 2025-26 (shipped calibrated model, players with 80+ games) | shots 2+ / shots 3+ / points 1+ / goals 1+ | 43.7% / 21.8% / 35.7% / 15.6% | 44.8% / 23.5% / 36.6% / 16.1% | ~39,600 each |
| Held-out, the bands the tickets used | shots 2+ at 50–62%; points 1+ at 38–50% | 55.9%; 43.6% | 59.0%; 46.3% | 7,624; 8,504 |
| The 15 recorded legs | all | 53.4% | 46.7% (7 of 15) | 15 |
| &nbsp;&nbsp;by market | shots / points | 59.6% / 44.1% | 55.6% / 33.3% | 9 / 6 |
| The 41 positive-edge candidate legs, Oct 8–9 | all | 34.1% | 26.8% (11 of 41) | 41 |
| &nbsp;&nbsp;by market | shots / points / goals | 34.0% / 36.4% / 15.6% | 38.5% / 16.0% / 66.7% | 13 / 25 / 3 |
| &nbsp;&nbsp;by band | under 30% / 30–50% / 50%+ | 20.0% / 40.1% / 59.0% | 31.2% / 19.0% / 50.0% | 16 / 21 / 4 |
| Legacy moneyline model | underdog sides / coin-flip / favourite sides | 44.3% / 50.0% / 55.7% | 16.7% / 50.0% / 83.3% | 12 / 52 / 12 |

Nine tickets are nowhere near enough to recalibrate anything: to see a 5-point overstatement with 80% power takes about **620 resolved legs** (about 1,700 for 3 points). No parameter was changed.

## 8. Is the selector delivering your objective?

* **+100 or better:** yes, always (every ticket's combined price was at least +129; the rule is enforced in code).
* **Worthwhile (positive modelled edge):** only nominally. After the 3-point haircut the margin ranged from +0.05% to +23.5% (the Oct 7 winner); on Oct 8–9 five of the eight losing tickets were under 3%.
* **A strong estimated chance of hitting:** **not enforced.** The best ticket on Oct 8 was 38.7%, on Oct 9 24.6% (the day had prices for only three games). Slots were filled anyway: tickets 4 and 5 on Oct 8
  (20.8%, 20.2%) and all three on Oct 9 (24.6%, 20.3%, 16.6%). Why: the selector ranks by hit chance and then accepts any ticket that clears the EV gates; a slot stays empty only when nothing at all qualifies, and on a thin day something always does.
* **Did filling slots add value?** On Oct 8 the top three tickets carried an expected 1.01 wins; tickets 4 and 5 added 0.41 expected wins, +$1.6 and +$1.8 at the recorded probabilities and about +$0.1 and
  +$0.25 after the haircut, while concentrating the book on Bourque, Coleman and Lee. The value claimed for them is inside the model's error; their cost is variance and shared exposure.

## 9. Assessing the streak without multiplying ticket losses

Exact enumeration over the underlying leg outcomes (`operational/postmortem_math.py`; verified against a Monte Carlo and by hand on small cases), with nested lines (Coleman 2+ and 3+) handled together:

| Quantity | Value |
|---|---|
| Expected wins among the 8 streak tickets | 2.04 |
| P(all 8 lose), wrongly independent | 9.1% |
| **P(all 8 lose), shared legs exact** | **17.1%** (18.4% if one player's different markets move together) |
| …if every recorded leg were 3 / 5 / 8 points lower | 21.3% / 24.4% / 29.5% |
| P(all 8 and the moneyline single lose) | 8.7% (model's 49.4%), 10.7% (conservative 37.3%), 11.5% (market) |
| Over the 12-bet record: P(last 9 all lose) / P(a run of 9 anywhere) / P(≤2 wins) | 10.9% / 13.4% / 25.9% |
| Likelihood ratio, "probabilities 3 / 5 / 8 points too high" vs "right" | 1.10 / 1.13 / 1.14 (≈ no information) |

Limits: players in the same game are treated as independent; the same-player bound is shown.

## 10. Repairs

| Change | Why | Tests |
|---|---|---|
| **Pause switch** (`operational/recording_pause.py`, file `recording_pause.json`): no new automatic ticket or bet is selected or recorded; collection, publishing, settlement and personal accounts carry on. Fails safe if the file is unreadable. Live since 08:23 ET Oct 10; extended to every writer the same morning. | Your request. | `tests/test_recording_pause.py` |
| **The pause covers every automatic writer** (`paper_bankroll.record_paper_bet`), not only the ticket selector | The moneyline pre-game job and the prop jobs write through that function and would have recorded tonight. | same |
| **Shadow selection log** (`operational/shadow_selection.py`): while paused, every priced leg with the model's probability, and what each policy variant would pick, are logged before the games (nothing staked) | The question that matters needs data nine tickets cannot give; there are no historical prices. | same |
| **Shadow scorer** (`deploy/score_selection_shadow.py`): counts each leg once at its first logged value; edge-filtered legs apart from all legs; by market and band; states "NOT ENOUGH YET" below ~620 legs | So no trend is read into a small sample. | `tests/test_postmortem.py` |
| **Watchdog row** shows the pause and warns after 3 days | A pause cannot be forgotten. | `tests/test_recording_pause.py` |
| **Today's ticket-count help text** | The count includes single bets. | page tests |
| Earlier, already in force: per-player limit of 2 tickets (Oct 9), recording-time revalidation, early-price cap of 2 a day (prices over 3 h before puck drop), pinned clean release for the jobs | Oct 8's five tickets were recorded at 2:07 PM for 7 PM games with three of them on one player. | existing suites |

**Deliberately not changed:** no probability, haircut, threshold or limit; no recalibration; no ticket rewritten; no balance reset. They are owner decisions (section 12) and nine tickets cannot support them.

## 11. Replay of the original decisions (entry information only)

The candidate pools were reconstructed from the stored selection reports (Oct 8: 26 of 27 legs matched to official players; Oct 9: all 15, with probabilities recovered by algebra from the stored tickets). **The
replay with each day's own rules reproduces the recorded tickets exactly on both days**, so it is faithful. The variants below were written down (`operational/selection_variants.py`) before any replacement ticket's
result was looked at; they are round numbers tied to your objective, to the measured model error and to exposure, and are not fitted to the nine losses.

| Variant | Oct 8: tickets (P none win; modelled profit / at −5 pts) | Oct 9: tickets (P none win; modelled profit / at −5 pts) |
|---|---|---|
| Rules in force Oct 8 (no per-player limit) | 5 (29.9%; +$7.51 / −$3.11) | n/a |
| **Rules in the code today** (≤2 tickets per player) | 5 (26.2%; +$7.95 / −$2.85) | 3 (57.2%; +$5.88 / −$1.84) |
| + hit chance ≥ 25% | 3 (37.5%; +$4.13 / −$1.56) | 0 |
| + hit chance ≥ 30% | 2 (47.5%; +$2.65 / −$0.92) | 0 |
| + edge survives a 5-point haircut | 3 (51.5%; +$8.32 / +$0.69) | 2 (88.5%; +$13.73 / +$0.30), best hit chance 8.4% |
| + one ticket per player | 5 (30.0%; +$9.80 / −$3.44), 10 distinct players | 3 (60.5%; +$11.34 / −$1.06), 6 distinct players |
| 25% floor + 5-pt haircut + one per player + ≤3 a day | 1 (72.1%; +$2.89 / +$0.52) | 0 |

What this shows: sharing legs raised P(no winner) on both days; a margin rule alone drifts toward longshots (an 8.4% best ticket), so it needs a floor; floors leave a thin day empty. The tickets' later
results are in the JSON "for completeness only" and are not used for any choice (a handful of tickets can neither validate nor reject a rule).

## 12. Proposed restart policy (for your review; recording stays paused)

1. **You set the objective as a number.** "Strong estimated chance" needs a floor: 25%, 30% or 35% (the replay shows the cost: fewer tickets, empty days). My recommendation: **30%**.
2. **A margin that means something:** a ticket's edge must survive a 5-point haircut on every leg *and* the floor above (alone, the margin picks longshots).
3. **Exposure:** each player on at most **one** ticket a day; at most two tickets per game; **at most three tickets a day**; never fill a slot to reach a count (already true) and say "nothing worthwhile today" on the page.
4. **Keep:** the early-price cap of two a day, revalidation at recording, $10 stakes, the $500 experiment (no reset).
5. **Moneyline singles stay shadow-only** (observations logged, nothing staked) until a moneyline model beats the no-vig market over at least 150 finished game-sides, the gate already used for the strength model.
6. **Evidence gate and review:** restart as a paper experiment under 1–5 once you approve it, with the shadow scorer reviewed weekly; do not recalibrate until it shows about 620 resolved edge-filtered legs
   (earlier if a market shows a large, persistent gap), and report the points market separately.
7. **Reversible:** `python3 -m operational.recording_pause resume` (explicit, logged); `pause` re-applies it.

## 13. What is still uncertain

* Whether the model is overstated on the legs the selector picks (needs ~620 resolved legs; points may be the weak market). Held-out evidence says the model is calibrated on all players.
* Why Bourque left the game (injury or role); unknowable at entry.
* Players in one game are treated as independent in the streak arithmetic.
* The Oct 7 tickets and any price history before Oct 8 cannot be replayed (dirty code, no stored pool).
* There are no historical sportsbook prices, so profitability has never been shown; every EV above is model-versus-price.
* The 5-slot count was never evidence-based; 3 is a proposal, not a finding.
