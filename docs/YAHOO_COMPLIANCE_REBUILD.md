# Yahoo Compliance Rebuild

**Date:** 2026-09-24. **Authority:** the real, fully-executed Yahoo API Access and Use Agreement (signed by the owner 2026-09-14, countersigned by Yahoo 2026-09-21) — read directly before any of this work, not worked from memory or generic Yahoo docs.

## Item 7: audit of existing persistence — before any change

Inspected `fantasy/yahoo/cache.py` and `fantasy/storage/fantasy_store.py` and their actual stored data, as required, before touching anything.

**Finding: (C) schema only.** `fantasy/storage/fantasy_cache.db` does not exist as a file at all — never created. `fantasy/storage/fantasy_store.db` exists, but every table is completely empty except `schema_version` (which holds only a version number). Zero rows anywhere: `yahoo_tokens: 0`, `user_selection: 0`, `league_settings_snapshots: 0`, `roster_snapshots: 0`, `standings_snapshots: 0`, `matchup_snapshots: 0`, `available_players_snapshots: 0`, `fantasy_recommendations: 0`, `watchlist: 0`.

**No real Yahoo Fantasy Information was ever persisted, and no destructive action was needed.** The reason: the old `dashboard/pages/35_Fantasy_Settings.py` could *start* the OAuth authorization redirect but had no callback handler that ever completed it — `exchange_code_for_token` was never called by any real code path. The integration was structurally incomplete, not merely unused.

## Item 6: audit of the proven OAuth implementation

Read `~/yahoo-fantasy-cockpit/app/yahoo/oauth.py`, `app/routes/deps.py`, `app/security/crypto.py`, and `tests/test_no_persistence.py` — the project that completed a real, live-verified Yahoo OAuth connection on 2026-09-14.

**What's genuinely reusable and was reused:**
- The authorization-code + refresh-token flow shape (`build_authorization_url`, `exchange_code_for_token`, `refresh_access_token`) — this repo's own `fantasy/yahoo/oauth.py` already had an equivalent, independently-written version; not replaced wholesale.
- **AES-256-GCM token encryption** (`app/security/crypto.py`) — ported directly into `fantasy/yahoo/crypto.py`. One deliberate difference: the cockpit encrypts for a browser cookie and never touches disk; this encrypts for a small local file instead (the signed agreement explicitly permits secure token persistence, unlike Yahoo Fantasy Information itself).
- **An AST-structural "never persists Yahoo data" test** (`tests/test_no_persistence.py`) — the rigor of this (walking the actual syntax tree for forbidden imports/writes, not trusting a comment) is the standard the NHL engine's own Yahoo tests should eventually meet; not ported verbatim this pass (scope discipline — see below), but is the reference model.

**A real bug found via this audit, not by luck:** `fantasy/yahoo/oauth.py`'s token-response parser did a hard `data["refresh_token"]` dict access. Yahoo's own docs (and the cockpit's own code comment, which is what prompted checking this) state a `/get_token` refresh call **may** omit `refresh_token` — it is not guaranteed on every call. The old code would have raised `YahooOAuthError` and broken every future refresh the first time Yahoo omitted the field. **Fixed**: `data.get("refresh_token") or ""`, with the caller (`fantasy/yahoo/client.py::_ensure_fresh_token`) falling back to the previous refresh token when the new one comes back empty — exactly the cockpit's own proven pattern. Covered by a new test (`test_refresh_response_omitting_refresh_token_does_not_raise`).

**Yahoo API surface gap found and closed:** `fantasy/yahoo/contracts.py` had no documented way to discover "my own identity/leagues/team" without already knowing a league_key — the entry point every diagnostic needs. Fetched live from `sports.yahoo.com/developer/docs/` this pass and added the verified `users;use_login=1/...` endpoint family to `contracts.py`, following that file's own established "VERIFIED (fetched live)" labeling convention.

## Item 5: the compliant architecture actually built

```
Yahoo Fantasy API
  -> OAuth authenticated request (fantasy/yahoo/client.py, unchanged interface)
  -> transient in-memory result (fantasy/yahoo/diagnostic.py -- read, summarize, return)
  -> deterministic display (dashboard/pages/35_Fantasy_Settings.py)
  -> discarded (nothing written to disk, ever)
```

Only the OAuth **token** (access_token, refresh_token, token_type, expiry) persists, encrypted at rest:

- **`fantasy/yahoo/crypto.py`** — AES-256-GCM, adapted from the proven cockpit implementation.
- **`fantasy/yahoo/token_store.py`** — `EncryptedFileTokenStore`, a single encrypted file (`fantasy/storage/yahoo_token.enc`, gitignored) holding exactly one token set — correct for a single-owner, ADMIN-only Yahoo connection, deliberately not a multi-user table. Implements the `TokenStore` interface `fantasy/yahoo/client.py` already defined, so the client needed no changes to use it.
- **`fantasy/yahoo/cache.py`** — gutted. Every function now raises `YahooCachingProhibited` instead of writing to a SQLite cache. Nothing real called this before (confirmed in the audit above), so nothing breaks.
- **`fantasy/storage/fantasy_store.py`** — the plaintext `save_token`/`load_token_row` and the six Yahoo-info snapshot functions (`record_/latest_league_settings_snapshot`, `record_/latest_roster_snapshot`, `record_/latest_standings_snapshot`) now raise clearly, pointing at their replacements, rather than silently being left callable. `fantasy/yahoo/sync.py` (the module whose entire job was calling those snapshot functions) is marked deprecated in its docstring; confirmed via search that no real dashboard page or scheduled job ever called it.
- **`fantasy/yahoo/diagnostic.py`** — the Phase 9 "First Test": fetches identity, NHL fantasy game, leagues, league settings/scoring, and team, live, and returns a sanitized summary dict for one-time display. Never writes anywhere.

**A judgment call, disclosed rather than silently decided:** `user_selection` (which league/team key you last picked), `fantasy_recommendations`, and `watchlist` (player IDs) were left as-is — they store the user's own navigational choices/opaque IDs, not substantive Yahoo Fantasy content like scores or rosters. This is a defensible reading, not a certainty; if you'd rather these be guarded too under the same conservative standard, say so and it's a small change.

## Items 6/9: YAHOO_CONNECTION_CERTIFIED status

**Not yet certified — one manual step remains that only you can complete.** The cockpit's own no-persistence design means there was never a dormant refresh token anywhere to reuse; a fresh interactive authorization is required regardless of which codebase does it. I built and tested every piece of the flow, but I cannot click "allow" on your Yahoo account — that step is yours by design (see the safety rules governing this session).

To certify:
1. Set `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REDIRECT_URI` (already required before this pass) and a new `YAHOO_TOKEN_ENCRYPTION_KEY` in `.env` — generate one with:
   `python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
2. Run `streamlit run dashboard/app.py`, open **Fantasy Settings**, click **CONNECT YAHOO FANTASY**, and complete Yahoo's own login/consent screen in your browser.
3. Yahoo redirects back to this same page with `?code=...&state=...` — the page now actually completes the exchange (it never did before this pass) and shows **CONNECTED**.
4. Click **Run connection diagnostic** — a pass shows **YAHOO_CONNECTION_CERTIFIED** along with your real league name, scoring type, and team.

No draft/waiver/streaming UI has been built (per instruction — that's Phase 10, gated on this diagnostic passing first).

## Tests

New: `tests/test_yahoo_token_store.py` (13 tests — encryption round-trip, tamper detection, wrong-key handling, never-plaintext-on-disk, the default-binding footgun check), `tests/test_yahoo_diagnostic.py` (5 tests — full success path, sanitized-summary secret-leakage check, three failure paths). Updated: `tests/test_fantasy_oauth.py` (+1, the refresh-token-omitted bug), `tests/test_fantasy_cache.py` (rewritten — proves prohibition, not caching), `tests/test_fantasy_privacy.py` (rewritten to match the new guard functions and single-owner token model), `tests/test_fantasy_dashboard_pages.py` (unchanged, still passing against the rewritten pages).

## What this deliberately did not do

No recommendation engine, no draft/waiver/streaming UI, no rework of `fantasy/yahoo/sync.py`'s orchestration idea (its *concept* — fetch multiple resources in sequence — is fine; only its *persistence* was the problem, and a proper transient rebuild of that orchestration is Phase 10 scope). No change to any betting model, decision policy, or production data. No deletion of `fantasy_store.db` or its schema — the file itself is harmless (empty), and other tables in it (`user_selection`, `fantasy_recommendations`, `watchlist`) are still in active, compliant use.
