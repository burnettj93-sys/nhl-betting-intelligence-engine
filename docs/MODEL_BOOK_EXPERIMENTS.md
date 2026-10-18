# Model book experiments and the 2026-10-18 reset

The model paper book is organised in **experiments**. Exactly one is ACTIVE; every account, performance, reconciliation, slot and watchdog read follows it. An archived experiment keeps all of its tickets, results, balance and policy versions exactly where they were and is reported apart.

## What the reset did (owner authorisation: "reset the active model paper bankroll to $500 for the corrected workflow")
* **Experiment 1 (archived):** the original model book, 2026-10-07 to the reset: 14 automatic tickets (+1 legacy manually-added record kept for audit, never counted), final cash $439.62, settled P&L -$60.38, no open stakes. Its rows stay in `paper_bets` under `experiment_id = 'EXP-1'` (nothing moved, copied over, deleted or edited).
* **Experiment 2 (active):** $500 cash, $0 open stakes, $0 P&L, no tickets. New tickets are stamped `EXP-2`.
* Before anything changed, the reset wrote (1) a database copy `operational/backups/paper_bankroll_experiment_1_final_<timestamp>.db`, (2) a JSON archive `operational/archive/model_book_experiment_1_<timestamp>.json` (tickets, results, audit log, balance history, the policy versions with digests, the owner's approval record and the recording-pause state), and (3) a summary frozen into the experiment row.
* **Idempotent:** `paper_experiment_resets.reset_id` is the key (`2026-10-18-corrected-workflow`). A restart, redeploy or repeated command reports ALREADY_APPLIED and changes nothing; a unique partial index allows only one ACTIVE experiment. Nothing calls the reset automatically: it is `python3 -m deploy.reset_model_bankroll --reset-id <id>`, run by hand.
* **Open tickets at a reset** (there were none) would stay open in the previous experiment, settle there (settlement scans every experiment) and never enter the new bankroll.
* **Unchanged:** automatic recording stays PAUSED (digest `1fad3ba03503cf94` unapproved); the +0.02 cap and every probability rule; the Parlay Builder stays read-only; the prediction ledger and its scoring are separate and unaffected.

## Where each surface reads
| Surface | Reads |
|---|---|
| Today | the ticket board's `account` (active experiment, with its label and start time) |
| Paper Performance | the active experiment; "Previous experiments (archived, not included above)" lists each archived one with its own final numbers |
| Ticket History | the active experiment's tickets; an Experiment selector opens an archived experiment's tickets |
| Settlement, revalidation | every experiment's open tickets |
| Reconciliation / watchdog `reconciliation` | the active experiment re-derived from raw rows with its own starting bankroll; fails on zero or several ACTIVE experiments or a ticket with an unknown experiment |
| `ticket_consistency` | also compares the board's experiment id with the ledger's active one |
| Daily slots (`recorded_today`, `todays_real_parlay_usage`) | the active experiment |
