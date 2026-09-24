# Deployment Recommendation

**Scope:** how to make the betting side of this app reliably available to a few friends, without a large architecture rewrite, before the regular season starts. **Not a decision, a recommendation** — no infrastructure has been changed by this document.

## The constraint that rules out the easy answers

- **Streamlit Community Cloud**: free, zero-ops, but its filesystem is ephemeral (confirmed in this repo's own code comments before this audit even started) — incompatible with an architecture that is SQLite-everywhere (6 separate database files) and depends on scheduled background jobs (`launchd`) that a cloud-hosted Streamlit app has no way to run at all. Ruled out for anything beyond a demo.
- **Local laptop + launchd** (today's actual setup): works, but "reliable enough that a few friends can use it at any time" fails the moment the laptop sleeps, reboots, loses network, or is simply closed. Not a real answer for always-on friend access.
- **A full Postgres migration**: explicitly out of scope for this pass ("do not begin a six-database migration to Postgres yet") — and genuinely premature: nothing about the current data volume (a handful of small SQLite files, the largest being research corpora that don't need to be "always on") requires it yet.

## Recommendation: a small always-on VPS, SQLite unchanged

Rent a small VPS (e.g. a $5-12/month instance — DigitalOcean, Linode, Hetzner, or a low tier of AWS Lightsail all fit) and run exactly what already exists on it:

- **Streamlit**, via `systemd` (the VPS equivalent of `launchd` — a unit file per process, restarts automatically on crash or reboot) instead of `launchd`.
- **The same 7 scheduled jobs**, via `cron` (or `systemd` timers) instead of `launchd` — the underlying Python entry points (`operational.live_odds_daily_pull`, `sync_daily.py`, `operational.settle_daily_observations`, `operational.daily_postmortem`) don't change at all; only the scheduler wrapper does.
- **The same SQLite files**, on the VPS's own persistent disk — a real disk, not an ephemeral container filesystem, so this is the one change (local Mac → VPS disk) that actually fixes the Streamlit Cloud problem without touching a single line of data-access code.
- **HTTPS**: a reverse proxy (Caddy is the simplest — it gets a free Let's Encrypt certificate with essentially zero configuration) in front of Streamlit's default port.
- **Authentication**: this still needs building regardless of hosting choice (Phase 11's ADMIN/USER split is a from-scratch feature either way) — the VPS doesn't remove that work, it just gives the finished auth layer somewhere reliable to run.
- **Backups**: a nightly `cron` job that copies `operational/paper_bankroll.db` and (once it exists) `operational/prospective_observations.db` to off-box storage (even a simple encrypted copy to cloud object storage, or as a first pass, emailed/synced to your own machine) — cheap insurance for the two files that hold real, non-regenerable operational history.

## Why this beats the alternatives right now

- **Minimum architecture change**: every module in `operational/`, `research/`, `dashboard/` keeps working exactly as it does today. The only thing that moves is *where* the process runs, not *what* it does.
- **Solves the actual reliability problem**: a VPS doesn't sleep, doesn't close its lid, and has a static IP/domain a friend can just visit.
- **Doesn't foreclose Postgres later**: if this ever needs true multi-writer concurrency or a bigger dataset, migrating from "SQLite files on a VPS" to "Postgres on the same VPS" is a much smaller step than migrating from "SQLite files on a laptop with no server at all."

## What still has to happen regardless of hosting choice

1. Phase 11's ADMIN/USER auth split (not a hosting question — needs building either way).
2. Server-side enforcement that Yahoo routes are never reachable by a USER account (same).
3. A real HTTPS certificate + domain (or a static IP friends bookmark) — trivial on a VPS, effectively impossible to do properly on a personal laptop.
4. Deciding on a backup destination for the two real operational databases.

## Not recommended right now

- Streamlit Community Cloud for anything beyond your own solo testing (ephemeral filesystem, no scheduled-job support).
- A full Postgres + containerized rewrite (real engineering cost, no problem it solves today that the VPS approach doesn't already solve).
- Any multi-region / high-availability setup — this is "a few friends," not a public product; one small VPS is enough.
