"""
Matchup Intelligence / category-swing strategy (Part 36-41) -- NOT
IMPLEMENTED this sprint (2026-09-01).

Deferred to a follow-up phase: comparing MY projected category totals
against a real OPPONENT roster (Part 36) requires the opponent's real
Yahoo roster (only available once OWNER_AUTH_REQUIRED is resolved and a
real matchup is fetched) plus fantasy.projections.schedule_value's
games-remaining-in-matchup calculation (needs a real forward schedule --
see streaming.py's own note on that same dependency). Part 41's matchup
win-probability is explicitly gated on being "statistically defensible"
-- with zero real settled Yahoo matchups to calibrate against yet, this
sprint correctly does not attempt it (falling back to category
projection only, once the above dependencies exist).
"""
from __future__ import annotations

STATUS = "NOT_IMPLEMENTED_THIS_SPRINT"
