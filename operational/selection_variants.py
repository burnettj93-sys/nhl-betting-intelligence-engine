"""
Policy variants for the automatic ticket selector, applied to a stored candidate set.

These are the rule sets under review after the 2026-10 losing-streak postmortem (docs/POSTMORTEM_2026-10-10.md). They are written down as DATA so that (a) the postmortem replays the
original decisions with each of them and (b) the shadow log (operational/shadow_selection.py) records, while automatic recording is paused, what each would pick from the live pool --
with every number frozen before the games are played. None of them is the policy in force: the policy in force is research/real_market_parlay/engine.py, unchanged.

The numbers are round and tied to the stated objective ("a strong estimated chance of hitting"), the measured model error (player-level error of 8-10 points, docs/SELECTOR_AUDIT.md) and
exposure (one player, one ticket). They were fixed before any replacement ticket's result was looked at and are not fitted to the nine losses.
"""
from __future__ import annotations

from research.real_market_parlay import policy as _policy

VARIANTS = {
    "rules_on_oct_8": {"label": "Rules in force on 2026-10-08 (no per-player limit)", "leg": 2, "game": 3, "player": None, "floor": 0.0, "shade": 0.03, "slots": 5},
    "current_code": {"label": "Rules in the code today (at most 2 tickets per player)", "leg": 2, "game": 3, "player": 2, "floor": 0.0, "shade": 0.03, "slots": 5},
    "floor_25": {"label": "Today's rules + estimated hit chance of at least 25%", "leg": 2, "game": 3, "player": 2, "floor": 0.25, "shade": 0.03, "slots": 5},
    "floor_30": {"label": "Today's rules + estimated hit chance of at least 30%", "leg": 2, "game": 3, "player": 2, "floor": 0.30, "shade": 0.03, "slots": 5},
    "margin_5pt": {"label": "Today's rules + the edge must survive a 5-point haircut on every leg", "leg": 2, "game": 3, "player": 2, "floor": 0.0, "shade": 0.05, "slots": 5},
    "one_per_player": {"label": "Today's rules + each player on at most one ticket", "leg": 2, "game": 3, "player": 1, "floor": 0.0, "shade": 0.03, "slots": 5},
    "proposed_restart": {"label": "THE PROPOSED RESTART POLICY: hit chance of at least 25%, edge of at least +3% after the 3-point haircut, one ticket per player, two per game, up to five a day (not validated, not approved)",
                         "leg": _policy.PROPOSED.max_per_leg, "game": _policy.PROPOSED.max_per_game, "player": _policy.PROPOSED.max_per_player, "floor": _policy.PROPOSED.floor,
                         "shade": _policy.PROPOSED.value_shade, "min_value": _policy.PROPOSED.min_value_after_shade, "slots": _policy.PROPOSED.slots},
    "earlier_draft_30_and_5pt": {"label": "The earlier draft (30% floor + 5-point haircut + one per player, two per game): too strict to be practical on entry information (kept for the record)",
                                 "leg": 1, "game": 2, "player": 1, "floor": 0.30, "shade": 0.05, "min_value": 0.0, "slots": 5},
}


def dec(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def candidates_from_legs(legs: list) -> list[dict]:
    """Every ticket the live engine's policy qualifies from these legs (research/real_market_parlay/engine.py), as plain dicts the variants can filter."""
    from research.real_market_parlay import engine as rmp
    pool = rmp._prepare_pool(legs)
    out = []
    for c in rmp._qualifying_tickets(pool):
        out.append({"legs": [{"id": f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}", "game": str(l.game_id), "player": str(l.participant_id), "market": l.market_family,
                              "p": l.conservative_probability, "name": l.participant_name, "threshold": l.threshold, "price": l.american_price, "label": rmp.leg_label(l)} for l in c.legs],
                    "p": c.joint_probability, "price": c.estimated_combo_price, "ev": c.ev_estimated, "ev_haircut": c.ev_conservative})
    return out


def shaded_probability(c: dict, shade: float) -> float:
    p = 1.0
    for l in c["legs"]:
        p *= max(l["p"] - shade, 0.0)
    return p


def select(cands: list[dict], rule: dict) -> list[dict]:
    """The selector's loop (research/real_market_parlay/engine.py::select_tickets): rank by estimated hit chance, then EV; skip a ticket that breaks the leg, game or player
    limit; stop at the day's slots. `floor` and `shade` are the only things the variants add to the live rules."""
    ok = [c for c in cands if c["p"] >= rule["floor"] and shaded_probability(c, rule["shade"]) * dec(c["price"]) - 1.0 >= rule.get("min_value", 0.0)]
    ok.sort(key=lambda c: (-c["p"], -c["ev"], len(c["legs"])))
    picked, leg_use, game_use, player_use = [], {}, {}, {}
    for c in ok:
        if len(picked) >= rule["slots"]:
            break
        ids = [l["id"] for l in c["legs"]]
        if any(leg_use.get(i, 0) >= rule["leg"] for i in ids):
            continue
        if any(game_use.get(l["game"], 0) >= rule["game"] for l in c["legs"]):
            continue
        players = {(l["game"], l["player"]) for l in c["legs"]}
        if rule["player"] is not None and any(player_use.get(pl, 0) >= rule["player"] for pl in players):
            continue
        picked.append(c)
        for i in ids:
            leg_use[i] = leg_use.get(i, 0) + 1
        for l in c["legs"]:
            game_use[l["game"]] = game_use.get(l["game"], 0) + 1
        for pl in players:
            player_use[pl] = player_use.get(pl, 0) + 1
    return picked
