"""
The ticket-selection policy as data.

Why this exists. The objective is "up to five worthwhile +100-or-better parlays with a strong estimated chance of hitting". Until 2026-10-10 the selector enforced "+100 or better"
and "positive modelled edge" but not "a strong chance of hitting" (no floor on a ticket's estimated hit chance) and treated "worthwhile" as an edge of at least zero after a small haircut.
The postmortem (docs/POSTMORTEM_2026-10-10.md) found tickets accepted with hit chances of 17-21% and margins of +0.05%, +0.42% and +0.99%.

Two policies are defined here, side by side, so the difference is visible and testable:

  LEGACY    exactly what ran on 2026-10-07 .. 2026-10-09 (minus the per-player limit added on 2026-10-09, which is included): the numbers the engine's module constants carry.
  PROPOSED  the restart proposal under owner review. Every number is a JUDGEMENT, not a validated result: the floor is a reading of "strong", the value margin is a plain safety margin (it does NOT verify
            Ontario prices or the sportsbook's void and parlay-reduction rules, which stay unverified), and the exposure limits follow from how shared legs raise the chance that no ticket wins. None was fitted to any losing or winning ticket,
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
    mode: str = "legacy"                 # "anchor": legs are scored by the player's own record / the market, ticket-level value, up to five legs (research/real_market_parlay/anchor.py)
    anchor_cap: float | None = None      # anchor mode: a shots leg's final estimate is held to the model's calibrated probability plus this. None = the original +0.10 (anchor.ANCHOR_CAP_V1); left out of as_dict() when None so the digest of a policy written before this field existed is unchanged
    max_combined_decimal: float | None = None   # a ticket whose combined price is longer than this is not selected (6.0 = +500). None = no upper limit; left out of as_dict() when None so earlier digests are unchanged

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if d.get("anchor_cap") is None:
            d.pop("anchor_cap", None)
        if d.get("max_combined_decimal") is None:
            d.pop("max_combined_decimal", None)
        return d

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:16]

    def summary(self) -> str:
        if self.mode == "anchor":
            top = f" and no longer than +{round((self.max_combined_decimal - 1) * 100)}" if self.max_combined_decimal else ""
            return (f"up to {self.slots} ticket{'s' if self.slots != 1 else ''} a day (zero is allowed); each +100 or better{top}, 2 to 5 legs (the fewest that qualify), every leg from a different game; legs are well supported (2+ shots for a player who reaches the line in "
                    f"most of his last 30 games, or a moneyline at the market's own chance), never points or goal bets; estimated hit chance of at least {self.floor:.0%}; estimated value of at least "
                    f"{self.min_estimated_ev:+.0%}, at least {self.min_value_after_shade:+.0%} after lowering every leg by {self.value_shade * 100:.0f} points, and not negative if the true chance is halfway "
                    f"between the player's record and the price; ranked by estimated hit chance; each player on at most {self.max_per_player} ticket a day")
        parts = [f"up to {self.slots} tickets a day (zero is allowed)", "each +100 or better"]
        if self.floor > 0:
            parts.append(f"estimated hit chance of at least {self.floor:.0%}")
        parts.append(f"edge of at least {self.min_value_after_shade:+.0%} after lowering every leg by {self.value_shade * 100:.0f} points")
        parts.append("each player on " + ("any number of tickets" if self.max_per_player is None else f"at most {self.max_per_player} ticket{'s' if self.max_per_player != 1 else ''} a day"))
        return "; ".join(parts)


LEGACY = TicketPolicy(policy_id="legacy-2026-10-09", status="IN_FORCE_UNTIL_2026-10-10_PAUSED")
# 2026-10-11 anchor policy (below): replaces the 2026-10-10 numbers. The rules it follows are documented in research/real_market_parlay/anchor.py.
# (The earlier proposal) changed FOUR numbers and keeps the rest (the +100 bar, the 5% EV bar on recorded probabilities, the 3-point haircut, five slots):
#   floor              0%  -> 25%  "strong": about one in four or better. A two-leg ticket reaches 25% when its legs' probabilities MULTIPLY to 0.25, i.e. their geometric average is 50%
#                                    (50% x 50%, or 70% x 36%, or 56% x 45%); it is not a requirement that both legs exceed 50%. Three legs need an average of about 63%.
#   min value after    0%  -> +3%  "meaningful": the edge that remains after the 3-point haircut must be at least 3%, so a ticket whose whole edge is a rounding of a price or a fraction of the model's error is
#                                    skipped. It is a safety margin only. It does NOT verify Ontario prices or the sportsbook's void and parlay-reduction rules (both unverified); it just refuses razor-thin edges.
#   per player         2   -> 1    one bad night cannot sink several tickets (docs/POSTMORTEM_2026-10-10.md: shared legs raise the chance that no ticket wins)
#   per game           3   -> 2    and the same for a game
# An earlier draft (30% floor, 5-point haircut) recorded nothing on either audited day on entry information alone, so it was not practical; that is why it was replaced, not any result.
# The 2026-10-10 restart proposal, kept (never active) so the floor/value/exposure mechanism it exercises stays tested and the shadow evidence logged under it stays interpretable.
EARLIER_PROPOSAL = TicketPolicy(policy_id="restart-proposal-2026-10-10b", status="SUPERSEDED_BY_ANCHOR_POLICY",
                                floor=0.25, value_shade=0.03, min_value_after_shade=0.03, max_per_leg=1, max_per_game=2, max_per_player=1)
# The policy the owner approved on 2026-10-11 (digest c236c526e00b9f04) and under which the tickets of 2026-10-12 were recorded. Its shots estimates held a leg to the model +0.10; the chronological
# validation shows that over-stated the capped 2+ legs by about 8 points (docs/PROPOSAL_ANCHOR_CAP.md). Superseded 2026-10-13; kept unchanged so its digest, its tickets and its logged predictions stay interpretable.
SUPERSEDED_BY = {"c236c526e00b9f04": "anchor-tickets-2026-10-13", "9f5e3448e10ff62e": "primary-slip-2026-10-14"}     # the digest of the 2026-10-11 policy -> the policy that replaced it (2026-10-13); the old digest is part of the record of every ticket recorded under it
ANCHOR_V1 = TicketPolicy(policy_id="anchor-tickets-2026-10-11", status="PROPOSED_NOT_VALIDATED_AWAITING_OWNER_APPROVAL", mode="anchor",   # (status text unchanged: it is part of the digest; SUPERSEDED_BY lists the successor)
                         floor=0.30, value_shade=0.03, min_value_after_shade=0.03, max_per_leg=1, max_per_game=3, max_per_player=1)
# The same policy with ONE change: the shots cap is +0.02 (chosen on the 2024 calibration season only, evaluated on 2025; deploy/anchor_cap_study.py). Every other number is unchanged.
# Up to five tickets a day. Superseded 2026-10-14 by the single-primary-slip workflow below; kept unchanged so its digest (9f5e3448e10ff62e) and the predictions logged under it stay interpretable.
ANCHOR_V2 = TicketPolicy(policy_id="anchor-tickets-2026-10-13", status="PROPOSED_NOT_VALIDATED_AWAITING_OWNER_APPROVAL", mode="anchor",
                         floor=0.30, value_shade=0.03, min_value_after_shade=0.03, max_per_leg=1, max_per_game=3, max_per_player=1, anchor_cap=0.02)
# 2026-10-14 workflow (owner instruction): ONE primary recommended parlay a day, combined price +100 to +500 inclusive, the fewest legs that qualify, ranked by estimated hit chance. Every leg rule and
# value test is the ANCHOR_V2 one (corrected +0.02 cap, floor 30%, value >= +5% and >= +3% after the 3-point haircut, midpoint test): nothing is loosened to produce a slip. Changed: slots 5 -> 1
# and the +500 ceiling. A day with no qualifying combination records nothing.
PROPOSED = TicketPolicy(policy_id="primary-slip-2026-10-14", status="PROPOSED_NOT_VALIDATED_AWAITING_OWNER_APPROVAL", mode="anchor", slots=1,
                        floor=0.30, value_shade=0.03, min_value_after_shade=0.03, max_per_leg=1, max_per_game=3, max_per_player=1, anchor_cap=0.02, max_combined_decimal=6.0)
