# Postmortem: the model book's nine consecutive losses (2026-10-10, revised the same day)

Everything below is reproducible: `python3 deploy/postmortem_losing_streak.py --use-extract --out docs/validation/postmortem_2026-10-10.json` (read-only; it uses the frozen ledger extract, the official
box scores, the audited candidate pools and the dependence estimates committed under `docs/validation/`). Automatic recording is **paused**; nothing in the ledger was changed, reset or rewritten, and
collection, settlement, publishing, personal accounts and the forward shadow log are running. Resuming needs your approval of the concrete policy in section 12 (a technical gate enforces it).

## 1. Bottom line

* **The streak is 8 parlay tickets plus 1 moneyline single, not nine tickets.** The model account is 2 wins and 10 losses, **−$64.24** ($500 → $435.76): the parlay tickets are **2W–9L, −$54.24**; the one single
  bet is 0W–1L, −$10. The nine losses cost $90. The personal manual ticket (−$10) is in none of these numbers.
* **No settlement error, no stale price, no scratched player.** All 12 settled bets agree with the official box scores; every Oct 8 and Oct 9 quote was under a minute old at entry; all 13 players dressed. One
  played far less than usual (Bourque, 8:15, on three of the tickets), which could not be known at entry.
* **The run is compatible with variance under the model's assumptions, and the dependence between tickets makes it likelier than the usual arithmetic says.** Treating the eight tickets as independent gives a
  9.1% chance that all lose; accounting for shared legs gives **17.1%**; adding every further dependence I could measure or bound moves that to at most about 18.4% (section 10). These are **conditional on the
  model's recorded probabilities being the true ones** and on the stated dependence structure; they are not the probability of the streak "in reality", and this evidence cannot show the probabilities are right.
* **The selector did not enforce your objective, and that is now fixed in the code as a proposal that cannot go live without your approval.** It enforced "+100 or better" and "positive edge", but there was no
  floor on a ticket's hit chance and the margin after the haircut could be a fraction of a point. The selector now supports an explicit hit-chance floor, a meaningful-value test and exposure limits as a named
  policy (`research/real_market_parlay/policy.py`), keeps **up to five tickets a day with zero allowed**, and explains an empty slot. The proposed numbers are judgements, not validated results (section 12).
* **Automatic moneyline and prop single bets are out of the experiment.** They are no longer recorded in the model account (their observations are still logged); the earlier single bet stays on the record and is
  shown separately; the account reconciles as parlay tickets + single bets = whole (section 7).
* **Two targets I quoted need their assumptions stated** (section 11): the 620-leg figure counts independent player-market-nights and is a pooled test of the edge-filtered legs, so realistically 690 to 1,230
  units; the 150-game figure is a floor for comparing two moneyline models, not a power calculation, and cannot validate moneyline bets.

## 2. What exactly lost

| | Count | Notes |
|---|---|---|
| Rows in the ledger | 13 | 12 automatic, 1 personal (manually added, moved out of model accounting earlier) |
| Parlay tickets (model account) | 11 | 3 on Oct 7, 5 on Oct 8, 3 on Oct 9; 2 wins, 9 losses |
| Single bets (model account) | 1 | the CGY +195 moneyline single (Oct 8 evening), lost; no longer a kind of bet the model account records |
| Personal bets | 1 | `ME3D7C508EBFDD3`, Arttu Hyry 1+ point, lost; excluded from everything here |
| **The run at the end of the record** | **9 losses** | five Oct 8 tickets, the moneyline single, three Oct 9 tickets; the last win was an Oct 7 ticket |

Tickets are not legs. The eight losing tickets have 16 leg slots but only **11 distinct legs on 9 distinct players**; four of those legs hit (Coleman 2+ and 3+ shots, Rust 1+ point, Kakko 1+ point) and seven missed.

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

(The three Oct 7 tickets, 2 wins and 1 loss, were recorded under an experimental earlier model by an uncommitted ("dirty") code tree and cannot be replayed.) Oct 8 tickets were recorded 4 h 53 min before puck drop
from quotes about a minute old; Oct 9 tickets 1 h 44 min before.

## 4. Settlement was checked independently

Each leg's official shots, goals, assists and time on ice come from the NHL's own box score (`docs/validation/postmortem_boxscores_2026-10-10.json`, 16 games, 396 skater rows). All 12 settled model bets
agree with the ledger's result, and the stored player rows match the box scores. **No settlement error.**

## 5. Participation, role, inputs

* **Participation:** all 13 players on the 11 tickets dressed and played. Ice time on the missed legs was in line with recent games (Seguin 15:40, Lee 15:19, Dvorak 16:02, Protas 15:35, Laba 13:42).
* **Bourque (3 of the 8 tickets): 8:15 against 16.5–19.8 in his previous four games.** His last recorded play-by-play event is at 11:07 of the second period (11 shifts). Cause unknown; not knowable at entry; the model
  prices players conditional on dressing and does not model injuries. He sank three tickets because he was on three.
* **Quotes:** every Oct 8 and Oct 9 leg was under one minute old at entry (provider time). **Model data lag:** the Today page on Oct 9 said player logs ran through Oct 7; with a 30-game rate half-life and an
  8-game ice-time half-life one missing game moves a projection by well under a point (a limit, not a demonstrated cause). **Low-sample players:** none (every player had at least 60 prior games).

## 6. Causes assessed: demonstrated, not supported, or hypothesis

| Candidate cause | Verdict | Evidence |
|---|---|---|
| Settlement error | **Not supported** | 12 of 12 agree with the official box scores. |
| Stale or wrong inputs at entry | **Not supported** (one limit noted) | Quotes under a minute old; all players played; no low-sample player; model data about a day behind (small effect). |
| Variance | **Compatible with variance under the model's assumptions** | 17.1% (shared legs exact) to 18.4% (upper bound for same-player dependence) that all eight lose; 8.7%–11.5% including the moneyline single; expected wins 3.8 ± 1.9 over the 12 bets, observed 2. Conditional on the recorded probabilities; stronger evidence than this does not exist. |
| Excessive shared exposure | **Demonstrated** | Oct 8: five tickets on five players, Coleman and Bourque on three each (no per-player limit then). P(no ticket wins) 29.9% against 18.2% from a naive product. Oct 9: three tickets on four players, 57.2% against 50.2%. |
| Weak ticket acceptance | **Demonstrated** | No hit-chance floor; margins after the haircut as small as +0.05%. Five of the eight losing tickets under 3%. Modelled profit on the eight tickets +$13.38 on $80, +$2.15 after the haircut; at 5 points lower the Oct 8 book goes from +$7.51 to −$3.11 and Oct 9 from +$5.88 to −$1.84. |
| Overstated probabilities | **Hypothesis (not demonstrated)** | Held-out validation (46,622 player-games) shows the model within about a point and slightly under-predicting in the bands used. The 15 recorded legs: 8.0 expected hits, 7 observed; 41 positive-edge candidate legs: 14.0 expected, 11 observed (points 4 of 25, shots in line). Six slices were looked at. |
| Legacy moneyline single | **Weak acceptance, small sample** | 76 side-observations over 38 games: log loss 0.666 vs the market's 0.648; probabilities spread 0.063 vs 0.100; on 12 underdog sides the model said 44%, the market 34%, and 2 won. |

## 7. The account, read apart: parlay experiment, single bets, whole

Nothing was reset or rewritten. The account is still the combined one; the tickets and the earlier single bet are now reported separately and add up to it (`paper_bankroll.book_breakdown`, shown on Today and
Paper Performance, checked by the watchdog every half hour):

| Part | Bets | Won–Lost | Settled stake | Settled P&L | ROI |
|---|---|---|---|---|---|
| **Parlay experiment (tickets)** | 11 | 2–9 | $110 | **−$54.24** | −49.3% |
| Single bets: moneyline and props (earlier; no longer recorded) | 1 | 0–1 | $10 | −$10.00 | −100% |
| **Whole account** | 12 | 2–10 | $120 | **−$64.24** | −53.5% |

Cash $435.76 = $500 start −$54.24 (parlay tickets) −$10.00 (single bets) − $0.00 open. All five bookkeeping checks agree (P&L, bet count, settled stake, open stake, cash).

**What changed in the code.** `paper_bankroll.record_paper_bet` now refuses automatic single-leg bets on the model account (`EXCLUDED_SINGLE`); the moneyline pre-game job and the prop jobs still record their
observations in the prospective ledger (so they can be scored and compared later) but create no bet. Personal accounts are unaffected. Only a unit-test run can switch the exclusion off.

## 8. Predicted versus actual, by market and probability band

| Evidence | Market / band | Model said | Happened | n |
|---|---|---|---|---|
| Held-out 2025-26 (shipped calibrated model, players with 80+ games) | shots 2+ / shots 3+ / points 1+ / goals 1+ | 43.7 / 21.8 / 35.7 / 15.6% | 44.8 / 23.5 / 36.6 / 16.1% | ~39,600 each |
| Held-out, the bands the tickets used | shots 2+ at 50–62%; points 1+ at 38–50% | 55.9%; 43.6% | 59.0%; 46.3% | 7,624; 8,504 |
| The 15 recorded legs | all / shots / points | 53.4 / 59.6 / 44.1% | 46.7 / 55.6 / 33.3% | 15 / 9 / 6 |
| The 41 positive-edge candidate legs, Oct 8–9 | all | 34.1% | 26.8% | 41 |
| | shots / points / goals | 34.0 / 36.4 / 15.6% | 38.5 / 16.0 / 66.7% | 13 / 25 / 3 |
| | under 30% / 30–50% / 50%+ | 20.0 / 40.1 / 59.0% | 31.2 / 19.0 / 50.0% | 16 / 21 / 4 |
| Legacy moneyline model | underdog / coin-flip / favourite sides | 44.3 / 50.0 / 55.7% | 16.7 / 50.0 / 83.3% | 12 / 52 / 12 |

Nine tickets are nowhere near enough to recalibrate anything; no parameter was changed.

## 9. Was the selector delivering your objective, and what changed

Your objective: up to five worthwhile, +100-or-better parlays with a strong estimated chance of hitting, a slot left empty when nothing is. Before: +100 or better **yes**; "worthwhile" **nominally**
(EV of at least 5% on the recorded probabilities and at least zero after a 3-point haircut, so margins of +0.05% passed); "strong chance" **not enforced** (the best ticket was 38.7% on Oct 8 and 24.6% on Oct 9 and
slots were filled anyway). On Oct 8 the pool the rules qualified had 101 tickets with a **median hit chance of 13%**; on Oct 9 28 tickets with a median of **6%**; the selector took the best of a weak pool.

What changed: the selector takes a `TicketPolicy` (`research/real_market_parlay/policy.py`): a floor on estimated hit chance, a value test that must hold after the stated haircut, and exposure limits, each
reported by name when it removes a ticket (`BELOW_HIT_CHANCE_FLOOR`, `VALUE_NOT_MEANINGFUL`, the limit statuses). With no policy the selector behaves exactly as before (tested). The policy in the code is the
proposal in section 12. It keeps five slots and allows zero; an empty day says why ("No ticket is worthwhile today under the ticket policy: … A slot left empty is a correct answer, not a gap").
**It is a proposal: `recording_pause.resume()` refuses until you approve its exact digest** (`python3 -m operational.ticket_policy show` / `approve <digest>`); changing any number changes the digest and voids an earlier approval.

## 10. What the streak probability assumes, and how far the dependence changes it

**Label: every figure here is conditional on the model's recorded probabilities being the true ones (before any shading) and on the dependence structure named in its row. It is not the probability of the
streak "in reality".**

Dependencies, one by one:

| Dependence | In the 17.1%? | Size, and where it comes from |
|---|---|---|
| Tickets that share a leg (a leg on two tickets is one event) | **Yes** (exact enumeration) | This is what moves 9.1% to 17.1% |
| Nested lines of one player and market (Coleman 2+ and 3+ shots) | **Yes** | One random number: 3+ cannot hit while 2+ misses |
| Same player, different markets (Bourque's shots and points) | Bounded (independent: 17.1%; at the measured correlation 0.15: 17.2%; fully dependent: 18.4%) | History (89,470 skater games since 2024-10-01): shots 2+ with points 1+ correlate 0.15, shots 2+ with goals 1+ 0.23 |
| Different players in the same game, opposite teams | Not modelled; **measured about −0.01 (points), −0.004 (shots)**, so ignoring it is harmless and slightly conservative | `deploy/estimate_dependence.py`; standard errors by resampling games |
| Teammates in the same game | Not modelled; **measured +0.10 for points, +0.01 for shots**; this would matter, but **no teammates share a ticket in any of the eight tickets** | same |
| Different games on one date | Not modelled; **measured 0.000** | same |
| A common error in the model itself on a given day | Shown as a range: a shared daily shift of 2, 4 or 6 points in every leg's true probability gives 17.4%, 17.3%, 17.4% | not estimated (nothing measures it yet) |
| A persistent model error across days | **Not modelled** | covered only by the shading rows below |

Sensitivity of "all eight lose" (Monte Carlo rows ±0.1 point): shared legs only **17.1%** · + same-player dependence at the historical correlation 17.2% · + same-player fully dependent 18.4% · + daily
model-error shift (2/4/6 points) 17.4/17.3/17.4% · every leg 3 / 5 / 8 points lower 21.3 / 24.4 / 29.5%. What applies here: the Oct 8 tickets use five players in five different games (no same-game dependence
among different players is possible; only Bourque's two markets matter); on Oct 9 Protas (WSH) and Laba (NYR) share a game as opponents (about −0.01); Rust and Kakko are in other games.

Over the 12-bet record (conditional on the same assumptions): the last nine losing 10.9%; a nine-loss run somewhere 13.4%; two wins or fewer 25.9%; expected wins 3.8 ± 1.9. **This is compatible with the
streak being unremarkable under the model, and also with the model being too high by several points (the likelihood ratio for "3 / 5 / 8 points too high" against "right" is 1.10 / 1.13 / 1.14): the
results do not discriminate.**

## 11. What the 620-leg and 150-game targets assume

**620 resolved legs** (619 exactly): `n = (1.645 + 0.8416)² × p(1−p) / 0.05²` with p = 0.5. It is the sample for a one-sided 5% test with 80% power that probabilities are **5 points too high**, as one pooled comparison
of the average prediction with the observed rate.

* **Independence is assumed, and is not true.** The unit is an independent **player-and-market-and-night**, not a leg row: a player's 2+, 3+, 4+ and 5+ shots resolve together. The Oct 8 pool had 26 leg rows,
  19 player-markets and 17 players; Oct 9 had 15, 14 and 12. Legs in a game or a night also share luck (measured weakly) and possibly model error (unmeasured). Allowing for that, **about 690 units (light
  clustering) to 1,230 (a shared nightly model error with correlation 0.05)**; at the measured 16 player-markets a night that is roughly **42 to 74 priced game nights**, longer when fewer games are priced.
* **p near 0.5** is the largest variance (it overstates the need for long shots). **Each market tested alone needs its own sample** (points and shots separately roughly doubles it). It allows for one test, not
  several bands.
* **What it evaluates: the legs that pass the edge filter (what a ticket can be built from), not the handful of tickets chosen and not the general player pool.** The general pool is already covered by the
  held-out validation. The selected-edge group is the right one for selection bias, and a proxy for the chosen legs, which are the extreme end of it; the scorer reports the top quarter by edge size separately.
* **A more efficient measure of the winner's curse uses every priced leg**: fit the outcome on both the model's probability and the price's implied probability (`deploy/score_selection_shadow.py`
  `blend_report`, night-level resampling). If the price carries information the model lacks, a leg where they disagree most is overstated by an amount that weight implies, without needing hundreds of
  selected bets. It is reported with the scorer and is meaningless until there are many nights.

**150 finished game-sides** (`MIN_GAMES_FOR_A_CLAIM` in `operational/moneyline_model_path.py`): a floor below which the scoreboard says "too few games"; at or above it the strength model may replace Elo **only
if** the paired 95% interval of (strength − Elo) log loss lies wholly below zero. **It was set as a minimum, not derived from an effect size, and it compares two models, not a model with the market and not
bets.** What it can detect: with a per-game standard deviation of the paired log-loss difference of 0.115 (1,312 held-out games), 150 games detect only a gap of about 0.023; the held-out gap is 0.006, which
would need about 2,400 games. Against the market the legacy model is currently *worse* (mean +0.017 on 38 games, per-game sd 0.155; about 1,500 games would resolve a 0.01 gap). Evaluating **bets** is harder still:
a bet needs a model-versus-market disagreement; the live record shows 1 in 38 games; 619 bets would take thousands of games (a season is about 1,300). My earlier suggestion that moneyline singles wait for "150
game-sides" borrowed this gate for a question it does not answer: moneyline singles cannot be validated forward in any practical time, and there are no historical moneyline prices here. They stay out.

## 12. Replay of the original decisions (entry information only; no result is used)

The candidate pools were reconstructed from the stored selection reports (Oct 8: 26 of 27 legs matched to official players; Oct 9: all 15, probabilities recovered by algebra from the stored tickets). Replaying
each day with its own rules **reproduces the recorded tickets exactly on both days**. The replay no longer reports what happened to any replayed ticket. The variants were fixed before any replacement ticket's
result was looked at; two of them were later revised for practicality, from entry-information counts alone (below).

| Variant | Oct 8: tickets · P(none win) · modelled profit (at −5 pts) | Oct 9: the same |
|---|---|---|
| Rules in force Oct 8 (no per-player limit) | 5 · 29.9% · +$7.51 (−$3.11) | n/a |
| Rules in the code before this change (≤2 tickets per player) | 5 · 26.2% · +$7.95 (−$2.85) | 3 · 57.2% · +$5.88 (−$1.84) |
| + hit chance ≥ 25% | 3 · 37.5% · +$4.13 (−$1.56) | 0 |
| + hit chance ≥ 30% | 2 · 47.5% · +$2.65 (−$0.92) | 0 |
| + edge survives a 5-point haircut | 3 · 51.5% · +$8.32 (+$0.69) | 2 · 88.5% · +$13.73 (+$0.30), best hit chance 8.4% |
| + one ticket per player | 5 · 30.0% · +$9.80 (−$3.44); 10 players | 3 · 60.5% · +$11.34 (−$1.06); 6 players |
| Earlier draft: 30% floor + 5-pt haircut + one per player + two per game | 0 | 0 |
| **The proposal in section 13** | 1 · 61.3% · +$1.55 (−$0.23) | 0 |

Counts of candidate tickets by element (entry information; Oct 8 / Oct 9): qualified under the old rules 101 / 28 (median hit chance 13% / 6%); hit chance ≥ 20%: 17 / 2; ≥ 25%: 10 / 0; ≥ 30%: 2 / 0; edge
surviving a 5-point haircut: 7 / 7 (best hit chance 27.9% / 8.4%); a 30% floor with a 5-point haircut: 0 / 0.

## 13. The proposed restart policy (one policy, for your approval)

**Up to five tickets a day, zero allowed; each +100 or better; an estimated hit chance of at least 25%; at least +3% edge left after lowering every leg by 3 points (and the existing 5% on the recorded
probabilities); each player on at most one ticket a day; at most two tickets per game; one ticket per leg.** Four numbers change from the rules that ran (floor 0 → 25%, value margin 0 → +3%, per player 2 → 1,
per game 3 → 2); the +100 bar, the 5% bar, the 3-point haircut and the five slots are kept. Digest `cb34b5563d244396`.

**Why each element, and its cost (none is validated; each is a judgement):**

* **25% hit-chance floor (the "strong chance" the objective asks for).** Two legs reach 25% only if both are about a coin flip (0.5 × 0.5); three legs need about 63% each. That puts tickets in the
  region where the held-out validation is densest (shots 2+ at 50–70%: 13,432 player-games; points 1+ above 50% is rare; goals 1+ above 40% has 111) and where the raw model was calibrated or
  under-predicting (it over-predicted below about 40%: shots 2+ at 20–30% said 25.8%, happened 20.7%). *Cost:* fewer tickets and empty days (Oct 9 would have been empty); and a **side effect**: it removes most
  tickets with a points or goals leg, because those legs rarely exceed 55% (80% of Oct 8's tickets at ≥ 25% still had one; at ≥ 30%, none). That is arithmetic, not a view on the market. 30% is as defensible as 25%: on the two audited days a 30% floor gives the same tickets as 25% (one on Oct 8, none on Oct 9) once the value margin and one-per-player apply, so the day-level evidence cannot choose between them; the owner can.
* **+3% edge after the 3-point haircut (the "meaningful value").** The haircut stands in for model uncertainty (the measured between-player error is 8.1 points for shots 2+ and 9.9 for points 1+, so 3 points is
  about a third of a standard deviation, a small safety margin). The extra 3% is a cushion for what the US-feed prices cannot show: Ontario prices are not verified and the void and parlay-reduction rules are
  unknown (`VOID_RULES_VERIFIED=False`). *Cost:* tickets whose entire edge is inside that cushion are skipped. The earlier draft's 5-point haircut (half a standard deviation) was **dropped as impractical**: with a
  30% floor it recorded nothing on either audited day, and alone it favours long shots (on Oct 9 it kept two tickets whose best hit chance was 8.4%).
* **One ticket per player, two per game.** Shared legs raise the chance that no ticket wins (Oct 8: 29.9% against 18.2%; Oct 9: 57.2% against 50.2%); with one per player they cannot share a player. *Cost:*
  the best player is used once; this reduces concentration, it does not add value.
* **Up to five, empty allowed.** The count of five was never evidence-based; an empty slot is a correct answer.

**Why this is not tuned to the streak.** The two numbers I changed from my first draft (30% → 25% and the 5-point haircut → the 3-point haircut with a +3% margin) were changed because the first draft
produced zero tickets on both audited days on entry information alone, not because of any result. The outcomes of the replayed tickets are not reported. On those two days the proposal would have recorded
**one ticket on Oct 8 and none on Oct 9**; I expect most days to record 0 to 2. If you find that too few, the next step is yours (a lower floor), and any change creates a new digest you must approve.

**What it cannot do:** a stronger chance and a bigger edge pull in opposite directions in this pool (the high-probability legs are priced near fair), so few tickets will satisfy both; it does not make a ticket
better, it removes the weakest and the most concentrated.

## 14. Repairs and instruments

| Change | Why | Tests |
|---|---|---|
| Pause switch covering every automatic writer; shadow selection log; shadow scorer; watchdog row | Pause the experiment, keep collecting evidence | `tests/test_recording_pause.py`, `tests/test_postmortem.py` |
| `TicketPolicy` in the selector; proposal as the default; empty-slot explanation; **approval gate on resume** | Enforce the objective without enacting unvalidated numbers | `tests/test_ticket_policy.py` |
| Singles excluded from the model account; account split and reconciliation on Today / Paper Performance / watchdog | Parlay experiment kept pure, nothing reset | `tests/test_book_breakdown.py`, page tests |
| Dependence estimates, dependent-streak Monte Carlo, evidence-target arithmetic, price-versus-model blend in the scorer | State the assumptions behind every probability and target | `tests/test_postmortem.py` |

## 15. What is still uncertain

* Whether the model is overstated on the legs the selector picks (needs on the order of 700–1,200 independent resolved units, or the price-versus-model blend over many nights).
* Why Bourque played 8:15. Players in one game are treated as independent beyond the measured correlations; a persistent model error is not modelled.
* The Oct 7 tickets and any pool before Oct 8 cannot be replayed.
* There are no historical sportsbook prices, so profitability has never been shown; every EV above is model against price. Ontario prices and void rules are unverified.
* How many tickets the proposal will record per day is unknown until the shadow log shows it; I expect few.
