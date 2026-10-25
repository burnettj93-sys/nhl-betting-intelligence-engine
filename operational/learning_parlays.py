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
from pathlib import Path
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
# The moneyline's model-ABOVE-market flags (operational/eggy_intelligence.py review_moneyline): the same kind of value-versus-price flag as MODEL_FAR_ABOVE_MARKET above, and like it they stay on the
# review card as caution but do not enter the parlay leg's non-price contradiction. A moneyline leg's estimate is the market's own no-vig probability while the model has not beaten the market, so the model
# liking a side MORE than the market does not touch the estimate; counting it would reject a favourite BECAUSE the model sees more value. Independent evidence still counts: MODEL_SAYS_OVERPRICED,
# HOCKEY_PICTURE_AGAINST, calibration below MEDIUM, missing data, back-to-back and every other flag.
MODEL_ABOVE_MARKET_FLAGS = ("MODEL_NOT_SHOWN_TO_BEAT_MARKET", "MARKET_MAY_KNOW_MORE")
SET_ASIDE_FLAGS = PRICE_FLAGS + MODEL_ABOVE_MARKET_FLAGS          # kept for reference; the gate itself uses ei.counted_flags (one registry, operational/eggy_intelligence.py FLAG_LAYER)


def leg_ok(c: dict) -> tuple[bool, str]:
    """Does this reviewed candidate ({'leg','card','action',...}) qualify as a PARLAY leg? The hockey/player review decides, not the straight bet's price: a likely leg priced shorter than our conservative estimate
    (every high-frequency leg is) is exactly what a parlay is made of, so the price-versus-estimate flags are set aside and the contradiction is recomputed from the rest. A PASS for any other reason stays a PASS."""
    card = c["card"]
    if card.get("kind") == "ERROR":
        return False, "the review failed"
    leg = c.get("leg")
    if leg is not None and getattr(leg, "market_family", None) == "PLAYER_POINTS":                         # a points threshold is a live ingredient only where the out-of-time validation allows it
        k = int(getattr(leg, "threshold", 0) or 0)
        if k not in ei.points_policy()["enabled"]:
            return False, f"points {k}+ is not validated for live use (docs/validation/points_thresholds.json)"
    price_only_pass = c["action"] == "PASS" and card["kind"] == "PLAYER" and card["thesis"]["type"] == "PRICE_ABOVE_ESTIMATE"
    if c["action"] not in ("RECORD", "LOWER_PRIORITY") and not price_only_pass:
        return False, "the leg's own review passes"
    level = ei.combine_contradiction(ei.counted_flags(card["contradiction"]["flags"]))
    if LEVEL[level] > 1:
        return False, f"contradiction {level} (price and model-above-market flags set aside)"
    if card["calibration_confidence"]["level"] not in ("HIGH", "MEDIUM") or card["data_completeness"]["fraction"] < 0.5:
        return False, "calibration below MEDIUM or too little data"
    if float(card["estimate_used"]) < MIN_LEG_P:
        return False, f"estimate {float(card['estimate_used']):.0%} is under {MIN_LEG_P:.0%}: not a likely outcome"
    return True, ""


def _pair_view(l) -> dict:
    return {"code": CODE.get(l.market_family, "OTHER"), "label": bet_label(l.participant_name, l.market_family, l.threshold, l.american_price), "team": l.team,
            "player_id": None if l.market_family == "MONEYLINE" else str(l.participant_id), "participant": l.participant_name}



# ------------------------------------------------------------------------------------------------ measured dependence between legs of one game
DEPENDENCE_PATH = Path(__file__).resolve().parent.parent / "docs" / "validation" / "dependence_lift.json"
_dep_cache: dict = {"mtime": None, "data": {}}
_CODE_OF = {"PLAYER_SOG_ALTERNATE": "SOG", "PLAYER_SOG": "SOG", "PLAYER_POINTS": "PTS", "MONEYLINE": "ML"}


def dependence_lifts() -> dict:
    """pair type -> {dependence_lift, ci95, reading, usable} from deploy/validate_dependence.py: how often two legs of one game hit together against what independence predicts, RELATIVE to a cross-game control
    (the model's own calibration bias would otherwise read as dependence). An unreadable file measures nothing."""
    try:
        mt = DEPENDENCE_PATH.stat().st_mtime
        if _dep_cache["mtime"] != mt:
            _dep_cache.update(mtime=mt, data=json.loads(DEPENDENCE_PATH.read_text()).get("pairs") or {})
        return _dep_cache["data"]
    except (OSError, ValueError):
        return {}


def pair_type(a, b) -> str | None:
    """The measured pair type for two legs of ONE game, or None when that combination was not measured (goals, saves)."""
    ca, cb = _CODE_OF.get(a.market_family), _CODE_OF.get(b.market_family)
    if ca is None or cb is None:
        return None
    if ca == "ML" and cb == "ML":
        return None
    if "ML" in (ca, cb):
        ml, pl = (a, b) if ca == "ML" else (b, a)
        return f"ML_{_CODE_OF[pl.market_family]}_{'SAMETEAM' if str(ml.team) == str(pl.team) else 'OPPOSING'}"
    return f"{'_'.join(sorted((ca, cb)))}_{'TEAMMATES' if str(a.team) == str(b.team) else 'OPPOSING'}"


def measured_lift(a, b) -> dict | None:
    t = pair_type(a, b)
    row = dependence_lifts().get(t) if t else None
    if not row or not row.get("usable") or row.get("dependence_lift") is None:
        return None
    return {"type": t, "lift": float(row["dependence_lift"]), "ci95": row.get("ci95"), "reading": row.get("reading")}


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
    bounds = _bounds(legs, ests)
    lifts = []
    if not independent:
        for a_, b_ in itertools.combinations(legs, 2):
            if str(a_.game_id) == str(b_.game_id):
                lifts.append(measured_lift(a_, b_))
    measured = bool(lifts) and all(l is not None for l in lifts)
    if independent:
        combined, basis = round(_product(ests), 4), "PRODUCT_OF_LEG_ESTIMATES (every leg in a different game: ASSUMED INDEPENDENT ABSENT KNOWN SHARED DEPENDENCE)"
    elif measured:
        adj = _product(ests)
        for l in lifts:
            adj *= l["lift"]
        combined = round(min(max(adj, bounds["low"]), bounds["high"]), 4)
        basis = ("EVIDENCE_ADJUSTED_PRODUCT: the legs' estimates times the MEASURED pairwise dependence of each same-game pair (" + ", ".join(f"{l['type']} x{l['lift']:.2f}" for l in lifts)
                 + "; docs/validation/dependence_lift.json), held inside the assumption-free bounds; an approximation when three legs share a game")
    else:
        combined, basis = None, "NOT_GIVEN: legs share a game and their dependence is not (fully) measured"
    dep_level = ("ASSUMED_INDEPENDENT" if independent else ("MEASURED_DEPENDENCE" if measured else ("UNCERTAIN" if any(p["direction"] == "UNCLEAR" for p in same_game) else "POSITIVE_NOT_QUANTIFIED")))
    wi = min(range(len(legs)), key=lambda i: (ests[i], -LEVEL[cands[i]["card"]["contradiction"]["level"]]))
    weakest = {"index": wi, "label": bet_label(legs[wi].participant_name, legs[wi].market_family, legs[wi].threshold, legs[wi].american_price), "estimate": round(ests[wi], 4),
               "contradiction": ei.combine_contradiction(ei.counted_flags(cands[wi]["card"]["contradiction"]["flags"]))}
    levels = [LEVEL[ei.combine_contradiction(ei.counted_flags(c["card"]["contradiction"]["flags"]))] for c in cands]
    if ests[wi] < MIN_LEG_P or max(levels) > 1:
        quality = "BAD"
    elif ests[wi] >= MIN_GOOD_WEAKEST and sum(levels) <= 1 and dep_level in ("ASSUMED_INDEPENDENT", "MEASURED_DEPENDENCE", "POSITIVE_NOT_QUANTIFIED"):
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
            "joint_bounds": bounds, "ranking_probability": round(combined if combined is not None else _product(ests), 4), "measured_dependence": lifts if measured else None,
            "price": {"status": "UNPRICED_LEARNING_PARLAY", "reference_decimal": round(ref, 4), "reference_note": "the product of the legs' own quoted prices: a REFERENCE only, not a sportsbook price; no return or ROI is claimed",
                      "plus_money_reference": ref >= 2.0}}



# ------------------------------------------------------------------------------------------------ the day's tickets as a portfolio
def portfolio_summary(tickets: list[dict], draws: int = 4000) -> dict:
    """The day's tickets as ONE portfolio. `tickets` = [{'legs': [{'key','label','estimate','team','game_id','role_risk'}], 'probability': p}]. Reports the exposure a day carries (the same leg on several
    tickets, the same team, the same game, legs carrying a counted role flag) and, by simulation over the legs' own estimates, how the number of tickets that hit is distributed: the expected number, the chance
    that none hit, and how much shared legs widen the spread compared with the same tickets built from distinct legs. Legs are drawn independently (the corpus finds same-game shots legs independent; points legs
    and a team result share a game, a modest dependence this simulation ignores and says so). Nothing here is a bet size or a price."""
    import random
    from collections import Counter as C
    legs = {}
    for t in tickets:
        for l in t["legs"]:
            legs.setdefault(l["key"], {**l, "tickets": 0})["tickets"] += 1
    if not tickets:
        return {"tickets": 0}
    top = max(legs.values(), key=lambda l: l["tickets"])
    teams, games = C(), C()
    for t in tickets:
        for team in {str(l.get("team")) for l in t["legs"] if l.get("team")}:
            teams[team] += 1
        for g in {str(l.get("game_id")) for l in t["legs"] if l.get("game_id") is not None}:
            games[g] += 1
    rng = random.Random(20261026)
    hits = []
    keys = list(legs)
    for _ in range(draws):
        win = {k: rng.random() < legs[k]["estimate"] for k in keys}
        hits.append(sum(all(win[l["key"]] for l in t["legs"]) for t in tickets))
    mean = sum(hits) / draws
    var = sum((h - mean) ** 2 for h in hits) / draws
    p_t = [float(t["probability"]) for t in tickets]
    indep_var = sum(p * (1 - p) for p in p_t)
    hits.sort()
    return {"tickets": len(tickets), "distinct_legs": len(legs), "leg_slots": sum(len(t["legs"]) for t in tickets),
            "most_reused_leg": {"label": top["label"], "tickets": top["tickets"], "chance_it_fails": round(1 - top["estimate"], 3), "expected_tickets_it_kills_if_it_fails": top["tickets"]},
            "team_exposure": dict(teams.most_common(3)), "game_exposure": dict(games.most_common(3)),
            "tickets_with_a_role_flagged_leg": sum(1 for t in tickets if any(l.get("role_risk") for l in t["legs"])),
            "expected_tickets_hit": round(sum(p_t), 2), "simulated_expected_tickets_hit": round(mean, 2), "chance_no_ticket_hits": round(sum(1 for h in hits if h == 0) / draws, 3),
            "tickets_hit_10th_to_90th_percentile": [hits[int(0.1 * draws)], hits[int(0.9 * draws)]],
            "spread_inflation_from_shared_legs": None if indep_var <= 0 else round(var / indep_var, 2),
            "note": "legs drawn independently from their own estimates; shared legs widen the spread of the day's result (inflation above 1.0); a diversified day is one with inflation near 1.0"}


def portfolio_from_rows(rows: list[dict]) -> dict:
    """The same summary from recorded ledger rows (`parlays` table rows with legs_json)."""
    tickets = []
    for r in rows:
        legs = json.loads(r["legs_json"])
        tickets.append({"probability": r["combined_probability"] if r["combined_probability"] is not None else _product([float(l["estimate"]) for l in legs]),
                        "legs": [{"key": l["key"], "label": bet_label(l["leg"].get("participant_name"), l["leg"].get("market_family"), l["leg"].get("threshold"), l["leg"].get("american_price")),
                                  "estimate": float(l["estimate"]), "team": l["leg"].get("team"), "game_id": l["leg"].get("game_id"),
                                  "role_risk": any(f["name"] in ("TOI_DROP", "ROLE_ASSUMPTION", "PP_ROLE_LOST", "POWER_PLAY_ASSUMPTION", "POSTMORTEM_ROLE_CAUTION") and f["level"] in ("MODERATE", "HIGH", "SEVERE")
                                                   for f in (l["card"].get("contradiction") or {}).get("flags", []))} for l in legs]})
    return portfolio_summary(tickets)

def _product(xs) -> float:
    p = 1.0
    for x in xs:
        p *= x
    return p


def rank_key(p: dict) -> tuple:
    """Quality first, then FEWER legs (3 preferred over 4), then the strongest weakest leg, then the likelier ticket (an ORDERING only: for dependent legs it is not a probability), independence, key."""
    return (QUALITY.index(p["construction_quality"]), p["n_legs"], -p["weakest_leg"]["estimate"], -p["ranking_probability"], {"ASSUMED_INDEPENDENT": 0, "MEASURED_DEPENDENCE": 0, "POSITIVE_NOT_QUANTIFIED": 1, "UNCERTAIN": 2}[p["dependence_level"]], p["key"])


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
    return {"parlays": chosen, "portfolio_of_new_tickets": portfolio_summary([{"probability": p["ranking_probability"], "legs": [{"key": c["key"], "label": bet_label(c["leg"].participant_name, c["leg"].market_family, c["leg"].threshold, c["leg"].american_price), "estimate": float(c["card"]["estimate_used"]),
                                                                                                                                   "team": c["leg"].team, "game_id": c["leg"].game_id, "role_risk": False} for c in p["cands"]]} for p in chosen]), "eligible_legs": len(ok), "legs_not_eligible": dict(rejected), "constructed": len(built), "pool": len(pool)}


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
