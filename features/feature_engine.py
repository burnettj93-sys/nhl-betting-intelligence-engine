"""
DEPRECATED as of the temporal-integrity rewrite (schema v2).

This module used to read a static `team_game_context` table that no
longer exists — rest/schedule-congestion features are now computed
directly from `games` in features/point_in_time.py::rest_context(), and
roster/lineup/goalie availability come from the point-in-time event
tables there too. Import from features.point_in_time instead.

Kept as an empty stub (rather than deleted) only so an old import doesn't
produce a confusing ModuleNotFoundError mid-refactor; it exports nothing
functional.
"""
