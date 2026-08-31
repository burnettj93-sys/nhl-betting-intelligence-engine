"""
Special-teams role overlay validation (challenger/research only -- see
module docstrings in core.py/evaluate.py). Tests whether the PP/PK
role-transition residual findings from the prior sprint
(research/period_event_timing/special_teams_roles.py,
SPECIAL_TEAMS_ROLE_TRANSITION_REPORT.md) survive a proper out-of-sample
challenger evaluation against the frozen marginal models. Nothing here
ever modifies a frozen model, decision_policy, or a joint model.
"""
