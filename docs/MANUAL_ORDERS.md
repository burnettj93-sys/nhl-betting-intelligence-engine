# Owner evidence queue (goalie confirmations, Ontario price checks, path checks)

**Scope correction, 2026-10-14.** Bets submitted by individual people were removed: there are no accounts, passcodes, personal logs or "add to the paper book" orders, and `LOG_WRITE_TOKEN` is
not needed. The $500 model book receives only the engine's own automatic tickets (one primary ticket per Eastern day at exactly $10). The earlier personal records are kept untouched as an
inactive archive (`operational/archive/`, written once by `deploy/archive_personal_logs.py`, never read by the product). GitHub issues carrying the old `paper-order` shape are not read.

## What the queue still does

The hosted dashboard is read-only (Community Cloud has no durable disk). Three kinds of **owner-only evidence records**, which stake nothing, can be filed as a small JSON document in a GitHub issue
and are answered by the engine on its 15-minute trader cycle (`operational/manual_orders.py`):

* `ONTARIO_VERIFICATION` — the owner's own look at an Ontario price for a leg (kept as evidence; it never changes a US-feed quote).
* `GOALIE_CONFIRMATION` — a starting goalie confirmed by hand with its source and time (the only way a saves leg can become confirmed while no automatic source is permitted).
* `ORDER_PATH_CHECK` — a non-staking check that the filing path works (below).

Only issues opened by the repository owner are read; anything else is ignored and listed. No ledger is written by any of these.

## Proving the filing path without staking anything

Diagnostics → "Evidence-queue path check" shows, as yes/no values only (the token and the allow-list are never displayed): whether the token secret is configured, how many allowed emails are configured,
whether the app has a signed-in viewer, whether that email is on the allow-list, and whether direct filing is ready. "Run non-staking order-path check" files an `order-path-check` issue
(directly when ready, otherwise through a pre-filled GitHub issue). The result appears on the same panel after the next snapshot publish; **no ticket or stake is created and the ledger is not touched.**

## One-time setup for direct filing (optional)

Direct filing needs two Streamlit secrets: `PAPER_ORDER_TOKEN` (a fine-grained GitHub token for this repository, Issues read/write) and an allow-list of who may use it — `ORDER_ALLOWED_EMAILS` plus an `[auth]` OIDC sign-in.
Without them the link path (a pre-filled GitHub issue that GitHub itself authenticates) keeps working. None of this is needed to read the product.

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
4. Open Diagnostics → *Evidence-queue path check*: press **Sign in to enable direct filing**, sign in with that Google account. The table should read: token yes, sign-in configured yes, signed in yes, on the allow-list yes, direct filing ready yes. Press **Run non-staking order-path check**: it files directly, the engine answers ACCEPTED within about two minutes plus a snapshot refresh, and nothing is staked.
Without these the link path (a pre-filled GitHub issue that GitHub itself authenticates) keeps working.
