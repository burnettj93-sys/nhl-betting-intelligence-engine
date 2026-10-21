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

## Ranking and slots
Score = 0.25 conservative probability + 0.10 model probability + 0.25 edge + 0.15 calibration confidence + 0.10 freshness + 0.15 model quality, with soft diversification penalties (same game / player /
team / market / price band). A strong bet is never rejected only for sharing a game. The ten slots are released with the puck-drop waves (cumulative cap = ceil(10 x games through the wave / games today),
relaxing in the last 25 minutes before a wave).

## Bankroll
Starts at $500.00. When cash falls below the next $10 stake it is reset to $500.00 (prior losses are never erased; the reset is funding, not winnings); each reset is an append-only funding event (time, balance before, amount, balance after, experiment day, reason
`LEARNING_EXPERIMENT_BANKROLL_RESET`). Betting P/L, ROI, hit rate and calibration come from settled bets only; resets are never profit.

## Automatic end
On 2026-11-03 (Eastern) it ends by date arithmetic alone: nothing new is recorded and the reset rule stops. Open bets still settle. The normal paper-book policy was never changed.

## Edge classes
Every bet is classed at recording time: POSITIVE_EDGE (edge of +2 points or more on an anchored estimate), NEUTRAL_UNCERTAIN_EDGE (within 2 points of zero, or positive on an unanchored estimate) or
LEARNING_SAMPLE_NEGATIVE_EDGE (below -2 points). A negative-edge candidate passes every integrity and support gate, is recorded only to fill the day's sample after the others, and only once no better price for its
wave can still arrive. Daily and final reports show the three classes separately.

## Reports
- Trader (every 10 minutes): records, settles, writes `learning_experiment_status.json` and the postmortem document.
- Daily postmortem (prior Eastern day, breakdowns by market, price band, probability band, edge band, team/game, player, data freshness, selection rank; loss classification through
  `ticket_loss_classifier`; recommendations only).
- 14-day scorecard and the Paper Performance panel.
- Final report (16 questions, recommendations only) written automatically once the window has ended and every bet has settled.

## Activation
`python3 -m deploy.activate_learning_experiment` (dry run) and `--apply` (log first, then approve the policy digest). Until approved the cohort records nothing; a changed policy digest fails closed.
