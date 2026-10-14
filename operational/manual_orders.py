"""
Hosted-app write queue, engine side (evidence only).

The hosted dashboard is read-only (Streamlit Community Cloud has no durable disk). An owner action there can file a small JSON document in a durable queue (a GitHub issue, see
dashboard/order_client.py) and the engine answers it here, on its own schedule. What it answers now:

  * ONTARIO_VERIFICATION, GOALIE_CONFIRMATION, ORDER_PATH_CHECK -> evidence records that stake nothing.

Bets submitted by individual people were REMOVED on 2026-10-14: there are no accounts, no personal logs and no "add to the paper book" orders. Issues carrying the old order labels are not read.
The $500 model book receives only the engine's own automatic tickets. The old tables and the personal-log database are kept untouched as an inactive archive (deploy/archive_personal_logs.py).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import re
import subprocess
from pathlib import Path

from operational import eastern_time as et
from operational import paper_bankroll as pb
from operational import quote_freshness, state_paths
from research.real_market_parlay import engine as rmp

ORDER_LABEL = "paper-order"
OWNER_LOGIN = "burnettj93-sys"
REPO = "burnettj93-sys/nhl-betting-intelligence-engine"
MAX_LEGS = 2
LOCK_NAME = "manual_orders.lock"
SCHEMA = 1

RECORDED, ALREADY, NEEDS_ACCEPTANCE, REJECTED = "RECORDED", "ALREADY_RECORDED", "NEEDS_ACCEPTANCE", "REJECTED"

LEG_KEY = ("game_id", "participant_id", "market_family", "threshold", "side")




# ------------------------------------------------------------------ validation ----









# ------------------------------------------------------------------- storage ----

def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")








# ------------------------------------------------------------------ GitHub queue ----

def _gh_binary() -> str:
    """launchd jobs run with a minimal PATH, so look in the usual Homebrew locations as well."""
    import shutil
    return shutil.which("gh") or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh") if Path(p).exists()), "gh")


def _gh(args: list[str], *, input_text: str | None = None) -> str:
    out = subprocess.run([_gh_binary(), *args], capture_output=True, text=True, input=input_text, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {out.stderr.strip()[:300]}")
    return out.stdout


def fetch_github_orders(repo: str = REPO, owner: str = OWNER_LOGIN, label: str = ORDER_LABEL) -> tuple[list[dict], list[dict]]:
    """(orders, ignored). Only open issues carrying the label AND opened by the repository owner are orders;
    anything else (the repository is public) is ignored and reported, never processed."""
    raw = _gh(["api", f"repos/{repo}/issues?labels={label}&state=open&per_page=50"])
    orders, ignored = [], []
    for issue in json.loads(raw):
        if "pull_request" in issue:
            continue
        author = (issue.get("user") or {}).get("login")
        if author != owner:
            ignored.append({"issue": issue["number"], "author": author, "reason": "not opened by the repository owner"})
            continue
        body = issue.get("body") or ""
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
        orders.append({"issue": issue["number"], "body": m.group(1) if m else body.strip(),
                       "created_at": issue.get("created_at")})
    return orders, ignored


# ------------------------------------------------------------ Ontario spot checks ----

ONTARIO_LABEL = "ontario-verification"
VERIFICATION_MAX_AGE_MIN = 60.0


def process_verification(conn, raw, *, now: dt.datetime, source: str) -> dict:
    """Stores one manual Ontario price check. Returns {"status": "RECORDED"|"ALREADY_RECORDED"|"REJECTED", ...}."""
    try:
        doc = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except json.JSONDecodeError:
        return {"status": "REJECTED", "reason": "not valid JSON"}
    if not isinstance(doc, dict) or doc.get("type") != "ONTARIO_VERIFICATION" or doc.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "not an ONTARIO_VERIFICATION document"}
    vid, leg = doc.get("verification_id"), doc.get("leg") or {}
    if not isinstance(vid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", vid):
        return {"status": "REJECTED", "reason": "verification_id malformed"}
    price = doc.get("ontario_price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or abs(price) < 100:
        return {"status": "REJECTED", "reason": "the price must be American odds (at least 100 in size)"}
    if any(k not in leg for k in LEG_KEY):
        return {"status": "REJECTED", "reason": "the selection is incomplete"}
    seen = quote_freshness.parse_utc(doc.get("observed_at_utc"))
    if seen is None:
        return {"status": "REJECTED", "reason": "observed_at_utc missing or malformed"}
    if (seen - now).total_seconds() > quote_freshness.FUTURE_TOLERANCE_S:
        return {"status": "REJECTED", "reason": "the time seen is in the future"}
    where = (doc.get("where_seen") or "").strip()
    if not where:
        return {"status": "REJECTED", "reason": "say where the price was seen (for example DraftKings Ontario app or website)"}
    if conn.execute("SELECT 1 FROM ontario_verifications WHERE verification_id = ?", (vid,)).fetchone():
        return {"status": "ALREADY_RECORDED", "verification_id": vid}
    conn.execute(
        "INSERT INTO ontario_verifications (verification_id, source, game_id, participant_id, participant_name, market_family, threshold, side, "
        "ontario_price, us_price_shown, observed_at_utc, recorded_at_utc, where_seen, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (vid, source, str(leg["game_id"]), str(leg["participant_id"]), leg.get("participant_name"), leg["market_family"],
         None if leg["threshold"] is None else int(leg["threshold"]), leg["side"], float(price), doc.get("us_price_shown"),
         quote_freshness.iso_z(seen), quote_freshness.iso_z(now), where[:120], (doc.get("notes") or "")[:400]))
    conn.commit()
    return {"status": "RECORDED", "verification_id": vid}


def recent_verifications(conn, limit: int = 100) -> list[dict]:
    rows = conn.execute("SELECT * FROM ontario_verifications ORDER BY observed_at_utc DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------ order-path check (never touches the ledger) ----

PATH_CHECK_LABEL = "order-path-check"
PATH_CHECK_STATE = "order_path_checks.json"
PATH_CHECK_KEEP = 20


def load_path_checks() -> list[dict]:
    p = state_paths.path(PATH_CHECK_STATE)
    try:
        rows = json.loads(p.read_text()) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []
    for r in rows:                                     # rows written before the key was renamed
        if "token_configured" in r:
            r["write_path_configured"] = r.pop("token_configured")
    return rows


def _save_path_checks(rows: list[dict]) -> None:
    p = state_paths.path(PATH_CHECK_STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-PATH_CHECK_KEEP:], sort_keys=True))
    tmp.replace(p)


def process_path_check(raw, *, now: dt.datetime, source: str, author: str | None = None) -> dict:
    """Records that a click in the hosted app reached the queue and was accepted. No order, no ticket, no stake, no ledger write."""
    try:
        doc = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except json.JSONDecodeError:
        return {"status": "REJECTED", "reason": "not valid JSON"}
    if not isinstance(doc, dict) or doc.get("type") != "ORDER_PATH_CHECK" or doc.get("schema") != SCHEMA:
        return {"status": "REJECTED", "reason": "not an ORDER_PATH_CHECK document"}
    cid = doc.get("check_id")
    if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", cid):
        return {"status": "REJECTED", "reason": "check_id malformed"}
    rows = load_path_checks()
    if any(r["check_id"] == cid and r["status"] == "ACCEPTED" for r in rows):
        return {"status": "ALREADY_RECORDED", "check_id": cid}
    row = {"check_id": cid, "status": "ACCEPTED", "source": source, "author": author, "processed_at_utc": quote_freshness.iso_z(now),
           "sent_at_utc": doc.get("sent_at_utc"), "via": doc.get("via"), "signed_in": bool(doc.get("signed_in", doc.get("viewer_email_present"))),
           "viewer_allowed": bool(doc.get("viewer_allowed")), "write_path_configured": bool(doc.get("write_path_configured", doc.get("token_configured"))),   # key names must pass the snapshot secret-name guard (no "token")
           "note": "Accepted by the queue processor. No order, ticket or stake was created."}
    _save_path_checks(rows + [row])
    return {"status": "ACCEPTED", "check_id": cid}


def note_ignored_path_checks(ignored: list[dict], now: dt.datetime) -> None:
    """A check opened by anyone but the repository owner is not processed; it is listed so the app can explain why nothing came back."""
    rows = load_path_checks()
    seen = {r.get("source") for r in rows}
    add = [{"check_id": None, "status": "IGNORED_AUTHOR", "source": f"github-issue:{i['issue']}", "author": i.get("author"),
            "processed_at_utc": quote_freshness.iso_z(now), "note": f"Issue {i['issue']} was opened by {i.get('author')}, not the repository owner, so the "
                                                                     "queue ignored it. A token that belongs to another account cannot place orders."}
           for i in ignored if i.get("issue") and f"github-issue:{i['issue']}" not in seen]
    if add:
        _save_path_checks(rows + add)


def _confirm(nhl_conn, body, now, source):
    from operational import goalie_confirmations
    try:
        doc = json.loads(body) if isinstance(body, (str, bytes)) else body
    except json.JSONDecodeError:
        return {"status": "REJECTED", "reason": "not valid JSON"}
    return goalie_confirmations.validate_and_record(nhl_conn, doc, now=now, source_ref=source)






def poll_and_process(conn, nhl_conn, now: dt.datetime, *, current_legs: list[rmp.ParlayLeg] | None = None,
                     repo: str = REPO, owner: str = OWNER_LOGIN, fetch=None, personal_conn=None, personal_fetch=None, builder_pool=None) -> dict:
    """One pass of the engine-side queue. Serialised by a lock file so overlapping runs cannot race."""
    lock_path = state_paths.path(LOCK_NAME)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "SKIPPED", "reason": "ANOTHER_PASS_IN_PROGRESS", "processed": 0}
        results, personal_results = [], []
        ignored = []
        v_results = []
        if fetch is None:
            from operational import goalie_confirmations
            handlers = {ONTARIO_LABEL: lambda body, src: process_verification(conn, body, now=now, source=src),
                        goalie_confirmations.LABEL: lambda body, src: _confirm(nhl_conn, body, now, src),
                        PATH_CHECK_LABEL: lambda body, src: process_path_check(body, now=now, source=src, author=owner)}
            v_ignored = []
            for label, handler in handlers.items():
                try:
                    v_orders, ign = fetch_github_orders(repo, owner, label)
                except Exception as exc:  # noqa: BLE001
                    v_orders, ign = [], [{"error": f"{exc.__class__.__name__}: {exc}"}]
                v_ignored += ign
                if label == PATH_CHECK_LABEL:
                    note_ignored_path_checks([i for i in ign if "issue" in i], now)
                for o in v_orders:
                    res = handler(o["body"], f"github-issue:{o['issue']}")
                    v_results.append({"issue": o["issue"], "label": label, **res})
                    try:
                        _gh(["api", f"repos/{repo}/issues/{o['issue']}/comments", "-f", f"body={label} -> **{res['status']}**" + (f": {res['reason']}" if res.get("reason") else ".")])
                        _gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{o['issue']}", "-f", "state=closed", "-f", "state_reason=completed"])
                    except Exception as exc:  # noqa: BLE001
                        v_results[-1]["comment_error"] = f"{exc.__class__.__name__}: {exc}"
        else:
            v_ignored = []
        return {"status": "OK", "processed": len(results) + len(v_results) + len(personal_results), "results": results, "verifications": v_results,
                "personal": personal_results, "ignored": ignored + v_ignored}
