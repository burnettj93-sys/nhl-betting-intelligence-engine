# Selector defect: a per-game cap hid qualifying tickets (found 2026-10-12, fixed the same day)

**What happened.** At 14:23 UTC the live candidate pool (18 supported legs in 3 games) held five tickets that pass every test of the approved policy `c236c526e00b9f04`
(+100 or better, estimated value ≥ +5%, ≥ +3% after lowering every leg 3 points, not negative halfway to the price, hit chance ≥ 30%). The recording selector returned none, and Today said "No ticket today".
The Best Options list, which searched a person's own legs directly, showed two of them. Reproduction: `selector_cap_repro_2026-10-12.py` and its output (run at 14:23 UTC, 12:04 UTC quotes).

**Cause.** `anchor.qualifying` kept only the 4 best non-dominated legs per game (`FRONTIER_PER_GAME = 4`) and only the 10 best games (`MAX_GAMES_SEARCHED = 10`) before combining. A leg with a longer price and a lower hit chance than four others
(Drake Batherson 2+ shots −145, Arseny Gritsyuk −120) is not dominated, so it is a legitimate partner; the cap removed it. Under the exposure rules at most two of the five could have been recorded
(Batherson + Eichel, +105, 54.9%; Gritsyuk + Quinn, +144, 46.7%).

| Hit | Price | Value | After haircut | Legs |
|---|---|---|---|---|
| 54.9% | +105 | +12.2% | +3.2% | Batherson 2+ (−145) + Eichel 2+ (−475) |
| 51.6% | +122 | +14.5% | +5.0% | Gritsyuk 2+ (−120) + Eichel 2+ (−475) |
| 46.7% | +144 | +14.1% | +4.3% | Gritsyuk 2+ (−120) + Quinn 2+ (−300) |
| 42.7% | +166 | +13.4% | +3.2% | Batherson 2+ + Hertl 2+ (−175) |
| 40.2% | +188 | +15.7% | +5.0% | Gritsyuk 2+ + Hertl 2+ |

**Fix (policy unchanged).** `anchor.qualifying` now searches every one-leg-per-game combination of 2–5 legs, with only two exact prunings: a partial ticket whose hit chance is already under the policy's floor is abandoned (more legs can only lower it),
and the +100 and value tests are checked on plain numbers before an object is built. There is one candidate evaluation: the recording selector, Today, the provisional board and Best Options all call it (`player_options` reads
the same qualifying set), a per-search cache answers the three calls of one trader cycle once, and a safety bound reports `truncated` instead of silently dropping tickets. Thresholds, exposure limits (one ticket per player and leg, three per game),
the early-price cap, freshness checks and recording-time revalidation are untouched.

**Tests** (`tests/test_anchor_tickets.py::TestTheSearchHidesNothing`): the real 2026-10-12 pool (17 shots legs and the NJD moneyline) returns exactly the five tickets and equals an independent enumeration; the selector offers Batherson + Eichel first and
still applies the exposure limits (two tickets); six random pools match the independent enumeration; a 16-game night (83 supported legs) is searched completely and not truncated (about 28 s worst case, once per cycle);
Best Options, the provisional board and recording select from the same qualifying set.

**Not backfilled.** The morning prices behind these tickets expired at about 14:34 UTC. Nothing was recorded on them; recording happens only through the normal cycle on prices that are fresh when it runs.
