"""
Manual "Add to paper book -- $10": the only way a ticket enters the paper book by hand.

The hosted dashboard is read-only (Streamlit Community Cloud has no durable disk), so a click there cannot write the
ledger directly. It files an ORDER -- a small JSON document naming the legs and prices the person was looking at --
in a durable, authenticated queue (a GitHub issue created by the repository owner; see dashboard/order_client.py).
This module is the engine side:

  1. fetch_github_orders()   reads open `paper-order` issues, keeps ONLY those opened by the repository owner;
  2. process_order()         revalidates the order against the CURRENT fresh prices and either
        RECORDED            -> one $10 MANUALLY_ADDED ticket in the shared paper account,
        ALREADY_RECORDED    -> the same order id / same bet already on the book (never a second stake),
        NEEDS_ACCEPTANCE    -> a price or probability moved since the person looked; nothing is recorded until they
                               explicitly accept the new details (a new order carrying the new prices),
        REJECTED            -> not eligible any more (stale/started/unavailable/no longer +100 with value/no cash/...);
  3. every order, whatever happened, is one row of `manual_orders` keyed by order id (a repeated click or a retried
     transport reads the stored answer back instead of processing again).

Rules carried over from the automatic tickets, unchanged: exact $10 stake, atomic funds check, deterministic ticket
identity, frozen entry details, no top-up, settlement by the same driver. A manual ticket never takes one of the five
automatic daily slots; exposure and the account are shared; performance is reported by origin.
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


def leg_key(d) -> tuple:
    g = (lambda k: d.get(k)) if isinstance(d, dict) else (lambda k: getattr(d, k))
    return (str(g("game_id")), str(g("participant_id")), g("market_family"), None if g("threshold") is None else int(g("threshold")), g("side"))


# ------------------------------------------------------------------ validation ----

def parse_order(raw) -> tuple[dict | None, str | None]:
    """(order, error). The order document is checked for shape only; whether it is still a good bet is revalidation."""
    try:
        order = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except json.JSONDecodeError:
        return None, "The order is not valid JSON."
    if not isinstance(order, dict) or order.get("type") != "PAPER_ORDER" or order.get("schema") != SCHEMA:
        return None, "Not a PAPER_ORDER document of the supported schema."
    oid = order.get("order_id")
    if not isinstance(oid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", oid):
        return None, "order_id is missing or malformed."
    accepted = order.get("accepted")
    if not isinstance(accepted, dict) or not isinstance(accepted.get("legs"), list) or not (1 <= len(accepted["legs"]) <= MAX_LEGS):
        return None, f"The order must name 1 to {MAX_LEGS} legs."
    for l in accepted["legs"]:
        if not isinstance(l, dict) or any(k not in l for k in LEG_KEY) or not isinstance(l.get("american_price"), (int, float)) \
                or isinstance(l.get("american_price"), bool) or abs(l["american_price"]) < 100:
            return None, "Every leg needs its identity fields and an American price."
    if len({l["game_id"] for l in accepted["legs"]}) != len(accepted["legs"]):
        return None, "Two legs from the same game are not supported (no same-game joint model or quoted combined price)."
    stake = accepted.get("stake", pb.PAPER_BET_STAKE)
    if isinstance(stake, bool) or not isinstance(stake, (int, float)) or abs(stake - pb.PAPER_BET_STAKE) > 1e-9:
        return None, f"The stake is fixed at ${pb.PAPER_BET_STAKE:.2f}."
    return order, None


def revalidate(accepted: dict, current_legs: list[rmp.ParlayLeg], now: dt.datetime) -> dict:
    """Compares what the person accepted with the current fresh state.
    status: OK (identical), CHANGED (still qualifies, details moved), UNAVAILABLE (a leg is gone/stale/started),
            NO_LONGER_QUALIFIES (available but no longer passes the ticket policy)."""
    by_key = {leg_key(l): l for l in current_legs if rmp.leg_is_eligible(l)}
    legs, missing, changes = [], [], []
    for a in accepted["legs"]:
        cur = by_key.get(leg_key(a))
        if cur is None:
            missing.append(a)
            continue
        legs.append(cur)
        if abs(float(a["american_price"]) - cur.american_price) > 1e-9:
            changes.append({"leg": rmp.leg_label(cur), "was": a["american_price"], "now": cur.american_price})
    if missing:
        names = ", ".join(f"{m.get('participant_name') or m['participant_id']}" for m in missing)
        return {"status": "UNAVAILABLE", "legs": legs, "changes": changes, "combo": None,
                "reason": f"No current fresh price for: {names} (the quote is stale, withdrawn, or the game has started)."}
    combo = rmp._evaluate_combo(legs)
    if combo is None or not rmp.ticket_passes_policy(combo):
        return {"status": "NO_LONGER_QUALIFIES", "legs": legs, "changes": changes, "combo": combo,
                "reason": "At the current prices this no longer reaches +100 with positive value under the ticket policy."}
    claimed_p = accepted.get("hit_probability")
    if claimed_p is not None and round(float(claimed_p), 2) != round(combo.joint_probability, 2):
        changes.append({"leg": "hit chance", "was": round(float(claimed_p), 4), "now": round(combo.joint_probability, 4)})
    return {"status": "CHANGED" if changes else "OK", "legs": legs, "changes": changes, "combo": combo, "reason": None}


def current_details(rv: dict) -> dict:
    """The details to show a person who must accept a change."""
    from operational import player_options
    combo = rv["combo"]
    if combo is None:
        return {}
    kind = player_options.SINGLE if len(combo.legs) == 1 else player_options.PARLAY
    opt = player_options._option_from_combo(kind, combo, et.eastern_today())
    opt["changes"] = rv["changes"]
    return opt


# ------------------------------------------------------------------- storage ----

def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stored_order(conn, order_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM manual_orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def _store(conn, order_id: str, source: str, option_id, status: str, reason, ticket_id, request, detail, received_at) -> dict:
    conn.execute(
        "INSERT OR IGNORE INTO manual_orders (order_id, source, option_id, status, reason, ticket_id, request_json, detail_json, "
        "received_at_utc, processed_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, source, option_id, status, reason, ticket_id, json.dumps(request, sort_keys=True, default=str),
         json.dumps(detail, sort_keys=True, default=str) if detail is not None else None, received_at, _utcnow()))
    conn.commit()
    return stored_order(conn, order_id)


def recent_orders(conn, limit: int = 40) -> list[dict]:
    rows = conn.execute("SELECT * FROM manual_orders ORDER BY processed_at_utc DESC, order_id LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json")) if d.get("detail_json") else None
        d.pop("request_json", None)
        out.append(d)
    return out


# ------------------------------------------------------------------ processing ----

def process_order(conn, raw_order, *, current_legs: list[rmp.ParlayLeg], now: dt.datetime, source: str,
                  received_at: str | None = None, page_generated_at: str | None = None) -> dict:
    """Process one order to a stored answer. Safe to call any number of times, concurrently: the first call decides,
    later calls (same order id) return what was stored."""
    received_at = received_at or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    order, error = parse_order(raw_order)
    if order is None:
        oid = None
        if isinstance(raw_order, dict):
            oid = raw_order.get("order_id")
        oid = oid if isinstance(oid, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", oid) else "INVALID-" + re.sub(r"\W", "", source)[:40]
        prior = stored_order(conn, oid)
        if prior:
            return prior
        return _store(conn, oid, source, None, REJECTED, error, None, {"raw": str(raw_order)[:2000]}, None, received_at)
    oid = order["order_id"]
    prior = stored_order(conn, oid)
    if prior:
        return prior
    accepted = order["accepted"]
    option_id = order.get("option_id")

    rv = revalidate(accepted, current_legs, now)
    if rv["status"] in ("UNAVAILABLE", "NO_LONGER_QUALIFIES"):
        return _store(conn, oid, source, option_id, REJECTED, rv["reason"], None, order,
                      current_details(rv) or None, received_at)
    if rv["status"] == "CHANGED":
        return _store(conn, oid, source, option_id, NEEDS_ACCEPTANCE,
                      "Prices or the hit chance changed since you looked. Nothing was recorded; review the new details "
                      "and accept them to add the ticket.", None, order, current_details(rv), received_at)

    combo = rv["combo"]
    created = max([now] + [t for t in (quote_freshness.parse_utc(l.retrieved_at_utc or l.captured_at_utc) for l in combo.legs)
                           if t is not None and (t - now).total_seconds() <= 120.0])
    from operational import daily_tickets
    provenance = {
        "order_id": oid, "source": source, "option_id": option_id, "received_at_utc": received_at,
        "revalidated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "page_generated_at_utc": page_generated_at or order.get("page_generated_at_utc"),
        "accepted": {"legs": accepted["legs"], "combined_american": accepted.get("combined_american"),
                     "hit_probability": accepted.get("hit_probability")},
        "supersedes_order_id": order.get("supersedes_order_id"),
        "price_basis": "SPORTSBOOK_QUOTE" if len(combo.legs) == 1 else "ESTIMATED_PRODUCT_OF_LEG_PRICES",
        "jurisdiction_note": "Prices are DraftKings US-feed quotes (The Odds API); not verified against DraftKings Ontario.",
    }
    res = pb.create_manual_paper_bet(conn, combo, provenance=provenance, event_start_utc=daily_tickets._earliest_start(combo),
                                     created_at_utc=created.isoformat(), code_version=daily_tickets.code_version())
    status = res["status"]
    if status == "INSERTED":
        return _store(conn, oid, source, option_id, RECORDED, None, res["paper_bet_id"], order, None, received_at)
    if status == "DUPLICATE":
        origin = res.get("duplicate_of_origin")
        why = ("The automatic tickets already hold this exact bet today." if origin == "AUTOMATIC"
               else "This exact bet is already on the book (added earlier).")
        return _store(conn, oid, source, option_id, ALREADY, why, res["paper_bet_id"], order, None, received_at)
    if status == "INSUFFICIENT_FUNDS":
        return _store(conn, oid, source, option_id, REJECTED,
                      f"Available cash ${res['available_cash']:.2f} is below the ${pb.PAPER_BET_STAKE:.0f} stake. "
                      "There is no top-up; cash returns as tickets settle.", None, order, None, received_at)
    return _store(conn, oid, source, option_id, REJECTED, f"Unexpected result {status}.", None, order, None, received_at)


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


def _confirm(nhl_conn, body, now, source):
    from operational import goalie_confirmations
    try:
        doc = json.loads(body) if isinstance(body, (str, bytes)) else body
    except json.JSONDecodeError:
        return {"status": "REJECTED", "reason": "not valid JSON"}
    return goalie_confirmations.validate_and_record(nhl_conn, doc, now=now, source_ref=source)


def _answer_text(row: dict) -> str:
    base = {RECORDED: f"Recorded as {row.get('ticket_id')} ($10 MANUALLY_ADDED).",
            ALREADY: f"Not recorded again: {row.get('reason')} ({row.get('ticket_id')}).",
            NEEDS_ACCEPTANCE: row.get("reason"), REJECTED: f"Rejected: {row.get('reason')}"}[row["status"]]
    return f"order `{row['order_id']}` -> **{row['status']}**. {base}"


def poll_and_process(conn, nhl_conn, now: dt.datetime, *, current_legs: list[rmp.ParlayLeg] | None = None,
                     repo: str = REPO, owner: str = OWNER_LOGIN, fetch=None) -> dict:
    """One pass of the engine-side queue. Serialised by a lock file so overlapping runs cannot race."""
    lock_path = state_paths.path(LOCK_NAME)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"status": "SKIPPED", "reason": "ANOTHER_PASS_IN_PROGRESS", "processed": 0}
        try:
            orders, ignored = (fetch or fetch_github_orders)(repo, owner)
        except Exception as exc:  # noqa: BLE001 -- the queue being unreachable must not crash the trader
            return {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "processed": 0}
        results = []
        legs = current_legs
        for o in orders:
            source = f"github-issue:{o['issue']}"
            if legs is None:
                from operational import daily_tickets
                legs = daily_tickets.collect_candidate_legs(nhl_conn, now)["legs"]
            try:
                parsed = json.loads(o["body"])
            except (json.JSONDecodeError, TypeError):
                parsed = o["body"]
            row = process_order(conn, parsed, current_legs=legs, now=now, source=source, received_at=o.get("created_at"))
            results.append({"issue": o["issue"], **{k: row[k] for k in ("order_id", "status", "reason", "ticket_id")}})
            try:
                _gh(["api", f"repos/{repo}/issues/{o['issue']}/comments", "-f", f"body={_answer_text(row)}"])
                _gh(["api", "-X", "PATCH", f"repos/{repo}/issues/{o['issue']}", "-f", "state=closed", "-f", "state_reason=completed"])
            except Exception as exc:  # noqa: BLE001 -- the answer is stored either way; a failed comment is reported
                results[-1]["comment_error"] = f"{exc.__class__.__name__}: {exc}"
        v_results = []
        if fetch is None:
            from operational import goalie_confirmations
            handlers = {ONTARIO_LABEL: lambda body, src: process_verification(conn, body, now=now, source=src),
                        goalie_confirmations.LABEL: lambda body, src: _confirm(nhl_conn, body, now, src)}
            v_ignored = []
            for label, handler in handlers.items():
                try:
                    v_orders, ign = fetch_github_orders(repo, owner, label)
                except Exception as exc:  # noqa: BLE001
                    v_orders, ign = [], [{"error": f"{exc.__class__.__name__}: {exc}"}]
                v_ignored += ign
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
        return {"status": "OK", "processed": len(results) + len(v_results), "results": results, "verifications": v_results, "ignored": ignored + v_ignored}
