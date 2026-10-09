# Puck line / spread: what is done, what is still needed

One real payload does **not** make the market ready. The market needs three separate things, and only the first is partly done.

| Need | State | What remains (and who) |
|---|---|---|
| **1. Settlement** (official final score, extra-time margin counts as one goal, ledger mapping) | Done and tested (`resolve_puck_line`) | Nothing. Rules for postponements/ties follow the shared settlement rules; the Ontario void rule stays unverified. |
| **2. Provider contract** (`spreads` market parses to the right team, line and side; both sides present; main and alternate lines understood; prices match what the book shows) | Validator, leg builder and certification test are written; the leg builder cannot select anything (`provider_contract_verified` is False) | **Owner:** authorise real captures. Not one payload: at least a handful across different games and times (about 5–10 credits), including a game with an alternate puck line and one near puck drop, so the shapes the parser will meet are the shapes it has seen. `python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit` runs one capture (1 credit); I will add the rest of the certification checks once real payloads exist. I will not spend credits without authorisation, and an earlier capture request was rejected by the approval layer and was not bypassed. |
| **3. A validated probability model** | The Skellam margin model failed (did not beat the base rate). A direct-logistic alternative (`puck-line-direct-v1`) is frozen and scored only on 2026-27 games: 10 finished of a required 300 | **Time and games:** about 300 finished games, i.e. roughly the first half of the 2026-27 regular season, then a verdict. Beating the base rate on those games is the minimum for validation; it still would not be evidence that prices can be beaten (that needs historical puck-line prices, which do not exist). |

Until 2 and 3 are both met the puck line is excluded from selection, by construction and by test.
