"""
Activate the temporary 14-day PAPER learning experiment (POSTMORTEM_LEARNING_14D) once every safeguard passes, and write the activation log.

    python3 -m deploy.activate_learning_experiment             # dry run: runs every safeguard and a no-write rehearsal on today's real legs; changes nothing
    python3 -m deploy.activate_learning_experiment --apply     # if every check passes: write the activation log, then approve the policy digest (funds the experiment bankroll)

Owner authorisation (chat, 2026-10-20), verbatim: "I AUTHORIZE A 14-DAY PAPER-BOOK LEARNING EXPERIMENT FROM OCTOBER 20 THROUGH NOVEMBER 2, 2026, TARGETING 10 REAL, ELIGIBLE PAPER BETS PER EASTERN DAY
AT $10 EACH, WITH ACTUAL ODDS OF -150 OR BETTER." Owner corrections (chat, 2026-10-20): the experiment bankroll STARTS at $500.00 and is reset to $500.00 whenever cash falls below the next $10 stake; every bet carries an edge class (POSITIVE_EDGE / NEUTRAL_UNCERTAIN_EDGE / LEARNING_SAMPLE_NEGATIVE_EDGE). PAPER ONLY; no real-money wagering is authorised.
Eggy stays at one official pick a day, on its own ledger. The experiment ends by itself on 2026-11-03 Eastern. Nothing here lowers an integrity gate, edits a probability or forces a bet.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EXPECTED = {"daily_target": 10, "stake_usd": 10.0, "odds_floor_american": -150.0, "initial_bankroll_usd": 500.0, "reset_to_usd": 500.0, "reset_when_below_usd": 10.0, "days": 14,
            "start_date": "2026-10-20", "end_date": "2026-11-02"}
EGGY_POLICY_DIGEST = "1fad3ba03503cf94"
OWNER = ("owner (chat authorisation 2026-10-20: 14-day paper-book learning experiment, 10 real eligible paper bets per Eastern day at $10, actual odds -150 or better, "
         "experiment bankroll reset to $500 whenever it cannot fund the next bet)")
LOG_NAME = "learning_experiment_activation_log.jsonl"
WATCHDOG_MAX_AGE_MIN = 45


def checks(now: dt.datetime) -> list[dict]:
    """Every safeguard, each {"name","ok","detail"}. Reads only (the rehearsal uses an in-memory ledger)."""
    from operational import learning_cohort as lc, recording_pause, ticket_policy
    out: list[dict] = []

    def add(name, ok, detail):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    wrong = {k: (lc.POLICY.get(k), v) for k, v in EXPECTED.items() if lc.POLICY.get(k) != v}
    add("policy_is_the_one_the_owner_named", not wrong and lc.POLICY["paper_only"] is True, f"digest {lc.policy_digest()}; differences from the authorised terms: {wrong or 'none'}")
    add("window_is_14_eastern_days", (dt.date.fromisoformat(lc.END_DATE) - dt.date.fromisoformat(lc.START_DATE)).days + 1 == 14 and lc.phase(now)["phase"] == "ACTIVE",
        f"{lc.START_DATE} to {lc.END_DATE}; now {lc.phase(now)}")
    et = lc.et.EASTERN
    last, after = lc.phase(dt.datetime(2026, 11, 2, 23, 59, tzinfo=et)), lc.phase(dt.datetime(2026, 11, 3, 0, 1, tzinfo=et))
    add("ends_automatically_on_nov_3", last["phase"] == "ACTIVE" and last["day"] == 14 and after["phase"] == "ENDED", f"Nov 2 23:59 ET {last['phase']}, Nov 3 00:01 ET {after['phase']}")
    try:
        pol = ticket_policy.checked_active()
        add("eggy_unchanged_one_official_pick_a_day", int(pol.slots) == 1 and pol.digest() == EGGY_POLICY_DIGEST, f"Eggy policy {pol.policy_id} digest {pol.digest()} slots {pol.slots}")
    except Exception as exc:  # noqa: BLE001
        add("eggy_policy_loads", False, f"{exc.__class__.__name__}: {exc}")
    add("eggy_recording_not_paused", not recording_pause.is_paused(), "the normal paper book keeps recording its one Eggy pick")
    # the ledger: schema, immutability and append-only triggers, funding and the reset rule, on a throwaway in-memory database
    try:
        m = lc.open_ledger(":memory:")
        lc.ensure_funded(m, now)
        triggers = {r[0] for r in m.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        want = {"bets_entry_is_immutable", "bets_never_replaced", "funding_is_append_only_u", "funding_is_append_only_d", "bets_are_never_deleted"}
        guarded = sorted(want - triggers)
        m.execute("INSERT INTO funding_events (at_utc, at_et, kind, balance_before, amount, balance_after, experiment_day, reason) VALUES ('x','x','TEST',0,-1395,0,1,'t')")
        ev = lc.reset_if_needed(m, now, lc.phase(now)["day"])
        ok = not guarded and lc.balance(m)["initial_funding"] == 500.0 and ev and ev["balance_after"] == 500.0
        add("ledger_and_reset_rule_work", ok, f"missing protections: {guarded or 'none'}; rehearsal reset {ev}")
    except Exception as exc:  # noqa: BLE001
        add("ledger_and_reset_rule_work", False, f"{exc.__class__.__name__}: {exc}")
    # watchdog + deployed release
    import subprocess
    from operational import watchdog
    st = watchdog.load_state() or {}
    try:
        age = (now - dt.datetime.fromisoformat(str(st.get("checked_at_utc")).replace("Z", "+00:00"))).total_seconds() / 60.0
    except (TypeError, ValueError):
        age = None
    add("watchdog_state_is_fresh", age is not None and age <= WATCHDOG_MAX_AGE_MIN, f"watchdog state {'unknown' if age is None else f'{age:.0f} min'} old (limit {WATCHDOG_MAX_AGE_MIN})")
    release = Path.home() / "nhl_engine_release"

    def head(path):
        try:
            return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    mine, rel = head(REPO), head(release)
    add("this_code_is_the_deployed_release", mine is not None and (mine == rel or REPO.resolve() == release.resolve()), f"this checkout {str(mine)[:10]}, release {str(rel)[:10]}")
    by = {c["name"]: c for c in (st.get("checks") or [])}
    for name in ("release_pinned", "trader_recent", "publish_recent", "database_path"):
        c = by.get(name)
        add(f"watchdog_{name}", bool(c) and c["status"] == watchdog.OK, (c or {}).get("detail", "no watchdog state"))
    return out


def rehearsal(now: dt.datetime) -> dict:
    """What the next trader cycle would record on today's real legs, using the real cycle code on an IN-MEMORY ledger. Nothing is written anywhere."""
    from unittest import mock
    import db
    from operational import daily_tickets, learning_cohort as lc
    m = lc.open_ledger(":memory:")
    nhl = db.get_conn()
    try:
        # collecting legs is read-only apart from the shadow log the trader itself appends every cycle; that append is suppressed here so the rehearsal changes nothing at all
        with mock.patch("operational.moneyline_model_path.record_shadow"):
            collected = daily_tickets.collect_candidate_legs(nhl, now)
        with mock.patch.object(lc, "is_authorised", return_value=True), mock.patch.object(lc, "write_status"), mock.patch("operational.learning_postmortem.refresh", return_value={"status": "REHEARSAL_SKIPPED"}):
            out = lc.run_cycle(nhl, now, collected, conn=m)
        bets = [dict(r) for r in m.execute("SELECT selection_rank, market_family, participant_name, team, threshold, american_price, conservative_probability, edge, selection_score, wave_index, game_id FROM bets ORDER BY selection_rank")]
        after = lc.phase(dt.datetime(2026, 11, 3, 12, 0, tzinfo=lc.et.EASTERN))
        with mock.patch.object(lc, "is_authorised", return_value=True), mock.patch.object(lc, "write_status"), mock.patch("operational.learning_postmortem.refresh", return_value={"status": "REHEARSAL_SKIPPED"}):
            ended = lc.run_cycle(nhl, dt.datetime(2026, 11, 3, 16, 0, tzinfo=dt.timezone.utc), collected, conn=m)
        return {"legs_offered": len(collected.get("legs") or []), "result": out, "would_record": bets, "after_end_date": {"phase": after["phase"], "records_anything": len(bets) != m.execute("SELECT COUNT(*) FROM bets").fetchone()[0], "result": ended.get("recording")}}
    finally:
        nhl.close()
        m.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--evidence", default="", help="free text recorded in the log: which tests passed")
    args = ap.parse_args(argv)
    from operational import learning_cohort as lc, state_paths
    now = dt.datetime.now(dt.timezone.utc)
    results = checks(now)
    failed = [c for c in results if not c["ok"]]
    try:
        rh = rehearsal(now)
    except Exception as exc:  # noqa: BLE001
        rh = {"error": f"{exc.__class__.__name__}: {exc}"}
        failed.append({"name": "rehearsal_runs", "ok": False, "detail": rh["error"]})
    print(json.dumps({"policy_digest": lc.policy_digest(), "checks": results, "rehearsal": rh, "all_pass": not failed}, indent=1, default=str))
    if not args.apply:
        return 0 if not failed else 1
    if failed:
        print("NOT ACTIVATED: " + ", ".join(c["name"] for c in failed), file=sys.stderr)
        return 1
    if lc.is_authorised():
        print("the experiment is already activated: nothing to do")
        return 0
    entry = {"activated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "policy_digest": lc.policy_digest(), "approved_by": OWNER, "policy": lc.POLICY,
             "limits": {"bets_per_eastern_day": 10, "stake_usd": 10.0, "odds": "-150 or better", "real_money": "NOT authorised"}, "safeguard_checks": results, "rehearsal": rh, "evidence": args.evidence}
    log = state_paths.path(LOG_NAME)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:                       # written FIRST: if anything after fails, the log still says what was intended
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    doc = lc.activate(now, approved_by=OWNER, evidence=args.evidence)
    print(json.dumps({"activated": True, "digest": doc["policy_digest"], "log": str(log)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
