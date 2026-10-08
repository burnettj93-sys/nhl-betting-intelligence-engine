# Add to paper book — $10 (manual tickets)

## What it is

The only way a ticket enters the paper book by hand. It is an explicit button on an option card (Best Options, Players). Browsing, filtering, searching, refreshing and
opening a page never write anything (tests: `tests/test_product_pages.py`, `tests/test_manual_orders.py::test_browsing_writes_nothing`).

## Flow

1. **Click.** The page builds an *order*: the legs, prices, quote times and hit chance the person saw, a fixed $10 stake, a random order id.
2. **File.** The hosted app cannot write the ledger (no durable disk), so the order goes to a durable, authenticated queue — a GitHub issue labelled `paper-order`:
   * *direct (one click)* when the app has the secrets `PAPER_ORDER_TOKEN` (a fine-grained token limited to Issues on this repository) and an allow-list entry (`ORDER_ALLOWED_EMAILS`, matched to a verified `st.login()` identity), and the signed-in
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

The one-click path needs two Streamlit secrets: `PAPER_ORDER_TOKEN` (a fine-grained GitHub token for this repository, Issues read/write) and an allow-list of who may use it — `ORDER_ALLOWED_EMAILS` plus an `[auth]` OIDC sign-in. The exact steps are below. Without them the link path still works end to end.

### Why writes use a supported sign-in, not the platform header (decided 2026-10-08)

Measured on the hosted app (Diagnostics → Order path): Streamlit 1.62.0, the app is private, `st.user` is empty (no email), and the request headers include `X-Streamlit-User`, an opaque 76-character value.
Trust in that header could not be established. Streamlit's own staff, asked whether it is a supported fallback, answered that it "is not a documented or stable public API ... an internal implementation detail
and should not be relied upon for authentication or user identification ... may change or remove this header at any time without notice, and there are no guarantees about its format or presence", and that "the only supported and
stable approach is to configure your own OIDC provider and use `st.login`/`st.user`" (https://discuss.streamlit.io/t/community-cloud-authentication-is-x-streamlit-user-a-supported-fallback/121998, accessed 2026-10-08;
also: since Streamlit 1.42 `st.user` no longer exposes the Community Cloud account email). Whether a client can supply or override the header could not be verified from outside (reaching the app requires the platform's own viewer login, and no
documentation states that the gateway overwrites it), so it is **never used for authorisation**: the earlier viewer-id fingerprint allow-list was removed. Writes are authorised only by `st.login()` (OIDC) → `st.user.email`
(must be verified) → `ORDER_ALLOWED_EMAILS`, plus the `PAPER_ORDER_TOKEN` secret. Reading the app stays governed by the platform's private-sharing list.

Exact steps (the owner does these; no secret goes through a chat):

1. **Google OAuth client** — console.cloud.google.com → select or create a project → *APIs & Services* → *OAuth consent screen* (User type External; app name; your email; add your own Google account under *Test users*; leaving it in "Testing" is fine for one user)
   → *Credentials* → *Create credentials* → *OAuth client ID* → Application type **Web application** → *Authorized redirect URIs*: `https://<your-app-subdomain>.streamlit.app/oauth2callback` (the same host you open the app on, plus `/oauth2callback`) → Create → copy the **Client ID** and **Client secret**.
2. **GitHub token** — GitHub (signed in as the repository owner) → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate: Resource owner yourself; Repository access *Only select repositories* → `nhl-betting-intelligence-engine`; Repository permissions **Issues: Read and write** only; set an expiry; copy the token once.
3. **Streamlit secrets** — share.streamlit.io → the app's menu → Settings → Secrets → paste, filling in your values (generate the cookie secret with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`), then Save (the app restarts and installs Authlib, which `dashboard/requirements.txt` now lists):

   ```
   PAPER_ORDER_TOKEN = "<fine-grained GitHub token from step 2>"
   ORDER_ALLOWED_EMAILS = "<the Google account email you will sign in with>"

   [auth]
   redirect_uri = "https://<your-app-subdomain>.streamlit.app/oauth2callback"
   cookie_secret = "<random string from the command above>"
   client_id = "<from step 1>"
   client_secret = "<from step 1>"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```
4. Open Diagnostics → *Order path*: press **Sign in to enable one-click adding**, sign in with that Google account. The table should read: token yes, sign-in configured yes, signed in yes, on the allow-list yes, one-click ready yes. Press **Run non-staking order-path check**: it files directly, the engine answers ACCEPTED within about two minutes plus a snapshot refresh, and nothing is staked.
5. Only after that is a real one-click stake test meaningful; it is made only when the owner explicitly selects an option.

Without these the link path (a pre-filled GitHub issue that GitHub itself authenticates) keeps working.
