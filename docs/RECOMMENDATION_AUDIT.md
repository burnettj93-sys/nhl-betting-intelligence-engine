# Player recommendation audit: beyond Hyry and Bourque

`python3 deploy/audit_recommendations.py --out docs/validation/recommendation_audit.json` (read-only, no network, deterministic sample; seed in the report).
The named audits stay: Hyry (`docs/PRODUCT_COMPLETION_CHECKLIST.md` §3 — small-sample over-prediction, floor moved 20 → 40 games) and Bourque (`docs/BOURQUE_AUDIT.md` — false trade alert, no-edge leg, three tickets on one player).

## What each sampled player is checked for
| Check | Defect it guards against | Fails when |
|---|---|---|
| Identity and team | Bourque: "now on DAL (trade)" from a stale identity corpus | the roster sync's team ≠ the team shown, or the revalidation check raises `TEAM_CHANGED` for his own next game |
| Small-sample gate | Hyry: priced with 28 games although the model over-predicts below 40 | fewer than 40 games but marked pricing-eligible, not flagged limited, missing the "not a confident recommendation" line, or present in a published option |
| Explanation | cards that claimed a line or power-play unit nobody reported | no "why" lines, the usage tier not labelled inferred, or "Reported by" shown without a reported line/PP (or the reverse) |
| Model sanity | a wrong input hiding behind a plausible-looking number | shots 2+ or points 1+ differs from his own last-60-game rate by more than 20 points (a prompt to look, not a failure) |

The audit can fail: `tests/test_audit_recommendations.py` feeds it a wrong team, a 19-game player marked eligible, and a 10-game player inside a published option, and each is caught.

## Result on the data published 2026-10-09 (630 skaters in the product)
23 players sampled: Hyry, Bourque, Coleman, five players under the 40-game floor (3–17 games), five tier 1–2 forwards, five tier 3–4 forwards, five defensemen, drawn at random within each group.
**All 23 passed every check.** No team mismatch, no false `TEAM_CHANGED`, every small-sample player unpriced and absent from options, usage always labelled inferred and "Reported by" absent for all of them (no permitted
lineup source is connected). The largest model-versus-own-rate gaps were 11 points (Hanifin shots 2+: model lower than his 60-game rate), 11 (Hyry points 1+) and 9 — inside the 8–10 point player-level error measured in testing; none reached 20.

## What this does and does not show
* It shows the two fixes hold across forwards, defensemen, new players and veterans, not only for the two players that exposed them.
* It does **not** show the recommendations are profitable, and it cannot see injuries, in-game exits or scratches (no source). A pass means "no input failed that we can check", the same standard used before calling Bourque's losses anything.
* The sample is the players with a scheduled game in the stored product state, not a random draw of every player ever offered. Re-run it after any model or roster change.
