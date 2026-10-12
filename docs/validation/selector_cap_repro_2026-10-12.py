"""Reproduces the 2026-10-12 selector defect (read-only): the anchor selector's per-game leg cap (anchor.FRONTIER_PER_GAME = 4) hides tickets that pass every policy test.
Run from the repo root against the current runtime: it rebuilds today's candidate legs (no purchases), then (1) asks the real selector and (2) enumerates every one-leg-per-game ticket with NO cap."""
import datetime as dt, itertools, sys
sys.path.insert(0, ".")
import db
from operational import daily_tickets, ticket_policy
from research.real_market_parlay import anchor, engine as rmp
now = dt.datetime.now(dt.timezone.utc)
legs = daily_tickets.collect_candidate_legs(db.get_conn(), now)["legs"]
pol = ticket_policy.active()
print("policy", pol.policy_id, pol.digest(), "time", now.isoformat())
sel = rmp.select_tickets(legs, policy=pol)
print("selector tickets:", len(sel["tickets"]), "|", sel["reason"][:220])
pool = anchor.prepare_pool(rmp.dedupe_legs_by_economic_identity([l for l in legs if rmp.leg_is_eligible(l)]))
by = {}
for l in pool:
    by.setdefault(l.game_id, []).append(l)
print("supported legs per game:", {g: len(v) for g, v in by.items()})
passing = []
for size in (2, 3):
    for gs in itertools.combinations(list(by), size):
        for grp in itertools.product(*(by[g] for g in gs)):
            c = rmp._evaluate_combo(list(grp))
            if c and c.combined_decimal >= pol.min_combined_decimal and c.ev_estimated >= pol.min_estimated_ev and c.ev_conservative >= 0 and rmp._policy_veto(c, pol) is None:
                passing.append(c)
print("tickets passing every policy test with no cap:", len(passing))
for c in sorted(passing, key=lambda c: -c.joint_probability):
    print(f"  hit {c.joint_probability:.3f}  price {c.estimated_combo_price:+.0f}  value {c.ev_estimated:+.3f}  after-haircut {c.ev_conservative:+.3f}  ", [(l.participant_name, l.threshold, l.american_price) for l in c.legs])
