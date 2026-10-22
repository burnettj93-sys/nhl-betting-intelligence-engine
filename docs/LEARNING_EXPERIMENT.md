# POSTMORTEM_LEARNING_14D: temporary 14-day paper learning experiment

Owner authorisation (chat, 2026-10-20): 10 real, eligible paper bets per Eastern day at $10 each, actual odds -150 or better, 2026-10-20 through 2026-11-02 inclusive (America/Toronto dates).
Paper only. No real-money wagering. Eggy stays at one official pick a day on its own ledger.

## What it is
- A separate ledger (`operational/runtime/learning_cohort.db`, shared with the pinned release by the runtime symlink), a separately labelled experiment bankroll that starts at $500.00, and a distinct cohort name on every bet.
- It is not Eggy and not a betting recommendation: many singles, no flagship conviction bar, a price floor. Eggy's pick, record and bankroll are untouched.

## What it never relaxes
Every accuracy and integrity gate: real event, real market with a verified contract, verified identity on a current official roster, a real DraftKings observation with a valid, non-future time taken from the newest capture of that market
(an older capture never resurrects a withdrawn market; a moneyline must come from the latest league pull with both sides from the same pull), event not started, a settlement path, no duplicate, a model estimate for the threshold,
no demo data, no fabricated price. If fewer than 10 legitimate bets exist the day records fewer and states why.

## Price age is metadata, not a gate (owner policy 2026-10-21)
For this cohort only, a real quote is not rejected because it is "old" by a minute threshold. Every bet stores the actual last observed price with price_capture_timestamp (when this system last saw it),
price_provider_update_utc, price_age_at_recording_min, provider_quote_age_at_recording_min, price_status (CURRENT <=5 min, RECENT <=30, LAST_KNOWN_PREGAME <=120, VERY_OLD_PREGAME beyond) and the age bucket
(0-5, 6-15, 16-30, 31-60, 61-120, 120+). Price age carries no ranking weight. Edges are always "model edge at the last observed price", never a current execution price. The cohort collects its own pool
(`collect_candidate_legs(advisory_price_age=True)`); Eggy and the normal book use the strict limits, unchanged. The postmortem reports results by age bucket and keeps SELECTION quality (hit rate against the model's
probability) apart from PRICE/VALUE quality (ROI against the edge at the last observed price), and measures same-game dependence rather than assuming independence.

## Decisions, ranking and slots (updated 2026-10-21: docs/EGGY_INTELLIGENCE.md)
Every eligible candidate is reviewed before it is ranked (the game, the player, the market, why the model disagrees with the market, the case against, does the thesis survive) and the review decides RECORD,
LOWER_PRIORITY or PASS; a PASS is never recorded. The survivors are ordered lexicographically (action, edge class, contradiction level, calibration confidence, hit chance, edge: there is no weighted score), and a
game that already holds three of the day's bets sends its further candidates behind every other game's. Each bet is stored with the review card it was recorded with, and the postmortem judges the DECISION
apart from the OUTCOME. The ten slots are released with the puck-drop waves (cumulative cap = ceil(10 x games through the wave / games today), relaxing in the last 25 minutes before a wave); a LOWER_PRIORITY bet
is recorded only in the last 25 minutes before its own game and inside the strict caps. If the review cannot run (the evidence file or the team or skater data missing or stale) nothing is recorded and the status
says why; an engine error in one candidate's review costs only that candidate and is reported as an error, not a judgment.

## Bankroll
Starts at $500.00. When cash falls below the next $10 stake it is reset to $500.00 (prior losses are never erased; the reset is funding, not winnings); each reset is an append-only funding event (time, balance before, amount, balance after, experiment day, reason
`LEARNING_EXPERIMENT_BANKROLL_RESET`). Betting P/L, ROI, hit rate and calibration come from settled bets only; resets are never profit.

## Automatic end
On 2026-11-03 (Eastern) it ends by date arithmetic alone: nothing new is recorded and the reset rule stops. Open bets still settle. The normal paper-book policy was never changed.

## Edge classes
Every bet is classed at recording time on the main engine's own 3-point threshold (the first version used 2 points, which on a moneyline measured only the bookmaker's margin on that side and favoured underdogs by
arithmetic): POSITIVE_EDGE (a model edge of +3 points or more with calibration at least MEDIUM), NEUTRAL_UNCERTAIN_EDGE (within 3 points of zero, or positive on an estimate nobody can vouch for) or
LEARNING_SAMPLE_NEGATIVE_EDGE (-3 points or worse). A moneyline is judged on the hockey model's edge, a prop on the estimate used less the price's implied probability. A negative-edge candidate is only ever a
bounded, LOWER_PRIORITY experiment (the event at least 25% likely, the price within 10 points of the estimate, calibration at least MEDIUM) recorded in its own game's last 25 minutes. Daily and final reports show
the three classes separately. (Rows recorded before 2026-10-21 carry the 2-point classes and, for the four moneylines of Oct 20 and the first of Oct 21, Elo as their `model_probability`; later rows carry the
hockey-model probability: the stored card says which.)

## Reports
- Trader (every 10 minutes): records, settles, writes `learning_experiment_status.json` and the postmortem document.
- Daily postmortem (prior Eastern day, breakdowns by market, price band, probability band, edge band, team/game, player, data freshness, selection rank; loss classification through
  `ticket_loss_classifier`; recommendations only).
- 14-day scorecard and the Paper Performance panel.
- Final report (16 questions, recommendations only) written automatically once the window has ended and every bet has settled.

## Activation
`python3 -m deploy.activate_learning_experiment` (dry run) and `--apply` (log first, then approve the policy digest). Until approved the cohort records nothing; a changed policy digest fails closed.
