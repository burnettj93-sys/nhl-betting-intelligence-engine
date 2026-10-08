# Add to paper book — $10 (manual tickets)

## What it is

The only way a ticket enters the paper book by hand. It is an explicit button on an option card (Best Options, Players). Browsing, filtering, searching, refreshing and
opening a page never write anything (tests: `tests/test_product_pages.py`, `tests/test_manual_orders.py::test_browsing_writes_nothing`).

## Flow

1. **Click.** The page builds an *order*: the legs, prices, quote times and hit chance the person saw, a fixed $10 stake, a random order id.
2. **File.** The hosted app cannot write the ledger (no durable disk), so the order goes to a durable, authenticated queue — a GitHub issue labelled `paper-order`:
   * *direct (one click)* when the app has the secrets `PAPER_ORDER_TOKEN` (a fine-grained token limited to Issues on this repository) and `ORDER_ALLOWED_EMAILS`, and the signed-in
     viewer's email is on the list;
   * *link* otherwise: a pre-filled GitHub issue opens; pressing "Submit new issue" while signed in as the repository owner files it.
3. **Answer.** `operational/manual_order_job.py` (every 2 minutes) and the 15-minute trader read the queue. Only issues opened by the repository owner are orders (the repository is public;
   anything else is ignored and listed). Each order is revalidated against the current fresh prices and ends in exactly one stored answer in `manual_orders`:
   * **RECORDED** — one $10 ticket, origin `MANUALLY_ADDED`, id prefix `M`, same account and atomic funds check as automatic tickets;
   * **ALREADY_RECORDED** — the same order id, the same bet added earlier, or the same bet already held by an automatic ticket today (no second stake);
   * **NEEDS_ACCEPTANCE** — a price or the hit chance moved since the person looked. Nothing is recorded; the page shows the new details and an "Accept new details and add — $10" button that files a new order carrying them;
   * **REJECTED** — a leg is stale, withdrawn or started; the ticket no longer reaches +100 with value; not enough cash; malformed order; stake other than $10; same-game legs.
4. The page shows the answer after the next data publish (the job republishes immediately after answering).

## Guarantees

* Exactly $10; invalid stakes or odds write nothing (database trigger and validation).
* Order id and bet identity are idempotent: repeated clicks, retries and concurrent processors cannot create two tickets (`manual_orders` primary key, ledger idempotency key, `BEGIN IMMEDIATE`).
* Frozen provenance: the accepted legs and prices, order id, source issue, receipt and revalidation times, price basis and jurisdiction note are stored in `provenance_json`; `origin`, `provenance_json` and `legs_json` are immutable.
* A manual ticket never takes one of the five automatic daily slots, but it is part of the account, the exposure tables and settlement. Performance is reported separately for AUTOMATIC, MANUALLY_ADDED and ALL (Paper Performance, Morning Review).
* Needs cash: no top-up; below $10 the order is rejected.
* A single's price is a DraftKings quote; a two-leg parlay's price is an *estimate* (product of the legs' prices). Both are US-feed prices, not verified for Ontario.

## Proving the one-click path without staking anything

Diagnostics → "Order path (one-click add) check" shows, as yes/no values only (the token and the allow-list are never displayed): whether the token secret is configured, how many allowed emails are configured,
whether the hosted app supplies a viewer email (`st.user.email`), whether that email is on the allow-list, and whether the direct path is ready. "Run non-staking order-path check" files an `order-path-check` issue
(directly when the path is ready, otherwise through a pre-filled GitHub issue). `operational/manual_orders.py::process_path_check` accepts it only from the repository owner (the same author check as orders), answers on the issue,
and records the result; **no order, ticket or stake is created and the ledger is not touched.** If the issue was opened by someone else the result is listed as `IGNORED_AUTHOR` with the author's login, which is how a token that belongs to the
wrong account shows itself. The result appears on the same Diagnostics panel after the next snapshot publish.

## One-time setup for the one-click path

In Streamlit Cloud → app → Settings → Secrets add:

```
PAPER_ORDER_TOKEN = "<fine-grained GitHub token: this repository only, Issues: read and write>"
ORDER_ALLOWED_EMAILS = "<the email you sign in to the app with>"
```

Without these the link path still works end to end.

Exact steps (nothing secret is ever pasted into a chat):

1. On GitHub, signed in as the repository owner (`burnettj93-sys`): Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Resource owner: yourself. Repository access: *Only select repositories* →
   `nhl-betting-intelligence-engine`. Repository permissions: **Issues: Read and write** (nothing else). Expiration: your choice (note the date). Generate and copy the token once.
2. On share.streamlit.io, open the app's menu → Settings → Secrets, paste the two lines above with the token and the email you sign in to the app with, Save. The app restarts.
3. Open the app → Diagnostics → "Order path" and press "Run non-staking order-path check". Expected: all five rows `yes`, then a green "ACCEPTED" result within about 2 minutes plus a snapshot refresh.
4. If "App supplies a viewer email" says `no`, the app is not exposing the signed-in email in this sharing mode; then the direct path cannot identify the viewer and the link path remains the working route (the Diagnostics panel states which).
