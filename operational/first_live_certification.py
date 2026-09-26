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

import datetime as dt
import subprocess

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
    report = {"architecture_ready": True, "live_observed": obs["live_observed"], "live_certified": obs.get("live_certified", False),
              "status": obs["status"],
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
        "real_cluster_seen": {"state": PASS, "detail": f"{key} (fired: pull at {r.get('actual_pull_utc')}, provider listed: {r.get('provider_listed')})"},
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
        "cloud_published": {"state": PASS if pub in ("SUCCESS", "PARTIAL_SUCCESS") else FAIL,
                            "detail": f"publish status {pub or 'not recorded'}; snapshot hash {(r.get('cloud_publish') or {}).get('content_hash')}"},
    }
    report["checks"] = checks
    # LIVE_CERTIFIED additionally needs the ledger-side proofs (never an HTTP 200 alone)
    ledger_ok = checks["observation_persisted"]["state"] == PASS and checks["paper_bet_if_bet"]["state"] in (PASS, NA)
    if report["status"] == mp.LIVE_CERTIFIED and not ledger_ok:
        report["status"], report["live_certified"] = mp.LIVE_OBSERVED, False
        report["certification_blocked_by"] = [k for k in ("observation_persisted", "paper_bet_if_bet") if checks[k]["state"] == FAIL]
    report["post_event"] = {"cluster_fired": True, "actual_pull_utc": r.get("actual_pull_utc"), "pull_within_window": bool(r.get("in_decision_window")),
                            "provider_listed": r.get("provider_listed"), "credits_spent": r.get("credits_spent"),
                            "dk_rows_stored": r.get("odds_rows_stored"), "recommendations_evaluated": r.get("recommendations_evaluated"),
                            "bet": r.get("bet_count"), "wait": r.get("wait_count"), "pass": r.get("pass_count"),
                            "data_unavailable": r.get("data_unavailable_count"), "observations_persisted": persisted,
                            "paper_bets": bets, "cloud_publish": pub, "snapshot_hash": (r.get("cloud_publish") or {}).get("content_hash")}
    report["last_real_cluster"] = {"cluster_id": key, "outcome": r.get("outcome"), "tags": r.get("tags")}
    return report


# ---- pre-flight (BEFORE the event) and the overall state machine ---------------------------------------------
LABEL = "com.nhlengine.moneyline-pregame"


def _git(wd: str, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", wd, *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def scheduler_code(label: str = LABEL, runner=None) -> dict:
    """Which code does the loaded launchd job execute? (working directory, branch, commit, master?, dirty?)"""
    import json as _json
    from operational import scheduler_audit as sa
    run = runner or sa._run
    info = sa.launchctl_info(label, run)
    wd = None
    plist = sa.AGENTS_DIR / f"{label}.plist"
    if plist.exists():
        pl = sa.read_plist(plist, run)
        wd = (pl or {}).get("WorkingDirectory")
    out = {"loaded": info["loaded"], "runs_since_boot": info["runs"], "last_exit": info["last_exit"], "working_directory": wd}
    if wd:
        branch, head = _git(wd, "rev-parse", "--abbrev-ref", "HEAD"), _git(wd, "rev-parse", "--short", "HEAD")
        dirty = bool(_git(wd, "status", "--porcelain", "--untracked-files=no"))
        master = _git(wd, "rev-parse", "--short", "master")
        out.update(branch=branch, commit=head, master_commit=master, dirty=dirty,
                   on_master=(branch == "master" and not dirty and head == master))
    return out


def preflight(now: dt.datetime | None = None, *, deps: dict | None = None) -> dict:
    """Read-only, ZERO paid requests (and no network at all): everything needed to know, before a cluster, that the
    first real T-35 will run. `deps` lets tests inject each probe."""
    now = now or dt.datetime.now(dt.timezone.utc)
    deps = deps or {}

    def dep(name, fn):
        return deps[name]() if name in deps else fn()

    def _next():
        starts = mp.scheduled_starts(now, hours=240, back_hours=0)
        clusters = [c for c in mp.plan_clusters(starts) if c.window[1] >= now and mp.archived_listing(c) is not False]
        if not clusters:
            return None
        c = clusters[0]
        state = mp.load_state()
        return {"cluster_id": c.key, "games": c.games, "expected_start_utc": c.smin.isoformat(), "t35_target_utc": c.target_pull.isoformat(),
                "t30_anchor_utc": c.anchor.isoformat(), "t40_opens_utc": c.window[0].isoformat(), "capture_window_utc": [c.window[0].isoformat(), c.window[1].isoformat()],
                "provider_listing": {True: "LISTED (newest archived events response)", False: "NOT_LISTED", None: "UNKNOWN"}[mp.archived_listing(c)],
                "already_done": (state["clusters"].get(c.key) or {}).get("status") == "DONE"}

    def _quota():
        from operational import odds_quota
        g = odds_quota.guard(planned=1, now=now, soft_multiplier=odds_quota.PREGAME_SOFT_MULTIPLIER)
        return {"credits_remaining": odds_quota.latest_remaining(), "sufficient": bool(g.get("allow")), "reason": g.get("reason"),
                "reset_day": odds_quota.reset_status()}

    def _publisher():
        from operational import publish_cloud_snapshot as pub
        return bool(pub.publishing_enabled())

    def _deployment():
        from operational import deployment_mode
        return deployment_mode.current_mode()

    def _power():
        from operational import keep_awake
        return keep_awake.power_risk()

    def _wake():
        from operational import schedule_next_wake
        return schedule_next_wake.verify(now)

    def _caffeinate():
        import os
        from operational import keep_awake
        return {"binary_present": os.path.exists("/usr/bin/caffeinate"), "guard_closes_wake_gap": hasattr(keep_awake, "guard_loop"),
                "assertion": "caffeinate -i -t <seconds> (idle-sleep only, self-ending), started by a wake-guard within ~5 s of a wake",
                "limits": "cannot wake a sleeping Mac; cannot override lid-close or explicit sleep/shutdown"}

    nxt = dep("next_cluster", _next)
    sched = dep("scheduler", scheduler_code)
    quota = dep("quota", _quota)
    publisher = dep("publisher", _publisher)
    mode = dep("deployment", _deployment)
    power = dep("power", _power)
    wake = dep("wake", _wake)
    caffeinate = dep("caffeinate", _caffeinate)
    repo = str(Path(__file__).resolve().parent.parent)
    git_state = dep("git", lambda: {"branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "commit": _git(repo, "rev-parse", "--short", "HEAD"),
                                    "worktree_clean": not bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))})
    audit = deps["audit"]() if "audit" in deps else mp.load_audit()
    obs = mp.live_observed(audit)

    problems = []
    if not sched.get("loaded"):
        problems.append("moneyline-pregame launchd job is not loaded")
    if sched.get("loaded") and sched.get("on_master") is False:
        problems.append(f"the scheduler executes {sched.get('branch')}@{sched.get('commit')}"
                        f"{' (uncommitted changes)' if sched.get('dirty') else ''}, not clean master")
    if not quota.get("sufficient"):
        problems.append(f"quota not sufficient ({quota.get('reason')})")
    if not publisher:
        problems.append("cloud publishing is not enabled (NHL_ENGINE_CLOUD_PUBLISH)")
    if str(mode).upper() != "ACTIVE":
        problems.append(f"deployment mode is {mode}, not ACTIVE")
    if not getattr(mp, "END_TO_END_CERTIFIED", False):
        problems.append("end-to-end dry run not certified")
    if not caffeinate.get("binary_present") or not caffeinate.get("guard_closes_wake_gap"):
        problems.append("keep-awake strategy is not available (caffeinate / wake-guard)")
    if not git_state.get("worktree_clean"):
        problems.append("the working tree has uncommitted changes")
    architecture_ready = not problems
    state = overall_state(obs, architecture_ready, bool(nxt))
    # PRE-FLIGHT verdict: READY / OWNER_ACTION_REQUIRED / NOT_READY (the wake needs the owner's sudo, so it is an owner action)
    owner_actions = []
    if nxt and wake.get("state") == "OWNER_ACTION_REQUIRED":
        owner_actions.append("schedule the one-time wake: " + str(wake.get("command")))
    if nxt and str(power.get("risk")) == "HIGH" and wake.get("state") != "SCHEDULED":
        owner_actions.append("or keep the Mac awake yourself (idle sleep is 1 minute on AC); lid open, on AC power")
    # An unknown quota RESET DATE is a warning, never a blocker: the spend guard's hard reserve depends only on the real
    # `x-requests-remaining`, so a 1-credit pull is safe whenever `quota.sufficient` is true (checked above, in `problems`).
    warnings = []
    if not (quota.get("reset_day") or {}).get("status", "OWNER_CONFIGURED").startswith("OWNER_CONFIGURED"):
        warnings.append("ODDS_RESET_DAY: OWNER VERIFICATION PENDING - set NHL_ENGINE_ODDS_RESET_DAY (Odds API Account / Usage); "
                        "the reserve floor is enforced from the real remaining credits, only the daily-pace projection assumes the 1st")
    verdict = "NOT_READY" if not architecture_ready else ("OWNER_ACTION_REQUIRED" if owner_actions else "READY")
    return {"generated_at_utc": now.isoformat(), "state": state, "architecture_ready": architecture_ready,
            "architecture_problems": problems, "next_cluster": nxt, "scheduler": sched, "quota": quota,
            "cloud_publisher_enabled": publisher, "deployment_mode": mode, "machine_power": power,
            "power_risk": power.get("risk"), "live_observed": obs["live_observed"], "live_certified": obs.get("live_certified", False),
            "git": git_state, "wake": wake, "keep_awake": caffeinate, "preflight_verdict": verdict, "owner_actions": owner_actions, "warnings": warnings,
            "lid_note": "lid must be OPEN (or an external display attached) and the Mac on AC power; neither pmset nor caffeinate can override closed-lid sleep",
            "paid_requests_made": 0}


def overall_state(obs: dict, architecture_ready: bool, next_cluster_known: bool) -> str:
    """NOT_READY -> ARCHITECTURE_READY -> WAITING_FOR_FIRST_REAL_CLUSTER -> LIVE_OBSERVED -> LIVE_CERTIFIED (FAILED on a
    real cluster that yielded no data). Evidence outranks configuration: once a real cluster certified, a later
    configuration problem does not un-certify history."""
    if obs.get("live_certified"):
        return mp.LIVE_CERTIFIED
    if obs.get("live_observed"):
        return mp.LIVE_OBSERVED
    if obs.get("status") == mp.FAILED_STATE:
        return mp.FAILED_STATE
    if not architecture_ready:
        return mp.NOT_READY
    return mp.ARCHITECTURE_READY_ONLY if next_cluster_known else mp.ARCHITECTURE_READY


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = certify()
    pre = preflight()
    report["preflight"] = pre
    report["overall_state"] = overall_state(mp.live_observed(), pre["architecture_ready"], bool(pre["next_cluster"]))
    if "--json" in argv:
        print(json.dumps(report, indent=2, default=str))
        return 0
    if "--wake-plan" in argv:
        from operational import schedule_next_wake
        return schedule_next_wake.main(["--verify"])
    print(f"PRE-FLIGHT: {pre['preflight_verdict']}")
    print(f"MONEYLINE T-35 OVERALL: {report['overall_state']}   (ARCHITECTURE_READY={pre['architecture_ready']}, "
          f"LIVE_OBSERVED={report['live_observed']}, LIVE_CERTIFIED={report['live_certified']})")
    print("\nPRE-FLIGHT (zero paid requests)")
    nc = pre["next_cluster"]
    print("  next cluster: " + (f"{nc['cluster_id']} - {nc['games']} game(s), start {nc['expected_start_utc']}, T-35 target {nc['t35_target_utc']}, "
                               f"T-30 anchor {nc['t30_anchor_utc']}, provider: {nc['provider_listing']}" if nc else "none within 10 days"))
    if nc:
        print(f"  T-40 window opens {nc['t40_opens_utc']}  (capture window {nc['capture_window_utc'][0]} .. {nc['capture_window_utc'][1]})")
    g = pre["git"]
    print(f"  master commit: {g.get('commit')} on {g.get('branch')}; worktree clean={g.get('worktree_clean')}")
    w = pre["wake"]
    print(f"  wake event: {w.get('state')} - {w.get('detail')}")
    k = pre["keep_awake"]
    print(f"  keep-awake: caffeinate present={k.get('binary_present')}, wake-guard={k.get('guard_closes_wake_gap')} ({k.get('limits')})")
    for a in pre["owner_actions"]:
        print(f"  OWNER ACTION: {a}")
    for a in pre.get("warnings", []):
        print(f"  WARNING: {a}")
    sc = pre["scheduler"]
    print(f"  scheduler: loaded={sc.get('loaded')} runs={sc.get('runs_since_boot')} code={sc.get('branch')}@{sc.get('commit')} "
          f"on clean master={sc.get('on_master')} ({sc.get('working_directory')})")
    q = pre["quota"]
    print(f"  quota: {q['credits_remaining']} credits remaining, sufficient={q['sufficient']} ({q['reason']}); reset day {q['reset_day']['status']}")
    print(f"  cloud publisher enabled={pre['cloud_publisher_enabled']}  deployment mode={pre['deployment_mode']}  machine power risk={pre['power_risk']}")
    for p in pre["architecture_problems"]:
        print(f"  ! {p}")
    print(f"\nPOST-EVENT (real clusters completed: {report['real_clusters_completed']}, audited: {report['clusters_audited']}; status {report['status']})")
    print(f"  last cluster: {report['last_cluster']}")
    for name, c in report["checks"].items():
        print(f"  [{c['state']:<14}] {name} -- {c['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
