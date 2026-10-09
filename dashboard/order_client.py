"""
The page side of the hosted write actions.

The hosted app cannot write a database, so an explicit click files an ORDER in a durable queue (a GitHub issue) and the engine answers it
(operational/manual_orders.py). Nothing here runs on page load, filtering or refresh; a function in this module is only called from a button handler.

  * Personal logs (add a bet / create a log): the app's own write credential (`LOG_WRITE_TOKEN`, a fine-grained token limited to Issues on this repository)
    creates the order issue. The people who can reach the app are the people the owner invited to it; the log code only says WHICH log a bet belongs to.
    The order carries a one-way hash of the code, never the code.
  * Evidence records that feed the shared product (goalie confirmations, Ontario price checks, the non-staking path check): the same credential, plus a
    signed-in viewer (`st.login()` OIDC) whose email is on `ORDER_ALLOWED_EMAILS`, because those change what everyone sees.
  * Without a credential the click builds a pre-filled GitHub issue; only the repository owner's issues are processed.
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

REPO = "burnettj93-sys/nhl-betting-intelligence-engine"
LABEL = "paper-order"
PERSONAL_LABEL = "personal-bet"
PERSONAL_TYPES = ("PERSONAL_BET", "PERSONAL_LOG_CREATE")
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


def build_personal_order(option: dict | None, *, order_id: str, log_hash: str, page_generated_at: str | None, stake: float,
                         create: dict | None = None, kind: str = "PERSONAL_BET") -> dict:
    """An order to add `option` to the personal log whose code hashes to `log_hash` (kind PERSONAL_BET), or just to create that log (PERSONAL_LOG_CREATE).
    `create` = {"creation_id", "display_name"} while the log does not exist yet. The code itself is never put in an order."""
    log = {"hash": log_hash}
    if create:
        log["create"] = {"creation_id": create["creation_id"], "display_name": create["display_name"]}
    doc = {"schema": SCHEMA, "type": kind, "order_id": order_id, "log": log, "page_generated_at_utc": page_generated_at}
    if kind == "PERSONAL_BET":
        doc["option_id"] = option["option_id"]
        doc["accepted"] = {
            "legs": [{k: l.get(k) for k in ("game_id", "participant_id", "participant_name", "market_family", "threshold", "side",
                                            "american_price", "quote_updated_utc")} for l in option["legs"]],
            "combined_american": option["combined_american"], "hit_probability": option["hit_probability"], "stake": float(stake),
            "price_basis": option.get("price_basis")}
    return doc


def personal_write_token(secrets_obj) -> str | None:
    """The app's write credential for personal-log orders, or None. `LOG_WRITE_TOKEN`; `PAPER_ORDER_TOKEN` (the earlier name) is accepted too."""
    try:
        for key in ("LOG_WRITE_TOKEN", "PAPER_ORDER_TOKEN"):
            tok = str(secrets_obj.get(key) or "").strip()
            if tok:
                return tok
    except Exception:  # noqa: BLE001 - no secrets at all
        pass
    return None


def build_verification(leg: dict, *, verification_id: str, ontario_price: float, observed_at_utc: str, where_seen: str,
                       us_price_shown: float | None, notes: str = "") -> dict:
    return {"schema": SCHEMA, "type": "ONTARIO_VERIFICATION", "verification_id": verification_id,
            "leg": {k: leg.get(k) for k in ("game_id", "participant_id", "participant_name", "market_family", "threshold", "side")},
            "ontario_price": ontario_price, "us_price_shown": us_price_shown, "observed_at_utc": observed_at_utc,
            "where_seen": where_seen, "notes": notes}


def build_confirmation(*, confirmation_id: str, game_id: str, team: str, goalie_id: str, where_seen: str, seen_at_utc: str) -> dict:
    return {"schema": SCHEMA, "type": "GOALIE_CONFIRMATION", "confirmation_id": confirmation_id, "game_id": str(game_id), "team": team,
            "goalie_id": str(goalie_id), "where_seen": where_seen, "seen_at_utc": seen_at_utc}


def build_path_check(*, check_id: str, sent_at_utc: str, via: str, signed_in: bool, viewer_allowed: bool, write_path_configured: bool) -> dict:
    """A document that proves the click-to-queue path without staking anything. It carries only booleans, never the email or the token."""
    return {"schema": SCHEMA, "type": "ORDER_PATH_CHECK", "check_id": check_id, "sent_at_utc": sent_at_utc, "via": via,
            "signed_in": signed_in, "viewer_allowed": viewer_allowed, "write_path_configured": write_path_configured}


def _allowed(secrets_obj, key: str) -> list[str]:
    return [e.strip().lower() for e in str(secrets_obj.get(key) or "").split(",") if e.strip()]


def login_configured(secrets_obj) -> bool:
    """True when the app has an OIDC sign-in configured (`[auth]` with a client id, secret, cookie secret and metadata URL). Names only; values are never read out."""
    try:
        auth = secrets_obj.get("auth")
        return bool(auth) and all(str(auth.get(k) or "").strip() for k in ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"))
    except Exception:  # noqa: BLE001 - no secrets at all
        return False


def path_status(secrets_obj, signed_in_email: str | None) -> dict:
    """What the direct one-click path can see, as booleans plus a masked address. Never returns the token or the allow-list.

    Writes are authorised ONLY by a supported sign-in: `st.login()` (OIDC) -> `st.user.email`, matched to `ORDER_ALLOWED_EMAILS`. The platform's undocumented
    `X-Streamlit-User` header is never used for authorisation (Streamlit staff: "not a documented or stable public API ... should not be relied upon for
    authentication or user identification")."""
    try:
        token = bool((secrets_obj.get("PAPER_ORDER_TOKEN") or "").strip())
        emails = _allowed(secrets_obj, "ORDER_ALLOWED_EMAILS")
    except Exception:  # noqa: BLE001 - no secrets at all
        token, emails = False, []
    email = (signed_in_email or "").strip().lower()
    masked = None
    if email and "@" in email:
        name, domain = email.split("@", 1)
        masked = f"{name[:1]}{'*' * max(len(name) - 1, 1)}@{domain}"
    allowed = bool(email) and email in emails
    return {"write_path_configured": token, "login_configured": login_configured(secrets_obj), "allowed_email_count": len(emails), "signed_in": bool(email),
            "signed_in_masked": masked, "viewer_allowed": allowed, "direct_ready": token and allowed,
            "personal_log_writes_ready": personal_write_token(secrets_obj) is not None}


def issue_title(order: dict) -> str:
    if order.get("type") in PERSONAL_TYPES:
        return f"personal-bet {order['order_id']}"
    if order.get("type") == "ORDER_PATH_CHECK":
        return f"order-path-check {order['check_id']}"
    if order.get("type") == "GOALIE_CONFIRMATION":
        return f"goalie-confirmation {order['confirmation_id']}"
    if order.get("type") == "ONTARIO_VERIFICATION":
        return f"ontario-verification {order['verification_id']}"
    return f"paper-order {order['order_id']}"


def issue_label(order: dict) -> str:
    if order.get("type") in PERSONAL_TYPES:
        return PERSONAL_LABEL
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


def configured_write_access(secrets_obj, signed_in_email: str | None) -> tuple[bool, str | None]:
    """(direct_allowed, token). Direct writes need the token secret AND a signed-in viewer whose email is on `ORDER_ALLOWED_EMAILS`."""
    try:
        token = (secrets_obj.get("PAPER_ORDER_TOKEN") or "").strip()
    except Exception:  # noqa: BLE001 - no secrets file at all
        return False, None
    if token and path_status(secrets_obj, signed_in_email)["viewer_allowed"]:
        return True, token
    return False, None
