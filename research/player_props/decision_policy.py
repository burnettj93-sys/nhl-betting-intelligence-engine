"""
Part 8: a central, auditable BET-eligibility policy layer for player
props, sitting ON TOP of (never inside) each prop's own pricing math.

Audited first (Part 1), not guessed: the only EXISTING prop pricing/
decision code is research/live_sog_pricing/pricing.py::decide() -- SOG-
only, already tested, and explicitly left UNCHANGED this slice ("SOG LOW
confidence: unchanged", Part 2). That function already has its own
edge/EV base-action step followed by a confidence-quality cap (LOW ->
WAIT for SOG specifically) -- this module does NOT replace or call into
it. Assists and Points have no live pricing engine at all yet
(`live_market_support="NOT_CURRENTLY_AVAILABLE"` in the registry), so
there is nothing existing to wire this into; this module is the
POLICY LAYER a future Assists/Points pricing engine (mirroring
research/live_sog_pricing/pricing.py's own base-action step) would call
its own already-computed action through, exactly the way Part 16 requires
("must automatically apply ... without requiring dashboard-specific
manual intervention") -- built now, wired in later, not speculative
beyond that one function.

CORE PRINCIPLE (Part 6): this module NEVER touches raw probability,
conservative probability, edge, or EV. It takes an already-computed
BET/WATCH/PASS/WAIT/DATA_UNAVAILABLE `mathematical_status` (the action
edge/EV math alone would produce) and only ever NARROWS it -- BET or
WATCH may be capped down for a gated LOW-confidence prop; PASS, WAIT,
and DATA_UNAVAILABLE always pass through completely unchanged (Part 4:
"a PASS stays PASS -- there is nothing to wait on if there was no edge
to begin with", mirrored from research/live_sog_pricing/pricing.py's own
decide() docstring).
"""
from __future__ import annotations

POLICY_VERSION = "prop_decision_policy_v3"

# Part 8: one central, auditable table. Only props with an ACTIVE
# override appear here -- absence means "no restriction", the correct
# default for SOG/Blocks (Part 2: unchanged) and any future prop until
# separately validated (Part 2: "any future prop: unchanged unless
# separately validated"). Ceiling is the highest action a LOW-confidence
# prediction for that market may resolve to.
#
# v2 (Unified Sparse-Prop Gating Review): added GOALS, backed by the same
# real evidence pattern already used for ASSISTS/POINTS -- LOW-confidence
# skill of -0.043/-0.036/-0.032 respectively, all in a ~1.0-1.3%-of-
# predictions bucket, all sharing the same real root-cause composition
# (~19-20 game mean history, ~51% mean appearance rate) -- vs. SOG/Blocks
# (the two higher-frequency props), whose LOW buckets remain non-negative
# (+0.027 / +0.002) and were therefore deliberately left OUT of this
# table again. See SPARSE_PROP_GATING_REVIEW_REPORT.md.
#
# v3 (Player SOG by Period): a narrow, PERIOD-SPECIFIC addition, deliberately
# NOT a blanket "period SOG" or "SOG" entry -- full-game SOG and Player SOG
# by Period P1/P2 all show non-negative real LOW-confidence skill (matching
# SOG's own long-standing healthy pattern, +0.004 to +0.037 across both eval
# seasons), so none of them are gated. Only PLAYER_SOG_PERIOD_3 is added:
# its LOW-confidence bucket showed NEGATIVE skill in BOTH 2024-25 (-0.014)
# and 2025-26 (-0.016) eval seasons -- a real, repeated pattern (not a
# single-season blip), on a modest sample (~370-450 rows/season, ~0.85% of
# P3 predictions). See PLAYER_SOG_BY_PERIOD_VALIDATION_REPORT.md Section AF.
PROP_LOW_CONFIDENCE_CEILING: dict[str, str] = {
    "ASSISTS": "WATCH",
    "POINTS": "WATCH",
    "GOALS": "WATCH",
    "PLAYER_SOG_PERIOD_3": "WATCH",
}

# Part 12: settlement-equivalent market labels must never diverge in
# eligibility merely because a sportsbook names them differently --
# ANYTIME_GOAL and any future "Goals Over 0.5"-style key both resolve to
# the SAME underlying event (P(goals>=1)) and therefore the SAME ceiling
# as GOALS itself, via one canonical lookup rather than duplicated table
# entries that could silently drift apart.
_MARKET_FAMILY_ALIASES: dict[str, str] = {
    "ANYTIME_GOAL": "GOALS",
    "GOALS_OVER_0_5": "GOALS",
}


def _canonical_market_family(market_type: str) -> str:
    return _MARKET_FAMILY_ALIASES.get(market_type, market_type)

_TERMINAL_STATUSES = ("PASS", "WAIT", "DATA_UNAVAILABLE")
_NARROWABLE_ORDER = {"PASS": 0, "WATCH": 1, "BET": 2}


def gate_low_confidence(market_type: str, confidence: str, mathematical_status: str,
                         mathematical_reason: str = "") -> dict:
    """The single entry point (Part 8). `mathematical_status` is whatever
    the prop's OWN edge/EV pricing math already decided (BET/WATCH/PASS),
    or an already-terminal upstream state (WAIT from unresolved lineup/
    data uncertainty, or DATA_UNAVAILABLE from a stale quote) -- Part 3's
    audited precedence: this policy gate never overrides a WAIT or
    DATA_UNAVAILABLE that upstream data-quality logic already produced
    (Part 4: WAIT is reserved for unresolved information, not a
    reliability-policy substitute), and never touches PASS.

    Returns a dict matching Part 10's observation-ledger concept exactly
    -- {mathematical_status, final_decision, policy_reason,
    policy_override, policy_version} -- so a future ledger can store the
    ungated and gated outcomes side by side without ever overwriting the
    underlying pricing result.
    """
    market_family = _canonical_market_family(market_type)
    ceiling = PROP_LOW_CONFIDENCE_CEILING.get(market_family)

    if mathematical_status in _TERMINAL_STATUSES or ceiling is None or confidence != "LOW":
        return {"mathematical_status": mathematical_status, "final_decision": mathematical_status,
                "policy_reason": mathematical_reason, "policy_override": None, "policy_version": POLICY_VERSION}

    if _NARROWABLE_ORDER.get(mathematical_status, 0) <= _NARROWABLE_ORDER[ceiling]:
        # already at or below the ceiling (e.g. WATCH capped at WATCH) --
        # no numeric change, but the reason still names the policy so a
        # reviewer can see WHY this stayed capped, not just that it did.
        reason = mathematical_reason or (
            f"{market_type} LOW-confidence predictions have demonstrated negative historical model "
            f"skill and are capped at WATCH under policy {POLICY_VERSION}.")
        return {"mathematical_status": mathematical_status, "final_decision": mathematical_status,
                "policy_reason": reason, "policy_override": None, "policy_version": POLICY_VERSION}

    reason = (f"would otherwise be {mathematical_status}"
              + (f" ({mathematical_reason})" if mathematical_reason else "")
              + f", but {market_type} LOW-confidence predictions have demonstrated negative "
                f"historical model skill -- not BET-eligible under policy {POLICY_VERSION}.")
    return {"mathematical_status": mathematical_status, "final_decision": ceiling,
            "policy_reason": reason, "policy_override": f"LOW_CONFIDENCE_{market_type}",
            "policy_version": POLICY_VERSION}


def parlay_eligible(market_type: str, confidence: str) -> bool:
    """Part 17/19: future parlay legs must inherit the same restriction --
    exposed as metadata only, no parlay logic built or implied here."""
    ceiling = PROP_LOW_CONFIDENCE_CEILING.get(_canonical_market_family(market_type))
    if ceiling is None or confidence != "LOW":
        return True
    return _NARROWABLE_ORDER[ceiling] >= _NARROWABLE_ORDER["BET"]
