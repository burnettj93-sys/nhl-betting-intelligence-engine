"""
Normalized external pregame-source observation schema + consensus/
conflict/confirmation-override logic (Parts 11-18).

**DESIGN ONLY THIS SLICE -- no live source is actually integrated.**
Part 1's contract review (see GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md
Sections A-E) found that none of the four preferred sources currently
offer a responsible automated-access path: Daily Faceoff blocks
non-browser requests outright (Cloudflare 403, confirmed this turn);
RotoWire's real structured/API access is a paid licensing product
(`api.rotowire.com`, confirmed via their own public documentation, not
casual page access); Goalie Post and Frozen Tools (same company, Dobber)
carry a `Content-Signal: ai-train=no, use=reference` policy with no
documented bulk/API access, meaning only ad-hoc reference lookups are
clearly sanctioned, not the kind of structured recurring feed a live
pregame system needs. This module exists so that WHENEVER any of those
is licensed/permitted (Stage 2, not this slice), the exact same
normalized schema and consensus logic below can consume it immediately
-- see `record_observation()` and `ExternalSourceUnavailableError`,
which is what every source currently returns.

STATUS VALUES: the raw value a source actually used is ALWAYS preserved
verbatim (`raw_status`) alongside a normalized `source_status` -- never
force every site's own terminology into identical semantics and then
discard the original (Part 11's explicit instruction).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Normalized status vocabulary. `raw_status` on every observation keeps
# the source's own original wording -- this list is for cross-source
# reasoning (consensus/conflict), not a claim that every source's
# terminology maps perfectly onto these six values.
PROJECTED = "PROJECTED"
EXPECTED = "EXPECTED"
LIKELY = "LIKELY"
CONFIRMED = "CONFIRMED"
UNCONFIRMED = "UNCONFIRMED"
UNKNOWN = "UNKNOWN"

VALID_STATUSES = {PROJECTED, EXPECTED, LIKELY, CONFIRMED, UNCONFIRMED, UNKNOWN}

# A status counts toward "this source projects/expects this goalie" for
# consensus purposes (Part 12) -- CONFIRMED is handled completely
# separately (Part 14), never folded into a projection tally.
PROJECTION_LIKE_STATUSES = {PROJECTED, EXPECTED, LIKELY}


class ExternalSourceUnavailableError(RuntimeError):
    """Raised by every source stub this slice -- Stage 1 has no licensed/
    permitted live source (see module docstring). Callers must catch
    this and fall back to the internal projected-starter model
    (research.goalie_intelligence.model), never silently substitute a
    guess as if it were a real source observation."""


@dataclass(frozen=True)
class SourceObservation:
    """One normalized external-source observation (Part 11's exact
    field list)."""
    game_id: int
    team_id: str
    goalie_id: str
    source: str
    source_status: str            # one of VALID_STATUSES
    raw_status: str                # the source's own original wording, verbatim
    source_observed_at_utc: str
    ingested_at_utc: str
    source_published_at_utc: str | None = None
    source_probability_if_exposed: float | None = None
    source_reference: str | None = None

    def __post_init__(self):
        if self.source_status not in VALID_STATUSES:
            raise ValueError(f"source_status {self.source_status!r} not in {VALID_STATUSES}")


@dataclass
class ConsensusResult:
    """Part 12/13: SOURCE PROJECTION CONSENSUS is explicitly separate
    from SOURCE CONFIRMATION -- `status` here is never CONFIRMED unless
    at least one observation's own source_status is CONFIRMED (Part 14).
    Strong multi-source agreement on a PROJECTION can raise `agreement`
    and inform a probability boost, but never upgrades `status` past
    PROJECTED on its own -- "2 sources agree" is not the same claim as
    "confirmed" (the exact anti-pattern Part 12 warns against)."""
    status: str
    leading_goalie_id: str | None
    agreement_fraction: float           # fraction of projection-like sources agreeing with leading_goalie_id
    conflicting: bool
    confidence: str                     # HIGH / MEDIUM / LOW, Part 16
    confirmed_by: SourceObservation | None
    observations: list[SourceObservation] = field(default_factory=list)


def record_observation(*args, **kwargs) -> SourceObservation:
    """The intended entry point for ingesting a real source observation
    once Stage 2 is authorized. Always raises this slice -- see
    ExternalSourceUnavailableError. Kept as a real function (not just a
    comment) so calling code and tests can exercise the actual failure
    path Part 27 requires ("if blocked... report BLOCKED / REQUIRES
    PERMISSION / REQUIRES LICENSE", never silently degrade)."""
    raise ExternalSourceUnavailableError(
        "No external goalie-starter source is licensed/permitted for automated access this slice. "
        "See GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md Sections A-E for the per-source contract findings."
    )


def compute_consensus(observations: list[SourceObservation]) -> ConsensusResult:
    """Part 12/13: aggregates same-game observations into one consensus
    view. Never silently picks the newest source on disagreement --
    reports `conflicting=True` and the full observation list instead."""
    if not observations:
        return ConsensusResult(status=UNKNOWN, leading_goalie_id=None, agreement_fraction=0.0,
                                conflicting=False, confidence="LOW", confirmed_by=None, observations=[])

    confirmations = [o for o in observations if o.source_status == CONFIRMED]
    if confirmations:
        # Part 14: confirmation overrides projection regardless of what
        # any projection-like source said. If multiple CONFIRMED
        # observations disagree with each other, that is itself reported
        # rather than silently resolved (Part 13's "do not silently
        # choose" applies to confirmations too).
        distinct_confirmed = {o.goalie_id for o in confirmations}
        if len(distinct_confirmed) > 1:
            return ConsensusResult(status=CONFIRMED, leading_goalie_id=None, agreement_fraction=0.0,
                                    conflicting=True, confidence="LOW", confirmed_by=None,
                                    observations=observations)
        confirmed_obs = confirmations[0]
        return ConsensusResult(status=CONFIRMED, leading_goalie_id=confirmed_obs.goalie_id,
                                agreement_fraction=1.0, conflicting=False, confidence="HIGH",
                                confirmed_by=confirmed_obs, observations=observations)

    projection_like = [o for o in observations if o.source_status in PROJECTION_LIKE_STATUSES]
    if not projection_like:
        return ConsensusResult(status=UNKNOWN, leading_goalie_id=None, agreement_fraction=0.0,
                                conflicting=False, confidence="LOW", confirmed_by=None,
                                observations=observations)

    tally: dict[str, int] = {}
    for o in projection_like:
        tally[o.goalie_id] = tally.get(o.goalie_id, 0) + 1
    leading_goalie_id = max(tally, key=tally.get)
    agreement_fraction = tally[leading_goalie_id] / len(projection_like)
    conflicting = len(tally) > 1

    if agreement_fraction >= 0.75 and len(projection_like) >= 2:
        confidence = "HIGH"
    elif agreement_fraction >= 0.5:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return ConsensusResult(status=PROJECTED, leading_goalie_id=leading_goalie_id,
                            agreement_fraction=round(agreement_fraction, 4), conflicting=conflicting,
                            confidence=confidence, confirmed_by=None, observations=observations)
