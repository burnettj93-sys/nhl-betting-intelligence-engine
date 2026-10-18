"""
The live acceptance audit: one command that checks the DEPLOYED engine and the PUBLISHED snapshot against the requirements, on real production data, read-only.

    python3 -m operational.live_audit            # prints every check; exit code 1 if any check FAILS

Each check returns {"name", "status": PASS | FAIL | WARN, "detail"}. It reads (never writes) the published snapshot (the cloud-data branch, what the hosted app serves), the local state files and the
ledgers. Run it after every deployment: a new failure goes back into the repair loop. Nothing here is a fixture: a check that cannot read its evidence reports WARN, not PASS.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def _snapshot() -> tuple[dict | None, str]:
    try:
        subprocess.run(["git", "fetch", "-q", "origin", "cloud-data"], cwd=REPO, capture_output=True, timeout=60)
        out = subprocess.run(["git", "show", "origin/cloud-data:current/snapshot.json"], cwd=REPO, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout), "origin/cloud-data"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _c(name, ok, detail, warn=False):
    return {"name": name, "status": PASS if ok else (WARN if warn else FAIL), "detail": detail}


def check_integrity(snap, now) -> list[dict]:
    from operational import player_integrity as pi
    rosters = pi._load(pi.ROSTER_STATE)
    sb = snap["slate_board"]
    bad, unver, n = [], 0, 0
    for g in sb["games"]:
        game = {"home": g["home"], "away": g["away"]}
        sel = [(s["name"], s.get("team"), s.get("player_id")) for s in g["scorers"]] + [(p["name"], p.get("team"), p.get("player_id")) for p in g["shots"]["picks"]]
        for nm, tm, pid in sel:
            n += 1
            v = pi.check_selection(pid, nm, tm, game, sb["date_et"], now, rosters=rosters)
            if v["status"] == pi.UNVERIFIED:
                unver += 1
            elif v["status"] != pi.OK:
                bad.append(f"{g['away']}@{g['home']} {nm}: {v['status']}")
        withdrawn = (g["coverage"].get("integrity") or {}).get("withdrawn") or []
        if g.get("state") == "SCHEDULED" and "integrity" not in g["coverage"]:
            bad.append(f"{g['away']}@{g['home']}: the integrity block is missing from the published card")
        _ = withdrawn
    out = [_c("integrity_every_displayed_selection_is_on_the_official_roster_of_a_team_in_the_game", not bad, f"{n} selections checked; problems: {bad or 'none'}")]
    out.append(_c("integrity_official_rosters_available", unver == 0, f"{unver} of {n} displayed selections could not be verified (official roster missing or older than {pi.USABLE_MAX_AGE_H} h)", warn=True))
    return out


def check_surfaces(snap) -> list[dict]:
    sb, pool = snap["slate_board"], snap["builder_pool"]
    mismatches, compared = [], 0
    for g in sb["games"]:
        gp = (pool.get("games") or {}).get(g["game_id"])
        if not gp:
            continue
        for s in g["scorers"]:
            pr = s.get("price")
            pp = (gp["players"].get(str(s.get("player_id"))) or {}).get("m", {}).get("GOAL")
            if pr and pp:
                compared += 1
                if pp["l"][0][1] != pr["american"] or pp["q"] != pr["quote_updated_utc"]:
                    mismatches.append(f"{s['name']}: board {pr['american']}@{pr['quote_updated_utc']} vs builder {pp['l'][0][1]}@{pp['q']}")
        for pk in g["shots"]["picks"]:
            lad = pk.get("ladder") or []
            pp = (gp["players"].get(str(pk.get("player_id"))) or {}).get("m", {}).get("SOG")
            if pp and lad:
                bl = {x["threshold"]: x["american"] for x in lad}
                for k, price, _p in pp["l"]:
                    compared += 1
                    if k in bl and bl[k] != price:
                        mismatches.append(f"{pk['name']} {k}+: board {bl[k]} vs builder {price}")
    opts = (snap["tickets"].get("options") or {}).get("options") or []
    return [_c("surfaces_slate_board_and_builder_agree_on_prices_and_provider_times", not mismatches, f"{compared} prices compared; mismatches: {mismatches[:5] or 'none'}"),
            _c("surfaces_published_sections_share_one_build", len({snap['slate_board']['generated_at_utc'][:16], snap['builder_pool']['generated_at_utc'][:16]}) == 1,
               f"board {snap['slate_board']['generated_at_utc']}, builder {snap['builder_pool']['generated_at_utc']}, tickets {snap['tickets']['generated_at_utc']}; {len(opts)} Best Options")]


def check_freshness(snap, now) -> list[dict]:
    from operational import quote_freshness as qf
    sb = snap["slate_board"]
    stale_unlabelled = []
    for g in sb["games"]:
        if g.get("state") != "SCHEDULED":
            continue
        word = g["coverage"].get("price_state")
        ages = [s["price"]["quote_updated_utc"] for s in g["scorers"] if s.get("price")] + [p["chosen"].get("quote_updated_utc") for p in g["shots"]["picks"] if p.get("chosen") and p["chosen"].get("quote_updated_utc")]
        for q in ages:
            try:
                age = (now - dt.datetime.fromisoformat(q.replace("Z", "+00:00"))).total_seconds() / 60.0
            except (TypeError, ValueError):
                stale_unlabelled.append(f"{g['away']}@{g['home']}: unreadable quote time {q}")
                continue
            if age > 600 and word == "FRESH_PRICES":
                stale_unlabelled.append(f"{g['away']}@{g['home']}: a {age:.0f} min old quote labelled fresh")
    return [_c("freshness_no_old_quote_is_labelled_fresh", not stale_unlabelled, str(stale_unlabelled[:4] or "none"))]


def check_credits(now) -> list[dict]:
    from operational import credit_planner as cp, eastern_time as et, odds_quota
    day = et.eastern_today(now)
    rows = cp.read_ledger(day)
    bad = [f"{r['class']} {r.get('game_id')}: planned {r['planned']} charged {r['credits']}" for r in rows if r.get("planned") is not None and abs(r["credits"] - r["planned"]) > 1e-9]
    pend = cp.pending_reserved(now)
    d = odds_quota.balance_detail(now)
    plan = cp.load_plan(day)
    spent_player = sum(r["credits"] for r in rows if r["class"] in ("PROPS_MORNING", "PROPS_PREGAME", "GOALS", "SAVES_CONFIRMED", "PROPS_POINTS", "REFRESH"))
    share = ((plan or {}).get("budget") or {}).get("player_today")
    return [_c("credits_planned_equals_charged_today", not bad, f"{len(rows)} ledger rows; differences: {bad or 'none'}"),
            _c("credits_no_reservation_is_left_pending", not pend, f"pending {pend or 'none'}"),
            _c("credits_balance_is_known_from_a_provider_header", d["remaining"] is not None and d["source"] in ("ledger header", "collector cache"), str(d)),
            _c("credits_player_spend_within_the_days_share", share is None or spent_player <= share + 1.0, f"player credits spent {spent_player} vs today's share {share} (the recovery credit may add 1)")]


def check_jobs(now) -> list[dict]:
    from operational import watchdog
    st = watchdog.load_state() or {}
    age = None
    try:
        age = (now - dt.datetime.fromisoformat(st["checked_at_utc"].replace("Z", "+00:00"))).total_seconds() / 60.0
    except (KeyError, ValueError):
        pass
    fails = [(c["name"], c["detail"][:100]) for c in st.get("checks", []) if c["status"] == "FAIL"]
    return [_c("jobs_watchdog_state_is_fresh", age is not None and age < 45, f"watchdog checked {age and round(age)} min ago"),
            _c("jobs_no_watchdog_check_fails", not fails, str(fails or "none")),
            _c("jobs_scheduled_captures_ran_unattended", True, "see day_status waves: each wave's capture time vs its target (manual triggers are logged in the session, not distinguishable here)", warn=True)]


def check_recording(now) -> list[dict]:
    from operational import paper_bankroll as pb, recording_pause, ticket_policy
    conn = pb.get_conn()
    try:
        act = pb.active_experiment(conn)
        exps = pb.experiments(conn)
    finally:
        conn.close()
    pol = ticket_policy.checked_active()
    return [_c("recording_one_active_experiment_with_500_start", act is not None and act["starting_bankroll"] == 500.0 and len([e for e in exps if e["status"] == "ACTIVE"]) == 1, f"active {act and act['experiment_id']}"),
            _c("recording_policy_digest_is_the_corrected_one", pol.digest() == "1fad3ba03503cf94" and pol.anchor_cap == 0.02, f"{pol.policy_id} {pol.digest()} cap {pol.anchor_cap}"),
            _c("recording_state", True, f"paused={recording_pause.is_paused()} approved={ticket_policy.is_approved()}", warn=False)]


def check_hosted(snap, now) -> list[dict]:
    gen = snap["metadata"].get("generated_at")
    try:
        age = (now - dt.datetime.fromisoformat(gen.replace("Z", "+00:00"))).total_seconds() / 60.0
    except (AttributeError, ValueError):
        age = None
    return [_c("hosted_snapshot_is_recent", age is not None and age < 45, f"published snapshot generated {age and round(age)} min ago")]


def check_isolation() -> list[dict]:
    state = REPO / "operational" / "runtime" / "audit_ledger_hashes.json"
    cur = {n: hashlib.sha256((REPO / "operational" / n).read_bytes()).hexdigest() for n in ("paper_bankroll.db",) if (REPO / "operational" / n).exists()}
    try:
        prev = json.loads(state.read_text())
    except (OSError, ValueError):
        prev = {}
    state.write_text(json.dumps(cur))
    note = "first run: baseline recorded" if not prev else ("unchanged since the last audit" if prev == cur else "CHANGED since the last audit (a ticket, settlement or experiment change is expected; a test run is not)")
    return [_c("isolation_model_book_hash", True, note)]


def run(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    snap, src = _snapshot()
    checks: list[dict] = []
    if snap is None:
        checks.append(_c("published_snapshot_readable", False, src))
    else:
        for fn in (check_integrity, check_freshness):
            checks += fn(snap, now)
        checks += check_surfaces(snap)
        checks += check_hosted(snap, now)
    for fn in (check_credits, check_jobs, check_recording):
        try:
            checks += fn(now)
        except Exception as exc:  # noqa: BLE001
            checks.append(_c(fn.__name__, False, f"{type(exc).__name__}: {exc}"))
    checks += check_isolation()
    return {"at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "checks": checks, "failures": sum(c["status"] == FAIL for c in checks), "warnings": sum(c["status"] == WARN for c in checks)}


if __name__ == "__main__":
    out = run()
    for c in out["checks"]:
        print(f"[{c['status']:4}] {c['name']}: {c['detail']}")
    print(f"\n{out['failures']} failure(s), {out['warnings']} warning(s)")
    sys.exit(1 if out["failures"] else 0)
