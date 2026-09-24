# VPS Deployment Preparation

**Status: PREPARATION ONLY. Nothing has been deployed.** This is exact, ready-to-use configuration for the smallest viable production setup recommended in `docs/DEPLOYMENT_RECOMMENDATION.md` — a small always-on VPS running the same stack (Streamlit + SQLite + the same scheduled jobs), reachable over HTTPS. **No database has been migrated, no cutover has happened, and the local Mac's scheduler keeps running exactly as it does today until you explicitly decide to cut over.**

## The double-write safeguard (already built, already tested)

`operational/deployment_mode.py` — every scheduled job's CLI entry point (`nhl_sync`, `live_odds_daily_pull`, `settle_daily_observations`, `daily_postmortem`, `backup_databases`) now checks `NHL_ENGINE_DEPLOYMENT_MODE` before doing any real work:

- Unset, or any value other than `STANDBY` → **ACTIVE** (today's real behavior, unchanged — nothing needs to be set for the local Mac to keep working exactly as it does now).
- `NHL_ENGINE_DEPLOYMENT_MODE=STANDBY` → the job prints a clear message and exits immediately, doing nothing.

13 tests in `tests/test_deployment_mode.py`, including one per real job entry point confirming it genuinely skips real work in STANDBY — not just the guard function in isolation.

## Exact cutover sequence (for when you're ready — not run yet)

1. **Provision the VPS.** Any small instance (1 vCPU / 1-2GB RAM is enough — DigitalOcean, Linode, Hetzner, or AWS Lightsail's smallest tier). Ubuntu 22.04 or 24.04 LTS.
2. **Install dependencies:**
   ```bash
   sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
   ```
3. **Copy the repository** to the VPS (git clone from wherever it's hosted, or `rsync` the working directory directly — this repo has no remote configured, so `rsync` is the practical choice today):
   ```bash
   rsync -avz --exclude='.git' "/path/to/nhl_engine 2/" user@vps-host:/opt/nhl_engine/
   ```
4. **Create the venv and install requirements:**
   ```bash
   cd /opt/nhl_engine && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
5. **Copy `.env` separately and securely** (never via the `rsync` above if `.git` or logs might catch it) — `scp` it directly, and generate a fresh `YAHOO_TOKEN_ENCRYPTION_KEY` and `NHL_ENGINE_DEPLOYMENT_MODE=ACTIVE` line for the VPS's own `.env` (do NOT reuse the same encryption key as any other environment).
6. **Do not copy the local Mac's live databases to the VPS yet.** Start the VPS with fresh/empty databases, OR do a one-time, explicit, verified restore from a `docs/BACKUP_AND_RESTORE.md` backup — either way this is a deliberate step, not part of routine deployment.
7. **Install the systemd units and Caddy config below.**
8. **Verify the VPS is producing real output** (check `operational/ingestion_health_cache.json`, the Data Status page, `opening_day_readiness.py`) while the local Mac is STILL ACTIVE — the VPS won't write anywhere conflicting yet because you haven't flipped any mode.
9. **Only once verified:** set `NHL_ENGINE_DEPLOYMENT_MODE=STANDBY` in the **local Mac's** `.env`. Its jobs stay loaded in `launchd` but immediately start no-op'ing.
10. **Confirm** the VPS keeps running correctly with the local Mac in STANDBY for a few real cycles (a day or two).
11. **Only then**, unload the local Mac's launchd jobs entirely (`launchctl unload ~/Library/LaunchAgents/com.nhlengine.*.plist`) — STANDBY was the safety net for the transition window, not a permanent state to leave the old machine in.

Reversible at every step up through #10 — nothing is destructive until you choose to unload the local jobs in #11, and even that's just unloading `launchd` entries, not deleting anything.

## systemd unit: Streamlit

`/etc/systemd/system/nhlengine-dashboard.service`:
```ini
[Unit]
Description=NHL Engine Streamlit Dashboard
After=network.target

[Service]
Type=simple
User=nhlengine
WorkingDirectory=/opt/nhl_engine
ExecStart=/opt/nhl_engine/.venv/bin/streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Bind to `127.0.0.1` — Caddy is what's actually exposed to the internet (below); Streamlit itself never needs a public interface.

## systemd timers: the 10 scheduled jobs

One template unit + one timer per job, instead of 10 separate service files. `/etc/systemd/system/nhlengine-job@.service`:
```ini
[Unit]
Description=NHL Engine scheduled job: %i

[Service]
Type=oneshot
User=nhlengine
WorkingDirectory=/opt/nhl_engine
ExecStart=/opt/nhl_engine/.venv/bin/python3 -m %i
```

Then one timer per job, matching each launchd plist's real schedule exactly. Example, `/etc/systemd/system/nhlengine-nhl-sync.timer` (mirrors `com.nhlengine.daily-nhl-sync.plist`'s 07:00 daily):
```ini
[Unit]
Description=Daily NHL sync at 07:00

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```
Its paired service, `/etc/systemd/system/nhlengine-nhl-sync.service`:
```ini
[Unit]
Description=NHL Engine daily sync

[Service]
Type=oneshot
User=nhlengine
WorkingDirectory=/opt/nhl_engine
ExecStart=/opt/nhl_engine/.venv/bin/python3 -m operational.nhl_sync
```

Repeat this service+timer pair for each of the other 9 real jobs, using each plist's own real `ProgramArguments`/schedule as the source of truth:

| Job | Module + args | Schedule |
|---|---|---|
| `nhlengine-nhl-sync` | `operational.nhl_sync` | `07:00` daily |
| `nhlengine-settlement` | `operational.settle_daily_observations` | `07:15` daily |
| `nhlengine-postmortem` | `operational.daily_postmortem` | `07:30` daily |
| `nhlengine-backup` | `operational.backup_databases` | `07:45` daily |
| `nhlengine-midday-refresh` | `operational.nhl_sync --mode=midday` | `13:00` daily |
| `nhlengine-pregame-refresh` | `operational.nhl_sync --mode=pregame` | `OnUnitActiveSec=1800` (equivalent to `StartInterval`) |
| `nhlengine-moneyline` | `operational.live_odds_daily_pull --mode=moneyline` | `08:00,13:00,17:00,20:00` daily |
| `nhlengine-props-pull` | `operational.live_odds_daily_pull --mode=props` | `08:15` daily |
| `nhlengine-sweep-first` | `operational.live_odds_daily_pull --mode=sweep-first` | `OnUnitActiveSec=1800` |
| `nhlengine-sweep-second` | `operational.live_odds_daily_pull --mode=sweep-second` | `OnUnitActiveSec=900` |

Enable everything:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nhlengine-dashboard.service
for t in nhl-sync settlement postmortem backup midday-refresh pregame-refresh moneyline props-pull sweep-first sweep-second; do
  sudo systemctl enable --now "nhlengine-$t.timer"
done
```

## Caddy (HTTPS)

`/etc/caddy/Caddyfile`:
```
your-domain.example.com {
    reverse_proxy 127.0.0.1:8501
}
```
Caddy handles the Let's Encrypt certificate automatically on first run — no manual certbot setup needed. Point your domain's DNS `A` record at the VPS's IP before starting Caddy.

## Authentication and backups on the VPS

Both already exist and need no VPS-specific changes: `dashboard/auth.py`'s login gate works identically wherever the app runs, and `operational/backup_databases.py` is already in the timer list above. Recommended addition once real friends are using it: point the VPS's own off-box backup copy (per `docs/BACKUP_AND_RESTORE.md`'s closing recommendation) somewhere other than the VPS itself, so a lost VPS doesn't also lose its own backups.
