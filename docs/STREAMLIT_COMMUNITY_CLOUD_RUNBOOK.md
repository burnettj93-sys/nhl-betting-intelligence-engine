# Streamlit Community Cloud Runbook

Owner checklist for running the thin, read-only Cloud app fed by the `cloud-data` branch.
Design background: `docs/CLOUD_LIVE_DATA_ARCHITECTURE.md`; memory numbers: `docs/STREAMLIT_MEMORY_CERTIFICATION.md`.

## 0. Read this before merging the PR

**Community Cloud tracks `master` and redeploys automatically when it changes.** Merging the
`feature/cloud-live-data` PR will therefore redeploy the live app with the new reader. This is safe by
design (no `cloud-data` branch yet → the app shows the bundled fallback, clearly labeled
`REMOTE SNAPSHOT UNAVAILABLE — BUNDLED FALLBACK`), but do steps 1–2 first if you want the first view to be live.
Nothing in this sprint changed any Streamlit setting or rebooted the app.

## 0b. Status and preflight (2026-09-25)

PR #6 is merged (`336cf80`); the `cloud-data` branch exists and has been published (verified: raw URL 200, schema 2,
content hash equal to the local publication) and `NHL_ENGINE_CLOUD_PUBLISH=ON` is set in the local `.env`.
Run the one-command preflight any time:

```bash
python3 -m operational.cloud_preflight            # add --offline to skip the snapshot fetch
```

It verifies entrypoint, Cloud requirements file, Community Cloud mode, the ADMIN setup-code requirement, the Cloud page
registry (no scheduler / API client / Yahoo imports), a Today render with **no database writes, no network and RSS under
250 MB**, and the live snapshot. Anything it cannot see (Streamlit UI settings, the deployed app) is reported as
`OWNER_ACTION_REQUIRED`, never guessed.

## 1. Publish the first snapshot (local machine, once) — DONE, kept for reference

```bash
python3 -m operational.publish_cloud_snapshot --dry-run   # builds + validates, pushes nothing
python3 -m operational.publish_cloud_snapshot             # creates/updates the cloud-data branch
```

Expected: `SUCCESS` (or `PARTIAL_SUCCESS` with the omitted sections listed). Re-running immediately prints
`NO_CHANGE` and pushes nothing. Uses your existing git credentials (`origin`); no token is added anywhere.
The live working directory is never switched off its branch (an isolated temp clone is used).

## 2. Enable automatic publication (optional, local/VPS engine only)

Add to the engine's `.env` (never committed):

```
NHL_ENGINE_CLOUD_PUBLISH=ON
```

Publication then follows the odds pull, settlement and postmortem jobs (not every 30-minute pregame run).
A publish failure only marks health `DEGRADED`/`FAILED`; it never undoes operational work.

## 2b. Access model and secrets (updated 2026-09-26)

**Streamlit private sharing is the access gate.** Settings → Sharing → "Only specific people can view this app" (already set; anonymous visitors are redirected to Streamlit sign-in). Whoever can reach the app may use it; the deployed app has **no login, no USER/ADMIN accounts, no admin setup code, and does not read `st.user.email`**. The Cloud surface is read-only presentation of the published snapshot (no credentials, no Yahoo, no writes, no ingestion, no destructive controls; enforced by tests). LOCAL and PRODUCTION keep the full account system.

**No auth-related secrets are needed.** `NHL_ENGINE_ADMIN_SETUP_CODE`, `NHL_ENGINE_TRUST_PLATFORM_VIEWER` and `NHL_ENGINE_ADMIN_EMAILS` are **no longer used**: the code ignores them if present, and they can be deleted from Streamlit Secrets after you have verified the deployment (delete the lines, Save, reboot). Optional: `NHL_ENGINE_SNAPSHOT_SOURCE` (default `REMOTE`; `BUNDLED` forces the frozen fallback), `NHL_ENGINE_SNAPSHOT_URL`, `NHL_ENGINE_SNAPSHOT_TOKEN`, `NHL_ENGINE_RUNTIME_MODE` (auto-detected under `/mount/src/`). Never put the Odds API key, Yahoo credentials or any provider key in Streamlit Secrets.

## 3. Inviting friends (App → Settings → Sharing)

Add each friend's email under "Invite viewers by email" → Save. They sign in with Streamlit and land directly in the app.

## 5. Reboot and smoke test

1. Merge the PR (see step 0), or use *Reboot app* after secrets change.
2. Open the app as ADMIN → the banner should read `SNAPSHOT CURRENT` with `DATA AS OF` and `LAST UPDATED`.
3. Pages to open: Today, Game Detail, Game Edge Parlay, Paper Performance, Morning Review, Data Status, Diagnostics.
4. Diagnostics → snapshot source `REMOTE`, fetch status `OK`, schema version 2, content hash present.
5. As an invited non-admin: Today works; Diagnostics/Morning Review are not listed and are denied by direct URL.

## 6. Memory check

Reference (local AppTest, remote mode): Today ≈145 MB, Paper Performance ≈179 MB, 25-cycle plateau ≈195 MB
(Community Cloud limit ≈1 GB; goal < 250 MB Today, < 400 MB heaviest). In Streamlit's *Manage app* → logs /
metrics confirm the app is flat over a day; a steadily rising line means open an issue.

## 7. Freshness check

Two separate checks (details: `docs/CLOUD_LIVE_DATA_ARCHITECTURE.md`):

- **Snapshot freshness** (banner, from `data_as_of`): `CURRENT` ≤ 13 h, `STALE` ≤ 36 h, `VERY_STALE` beyond,
  `UNAVAILABLE` if the timestamp is missing. Governs general/daily data such as Morning Review.
- **Market freshness** (each price/recommendation, from its own capture time): `CURRENT` ≤ 3 h, or ≤ 90 min when
  its game starts within 4 h; otherwise `STALE`. A freshly published snapshot does **not** make an old price current.
  Game Edge Parlays take their stalest leg.

Seeing "ODDS STALE (not live)" with a `CURRENT` snapshot means the snapshot is fine but the newest price is old:
check the local odds pull (the engine pulls 4×/day) rather than the publisher. If the banner says
`REMOTE UPDATE FAILED`, the app is showing the last-known-good snapshot; check the local engine's Data Health →
CLOUD_SNAPSHOT_PUBLISH and re-run step 1.

## 8. Rollback

- **Bad snapshot:** publish a fixed one (the reader rejects invalid/oversized/unsupported files and keeps the
  last good one). To stop remote reading immediately: set `NHL_ENGINE_SNAPSHOT_SOURCE = "BUNDLED"` in secrets and reboot.
- **Bad code:** revert the merge commit on `master` (Cloud redeploys the previous version).
- **Stop publishing:** remove `NHL_ENGINE_CLOUD_PUBLISH` from `.env`. The `cloud-data` branch may be deleted
  at any time; the app falls back to the bundled snapshot.

## 9. Privacy note

The repository is public, so `cloud-data/current/snapshot.json` is world-readable by URL even if the app itself
is viewer-restricted. It contains only shared betting data (no user data, no Yahoo content, no credentials,
no paths). If that is unacceptable, make the repo private and set `NHL_ENGINE_SNAPSHOT_TOKEN`
(fine-grained, contents:read) in Streamlit secrets — Community Cloud can read private repos via its GitHub link.


## Owner completion (what remains)

1. **Invite friends** (optional): Settings → Sharing → add emails.
2. **Delete the unused auth secrets** from Streamlit Secrets after you have confirmed the app works (optional cleanup).
3. **Odds API reset day:** the-odds-api.com → **Account / Usage** → note the date your quota renews → add `NHL_ENGINE_ODDS_RESET_DAY=<day 1-28>` to the gitignored `.env`.
4. **Check while signed in:** the app opens straight to Today (no login screen); Diagnostics shows source **REMOTE**, schema **2**, a content hash equal to the newest `cloud-data` snapshot, and RSS well under 1 GB.
