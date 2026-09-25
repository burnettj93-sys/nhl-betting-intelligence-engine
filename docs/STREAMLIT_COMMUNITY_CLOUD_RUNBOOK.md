# Streamlit Community Cloud Runbook

Owner checklist for running the thin, read-only Cloud app fed by the `cloud-data` branch.
Design background: `docs/CLOUD_LIVE_DATA_ARCHITECTURE.md`; memory numbers: `docs/STREAMLIT_MEMORY_CERTIFICATION.md`.

## 0. Read this before merging the PR

**Community Cloud tracks `master` and redeploys automatically when it changes.** Merging the
`feature/cloud-live-data` PR will therefore redeploy the live app with the new reader. This is safe by
design (no `cloud-data` branch yet → the app shows the bundled fallback, clearly labeled
`REMOTE SNAPSHOT UNAVAILABLE — BUNDLED FALLBACK`), but do steps 1–2 first if you want the first view to be live.
Nothing in this sprint changed any Streamlit setting or rebooted the app.

## 1. Publish the first snapshot (local machine, once)

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

## 3. Streamlit app secrets (App → Settings → Secrets)

```toml
NHL_ENGINE_ADMIN_SETUP_CODE = "<long random string>"   # required to create the first ADMIN account
NHL_ENGINE_TRUST_PLATFORM_VIEWER = "ON"                # optional: use Streamlit's signed-in viewer email
NHL_ENGINE_ADMIN_EMAILS = "you@example.com"            # optional: viewer emails that are ADMIN
# NHL_ENGINE_RUNTIME_MODE = "COMMUNITY_CLOUD_MODE"     # optional: auto-detected under /mount/src/
# NHL_ENGINE_SNAPSHOT_SOURCE = "REMOTE"                # default; BUNDLED forces the frozen fallback
# NHL_ENGINE_SNAPSHOT_URL = "https://raw.githubusercontent.com/<owner>/<repo>/cloud-data/current/snapshot.json"
# NHL_ENGINE_SNAPSHOT_TOKEN = "<read-only token>"      # only if the data source is ever made private
```

Do NOT add Yahoo credentials, the Odds API key, or any provider key to the Cloud app: it does not use them.
The setup code is never shown in the UI or logs; without it nobody can become ADMIN (the first anonymous
visitor is never promoted).

## 4. Viewer access (App → Settings → Sharing)

Set the app to **Only specific people can view this app** and add friends' emails. This is the primary USER
gate in Cloud mode. Invited viewers see betting pages; they cannot open Diagnostics, Morning Review, Data
Status, Yahoo, or admin controls (ADMIN-only, enforced server-side).

Limits: whether `st.user` carries a verified email depends on the platform; the app treats it as a role hint
only when `NHL_ENGINE_TRUST_PLATFORM_VIEWER=ON`, and never fabricates identity. If unsure, leave it OFF
and rely on the in-app ADMIN login. Check on the Diagnostics page (ADMIN) after enabling.

## 5. Reboot and smoke test

1. Merge the PR (see step 0), or use *Reboot app* after secrets change.
2. Open the app as ADMIN → the banner should read `LIVE SNAPSHOT (CURRENT)` with `DATA AS OF` and `LAST UPDATED`.
3. Pages to open: Today, Game Detail, Game Edge Parlay, Paper Performance, Morning Review, Data Status, Diagnostics.
4. Diagnostics → snapshot source `REMOTE`, fetch status `OK`, schema version 2, content hash present.
5. As an invited non-admin: Today works; Diagnostics/Morning Review are not listed and are denied by direct URL.

## 6. Memory check

Reference (local AppTest, remote mode): Today ≈145 MB, Paper Performance ≈179 MB, 25-cycle plateau ≈195 MB
(Community Cloud limit ≈1 GB; goal < 250 MB Today, < 400 MB heaviest). In Streamlit's *Manage app* → logs /
metrics confirm the app is flat over a day; a steadily rising line means open an issue.

## 7. Freshness check

`CURRENT` ≤ 13 h since `data_as_of`; `STALE` ≤ 36 h; `VERY_STALE` beyond; `UNAVAILABLE` if the timestamp is
missing. Stale data shows `DATA STALE`, downgrades "live" badges, and never presents as current. If the banner
says `REMOTE UPDATE FAILED`, the app is showing the last-known-good snapshot; check the local engine's
Data Health → CLOUD_SNAPSHOT_PUBLISH and re-run step 1.

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
