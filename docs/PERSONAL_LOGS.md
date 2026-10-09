# Personal bet logs

Friends can keep their own paper bets under a code they choose, on the **My Bets** page. A log is completely separate from the model's $500 book.

## What it is

* **My Bets** (hosted app, "Operate" section): open a log with its code, or create one (display name + code). A log shows its own bets (open, settled), its own totals
  (record, P&L, win rate, ROI) and the engine's answer to every order, including bets that were not added and why.
* **Where a bet comes from:** the *Add to my log* control on Best Options, Players (and any card with an option). It always names the destination log first ("Adding to your
  personal log *Casey's picks* — separate from the model book"), has its own stake box (default $10, $1–$1,000), and does nothing until you press the button.
  Browsing, filtering and refreshing never add a bet. Every bet is labelled **Manually added**.
* **Views:** *My bets*, *Model bets* (the engine's $500 book) or *Both* side by side with separate totals. They are never added together.

## Separation from the model book (structural, tested)

| | Model book | Personal logs |
|---|---|---|
| Database file | `operational/paper_bankroll.db` | `operational/runtime/personal_logs.db` |
| Written by | the 15-minute trader only | the order processor (`operational/personal_logs.py`) |
| Cash, exposure, daily slots, P&L, win rate, performance reviews | yes | **no** — a personal bet never reaches any of them |
| Stake | exactly $10, atomic funds check | your own paper amount, no bankroll limit |

`operational/personal_logs.py` never calls the model ledger's writers (a test fails if it ever mentions them). `tests/test_personal_logs.py` adds and settles bets in two
different logs and proves a hash of **every table** of the model ledger and its account state is identical before and after.
The model book's own queries count only `origin = 'AUTOMATIC'` rows (`paper_bankroll.MODEL_BOOK_ORIGIN`), which is what moves the earlier manual ticket out of the model's accounting.

## What a code is — and is not

A code is a name for a log. **It is not authentication.** Whoever knows it can open that log and add bets to it. The engine publishes its data to a public repository, so the bets in a
log are readable by anyone who finds the published file; they are filed under a one-way scrypt hash of the code and the display name, never the code. The page says so before a
log is created. Do not put anything personal in a log or its name (names are limited to letters, numbers, spaces and `. ' _ -`). Paper bets only; no money moves.

Collisions: a new code is checked against the published hashes; the engine refuses to create a log whose hash already exists (`CODE_IN_USE`) unless the order carries the same
`creation_id` as the log's creator (so retries and a first bet filed before the log appears both work). Opening a code that does not exist is refused (`LOG_NOT_FOUND`).
Abuse limits: 40 orders per log per day, 25 new logs per day.

## How a click becomes a bet

1. The hosted page files an order (a GitHub issue labelled `personal-bet`) with the app's write credential. The order carries a hash of the code, never the code; it carries the
   legs and prices the person was looking at.
2. The engine (`operational/manual_order_job.py` every 2 minutes, and the 15-minute trader) answers the order: it **revalidates against the current fresh prices**
   (`RECORDED`; `NEEDS_ACCEPTANCE` if a price or chance moved — nothing is recorded; `REJECTED` if a leg is stale, started or no longer qualifies; `ALREADY_RECORDED` for the same
   bet in the same log the same day). Every answer is stored once by order id, so a double click or a retry cannot add a second bet.
3. Started games settle with the same resolver and rules as the model book (`paper_bet_settlement_driver.terminal_outcome`): a leg that did not play leaves the bet open and visible
   rather than guessing. The log's published view refreshes with the next publish (about 8 minutes at most).

## Owner setup (one step)

The hosted app needs a credential that can create issues in this repository. Create a **fine-grained personal access token** for `burnettj93-sys/nhl-betting-intelligence-engine` with
**Issues: Read and write** and nothing else, then in the Streamlit app's *Settings → Secrets* add:

```toml
LOG_WRITE_TOKEN = "<the token>"
```

Reboot the app. Anyone you invite to the (private) app can then add bets to their own log with one click. Without the secret the button builds a pre-filled GitHub issue that only
the repository owner can submit — useful for you, not for friends. (The earlier name `PAPER_ORDER_TOKEN` is also accepted.) Rotate the token if it is ever exposed.

## The earlier manual ticket

Before logs existed one ticket (`ME3D7C508EBFDD3`, lost, −$10) had been added by hand and sat in the model account. It was **moved out of the model's accounting**: the ledger row is kept
untouched (immutable) for audit, a copy was written to the personal database under *Unclaimed earlier manual tickets* (`migrations` and `audit` tables record when), and the model
book now shows only automatic tickets. The owner can claim it into their own log by choosing a code on My Bets and then running locally:

```bash
python3 -m operational.personal_logs claim-legacy
```

(it prompts for the code without echoing it and for the display name).
