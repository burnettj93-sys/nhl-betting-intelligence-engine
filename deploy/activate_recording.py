"""
Activate automatic paper recording of the ONE Eggy primary parlay a day, once the safeguards pass, and write the activation log.

    python3 -m deploy.activate_recording                  # runs the safeguard checks and prints them; changes nothing
    python3 -m deploy.activate_recording --apply          # if every check passes: approve the policy digest, end the pause, append the activation log

Owner authorisation (chat, 2026-10-18): automatic PAPER recording is authorised once deployment and recording-safeguard checks pass: at most one qualifying Eggy parlay per Eastern day, $10 stake,
combined odds from +100 to +500 inclusive, under the corrected policy (digest below). No daily approval is required. No real-money betting is authorised. Experiment 2 keeps its $500 start and is
never reset by this command; Experiment 1 and every historical record stay as they are.

Nothing here lowers a threshold, edits a probability or forces a ticket: it only lifts the pause the owner placed on 2026-10-13, for exactly the policy the owner named.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EXPECTED_POLICY_ID = "primary-slip-2026-10-14"
EXPECTED_DIGEST = "1fad3ba03503cf94"
EXPECTED_PARAMETERS = {"slots": 1, "min_combined_decimal": 2.0, "max_combined_decimal": 6.0, "anchor_cap": 0.02}
EXPECTED_STAKE = 10.0
LOG_NAME = "recording_activation_log.jsonl"
WATCHDOG_MAX_AGE_MIN = 45
OWNER = "owner (chat instruction 2026-10-18: automatic paper recording authorised once safeguards pass; one qualifying Eggy parlay per Eastern day, $10, +100 to +500)"


def checks(now: dt.datetime | None = None) -> list[dict]:
    """Every safeguard, each as {"name", "ok", "detail"}. Reads only; never changes state."""
    from operational import paper_bankroll as pb, ticket_policy, watchdog
    now = now or dt.datetime.now(dt.timezone.utc)
    out: list[dict] = []

    def add(name, ok, detail):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        pol = ticket_policy.checked_active()
        params = pol.as_dict()
        wrong = {k: (params.get(k), v) for k, v in EXPECTED_PARAMETERS.items() if params.get(k) != v}
        add("policy_is_the_one_the_owner_named", pol.policy_id == EXPECTED_POLICY_ID and pol.digest() == EXPECTED_DIGEST and not wrong,
            f"{pol.policy_id} digest {pol.digest()} (expected {EXPECTED_POLICY_ID} {EXPECTED_DIGEST}); parameters differing from the authorised ones: {wrong or 'none'}")
        add("one_ticket_a_day_at_most", int(pol.slots) == 1 and (pol.max_per_player or 1) == 1, f"slots {pol.slots}, per player {pol.max_per_player}")
        add("odds_window_plus_100_to_plus_500", pol.min_combined_decimal == 2.0 and pol.max_combined_decimal == 6.0, f"{pol.min_combined_decimal:.1f} to {pol.max_combined_decimal} decimal")
        add("shots_cap_is_the_corrected_plus_0_02", pol.anchor_cap == 0.02, f"anchor_cap {pol.anchor_cap}")
    except Exception as exc:  # noqa: BLE001
        add("policy_loads", False, f"{exc.__class__.__name__}: {exc}")
    add("stake_is_ten_dollars", pb.PAPER_BET_STAKE == EXPECTED_STAKE, f"PAPER_BET_STAKE {pb.PAPER_BET_STAKE}")
    conn = pb.init_db()
    try:
        exps = pb.experiments(conn)
        active = [e for e in exps if e["status"] == "ACTIVE"]
        a = active[0] if len(active) == 1 else None
        acct = pb.account_state(conn, pb.MODEL_TRACK)
        add("experiment_2_is_the_single_active_experiment", a is not None and a["number"] >= 2 and a["starting_bankroll"] == 500.0, f"active: {[(e['experiment_id'], e['starting_bankroll']) for e in active]}")
        add("experiment_1_is_archived_and_intact", any(e["experiment_id"] == "EXP-1" and e["status"] == "ARCHIVED" for e in exps)
            and pb.experiment_report(conn, "EXP-1")["account"]["tickets"] >= 1, "EXP-1 ARCHIVED with its tickets")
        add("experiment_2_account_reconciles", abs(acct["available_cash"] + acct["open_stakes"] - (float(a["starting_bankroll"]) + acct["settled_pnl"])) < 0.005 if a else False,
            f"cash {acct['available_cash']} open {acct['open_stakes']} pnl {acct['settled_pnl']} tickets {acct['tickets']}")
        rec = watchdog.check_reconciliation(conn)
        add("reconciliation_agrees_three_ways", rec["status"] == watchdog.OK, rec["detail"])
    finally:
        conn.close()
    for fn_name in ("check_ticket_policy",):
        r = getattr(watchdog, fn_name)()
        add("policy_check_passes", r["status"] != watchdog.FAIL, r["detail"])
    st = watchdog.load_state() or {}
    try:
        age_min = (now - dt.datetime.fromisoformat(str(st.get("checked_at_utc")).replace("Z", "+00:00"))).total_seconds() / 60.0
    except (TypeError, ValueError):
        age_min = None
    add("watchdog_state_is_fresh", age_min is not None and age_min <= WATCHDOG_MAX_AGE_MIN, f"watchdog state is {'unknown' if age_min is None else f'{age_min:.0f} min'} old (limit {WATCHDOG_MAX_AGE_MIN})")
    by = {c["name"]: c for c in (st.get("checks") or [])}
    import subprocess
    release = Path.home() / "nhl_engine_release"
    def head(path):
        try:
            return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    mine, rel = head(REPO), head(release)
    add("this_code_is_the_deployed_release", mine is not None and (mine == rel or REPO.resolve() == release.resolve()), f"this checkout {str(mine)[:10]}, release {str(rel)[:10]}")
    for name in ("release_pinned", "trader_recent", "publish_recent", "database_path"):
        c = by.get(name)
        add(f"watchdog_{name}", bool(c) and c["status"] == watchdog.OK, (c or {}).get("detail", "no watchdog state"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--evidence", default="", help="free text recorded in the log: which safeguard test runs passed")
    args = ap.parse_args(argv)
    from operational import paper_bankroll as pb, recording_pause, state_paths, ticket_policy
    now = dt.datetime.now(dt.timezone.utc)
    results = checks(now)
    failed = [c for c in results if not c["ok"]]
    print(json.dumps({"checks": results, "all_pass": not failed}, indent=1))
    if not args.apply:
        return 0 if not failed else 1
    if failed:
        print("NOT ACTIVATED: " + ", ".join(c["name"] for c in failed), file=sys.stderr)
        return 1
    st = recording_pause.status()
    if not st["paused"]:
        print("recording is already on: nothing to do")
        return 0
    if st.get("set_by") == "unreadable":
        print("NOT ACTIVATED: the pause file is unreadable; fix it first (resume would delete it)", file=sys.stderr)
        return 1
    conn = pb.init_db()
    try:
        exp = pb.experiment_brief(pb.active_experiment(conn))
    finally:
        conn.close()
    pol = ticket_policy.checked_active()
    entry = {"activated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "policy_id": pol.policy_id, "approved_policy_digest": pol.digest(), "approved_by": OWNER,
             "parameters": pol.as_dict(), "limits": {"tickets_per_eastern_day": 1, "stake_usd": EXPECTED_STAKE, "combined_odds": "+100 to +500 inclusive", "real_money": "NOT authorised"},
             "pause_in_force": st, "experiment": exp, "safeguard_checks": results, "evidence": args.evidence}
    log = state_paths.path(LOG_NAME)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:                       # the record is written FIRST: if anything after it fails, the log still says what was intended
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    approval = ticket_policy.approve(EXPECTED_DIGEST, approved_by=OWNER, now=now)
    recording_pause.resume(now=now)
    print(json.dumps({"activated": True, "digest": approval["digest"], "log": str(log)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
