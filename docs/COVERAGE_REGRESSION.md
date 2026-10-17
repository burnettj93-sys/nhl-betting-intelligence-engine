# Why coverage fell, and what was repaired (2026-10-17)

Evidence: the credit ledger and its provider-header remaining balances, the odds archive, the moneyline pregame audit, config history in git, the trader log and the macOS power log (`pmset -g log`).

## What existed earlier
Early in the cycle (Oct 1-8) the engine bought a league-wide moneyline at each start-time cluster (T-35), a morning shots look, shots/goals pulls per game, and goalie-saves lines for every game. That produced moneylines, shots ladders and saves prices on most games.

## What reduced it (in order)
1. **Redundant spend consumed the cycle.** Per-cluster T-35 league pulls (several a day, each re-buying the same moneyline), the morning player look (the same ladder re-bought hours before the real capture) and all-game saves pulls cost roughly **215 of the cycle's 500 credits by Oct 8**. This is *our planner's allocation*, not the provider and not a balance error.
2. **The planner then rationed what was left** to about 11 credits a day (flat share), enough for 1-2 games, and limited saves to *confirmed* starters, of which there were none (no permitted source confirms starters). Result: almost no saves prices, few goals prices.
3. **The Mac slept** Oct 14-17 (gaps of 13.7 h, 17.4 h, 14.3 h in the trader log; `pmset` shows the sleeps). Every T-35 window in that period was missed. That is a *host* problem, independent of credits. It is now reported as MACHINE_ASLEEP only where the power log supports it, otherwise as an observed engine outage.
4. **Provider:** no outage or rate-limit changed coverage; costs matched the documented one credit per market per game (`docs/validation/credit_cost_audit.json`). **Balance:** the allowance itself is small (500/month). **Reserve:** the 20-credit reserve held but was not the cause.

## Useful versus redundant spend
Useful: the shared morning league pull, one coordinated shots+goals+saves capture per selected game, recovery retries of genuinely missing markets. Redundant: per-cluster T-35 league pulls, the morning player look, the paid tomorrow check, blanket re-pulls to refresh unchanged prices.

## Repairs (all in code, tested)
Wave capture timeline; budget divided across the remaining schedule before spending; bundle of the three markets in one call; saves prices collected regardless of confirmation (confirmation still gates recommendation); reserve-then-settle ledger; redundant pulls removed; missed windows attributed from evidence; spreads taken from the league call that already runs; frozen pregame analysis kept after start.

## Still true
At 192 credits and 15 days left the free allowance prices about 2 games a day. That is an arithmetic limit, shown on the board, not a defect.
