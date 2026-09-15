"""Explicit sportsbook-line-to-model-threshold mapping (Live Odds/Parlay/
Post-Mortem activation sprint, Part 33). A DraftKings "Over 2.5 SOG"
line and this project's own "3+" validated threshold are the same real
event, but nothing before this module said so in code -- every prior
market-parsing module kept the sportsbook's raw `point` value (2.5) and
left the reader to do the +0.5 conversion in their head. That is exactly
the kind of silent, error-prone translation this project's own house
style refuses to leave implicit.

Real rule: a half-point Over line always means "strictly more than the
line," so `Over X.5` is actionable at the integer threshold `X+1` (i.e.
"X+1 or more"). This holds for any half-point counting-stat line, not
just SOG/Saves specifically, but the two families here are the two the
model registry has validated thresholds for.
"""
from __future__ import annotations

from dataclasses import dataclass

# research/model_registry.py's own validated/rejected/insufficient
# threshold classifications for the two Part-32-priority markets.
SOG_ACTIONABLE_THRESHOLDS = frozenset({2, 3, 4, 5})
SOG_INSUFFICIENT_THRESHOLDS = frozenset({1, 6, 7, 8})

SAVES_VALIDATED_THRESHOLDS = frozenset({20, 25})
SAVES_PARTIAL_THRESHOLDS = frozenset({30})
SAVES_REJECTED_THRESHOLDS = frozenset({35})
SAVES_INSUFFICIENT_THRESHOLDS = frozenset({40})


class NonHalfPointLineError(ValueError):
    """Raised when a sportsbook line isn't the expected X.5 shape --
    surfaced loudly rather than silently rounding, since a whole-number
    line (push-eligible) is not the same bet as a half-point line and
    must never be mapped as if it were."""


def line_to_threshold(point: float) -> int:
    """Over X.5 -> X+1 (the smallest integer count that wins the bet).
    Only defined for a genuine half-point line -- raises on anything
    else (a whole-number or otherwise malformed `point` value) rather
    than guessing."""
    if point is None:
        raise NonHalfPointLineError("point is None -- no line to map")
    doubled = point * 2
    if abs(doubled - round(doubled)) > 1e-9 or int(round(doubled)) % 2 == 0:
        raise NonHalfPointLineError(f"{point!r} is not a half-point (X.5) line")
    return int(point + 0.5)


def threshold_to_line(threshold: int) -> float:
    """The inverse: this project's own "3+" threshold -> the sportsbook
    line that means the same thing ("Over 2.5")."""
    return threshold - 0.5


@dataclass(frozen=True)
class ThresholdEligibility:
    threshold: int
    eligible: bool
    reason: str


def classify_sog_threshold(threshold: int) -> ThresholdEligibility:
    if threshold in SOG_ACTIONABLE_THRESHOLDS:
        return ThresholdEligibility(threshold, True, "VALIDATED (model_registry PLAYER_SOG)")
    if threshold in SOG_INSUFFICIENT_THRESHOLDS:
        return ThresholdEligibility(threshold, False, "INSUFFICIENT sample/support at this threshold")
    return ThresholdEligibility(threshold, False, f"threshold {threshold} outside the researched SOG range entirely")


def classify_saves_threshold(threshold: int) -> ThresholdEligibility:
    if threshold in SAVES_VALIDATED_THRESHOLDS:
        return ThresholdEligibility(threshold, True, "VALIDATED (model_registry GOALIE_SAVES)")
    if threshold in SAVES_PARTIAL_THRESHOLDS:
        return ThresholdEligibility(threshold, False, "PARTIAL/RESEARCH only -- not actionable for a real recommendation")
    if threshold in SAVES_REJECTED_THRESHOLDS:
        return ThresholdEligibility(threshold, False, "REJECTED by model_registry -- never actionable")
    if threshold in SAVES_INSUFFICIENT_THRESHOLDS:
        return ThresholdEligibility(threshold, False, "INSUFFICIENT sample/support at this threshold")
    return ThresholdEligibility(threshold, False, f"threshold {threshold} outside the researched Saves range entirely")


def sog_line_is_actionable(point: float) -> ThresholdEligibility:
    return classify_sog_threshold(line_to_threshold(point))


def saves_line_is_actionable(point: float) -> ThresholdEligibility:
    return classify_saves_threshold(line_to_threshold(point))
