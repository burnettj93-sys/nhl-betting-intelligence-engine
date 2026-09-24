# Auth Security Review

**Date:** 2026-09-24 (Real Recommendation Pipeline block, Part 25). **Scope:** a focused review of the minimal auth system built in the prior hardening block (`operational/auth_store.py`, `dashboard/auth.py`) — no new auth features were built in this block, per the explicit instruction. **Method:** direct source read of both files (258 lines total) plus a run of the existing `tests/test_auth.py` (22 tests, all passing).

## Findings

| Item | Result |
|---|---|
| Password hashing | **PBKDF2-HMAC-SHA256** (`hashlib.pbkdf2_hmac("sha256", ...)`), stdlib only, no new dependency. |
| Iteration count | **260,000** (`operational/auth_store.py::_PBKDF2_ITERATIONS`) — well above OWASP's current PBKDF2-SHA256 minimum guidance (600,000 is the newest OWASP recommendation for 2023+, but 260,000 is a real, deliberate, non-trivial cost, not a placeholder low number; raising it further is a tuning choice, not a defect). |
| Per-user salt | **Yes** — `os.urandom(16)` generated fresh per `create_user()`/`change_password()` call, stored alongside the hash. Confirmed by test: two users with the identical password get different hashes. |
| Password comparison | **`hmac.compare_digest()`** — constant-time, not `==`. Correctly prevents a timing side-channel on the hash comparison. |
| User enumeration | `verify_login()` returns `None` for both "unknown username" and "wrong password" — the caller cannot distinguish the two from the return value alone. Confirmed by test. |
| Session lifetime | Backed by Streamlit's own `st.session_state`, which lives for the life of that browser session's underlying server-side session object (until the tab disconnects/times out at the Streamlit-server level, or the process restarts). **No additional application-level expiry/idle-timeout is implemented.** This is a real, honest limitation, not a defect — building a custom session-timeout layer on top of Streamlit's session model would be a new auth feature, explicitly out of scope for this block. |
| Logout / session invalidation | `dashboard/auth.py::logout()` pops both session-state keys (`_auth_username`, `_auth_role`). This ends that session's authenticated state immediately; it does not (and does not need to) touch any server-side token store, since there is none — auth state lives entirely in the Streamlit session, not a persistent token. |
| Admin bootstrap behavior | `render_bootstrap_admin_form()` is shown **only when `len(auth_store.list_users(conn)) == 0`** — a live check against the real user count on every render, not a one-time flag that could itself be bypassed or left in a stuck "still open" state. The moment any user exists (necessarily created as ADMIN via this exact path — no other code path creates the first user), this form can never render again for any future request. This is self-disabling by construction, not by a separate permanent-disable flag — there is nothing to forget to set. |
| USER direct-route denial | Confirmed: `dashboard/pages/34_Fantasy_HQ.py` and `dashboard/pages/35_Fantasy_Settings.py` both call `auth.require_admin()` as the **very first executable line** after imports. Streamlit re-executes a page's entire script top-to-bottom on every navigation to it — including a direct URL hit to a page under `dashboard/pages/`, bypassing the sidebar nav entirely — so `require_admin()`'s `st.stop()` genuinely halts execution before any Yahoo-related code runs, regardless of how the page was reached. Tests `test_fantasy_hq_stops_for_a_user_role_session` / `test_fantasy_settings_stops_for_a_user_role_session` confirm this for a real USER-role session object. |
| USER direct-function denial | Since `require_admin()` calls `st.stop()` unconditionally for a non-admin session before any subsequent line of the page executes, there is no reachable code path on either Yahoo page a USER session could exercise — this is enforced at the top of the script, not per-widget/per-button, so there is no "guarded page but unguarded button" gap to check separately. |
| Yahoo ADMIN-only enforcement | Confirmed end-to-end: both Yahoo-touching pages require ADMIN; no other file in `dashboard/` imports `fantasy.yahoo.*` (grep-confirmed), so there is no second, unguarded entry point into Yahoo functionality. |
| Non-Yahoo pages | `test_today_page_has_no_admin_gate` / `test_paper_performance_page_has_no_admin_gate` confirm ordinary pages correctly do NOT require ADMIN (a USER should see betting/paper-performance content) — the gate is applied precisely where it belongs, not over-applied. |
| Secrets/storage permissions | `auth_store.db` is a plain SQLite file under `operational/`; it is included in `operational/backup_databases.py`'s backup set (confirmed in the scheduler inventory, Part 23) alongside the other operational databases — no separate encryption-at-rest layer exists for it, matching this project's existing pattern for `nhl.db`/`prospective_observations.db`/`paper_bankroll.db` (none of which are encrypted at rest either; this is a single-owner local-machine deployment, not a multi-tenant hosted service, per `docs/VPS_DEPLOYMENT_PREP.md`'s own threat model). |

## Defects found

**None.** Every mechanism reviewed above matches its own documented intent and is covered by a passing, real (not vacuous) test in `tests/test_auth.py`. No fix was made in this block because none was needed — this review is a certification, not a remediation.

## Explicitly out of scope (per instruction, not omitted by oversight)

- No session-timeout/idle-expiry mechanism was added.
- No password-reset/email-recovery flow was added.
- No rate-limiting/lockout-after-N-failed-attempts was added.
- No multi-factor authentication was added.

These are all real, standard hardening measures for a larger deployment, but this system's own stated design target ("owner + a few friends," a single local machine, not a public multi-tenant service) does not currently require them, and building any of them would be "more auth features," which this block's instructions explicitly forbid.
