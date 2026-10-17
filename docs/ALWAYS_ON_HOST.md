# Running the engine on an always-on host (recommendation, 2026-10-17)

**Nothing here has been purchased, created or migrated. It needs the owner's authorization.**

## Why

Since 2026-10-14 every moneyline pull window (T-35) and most pregame prop windows were missed because the MacBook (M5, 16 GB) sleeps on battery with the lid closed. From the engine's own audit
(`moneyline_pregame_audit.json`, Eastern dates): Oct 14 2 of 2 missed, Oct 15 5 of 5, Oct 16 2 of 2, Oct 17 3 of 3 so far; Oct 12 and 13 had 4 missed. The trader log shows 13.7 h (Oct 14 15:17 to Oct 15 04:59), 17.4 h
and 14.3 h gaps that span game windows. The code recovers when awake; it cannot run while the lid is closed.

## Recommendation

One small Linux virtual server in a US-East data centre, running the same release checkout and the same launchd schedules as systemd timers.

| Item | Choice |
|---|---|
| Host | **DigitalOcean Basic Droplet, 2 GB RAM / 1 vCPU / 50 GB SSD, New York, Ubuntu 24.04 LTS** |
| Price (provider's pricing page, fetched 2026-10-17) | **$12/month**; weekly backups +20% = **$2.40/month**; total **$14.40/month** (per-second billing, capped monthly) |
| Why this size | one trader cycle measured 13 s and 750 MB peak memory; state is about 0.3 GB (nhl.db 14 MB, runtime 82 MB, odds archive 17 MB, backups 133 MB, special-teams 40 MB) plus the repository checkout (about 1.3 GB with raw data); 50 GB disk is ample |
| Not chosen | a $4/$6 droplet (512 MB-1 GB: too small for the 750 MB cycle plus pandas); Hetzner (cheaper in Europe/US but its price page did not render for me, so I will not quote it); buying a Mac mini ($599+ one-off, still a single point of failure at home) |

Not covered by this price: The Odds API plan (see `docs/ODDS_PLAN_DECISION.md`) and any goalie-confirmation feed.

## What must be preserved

| Asset | Where it is now | How it moves |
|---|---|---|
| Databases and history | `nhl.db`, `operational/paper_bankroll.db` (the $500 book), `operational/*.db`, `operational/runtime/` (state, ledgers, frozen predictions, credit ledger), `operational/odds_archive/`, `operational/backups/`, `operational/archive/` | `rsync -a` from the Mac while the engine is **stopped** (one final copy), then verify the ledger hash and row counts |
| Secrets | `.env` (`THE_ODDS_API_KEY`, `NHL_ENGINE_CLOUD_PUBLISH`, `NHL_ENGINE_STREAMLIT_URL`) and the git credential for publishing to `cloud-data` | copy `.env` over SSH (never through chat or git); create a **new fine-grained GitHub token limited to this repository (Contents: read/write)** for the host; rotate the Odds API key on the same day (docs/CREDENTIAL_ROTATION.md) |
| Scheduled jobs | 14 launchd jobs + the watchdog (`~/Library/LaunchAgents`) | `python3 deploy/export_systemd_units.py --out units --release-dir /opt/nhl_engine_release --python /opt/nhl-venv/bin/python` writes 30 unit files with the same intervals and calendar times (tested); `Persistent=false`, so a missed run is reported, never run late |
| Release pinning | `~/nhl_engine_release` (detached worktree) | `git worktree add --detach /opt/nhl_engine_release <commit>` and `deploy/release_checkout.sh` unchanged |

## Migration steps (about half a day, no downtime for the hosted app)

1. **Authorize** (owner): create the droplet and add your SSH key (or tell me to prepare the exact commands; I cannot create or pay for it).
2. Server setup: `timedatectl set-timezone America/Toronto` (the calendar jobs are wall-clock Eastern); create user `nhl`; install `git`, `uv`; `uv python install 3.14`; create `/opt/nhl-venv` and install the packages in `requirements.txt`.
3. Clone the repository, create the worktree at the current release commit, `rsync` the state directories listed above (with the Mac's engine paused: `launchctl bootout` the jobs or let them idle).
4. Code changes needed before the first run (small, tested before use): the watchdog's `launchctl list` check becomes `systemctl list-timers`; `operational/keep_awake.py` becomes a no-op on Linux; paths in `deploy/` that mention `/Users/johnburnett` are parameters. I will make these in one PR before migration.
5. Install the units, `systemctl enable --now nhl-*.timer`, run the watchdog; confirm: trader and publication recency OK, reconciliation OK, **model balance and ticket history identical** to the Mac (`paper_bankroll.db` hash and the 14-ticket history).
6. Run both machines in **read-only mode for one day**: on the Mac, `launchctl bootout` every `com.nhlengine.*` job so only one machine buys credits and publishes (two machines would double-spend credits and race the cloud-data branch). Keep the Mac copy as the rollback.
7. After one clean day (all windows captured, nothing missed), retire the Mac jobs permanently and delete the Mac's token.
8. Keep **recording paused** throughout; the host inherits `recording_pause.json` and the unapproved policy digest as files.

## Rollback and duplicate-engine prevention

* **Cost, in USD:** $12.00/month droplet + $2.40/month weekly backups = **$14.40/month** (about $173/year); no setup fee; billed per second, capped monthly. Not purchased.
* **Only one engine may buy credits and publish.** Before the host's first run, `launchctl bootout` every `com.nhlengine.*` job on the Mac and confirm `launchctl list | grep nhlengine` prints nothing. The reservation ledger is file-locked on ONE machine only; two machines would each see a full balance and double-spend. There is no automatic cross-machine lock today: this is a manual step, and the migration PR should add one (the host would read the provider's remaining-credit header at start and refuse to run if it is lower than the last recorded balance).
* **Rollback:** stop the timers (`systemctl disable --now 'nhl-*.timer'`), rsync the host's `operational/runtime/` and `paper_bankroll.db` back to the Mac, verify the ledger hash, then `launchctl bootstrap` the jobs. The Mac copy is kept untouched for 7 days after cut-over.

## Until then: exact Mac power requirements

The engine cannot buy a pregame price while the Mac sleeps. Required, 7:50 AM until about 30 minutes after the last puck drop (about 11:30 PM ET on late-West nights):

1. **Plugged in to AC power.** On battery the Mac idle-sleeps after **1 minute** (measured: `sleep 1`); only an app holding a power assertion keeps it awake.
2. **Lid open.** Closing the lid sleeps the Mac even on AC (unless it runs closed with an external display and keyboard attached, which this setup does not).
3. **Never sleep on AC:** `sudo pmset -c sleep 0` (run once, by you; I do not run `sudo`).
4. **Wake it for the morning look:** `sudo pmset repeat wakeorpoweron MTWRFSU 07:45:00` (one repeating event; the 8:00 AM update then runs).
5. **Stay logged in and online:** the jobs are LaunchAgents, so they run only inside your logged-in session; Wi-Fi must stay connected.
6. Check at any time: `pmset -g assertions` (look for `PreventSystemSleep`/`caffeinate`) and `python3 -m operational.keep_awake --plan`.

Evidence that the jobs run without an interactive session: this Claude session issued no command between 2026-10-14 15:40 and 2026-10-17 16:00 ET, yet launchd ran `postmortem` (Oct 17 7:32 AM ET), `database_backups` (7:48), the morning update (8:01), `nhl_sync_full` (9:15) and the MoneyPuck and schedule refreshes on their own calendar times, and the 15-minute trader ran whenever the Mac was awake. The only failures are the windows during which the Mac slept.
