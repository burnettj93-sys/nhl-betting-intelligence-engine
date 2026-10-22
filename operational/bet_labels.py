"""The complete name of a bet, the same everywhere it is shown (owner rule 2026-10-21):

    PLAYER — MARKET THRESHOLD — PRICE        'Filip Chytil — SHOTS ON GOAL 2+ — -130'      'Jamie Oleksiak — GOALS 1+ — +2500'      'VAN — MONEYLINE — +240'

Never 'Chytil 2+'. The market comes from the bet's own market family, which comes from the market key the provider's archived payload actually carried; it is never inferred from a price or a
threshold. Standard library only: the hosted dashboard imports this module.
"""
from __future__ import annotations

import re

MARKET_NAMES = {"MONEYLINE": "MONEYLINE", "PLAYER_SOG_ALTERNATE": "SHOTS ON GOAL", "PLAYER_SOG": "SHOTS ON GOAL", "GOALIE_SAVES": "SAVES", "PLAYER_POINTS": "POINTS", "PLAYER_GOALS": "GOALS",
                "PLAYER_ASSISTS": "ASSISTS", "PUCK_LINE": "PUCK LINE"}
MARKET_KEYS = {"MONEYLINE": "h2h", "PLAYER_SOG_ALTERNATE": "player_shots_on_goal_alternate", "PLAYER_SOG": "player_shots_on_goal", "GOALIE_SAVES": "player_total_saves", "PLAYER_POINTS": "player_points",
               "PLAYER_GOALS": "player_goal_scorer_anytime", "PUCK_LINE": "spreads"}
FAMILY_OF_KEY = {v: k for k, v in MARKET_KEYS.items()}
CODE_FAMILY = {"SOG": "PLAYER_SOG_ALTERNATE", "PTS": "PLAYER_POINTS", "GOAL": "PLAYER_GOALS", "SAVES": "GOALIE_SAVES", "ML": "MONEYLINE"}


def market_name(family: str, threshold=None) -> str:
    """'SHOTS ON GOAL 2+', 'GOALS 1+', 'POINTS 2+', 'SAVES 25+', 'MONEYLINE'. An unknown family is shown as itself (never guessed)."""
    base = MARKET_NAMES.get(family, str(family).replace("_", " "))
    if family == "MONEYLINE" or threshold is None:
        return base
    if family == "PUCK_LINE":
        return f"{base} {float(threshold):+.1f}"
    return f"{base} {_int(threshold)}+"


def bet_label(name: str, family: str, threshold=None, price=None) -> str:
    """'Filip Chytil — SHOTS ON GOAL 2+ — -130'. The price is left off when not given."""
    text = f"{name} — {market_name(family, threshold)}"
    if price is not None:
        text += f" — {float(price):+.0f}"
    return text


def _int(v) -> int:
    return int(float(str(v).replace("+", "").strip()))


def threshold_from_label(label: str | None) -> int | None:
    """A ticket leg frozen before thresholds were stored carries it only inside its label ('Filip Chytil 2+ shots on goal', 'X to score a goal (anytime)')."""
    m = re.search(r"(\d+)\+", label or "")
    if m:
        return int(m.group(1))
    return 1 if "anytime" in (label or "") else None


def leg_dict_label(leg: dict, price=None) -> str:
    """bet_label for a leg dict of any surface (a ticket leg, a candidate leg, a ledger leg). The family is the leg's own (`market_family`, else its market key, else its candidate code); the threshold is the
    leg's own or, for an older frozen leg, the one in its label; the price is the leg's recorded or quoted one. A leg that names no market falls back to its own label, never to a guess."""
    fam = leg.get("market_family") or FAMILY_OF_KEY.get(leg.get("market_key")) or CODE_FAMILY.get(leg.get("code"))
    name = leg.get("participant_name") or leg.get("participant") or leg.get("name")
    if not name and fam == "MONEYLINE":
        name = leg.get("team")                    # a team is the participant of a moneyline only: for any other market the team is NOT the bet's name
    if not name or not fam:
        return str(leg.get("label") or name or "unknown bet")
    threshold = leg.get("threshold")
    if threshold is None and fam not in ("MONEYLINE", "PLAYER_GOALS"):
        threshold = threshold_from_label(leg.get("label"))
    if fam == "PLAYER_GOALS" and threshold is None:
        threshold = 1
    if isinstance(threshold, str):
        try:
            threshold = _int(threshold)
        except ValueError:
            threshold = None
    p = price
    if p is None:
        p = leg.get("american_price")
    if p is None and isinstance(leg.get("price"), dict):
        p = leg["price"].get("american")
    return bet_label(name, fam, threshold, p)
