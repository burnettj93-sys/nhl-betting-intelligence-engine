
import os as _os

_os.environ.setdefault("NHL_ENGINE_TICKET_POLICY", "legacy")      # machinery tests run on the earlier rules; tests of the proposed policy select it explicitly (see tests/test_ticket_policy.py)
_os.environ.setdefault("NHL_ENGINE_ALLOW_SINGLES", "1")        # older accounting tests record single bets; tests of the exclusion turn this off explicitly
_os.environ.setdefault("NHL_ENGINE_PROP_MARKETS", "player_shots_on_goal_alternate,player_points")   # machinery tests keep the two-market capture they were written for; the shots-only default is tested in tests/test_credit_planner.py

# A test run can never open the production ledgers for writing (2026-10-17: a branch test run migrated the live paper_bankroll.db through a dashboard page's default path). The ledgers' own
# get_conn() already redirects a production path to a throw-away file under test (operational/state_paths.guard_database); this is the backstop for any direct sqlite3.connect: a read-WRITE open of
# one of these two files raises, a read-only open (mode=ro) is allowed.
import sqlite3 as _sqlite3
from pathlib import Path as _Path

_PRODUCTION_LEDGERS = {(_Path(__file__).resolve().parent.parent / "operational" / name).resolve() for name in ("paper_bankroll.db", "prospective_observations.db")}
_real_connect = _sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    target = str(database)
    read_only = False
    if target.startswith("file:"):
        read_only = "mode=ro" in target
        target = target[5:].split("?", 1)[0]
    try:
        resolved = _Path(target).resolve()
    except (OSError, ValueError):
        resolved = None
    if resolved in _PRODUCTION_LEDGERS and not read_only:
        raise AssertionError(f"a test opened the PRODUCTION ledger {resolved.name} for writing; use a temporary database (tests must never touch production state)")
    return _real_connect(database, *args, **kwargs)


_sqlite3.connect = _guarded_connect
