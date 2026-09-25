# VPS Observability

VPS Production Deployment block (2026-09-24), Part 15. How to check on the running system once it's on a VPS -- `systemctl`/`journalctl` for process-level state, the existing dashboard health views for application-level state. No new tooling invented; this is a guide to what already exists (`operational/system_health.py`, `opening_day_readiness.py`, and this block's new `operational/system_health.py::production_health_summary()`).

## Streamlit logs

```bash
sudo journalctl -u nhlengine-dashboard.service -f          # follow live
sudo journalctl -u nhlengine-dashboard.service --since today
sudo systemctl status nhlengine-dashboard.service           # is it up, when did it last restart, why
```

## Each scheduler job's logs

Every job is a `oneshot` service triggered by its own timer (`deploy/systemd/nhlengine-<job>.service` / `.timer`) -- its stdout/stderr goes to the journal under that service's own unit name:

```bash
sudo journalctl -u nhlengine-nhl-sync.service --since today
sudo journalctl -u nhlengine-settlement.service -n 50
sudo journalctl -u nhlengine-backup.service --since "1 hour ago"
```

## Last successful run of a job

Two ways, matching `docs/VPS_DEPLOYMENT_PREP.md`'s already-real state tracking:

1. `systemctl list-timers --all` shows the last time each timer fired and the next scheduled fire time.
2. The application's own record is more precise than "did the process run" -- it's "did the job actually *succeed*": `python3 -c "from operational import system_health as sh; import json; print(json.dumps(sh.production_health_summary(), indent=2))"` reports `NHL_DATA`/`SETTLEMENT`/`POSTMORTEM`/`BACKUPS`, each with a real `last_run`/`reason` sourced from `operational/ingestion_health_cache.json` (written by every job's own entry point on every real run, success or failure).

## Inspecting a failed timer

```bash
sudo systemctl list-timers --all | grep nhlengine    # OK/FAILED state per timer
sudo systemctl status nhlengine-<job>.service         # exit code + last few log lines inline
sudo journalctl -u nhlengine-<job>.service -n 100     # full recent output
```

A `oneshot` service that failed shows `Active: failed` in `systemctl status` even though its timer will still fire again next cycle (systemd does not disable a timer just because one run of its service failed) -- this is the correct, desired behavior (matches this project's own "no infinite retry loops, the next scheduled run picks it back up" design in `operational/settle_daily_observations.py`/`operational/daily_postmortem.py`).

## Database and backup health

```bash
python3 -c "from operational import system_health as sh; import json; print(json.dumps(sh.production_health_summary(), indent=2))"
```
`DATABASES` and `BACKUPS` keys cover this directly. For backup-specific detail (which databases, how many rotated copies, retention), `ls -la /opt/nhl-engine/backups/*/` shows the real rotated files (14-deep per database, per `docs/BACKUP_AND_RESTORE.md`'s retention policy).

## The existing dashboard health views

Once logged in as ADMIN, `dashboard/pages/9_Data_Status.py` (Data Status page) renders `operational/system_health.py::build_system_health()`'s full 20-component breakdown -- the fine-grained view. `production_health_summary()` (this block, Part 13) is the coarse, 8-category-plus-Yahoo view meant for a quick CLI check or an external monitor, not a dashboard page (no page was added for it -- it's a thin reduction of data the Data Status page already shows in more detail).

## Scheduler-wide status at a glance

```bash
sudo systemctl list-timers --all --no-legend | grep nhlengine
```
or, from inside the app (works identically on macOS/launchd and Linux/systemd, per this block's Part 14 cross-platform fix):
```bash
python3 -c "from operational import system_health as sh; import json; print(json.dumps(sh.live_odds_scheduler_health(), indent=2))"
```
