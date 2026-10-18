"""
Reset the model paper bankroll: close the ACTIVE experiment (archived intact, never deleted or rewritten) and start a new one with $500 cash, $0 open stakes and $0 P&L.

    python3 -m deploy.reset_model_bankroll --reset-id 2026-10-18-corrected-workflow            # applies it (once)
    python3 -m deploy.reset_model_bankroll --reset-id 2026-10-18-corrected-workflow --dry-run   # shows what it would archive; changes nothing

The owner authorised this reset on 2026-10-18 (corrected workflow). It is run by hand, never by a job, a deploy or an import. `--reset-id` is the idempotency key: running it again with the same
id (a restart, a redeploy, a second attempt) reports ALREADY_APPLIED and changes nothing. Before it changes anything it writes (1) a copy of the database exactly as it is, (2) a JSON archive of the
previous experiment's tickets, results, audit log, balance and the policy versions in force, and (3) a summary frozen into the experiment row. Tickets that are still open stay open in the
previous experiment and settle there; they are not carried into the new bankroll. Automatic recording stays paused (nothing here touches recording_pause or the policy approval).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def policy_versions() -> dict:
    """Every ticket policy the code defines, with its digest, plus the owner's approval record and the recording-pause state at the moment of the reset."""
    from operational import state_paths
    from research.real_market_parlay import policy as P
    out = {"policies": {name: {"policy_id": getattr(P, name).policy_id, "status": getattr(P, name).status, "digest": getattr(P, name).digest(), "parameters": getattr(P, name).as_dict()}
                        for name in ("LEGACY", "ANCHOR_V1", "PROPOSED") if hasattr(P, name)}}
    for key, fname in (("owner_approval", "ticket_policy_approval.json"), ("recording_pause", "recording_pause.json")):
        try:
            out[key] = json.loads(state_paths.path(fname).read_text())
        except (OSError, json.JSONDecodeError):
            out[key] = None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset-id", required=True, help="idempotency key for this reset, e.g. 2026-10-18-corrected-workflow")
    ap.add_argument("--label", default="Experiment 2: corrected workflow ($500 reset)")
    ap.add_argument("--reason", default="Owner-authorised reset of the model paper bankroll to $500 for the corrected workflow (2026-10-18). Previous experiment archived intact.")
    ap.add_argument("--db", default=None, help="paper bankroll database (default: the production file)")
    ap.add_argument("--archive-dir", default=None)
    ap.add_argument("--backup-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    import datetime as dt
    import shutil
    import sqlite3
    from operational import paper_bankroll as pb
    db = Path(args.db) if args.db else pb.DB_PATH
    if args.dry_run:                                   # strictly read-only: no migration, no experiment record created, nothing written
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ro.row_factory = sqlite3.Row
        try:
            has = bool(ro.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_experiments'").fetchone())
            done = ro.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (args.reset_id,)).fetchone() if has else None
            print(json.dumps({"status": "ALREADY_APPLIED" if done else "DRY_RUN", "reset": dict(done) if done else None,
                              "database_has_experiments": has, "note": None if has else "the database predates experiments: the first normal open migrates it (EXP-1, nothing rewritten), then this command can run",
                              "tickets": ro.execute("SELECT COUNT(*) c FROM paper_bets").fetchone()["c"], "open_tickets": ro.execute("SELECT COUNT(*) c FROM paper_bets WHERE result_status IN ('PENDING','UNRESOLVED')").fetchone()["c"]},
                             indent=1, default=str))
        finally:
            ro.close()
        return 0
    backup_dir = Path(args.backup_dir) if args.backup_dir else REPO / "operational" / "backups"
    if db.exists():                                    # already applied? then nothing is copied, opened for writing or changed
        probe = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        probe.row_factory = sqlite3.Row
        try:
            has = bool(probe.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_experiment_resets'").fetchone())
            applied = probe.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (args.reset_id,)).fetchone() if has else None
        finally:
            probe.close()
        if applied:
            print(json.dumps({"status": "ALREADY_APPLIED", "reset": dict(applied)}, indent=1, default=str))
            return 0
    if db.exists():                                    # a byte-for-byte copy of the file as it is, BEFORE the first open can migrate it
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, backup_dir / f"paper_bankroll_before_reset_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.db")
    conn = pb.init_db(db)
    try:
        done = conn.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (args.reset_id,)).fetchone()
        if done:
            print(json.dumps({"status": "ALREADY_APPLIED", "reset": dict(done), "account_now": pb.account_state(conn, pb.MODEL_TRACK)}, indent=1, default=str))
            return 0
        out = pb.start_new_experiment(conn, reset_id=args.reset_id, label=args.label, reason=args.reason, policies=policy_versions(),
                                      archive_dir=Path(args.archive_dir) if args.archive_dir else REPO / "operational" / "archive", backup_dir=backup_dir)
        out["account_after"] = pb.account_state(conn, pb.MODEL_TRACK)
        print(json.dumps(out, indent=1, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
