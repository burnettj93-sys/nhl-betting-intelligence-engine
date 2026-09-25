# VPS Cutover Runbook

VPS Production Deployment block (2026-09-24), Parts 17/18/23. **STOP: this runbook is prepared, not executed.** No VPS has been provisioned, no cutover has happened, and the local Mac's real schedulers keep running exactly as they do today. Actual cutover requires the owner to explicitly confirm real VPS details (provider, IP/domain, SSH access) and explicitly authorize proceeding -- this document does not do that on its own.

Every step through step 10 is reversible. Step 11 (unloading the local Mac's launchd jobs) is the only step that isn't trivially undone, and the Rollback Runbook below exists specifically for before/around that point.

## Prerequisites

- A provisioned VPS (Ubuntu 22.04/24.04, 1 vCPU / 1-2GB RAM is enough) with SSH access.
- `docs/VPS_DEPLOYMENT_PREP.md` steps 1-4 done (dependencies installed, repo copied, venv created).
- Real secrets ready per `docs/VPS_SECRETS_MANAGEMENT.md` (a fresh `YAHOO_TOKEN_ENCRYPTION_KEY`, the real `THE_ODDS_API_KEY` and Yahoo credentials).
- `docs/VPS_DATABASE_MIGRATION_INVENTORY.md` reviewed -- know which databases are moving.
- A domain pointed at the VPS's IP (or accept plain HTTP on a bare IP as a temporary state -- see `deploy/caddy/Caddyfile`'s own comment on this).

## Cutover sequence

1. **Stop local production schedulers.** On the local Mac, set `NHL_ENGINE_DEPLOYMENT_MODE=STANDBY` in the local `.env`. Every scheduled job's entry point (`sync_daily.py`, `operational.nhl_sync`, `operational.live_odds_daily_pull`, `operational.settle_daily_observations`, `operational.daily_postmortem`, `operational.backup_databases`) now no-ops immediately on its next invocation (`operational/deployment_mode.py`) -- the `launchd` jobs stay loaded, they just do nothing. This is deliberately step 1, before anything else, so there is never a moment where both machines could write for real simultaneously.

2. **Verify local jobs are genuinely disabled**, don't just trust the `.env` edit:
   ```bash
   python3 -m operational.backup_databases   # should print a STANDBY no-op message, not run a real backup
   ```
   Confirm the printed message matches `operational/deployment_mode.py::require_active_scheduler_or_exit()`'s exact wording.

3. **Take final safe backups** on the local Mac:
   ```bash
   python3 -m operational.backup_databases
   ```
   (Safe to run even in STANDBY? No -- `backup_databases.main()` itself checks the guard first, same as every other job. Temporarily set `NHL_ENGINE_DEPLOYMENT_MODE=ACTIVE` for this one invocation, or call `operational.backup_databases.run_all_backups()` directly rather than through `main()` -- that function itself has no STANDBY guard, only `main()` does, and running a backup is never the write conflict this guard exists to prevent.)

4. **Checksum critical databases** per `docs/VPS_DATABASE_MIGRATION_INVENTORY.md`'s exact command block. Record the output.

5. **Copy code and runtime state:**
   - Code: `git clone`/`git pull` the repo into `/opt/nhl-engine/app` on the VPS (never `rsync` the whole tree if secrets or DB files might be caught in it).
   - Runtime state (the databases marked "must move" in the inventory): `scp`/`rsync` each one individually into a **new, isolated path** on the VPS first, per that doc's verification procedure -- never straight into the path the app will read from.

6. **Verify checksums and row counts** on the VPS copies against step 4's recorded values, per `docs/VPS_DATABASE_MIGRATION_INVENTORY.md`'s procedure. Do not proceed on a mismatch.

7. **Install production secrets:** write `/opt/nhl-engine/secrets/.env` on the VPS per `docs/VPS_SECRETS_MANAGEMENT.md`, then run `deploy/provision_layout.sh` (moves the verified database copies into `/opt/nhl-engine/data/...` if they aren't already there, symlinks everything, sets `chmod 600`/`700` throughout).

8. **Start the web app:**
   ```bash
   sudo cp deploy/systemd/nhlengine-dashboard.service /etc/systemd/system/
   sudo cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile
   sudo systemctl daemon-reload
   sudo systemctl enable --now nhlengine-dashboard.service
   NHL_ENGINE_DOMAIN=your-real-domain.example.com sudo systemctl reload caddy   # or restart, if first install
   ```

9. **Verify auth:** visit the real domain over HTTPS, confirm the login page renders, confirm the existing ADMIN account (from the migrated `auth_store.db`) logs in correctly, confirm a USER-role account cannot reach Yahoo/admin pages (`dashboard/auth.py::require_admin()` -- already tested, see `tests/test_auth.py::TestYahooRouteEnforcement`).

10. **Verify health:**
    ```bash
    python3 -c "from operational import system_health as sh; import json; print(json.dumps(sh.production_health_summary(), indent=2))"
    ```
    Expect `DATABASES: OK`, `SCHEDULERS: WAITING` (timers not enabled yet -- that's step 11, correct at this point), everything else reflecting the migrated data's real last-known state.

11. **Enable VPS schedulers:**
    ```bash
    sudo cp deploy/systemd/nhlengine-*.service deploy/systemd/nhlengine-*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    for t in nhl-sync settlement postmortem backup midday-refresh pregame-refresh moneyline props-pull sweep-first sweep-second; do
      sudo systemctl enable --now "nhlengine-$t.timer"
    done
    ```

12. **Confirm one and only one scheduler environment is active.** On the VPS: `NHL_ENGINE_DEPLOYMENT_MODE` is unset or `ACTIVE` (the default). On the local Mac: confirm it is still `STANDBY` (step 1). Re-run step 2's verification command on the local Mac one more time to be certain nothing flipped back.

13. **Verify the first real NHL sync** on the VPS: wait for `nhlengine-nhl-sync.timer` to fire (or `sudo systemctl start nhlengine-nhl-sync.service` to trigger it immediately), then check `journalctl -u nhlengine-nhl-sync.service -n 50` for a real `SUCCESS`/`PARTIAL_SUCCESS` result, not `FAILED`.

14. **Verify the first real odds pull** the same way against `nhlengine-moneyline.service` or `nhlengine-sweep-first.service`, whichever's timer fires first. Confirm a real credit was spent and a real snapshot landed (`operational/moneyline_snapshot_cache.json` or a new file under `/opt/nhl-engine/data/operational/odds_archive/live/`).

15. **Verify the first real backup** the same way against `nhlengine-backup.service`, then spot-check `ls -la /opt/nhl-engine/backups/*/` for a fresh file per critical database.

16. **Confirm the VPS keeps running correctly with the local Mac in STANDBY for a few real cycles** (a day or two) before touching anything further -- `docs/VPS_DEPLOYMENT_PREP.md`'s own instruction, repeated here because it's the actual safety margin this whole cutover is built around.

17. **Only then, unload the local Mac's launchd jobs entirely:**
    ```bash
    for job in daily-nhl-sync daily-postmortem daily-props-pull daily-settlement database-backup \
               midday-schedule-refresh moneyline-snapshot pregame-targeted-refresh \
               prop-sweep-first prop-sweep-second; do
      launchctl unload ~/Library/LaunchAgents/com.nhlengine.$job.plist
    done
    ```
    This is the only step in this whole runbook that isn't instantly reversible (the plist files themselves are untouched -- `launchctl load` brings them straight back -- but STANDBY was a transition safety net, not a state to leave the old machine in indefinitely).

## Rollback runbook

If the VPS cutover fails at any point **up through step 16**:

1. **Disable VPS schedulers:**
   ```bash
   for t in nhl-sync settlement postmortem backup midday-refresh pregame-refresh moneyline props-pull sweep-first sweep-second; do
     sudo systemctl disable --now "nhlengine-$t.timer"
   done
   ```
2. **Restore/re-enable local schedulers:** set `NHL_ENGINE_DEPLOYMENT_MODE=ACTIVE` (or remove the line entirely -- ACTIVE is the default) in the local Mac's `.env`. The already-loaded `launchd` jobs resume real work on their next scheduled fire -- no `launchctl load` needed since step 17 (unloading) hasn't happened yet at this point in the sequence.
3. **Preserve any new VPS state.** Do not delete anything on the VPS. If the VPS ran for real even briefly (wrote to `nhl.db`, recorded a paper bet, etc.), that's now real data that must be reconciled, not discarded.
4. **Reconcile before copying anything back.** Compare row counts/checksums between the local Mac's database (which kept running, per step 2 above) and the VPS's version (which may have diverged during its active window). This is a manual, deliberate review -- there is no automatic merge tool for two SQLite files that both received real writes, and building one is out of scope for this block (Part: no new predictive models/infrastructure beyond what's asked). If the VPS window was short and produced no real writes (most likely, if the failure was caught quickly), no reconciliation is needed -- the local Mac's database is simply the source of truth again.
5. **Never create two active writers.** If in doubt about whether the VPS is still writing, re-run step 1 of the rollback (disable VPS schedulers) again and re-check `systemctl list-timers` shows all `nhlengine-*` timers as disabled before touching the local Mac's mode.

If the failure happens **after step 17** (local jobs already unloaded), rollback additionally requires:
```bash
for job in daily-nhl-sync daily-postmortem daily-props-pull daily-settlement database-backup \
           midday-schedule-refresh moneyline-snapshot pregame-targeted-refresh \
           prop-sweep-first prop-sweep-second; do
  launchctl load ~/Library/LaunchAgents/com.nhlengine.$job.plist
done
```
before proceeding with rollback steps 1-5 above.

## Stop condition (Part 23)

This runbook is safe to prepare, review, and rehearse against a staging copy. **Do not run the Cutover Sequence against a real VPS, and do not run step 1 (setting the local Mac to STANDBY) against real production, without the owner explicitly confirming real VPS connection details and explicitly authorizing the cutover.**
