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
    from operational import paper_bankroll as pb
    db = Path(args.db) if args.db else pb.DB_PATH
    conn = pb.init_db(db)
    try:
        done = conn.execute("SELECT * FROM paper_experiment_resets WHERE reset_id = ?", (args.reset_id,)).fetchone()
        prev = pb.active_experiment(conn)
        acct = pb.account_state(conn, pb.MODEL_TRACK)
        if args.dry_run or done:
            print(json.dumps({"status": "ALREADY_APPLIED" if done else "DRY_RUN", "reset": dict(done) if done else None, "active_experiment": pb.experiment_brief(prev),
                              "account_now": acct}, indent=1, default=str))
            return 0
        out = pb.start_new_experiment(conn, reset_id=args.reset_id, label=args.label, reason=args.reason, policies=policy_versions(),
                                      archive_dir=Path(args.archive_dir) if args.archive_dir else REPO / "operational" / "archive",
                                      backup_dir=Path(args.backup_dir) if args.backup_dir else REPO / "operational" / "backups")
        out["account_after"] = pb.account_state(conn, pb.MODEL_TRACK)
        print(json.dumps(out, indent=1, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
