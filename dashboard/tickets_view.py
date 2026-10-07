"""Display formatting for the Today ticket board. Pure functions (no I/O, no
db): operational/daily_tickets.py writes the state document, this turns it into
the strings the page shows, and the page and its tests share this one path."""
from __future__ import annotations

import datetime as dt

from operational import eastern_time as et

STALE_AFTER_MINUTES = 45

BADGES = {
    "RECOMMENDED": ("Recommended", "#8a6d00", "#fff4cc"),
    "RECORDED": ("Recorded", "#0b4a8f", "#dbeafe"),
    "PENDING": ("Pending", "#5b3b9a", "#ece3ff"),
    "WON": ("Won", "#0d6b2f", "#d6f5df"),
    "LOST": ("Lost", "#9b1c1c", "#fde0e0"),
    "VOID": ("Void", "#4b5563", "#e5e7eb"),
    "UNRESOLVED": ("Unresolved", "#9a3412", "#ffe6d5"),
}


def money(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def signed_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{'+' if value > 0 else '-' if value < 0 else ''}${abs(value):,.2f}"


def american(price: float | None) -> str:
    if price is None:
        return "—"
    price = int(round(price))
    return f"+{price}" if price > 0 else str(price)


def et_time(iso_utc: str | None, with_date: bool = False) -> str:
    if not iso_utc:
        return "time n/a"
    parsed = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    fmt = "%b %-d, %-I:%M %p ET" if with_date else "%-I:%M %p ET"
    return parsed.astimezone(et.EASTERN).strftime(fmt)


def badge(status: str) -> tuple[str, str, str]:
    return BADGES.get(status, (status.title(), "#4b5563", "#e5e7eb"))


def percent(p: float | None) -> str:
    return "—" if p is None else f"{p * 100:.0f}%"


def account_header(state: dict) -> list[tuple[str, str, str | None]]:
    a = state["account"]
    return [
        ("Available cash", money(a["available_cash"]), f"starting {money(a['starting_bankroll'])}"),
        ("Open stakes", money(a["open_stakes"]), f"{a['open_tickets']} open ticket(s)"),
        ("Equity", money(a["equity"]), "cash + open stakes at cost"),
        ("Settled P&L", signed_money(a["settled_pnl"]), None),
    ]


def freshness(state: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    generated = dt.datetime.fromisoformat(state["generated_at_utc"])
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=dt.timezone.utc)
    age = (now - generated).total_seconds() / 60.0
    return {"updated": et_time(state["generated_at_utc"]), "age_minutes": round(age),
            "stale": age > STALE_AFTER_MINUTES}


def leg_row(leg: dict) -> dict:
    where = ""
    if leg.get("team") and leg.get("opponent"):
        where = f"{leg['team']} vs {leg['opponent']} · "
    mark = {"WIN": "✓ ", "LOSS": "✗ ", "VOID": "– void ", "UNRESOLVED": "? "}.get(leg.get("outcome") or "", "")
    return {
        "selection": f"{mark}{leg['label']}",
        "game": f"{where}{et_time(leg.get('game_start_utc'))}",
        "price": american(leg["american_price"]),
        "priced_at": et_time(leg.get("price_captured_at_utc")),
        "model": percent(leg.get("probability")),
    }


def alert_text(alert: dict) -> str:
    detail = alert["detail"]
    prefix = f"{alert['kind']}: "
    if detail.startswith(prefix):
        detail = detail[len(prefix):]
    return (f"Alert, ticket unchanged — {alert['kind'].replace('_', ' ').lower()}: {detail}. "
            f"The stake stays open; if a player does not play, that leg is settled under the void rules.")


def labels_line(ticket: dict) -> str:
    versions = sorted({l.get("model_version") for l in ticket["legs"] if l.get("model_version")})
    experimental = any("EXPERIMENTAL" in v for v in versions)
    parts = ["EXPERIMENTAL model probabilities (not calibrated)" if experimental else "Model probabilities (research)",
             "DraftKings US-feed prices, not matched to Ontario", "estimated combined price (product of leg prices)"]
    return " · ".join(parts)


def exposure_rows(exposure_state: dict) -> tuple[list[dict], list[dict]]:
    players = [{"Player": p["player"], "On tickets": p["count"], "Ticket IDs": ", ".join(p["tickets"])}
               for p in exposure_state["players"] if p["count"] > 1]
    games = [{"Game": g["matchup"], "On tickets": g["count"], "Ticket IDs": ", ".join(g["tickets"])}
             for g in exposure_state["games"]]
    return players, games


def card(ticket: dict) -> dict:
    label, fg, bg = badge(ticket["status"])
    result = ticket.get("result") or {}
    outcome = None
    if ticket["status"] in ("WON", "LOST", "VOID"):
        outcome = f"{signed_money(result.get('profit_loss'))} on this ticket"
        if result.get("settled_odds"):
            outcome += f" (repriced to {american(result['settled_odds'])} after a void leg)"
    elif ticket["status"] == "UNRESOLVED":
        outcome = result.get("notes") or "A leg could not be graded; the stake stays open until it can."
    return {
        "title": f"Ticket {ticket['ticket_id']}", "badge": label, "badge_fg": fg, "badge_bg": bg,
        "legs": [leg_row(l) for l in ticket["legs"]],
        "combined": f"{american(ticket['combined_american'])} (estimated combined price)",
        "stake": money(ticket["stake"]),
        "potential_return": f"{money(ticket['potential_return'])} return · {money(ticket['potential_profit'])} profit",
        "hit": percent(ticket.get("hit_probability")),
        "rationale": ticket.get("rationale", ""),
        "labels": labels_line(ticket),
        "recorded_at": et_time(ticket.get("recorded_at_utc"), with_date=True) if ticket.get("recorded_at_utc") else None,
        "outcome": outcome,
        "alerts": [alert_text(a) for a in ticket.get("alerts", [])],
    }


def single_row(single: dict) -> dict:
    where = f"{single['team']} vs {single['opponent']} · " if single.get("team") and single.get("opponent") else ""
    return {"Selection": single["label"], "Game": f"{where}{et_time(single.get('game_start_utc'))}",
            "Price": american(single["american_price"]), "Priced at": et_time(single.get("price_captured_at_utc")),
            "Modeled hit chance": percent(single.get("probability"))}


def empty_slots_text(state: dict) -> str | None:
    empty = state["slots"]["empty"]
    if not empty:
        return None
    slot = "slot" if empty == 1 else "slots"
    return f"{empty} of {state['slots']['total']} ticket {slot} empty. {state.get('empty_slot_reason') or ''}".strip()
