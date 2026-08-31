"""
Preseason Operational Readiness Closure sprint (2026-08-30), Track 6:
the closing-price resolution mechanism. We cannot populate REAL CLV yet
-- no real sportsbook price has ever been observed for any of the 142
canonical markets (NHL_ENGINE_STATE_OF_THE_UNION_2026_08_30.md Part 33)
-- but the mechanism itself is real, tested, and ready the moment a real
price history exists.

Part 44: a "closing snapshot" is the LATEST price observation with
captured_at_utc STRICTLY before event_start_utc -- exact equality is
excluded, matching the identical rule prospective_ledger.py's own
insert_prediction() already enforces for odds_captured_at_utc.

Part 45: given an archived normalized price history for one market side,
find that valid close; CLV_NOT_AVAILABLE if none exists.

Part 46: demo / research / shadow price observations can never produce
real CLV -- REAL_PRICE_SOURCES is an explicit allowlist, not a
convention callers might forget.

Part 47: this module computes a CLV NUMBER (a probability/price delta)
only -- it never computes profit/loss. Real P&L requires a real stake
and a real placed price, which only a REAL_BET row carries, and no
automated P&L calculator exists in this project (confirmed in the same
audit) -- attaching a CLV number to a MODEL_OBSERVATION's `clv` field is
research metadata about market movement, categorically never a claim
about money won or lost.
"""
from __future__ import annotations

CLV_NOT_AVAILABLE = "CLV_NOT_AVAILABLE"
RESOLVED = "RESOLVED"

# Only price observations captured from a REAL, live sportsbook feed can
# ever produce real CLV (Part 46). Demo/research/shadow-simulated prices
# are structurally excluded here, not just documented as off-limits.
REAL_PRICE_SOURCES = frozenset({"THE_ODDS_API"})


def find_closing_price(price_history: list[dict], event_start_utc: str) -> dict:
    """`price_history`: a list of already-normalized price observations,
    each carrying at least {"captured_at_utc", "american_price", "source"}
    -- e.g. rows read back from an archive or observation ledger. Never
    fetches anything itself; this is a pure function over already-real
    (or already-known-fake) data, so a caller controls exactly what
    history it's allowed to see."""
    real_observations = [p for p in price_history if p.get("source") in REAL_PRICE_SOURCES]
    if not real_observations:
        return {"status": CLV_NOT_AVAILABLE,
                "reason": "no REAL (non-demo/research/shadow) price observation supplied"}

    valid = [p for p in real_observations if p["captured_at_utc"] < event_start_utc]
    if not valid:
        return {"status": CLV_NOT_AVAILABLE,
                "reason": "no real price observation strictly before event_start_utc "
                          "(Part 44: exact equality does not count as a valid close)"}

    closing = max(valid, key=lambda p: p["captured_at_utc"])
    return {"status": RESOLVED, "closing_odds": closing["american_price"],
            "closing_captured_at_utc": closing["captured_at_utc"], "source": closing["source"]}


def compute_clv(entry_odds: float, closing_odds: float) -> float:
    """The CLV number itself: entry-vs-close implied-probability delta.
    Reuses pricing/odds_math.py's real, unmodified american_to_prob --
    never a second, parallel probability conversion. This is a pure
    number about market movement -- see this module's own docstring for
    why it is never profit/loss."""
    from pricing import odds_math
    return odds_math.american_to_prob(closing_odds) - odds_math.american_to_prob(entry_odds)
