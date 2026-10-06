"""Display formatting for the Best Bets (+100 target) section on the Today page
(operational/best_bets.py produces the state). Pure functions -- no I/O, no db --
so the page and the tests share one formatting path."""
from __future__ import annotations

import datetime as dt

from operational import eastern_time as et


def _start_et(start_utc: str) -> str:
    parsed = dt.datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    return parsed.astimezone(et.EASTERN).strftime("%-I:%M %p ET")


def _american(price: int) -> str:
    return f"+{price}" if price > 0 else str(price)


def _leg_text(leg: dict) -> str:
    return f"{leg['player']} {leg['label']}"


def _game_text(leg: dict) -> str:
    where = "vs" if leg["home"] else "@"
    return f"{leg['team']} {where} {leg['opp']} · {_start_et(leg['start_utc'])}"


def format_single(leg: dict) -> dict:
    return {
        "Bet": _leg_text(leg), "Game": _game_text(leg), "Price": _american(leg["price"]),
        "Model hit chance": f"{leg['p'] * 100:.0f}%", "Book implies": f"{leg['implied'] * 100:.0f}%",
        "Edge": f"{leg['edge'] * 100:+.0f} pts", "Price age": f"{leg['price_age_min']:.0f} min",
    }


def format_parlay(parlay: dict) -> dict:
    a, b = parlay["legs"]
    return {
        "Leg 1": f"{_leg_text(a)} ({_american(a['price'])})", "Leg 2": f"{_leg_text(b)} ({_american(b['price'])})",
        "Games": f"{_game_text(a)}  |  {_game_text(b)}", "Combined price": _american(parlay["american"]),
        "Model hit chance": f"{parlay['p'] * 100:.0f}%",
        "Price age": f"{max(a['price_age_min'], b['price_age_min']):.0f} min",
    }


def empty_reason(state: dict) -> str:
    if state.get("games_today_upcoming", 0) == 0:
        return "No upcoming games left today."
    if state.get("events_priced", 0) == 0:
        return ("Prices for today's games are captured shortly before puck drop (about 5 hours out, refreshed near "
                "puck drop) -- none have been captured yet.")
    return ("Real prices are in, but no leg or 2-leg combination at +100 or better clears the model's minimum edge "
            "over DraftKings right now. That is an honest result, not an error.")


def format_state(state: dict | None) -> dict:
    if not state:
        return {"status": "NOT_RUN", "singles": [], "parlays": [], "message": "Best Bets has not run yet."}
    return {
        "status": state.get("status"), "limits": state.get("limits", ""),
        "singles": [format_single(l) for l in state.get("singles", [])],
        "parlays": [format_parlay(p) for p in state.get("parlays", [])],
        "message": "" if (state.get("singles") or state.get("parlays")) else empty_reason(state),
        "generated_at_utc": state.get("generated_at_utc"), "events_priced": state.get("events_priced", 0),
    }
