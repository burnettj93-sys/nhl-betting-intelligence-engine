"""Review cards for Eggy's own legs: the legs of today's official pick and of the best candidate on the Slate Board.

Eggy's SELECTION is unchanged by this (the policy digest is not touched, and nothing here adds, removes or re-ranks a leg): this only attaches, to each leg dict in the Slate Board document, the same review
card the learning book records with every bet (operational/eggy_intelligence.py), so that opening any Eggy leg shows why this player or team, why tonight, why this market, why this price, what the model and
the market each believe, what argues against it, and what would make Eggy pass. A leg the review would PASS on is shown as such on its card: the owner sees the disagreement; nothing is silenced.

No new data is read: the review uses the files the platform already holds. When the review cannot run (a data file missing), the legs are simply left without a card.
"""
from __future__ import annotations

import datetime as dt

from operational import bet_labels
from operational import eggy_intelligence as ei


def slim(card: dict) -> dict:
    """A card for a published document: rounded, and without `answers` (they are rebuilt from the card by `eggy_intelligence.standard_answers`, so storing them again only grows the snapshot)."""
    return {k: v for k, v in ei.compact(card).items() if k != "answers"}


def _games(product: dict | None) -> dict:
    out = {}
    for g in (((product or {}).get("games") or {}).get("games")) or []:
        if g.get("game_id") is not None and g.get("home") and g.get("away"):
            out[str(g["game_id"])] = {"home": g["home"], "away": g["away"], "start_utc": g.get("start_utc")}
    return out


def leg_from_dict(l: dict, games: dict):
    """(ParlayLeg, estimate, basis) for a leg dict of the official ticket (a frozen leg) or of a candidate, or None when it cannot be reviewed (no price, no market, no game)."""
    from research.real_market_parlay.engine import ParlayLeg
    family = l.get("market_family") or bet_labels.CODE_FAMILY.get(l.get("code")) or bet_labels.FAMILY_OF_KEY.get(l.get("market_key"))
    price = l.get("american_price")
    if price is None and isinstance(l.get("price"), dict):
        price = l["price"].get("american")
    gid = str(l.get("game_id") or "")
    name = l.get("participant_name") or l.get("participant") or l.get("team")
    if not family or price is None or gid not in games or not name:
        return None
    g = games[gid]
    team = l.get("team")
    opp = l.get("opponent") or ((g["away"] if team == g["home"] else g["home"]) if team in (g["home"], g["away"]) else None)
    est = l.get("probability") if l.get("probability") is not None else l.get("estimate")
    if est is None:
        return None
    threshold = l.get("threshold")
    if threshold is None and family not in ("MONEYLINE", "PLAYER_GOALS"):
        threshold = bet_labels.threshold_from_label(l.get("label"))
    if family == "PLAYER_GOALS" and threshold is None:
        threshold = 1
    if isinstance(threshold, str):
        threshold = int(threshold.replace("+", ""))
    ev = l.get("evidence") or {}
    moneyline = family == "MONEYLINE"
    pid = str(l.get("participant_id") or l.get("player_id") or (team if moneyline else ""))
    leg = ParlayLeg(game_id=gid, event_id=None, market_family=family, participant_id=pid, participant_name=name, side=(("HOME" if team == g["home"] else "AWAY") if moneyline else (l.get("side") or "OVER")),
                    threshold=threshold, american_price=float(price), conservative_probability=float(est), sportsbook="DRAFTKINGS", captured_at_utc=l.get("price_captured_at_utc") or (l.get("price") or {}).get("quote_updated_utc"),
                    provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent=opp, game_start_utc=g.get("start_utc"),
                    model_version=l.get("model_version") or "", quote_updated_utc=l.get("quote_updated_utc") or (l.get("price") or {}).get("quote_updated_utc"),
                    model_probability=(l.get("model_probability") if l.get("model_probability") is not None else ev.get("model")), empirical_probability=ev.get("recent_hit_rate"),
                    empirical_hits=ev.get("hits"), empirical_games=ev.get("games"), market_probability=(float(est) if moneyline else None))
    return leg, float(est), (l.get("estimate_basis") or ("MARKET_NO_VIG" if moneyline else "TICKET_ESTIMATE"))


def review_legs(ctx: ei.Context, legs: list[dict]) -> int:
    """Attach `review` to each reviewable leg dict in place; returns how many were reviewed. A leg that cannot be reviewed is left as it was."""
    games = ctx.games
    n = 0
    for l in legs:
        got = leg_from_dict(l, games)
        if got is None:
            continue
        leg, est, basis = got
        l["review"] = slim(ei.review_leg(ctx, leg, basis, est))
        n += 1
    return n


def attach(now: dt.datetime, product: dict | None, primary: dict | None, eggy: dict | None) -> dict:
    """Review the legs of today's official ticket and of the best candidate (not every candidate: the Slate Board document stays small). Never raises: the board never waits on a review."""
    try:
        legs = list(((primary or {}).get("ticket") or {}).get("legs") or []) + list(((eggy or {}).get("best_candidate") or {}).get("legs") or [])
        if not legs:
            return {"reviewed": 0, "reason": "no ticket or candidate legs today"}
        ctx = ei.build_context(None, now)
        ctx.games = _games(product)
        ok, why = ei.usable(ctx)
        if not ok:
            return {"reviewed": 0, "reason": f"the review could not run: {why}"}
        return {"reviewed": review_legs(ctx, legs)}
    except Exception as exc:  # noqa: BLE001
        return {"reviewed": 0, "reason": f"{exc.__class__.__name__}: {exc}"}
