# Personal bet logs

Friends keep their own paper bets in a **personal account named by their last name**, on the **My Bets** page, and build their own parlays on the **Parlay Builder** page. An account is completely
separate from the model's $500 book and from every other account. (The earlier code-based logs still work; see the end of this page.)

## Accounts, bankrolls and the Paper Parlay Builder

* **Open an account:** type your last name on My Bets. The account is filed as the lowercase letters of the name (`Burnett` → `burnett`). If someone already has it, the page tells you and offers
  the next numbered one (`Burnett 2`, `Burnett 3`…); to open an existing account you type the same name (and number) and your passcode. Nobody can take over a name that exists: a creation
  order for it is refused (`NAME_TAKEN`), and an order for it signed with a different passcode is refused (`BAD_SIGNATURE`).
* **Passcode, not a long key:** creating an account generates an 8-character passcode (`XXXX-XXXX`, 40 bits, shown once). You type your last name and the passcode to open the account on any
  device. The passcode never travels: the browser derives an Ed25519 key pair from it, only the **public** half is stored and published, and every order is **signed**. Because the public half
  is published, an attacker could try passcodes offline, so the derivation is deliberately slow (scrypt, 32 MiB, roughly 0.2 s per guess; about a thousand CPU-years for all 2^40). A lost passcode cannot be recovered.
* **Your own $500:** every account starts with a **paper balance of $500** that belongs to it alone. The page shows **available cash**, **open stakes**, **payouts received**, **settled P&L**
  and **equity**. Cash is *recomputed from the account's own bets* (`500 + settled P&L − open stakes`), never stored, so reopening the page, the database or the account cannot reset it.
  A stake larger than the available cash is refused (`INSUFFICIENT_FUNDS`) in the same transaction that would have recorded it. A win pays stake plus profit, a loss costs the stake, a void returns it.
* **Separate from everything else:** the model's book is `paper_bankroll.db`; accounts live in `personal_logs.db`. `tests/test_personal_accounts.py` creates two accounts, builds, adds and settles bets,
  and proves a hash of every table of the model ledger is identical before and after; `deploy/qa_two_logs.py` does the same against a copy of the real ledger. One account cannot write to another
  (a forged order is `BAD_SIGNATURE`) and a refused order from a stranger does not use up an account's daily limit. The watchdog re-derives every account's cash a second way each half hour.
* **Paper Parlay Builder** (page *Parlay Builder*): search a player → choose a market (shots on goal, points, anytime goal; saves only for a confirmed starter) → choose a line from DraftKings'
  current price list → **Add** → browse other players (the slip is kept) → edit (a new line for the same player and market replaces the old one) or remove legs → choose a stake → **Submit to
  *your account***. Browsing never files anything. The slip shows each leg's actual price and quote age, the combined price, the possible return, and the destination account and its cash.
* **What the combined price is:** one leg is a real DraftKings price. Legs from different games are shown as an **estimate** (the product of each leg's price). Legs from the **same game** are
  shown as **multiplied, not a DraftKings quote** — DraftKings prices same-game combinations together with its own adjustment — and need a tick-box acknowledgement. The estimate is never described
  as a sportsbook quote, and the record stores which of the three it was.
* **Builder bets are your choices:** no model edge and no +100 requirement. They still need, checked **by the engine when it processes the order**: a real DraftKings price for every leg that is
  fresh (judged from the provider's quote time), a game that has not started, a player who can be identified, sufficient cash, and no duplicate of the same slip in the same account the same day.
  If a price moved nothing is recorded and the new prices are returned (`NEEDS_ACCEPTANCE`). Settlement uses the same resolver and rules as the model book; a leg whose player did not play leaves the bet open (`UNRESOLVED`)
  until the sportsbook's void/parlay-reduction rule is verified for Ontario (it is not yet).
* **Existing logs are migrated once, idempotently** (`python3 -m operational.personal_logs migrate-bankrolls`, also run by each trader cycle): every log gets its own $500 starting balance and its
  current state is recorded in `migrations`/`audit`; open stakes are counted once (cash is derived from the bets, which are never copied), settled history is untouched, and the unclaimed
  earlier-ticket bucket has no bankroll.

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
| Stake | exactly $10, atomic funds check against the $500 book | your own paper amount ($1–$1,000), checked against **your own** $500 account |

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
and after). Product readiness on Diagnostics changes from NOT VERIFIED to WORKING only when an order sent by the app's own write path has been processed. The engine-side proof is `python3 deploy/verify_personal_workflow.py`: it reports PENDING until two logs were created through the app's write path and one bet has settled, and FAIL if a personal bet ever appears in the model ledger or a book does not reconcile.

## The earlier code-based logs

Logs created before last-name accounts keep working unchanged (a code names the log, a 20-character write key signs; their bets, history and totals are kept, and each got its own $500 by the migration above).

## The earlier manual ticket

Before logs existed one ticket (`ME3D7C508EBFDD3`, lost, −$10) had been added by hand and sat in the model account. It was **moved out of the model's accounting**: the ledger row is kept
untouched (immutable) for audit, a copy was written to the personal database under *Unclaimed earlier manual tickets* (`migrations` and `audit` tables record when), and the model
book now shows only automatic tickets. The owner can claim it into their own log by choosing a code on My Bets and then running locally:

```bash
python3 -m operational.personal_logs claim-legacy
```

(it prompts for the code without echoing it, for the display name, and for a write key — press Enter to have one generated and printed once).
