"""
Goalie-quality x projected-starter-probability integration logic. Pure
functions -- no I/O beyond what's explicitly passed in, fully
reproducible, mirroring every prior research comparison module's style.

INTEGRATION FORMULA (Part 7/H/I): for one real game with home candidate
goalies {h} (probabilities P(h)) and away candidate goalies {a}
(probabilities P(a)):

    p_home_win(h, a) = sigmoid( logit(p_baseline) + adj(h) - adj(a) )
    p_candidate       = Σ_h Σ_a P(h) * P(a) * p_home_win(h, a)

INDEPENDENCE ASSUMPTION, STATED EXPLICITLY (Part 7): P(h) and P(a) are
treated as independent -- which team's goalie starts is assumed
unrelated to which goalie the OTHER team starts. There is no realistic
mechanism linking two different teams' independent rotation decisions,
so this is a reasonable, named assumption, not a silent one.

THIS IS A WEIGHTED AVERAGE OF PROBABILITIES, NOT A SIGMOID OF A WEIGHTED
AVERAGE OF ADJUSTMENTS (Part 7's explicit warning) -- p_candidate above
averages p_home_win(h,a) itself across scenarios; it does NOT compute
sigmoid(logit(p_baseline) + Σ P(h)*adj(h) - Σ P(a)*adj(a)), which would
be mathematically different (sigmoid is nonlinear) and was explicitly
flagged as an error to avoid.
"""
from __future__ import annotations

import math

EPS = 1e-9


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def scenario_weighted_probability(p_baseline: float,
                                   home_candidates: list[tuple[float, float]],
                                   away_candidates: list[tuple[float, float]]) -> float:
    """home_candidates / away_candidates: [(start_probability, quality_adj_elo_units), ...].
    Returns the full Σ_h Σ_a P(h)*P(a)*p_home_win(h,a) mixture -- the
    headline, mathematically-correct integration (Part 3/7/9-A)."""
    base_logit = logit(p_baseline)
    total = 0.0
    for p_h, adj_h in home_candidates:
        for p_a, adj_a in away_candidates:
            p_scenario = sigmoid(base_logit + adj_h - adj_a)
            total += p_h * p_a * p_scenario
    return total


def top1_probability(p_baseline: float, home_top: tuple[float, float],
                      away_top: tuple[float, float]) -> float:
    """Part 9-B: collapse to the single most-likely starter per team,
    pretend that goalie is certain (P=1), and price only that one
    scenario -- the naive alternative the headline result is compared
    against."""
    _, adj_h = home_top
    _, adj_a = away_top
    return sigmoid(logit(p_baseline) + adj_h - adj_a)


def oracle_probability(p_baseline: float, home_actual_adj: float, away_actual_adj: float) -> float:
    """Part 8/9-C: ORACLE STARTER QUALITY TEST -- uses the quality of the
    ACTUAL historical starter (postgame-known truth), never a legitimate
    pregame model. Diagnostic only -- see the report's explicit
    isolation of this from the headline projected-starter result."""
    return sigmoid(logit(p_baseline) + home_actual_adj - away_actual_adj)


def multiclass_probs_to_pairs(candidates: list[str], probs: list[float],
                               quality_by_goalie: dict[str, float]) -> list[tuple[float, float]]:
    return [(p, quality_by_goalie.get(g, 0.0)) for g, p in zip(candidates, probs)]
