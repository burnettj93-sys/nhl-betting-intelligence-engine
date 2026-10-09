"""
Persistent self-check (launchd, every 30 minutes; deploy/launchd/com.nhlengine.watchdog.plist), in TWO separate parts:

  OPERATIONAL HEALTH -- is the unattended machinery running the code it should, on the right data, and are its books consistent?
      jobs_loaded           every scheduled job the product depends on is loaded in launchd, none last exited with an error;
      release_pinned        the release checkout is clean and equals origin/master (WARN while a deploy is pending);
      trader_recent         the 15-minute trader recorded a success in the last 45 minutes;
      publish_recent        the hosted snapshot was published in the last 60 minutes;
      database_path         the resolved NHL database is the live one and the ledger exists;
      reconciliation        the model book re-derived from raw rows (cash by P&L = cash by stake/return flows = the account helper); every personal log's
                            summary = its raw rows; no personal bet is in the model ledger; a migrated legacy ticket equals its original row;
      settlement_backlog    bets (model and personal) whose game started more than 6 hours ago and are still open: WARN, FAIL past 12 hours;
      source_freshness      the sources the engine depends on (schedule, results, player logs) are inside their own freshness policies;
      quote_freshness       prop and moneyline prices are inside their rules (a price stale only because the credit budget forbids a refresh is readiness, not failure);
      publishing_enabled    publication to the hosted app is switched ON (a demonstration that left it OFF is a FAIL).
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
    "com.nhlengine.real-parlay-paper-trader", "com.nhlengine.manual-order-job", "com.nhlengine.moneyline-pregame", "com.nhlengine.moneyline-snapshot",
    "com.nhlengine.prop-sweep-first", "com.nhlengine.prop-sweep-second", "com.nhlengine.daily-props-pull", "com.nhlengine.daily-nhl-sync",
    "com.nhlengine.midday-schedule-refresh", "com.nhlengine.pregame-targeted-refresh", "com.nhlengine.daily-settlement", "com.nhlengine.daily-postmortem",
    "com.nhlengine.database-backup", "com.nhlengine.morning-update",
)
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
    rows = [dict(r) for r in ledger.execute("SELECT * FROM paper_bets WHERE track='REAL_MARKET_PAPER' AND origin='AUTOMATIC'")]
    settled = [r for r in rows if r["result_status"] in ("WIN", "LOSS", "VOID")]
    open_ = [r for r in rows if r["result_status"] in ("PENDING", "UNRESOLVED")]
    pnl = round(sum(r["profit_loss"] or 0 for r in settled), 2)
    staked = round(sum(r["stake"] for r in rows), 2)
    returned = round(sum(r["stake"] + (r["profit_loss"] or 0) for r in settled if r["result_status"] != "LOSS"), 2)
    return {"cash_by_pnl": round(500 + pnl - sum(r["stake"] for r in open_), 2), "cash_by_flows": round(500 - staked + returned, 2), "settled_pnl": pnl,
            "open_stakes": round(sum(r["stake"] for r in open_), 2), "tickets": len(rows)}


def check_reconciliation(ledger, personal) -> dict:
    """Both books re-derived from raw rows and compared; the separation itself is checked too. FAIL on any disagreement."""
    from operational import paper_bankroll as pb
    from operational import personal_logs as pl
    problems = []
    ind = _independent_model_book(ledger)
    helper = pb.account_state(ledger, "REAL_MARKET_PAPER")
    for k, ik in (("available_cash", "cash_by_pnl"), ("settled_pnl", "settled_pnl"), ("open_stakes", "open_stakes"), ("tickets", "tickets")):
        if helper[k] != ind[ik]:
            problems.append(f"model {k}: account {helper[k]} != re-derived {ind[ik]}")
    if ind["cash_by_flows"] != ind["cash_by_pnl"]:
        problems.append(f"model cash by stake/return flows {ind['cash_by_flows']} != cash by P&L {ind['cash_by_pnl']}")
    personal_ids = {r["bet_id"] for r in personal.execute("SELECT bet_id FROM bets")}
    clash = [r["paper_bet_id"] for r in ledger.execute("SELECT paper_bet_id FROM paper_bets") if r["paper_bet_id"] in personal_ids]
    if clash:
        problems.append(f"a bet id exists in both books: {clash[:3]}")
    n_logs = 0
    for lg in personal.execute("SELECT log_hash FROM logs"):
        n_logs += 1
        bets = [dict(b) for b in personal.execute("SELECT * FROM bets WHERE log_hash = ?", (lg["log_hash"],))]
        summ = pl.summarize(bets)
        pnl = round(sum(b["profit_loss"] or 0 for b in bets if b["result_status"] in ("WIN", "LOSS", "VOID")), 2)
        if pnl != summ["settled_pnl"] or summ["bets"] != len(bets):
            problems.append(f"personal log {lg['log_hash'][:8]}: summary does not match its rows")
    for m in personal.execute("SELECT * FROM migrations"):
        src = ledger.execute("SELECT * FROM paper_bets WHERE paper_bet_id = ?", (m["source_ref"].split(":", 1)[1],)).fetchone()
        cp = personal.execute("SELECT * FROM bets WHERE bet_id = ?", (m["bet_id"],)).fetchone()
        if src is None or cp is None or (src["legs_json"], src["stake"], src["profit_loss"], src["result_status"]) != (cp["legs_json"], cp["stake"], cp["profit_loss"], cp["result_status"]):
            problems.append(f"migrated ticket {m['bet_id']} differs from its original ledger row")
    if problems:
        return {"name": "reconciliation", "status": FAIL, "detail": "; ".join(problems)[:300]}
    return {"name": "reconciliation", "status": OK, "detail": f"model book ${helper['available_cash']:.2f} agrees three ways; {n_logs} personal log(s) agree with their rows; no bet in both books"}


def check_settlement_backlog(ledger, personal, now: dt.datetime) -> dict:
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
    mine = stuck(personal.execute("SELECT event_start_utc FROM bets WHERE result_status IN ('PENDING','UNRESOLVED')").fetchall())
    late = [h for h in model + mine if h > SETTLEMENT_WARN_H]
    worst = max(late, default=0.0)
    detail = f"{len([h for h in model if h > 0])} model and {len([h for h in mine if h > 0])} personal bet(s) started and still open; {len(late)} open more than {SETTLEMENT_WARN_H:.0f} h after puck drop"
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


def readiness(now: dt.datetime, personal=None, evaluated: dict | None = None) -> dict:
    """Which user-facing features work end to end. Statuses: WORKING, LIMITED, NOT_VERIFIED, BLOCKED, OWNER_ACTION. Independent of operational health."""
    feats = []

    def add(feature, status, detail, action=None):
        feats.append({"feature": feature, "status": status, "detail": detail, "owner_action": action})

    # personal-log writes from the hosted app: proven only by an order that arrived through the app's own write path
    verified = None
    if personal is not None:
        for r in personal.execute("SELECT request_json, status, processed_at_utc FROM orders WHERE status IN ('RECORDED','CREATED') ORDER BY processed_at_utc DESC"):
            try:
                if json.loads(r["request_json"] or "{}").get("via") == "direct":
                    verified = r["processed_at_utc"]
                    break
            except json.JSONDecodeError:
                continue
    if verified:
        add("Personal logs: one-click add from the hosted app", "WORKING", f"an order sent by the app's own write path was processed (latest {verified})")
    else:
        add("Personal logs: one-click add from the hosted app", "NOT_VERIFIED", "no order has ever arrived through the app's own write credential; a link-filed or hand-filed order does not prove it",
            "Add the LOG_WRITE_TOKEN Streamlit secret (docs/PERSONAL_LOGS.md), then run the create → add → settle check")
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
    ev = [e for e in morning_evidence() if e.get("state") == "DONE"]
    scheduled = [e for e in ev if e.get("first_look_utc") and e.get("starts_at_utc") and
                 (dt.datetime.fromisoformat(e["first_look_utc"].replace("Z", "+00:00")) - dt.datetime.fromisoformat(e["starts_at_utc"].replace("Z", "+00:00"))).total_seconds() <= 45 * 60]
    if scheduled:
        add("Morning update (about 8 AM ET: games, statistics, prices, provisional picks)", "WORKING",
            f"observed on {scheduled[-1]['day']}: first look at {scheduled[-1]['first_look_utc']}, {scheduled[-1]['looked']} of {scheduled[-1]['total']} games; "
            "the hosted-app check is recorded in docs/MORNING_WORKFLOW.md")
    else:
        add("Morning update (about 8 AM ET: games, statistics, prices, provisional picks)", "NOT_VERIFIED",
            "built and tested, but no day on record shows a first look within 45 minutes of 08:00 ET; this requirement is UNMET until one does and the hosted app has been checked",
            "None — the first scheduled morning after the release proves it")
    add("Several price refreshes a day", "LIMITED",
        "NOT MET under the free 500-credit allowance: the plan buys a morning look for part of the slate and the pregame price for fewer games; it cannot also refresh every game midday. "
        "Meeting it for every game needs about 60 credits a day (1,812 a month; docs/MORNING_WORKFLOW.md)", "Budget decision: the provider's 20K tier is the smallest above the free allowance (not purchased)")
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


def check_morning_update(now: dt.datetime) -> dict:
    """The 08:00 ET update (first look at today's games and prices) must happen on its own. Late = WARN from 08:45 ET, FAIL from 10:00 ET; it is judged from the engine's own
    availability record, and each day's outcome is appended to a persistent evidence file, so 'it ran every morning' does not depend on anyone watching."""
    from operational import price_availability as pa
    try:
        m = pa.morning_status(now)
    except Exception as exc:  # noqa: BLE001
        return {"name": "morning_update", "status": WARN, "detail": f"could not evaluate the morning update: {type(exc).__name__}"}
    _log_morning_evidence(m, now)
    state = m["state"]
    if state in (pa.MORNING_NO_GAMES, pa.MORNING_NOT_YET):
        return {"name": "morning_update", "status": OK, "detail": "no morning update is due yet" if state == pa.MORNING_NOT_YET else "no games today"}
    if state == pa.MORNING_LATE:
        return {"name": "morning_update", "status": WARN, "detail": f"the first price look came late ({m['first_look_utc']}, scheduled 08:00 ET): {m['looked']} of {m['total']} games looked at since"}
    if state == pa.MORNING_BUDGET_ONLY:
        return {"name": "morning_update", "status": WARN, "detail": f"the morning update ran but could not look at any game: today's credit plan left no credits for it ({m['budget_blocked']} of {m['total']} games budget-blocked); "
                                                                   "a pregame price still follows for the games the plan covers"}
    if state == pa.MORNING_DONE:
        return {"name": "morning_update", "status": OK, "detail": f"morning update done: {m['looked']} of {m['total']} games looked at (first look {m['first_look_utc']}); "
                                                                f"prices posted for {m['posted']}, not posted yet for {m['not_posted']}, not fetched for budget for {m['budget_blocked']}"}
    from operational import capture_schedule as sched
    ten = sched._localize(dt.date.fromisoformat(m["day"]), 10, 0)
    status = FAIL if now >= ten else WARN
    return {"name": "morning_update", "status": status, "detail": f"morning update {state.lower()}: {m['looked']} of {m['total']} games looked at since 08:00 ET"}


def _log_morning_evidence(m: dict, now: dt.datetime) -> None:
    """One line per day, the last state seen once the first look has happened (appended again only when the state or counts change)."""
    try:
        if m["state"] in ("NOT_YET", "NO_GAMES"):
            return
        p = state_paths.path(MORNING_EVIDENCE)
        last = None
        if p.exists():
            rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
            last = next((r for r in reversed(rows) if r.get("day") == m["day"]), None)
        key = (m["state"], m["looked"], m["total"], m["posted"], m["not_posted"], m["budget_blocked"])
        if last and (last["state"], last["looked"], last["total"], last["posted"], last["not_posted"], last["budget_blocked"]) == key:
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
    checks.append(check_morning_update(now))
    ready = None
    if deep:
        try:
            import sqlite3
            from operational import paper_bankroll as pb
            from operational import personal_logs as pl
            ledger = sqlite3.connect(f"file:{pb.DB_PATH}?mode=ro", uri=True)
            ledger.row_factory = sqlite3.Row
            personal = pl.connect()
            try:
                checks.append(check_reconciliation(ledger, personal))
                checks.append(check_settlement_backlog(ledger, personal, now))
                checks.append(check_sources(now))
                checks.append(check_quotes(now))
                ready = readiness(now, personal)
            finally:
                ledger.close()
                personal.close()
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
