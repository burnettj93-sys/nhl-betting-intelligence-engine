"""
Probability arithmetic for a losing-streak postmortem. Pure functions, no I/O, nothing about any particular day.

The point of this module is one correction: tickets that share a leg are NOT independent. Multiplying each ticket's chance of losing treats two tickets that both contain
"Player A scores a point" as if they could lose for different reasons; when A has a quiet night they lose together. The exact joint probability is therefore computed over the
underlying LEG outcomes, not over the tickets:

  * a "group" is one source of randomness (one player, or one player-and-market). Everything in a group is driven by one uniform number U; a leg with probability p hits iff U < p.
    That makes nested lines of one market consistent (a player's 3+ shots can never hit while his 2+ misses) and lets same-player legs of different markets be treated either as
    independent (every player-and-market its own group) or as maximally dependent (one group per player). The two settings bound the answer from both sides.
  * different groups are independent. (Players in the same game are not strictly independent; that is stated as a limit, not modelled.)
  * a ticket wins iff every one of its legs hits.

`prob_all_lose` enumerates every combination of group states exactly (the groups here are a handful of players, so this is a few thousand states, not a simulation).
"""
from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict

INDEPENDENT_MARKETS = "player-and-market"       # same player, different markets: independent
SAME_PLAYER_TOGETHER = "player"                  # same player, different markets: one shared random number (upper bound on dependence)


def _group_key(leg: dict, scope: str):
    return (leg["game"], leg["player"]) if scope == SAME_PLAYER_TOGETHER else (leg["game"], leg["player"], leg["market"])


def _states(groups: dict) -> list:
    """For each group, [(probability of the state, ids of the legs that hit in that state)] over the intervals of U.
    With the group's legs sorted by ascending probability and edges 0, p1, ..., pm, 1: for U in [edge_i, edge_i+1) exactly the legs from index i upward have p > U."""
    out = []
    for legs in groups.values():
        legs = sorted(legs, key=lambda l: l["p"])
        edges = [0.0] + [l["p"] for l in legs] + [1.0]
        states = []
        for i in range(len(edges) - 1):
            width = edges[i + 1] - edges[i]
            if width <= 0:
                continue
            states.append((width, {l["id"] for l in legs[i:]}))
        out.append(states)
    return out


def _group_legs(tickets: list[list[dict]], scope: str) -> dict:
    groups: dict = defaultdict(dict)
    for t in tickets:
        for leg in t:
            groups[_group_key(leg, scope)][leg["id"]] = leg
    return {k: list(v.values()) for k, v in groups.items()}


def prob_all_lose(tickets: list[list[dict]], *, scope: str = INDEPENDENT_MARKETS, shade: float = 0.0, extra_independent: list[float] | None = None) -> float:
    """Exact probability that EVERY ticket loses. Each leg is {"id", "game", "player", "market", "p"}; tickets sharing a leg id share its outcome.
    `shade` lowers every leg's probability by that many points (a sensitivity, never the recorded value). `extra_independent` adds independent single bets
    (each given by its win probability) that must also lose."""
    shaded = [[{**l, "p": max(l["p"] - shade, 0.0)} for l in t] for t in tickets]
    groups = _group_legs(shaded, scope)
    states = _states(groups)
    total = 0.0
    for combo in itertools.product(*states):
        pr = 1.0
        hit: set = set()
        for w, h in combo:
            pr *= w
            hit |= h
        if all(any(l["id"] not in hit for l in t) for t in shaded):
            total += pr
    for q in (extra_independent or []):
        total *= (1.0 - max(q - shade, 0.0))
    return total


def naive_product_all_lose(ticket_probs: list[float]) -> float:
    """What you get by treating the tickets as independent. Shown only to quantify how far off it is."""
    out = 1.0
    for p in ticket_probs:
        out *= (1.0 - p)
    return out


def expected_wins(ticket_probs: list[float]) -> float:
    return sum(ticket_probs)


def simulate(bets: list[dict], *, tail: int, wins_seen: int, n: int = 200_000, seed: int = 20261010, scope: str = INDEPENDENT_MARKETS, shade: float = 0.0) -> dict:
    rng = random.Random(seed)
    tickets = [b["legs"] for b in bets if "legs" in b]
    shaded_bets = []
    for b in bets:
        if "legs" in b:
            shaded_bets.append({"legs": [{**l, "p": max(l["p"] - shade, 0.0)} for l in b["legs"]]})
        else:
            shaded_bets.append({"p": max(b["p"] - shade, 0.0)})
    groups = _group_legs([b["legs"] for b in shaded_bets if "legs" in b], scope)
    group_index = {leg["id"]: key for key, legs in groups.items() for leg in legs}
    tail_all = anywhere = few = 0
    wins_total = wins_sq = 0
    for _ in range(n):
        u = {key: rng.random() for key in groups}
        outcome = []
        for b in shaded_bets:
            if "legs" in b:
                outcome.append(all(u[group_index[l["id"]]] < l["p"] for l in b["legs"]))
            else:
                outcome.append(rng.random() < b["p"])
        w = sum(outcome)
        wins_total += w
        wins_sq += w * w
        if w <= wins_seen:
            few += 1
        run = best = 0
        for o in outcome:
            run = 0 if o else run + 1
            best = max(best, run)
        if best >= tail:
            anywhere += 1
        if len(outcome) >= tail and not any(outcome[-tail:]):
            tail_all += 1
    mean = wins_total / n
    return {"n": n, "tail_losses_ending_the_record": tail_all / n, "a_run_of_that_length_anywhere": anywhere / n, "at_most_the_wins_seen": few / n,
            "expected_wins": mean, "sd_wins": math.sqrt(max(wins_sq / n - mean * mean, 0.0))}


def likelihood_ratio(unique_legs: list[dict], overstated_by: float) -> float:
    """How much more (or less) likely the observed leg results are if every recorded probability were really `overstated_by` too high, than if they were right.
    One factor per UNIQUE leg (a leg that sits on three tickets is one event, not three). 1.0 = the results cannot tell the two apart."""
    ratio = 1.0
    for l in unique_legs:
        p, q = l["p"], max(l["p"] - overstated_by, 1e-6)
        ratio *= (q / p) if l["hit"] else ((1.0 - q) / (1.0 - p))
    return ratio


def legs_needed_to_detect(overstated_by: float, p: float = 0.5, alpha_z: float = 1.645, power_z: float = 0.8416) -> int:
    """Resolved legs needed to see a bias of `overstated_by` with one-sided 5% significance and 80% power (normal approximation, probabilities near p)."""
    return math.ceil(((alpha_z + power_z) ** 2) * p * (1 - p) / (overstated_by ** 2))


def poisson_binomial(ps: list[float]) -> dict:
    """Mean, standard deviation, and the exact probability of seeing no more hits than `k` (call cdf(k))."""
    dist = [1.0]
    for p in ps:
        new = [0.0] * (len(dist) + 1)
        for k, v in enumerate(dist):
            new[k] += v * (1 - p)
            new[k + 1] += v * p
        dist = new
    mean = sum(ps)
    sd = math.sqrt(sum(p * (1 - p) for p in ps))
    return {"mean": mean, "sd": sd, "cdf": lambda k: sum(dist[: k + 1]), "dist": dist}
