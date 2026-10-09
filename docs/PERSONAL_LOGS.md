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

## What is public, what is protected (read this before using it)

* **Reading is not private.** The engine publishes its data to a *public* GitHub repository (that is what lets the free hosted app read it). Anyone can open the published file and read every
  log's bets, filed under a one-way hash of the log's code and a display name — never the code itself. That is the cost of a free, always-on app with no account system. Do not put anything
  personal in a log or its name. Paper bets only; no money moves.
* **Writing is protected.** Each log has a **write key**: 20 random characters (100 bits) generated when the log is created and shown **once**. Knowing a log's code lets you *read* it, not add to it.
  The app derives an Ed25519 key pair from the write key; only the **public** half is stored and published. Every order (add a bet, create a log) is **signed** with the private half, which exists only in
  the browser session of whoever holds the key. The engine verifies the signature and refuses anything else (`BAD_SIGNATURE`). The order queue is public too, but a signature in it cannot be reused
  to write anything else (every order id is answered once, and the signature covers the whole order), so the public queue leaks nothing that grants access.
* **Why this and not a password:** a password would have to travel in the (public) order. A signature does not. It is the least machinery that keeps writes private on a public queue: no accounts,
  no server-side secrets, one extra string to save.
* **Lost key:** cannot be recovered (the engine never has it). The log stays readable; create a new one.
* **Opening view-only:** enter only the code to read a log; the page then offers no add button.
* The code itself is a *name*: 8+ characters you choose (or accept the suggestion). It is **not** the secret.

## How a click becomes a bet

1. The hosted page files an order (a GitHub issue labelled `personal-bet`) with the app's write credential. The order carries a hash of the code, never the code; it carries the
   legs and prices the person was looking at.
2. The engine (`operational/manual_order_job.py` every 2 minutes, and the 15-minute trader) answers the order: it **revalidates against the current fresh prices**
   (`RECORDED`; `NEEDS_ACCEPTANCE` if a price or chance moved — nothing is recorded; `REJECTED` if a leg is stale, started or no longer qualifies; `ALREADY_RECORDED` for the same
   bet in the same log the same day). Every answer is stored once by order id, so a double click or a retry cannot add a second bet.
3. Started games settle with the same resolver and rules as the model book (`paper_bet_settlement_driver.terminal_outcome`): a leg that did not play leaves the bet open and visible
   rather than guessing. The log's published view refreshes with the next publish (about 8 minutes at most).

## Owner setup (the exact steps)

The hosted app needs one credential: permission to create issues (the order queue) in this repository. Nothing else.

1. GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name:** `nhl-app-log-writer`. **Expiration:** 90 days (put a reminder; a shorter life limits damage if it leaks).
3. **Resource owner:** `burnettj93-sys`. **Repository access:** *Only select repositories* → `nhl-betting-intelligence-engine`.
4. **Permissions → Repository permissions:** **Issues: Read and write**. Leave everything else as *No access* (Metadata: Read-only is added automatically). No account or organisation permissions.
5. **Generate token** and copy it (it starts with `github_pat_`). Do not paste it anywhere else, and do not send it to me.
6. Open the app on Streamlit Community Cloud → **Manage app** → ⋮ → **Settings** → **Secrets**, and add one line:

```toml
LOG_WRITE_TOKEN = "github_pat_…the token…"
```

7. **Save**, then ⋮ → **Reboot app**. My Bets then stops saying "no write credential configured".

If the token is ever exposed: GitHub → Settings → Developer settings → revoke it, make a new one, replace the secret. Orders are only processed when the issue was opened by the repository owner's account, so
a token from another account cannot file orders.

## The verification I run once the secret exists (isolated test data)

On the hosted app: **My Bets → Create a log** (name "QA test", the suggested code) → save the code and write key shown once → **Open** it with the write key → add one option from Best Options → wait for the engine's answer
(≤ ~10 minutes) and confirm it appears under *Open bets* → reload and reopen on another browser (durability) → after the game finishes confirm it moves to *Settled* with the right result. Also: open the same log
with the code only and confirm there is no add button; try the wrong key and confirm it is refused. The test log is a personal log: it never touches the model book (Today / Paper Performance are compared before
and after). Product readiness on Diagnostics changes from NOT VERIFIED to WORKING only when an order sent by the app's own write path has been processed.

## The earlier manual ticket

Before logs existed one ticket (`ME3D7C508EBFDD3`, lost, −$10) had been added by hand and sat in the model account. It was **moved out of the model's accounting**: the ledger row is kept
untouched (immutable) for audit, a copy was written to the personal database under *Unclaimed earlier manual tickets* (`migrations` and `audit` tables record when), and the model
book now shows only automatic tickets. The owner can claim it into their own log by choosing a code on My Bets and then running locally:

```bash
python3 -m operational.personal_logs claim-legacy
```

(it prompts for the code without echoing it, for the display name, and for a write key — press Enter to have one generated and printed once).
