"""
The best qualifying +100 option for each person (player or goalie) with a priced prop today.

A person's option is, in order of preference:
  1. a SINGLE: one sportsbook-quoted leg of that person at +100 or better that passes the ticket policy
     (the policy that picks the automatic tickets: estimated EV >= 5% at the model probability, EV >= 0 after the
     3-point probability haircut);
  2. otherwise a two-leg CROSS-GAME parlay: one of the person's legs plus one companion leg from a different game,
     combined price (the product of the two legs' own prices, an ESTIMATE, not a quoted parlay price) +100 or better,
     passing the same policy.
Longer combinations are not offered: every leg beyond two lowers the hit chance for little extra value, and the policy's
own rule is to add legs only when no shorter ticket works, which two legs always can when any exists for this pool.

Several people can have the same best option (a companion leg is shared). Options are de-duplicated by their legs, and
each distinct option lists everyone it is best for, so the same ticket is never shown twice under different names.

Nothing here records anything. Recording is the explicit manual action in operational/manual_orders.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict

from research.real_market_parlay import engine as rmp

COMPANION_POOL = 60                 # best companion legs considered per person (by haircut EV)
PERSON_LEGS = 4                     # a person's best legs tried as the parlay anchor
STAKE = 10.0

SINGLE = "SINGLE"
PARLAY = "PARLAY"
PRICE_QUOTED = "SPORTSBOOK_QUOTE"
PRICE_ESTIMATED = "ESTIMATED"

REASON_NO_PRICE = "NO_FRESH_PRICED_LEG"
REASON_NO_EDGE = "NO_LEG_BEATS_ITS_PRICE"
REASON_NO_COMPANION = "NO_COMPANION_REACHES_PLUS_100_WITH_POSITIVE_VALUE"


def option_id(et_date: str, legs) -> str:
    identities = sorted(f"{l.game_id}:{l.participant_id}:{l.market_family}:{l.threshold}:{l.side}" for l in legs)
    return "O" + hashlib.sha256(f"{et_date}|{'|'.join(identities)}".encode()).hexdigest()[:14].upper()


def leg_card(l: rmp.ParlayLeg) -> dict:
    d = rmp.leg_decimal(l)
    return {"label": rmp.leg_label(l), "market_family": l.market_family, "game_id": l.game_id,
            "participant_id": l.participant_id, "participant_name": l.participant_name, "threshold": l.threshold,
            "side": l.side, "team": l.team, "opponent": l.opponent, "game_start_utc": l.game_start_utc,
            "american_price": l.american_price, "decimal_price": round(d, 4), "probability": round(l.conservative_probability, 4),
            "quote_updated_utc": l.quote_updated_utc, "retrieved_at_utc": l.retrieved_at_utc,
            "quote_age_min": l.quote_age_min, "model_version": l.model_version}


def _passes(legs: list[rmp.ParlayLeg]) -> rmp.ParlayResult | None:
    combo = rmp._evaluate_combo(legs)
    return combo if combo is not None and rmp.ticket_passes_policy(combo) else None


def _rationale(kind: str, combo: rmp.ParlayResult) -> str:
    implied = 1.0 / combo.combined_decimal
    if kind == SINGLE:
        return (f"Model chance {combo.joint_probability:.0%} against {implied:.0%} implied by the quoted price. Value after "
                f"lowering the probability by {combo.leg_probability_margin:.0%} points: {combo.ev_conservative:+.1%} "
                "(a policy margin, not a calibration).")
    return (f"No single leg of this person reaches +100 with value, so this pairs one with a leg from another game. "
            f"Estimated hit chance {combo.joint_probability:.0%} against {implied:.0%} implied; value after the "
            f"{combo.leg_probability_margin:.0%}-point haircut {combo.ev_conservative:+.1%}.")


def _option_from_combo(kind: str, combo: rmp.ParlayResult, et_date: str) -> dict:
    stake = STAKE
    return {
        "option_id": option_id(et_date, combo.legs), "kind": kind,
        "legs": [leg_card(l) for l in combo.legs],
        "price_basis": PRICE_QUOTED if kind == SINGLE else PRICE_ESTIMATED,
        "price_label": ("DraftKings quote (US feed)" if kind == SINGLE
                        else "Estimated: product of each leg's own price, not a quoted parlay price"),
        "combined_decimal": round(combo.combined_decimal, 4), "combined_american": round(combo.estimated_combo_price),
        "hit_probability": round(combo.joint_probability, 4), "stake": stake,
        "potential_return": round(stake * combo.combined_decimal, 2), "potential_profit": round(stake * (combo.combined_decimal - 1), 2),
        "ev_estimated": round(combo.ev_estimated, 4), "ev_after_haircut": round(combo.ev_conservative, 4),
        "haircut_margin": combo.leg_probability_margin, "rationale": _rationale(kind, combo),
        "oldest_quote_age_min": max((l.quote_age_min or 0.0) for l in combo.legs),
        "earliest_start_utc": min((l.game_start_utc for l in combo.legs if l.game_start_utc), default=None),
    }


def build_options(legs: list[rmp.ParlayLeg], et_date: str) -> dict:
    """{"options": [distinct options, each with "best_for"], "persons": {person_id: {...}}, "diagnostics": {...}}"""
    priced = [l for l in legs if l.market_family != "MONEYLINE" and rmp.leg_is_eligible(l)]
    pool = rmp.dedupe_legs_by_economic_identity(priced)
    with_edge = [l for l in pool if rmp.leg_has_edge(l)]
    by_person: dict[str, list] = defaultdict(list)
    for l in pool:
        by_person[l.participant_id].append(l)
    companions = sorted(with_edge, key=lambda l: -(l.conservative_probability - rmp.LEG_PROBABILITY_MARGIN) * rmp.leg_decimal(l))[:COMPANION_POOL]

    persons: dict[str, dict] = {}
    distinct: dict[str, dict] = {}
    for pid, plegs in by_person.items():
        name = plegs[0].participant_name
        best_single = None
        for l in plegs:
            combo = _passes([l])
            if combo and (best_single is None or combo.ev_conservative > best_single.ev_conservative):
                best_single = combo
        chosen, kind, reason = best_single, SINGLE, None
        if chosen is None:
            edge_legs = sorted([l for l in plegs if rmp.leg_has_edge(l)],
                               key=lambda l: -(l.conservative_probability - rmp.LEG_PROBABILITY_MARGIN) * rmp.leg_decimal(l))[:PERSON_LEGS]
            if not edge_legs:
                reason = REASON_NO_EDGE
            else:
                best = None
                for a in edge_legs:
                    for b in companions:
                        if b.game_id == a.game_id or b.participant_id == a.participant_id:
                            continue
                        combo = _passes([a, b])
                        if combo and (best is None or combo.ev_conservative > best.ev_conservative):
                            best = combo
                chosen, kind = best, PARLAY
                reason = REASON_NO_COMPANION if best is None else None
        entry = {"person_id": pid, "name": name, "team": plegs[0].team, "priced_legs": len(plegs), "option_id": None,
                 "reason_no_option": reason}
        if chosen is not None:
            oid = option_id(et_date, chosen.legs)
            entry["option_id"] = oid
            opt = distinct.get(oid)
            if opt is None:
                opt = distinct[oid] = {**_option_from_combo(kind, chosen, et_date), "best_for": []}
            opt["best_for"].append({"person_id": pid, "name": name})
        persons[pid] = entry
    options = sorted(distinct.values(), key=lambda o: (o["kind"] != SINGLE, -o["ev_after_haircut"]))
    diagnostics = {
        "legs_offered": len(legs), "priced_prop_legs": len(priced), "legs_after_dedupe": len(pool), "legs_beating_price": len(with_edge),
        "people_with_priced_props": len(by_person), "people_with_option": sum(1 for p in persons.values() if p["option_id"]),
        "singles": sum(1 for o in options if o["kind"] == SINGLE), "parlays": sum(1 for o in options if o["kind"] == PARLAY),
        "no_option_reasons": {r: sum(1 for p in persons.values() if p["reason_no_option"] == r)
                              for r in (REASON_NO_PRICE, REASON_NO_EDGE, REASON_NO_COMPANION)},
        "companion_pool": len(companions),
    }
    return {"date_et": et_date, "options": options, "persons": persons, "diagnostics": diagnostics,
            "policy": {"min_combined_decimal": rmp.MIN_COMBINED_DECIMAL, "min_estimated_ev": rmp.MIN_ESTIMATED_EV,
                       "leg_probability_margin": rmp.LEG_PROBABILITY_MARGIN, "max_legs_offered": 2, "stake": STAKE}}
