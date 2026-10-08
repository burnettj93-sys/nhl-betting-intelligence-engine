"""
The page side of "Add to paper book -- $10".

The hosted app cannot write the ledger, so an explicit click files an ORDER in a durable, authenticated queue and the
engine (operational/manual_orders.py) answers it after revalidating against current prices. Two ways in:

  * direct: when this app has a GitHub token secret (PAPER_ORDER_TOKEN, a fine-grained token limited to Issues on this
    repository) and the signed-in viewer's email is listed in ORDER_ALLOWED_EMAILS, the click creates the order issue
    itself -- one click;
  * link: otherwise the click builds a pre-filled GitHub issue; opening it and pressing "Submit new issue" while signed in
    as the repository owner files the order (GitHub is the authentication). The engine ignores issues from anyone else.

Nothing here runs on page load, filtering or refresh; a function in this module is only called from a button handler.
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

REPO = "burnettj93-sys/nhl-betting-intelligence-engine"
LABEL = "paper-order"
SCHEMA = 1


def new_order_id() -> str:
    return "ord_" + secrets.token_hex(8)


def build_order(option: dict, *, order_id: str, page_generated_at: str | None, supersedes: str | None = None) -> dict:
    return {
        "schema": SCHEMA, "type": "PAPER_ORDER", "order_id": order_id, "option_id": option["option_id"],
        "page_generated_at_utc": page_generated_at, "supersedes_order_id": supersedes,
        "accepted": {
            "legs": [{k: l.get(k) for k in ("game_id", "participant_id", "participant_name", "market_family", "threshold", "side",
                                            "american_price", "quote_updated_utc")} for l in option["legs"]],
            "combined_american": option["combined_american"], "hit_probability": option["hit_probability"], "stake": 10.0,
            "price_basis": option.get("price_basis")}}


def build_verification(leg: dict, *, verification_id: str, ontario_price: float, observed_at_utc: str, where_seen: str,
                       us_price_shown: float | None, notes: str = "") -> dict:
    return {"schema": SCHEMA, "type": "ONTARIO_VERIFICATION", "verification_id": verification_id,
            "leg": {k: leg.get(k) for k in ("game_id", "participant_id", "participant_name", "market_family", "threshold", "side")},
            "ontario_price": ontario_price, "us_price_shown": us_price_shown, "observed_at_utc": observed_at_utc,
            "where_seen": where_seen, "notes": notes}


def build_confirmation(*, confirmation_id: str, game_id: str, team: str, goalie_id: str, where_seen: str, seen_at_utc: str) -> dict:
    return {"schema": SCHEMA, "type": "GOALIE_CONFIRMATION", "confirmation_id": confirmation_id, "game_id": str(game_id), "team": team,
            "goalie_id": str(goalie_id), "where_seen": where_seen, "seen_at_utc": seen_at_utc}


def build_path_check(*, check_id: str, sent_at_utc: str, via: str, viewer_email_present: bool, viewer_allowed: bool, token_configured: bool) -> dict:
    """A document that proves the click-to-queue path without staking anything. It carries only booleans, never the email or the token."""
    return {"schema": SCHEMA, "type": "ORDER_PATH_CHECK", "check_id": check_id, "sent_at_utc": sent_at_utc, "via": via,
            "viewer_email_present": viewer_email_present, "viewer_allowed": viewer_allowed, "token_configured": token_configured}


def fingerprint(viewer_value: str | None) -> str | None:
    """A short, one-way tag of an opaque platform viewer id (first 12 hex characters of its SHA-256) -- what goes in the allow-list, so the raw id is never stored or shown."""
    import hashlib
    return hashlib.sha256(viewer_value.encode()).hexdigest()[:12] if viewer_value else None


def _allowed(secrets_obj, key: str) -> list[str]:
    return [e.strip().lower() for e in str(secrets_obj.get(key) or "").split(",") if e.strip()]


def path_status(secrets_obj, user_email: str | None, viewer_id: str | None = None) -> dict:
    """What the direct one-click path can see, as booleans plus masked identifiers. Never returns the token or an allow-list.
    The viewer is recognised by email (`ORDER_ALLOWED_EMAILS`) or, where the platform supplies only an opaque viewer id, by that id's fingerprint
    (`ORDER_ALLOWED_VIEWER_IDS`)."""
    try:
        token = bool((secrets_obj.get("PAPER_ORDER_TOKEN") or "").strip())
        emails, ids = _allowed(secrets_obj, "ORDER_ALLOWED_EMAILS"), _allowed(secrets_obj, "ORDER_ALLOWED_VIEWER_IDS")
    except Exception:  # noqa: BLE001 - no secrets at all
        token, emails, ids = False, [], []
    email = (user_email or "").strip().lower()
    masked = None
    if email and "@" in email:
        name, domain = email.split("@", 1)
        masked = f"{name[:1]}{'*' * max(len(name) - 1, 1)}@{domain}"
    fp = fingerprint(viewer_id)
    by_email = bool(email) and email in emails
    by_id = bool(fp) and fp in ids
    return {"token_configured": token, "allowed_email_count": len(emails), "allowed_viewer_id_count": len(ids), "viewer_email_present": bool(email),
            "viewer_email_masked": masked, "viewer_id_present": bool(fp), "viewer_fingerprint": fp, "viewer_allowed": by_email or by_id,
            "allowed_by": "email" if by_email else ("viewer id" if by_id else None), "direct_ready": token and (by_email or by_id)}


def issue_title(order: dict) -> str:
    if order.get("type") == "ORDER_PATH_CHECK":
        return f"order-path-check {order['check_id']}"
    if order.get("type") == "GOALIE_CONFIRMATION":
        return f"goalie-confirmation {order['confirmation_id']}"
    if order.get("type") == "ONTARIO_VERIFICATION":
        return f"ontario-verification {order['verification_id']}"
    return f"paper-order {order['order_id']}"


def issue_label(order: dict) -> str:
    if order.get("type") == "ORDER_PATH_CHECK":
        return "order-path-check"
    if order.get("type") == "GOALIE_CONFIRMATION":
        return "goalie-confirmation"
    return "ontario-verification" if order.get("type") == "ONTARIO_VERIFICATION" else LABEL


def issue_body(order: dict) -> str:
    return "Request from the dashboard. Do not edit.\n\n```json\n" + json.dumps(order, indent=1, sort_keys=True) + "\n```\n"


def prefilled_issue_url(order: dict) -> str:
    q = urllib.parse.urlencode({"labels": issue_label(order), "title": issue_title(order), "body": issue_body(order)})
    return f"https://github.com/{REPO}/issues/new?{q}"


def submit_direct(order: dict, token: str, *, opener=urllib.request.urlopen) -> dict:
    """Creates the order issue as the token's owner. Returns {"ok": True, "issue": n} or {"ok": False, "error": text}."""
    payload = json.dumps({"title": issue_title(order), "body": issue_body(order), "labels": [issue_label(order)]}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/issues", data=payload, method="POST", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "User-Agent": "nhl-engine-dashboard", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with opener(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        return {"ok": True, "issue": body.get("number")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"GitHub refused the order (HTTP {exc.code})."}
    except Exception as exc:  # noqa: BLE001 - shown to the person, never raised into the page
        return {"ok": False, "error": f"The order could not be filed ({type(exc).__name__})."}


def configured_write_access(secrets_obj, user_email: str | None, viewer_id: str | None = None) -> tuple[bool, str | None]:
    """(direct_allowed, token). Direct writes need the token secret AND a viewer on an allow-list (by email or by viewer-id fingerprint)."""
    try:
        token = (secrets_obj.get("PAPER_ORDER_TOKEN") or "").strip()
    except Exception:  # noqa: BLE001 - no secrets file at all
        return False, None
    st_ = path_status(secrets_obj, user_email, viewer_id)
    if token and st_["viewer_allowed"]:
        return True, token
    return False, None
