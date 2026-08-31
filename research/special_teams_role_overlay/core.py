"""
Core overlay math: count-scale log-mu adjustment (never a direct,
independent probability-point addition per threshold -- Part 10), decay
functions for the transition effect, role-certainty shrinkage, and the
games-since-onset feature builder. Reuses
research.player_sog.count_models.threshold_probabilities for the actual
Poisson/NegBinom threshold derivation -- never reimplemented -- so
monotonicity and count-distribution coherence come for free from
already-tested code.
"""
from __future__ import annotations

import math
from collections import defaultdict

from research.player_sog import count_models as cm

TRANSITION_STATES_POSITIVE = ("PROMOTED_PP2_TO_PP1", "ADDED_TO_PP1", "ADDED_TO_PP2",
                              "PROMOTED_PK2_TO_PK1", "ADDED_TO_PK1", "ADDED_TO_PK2")
TRANSITION_STATES_NEGATIVE = ("DEMOTED_PP1_TO_PP2", "REMOVED_FROM_PP",
                              "DEMOTED_PK1_TO_PK2", "REMOVED_FROM_PK")
TRANSITION_STATES = TRANSITION_STATES_POSITIVE + TRANSITION_STATES_NEGATIVE
MAX_GAMES_SINCE_ONSET = 10  # beyond this, the transition effect is treated as fully decayed


def add_games_since_onset(rows_by_player: dict[str, list[dict]], state_key: str,
                           out_field: str, direction_field: str) -> None:
    """Mutates each row in place, adding `out_field` (int or None -- None
    means "no transition on record yet for this player") and
    `direction_field` (+1/-1/None) for the transition currently "in
    effect" as of that game. A NEW transition state occurring while a
    prior one is still within its decay window resets the counter to 0
    (Part "onset", not a fixed look-back)."""
    for player_id, games in rows_by_player.items():
        since = None
        direction = None
        for g in games:
            state = g.get(state_key)
            if state in TRANSITION_STATES:
                since = 0
                direction = 1 if state in TRANSITION_STATES_POSITIVE else -1
            elif since is not None:
                since = min(since + 1, MAX_GAMES_SINCE_ONSET)
            g[out_field] = since
            g[direction_field] = direction if since is not None and since <= MAX_GAMES_SINCE_ONSET else None


def add_games_since_specific_state(rows_by_player: dict[str, list[dict]], state_key: str,
                                    target_state: str, out_field: str) -> None:
    """Narrower sibling of add_games_since_onset for a single named
    state (Part 33's explicit "REMOVED_FROM_PK only" scope, never
    conflated with the broader DEMOTED_PK1_TO_PK2 negative-direction
    set)."""
    for player_id, games in rows_by_player.items():
        since = None
        for g in games:
            if g.get(state_key) == target_state:
                since = 0
            elif since is not None:
                since = min(since + 1, MAX_GAMES_SINCE_ONSET)
            g[out_field] = since


def decay_step(games_since: int | None, active_games: int = 4) -> float:
    if games_since is None:
        return 0.0
    return 1.0 if games_since <= active_games else 0.0


def decay_linear(games_since: int | None, horizon: int = 5) -> float:
    if games_since is None or games_since > horizon:
        return 0.0
    return max(0.0, 1.0 - games_since / horizon)


def decay_exponential(games_since: int | None, tau: float = 2.0) -> float:
    if games_since is None:
        return 0.0
    return math.exp(-games_since / tau)


DECAY_CANDIDATES = {
    "step_4": lambda g: decay_step(g, active_games=4),
    "linear_5": lambda g: decay_linear(g, horizon=5),
    "exp_tau_1": lambda g: decay_exponential(g, tau=1.0),
    "exp_tau_2": lambda g: decay_exponential(g, tau=2.0),
    "exp_tau_3": lambda g: decay_exponential(g, tau=3.0),
}

def decay_fn_for_name(name: str | None):
    """Resolves any DECAY_CANDIDATES key, or a fit.py-produced
    "step_<N>" name for arbitrary N (fit_beta_transition tries window
    lengths beyond the one hardcoded "step_4" entry in DECAY_CANDIDATES),
    to a callable decay(games_since) -> float. Unknown/None falls back
    to step_4."""
    if name in DECAY_CANDIDATES:
        return DECAY_CANDIDATES[name]
    if name and name.startswith("step_"):
        try:
            window = int(name.split("_")[1])
            return lambda g, w=window: decay_step(g, active_games=w)
        except (ValueError, IndexError):
            pass
    return DECAY_CANDIDATES["step_4"]


# Candidate ACTIVE-WINDOW lengths used only for FITTING beta_transition
# (see fit.py's own docstring on why fitting uses a step window even
# when the candidate being tested is smooth/exponential): a real
# per-row log-ratio regression is numerically unstable at low Poisson
# counts (Blocked Shots, mean ~0.8/game) -- a single 0-count game
# produces a huge log-ratio outlier -- so the fit itself always uses the
# stable AGGREGATE sum(actual)/sum(mu) ratio over a discrete active
# window, exactly like fit_beta_role's own method, matched afterward to
# whichever DECAY_CANDIDATES shape it's meant to approximate.
STEP_WINDOW_CANDIDATES = (2, 3, 4, 5)


def role_certainty(n_recent: int, n_baseline: int, min_recent: int = 2, min_baseline: int = 5,
                    saturating_recent: int = 3, saturating_baseline: int = 8) -> float:
    """Part 14: a simple, transparent [0,1] certainty score -- linear
    ramp from the minimum-support gate up to the window's own target
    size, capped at 1.0. Not a re-derivation of PP-role r^2; a coarse,
    interpretable shrinkage weight only."""
    if n_recent < min_recent or n_baseline < min_baseline:
        return 0.0
    recent_frac = min(1.0, n_recent / saturating_recent)
    baseline_frac = min(1.0, n_baseline / saturating_baseline)
    return min(recent_frac, baseline_frac)


def adjusted_mu(mu_frozen: float, beta_role: float, beta_transition: float,
                decay_value: float, direction: int | None, certainty: float = 1.0) -> float:
    """log(mu_adjusted) = log(mu_frozen) + beta_role*certainty +
    beta_transition*decay*direction*certainty -- Part 11's candidate
    form, with certainty (Part 14) shrinking BOTH terms toward the
    frozen baseline when role evidence is weak, never amplifying it."""
    direction = direction or 0
    log_mu = math.log(max(mu_frozen, 1e-9))
    log_mu += beta_role * certainty
    log_mu += beta_transition * decay_value * direction * certainty
    return math.exp(log_mu)


def adjusted_threshold_probs(mu_adjusted: float, alpha: float | None, thresholds: tuple[int, ...]) -> dict[int, float]:
    """The ONLY place threshold probabilities are derived -- always from
    the adjusted COUNT mean via the real, reused, already-tested
    threshold_probabilities function, never as independent per-threshold
    probability deltas (Part 10's explicit instruction)."""
    return cm.threshold_probabilities(mu_adjusted, alpha, thresholds=thresholds)
