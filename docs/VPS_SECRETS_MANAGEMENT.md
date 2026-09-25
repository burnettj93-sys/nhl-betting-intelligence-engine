# VPS Secrets Management

VPS Production Deployment block (2026-09-24), Part 9. **Status: DESIGN ONLY.** No secret has been provisioned on any VPS -- none exists yet.

## Every real secret this app needs

| Variable | Used by | Required for |
|---|---|---|
| `THE_ODDS_API_KEY` | `research/live_sog_pricing/client.py` and everywhere else that calls The Odds API | Moneyline/prop odds pulls. Without it, every odds-pulling scheduled job fails cleanly (reported, not silently). |
| `YAHOO_CLIENT_ID` | `fantasy/yahoo/oauth.py` | Yahoo OAuth (ADMIN-only feature). |
| `YAHOO_CLIENT_SECRET` | `fantasy/yahoo/oauth.py` | Same. **Never** returned to, stored in, or read from anywhere but this variable (see that module's own docstring) -- never logged, never put in a DB row. |
| `YAHOO_REDIRECT_URI` | `fantasy/yahoo/oauth.py` | Same -- must match the URI registered with Yahoo's developer console exactly, which means it changes when the domain changes (local -> VPS). |
| `YAHOO_TOKEN_ENCRYPTION_KEY` | `fantasy/yahoo/token_store.py` | Encrypts `fantasy/storage/yahoo_token.enc` at rest. **Generate a fresh one for the VPS -- never reuse the local Mac's key** (`docs/VPS_DEPLOYMENT_PREP.md` already says this; repeated here because it's easy to accidentally `scp` the whole `.env` and carry the old key over, which would just mean re-doing the Yahoo consent flow once, harmless but avoidable). |
| `NHL_DB_PATH` | `db.py` | Not a secret -- location of the live NHL database (VPS: `/opt/nhl-engine/data/nhl.db`). Lives in `.env` for convenience; see `docs/RUNTIME_DB_HYGIENE.md`. Unset locally: `operational/runtime/nhl.db` is used. |
| `NHL_ENGINE_DEPLOYMENT_MODE` | `operational/deployment_mode.py` | The ACTIVE/STANDBY double-writer safeguard (Part 3). Unset or anything but `STANDBY` = ACTIVE. |

None of these are optional in the sense of "the app crashes without them" -- every module that reads one fails closed and reports a clear status (`REQUIRES_PERMISSION`/`SOURCE_CONTRACT_FAILURE`/`CONTRACT_NOT_VERIFIED`/etc., never a raw traceback to a user). But `THE_ODDS_API_KEY` and the three `YAHOO_*` values are required for the odds and Yahoo features to do anything real at all.

## Where they live in production

Per `docs/VPS_PRODUCTION_LAYOUT.md`: physically at `/opt/nhl-engine/secrets/.env`, `chmod 600` (owner-only), symlinked to `/opt/nhl-engine/app/.env` by `deploy/provision_layout.sh` so `operational/deployment_mode.py`'s and `fantasy/yahoo/oauth.py`'s existing `_load_dotenv_into_os_environ()` readers keep working with zero code change (they both already resolve `ENV_PATH` as `REPO_ROOT / ".env"`).

**Never inside `app/`'s own git-tracked tree as a real file** -- only ever as a symlink pointing outside it, so a `git pull`/fresh checkout in `app/` can never accidentally expose or overwrite it, and `.env` staying in `.gitignore` (already true today) is a second, independent layer of the same protection.

## Getting secrets onto the VPS the first time

1. Generate the fresh `YAHOO_TOKEN_ENCRYPTION_KEY` locally: `python3 -c "import secrets; print(secrets.token_hex(32))"` (see `fantasy/yahoo/token_store.py::get_encryption_key()`'s own docstring for the exact format it expects).
2. Write `/opt/nhl-engine/secrets/.env` directly on the VPS via `ssh` + a text editor, or `scp` a file containing ONLY the six variables above -- never as part of an `rsync` of the whole repo (Part 9's own instruction; `docs/VPS_DEPLOYMENT_PREP.md` step 5 already says this).
3. `chmod 600 /opt/nhl-engine/secrets/.env` (the provisioning script does this once at directory-creation time via `chmod 700` on the parent `secrets/` directory; run this explicitly on the file itself too after writing it, since a fresh `scp` creates the file under the shell's own umask).
4. Run `deploy/provision_layout.sh`, which symlinks `app/.env` to this file if it isn't already linked.

## Surviving deploys and restarts

Because `.env` is a symlink into `/opt/nhl-engine/secrets/`, not a file inside `app/`, it survives every `git pull` and every full re-checkout of `app/` untouched -- there is nothing to "restore" after a deploy. `systemd`'s `Restart=on-failure` (dashboard) and each job's next scheduled timer firing both re-read `.env` fresh on every process start (`_load_dotenv_into_os_environ()` runs at the top of every real entry point), so a secret rotation just means editing the one real file and letting the next natural restart/run pick it up -- no code redeploy needed.

## What this deliberately does not do

- No secrets manager (Vault, AWS Secrets Manager, etc.) -- six flat key=value pairs on a single small VPS with one operator does not need one; adding one would be exactly the kind of platform redesign Part 1 rules out.
- No secret rotation automation -- rotation is a manual, deliberate action (edit the file, nothing auto-rotates), matching this project's existing "deliberate, explicit action" philosophy for anything security-sensitive (`docs/BACKUP_AND_RESTORE.md`'s restore procedure is the same shape).
