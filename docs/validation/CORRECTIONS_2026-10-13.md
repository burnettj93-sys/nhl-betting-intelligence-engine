# Correction, 2026-10-13: "legs on selected tickets" lost legs first logged before they were selected

**Found by:** reading the Oct 12 daily postmortem after settlement. It said "legs on selected tickets: 1 player-game", but the two recorded tickets had four legs.

**Cause:** the frozen-prediction scorer counts each leg once, at its first logged snapshot (so nothing is chosen with hindsight). The "selected" flag was read from that first snapshot only. Three of the four recorded legs were first logged at 12:04Z, before any ticket used them, and were selected in the 21:18Z snapshot, so they were missed.

**Fix (scorer revision stays 2; the table gains the field `selected`):** a leg's selection is taken from the first snapshot in which it was on a proposed or recorded ticket, with the price and estimates frozen in that snapshot. The candidate pool still uses the first look. Regression test: `TestFrozenPredictionScoring.test_a_leg_selected_in_a_later_snapshot_is_still_a_leg_on_a_selected_ticket`.

**Effect on the Oct 12 numbers:** legs on selected tickets 4 (not 1), 3 of 4 happened (75%), mean final estimate 69.6%, price implied 64.6%. Four legs in two games is too few to conclude anything; the policy is not changed. The earlier file `shadow_scoring_rev2_2026-10-13.json` is kept unchanged as the audit trail; the corrected table is `shadow_scoring_rev3_2026-10-13.json`.
