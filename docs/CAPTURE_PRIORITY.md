# Capture priority and the data coverage floor (credit plan version 5)

An operations rule, not a betting rule. Nothing here changes a probability, a threshold, an eligibility test, GOOD / QUESTIONABLE, the reuse cap, the portfolio, the flagship or Eggy.
It decides only **which sportsbook prices the day's credit share buys**, so the learning engine always has enough shooters to review.

## Priority (strict: a layer gets credits only once the layer above has reached every eligible game the day's share can reach)

1. **Shots-on-goal ladder**, one credit a game, across as much of the slate as the share allows.
2. **Player points** (anytime goals when points are not first class), one credit a game, on the games that have shots.
3. Extra league-wide moneyline / spread pulls for later waves (cheap, league-wide).
4. **Goalie saves**, last: they get only what the layers above leave.

Before v5 a game cost a fixed three-credit bundle (shots + points + saves), so a thin share priced fewer games: Oct 30 had a 6.55-credit share, bought two bundles, and left the third game unpriced.
Under v5 the same share prices shots and points on all three games and nothing on saves.

## The data coverage floor

`docs/validation/capture_coverage_floor.json`, produced by `deploy/derive_capture_floor.py` from the engine's own history (read-only):

- distinct legs a full day needs = the most distinct legs any day that reached the 10-ticket target actually used (22; those days used 18-22);
- eligible legs per shots-priced game = the 10th percentile of each day's best cycle (`parlay_legs_eligible` / games captured then): 6.35;
- floor games = ceil(22 / 6.35) = **4**.

Every day's share is first floored at `min(games on the slate, floor)` shots credits, carved out of the pool before the weighted split (so the cycle total is unchanged). If the share still cannot reach the floor the plan says so
(`coverage.met` false and a `DATA COVERAGE FLOOR NOT MET` note), never silently. Re-derive with `python3 deploy/derive_capture_floor.py --write` as the sample grows.

## Slate size

The same rule adapts by itself: a large slate spreads shots across waves (the existing wave-fair `select_games`), a small slate has credits left after shots and goes deeper (points on every game, then saves).

## Replay

`python3 deploy/replay_capture_plan.py DAY plan_time remaining 'gid=start,...' --ahead 'day=n'` rebuilds a past day's capture plan from the ledger as it stood, in scratch state.

## What this does not fix

A captured price is only useful if the machine is awake to record the tickets built from it. Tickets with a lesser leg wait for their first game's last 25 minutes (`learning_cohort.RELAX_MIN`); a closed lid on battery
(clamshell sleep) during that window loses them. That is a power setting, not a credit allocation.
