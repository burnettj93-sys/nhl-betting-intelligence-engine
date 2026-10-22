"""Learning parlays: the learning unit is a PARLAY built from the intelligent candidate legs the review already produced (docs/EGGY_INTELLIGENCE.md). A minimal conversion: no new scoring framework, no new data,
no pricing system. Pure logic; operational/learning_cohort.py records, settles and publishes what this module constructs.

The question it asks: can several individually strong, repeatable outcomes be combined into a worthwhile ticket without one weak leg ruining the construction?

* A LEG is eligible only if its own review survived (RECORD or LOWER_PRIORITY, contradiction LOW or MODERATE, calibration at least MEDIUM) and the event is a likely one (estimate at least MIN_LEG_P): no
  lottery legs, and no leg is added merely to lengthen the payout.
* 3 legs are preferred. A 4th is added only if it independently passes the same review, is at least as strong as the ticket's current weakest leg, and adds no strong dependence.
* DEPENDENCE comes from operational/eggy_candidates.classify_pair (directional knowledge only). Legs in different games are ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE (an assumption, not a finding). Two legs of one game are never multiplied: the combined probability is
  then NOT given (None); only assumption-free Frechet bounds are, and the ticket is labelled UNCERTAIN. Strongly dependent or redundant pairs (one player twice, a goal and a point) are refused.
* PRICE. The system has no combined sportsbook price for any parlay, so every parlay here is an UNPRICED LEARNING PARLAY. The product of the legs' quoted prices is kept as a labelled REFERENCE (never a
  quote, never an odds, no return or ROI is ever computed from it). The ticket is settled by its legs and records whether the whole ticket would have hit.
* WEAKEST LEG: the leg with the lowest estimate (ties: the higher contradiction). It sets the ticket's quality: GOOD needs it at MIN_GOOD_WEAKEST or better.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter

from operational import eggy_candidates as ec
from operational import eggy_intelligence as ei
from research.real_market_parlay.engine import leg_decimal
from operational.bet_labels import bet_label

MIN_LEG_P = 0.55                 # an individually likely event (design choice; the pool's -150 floor means most legs sit between 55% and 70%)
MIN_GOOD_WEAKEST = 0.62          # the weakest leg of a GOOD ticket
MIN_FOURTH_P = 0.60
POOL_LEGS = 14                   # the strongest eligible legs considered: keeps the search small and every constructed ticket made of the best of the day
MAX_PARLAYS_PER_DAY = 10
LEG_REUSE = 2                    # a leg appears in at most this many of the day's parlays
MAX_SAME_GAME = 3
QUALITY = ("GOOD", "QUESTIONABLE", "BAD")
CODE = {"PLAYER_SOG_ALTERNATE": "SOG", "PLAYER_SOG": "SOG", "PLAYER_POINTS": "PTS", "PLAYER_GOALS": "GOAL", "MONEYLINE": "ML", "GOALIE_SAVES": "SAVES"}
LEVEL = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "SEVERE": 3}


PRICE_FLAGS = ("PRICE_ABOVE_ESTIMATE", "MODEL_FAR_ABOVE_MARKET", "LONG_PRICE", "LONGSHOT_TAIL")          # about the straight bet's VALUE: a parlay leg is judged on the hockey and the player, and the ticket on its construction


def leg_ok(c: dict) -> tuple[bool, str]:
    """Does this reviewed candidate ({'leg','card','action',...}) qualify as a PARLAY leg? The hockey/player review decides, not the straight bet's price: a likely leg priced shorter than our conservative estimate
    (every high-frequency leg is) is exactly what a parlay is made of, so the price-versus-estimate flags are set aside and the contradiction is recomputed from the rest. A PASS for any other reason stays a PASS."""
    card = c["card"]
    if card.get("kind") == "ERROR":
        return False, "the review failed"
    price_only_pass = c["action"] == "PASS" and card["kind"] == "PLAYER" and card["thesis"]["type"] == "PRICE_ABOVE_ESTIMATE"
    if c["action"] not in ("RECORD", "LOWER_PRIORITY") and not price_only_pass:
        return False, "the leg's own review passes"
    level = ei.combine_contradiction([f for f in card["contradiction"]["flags"] if f["name"] not in PRICE_FLAGS])
    if LEVEL[level] > 1:
        return False, f"contradiction {level} (price flags set aside)"
    if card["calibration_confidence"]["level"] not in ("HIGH", "MEDIUM") or card["data_completeness"]["fraction"] < 0.5:
        return False, "calibration below MEDIUM or too little data"
    if float(card["estimate_used"]) < MIN_LEG_P:
        return False, f"estimate {float(card['estimate_used']):.0%} is under {MIN_LEG_P:.0%}: not a likely outcome"
    return True, ""


def _pair_view(l) -> dict:
    return {"code": CODE.get(l.market_family, "OTHER"), "label": bet_label(l.participant_name, l.market_family, l.threshold, l.american_price), "team": l.team,
            "player_id": None if l.market_family == "MONEYLINE" else str(l.participant_id), "participant": l.participant_name}


def pairs_of(legs: list) -> list[dict]:
    """Every pair of legs with what is known about how they move together: different games are ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE; one game's legs go through classify_pair (directional only)."""
    out = []
    for a, b in itertools.combinations(legs, 2):
        if str(a.game_id) != str(b.game_id):
            out.append({"a": bet_label(a.participant_name, a.market_family, a.threshold, a.american_price), "b": bet_label(b.participant_name, b.market_family, b.threshold, b.american_price),
                        "kind": "DIFFERENT_GAMES", "direction": "NONE", "strength": "assumed_independent", "note": "legs in different games: ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE"})
        else:
            out.append(ec.classify_pair(_pair_view(a), _pair_view(b)))
    return out


def _bounds(legs: list, ests: list[float]) -> dict:
    """Assumption-free bounds on the joint probability: Frechet within each game's legs, multiplied across games (different games: ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE)."""
    low = high = 1.0
    by_game: dict = {}
    for l, e in zip(legs, ests):
        by_game.setdefault(str(l.game_id), []).append(e)
    for es in by_game.values():
        b = ec.frechet(es)
        low *= b["low"]
        high *= b["high"]
    return {"low": round(low, 4), "high": round(high, 4), "basis": "Frechet bounds (true for ANY dependence within a game), multiplied across games; not an estimate"}


def construct(cands: list[dict]) -> dict | None:
    """One parlay from candidate dicts, or None if the legs cannot make a legitimate ticket (a duplicate player or exposure, or a strongly dependent / redundant pair)."""
    legs = [c["leg"] for c in cands]
    ests = [float(c["card"]["estimate_used"]) for c in cands]
    if len({(str(l.game_id), str(l.participant_id)) for l in legs}) < len(legs):
        return None
    if len({str(l.participant_id) for l in legs if l.market_family != "MONEYLINE"}) < sum(1 for l in legs if l.market_family != "MONEYLINE"):
        return None                                                                    # one player twice (two thresholds, shots and points): redundant by construction
    if Counter(str(l.game_id) for l in legs if l.market_family == "MONEYLINE").most_common(1) and Counter(str(l.game_id) for l in legs if l.market_family == "MONEYLINE").most_common(1)[0][1] > 1:
        return None                                                                    # both sides of one moneyline can never both win
    if Counter(str(l.game_id) for l in legs).most_common(1)[0][1] > MAX_SAME_GAME:
        return None
    pairs = pairs_of(legs)
    if any(p["strength"] in ("strong", "total") for p in pairs):
        return None
    same_game = [p for p in pairs if p["kind"] != "DIFFERENT_GAMES"]
    independent = not same_game
    if independent:
        combined, basis = round(_product(ests), 4), "PRODUCT_OF_LEG_ESTIMATES (every leg in a different game: ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE)"
    else:
        combined, basis = None, "NOT_GIVEN: legs share a game and their dependence is not quantified"
    dep_level = "ASSUMED_INDEPENDENT" if independent else ("UNCERTAIN" if any(p["direction"] == "UNCLEAR" for p in same_game) else "POSITIVE_NOT_QUANTIFIED")
    wi = min(range(len(legs)), key=lambda i: (ests[i], -LEVEL[cands[i]["card"]["contradiction"]["level"]]))
    weakest = {"index": wi, "label": bet_label(legs[wi].participant_name, legs[wi].market_family, legs[wi].threshold, legs[wi].american_price), "estimate": round(ests[wi], 4),
               "contradiction": ei.combine_contradiction([f for f in cands[wi]["card"]["contradiction"]["flags"] if f["name"] not in PRICE_FLAGS])}
    levels = [LEVEL[ei.combine_contradiction([f for f in c["card"]["contradiction"]["flags"] if f["name"] not in PRICE_FLAGS])] for c in cands]
    if ests[wi] < MIN_LEG_P or max(levels) > 1:
        quality = "BAD"
    elif ests[wi] >= MIN_GOOD_WEAKEST and sum(levels) <= 1 and dep_level in ("ASSUMED_INDEPENDENT", "POSITIVE_NOT_QUANTIFIED"):
        quality = "GOOD"
    else:
        quality = "QUESTIONABLE"
    ref = 1.0
    for l in legs:
        ref *= leg_decimal(l)
    key = hashlib.sha256("|".join(sorted(c["key"] for c in cands)).encode()).hexdigest()
    why = []
    if quality != "GOOD":
        if ests[wi] < MIN_GOOD_WEAKEST:
            why.append(f"the weakest leg ({weakest['label']}) is {ests[wi]:.0%}, under the {MIN_GOOD_WEAKEST:.0%} a GOOD ticket needs")
        if sum(levels) > 1:
            why.append("more than one leg carries a MODERATE contradiction")
        if dep_level == "UNCERTAIN":
            why.append("legs in one game with unclear dependence")
    return {"key": key, "n_legs": len(legs), "cands": cands, "legs": legs, "estimates": [round(e, 4) for e in ests], "weakest_leg": weakest, "construction_quality": quality, "quality_reasons": why,
            "action": "PASS" if quality == "BAD" else "RECORD", "dependence_level": dep_level, "pairs": pairs, "combined_probability": combined, "probability_basis": basis,
            "joint_bounds": _bounds(legs, ests), "ranking_probability": round(_product(ests), 4),
            "price": {"status": "UNPRICED_LEARNING_PARLAY", "reference_decimal": round(ref, 4), "reference_note": "the product of the legs' own quoted prices: a REFERENCE only, not a sportsbook price; no return or ROI is claimed",
                      "plus_money_reference": ref >= 2.0}}


def _product(xs) -> float:
    p = 1.0
    for x in xs:
        p *= x
    return p


def rank_key(p: dict) -> tuple:
    """Quality first, then FEWER legs (3 preferred over 4), then the strongest weakest leg, then the likelier ticket (an ORDERING only: for dependent legs it is not a probability), independence, key."""
    return (QUALITY.index(p["construction_quality"]), p["n_legs"], -p["weakest_leg"]["estimate"], -p["ranking_probability"], {"ASSUMED_INDEPENDENT": 0, "POSITIVE_NOT_QUANTIFIED": 1, "UNCERTAIN": 2}[p["dependence_level"]], p["key"])


def build(cands: list[dict], already: list[dict] | None = None, slots: int = MAX_PARLAYS_PER_DAY) -> dict:
    """Up to `slots` parlays from the day's reviewed candidates. `already` are today's recorded parlays ({'key','leg_keys'}) whose legs count against the reuse cap. Fewer than `slots` is a correct answer."""
    ok, rejected = [], Counter()
    for c in cands:
        good, why = leg_ok(c)
        if good:
            ok.append(c)
        else:
            rejected[why] += 1
    ok.sort(key=lambda c: c["rank_key"])
    pool = ok[:POOL_LEGS]
    built = []
    for combo in itertools.combinations(pool, 3):
        p = construct(list(combo))
        if p:
            built.append(p)
            for extra in pool:                                              # a 4th leg only if it is as strong as the weakest and passes everything the others did
                if extra in combo or float(extra["card"]["estimate_used"]) < max(MIN_FOURTH_P, p["weakest_leg"]["estimate"]):
                    continue
                q = construct(list(combo) + [extra])
                if q and q["construction_quality"] == p["construction_quality"] and q["action"] == "RECORD":
                    built.append(q)
    built.sort(key=rank_key)
    used: Counter = Counter()
    kept_sets = [set(a["leg_keys"]) for a in already or []]
    for a in already or []:
        used.update(a["leg_keys"])
    have = {a["key"] for a in already or []}
    chosen = []
    for p in built:
        if len(chosen) + len(kept_sets) >= slots or p["action"] != "RECORD" or p["key"] in have:
            continue
        ks = {c["key"] for c in p["cands"]}
        if any(used[k] >= LEG_REUSE for k in ks) or any(len(ks & other) >= min(len(ks), len(other)) - 1 for other in kept_sets):
            continue                                                         # a leg is used at most twice, and two tickets differ by at least two legs
        chosen.append(p)
        kept_sets.append(ks)
        used.update(ks)
    return {"parlays": chosen, "eligible_legs": len(ok), "legs_not_eligible": dict(rejected), "constructed": len(built), "pool": len(pool)}


def flagship(parlays: list[dict]) -> dict | None:
    """Eggy's flagship: the strongest qualifying construction from the same pool (rank_key order), at most one. A GOOD ticket only; None is a correct answer."""
    best = [p for p in sorted(parlays, key=rank_key) if p["action"] == "RECORD" and p["construction_quality"] == "GOOD"]
    return best[0] if best else None


def record_dict(p: dict) -> dict:
    """The ticket as stored: frozen legs with the review card each was reviewed with, dependence, weakest leg, quality, the unpriced reference."""
    import dataclasses
    from research.real_market_parlay.engine import ParlayLeg  # noqa: F401
    legs = [{"leg": dataclasses.asdict(c["leg"]), "key": c["key"], "card": ei.compact(c["card"]), "action": c["action"], "estimate": float(c["card"]["estimate_used"])} for c in p["cands"]]
    return {"legs": legs, "dependence": {"level": p["dependence_level"], "pairs": p["pairs"], "joint_bounds": p["joint_bounds"]}, "weakest_leg": p["weakest_leg"], "quality_reasons": p["quality_reasons"]}


def settle(leg_results: list[dict]) -> dict:
    """HIT / MISS / VOID / UNRESOLVED / PENDING from each leg's result; names the leg(s) that killed it. Never a price, return or ROI."""
    outs = [r["outcome"] for r in leg_results]
    killed = [r["label"] for r in leg_results if r["outcome"] == "LOSS"]
    if killed:
        return {"result": "MISS", "legs_hit": outs.count("WIN"), "killed_by": killed}
    if any(o in (None, "PENDING") for o in outs):
        return {"result": "PENDING", "legs_hit": outs.count("WIN"), "killed_by": []}
    if any(o == "UNRESOLVED" for o in outs):
        return {"result": "UNRESOLVED", "legs_hit": outs.count("WIN"), "killed_by": []}
    if all(o == "VOID" for o in outs):
        return {"result": "VOID", "legs_hit": 0, "killed_by": []}
    return {"result": "HIT", "legs_hit": outs.count("WIN"), "killed_by": []}          # every leg won (voided legs drop out of the ticket, as the existing parlay settlement rules say)
