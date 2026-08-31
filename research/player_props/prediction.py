"""
Section F: the shared player-prop prediction contract every prop family
(SOG, blocked shots, and any future market) should be able to produce,
so downstream pricing/UI code can treat them uniformly without knowing
which specific model produced a given prediction.

This is a DOCUMENTED SHAPE, not a rewrite of any existing model:
research/player_sog/live_projection.py already returns a dict carrying
every one of these concepts (just under slightly different key names,
since it predates this formal contract) — SOG is not migrated to this
exact dataclass this slice, to avoid any risk of changing the behavior
of a model that has already passed validation (`PLAYER_SOG_FOUNDATION_REPORT.md`).
`to_prop_prediction()` below adapts either shape into this canonical
one for any caller (e.g. a future shared dashboard prop-card component)
that wants one consistent shape regardless of which prop produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PropPrediction:
    game_id: int | None
    player_id: str
    player_name: str
    market_type: str            # e.g. "SOG", "BLOCKED_SHOTS"
    threshold: int               # the "N+" being priced
    expected_count: float        # the model's expected value of the underlying stat (NOT a betting EV)
    conservative_count: float
    raw_probability: float       # P(stat >= threshold), raw model probability
    conservative_probability: float
    confidence: str              # HIGH / MEDIUM / LOW
    confidence_drivers: list[str] = field(default_factory=list)
    confidence_risks: list[str] = field(default_factory=list)
    model_version: str = "v1"
    feature_version: str = "v1"
    data_provenance: str = "ARCHIVAL_RESEARCH"   # or LIVE_OBSERVED
    lineup_status: str = "PROJECTED/UNCONFIRMED"  # never CONFIRMED without a live source

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id, "player_id": self.player_id, "player_name": self.player_name,
            "market_type": self.market_type, "threshold": self.threshold,
            "expected_count": self.expected_count, "conservative_count": self.conservative_count,
            "raw_probability": self.raw_probability, "conservative_probability": self.conservative_probability,
            "confidence": self.confidence, "confidence_drivers": self.confidence_drivers,
            "confidence_risks": self.confidence_risks, "model_version": self.model_version,
            "feature_version": self.feature_version, "data_provenance": self.data_provenance,
            "lineup_status": self.lineup_status,
        }


def from_sog_view(view: dict, threshold: int, game_id: int | None, player_id: str, player_name: str) -> PropPrediction:
    """Adapts research/player_sog/live_projection.py's existing return
    shape into the shared contract, without changing that module at all."""
    return PropPrediction(
        game_id=game_id, player_id=player_id, player_name=player_name, market_type="SOG",
        threshold=threshold, expected_count=view["expected_sog"], conservative_count=view["conservative_sog"],
        raw_probability=view["probs"][str(threshold)], conservative_probability=view["conservative_probs"][str(threshold)],
        confidence=view["confidence"], confidence_drivers=view["confidence_drivers"],
        confidence_risks=view["confidence_risks"], lineup_status="PROJECTED/UNCONFIRMED",
    )
