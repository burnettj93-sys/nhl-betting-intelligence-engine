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

MIN_LEG_P = ei.LEG_FLOOR                 # an individually likely event (design choice; the pool's -150 floor means most legs sit between 55% and 70%)
MIN_GOOD_WEAKEST = 0.62          # the weakest leg of a GOOD ticket
MIN_FOURTH_P = 0.60
MAX_PARLAYS_PER_DAY = 10
LEG_REUSE = 2                    # a leg appears in at most this many of the day's parlays
POOL_LEGS = -(-MAX_PARLAYS_PER_DAY * 3 * 6 // (LEG_REUSE * 5))          # the strongest eligible legs considered (18). Ten three-leg tickets need 30 leg slots and each leg gives LEG_REUSE of them: 15 legs is the exact minimum for the day's
                                                              # maximum to be possible AT ALL, and a one-pass search cannot use a pool with no slack (measured: 15 legs fill 8 to 9 slots, 18 fill 10, on pools of equally
                                                              # strong legs). The pool is only what is SEARCHED: a weaker leg enters a ticket only after the stronger tickets are taken, and every ticket still has to pass the
                                                              # same gates, so a larger pool never admits anything the gates refuse. (14 legs gave 28 leg slots: at most nine tickets whatever their quality.)
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
        pol = ei.points_policy()
        if k not in pol["enabled"]:
            return False, f"points {k}+ is not validated for live use (docs/validation/points_thresholds.json)"
        if k not in pol["actionable"]:
            return False, f"points {k}+ is validated but RESEARCH ONLY: only {pol['cases_at_or_above_the_leg_floor'].get(k, 0)} validated predictions reach the {pol['leg_floor']:.0%} leg floor, so it cannot be a live leg"
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
# ONE authoritative portfolio assessment. The ticket search (build) uses it to choose between similarly strong tickets, and the status and the postmortem report the SAME function over the recorded rows, so
# the portfolio that selected the day's tickets and the portfolio shown afterwards are one piece of logic (`leg_view` builds a leg the same way from a candidate and from a stored row).
SIMILARITY_Z = ei.Z_SIGNIFICANT  # two weakest legs are NOT distinguishable when their estimates differ by less than this many standard errors of the difference (the engine's own bar for a finding)
COV_RESOLUTION = 0.005           # a difference in how two tickets move together smaller than half a point of probability is below the noise of the leg estimates themselves
ROLE_FAMILIES = ("TOI_ROLE", "PP_ROLE")
MODEL_FAMILIES = ("MODEL_OVERCONFIDENCE", "PRODUCTION_BELOW_MODEL")


def leg_risks(card: dict) -> dict:
    """The two shared-uncertainty facts about a leg, from its review card: ROLE (a counted role / deployment family at MODERATE or worse: lineup uncertainty touches every leg built on that team's minutes) and
    MODEL (the model's own reliability is in doubt for this leg: a counted overconfidence or production-below-model family, or calibration below HIGH: the same calibration failure can hit every ticket using the model there)."""
    flags = ei.counted_flags((card.get("contradiction") or {}).get("flags") or [])
    fams = {f["family"]: f["level"] for f in ei.families_of(flags)}
    role = any(fams.get(f) in ("MODERATE", "HIGH", "SEVERE") for f in ROLE_FAMILIES)
    model = any(fams.get(f) in ("MODERATE", "HIGH", "SEVERE") for f in MODEL_FAMILIES) or (card.get("calibration_confidence") or {}).get("level") != "HIGH"
    return {"role_risk": bool(role), "model_risk": bool(model)}


def leg_view(key: str, leg, estimate: float, card: dict) -> dict:
    """A leg as the portfolio sees it. `leg` is a ParlayLeg or the dict of one (a stored row): the same fields, the same risk logic."""
    g = (lambda n: leg.get(n)) if isinstance(leg, dict) else (lambda n: getattr(leg, n, None))
    return {"key": key, "label": bet_label(g("participant_name"), g("market_family"), g("threshold"), g("american_price")), "estimate": float(estimate), "team": g("team"), "game_id": g("game_id"),
            "market_family": g("market_family"), "threshold": g("threshold"), "player": None if g("market_family") == "MONEYLINE" else str(g("participant_id")), **leg_risks(card)}


def view_of_candidates(p: dict) -> dict:
    legs = [leg_view(c["key"], c["leg"], float(c["card"]["estimate_used"]), c["card"]) for c in p["cands"]]
    return {"key": p["key"], "legs": legs, "probability": ticket_probability(legs)}


def view_of_row(r) -> dict:
    legs = [leg_view(l["key"], l["leg"], float(l["estimate"]), l.get("card") or {}) for l in json.loads(r["legs_json"])]
    return {"key": r["idempotency_key"] if "idempotency_key" in r.keys() else r["parlay_id"], "legs": legs, "probability": ticket_probability(legs)}


def _lift_of(a: dict, b: dict, which: str = "dependence_lift") -> tuple[float, bool]:
    """(the measured lift of two legs of one game, whether it was measured). Different games: independent by assumption (1.0, True is NOT claimed: it is an assumption, reported as such by the caller)."""
    from types import SimpleNamespace
    t = pair_type(SimpleNamespace(market_family=a.get("market_family"), team=a.get("team")), SimpleNamespace(market_family=b.get("market_family"), team=b.get("team")))
    row = dependence_lifts().get(t) if t else None
    if not row or not row.get("usable") or row.get("dependence_lift") is None:
        return 1.0, False
    if which == "dependence_lift":
        return float(row["dependence_lift"]), True
    ci = row.get("ci95") or [row["dependence_lift"]] * 2
    return float(ci[0] if which == "low" else ci[1]), True


def _same_game_pairs(legs: list[dict]):
    for a, b in itertools.combinations(legs, 2):
        if a.get("game_id") is not None and str(a["game_id"]) == str(b.get("game_id")):
            yield a, b


def ticket_probability(legs: list[dict], which: str = "dependence_lift") -> float:
    """The legs' estimates multiplied, times the measured pairwise lift of every same-game pair (an approximation for three legs of one game: only pairwise evidence exists). Different games: independent by assumption."""
    p = 1.0
    for l in legs:
        p *= float(l["estimate"])
    for a, b in _same_game_pairs(legs):
        p *= _lift_of(a, b, which)[0]
    return max(0.0, min(1.0, p))


def joint_probability(t1: dict, t2: dict) -> float:
    """P(both tickets hit): the union of their legs (a shared leg counts once) with the same measured lifts. The covariance of two tickets is this minus the product of their probabilities."""
    union = {l["key"]: l for l in t1["legs"]}
    union.update({l["key"]: l for l in t2["legs"]})
    j = ticket_probability(list(union.values()))
    return max(min(j, t1["probability"], t2["probability"]), t1["probability"] + t2["probability"] - 1.0, 0.0)


def _game_loadings(legs: list[dict], which: str) -> dict:
    """For one game's legs, a loading per leg on a shared game state s = +-1 (probability 1/2 each): P(win | s) = p (1 + delta s). Marginals are exact and two legs hit together p_i p_j (1 + delta_i delta_j), so the
    pair's measured lift is reproduced exactly; three or more legs reproduce the pairwise lifts approximately (least squares). Only pairwise lifts exist: nothing about triples is claimed."""
    n = len(legs)
    c = [[0.0] * n for _ in range(n)]
    for i, j in itertools.combinations(range(n), 2):
        c[i][j] = c[j][i] = _lift_of(legs[i], legs[j], which)[0] - 1.0
    d = [0.1] + [0.1 * (1.0 if c[0][i] >= 0 else -1.0) for i in range(1, n)]          # signs from the first leg's pairs: a negative lift needs opposite loadings
    for _ in range(400):
        g = [sum(2 * (d[i] * d[j] - c[i][j]) * d[j] for j in range(n) if j != i) for i in range(n)]
        d = [d[i] - 0.2 * g[i] for i in range(n)]
    return {legs[i]["key"]: max(-min(1.0, 1.0 / max(float(legs[i]["estimate"]), 1e-9) - 1.0), min(min(1.0, 1.0 / max(float(legs[i]["estimate"]), 1e-9) - 1.0), d[i])) for i in range(n)}


def simulate_tickets_hit(tickets: list[dict], which: str = "dependence_lift", draws: int = 4000, seed: int = 20261026) -> list[int]:
    import random
    legs: dict = {}
    for t in tickets:
        for l in t["legs"]:
            legs.setdefault(l["key"], l)
    games: dict = {}
    for l in legs.values():
        games.setdefault(str(l.get("game_id")), []).append(l)
    load = {}
    for g, ls in games.items():
        if len(ls) >= 2 and g != "None":
            load.update(_game_loadings(ls, which))
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        state = {g: (1 if rng.random() < 0.5 else -1) for g in games}
        win = {}
        for k, l in legs.items():
            p = float(l["estimate"]) * (1.0 + load.get(k, 0.0) * state[str(l.get("game_id"))])
            win[k] = rng.random() < p
        out.append(sum(all(win[l["key"]] for l in t["legs"]) for t in tickets))
    return out


def portfolio_summary(tickets: list[dict], draws: int = 4000) -> dict:
    """The day's tickets as ONE portfolio. `tickets` = [{'key','probability','legs': [leg_view]}]. Reports what the day concentrates (the same leg on several tickets, the same player, team and game, legs carrying
    role or model uncertainty) and, by simulation that RESPECTS the measured same-game dependence (docs/validation/dependence_lift.json; cross-game legs are independent by assumption), how many tickets hit: the
    expected number, the chance none hit (with the range it takes when the measured lifts sit at either end of their 95% interval), and how much shared legs and dependence widen the spread. Nothing here is a bet
    size or a price."""
    from collections import Counter as C
    if not tickets:
        return {"tickets": 0}
    legs: dict = {}
    for t in tickets:
        for l in t["legs"]:
            legs.setdefault(l["key"], {**l, "tickets": 0})["tickets"] += 1
    top = max(legs.values(), key=lambda l: l["tickets"])
    teams, games, players = C(), C(), C()
    for t in tickets:
        for team in {str(l.get("team")) for l in t["legs"] if l.get("team")}:
            teams[team] += 1
        for g in {str(l.get("game_id")) for l in t["legs"] if l.get("game_id") is not None}:
            games[g] += 1
        for pl in {str(l.get("player")) for l in t["legs"] if l.get("player")}:
            players[pl] += 1
    hits = sorted(simulate_tickets_hit(tickets, draws=draws))
    lo = simulate_tickets_hit(tickets, "low", draws=draws)
    hi = simulate_tickets_hit(tickets, "high", draws=draws)
    none = lambda h: sum(1 for x in h if x == 0) / len(h)
    mean = sum(hits) / draws
    var = sum((h - mean) ** 2 for h in hits) / draws
    p_t = [float(t["probability"]) for t in tickets]
    indep_var = sum(p * (1 - p) for p in p_t)
    pairs_total = len(list(_same_game_pairs(list(legs.values()))))
    unmeasured = sum(1 for a, b in _same_game_pairs(list(legs.values())) if not _lift_of(a, b)[1])
    none_range = sorted((round(none(lo), 3), round(none(hits), 3), round(none(hi), 3)))
    return {"tickets": len(tickets), "distinct_legs": len(legs), "leg_slots": sum(len(t["legs"]) for t in tickets),
            "most_reused_leg": {"label": top["label"], "tickets": top["tickets"], "chance_it_fails": round(1 - top["estimate"], 3), "expected_tickets_it_kills_if_it_fails": top["tickets"]},
            "team_exposure": dict(teams.most_common(3)), "game_exposure": dict(games.most_common(3)), "player_exposure": dict(players.most_common(3)),
            "tickets_with_a_role_flagged_leg": sum(1 for t in tickets if any(l.get("role_risk") for l in t["legs"])),
            "tickets_with_a_model_risk_leg": sum(1 for t in tickets if any(l.get("model_risk") for l in t["legs"])),
            "expected_tickets_hit": round(sum(p_t), 2), "simulated_expected_tickets_hit": round(mean, 2), "chance_no_ticket_hits": round(none(hits), 3), "chance_no_ticket_hits_range": [none_range[0], none_range[-1]],
            "tickets_hit_10th_to_90th_percentile": [hits[int(0.1 * draws)], hits[int(0.9 * draws)]],
            "spread_inflation_from_shared_legs": None if indep_var <= 0 else round(var / indep_var, 2),
            "same_game_pairs": {"total": pairs_total, "without_a_measured_lift": unmeasured},
            "note": "cross-game legs are independent by assumption; same-game legs follow the measured pairwise lifts (docs/validation/dependence_lift.json), an approximation where three legs share a game; a diversified day has inflation near 1.0"}


assess_portfolio = portfolio_summary


def portfolio_from_rows(rows: list[dict]) -> dict:
    """The same summary from recorded ledger rows (`parlays` table rows with legs_json)."""
    return portfolio_summary([view_of_row(r) for r in rows])


def weakest_se(p: dict) -> float | None:
    """The standard error of the calibration evidence behind the ticket's WEAKEST leg: how precisely the engine knows that leg's estimate (the cell its calibration confidence stands on). None when the card
    carries no such evidence."""
    card = p["cands"][p["weakest_leg"]["index"]]["card"]
    ev = (card.get("calibration_confidence") or {}).get("evidence") or {}
    se = ev.get("se", ev.get("standard_error"))
    return None if se is None else float(se)


def similar_strength(a: dict, b: dict) -> bool:
    """Are two tickets' weakest legs indistinguishable? The difference of their estimates is smaller than SIMILARITY_Z standard errors of that difference, each leg's error being the standard error of its own
    calibration evidence. No such evidence on either leg means no claim of similarity: they are then compared strictly, by their estimates (the transparent rule, with no tolerance to defend)."""
    sa, sb = weakest_se(a), weakest_se(b)
    if sa is None or sb is None:
        return a["weakest_leg"]["estimate"] == b["weakest_leg"]["estimate"]
    return abs(a["weakest_leg"]["estimate"] - b["weakest_leg"]["estimate"]) < SIMILARITY_Z * (sa * sa + sb * sb) ** 0.5


def concentration_key(view: dict, kept: list[dict]) -> tuple:
    """How much a candidate ticket adds to what the day already concentrates, most important first; used ONLY to choose between tickets of similar strength (see build). Lower is more diversified.
    (1) legs it shares with kept tickets (one failure kills both); (2) other legs of a player the day already stakes (a different threshold of the same player fails with him); (3) how much it moves WITH the kept
    tickets: the covariance of the tickets' results at the measured lifts, in COV_RESOLUTION steps; (4) legs in a game
    the day already uses; (5) legs of a team the day already uses; (6) role-uncertain legs on a team that already carries role uncertainty; (7) model-uncertain legs of a market the day already trusts the model on."""
    if not kept:
        return (0, 0, 0, 0, 0, 0, 0)
    kept_keys = [{l["key"] for l in k["legs"]} for k in kept]
    shared = sum(len({l["key"] for l in view["legs"]} & ks) for ks in kept_keys)
    cov = sum(joint_probability(view, k) - view["probability"] * k["probability"] for k in kept)
    kl = [l for k in kept for l in k["legs"]]
    k_players = {str(l["player"]) for l in kl if l.get("player")}
    k_games = {str(l["game_id"]) for l in kl if l.get("game_id") is not None}
    k_teams = {str(l["team"]) for l in kl if l.get("team")}
    k_role_teams = {str(l["team"]) for l in kl if l.get("role_risk") and l.get("team")}
    k_model_mkts = {(l.get("market_family"), l.get("threshold")) for l in kl if l.get("model_risk")}
    own = [l for l in view["legs"] if l["key"] not in set().union(*kept_keys)]
    return (shared, sum(1 for l in own if l.get("player") and str(l["player"]) in k_players), int(max(cov, 0.0) / COV_RESOLUTION), sum(1 for l in own if str(l.get("game_id")) in k_games), sum(1 for l in own if str(l.get("team")) in k_teams),
            sum(1 for l in own if l.get("role_risk") and str(l.get("team")) in k_role_teams), sum(1 for l in own if l.get("model_risk") and (l.get("market_family"), l.get("threshold")) in k_model_mkts))


def _product(xs) -> float:
    p = 1.0
    for x in xs:
        p *= x
    return p


def rank_key(p: dict) -> tuple:
    """Quality first, then FEWER legs (3 preferred over 4), then the strongest weakest leg, then the likelier ticket (an ORDERING only: for dependent legs it is not a probability), independence, key."""
    return (QUALITY.index(p["construction_quality"]), p["n_legs"], -p["weakest_leg"]["estimate"], -p["ranking_probability"], {"ASSUMED_INDEPENDENT": 0, "MEASURED_DEPENDENCE": 0, "POSITIVE_NOT_QUANTIFIED": 1, "UNCERTAIN": 2}[p["dependence_level"]], p["key"])


def build(cands: list[dict], already: list[dict] | None = None, slots: int = MAX_PARLAYS_PER_DAY) -> dict:
    """Up to `slots` parlays from the day's reviewed candidates. `already` are today's recorded parlays ({'key','leg_keys', and 'view' when known}) whose legs count against the reuse cap. Fewer than `slots` is a
    correct answer.

    SELECTION IS PORTFOLIO-AWARE. The tickets are not taken in rank order: at each step the best remaining ticket sets the bar, every ticket of the same quality and length whose weakest leg is not
    distinguishably weaker (see similar_strength: within two standard errors of the difference) is 'similarly strong', and among those the one that adds the LEAST concentration to what is already kept is taken (concentration_key: shared legs, how the tickets
    move together at the measured lifts, repeated games, teams, role uncertainty and model uncertainty), rank order breaking ties. A clearly stronger ticket is never passed over for diversification, and no ticket
    that the old order would not have admitted is admitted: the candidate set, the quality gate, the reuse cap of 2 and the two-leg difference are unchanged."""
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
    kept_views = [a["view"] for a in already or [] if a.get("view")]
    for a in already or []:
        used.update(a["leg_keys"])
    have = {a["key"] for a in already or []}
    chosen = []
    remaining = [p for p in built if p["action"] == "RECORD" and p["key"] not in have]
    views = {p["key"]: view_of_candidates(p) for p in remaining}
    while len(kept_sets) < slots and remaining:                                                 # kept_sets holds the day's tickets so far (recorded + chosen): one ticket, one slot
        allowed = [p for p in remaining if not any(used[c["key"]] >= LEG_REUSE for c in p["cands"])
                   and not any(len({c["key"] for c in p["cands"]} & other) >= min(p["n_legs"], len(other)) - 1 for other in kept_sets)]
        if not allowed:
            break
        lead = allowed[0]
        similar = [p for p in allowed if p["construction_quality"] == lead["construction_quality"] and p["n_legs"] == lead["n_legs"] and similar_strength(lead, p)]
        pick = min(similar, key=lambda p: (concentration_key(views[p["key"]], kept_views), rank_key(p)))
        ks = {c["key"] for c in pick["cands"]}
        chosen.append(pick)
        kept_sets.append(ks)
        kept_views.append(views[pick["key"]])
        used.update(ks)
        remaining = [p for p in remaining if p["key"] != pick["key"]]
    return {"parlays": chosen, "portfolio": portfolio_summary(kept_views), "portfolio_of_new_tickets": portfolio_summary([views[p["key"]] for p in chosen]),
            "eligible_legs": len(ok), "legs_not_eligible": dict(rejected), "constructed": len(built), "pool": len(pool)}


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
