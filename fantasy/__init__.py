"""
Yahoo Fantasy Hockey intelligence layer (2026-09-01 sprint).

READ-ONLY consumer of the existing NHL Intelligence Engine. This domain
is deliberately isolated from the betting engine -- see
fantasy/yahoo/contracts.py's module docstring for the exact boundary
and YAHOO_FANTASY_INTEGRATION_GUIDE.md for the full architecture.

Nothing under fantasy/ may import from operational/ (the real-money/
paper-bankroll/prospective ledgers), research/live_sog_pricing/ (the
DraftKings pricing pipeline), or research/player_props/decision_policy.py
(the betting decision gate) -- see tests/test_fantasy_betting_isolation.py
for the enforced guard.
"""
