"""
The ticket-selection policy as data.

Why this exists. The objective is "up to five worthwhile +100-or-better parlays with a strong estimated chance of hitting". Until 2026-10-10 the selector enforced "+100 or better"
and "positive modelled edge" but not "a strong chance of hitting" (no floor on a ticket's estimated hit chance) and treated "worthwhile" as an edge of at least zero after a small haircut.
The postmortem (docs/POSTMORTEM_2026-10-10.md) found tickets accepted with hit chances of 17-21% and margins of +0.05%, +0.42% and +0.99%.

Two policies are defined here, side by side, so the difference is visible and testable:

  LEGACY    exactly what ran on 2026-10-07 .. 2026-10-09 (minus the per-player limit added on 2026-10-09, which is included): the numbers the engine's module constants carry.
  PROPOSED  the restart proposal under owner review. Every number is a JUDGEMENT, not a validated result: the floor is a reading of "strong", the value margin is a cushion for unverified
            Ontario prices and void rules, and the exposure limits follow from how shared legs raise the chance that no ticket wins. None was fitted to any losing or winning ticket,
            and none has been validated against historical sportsbook prices (there are none). It cannot be switched on without the owner approving THIS policy by its hash
            (operational/ticket_policy.py); recording is paused until then.

`slots` stays five in both: a day may record anything from zero to five tickets, and an empty slot is a correct answer.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class TicketPolicy:
    policy_id: str
    status: str
    slots: int = 5                       # up to this many tickets a day; zero is allowed
    min_combined_decimal: float = 2.0    # +100 or better
    min_estimated_ev: float = 0.05       # EV at the recorded (conservative) leg probabilities
    leg_shade: float = 0.03              # the haircut on every leg for the legacy value test
    floor: float = 0.0                   # minimum estimated hit chance of a ticket (the "strong chance" requirement); 0 = none
    value_shade: float = 0.03            # per-leg probability reduction the ticket's edge must survive
    min_value_after_shade: float = 0.0   # EV after value_shade must be at least this
    max_per_leg: int = 2
    max_per_game: int = 3
    max_per_player: int | None = 2       # None = no per-player limit

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:16]

    def summary(self) -> str:
        parts = [f"up to {self.slots} tickets a day (zero is allowed)", "each +100 or better"]
        if self.floor > 0:
            parts.append(f"estimated hit chance of at least {self.floor:.0%}")
        parts.append(f"edge of at least {self.min_value_after_shade:+.0%} after lowering every leg by {self.value_shade * 100:.0f} points")
        parts.append("each player on " + ("any number of tickets" if self.max_per_player is None else f"at most {self.max_per_player} ticket{'s' if self.max_per_player != 1 else ''} a day"))
        return "; ".join(parts)


LEGACY = TicketPolicy(policy_id="legacy-2026-10-09", status="IN_FORCE_UNTIL_2026-10-10_PAUSED")
# The proposal changes FOUR numbers and keeps the rest (the +100 bar, the 5% EV bar on recorded probabilities, the 3-point haircut, five slots):
#   floor              0%  -> 25%  "strong": about one in four or better; for two legs, each at least a coin flip (0.5 x 0.5), the region where the validation data are densest
#   min value after    0%  -> +3%  "meaningful": the edge that is left after the 3-point haircut must exceed what unverified Ontario price differences and the unknown void rules could take
#   per player         2   -> 1    one bad night cannot sink several tickets (docs/POSTMORTEM_2026-10-10.md: shared legs raise the chance that no ticket wins)
#   per game           3   -> 2    and the same for a game
# An earlier draft (30% floor, 5-point haircut) recorded nothing on either audited day on entry information alone, so it was not practical; that is why it was replaced, not any result.
PROPOSED = TicketPolicy(policy_id="restart-proposal-2026-10-10b", status="PROPOSED_NOT_VALIDATED_AWAITING_OWNER_APPROVAL",
                        floor=0.25, value_shade=0.03, min_value_after_shade=0.03, max_per_leg=1, max_per_game=2, max_per_player=1)
