"""
Persistent self-check (launchd, every 30 minutes; deploy/launchd/com.nhlengine.watchdog.plist), in TWO separate parts:

  OPERATIONAL HEALTH -- is the unattended machinery running the code it should, on the right data, and are its books consistent?
      jobs_loaded           every scheduled job the product depends on is loaded in launchd, none last exited with an error;
      release_pinned        the release checkout is clean and equals origin/master (WARN while a deploy is pending);
      trader_recent         the 15-minute trader recorded a success in the last 45 minutes;
      publish_recent        the hosted snapshot was published in the last 60 minutes;
      database_path         the resolved NHL database is the live one and the ledger exists;
      reconciliation        the model book re-derived from raw rows (cash by P&L = cash by stake/return flows = the account helper);
      settlement_backlog    model bets whose game started more than 6 hours ago and are still open: WARN, FAIL past 12 hours;
      source_freshness      the sources the engine depends on (schedule, results, player logs) are inside their own freshness policies;
      quote_freshness       prop and moneyline prices are inside their rules (a price stale only because the credit budget forbids a refresh is readiness, not failure);
      publishing_enabled    publication to the hosted app is switched ON (a demonstration that left it OFF is a FAIL).
      jobs_ran              every expected job wrote to its log within its own schedule (loaded is not enough)
      missed_windows        capture windows that closed without a capture in the last 24 h (reported, never bought late; WARN)
  PRODUCT READINESS -- which user-facing features actually work end to end, and which are blocked, limited or unverified, with the owner action each needs.

An operational "OK" means the machinery is running and the books agree. It does NOT mean a blocked feature works: readiness is reported on its own and
a blocked or unverified feature is shown as such however healthy the machinery is.

It writes operational/runtime/watchdog_state.json (read by Diagnostics, published in the snapshot's `health` section: timestamps and plain facts only) and
sends one macOS notification when operational health turns FAIL. It never changes anything, makes no network call except `git ls-remote`, spends no credits.

Run: python3 -m operational.watchdog
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from operational import state_paths

STATE_NAME = "watchdog_state.json"
REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_DIR = Path.home() / "nhl_engine_release"
TRADER_MAX_AGE_MIN = 45.0
PUBLISH_MAX_AGE_MIN = 60.0

EXPECTED_JOBS = (
    "com.nhlengine.real-parlay-paper-trader", "com.nhlengine.moneyline-snapshot", "com.nhlengine.daily-nhl-sync", "com.nhlengine.morning-refresh",
    "com.nhlengine.midday-schedule-refresh", "com.nhlengine.pregame-targeted-refresh", "com.nhlengine.daily-settlement", "com.nhlengine.daily-postmortem",
    "com.nhlengine.database-backup", "com.nhlengine.morning-update", "com.nhlengine.moneypuck-refresh",
)
# Retired 2026-10-17 (unloaded; their plists stay in the repository for the record): moneyline-pregame (the T-35 pull), prop-sweep-first and prop-sweep-second (saves ride on the wave capture),
# daily-props-pull (retired earlier). They are not expected to be loaded and must not make the watchdog fail.
RETIRED_JOBS = ("com.nhlengine.moneyline-pregame", "com.nhlengine.prop-sweep-first", "com.nhlengine.prop-sweep-second", "com.nhlengine.daily-props-pull")
OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 30) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or f"exit {out.returncode}")
    return out.stdout


def _age_min(stamp: str | None, now: dt.datetime) -> float | None:
    if not stamp:
        return None
    t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return (now - t).total_seconds() / 60.0


def check_jobs(launchctl_text: str) -> dict:
    rows = {}
    for line in launchctl_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.nhlengine."):
            rows[parts[2]] = parts[1]
    missing = [j for j in EXPECTED_JOBS if j not in rows]
    failing = [j for j in EXPECTED_JOBS if j in rows and rows[j] not in ("0", "-")]
    if missing:
        return {"name": "jobs_loaded", "status": FAIL, "detail": f"not loaded: {', '.join(m.split('.')[-1] for m in missing)}"}
    if failing:
        return {"name": "jobs_loaded", "status": WARN, "detail": f"last exit non-zero: {', '.join(f'{j.split(chr(46))[-1]}={rows[j]}' for j in failing)}"}
    return {"name": "jobs_loaded", "status": OK, "detail": f"all {len(EXPECTED_JOBS)} jobs loaded, last exits clean"}


def check_release(release_head: str | None, dirty: bool, origin_master: str | None) -> dict:
    if release_head is None:
        return {"name": "release_pinned", "status": FAIL, "detail": "the release checkout is missing or unreadable"}
    if dirty:
        return {"name": "release_pinned", "status": FAIL, "detail": f"the release checkout at {release_head[:10]} has local changes (jobs may run unreviewed code)"}
    if origin_master is None:
        return {"name": "release_pinned", "status": WARN, "detail": f"release at {release_head[:10]}; origin/master could not be read"}
    if release_head != origin_master:
        return {"name": "release_pinned", "status": WARN, "detail": f"release {release_head[:10]} differs from origin/master {origin_master[:10]} (a deploy is pending, or master moved)"}
    return {"name": "release_pinned", "status": OK, "detail": f"release equals origin/master at {release_head[:10]}"}


def check_age(name: str, stamp: str | None, limit_min: float, now: dt.datetime, what: str) -> dict:
    age = _age_min(stamp, now)
    if age is None:
        return {"name": name, "status": FAIL, "detail": f"{what}: no success on record"}
    if age > limit_min:
        return {"name": name, "status": FAIL, "detail": f"{what}: last success {age:.0f} min ago (limit {limit_min:.0f})"}
    return {"name": name, "status": OK, "detail": f"{what}: last success {age:.0f} min ago (limit {limit_min:.0f})"}


def check_database() -> dict:
    try:
        import db
        from operational import paper_bankroll as pb
        path = Path(db.resolve_db_path())
        if not path.exists() or path.stat().st_size == 0:
            return {"name": "database_path", "status": FAIL, "detail": "the resolved NHL database is missing or empty"}
        import sqlite3
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            games = c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        finally:
            c.close()
        if not Path(pb.DB_PATH).exists():
            return {"name": "database_path", "status": FAIL, "detail": "the paper ledger file is missing"}
        return {"name": "database_path", "status": OK, "detail": f"{path.name} has {games} games; the paper ledger exists"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "database_path", "status": FAIL, "detail": f"{type(exc).__name__}: {exc}"[:200]}


SETTLEMENT_WARN_H, SETTLEMENT_FAIL_H = 6.0, 12.0
CORE_SOURCES = ("nhl_schedule", "nhl_results", "moneypuck_skater", "moneypuck_goalie")


def _independent_model_book(ledger) -> dict:
    """The ACTIVE experiment's book re-derived from raw rows, by a route that does not use the account helper: its own rows (by experiment_id) and its own starting bankroll."""
    from operational import paper_bankroll as pb
    clause, params = pb.experiment_filter(ledger)
    active = pb.active_experiment(ledger)
    start = float(active["starting_bankroll"]) if active else 500.0
    rows = [dict(r) for r in ledger.execute("SELECT * FROM paper_bets WHERE track='REAL_MARKET_PAPER' AND origin='AUTOMATIC'" + clause, params)]
    settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "VOID")]
    open_ = [r for r in rows if r["result_status"] in ("PENDING", "UNRESOLVED")]
    pnl = round(sum(r["profit_loss"] or 0 for r in settled), 2)
    staked = round(sum(r["stake"] for r in rows), 2)
    returned = round(sum(r["stake"] + (r["profit_loss"] or 0) for r in settled if r["result_status"] != "LOSS"), 2)
    return {"cash_by_pnl": round(start + pnl - sum(r["stake"] for r in open_), 2), "cash_by_flows": round(start - staked + returned, 2), "settled_pnl": pnl,
            "open_stakes": round(sum(r["stake"] for r in open_), 2), "tickets": len(rows)}


def check_reconciliation(ledger) -> dict:
    """The model book re-derived from raw rows and compared with the account helper and its split. FAIL on any disagreement. (Personal accounts were removed on 2026-10-14; their
    database is an inactive archive and is not part of any check.)"""
    from operational import paper_bankroll as pb
    problems = []
    exps = pb.experiments(ledger)
    if exps:
        actives = [e for e in exps if e["status"] == "ACTIVE"]
        if len(actives) != 1:
            problems.append(f"{len(actives)} ACTIVE experiments (exactly one is required)")
        strays = ledger.execute("SELECT COUNT(*) c FROM paper_bets WHERE experiment_id NOT IN (SELECT experiment_id FROM paper_experiments)").fetchone()["c"]
        if strays:
            problems.append(f"{strays} ticket(s) belong to an unknown experiment")
    ind = _independent_model_book(ledger)
    helper = pb.account_state(ledger, "REAL_MARKET_PAPER")
    for k, ik in (("available_cash", "cash_by_pnl"), ("settled_pnl", "settled_pnl"), ("open_stakes", "open_stakes"), ("tickets", "tickets")):
        if helper[k] != ind[ik]:
            problems.append(f"model {k}: account {helper[k]} != re-derived {ind[ik]}")
    if ind["cash_by_flows"] != ind["cash_by_pnl"]:
        problems.append(f"model cash by stake/return flows {ind['cash_by_flows']} != cash by P&L {ind['cash_by_pnl']}")
    split = pb.book_breakdown(ledger, "REAL_MARKET_PAPER")["reconciliation"]            # the parlay experiment + the earlier single bets must add up to the whole account
    if not split["all_agree"]:
        problems.append("model account split does not add up: " + ", ".join(k for k, v in split["checks"].items() if not v))
    if problems:
        return {"name": "reconciliation", "status": FAIL, "detail": "; ".join(problems)[:300]}
    return {"name": "reconciliation", "status": OK, "detail": f"model book ({(helper.get('experiment') or {}).get('label', 'experiment')}) ${helper['available_cash']:.2f} agrees three ways and splits into parlay tickets ({split['parlay_pnl']:+.2f}) and single bets ({split['single_bets_pnl']:+.2f})"}


def check_settlement_backlog(ledger, now: dt.datetime) -> dict:
    def stuck(rows):
        out = []
        for r in rows:
            t = (r["event_start_utc"] or "")
            if not t:
                continue
            started = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
            started = started if started.tzinfo else started.replace(tzinfo=dt.timezone.utc)
            out.append((now - started).total_seconds() / 3600.0)
        return out
    model = stuck(ledger.execute("SELECT event_start_utc FROM paper_bets WHERE track='REAL_MARKET_PAPER' AND origin='AUTOMATIC' AND result_status IN ('PENDING','UNRESOLVED')").fetchall())
    late = [h for h in model if h > SETTLEMENT_WARN_H]
    worst = max(late, default=0.0)
    detail = f"{len([h for h in model if h > 0])} model bet(s) started and still open; {len(late)} open more than {SETTLEMENT_WARN_H:.0f} h after puck drop"
    if worst > SETTLEMENT_FAIL_H:
        return {"name": "settlement_backlog", "status": FAIL, "detail": detail + f" (oldest {worst:.0f} h)"}
    if late:
        return {"name": "settlement_backlog", "status": WARN, "detail": detail + f" (oldest {worst:.0f} h)"}
    return {"name": "settlement_backlog", "status": OK, "detail": detail}


def check_sources(now: dt.datetime, evaluated: dict | None = None) -> dict:
    """The sources the engine itself depends on, against their own freshness policies (operational/source_status.py). Disabled, budget-limited and
    not-due sources are readiness matters, not operational failures."""
    from operational import source_status as ss
    try:
        view = evaluated or ss.evaluate(ss.build(now), now)
    except Exception as exc:  # noqa: BLE001
        return {"name": "source_freshness", "status": WARN, "detail": f"could not evaluate sources: {type(exc).__name__}"}
    rows = {r["key"]: r for r in view["rows"]}
    bad = [(k, rows[k]) for k in CORE_SOURCES if k in rows and rows[k]["state"] in (ss.STALE, ss.UNAVAILABLE)]
    if not bad:
        return {"name": "source_freshness", "status": OK, "detail": "core sources inside their policies: " + ", ".join(f"{k}={rows[k]['state'].lower()}" for k in CORE_SOURCES if k in rows)}
    return {"name": "source_freshness", "status": FAIL if any(r["state"] == ss.UNAVAILABLE for _, r in bad) else WARN,
            "detail": "; ".join(f"{k} {r['state'].lower()} ({ss._fmt_age(r['age_min']) if r.get('age_min') is not None else 'no timestamp'}, policy {ss._fmt_age(r['limit_min']) if r.get('limit_min') else '—'})" for k, r in bad)}


BOARD_LOG = "board_evidence.jsonl"


def _log_board_evidence(options: int, tickets: int, built: str | None, now: dt.datetime) -> bool:
    """Appends a line when the published board's option count changes (persistent evidence that cards were populated, and when).
    Returns True when the board has just gone from empty to populated; the watchdog then sends one macOS notification, so the moment prices
    arrive does not depend on anyone (or any session) watching."""
    p = state_paths.path(BOARD_LOG)
    try:
        last = json.loads(p.read_text().splitlines()[-1]) if p.exists() and p.read_text().strip() else None
    except (OSError, json.JSONDecodeError, IndexError):
        last = None
    if last is None or last.get("options") != options or last.get("tickets") != tickets:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "options": options, "tickets": tickets, "options_built_utc": built}, sort_keys=True) + "\n")
        if options > 0 and not (last or {}).get("options"):
            try:
                from operational import notify as _n
                _n.send("NHL board populated", f"{options} option card(s) are now published - time to check the hosted Best Options page.")
            except Exception:  # noqa: BLE001
                pass
            return True
    return False


def _first_board_evidence() -> str | None:
    p = state_paths.path(BOARD_LOG)
    try:
        for line in p.read_text().splitlines():
            row = json.loads(line)
            if row.get("options"):
                return row["at_utc"]
    except (OSError, json.JSONDecodeError):
        return None
    return None


def readiness(now: dt.datetime, evaluated: dict | None = None) -> dict:
    """Which user-facing features work end to end. Statuses: WORKING, LIMITED, NOT_VERIFIED, BLOCKED, OWNER_ACTION. Independent of operational health."""
    feats = []

    def add(feature, status, detail, action=None):
        feats.append({"feature": feature, "status": status, "detail": detail, "owner_action": action})

    df = json.loads(state_paths.path("dailyfaceoff_state.json").read_text()) if state_paths.path("dailyfaceoff_state.json").exists() else {}
    # Is tonight's board actually populated? Judged from the engine's own published ticket board and logged persistently, so the evidence does not depend on anyone watching.
    try:
        tk = json.loads(state_paths.path("tickets_state.json").read_text())
        opts = (tk.get("options") or {}).get("options") or []
        built = (tk.get("options") or {}).get("generated_at_utc")
        _log_board_evidence(len(opts), len(tk.get("tickets") or []), built, now)
        if opts:
            add("Best Options board (populated option cards)", "WORKING", f"{len(opts)} option(s) published (built {built}); first-ever populated board: {_first_board_evidence() or 'now'}")
        else:
            add("Best Options board (populated option cards)", "LIMITED", "no option is published yet: the morning look has not produced one (prices not posted, or not covered by the credit plan) and no pregame price has arrived",
                "None — it fills by itself when the day's captures arrive")
    except Exception:  # noqa: BLE001
        pass
    # the morning workflow and the refresh cadence: stated plainly, and judged only on what the engine has actually recorded
    ev = [e for e in morning_evidence() if e.get("state") == "DONE" and e.get("analysis_due_utc")]
    on_time = [e for e in ev if (dt.datetime.fromisoformat(e["analysis_published_utc"].replace("Z", "+00:00")) - dt.datetime.fromisoformat(e["analysis_due_utc"].replace("Z", "+00:00"))).total_seconds() <= 45 * 60]
    if on_time:
        add("Morning preparation (07:45 refresh, 08:00 whole-slate analysis, league pull)", "WORKING",
            f"observed on {on_time[-1]['day']}: refresh finished {on_time[-1].get('refresh_finished_utc')}, analysis of {on_time[-1].get('analysis_games')} of {on_time[-1].get('slate_games')} games published "
            f"{on_time[-1]['analysis_published_utc']}")
    else:
        add("Morning preparation (07:45 refresh, 08:00 whole-slate analysis, league pull)", "NOT_VERIFIED",
            "built and tested, but no day on record shows the analysis published within 45 minutes of its due time; this requirement is UNMET until one does",
            "None: the first scheduled morning after the release proves it")
    add("Coordinated player-odds captures by start-time wave", "LIMITED",
        "ONE capture per selected game (shots ladder, anytime goal, goalie saves) 90 minutes before its wave, within the existing 500-credit allowance: about four games a day on average at 3 credits each; the "
        "games the budget excludes are named on the Slate Board with analysis but no prices", "Budget decision only if more coverage is wanted: the 20K tier ($30/month, not purchased)")
    add("Automatic starting-goalie confirmation", "BLOCKED", df.get("disabled_reason") or "no permitted automatic source is connected",
        "Grant or choose a permitted source (docs/STARTING_GOALIE_SOURCE_AUDIT.md); until then confirmations are recorded by hand")
    add("Reported lines and power-play units", "BLOCKED", "same source; the app shows only the inferred estimate, labelled as such", "same as above")
    add("Goalie-saves tickets", "BLOCKED", "a saves leg needs a confirmed starter, and none can be confirmed automatically", "same as above")
    add("Puck-line / spread selection", "BLOCKED", "the provider contract is uncertified (one real payload, 1 credit) and the model is unvalidated; kept out of selection",
        "Authorise one credit: python3 deploy/capture_puck_line_contract.py --confirm-spend-1-credit")
    try:
        from operational import source_status as ss
        view = evaluated or ss.evaluate(ss.build(now), now)
        rows = {r["key"]: r for r in view["rows"]}
        for key, label in (("odds_props", "Player-prop prices"), ("odds_moneyline", "Moneyline prices")):
            r = rows.get(key)
            if r:
                status = "LIMITED" if r["state"] in (ss.BUDGET_LIMITED, ss.STALE, ss.NOT_DUE) else "WORKING"
                add(f"{label}: coverage and refresh", status, f"{r['state'].replace('_', ' ').title()} — " + (r.get("reason") or "inside its policy")
                    + (" (the free credit allowance prices part of the slate)" if status == "LIMITED" else ""),
                    "Budget decision: stay on the free plan, switch to points only, or buy the 20K tier (docs/ODDS_BUDGET_CONFIGURATIONS.md)" if status == "LIMITED" else None)
    except Exception:  # noqa: BLE001
        pass
    add("Moneyline strength model", "LIMITED", "Elo prices tickets; the strength model is shadow-scored and not promoted until the evidence gate is met")
    add("Odds API key rotation", "OWNER_ACTION", "the exposed key stays a liability until the owner rotates it; the watchdog cannot verify this", "docs/CREDENTIAL_ROTATION.md")
    counts = {k: sum(1 for f in feats if f["status"] == k) for k in ("WORKING", "LIMITED", "NOT_VERIFIED", "BLOCKED", "OWNER_ACTION")}
    return {"features": feats, "counts": counts,
            "note": "Operational health OK means the machinery runs and the books agree. It does not mean a blocked, limited or unverified feature works."}


MORNING_EVIDENCE = "morning_update_evidence.jsonl"
MORNING_WARN_MIN, MORNING_FAIL_MIN = 45.0, 120.0


def check_morning_update(now: dt.datetime) -> dict:
    """The day's preparation must happen on its own: the 07:45 ET refresh (earlier when the first game is early), the whole-slate analysis published after 08:00 ET, and the league-wide moneyline +
    spread pull. Judged from the published day status (operational/day_status.py): WARN when something is more than 45 minutes late, FAIL at two hours. Each complete day is appended to a persistent
    evidence file, so 'it ran every morning' does not depend on anyone watching."""
    from operational import day_status as ds_mod
    try:
        ds = ds_mod.build(now)
    except Exception as exc:  # noqa: BLE001
        return {"name": "morning_update", "status": WARN, "detail": f"could not evaluate today's preparation: {type(exc).__name__}"}
    if not ds.get("plan_present"):
        from operational import capture_timeline as ct, morning_refresh
        try:
            if morning_refresh.first_start_today(now) is None:
                return {"name": "morning_update", "status": OK, "detail": "no games are scheduled today: there is no plan to make"}
        except Exception:  # noqa: BLE001 - without the schedule, judge by the clock below
            pass
        due = ct.prep_times(ct.et_date(now), None)["analysis_at"]
        late = (now - due).total_seconds() / 60.0
        if late < MORNING_WARN_MIN:
            return {"name": "morning_update", "status": OK, "detail": "no day plan is due yet"}
        return {"name": "morning_update", "status": FAIL if late >= MORNING_FAIL_MIN else WARN, "detail": f"no capture plan has been made today ({late:.0f} min after the 08:00 ET analysis time); the first trader cycle makes it"}
    p, lp = ds.get("prep") or {}, ds.get("league_pulls") or {}
    problems, worst = [], 0.0
    def late_min(due):
        return None if not due else (now - dt.datetime.fromisoformat(due.replace("Z", "+00:00"))).total_seconds() / 60.0
    r_late = late_min(p.get("refresh_due_utc"))
    if r_late is not None and r_late >= MORNING_WARN_MIN and p.get("refresh_status") not in ("DONE", "PARTIAL"):
        problems.append(f"the refresh (due {p['refresh_due_utc']}) has not completed ({p.get('refresh_status')})")
        worst = max(worst, r_late)
    a_late = late_min(p.get("analysis_due_utc"))
    published = p.get("analysis_published_utc")
    if a_late is not None and a_late >= MORNING_WARN_MIN and not (published and published >= p["analysis_due_utc"]):
        problems.append(f"the whole-slate analysis (due {p['analysis_due_utc']}) has not been published")
        worst = max(worst, a_late)
    if a_late is not None and a_late >= MORNING_WARN_MIN and lp.get("planned") and not lp.get("made"):
        problems.append("the league-wide moneyline + spread pull has not been made (check credits, the connection and the deferral reason)")
        worst = max(worst, a_late * 0.0 + MORNING_WARN_MIN)
    if problems:
        return {"name": "morning_update", "status": FAIL if worst >= MORNING_FAIL_MIN else WARN, "detail": "; ".join(problems)}
    if a_late is not None and a_late >= 0 and p.get("refresh_status") in ("DONE", "PARTIAL") and published:
        _log_morning_evidence({"day": ds["day"], "state": "DONE", "refresh_finished_utc": p.get("refresh_finished_utc"), "analysis_published_utc": published,
                               "analysis_games": p.get("analysis_games"), "slate_games": p.get("slate_games"), "league_pulls_made": len(lp.get("made") or []),
                               "analysis_due_utc": p.get("analysis_due_utc"), "first_look_utc": published, "starts_at_utc": p.get("analysis_due_utc"),
                               "looked": p.get("analysis_games"), "total": p.get("slate_games")}, now)
        return {"name": "morning_update", "status": OK, "detail": f"preparation done: refresh {p.get('refresh_status', '').lower()} ({p.get('refresh_finished_utc')}); analysis of {p.get('analysis_games')} of "
                                                                f"{p.get('slate_games')} games published {published}; {len(lp.get('made') or [])} league pull(s) made"}
    return {"name": "morning_update", "status": OK, "detail": "today's preparation is not due yet"}


def _log_morning_evidence(m: dict, now: dt.datetime) -> None:
    """One line per day once the preparation is complete (appended again only when the counts change)."""
    try:
        p = state_paths.path(MORNING_EVIDENCE)
        last = None
        if p.exists():
            rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
            last = next((r for r in reversed(rows) if r.get("day") == m["day"]), None)
        key = (m["state"], m.get("analysis_games"), m.get("league_pulls_made"))
        if last and (last.get("state"), last.get("analysis_games"), last.get("league_pulls_made")) == key:
            return
        with open(p, "a") as f:
            f.write(json.dumps({**m, "logged_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    except Exception:  # noqa: BLE001 - evidence logging must never break a check
        pass


def morning_evidence() -> list[dict]:
    try:
        return [json.loads(x) for x in state_paths.path(MORNING_EVIDENCE).read_text().splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return []


PAUSE_REMINDER_DAYS = 3


def check_recording_pause(now: dt.datetime) -> dict:
    """An owner pause on NEW automatic tickets (operational/recording_pause.py) is intended, so it is not a failure; it is shown, and it WARNs once it has lasted PAUSE_REMINDER_DAYS so a
    pause cannot be forgotten. An unreadable pause file fails safe (paused) and WARNs at once."""
    from operational import recording_pause as rp
    st = rp.status()
    if not st["paused"]:
        return {"name": "recording_pause", "status": OK, "detail": "automatic recording is on"}
    since = st.get("since_utc")
    try:
        days = (now - dt.datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return {"name": "recording_pause", "status": WARN, "detail": f"automatic recording is paused and the pause file has no readable start time ({st.get('reason') or 'no reason'})"}
    detail = f"automatic recording is PAUSED by the owner since {since} ({days:.1f} days): {st.get('reason') or ''}".strip()
    return {"name": "recording_pause", "status": WARN if days >= PAUSE_REMINDER_DAYS else OK, "detail": detail[:300]}


def check_ticket_policy() -> dict:
    """The selector's policy must load: if it cannot, the trader blocks recording (it never falls back to the superseded five tickets), and this says so."""
    try:
        from operational import recording_pause, ticket_policy
        pol = ticket_policy.checked_active()
        approved = ticket_policy.is_approved(pol)
        unapproved_and_live = (not approved) and not recording_pause.status()["paused"]
        return {"name": "ticket_policy", "status": WARN if unapproved_and_live else OK,
                "detail": (f"{pol.policy_id} ({pol.digest()}), {pol.slots} slot(s) a day, {'approved' if approved else 'NOT approved by the owner'}"
                           + ("; automatic recording is ON with an unapproved policy" if unapproved_and_live else ""))}
    except Exception as exc:  # noqa: BLE001
        return {"name": "ticket_policy", "status": FAIL, "detail": f"the ticket policy cannot be loaded; automatic recording is blocked: {exc.__class__.__name__}: {exc}"[:300]}


PREDICTION_LOG_MAX_AGE_MIN = 60.0


def check_prediction_log(now: dt.datetime) -> dict:
    """The frozen-prediction log (operational/shadow_selection.py) must be looked at on every trader cycle, recording or paused: it is where the entry estimates are kept before the games. It stamps
    every look, so a quiet pool is told apart from a logger that stopped (as it did when recording resumed on 2026-10-11)."""
    from operational import shadow_selection, state_paths
    try:
        st = json.loads(state_paths.path(shadow_selection.STATE).read_text())
    except (OSError, json.JSONDecodeError):
        return {"name": "prediction_log", "status": WARN, "detail": "the frozen-prediction log has not been written yet"}
    checked = st.get("checked_at_utc") or st.get("written_at_utc")
    age = _age_min(checked, now) if checked else None
    if age is None or age > PREDICTION_LOG_MAX_AGE_MIN:
        return {"name": "prediction_log", "status": WARN, "detail": f"the frozen-prediction log was last looked at {checked or 'never'} (limit {PREDICTION_LOG_MAX_AGE_MIN:.0f} min): predictions would not be frozen before games"}
    return {"name": "prediction_log", "status": OK, "detail": f"the frozen-prediction log was checked {age:.0f} min ago (last change {st.get('written_at_utc') or 'n/a'})"}


def _job_definitions() -> dict[str, dict]:
    """{label: plist} for the scheduled jobs the watchdog expects (the repository copies, which match the installed ones)."""
    import glob
    import plistlib
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(REPO_ROOT / "deploy" / "launchd" / "*.plist"))) + sorted(glob.glob(str(REPO_ROOT / "operational" / "*.plist"))):
        try:
            d = plistlib.loads(Path(f).read_bytes())
        except Exception:  # noqa: BLE001 - an unreadable definition is reported by tests, not here
            continue
        if d.get("Label") in EXPECTED_JOBS:
            out.setdefault(d["Label"], d)
    return out


# What each scheduled job PRODUCES: the ingestion-health component it records on success, and how old that success may be. Log activity alone does not prove success: a job can run every cycle and
# fail every cycle, or run and produce nothing.
JOB_OUTPUTS = {
    "com.nhlengine.real-parlay-paper-trader": ("real_parlay_paper_trader", 45.0), "com.nhlengine.daily-nhl-sync": ("nhl_sync_full", 26 * 60.0),
    "com.nhlengine.morning-refresh": ("morning_refresh", 26 * 60.0), "com.nhlengine.midday-schedule-refresh": ("nhl_midday_schedule_refresh", 26 * 60.0),
    "com.nhlengine.pregame-targeted-refresh": ("nhl_pregame_targeted_refresh", 3 * 60.0), "com.nhlengine.daily-settlement": ("settlement", 26 * 60.0),
    "com.nhlengine.daily-postmortem": ("postmortem", 26 * 60.0), "com.nhlengine.database-backup": ("database_backups", 26 * 60.0),
    "com.nhlengine.moneypuck-refresh": ("moneypuck_afternoon_refresh", 26 * 60.0),
}
RUNNING, FAILED_STATE, OVERDUE, STALE_OUTPUT, JOB_OK = "RUNNING", "FAILED", "OVERDUE", "STALE_OUTPUT", "OK"


def job_states(now: dt.datetime, *, launchctl_text: str = "", health: dict | None = None, definitions: dict | None = None, mtime=None) -> list[dict]:
    """One row per scheduled job with a state that says what is actually true:
      RUNNING       a process of the job is alive now (launchctl shows a PID);
      FAILED        its last attempt failed (ingestion health says so, or launchctl's last exit status is non-zero) and has not been followed by a success;
      OVERDUE       it has not started within its own schedule (three intervals, at least 20 minutes; 26 hours for a calendar job), judged from its log's modification time;
      STALE_OUTPUT  it started recently but its last SUCCESSFUL output is older than the job's output limit (it runs and produces nothing valid);
      OK            started on schedule and its output is fresh.
    Log activity alone is never taken as success."""
    definitions = definitions if definitions is not None else _job_definitions()
    mtime = mtime or (lambda path: Path(path).stat().st_mtime)
    health = health if health is not None else {}
    live = {}
    for line in launchctl_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].startswith("com.nhlengine."):
            live[parts[2]] = (parts[0], parts[1])                   # (pid or '-', last exit status)
    rows = []
    for label, d in sorted(definitions.items()):
        if label == "com.nhlengine.watchdog":
            continue
        short = label.removeprefix("com.nhlengine.")
        limit_min = max(3 * int(d["StartInterval"]) / 60.0, 20.0) if d.get("StartInterval") else 26 * 60.0
        log = d.get("StandardOutPath")
        age_min = None
        if log:
            try:
                age_min = (now.timestamp() - mtime(log)) / 60.0
            except OSError:
                age_min = None
        pid, exit_status = live.get(label, ("-", "0"))
        comp, out_limit = JOB_OUTPUTS.get(label, (None, None))
        row = health.get(comp) or {} if comp else {}
        last_ok = row.get("last_success_utc")
        last_try = row.get("last_attempt_utc")
        def _age(stamp):
            return None if not stamp else (now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 60.0
        ok_age, try_age = _age(last_ok), _age(last_try)
        failed = bool(comp and row.get("last_status") in ("FAILED", "FAIL", "HALT") and (last_ok is None or (last_try and last_try > last_ok))) or (exit_status not in ("0", "-") and pid == "-")
        if pid not in ("-", ""):
            state, why = RUNNING, f"a process is alive (pid {pid})"
        elif failed:
            state, why = FAILED_STATE, (f"last attempt failed ({row.get('last_status')}: {str(row.get('last_detail') or '')[:80]})" if comp and row.get("last_status") in ("FAILED", "FAIL", "HALT") else f"launchd last exit status {exit_status}")
        elif age_min is None or age_min > limit_min:
            state, why = OVERDUE, ("no log at all" if age_min is None else f"last started {age_min / 60.0:.1f} h ago (limit {limit_min / 60.0:.1f} h)")
        elif comp and (ok_age is None or ok_age > out_limit):
            state, why = STALE_OUTPUT, ("it has never recorded a success" if ok_age is None else f"last successful output {ok_age / 60.0:.1f} h ago (limit {out_limit / 60.0:.1f} h)")
        else:
            state, why = JOB_OK, "started on schedule" + (f"; last success {ok_age:.0f} min ago" if ok_age is not None else "")
        rows.append({"job": short, "state": state, "detail": why, "log_age_min": None if age_min is None else round(age_min, 1), "last_success_age_min": None if ok_age is None else round(ok_age, 1)})
    return rows


def check_jobs_ran(now: dt.datetime, *, definitions: dict | None = None, mtime=None, launchctl_text: str | None = None, health: dict | None = None) -> dict:
    """A loaded scheduler proves nothing, and neither does a log that was written to. Each expected job is classified RUNNING / FAILED / OVERDUE / STALE_OUTPUT / OK (see job_states) from launchctl, its log
    and its recorded output. WARN when any job is FAILED, OVERDUE or STALE_OUTPUT (a sleeping Mac produces OVERDUE, and missed_windows says so); the detail names each with its reason."""
    definitions = definitions if definitions is not None else _job_definitions()
    if launchctl_text is None:
        try:
            launchctl_text = _run(["launchctl", "list"])
        except Exception:  # noqa: BLE001
            launchctl_text = ""
    if health is None:
        try:
            from operational import ingestion_health
            health = ingestion_health.load_health()
        except Exception:  # noqa: BLE001
            health = {}
    rows = job_states(now, launchctl_text=launchctl_text, health=health, definitions=definitions, mtime=mtime)
    expected = len(EXPECTED_JOBS) - (1 if "com.nhlengine.watchdog" in EXPECTED_JOBS else 0)
    if len(rows) < expected:
        return {"name": "jobs_ran", "status": WARN, "detail": f"only {len(rows)} of {expected} expected jobs could be checked (a plist is missing or unreadable, or no log path is defined): the rest are unverified", "jobs": rows}
    bad = [r for r in rows if r["state"] in (FAILED_STATE, OVERDUE, STALE_OUTPUT)]
    if bad:
        return {"name": "jobs_ran", "status": WARN, "jobs": rows,
                "detail": f"{len(rows)} job(s) checked; " + "; ".join(f"{r['job']} {r['state']} ({r['detail']})" for r in bad) + ". A job that is loaded but has not run, or has run without producing valid output, is not working."}
    running = [r["job"] for r in rows if r["state"] == RUNNING]
    return {"name": "jobs_ran", "status": OK, "jobs": rows,
            "detail": f"all {len(rows)} scheduled jobs started on schedule and produced timely output" + (f" ({len(running)} running now)" if running else "")}


def check_power(now: dt.datetime, *, power: dict | None = None, windows: list | None = None) -> dict:
    """Can the Mac actually be awake for the next windows (the 07:45/08:00 prep and each wave capture)? On battery it cannot be guaranteed: WARN with the exact owner actions, FAIL when the charge is
    low enough that it may shut down before the next window. On AC the engine's assertion bridges overnight; only an unreadable power state or no upcoming window is not a problem."""
    from operational import keep_awake as ka
    try:
        power = power or ka.battery_state()
        if windows is None:
            from operational import moneyline_pregame as mp
            windows = ka.windows(now, mp.scheduled_starts(now, hours=ka.PLAN_HORIZON_H, back_hours=1), horizon_h=24)
    except Exception as exc:  # noqa: BLE001
        return {"name": "power", "status": WARN, "detail": f"could not read the power state: {type(exc).__name__}"}
    if not windows:
        return {"name": "power", "status": OK, "detail": "no capture window in the next 24 hours"}
    nxt = min(windows, key=lambda w: w["hold_from_utc"])
    when = nxt["hold_from_utc"].astimezone().strftime("%a %H:%M")
    if power.get("on_ac", True):
        gap_h = (nxt["assert_from_utc"] - now).total_seconds() / 3600.0
        if gap_h > ka.AC_BRIDGE_H:
            return {"name": "power", "status": WARN, "detail": f"on AC power, but the next window ({nxt['cluster']}) opens {when}, {gap_h:.0f} h away: further than the {ka.AC_BRIDGE_H} h the engine holds the Mac awake across, so it may sleep before then (an owner `pmset` wake event would cover it)."}
        return {"name": "power", "status": OK, "detail": f"on AC power; the next window ({nxt['cluster']}) opens {when} and an idle-sleep assertion bridges to it. The Mac must stay logged in and online with the lid open."}
    pct = power.get("percent")
    steps = "plug in the charger, keep the lid open, and (optional, needs sudo) `sudo pmset -c sleep 0`; `sudo pmset repeat wakeorpoweron MTWRFSU 07:40:00`"
    if pct is not None and pct < ka.BATTERY_FLOOR_PCT:
        return {"name": "power", "status": FAIL, "detail": f"on battery at {pct}%: the Mac may shut down before the next window ({nxt['cluster']}, {when}) and no capture can be made while it sleeps. Owner action: {steps}."}
    return {"name": "power", "status": WARN, "detail": f"on battery ({pct}%): the next window ({nxt['cluster']}, {when}) cannot be guaranteed; the Mac idle-sleeps after 1 minute unless an assertion holds it. Owner action: {steps}."}


def check_missed_windows(now: dt.datetime) -> dict:
    """Planned wave capture windows that CLOSED without their capture in the last 24 hours (operational/price_availability.py). A missed window is reported, never bought late and never
    backfilled. It is attributed to machine sleep only when the operating system's power log shows sleep inside the window (MACHINE_ASLEEP); with no sleep evidence and no trader run in the window
    it is an observed engine outage (ENGINE_OUTAGE_OBSERVED); otherwise it is unattributed (the engine ran and made no capture)."""
    from operational import eastern_time as et, price_availability as pa, quote_freshness as qf
    cutoff = now - dt.timedelta(hours=24)
    n = asleep = outage = 0
    for day in {et.eastern_today(now), et.eastern_today(cutoff)}:
        for m in pa.missed_windows(day):
            t = qf.parse_utc(m.get("closed_utc"))
            if t is not None and cutoff <= t <= now:
                n += 1
                asleep += int("MACHINE_ASLEEP" in (m.get("tags") or []))
                outage += int("ENGINE_OUTAGE_OBSERVED" in (m.get("tags") or []))
    if not n:
        return {"name": "missed_windows", "status": OK, "detail": "no planned capture window closed without its capture in the last 24 hours"}
    advice = (" Keep the Mac plugged in with the lid open from 7:50 AM to the last puck drop, or move the engine to an always-on host." if asleep else
              " The engine itself was down or blocked in those windows: see the job states." if outage else "")
    return {"name": "missed_windows", "status": WARN,
            "detail": f"{n} planned capture window(s) closed without a capture in the last 24 h ({asleep} with sleep evidence, {outage} engine outage(s) observed, {n - asleep - outage} unattributed). "
                      "They are not bought late: a price taken after the window is not a pregame price." + advice}


def check_publishing_enabled() -> dict:
    """Publication to the hosted app must be ON. A demonstration that turned it off and was never restored is a FAIL, not a quiet gap."""
    try:
        from operational import publish_cloud_snapshot as pcs
        on = bool(pcs.publishing_enabled())
    except Exception as exc:  # noqa: BLE001
        return {"name": "publishing_enabled", "status": FAIL, "detail": f"could not read the publishing switch: {type(exc).__name__}"}
    return {"name": "publishing_enabled", "status": OK if on else FAIL,
            "detail": "NHL_ENGINE_CLOUD_PUBLISH is ON" if on else "publication to the hosted app is switched OFF (NHL_ENGINE_CLOUD_PUBLISH); the hosted app will go stale"}


def check_quotes(now: dt.datetime, evaluated: dict | None = None) -> dict:
    """Price freshness for what is about to be bet on: the prop and moneyline prices judged by their own freshness rules. A price that is stale only because the
    odds-credit budget forbids a refresh is NOT an operational failure (it is the LIMITED line on product readiness); one that is stale while a refresh was
    allowed means the capture machinery is not doing its job."""
    from operational import source_status as ss
    try:
        view = evaluated or ss.evaluate(ss.build(now), now)
    except Exception as exc:  # noqa: BLE001
        return {"name": "quote_freshness", "status": WARN, "detail": f"could not evaluate quotes: {type(exc).__name__}"}
    rows = {r["key"]: r for r in view["rows"]}
    bad = [(k, rows[k]) for k in ("odds_props", "odds_moneyline") if k in rows and rows[k]["state"] == ss.STALE]
    budget = [k for k in ("odds_props", "odds_moneyline") if k in rows and rows[k]["state"] == ss.BUDGET_LIMITED]
    if bad:
        return {"name": "quote_freshness", "status": WARN, "detail": "; ".join(f"{k} stale ({ss._fmt_age(r['age_min'])} old, policy {ss._fmt_age(r['limit_min']) if r.get('limit_min') else 'n/a'}) although a refresh was allowed" for k, r in bad)}
    return {"name": "quote_freshness", "status": OK, "detail": ("quotes inside their rules" if not budget else
                                                                f"{', '.join(budget)} past their freshness limit only because the credit budget forbids a refresh (product readiness: LIMITED)")}


def run(now: dt.datetime | None = None, *, runner=_run, notify=True, deep=True) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    checks = []
    try:
        checks.append(check_jobs(runner(["launchctl", "list"])))
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "jobs_loaded", "status": FAIL, "detail": f"launchctl failed: {exc}"})
    try:
        head = runner(["git", "rev-parse", "HEAD"], RELEASE_DIR).strip()
        dirty = bool(runner(["git", "status", "--porcelain", "--untracked-files=no"], RELEASE_DIR).strip())
    except Exception:  # noqa: BLE001
        head, dirty = None, False
    try:
        origin = runner(["git", "ls-remote", "origin", "refs/heads/master"], REPO_ROOT).split()[0]
    except Exception:  # noqa: BLE001
        origin = None
    checks.append(check_release(head, dirty, origin))
    try:
        from operational import ingestion_health
        health = ingestion_health.load_health()
    except Exception:  # noqa: BLE001
        health = {}
    checks.append(check_age("trader_recent", (health.get("real_parlay_paper_trader") or {}).get("last_success_utc"), TRADER_MAX_AGE_MIN, now, "the 15-minute trader"))
    checks.append(check_age("publish_recent", (health.get("cloud_snapshot_publish") or {}).get("last_success_utc"), PUBLISH_MAX_AGE_MIN, now, "the hosted snapshot publication"))
    checks.append(check_database())
    checks.append(check_publishing_enabled())
    checks.append(check_recording_pause(now))
    checks.append(check_ticket_policy())
    checks.append(check_prediction_log(now))
    checks.append(check_morning_update(now))
    for fn in (check_jobs_ran, check_missed_windows, check_power):
        try:
            checks.append(fn(now))
        except Exception as exc:  # noqa: BLE001 - one broken check must not stop the watchdog from writing its state
            checks.append({"name": fn.__name__.removeprefix("check_"), "status": WARN, "detail": f"the check itself failed: {exc.__class__.__name__}: {exc}"})
    ready = None
    if deep:
        try:
            import sqlite3
            from operational import paper_bankroll as pb
            ledger = sqlite3.connect(f"file:{pb.DB_PATH}?mode=ro", uri=True)
            ledger.row_factory = sqlite3.Row
            try:
                checks.append(check_reconciliation(ledger))
                checks.append(check_settlement_backlog(ledger, now))
                checks.append(check_sources(now))
                checks.append(check_quotes(now))
                ready = readiness(now)
            finally:
                ledger.close()
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "reconciliation", "status": FAIL, "detail": f"could not run the book checks: {type(exc).__name__}: {exc}"[:200]})
    worst = FAIL if any(c["status"] == FAIL for c in checks) else WARN if any(c["status"] == WARN for c in checks) else OK
    prior = load_state()
    state = {"checked_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": worst, "checks": checks,
             "interval_min": 30, "release_commit": head, "origin_master_commit": origin, "readiness": ready}
    p = state_paths.path(STATE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=1))
    tmp.replace(p)
    if notify and worst == FAIL and (prior or {}).get("status") != FAIL:
        failing = ", ".join(c["name"] for c in checks if c["status"] == FAIL)
        try:
            from operational import notify as _n
            _n.send("NHL engine watchdog", f"Failing: {failing}")
        except Exception:  # noqa: BLE001
            pass
    return state


def load_state() -> dict | None:
    p = state_paths.path(STATE_NAME)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def view(state: dict | None, now: dt.datetime | None = None, max_age_min: float = 75.0) -> dict:
    """What a page shows: the stored result, re-aged at view time. A watchdog that has itself stopped (no check in 75 minutes) is a FAIL of its own."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if not state:
        return {"status": "UNKNOWN", "message": "The watchdog has not run yet.", "checks": [], "age_min": None}
    age = _age_min(state["checked_at_utc"], now)
    if age is not None and age > max_age_min:
        return {"status": FAIL, "message": f"The watchdog itself has not run for {age:.0f} minutes (it should run every 30).", "checks": state["checks"], "age_min": age}
    return {"status": state["status"], "message": "", "checks": state["checks"], "age_min": age}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
