# Add to paper book — $10 (manual tickets)

## What it is

The only way a ticket enters the paper book by hand. It is an explicit button on an option card (Best Options, Players). Browsing, filtering, searching, refreshing and
opening a page never write anything (tests: `tests/test_product_pages.py`, `tests/test_manual_orders.py::test_browsing_writes_nothing`).

## Flow

1. **Click.** The page builds an *order*: the legs, prices, quote times and hit chance the person saw, a fixed $10 stake, a random order id.
2. **File.** The hosted app cannot write the ledger (no durable disk), so the order goes to a durable, authenticated queue — a GitHub issue labelled `paper-order`:
   * *direct (one click)* when the app has the secrets `PAPER_ORDER_TOKEN` (a fine-grained token limited to Issues on this repository) and an allow-list entry (`ORDER_ALLOWED_VIEWER_IDS` or `ORDER_ALLOWED_EMAILS`), and the signed-in
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

The one-click path needs two Streamlit secrets: `PAPER_ORDER_TOKEN` (a fine-grained GitHub token for this repository, Issues read/write) and an allow-list of who may use it — `ORDER_ALLOWED_VIEWER_IDS`
(the viewer's fingerprint; this is what the hosted app can actually identify) or `ORDER_ALLOWED_EMAILS` (works only if the app later gains Streamlit login). The exact steps are below. Without them the link path still works end to end.

### What the hosted app's authentication actually supplies (measured 2026-10-08)

Diagnostics → "Order path" shows it, names only: Streamlit 1.62.0; the app is private ("Only specific people can view this app"); `st.user.is_logged_in` is `None` and `st.user` has **no fields** (no email);
the platform's request headers include `X-Streamlit-User`, an **opaque 76-character value** (not an email, not a signed token with claims). So an email allow-list cannot work as first designed
(`ORDER_ALLOWED_EMAILS` would never match). The workable identity is that opaque viewer id, matched by a one-way fingerprint: Diagnostics shows the signed-in viewer's 12-character fingerprint,
and `ORDER_ALLOWED_VIEWER_IDS` lists the fingerprints allowed to use the one-click path. An email allow-list still works if you later add Streamlit's own login (`st.login`, an OIDC provider), which needs a
provider client id/secret you would create — not done. Trust model: the header is added by the platform's gateway after viewer authentication, outsiders cannot reach the app, and the id is never displayed to anyone but its owner;
an invited viewer who learned another viewer's raw id could replay it, so keep the invite list to people you trust. The worst case is a $10 paper order.

Exact steps (nothing secret is ever pasted into a chat):

1. On GitHub, signed in as the repository owner (`burnettj93-sys`): Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Resource owner: yourself. Repository access: *Only select repositories* →
   `nhl-betting-intelligence-engine`. Repository permissions: **Issues: Read and write** (nothing else). Expiration: your choice (note the date). Generate and copy the token once.
2. Open the hosted app → Diagnostics → "Order path". Read your **fingerprint** (12 characters) from the table row "App supplies an opaque platform viewer id".
3. On share.streamlit.io, open the app's menu → Settings → Secrets and paste (with your token and fingerprint), then Save; the app restarts:

   ```
   PAPER_ORDER_TOKEN = "<the token from step 1>"
   ORDER_ALLOWED_VIEWER_IDS = "<your fingerprint from step 2>"
   ```
4. Diagnostics → "Order path": all rows should read yes and "This viewer is on an allow-list" should say "(by viewer id)". Press "Run non-staking order-path check": within about two minutes plus a snapshot refresh the panel shows ACCEPTED. Nothing is staked.
5. Only then is the real one-click stake test meaningful; it will be made only after you select an option explicitly.
