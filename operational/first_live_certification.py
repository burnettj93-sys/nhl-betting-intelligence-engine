"""
First-live MONEYLINE T-35 certification report (Live Run Reliability block, 2026-09-25).

    python3 -m operational.first_live_certification            # human report
    python3 -m operational.first_live_certification --json

READ-ONLY: reads the pregame cluster audit (`operational/runtime/moneyline_pregame_audit.json`), the pregame
state, the prospective ledger and the paper bankroll (both opened `mode=ro`). It imports no Odds API client
and makes NO network request, so it can never spend a credit.

Answers, for the most recent REAL (provider-listed, actually pulled) cluster:
  has any real T-35 cluster completed?   last cluster status   quote timing valid?   decision evaluated?
  observation persisted?   paper bet created if the action was BET?   cloud published?
ARCHITECTURE_READY is always shown separately from LIVE_OBSERVED: the cadence is only called live-certified once a
real cluster has completed the whole chain. PASS / WAIT decisions are sufficient -- a BET is not required.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from operational import moneyline_pregame as mp

PASS, FAIL, PENDING, NA = "PASS", "FAIL", "PENDING", "NOT_APPLICABLE"


def _ro(path: Path):
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _persisted(game_ids: list, ledger_path: Path | None = None) -> int:
    from operational import prospective_ledger as pl
    conn = _ro(ledger_path or pl.DB_PATH)
    if conn is None or not game_ids:
        return 0
    try:
        marks = ",".join("?" for _ in game_ids)
        row = conn.execute(f"SELECT COUNT(*) c FROM predictions WHERE market_id = 'MONEYLINE' AND game_id IN ({marks})",
                           [str(g) for g in game_ids]).fetchone()
        return int(row["c"])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _paper_bets(game_ids: list, bankroll_path: Path | None = None) -> int:
    from operational import paper_bankroll as pb
    conn = _ro(bankroll_path or pb.DB_PATH)
    if conn is None or not game_ids:
        return 0
    try:
        marks = ",".join("?" for _ in game_ids)
        row = conn.execute(f"SELECT COUNT(*) c FROM paper_bets WHERE track = 'REAL_MARKET_PAPER' AND event_id IN ({marks})",
                           [str(g) for g in game_ids]).fetchone()
        return int(row["c"])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def certify(audit: dict | None = None, *, ledger_path: Path | None = None, bankroll_path: Path | None = None) -> dict:
    audit = audit if audit is not None else mp.load_audit()
    obs = mp.live_observed(audit)
    real = [(k, r) for k, r in sorted(audit["records"].items()) if r.get("provider_listed") is True and r.get("actual_pull_utc")]
    report = {"architecture_ready": True, "live_observed": obs["live_observed"], "status": obs["status"],
              "real_clusters_completed": obs.get("complete_clusters", 0), "clusters_audited": len(audit["records"]),
              "last_cluster": mp.last_cluster_outcome(audit), "checks": {}}
    if not real:
        report["checks"] = {"real_cluster_seen": {"state": PENDING, "detail": "no provider-listed cluster has been pulled yet "
                            "(first expected: the 2026-09-29 21:00Z game, pull ~20:25Z)"}}
        return report
    key, r = real[-1]
    ids = r.get("game_ids") or []
    persisted = _persisted(ids, ledger_path)
    bets = _paper_bets(ids, bankroll_path)
    pub = (r.get("cloud_publish") or {}).get("status")
    checks = {
        "real_cluster_seen": {"state": PASS, "detail": key},
        "triggered_and_spent_expected_credit": {"state": PASS if int(r.get("credits_spent") or 0) >= 1 else FAIL,
                                                "detail": f"credits_spent={r.get('credits_spent')}"},
        "quote_timing_valid": {"state": PASS if r.get("in_decision_window") else FAIL,
                               "detail": f"pull {r.get('actual_pull_utc')} vs anchor {r.get('decision_anchor_utc')} (window T-40..T-30)"},
        "quote_stored": {"state": PASS if r.get("odds_rows_stored") else FAIL, "detail": f"rows={r.get('odds_rows_stored')}"},
        "decision_evaluated_at_t30": {"state": PASS if r.get("recommendations_evaluated") else FAIL,
                                      "detail": f"evaluated={r.get('recommendations_evaluated')} BET={r.get('bet_count')} "
                                                f"WAIT={r.get('wait_count')} PASS={r.get('pass_count')} DATA_UNAVAILABLE={r.get('data_unavailable_count')}"},
        "observation_persisted": {"state": PASS if persisted else (FAIL if r.get("recommendations_evaluated") else PENDING),
                                  "detail": f"{persisted} immutable MONEYLINE observation(s) for the cluster's games"},
        "paper_bet_if_bet": ({"state": PASS if bets >= int(r.get("bet_count") or 0) else FAIL,
                              "detail": f"{bets} REAL_MARKET_PAPER bet(s) for {r.get('bet_count')} BET decision(s)"}
                             if r.get("bet_count") else {"state": NA, "detail": "no BET decision (PASS/WAIT is sufficient for certification)"}),
        "cloud_published": {"state": PASS if pub in ("SUCCESS", "PARTIAL_SUCCESS") else FAIL, "detail": f"publish status {pub or 'not recorded'}"},
    }
    report["checks"] = checks
    report["last_real_cluster"] = {"cluster_id": key, "outcome": r.get("outcome"), "tags": r.get("tags")}
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = certify()
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"MONEYLINE T-35: ARCHITECTURE_READY={report['architecture_ready']}  LIVE_OBSERVED={report['live_observed']}  "
              f"({report['status']}; real clusters completed: {report['real_clusters_completed']}, audited: {report['clusters_audited']})")
        print(f"last cluster: {report['last_cluster']}")
        for name, c in report["checks"].items():
            print(f"  [{c['state']:<14}] {name} -- {c['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
