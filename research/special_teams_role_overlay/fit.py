"""
Fits beta_role (absolute-role log-mu offset) and beta_transition (decay-
weighted log-mu offset for a recent role change) on TUNING data only
(2022-23 + 2023-24, this project's own established foundation/tune
seasons -- never the two historical-OOS evaluation seasons). Both are
simple, closed-form, interpretable fits -- Part 13's "do not
over-engineer" / "prefer smooth/simple" -- not a general-purpose GLM
solver.
"""
from __future__ import annotations

import math
import statistics

from research.special_teams_role_overlay import core

TUNING_SEASONS = (20222023, 20232024)
EVAL_SEASONS = {"2024-25": 20242025, "2025-26": 20252026}

PSEUDOCOUNT = 0.02  # avoids log(0) for a real 0-count game -- must stay small relative to the
                     # LOWEST-rate prop this is used for (Goals: mean ~0.17/game), not just SOG
                     # (mean ~1.6/game). A real bug found during this sprint: an earlier 0.5
                     # pseudocount silently floored both the actual-mean and frozen-mu-mean for
                     # Goals/Assists (means ~0.17/~0.29) up to the SAME clamped value, making every
                     # fitted beta_role come out to exactly log(1.0)=0.0 -- a false "no PP-role
                     # signal" finding that was actually the smoothing constant swallowing the
                     # entire real effect, not a genuine null result.


def fit_beta_role(rows: list[dict], role_field: str, mu_field: str, actual_field: str) -> dict[str, float]:
    """One offset per non-baseline role value (e.g. "PP1", "PP2"; "NONE"
    is the implicit zero baseline) -- the closed-form MLE for a
    single-factor Poisson GLM with a log-offset: beta[role] =
    log(mean(actual)/mean(mu)) within that role's stratum."""
    by_role: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        role = r.get(role_field)
        mu, actual = r.get(mu_field), r.get(actual_field)
        if role is None or mu is None or actual is None:
            continue
        by_role.setdefault(role, []).append((mu, actual))

    betas = {}
    for role, pairs in by_role.items():
        if role == "NONE" or len(pairs) < 30:
            continue
        mean_mu = statistics.fmean(p[0] for p in pairs)
        mean_actual = statistics.fmean(p[1] for p in pairs)
        betas[role] = math.log(max(mean_actual, PSEUDOCOUNT) / max(mean_mu, PSEUDOCOUNT))
    return betas


def _role_adjusted_mu(mu_frozen: float, role: str | None, beta_role: dict[str, float]) -> float:
    b = beta_role.get(role, 0.0) if role else 0.0
    return mu_frozen * math.exp(b)


def fit_beta_transition(rows: list[dict], role_field: str, mu_field: str, actual_field: str,
                         beta_role: dict[str, float], since_field: str, direction_field: str) -> dict:
    """Fits beta_transition via the STABLE AGGREGATE sum(actual)/sum(mu)
    ratio (exactly fit_beta_role's own method) over a discrete active
    window [0, w] games since onset, tried for each candidate window
    length in core.STEP_WINDOW_CANDIDATES, selecting the window whose
    OWN active-vs-decayed split best matches a real decline (i.e. the
    aggregate effect in the FIRST HALF of the window exceeds the SECOND
    HALF -- confirms a genuine decay rather than a flat, permanent
    shift, which would just be role misclassified as transition).

    Real bug found and fixed during this sprint: an initial per-row
    log-ratio regression (even Poisson-weighted) was numerically
    unstable at low Poisson counts -- Blocked Shots (mean ~0.83/game)
    produced an implausible beta_removal of +1.3-1.44 (a ~75%+
    multiplicative swing) driven by individual 0-count-game log-ratio
    outliers, nothing like the real, modest -0.064 residual mean found
    in the prior sprint's simple aggregate analysis. The aggregate
    sum-ratio estimator used here is the same stable method already
    proven correct for beta_role."""
    tagged = []
    for r in rows:
        since, direction = r.get(since_field), r.get(direction_field)
        role, mu, actual = r.get(role_field), r.get(mu_field), r.get(actual_field)
        if since is None or direction is None or mu is None or actual is None:
            continue
        tagged.append((since, direction, _role_adjusted_mu(mu, role, beta_role), actual))

    best = None
    for window in core.STEP_WINDOW_CANDIDATES:
        active = [(since, direction, mu_role, actual) for since, direction, mu_role, actual in tagged
                  if since <= window]
        if len(active) < 60:
            continue
        # `rows` is assumed single-direction (enforced by the caller --
        # e.g. blocks' removal-only script filters to direction=-1
        # only); the fitted beta is the plain aggregate log-ratio, with
        # `direction` re-applied at APPLICATION time via adjusted_mu's
        # own `direction` argument, never baked in twice here.
        sum_actual = sum(a[3] for a in active)
        sum_mu = sum(a[2] for a in active)
        beta = math.log(max(sum_actual, PSEUDOCOUNT) / max(sum_mu, PSEUDOCOUNT))

        half = max(1, window // 2)
        first_half = [a for a in active if a[0] <= half]
        second_half = [a for a in active if a[0] > half]
        first_beta = (math.log(max(sum(a[3] for a in first_half), PSEUDOCOUNT) /
                                max(sum(a[2] for a in first_half), PSEUDOCOUNT)) if first_half else 0.0)
        second_beta = (math.log(max(sum(a[3] for a in second_half), PSEUDOCOUNT) /
                                 max(sum(a[2] for a in second_half), PSEUDOCOUNT)) if second_half else 0.0)
        # "declines" = the effect's magnitude in the second half is
        # smaller than the first half (both compared to 0, using the
        # sign of the fitted beta itself since direction is already
        # folded into sum_actual/sum_mu).
        declines = abs(second_beta) < abs(first_beta)

        candidate = {"decay_name": f"step_{window}", "beta_transition": beta,
                     "n_active_rows": len(active), "first_half_beta": first_beta,
                     "second_half_beta": second_beta, "declines": declines}
        if best is None or (declines and not best.get("declines")) or (
                declines == best.get("declines") and abs(beta) > abs(best["beta_transition"])):
            best = candidate
    return best or {"decay_name": None, "beta_transition": 0.0, "n_active_rows": 0, "declines": None}
