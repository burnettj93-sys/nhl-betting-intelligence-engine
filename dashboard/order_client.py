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


def issue_title(order: dict) -> str:
    return f"paper-order {order['order_id']}"


def issue_body(order: dict) -> str:
    return "Paper-book order from the dashboard. Do not edit.\n\n```json\n" + json.dumps(order, indent=1, sort_keys=True) + "\n```\n"


def prefilled_issue_url(order: dict) -> str:
    q = urllib.parse.urlencode({"labels": LABEL, "title": issue_title(order), "body": issue_body(order)})
    return f"https://github.com/{REPO}/issues/new?{q}"


def submit_direct(order: dict, token: str, *, opener=urllib.request.urlopen) -> dict:
    """Creates the order issue as the token's owner. Returns {"ok": True, "issue": n} or {"ok": False, "error": text}."""
    payload = json.dumps({"title": issue_title(order), "body": issue_body(order), "labels": [LABEL]}).encode()
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


def configured_write_access(secrets_obj, user_email: str | None) -> tuple[bool, str | None]:
    """(direct_allowed, token). Direct writes need the token secret AND a viewer email on the allow-list."""
    try:
        token = (secrets_obj.get("PAPER_ORDER_TOKEN") or "").strip()
        allowed = [e.strip().lower() for e in str(secrets_obj.get("ORDER_ALLOWED_EMAILS") or "").split(",") if e.strip()]
    except Exception:  # noqa: BLE001 - no secrets file at all
        return False, None
    if token and allowed and user_email and user_email.strip().lower() in allowed:
        return True, token
    return False, None
