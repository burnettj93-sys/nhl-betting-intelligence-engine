# Runtime DB Hygiene

2026-09-25. Separates the live, scheduler-mutated NHL database from source control. **Streamlit memory optimization is deliberately not part of this change.**

## Audit (before)

| Question | Finding |
|---|---|
| Git-tracked? | Yes -- since the first snapshot commit `e4652a9` (2026-08-27). Almost certainly so the Streamlit Community Cloud demo and fresh clones have data (they only receive tracked files). |
| Size | 14,245,888 bytes (~14 MB), 17 tables, 102,725 rows; latest game 2026-10-08. |
| Who writes it | `sync_daily.py` (07:00), `operational.nhl_sync` midday (13:00) and pregame (every 30 min), the moneyline snapshot job via `operational/real_odds_bridge.py` (`odds_snapshots`). Settlement and the prop/moneyline orchestrators only *read* it (game results, identities). |
| How often it changed | Every scheduler cycle -- 5 "routine capture" commits touched it in ~2 days, plus constant uncommitted drift that dirtied `git status`. |
| Does anything need the repo copy? | Tests already use temp DBs (`db.init_db(tmp, wipe=True)`); the few that touch the real DB go through `db.get_conn()`/`db.DB_PATH`, so they follow the resolver. Nothing hardcodes the repo-root path except `operational/backup_databases.py` (fixed, below) and the old VPS provisioning symlink (fixed). |
| Path resolution | One place: `db.py`. (27 modules import it; the default was bound at definition time -- this repo's recurring footgun -- now looked up at call time.) |

## Design

`db.resolve_db_path()`, in order:

1. `NHL_DB_PATH` -- real environment variable, else a `NHL_DB_PATH=` line in `.env` (VPS: `/opt/nhl-engine/data/nhl.db`).
2. `operational/runtime/nhl.db` if it exists -- **gitignored**, the local default after migration.
3. `nhl.db` at the repo root -- the git-tracked file, now a **frozen snapshot**. Keeps a fresh clone and Streamlit Community Cloud working exactly as before.

`get_conn()` / `init_db()` take `db_path=None` and read `db.DB_PATH` at call time, so `mock.patch.object(db, "DB_PATH", ...)` works. `operational/backup_databases.py::critical_sources()` redirects the default `nhl_db` entry to the resolved path (otherwise backups would have silently kept copying the frozen snapshot). `deploy/provision_layout.sh` no longer symlinks `nhl.db`; it reminds you to set `NHL_DB_PATH`.

The tracked `nhl.db` was **not** untracked or deleted: doing so would change what Streamlit Cloud ships. That decision belongs to the memory sprint.

## Migration performed (local Mac)

Copy made with SQLite's online backup API while no job was running, then verified:

- 17 tables / 102,725 rows identical; latest game identical (`2026-10-08T02:00`); `PRAGMA integrity_check` = ok on both.
- Logical dump (`iterdump`) SHA-256 identical. The raw file SHA-256 differs (`a9d1162e...` vs `65b62c60...`) because the backup API repacks pages -- expected, and why the logical hash is the meaningful check.
- `db.resolve_db_path()` -> `operational/runtime/nhl.db`; `db.get_conn()` reads 1,192 games there.
- A real `operational.nhl_sync --mode=midday` run, spied at the `sqlite3.connect` level: it opened only `operational/runtime/nhl.db` (BEGIN, 12 INSERT, 4 SELECT, COMMIT); the tracked file's SHA-256 was unchanged. `git status` stays clean.
- The tracked `nhl.db` was then restored to its committed content (`git checkout -- nhl.db`). Only after the live data was proven present in the runtime copy.

## Streamlit implications (for the memory sprint)

- **Does startup touch `nhl.db`?** Yes, once: `operational/system_health.py::database_health()` (rendered on the entry/Today page) runs `SELECT 1`. Measured by spying on `sqlite3.connect` across `dashboard/app.py` and every page: only `app.py`/`21_Today.py` open `nhl.db`, and the only statement is `SELECT 1`.
- **Does any page load large tables from it?** No. The dashboard deliberately never imports `db.py` (asserted by `tests/test_dashboard.py`). Other databases the pages do open: `prospective_observations.db` (Today, Ledger, Model Learning), `special_teams_history.db` (Today, Player Intelligence), `research_moneypuck.db` (Game Detail, Team Ratings), `paper_bankroll.db` (Paper Performance, Morning Review).
- **Does moving the runtime DB change cloud behavior?** No. Cloud has no `operational/runtime/`, so it resolves to the tracked snapshot exactly as before. One consequence worth knowing: with no more routine-capture commits, the Cloud copy of `nhl.db` will be frozen at whatever is committed. It could not have been fresher than the last push before either.
- File size: 14 MB, opened only for a `SELECT 1` -- not a meaningful memory contributor by itself.

## Operating notes

- Get the live path anywhere: `python3 -c "import db; print(db.resolve_db_path())"`.
- `demo_setup.py` (`db.init_db(wipe=True)`) wipes the *resolved* database -- as it always wiped the live one; it is a deliberate, manual script.
- Backups (`operational.backup_databases`) now cover the runtime DB.

## Pinned-hash guard updated (deliberate)

Twelve older research-sprint tests (`test_production_boundary_files_unchanged` and equivalents) pin `db.py`'s SHA-256 to prove research work never touched the production DB layer. Centralizing path resolution necessarily changes `db.py`, so the pinned value was updated from `b598f464...` to `02361fb5...` in exactly those 12 assertions (12 one-line hash swaps, nothing else). `config.py` and `schema.sql` pins are untouched; `schema.sql` and all query/connection semantics are unchanged.
