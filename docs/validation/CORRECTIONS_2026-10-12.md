# Corrections to prediction scoring and daily reviews (2026-10-12)

Corrections never overwrite the frozen entries; they change how outcomes are graded and reported. The original outputs are kept beside the corrected ones.

## 1. Moneylines were graded as players (scorer revision 1 → 2)
`deploy/score_selection_shadow.py` looked every logged row up as a player. A moneyline row carries a team abbreviation, so it came back "did not play". Revision 2 grades a moneyline as a **team outcome** from the official final score
(shootouts and overtime included; a game not final stays pending; a final with no score, or a team that was not in the game, stays unresolved and is never guessed) and reports moneylines on their own.

| Report field | Revision 1 (`shadow_scoring_rev1_2026-10-12.json`) | Revision 2 (`shadow_scoring_rev2_2026-10-12.json`) |
|---|---|---|
| Frozen snapshots / distinct legs | 13 / 533 | 13 / 533 (unchanged: the frozen log was not touched) |
| Prop legs resolved | 340 | 340 (unchanged) |
| "Player did not play" (leg rows) | 33 | 9 (all lines of 2 players: Konecny Oct 10, Dubois Oct 11) |
| Players who did not play | not reported | 2 |
| Moneyline legs graded | 0 (24 counted as did-not-play) | 24 graded as team outcomes (12 games; 50.0% won against 52.2% implied before margin) |

The prop tables, the model-versus-price fit and the edge-filter results are unchanged: they never included the moneyline rows' outcomes, only the participation count was wrong.

## 2. Daily review participation line
The daily review (`operational/daily_review.py`) inherited the inflated count ("4 logged leg(s) belong to players who did not play" on the review of 2026-10-11, where the real figure was 1 player). It now uses `players_who_did_not_play`,
carries the scorer revision and lists logging gaps. The published review (the JSON snapshot section) was regenerated from the frozen log at 16:02 UTC on 2026-10-12; the markdown report now carries the frozen-prediction section (it had none, so the earlier file had nothing to supersede). The pre-correction figures are kept in `shadow_scoring_rev1_2026-10-12.json`.

## 3. The nine-hour logging gap
The frozen-prediction log was written only inside the paused branch of the cycle. After recording resumed (2026-10-11 15:29 UTC) no snapshot was written until the fix was deployed (2026-10-12 00:42 UTC), so predictions priced in that window
(including the pregame wave of 2026-10-11, a 7-leg, 2-game pool at 21:49 UTC) were **not frozen**. They are not reconstructed: nothing was added to `selection_shadow.jsonl`. The gap is recorded in `selection_shadow_gaps.jsonl`
(`LOGGER_NOT_WRITTEN_WHILE_RECORDING_ON`, 15:29:23Z → 00:42:15Z), is reported by the scorer (`logging_gaps`) and the daily review, and any later stretch of more than 90 minutes in the daytime without a log check is noted automatically (`NO_CHECKS`).
Logging is now covered by tests for paused, resumed with a ticket, resumed with no ticket, and no fresh legs (`tests/test_shadow_scoring.py`).
