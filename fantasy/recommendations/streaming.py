"""
Streaming Center (Part 47-49) -- NOT IMPLEMENTED this sprint (2026-09-01).

Deferred to a follow-up phase: see YAHOO_FANTASY_HOCKEY_INTEGRATION_REPORT.md's
Known Limitations section for why (real forward NHL schedule data is
required for a meaningful 2-day/3-day/rest-of-matchup stream ranking,
and this repo currently has no live 2026-27 schedule feed -- a
pre-existing constraint, not something Yahoo access blocks).

The real building blocks this module will use once built:
fantasy.recommendations.waiver_wire.rank_waiver_wire() for the
underlying ranking, fantasy.projections.schedule_value for the
startable-games/off-night calculation, and each league's real
acquisition-limit setting (LeagueSettings.max_weekly_adds) to downgrade
a marginal one-game streamer when few adds remain (Part 48).
"""
from __future__ import annotations

STATUS = "NOT_IMPLEMENTED_THIS_SPRINT"
