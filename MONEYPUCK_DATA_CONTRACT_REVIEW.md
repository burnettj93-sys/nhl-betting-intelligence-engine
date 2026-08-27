# MoneyPuck Historical + Daily Data Foundation Review

**This turn is inspection-only.** No model code was changed. No Elo variants
were implemented. No daily sync was built. Confirmed unchanged: 322/322
tests passing, `nhl.db` untouched (mtime still 2026-08-26 20:15:02), the
real NHL results corpus untouched. All fetches were of files MoneyPuck's
own `data.htm` page links to and advertises for download — nothing was
scraped from pages not listed there.

Raw samples, both data dictionaries, and a machine-readable summary are
saved under `research/moneypuck_review/` (data dictionaries in full;
season-level team CSV in full; everything else recorded as schema +
sample rows + checksum rather than bulk-downloaded, per your explicit
instruction not to bulk-download historical files this turn).

---

## A. Datasets inspected

1. Season-level team data, 2024-25 (`teams.csv`)
2. Game-by-game team data, all teams, all seasons 2008-09 → 2025-26 (`all_teams.csv`)
3. Game-by-game goalie data, 2024-25 (ZIP)
4. Game-by-game skater data, 2024-25 (ZIP)
5. Game-by-game line/pairing data, 2024-25 (ZIP)
6. Shot-level data, 2024-25 (ZIP) — schema/sample only, not fully materialized (see W)
7. Player biography data (`allPlayersLookup.csv`)
8. Players data dictionary
9. Shots data dictionary
10. `data.htm` and `about.htm` (licensing text, methodology text, download index)

**Not inspected this turn** (deliberately, per your "no bulk download" instruction): individual-season skater/goalie/line/team CSVs for any season other than 2024-25; the multi-season combined ZIPs (`skaters_2008_to_2024.zip` etc.); any shot-data season besides 2024-25, and even that one only partially (see W); the player-search career tool.

## B. Exact download URLs/files

| Dataset | URL |
|---|---|
| Team season-level, 2024-25 | `https://moneypuck.com/moneypuck/playerData/seasonSummary/2024/regular/teams.csv` |
| Team game-by-game, all seasons | `https://moneypuck.com/moneypuck/playerData/careers/gameByGame/all_teams.csv` |
| Goalie game-by-game, 2024-25 | `https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary/goalies/2024.zip` |
| Skater game-by-game, 2024-25 | `https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary/skaters/2024.zip` |
| Line/pairing game-by-game, 2024-25 | `https://peter-tanner.com/moneypuck/downloads/seasonPlayersSummary/lines/2024.zip` |
| Shots, 2024-25 | `https://peter-tanner.com/moneypuck/downloads/shots_2024.zip` |
| Player bios | `https://moneypuck.com/moneypuck/playerData/playerBios/allPlayersLookup.csv` |
| Players data dictionary | `https://peter-tanner.com/moneypuck/downloads/MoneyPuckDataDictionaryForPlayers.csv` |
| Shots data dictionary | `https://peter-tanner.com/moneypuck/downloads/MoneyPuck_Shot_Data_Dictionary.csv` |

Two distinct hosts are in play: `moneypuck.com` serves the site and the season-summary/game-by-game-team/player-bio CSVs directly; `peter-tanner.com` (a Cloudflare-fronted static file host, evidently MoneyPuck's data CDN) serves every ZIP archive and both data dictionaries. The two origins do not share CORS permissions with each other (confirmed: a fetch from a `moneypuck.com`-hosted page to `peter-tanner.com` fails with a CORS error) — a future daily-sync implementation needs to treat these as two separate configured hosts, not one.

**Access method used**: real browser fetches (same-origin `fetch()` per host, same technique as the NHL corpus work), because this sandbox's own network cannot reach either host directly (confirmed: `curl` to `moneypuck.com` returns a synthetic 403 / connection failure from this container, the same sandbox-network restriction seen throughout this project). Nothing was scraped via HTML parsing — every file above is a direct data-file download link MoneyPuck's own `data.htm` publishes.

## C. Checksums

Computed via `crypto.subtle.digest('SHA-256', ...)` in-browser on the fully-decoded content, for the files small enough to fully materialize:

| File | SHA-256 |
|---|---|
| `MoneyPuckDataDictionaryForPlayers.csv` | `74c5a09f5eb5e12c03838c9efea0a84d9b33024193a1a29b9c67d998f5324a91` (note: 65 hex chars printed by the browser's own hex-join — the leading 64 are the real digest; this is a formatting artifact of the join code, not a corrupted hash — flagged here rather than silently corrected) |
| `teams.csv` (2024-25 season-level) | `c56d1b1fd36288b4b4751ef2c51c05813efe2011aa8d5c17db11556956cff5b1` (same trailing-character artifact) |

**Checksums NOT computed** for `all_teams.csv` (126,483,024 characters / ~126MB decoded — hashing succeeded technically but was not re-run cleanly after discovering the join-formatting bug above, and is not load-bearing for a review step), nor for any of the three ZIP archives or the shots file — those were inspected via schema/row-count sampling (see D, W) rather than full materialization, consistent with your "do not bulk-download" instruction. File sizes (a weaker but honest substitute for a full checksum) are recorded in D and W instead.

**Correction note**: the SHA-256 hex-encoding helper used in-browser had an off-by-one bug (an extra character was appended once). This affects only the display string above, not any file's integrity — no downloaded byte was altered — but it means the two hashes above should be treated as unverified pending a clean re-hash before anyone relies on them for provenance, and no other checksum in this report should be trusted without regenerating it. Flagging plainly rather than quietly re-running and overwriting the error.

## D. File sizes

| File | Size |
|---|---|
| `MoneyPuckDataDictionaryForPlayers.csv` | 15,040 bytes |
| `MoneyPuck_Shot_Data_Dictionary.csv` | 15,680 bytes |
| `teams.csv` (2024-25 season-level) | 99,536 bytes decoded (36,494 bytes gzip-compressed over the wire) |
| `all_teams.csv` (all seasons, game-by-game team) | 126,483,024 bytes decoded (~126 MB) |
| `allPlayersLookup.csv` (player bios) | 218,227 bytes |
| `goalies/2024.zip` | 570,632 bytes compressed → 2,845,139 bytes decoded |
| `skaters/2024.zip` | 23,427,199 bytes compressed → 169,511,756 bytes decoded (~170 MB) |
| `lines/2024.zip` | 6,769,567 bytes compressed → 44,572,042 bytes decoded (~45 MB) |
| `shots_2024.zip` | 20,138,716 bytes compressed → 68,879,950 bytes decoded (~69 MB) |

The scale jump from goalies (2.8MB/season) to skaters (170MB/season) to the full shot log (69MB/season, and that's one of 18 seasons) is exactly why "do not bulk-download every historical shot file yet" was the right instruction — a full multi-season skaters + shots backfill would be on the order of several gigabytes decoded, not something to pull through a browser-fetch bridge casually.

## E. Exact columns

Full column lists are saved verbatim in the two data-dictionary files under `research/moneypuck_review/data_dictionaries/`. Summary by dataset:

- **Team season-level / team game-by-game**: 107 columns (season-level) / 111 columns (game-by-game, which adds `gameId, playerTeam, opposingTeam, home_or_away, gameDate, playoffGame` around the same core metric block). Metric block: `xGoalsPercentage, corsiPercentage, fenwickPercentage, iceTime`, then a full `*For`/`*Against` mirrored block covering `xGoals, xRebounds, xFreeze, xPlayStopped, xPlayContinuedInZone/OutsideZone, flurryAdjustedxGoals, scoreVenueAdjustedxGoals, shotsOnGoal, missedShots, blockedShotAttempts, shotAttempts, goals, rebounds, reboundGoals, freeze, playStopped, savedShotsOnGoal, penalties, penalityMinutes, faceOffsWon, hits, takeaways, giveaways, low/medium/highDangerShots, low/medium/highDangerxGoals, low/medium/highDangerGoals, scoreAdjustedShotsAttempts, unblockedShotAttempts, dZoneGiveaways`, plus derived rebound-credit fields.
- **Goalie game-by-game**: 38 columns — `playerId, name, gameId, season, playerTeam, opposingTeam, home_or_away, gameDate, position, situation, icetime, xGoals, goals, unblocked_shot_attempts, xRebounds, rebounds, xFreeze, freeze, xOnGoal, ongoal, xPlayStopped, playStopped, xPlayContinuedInZone, playContinuedInZone, xPlayContinuedOutsideZone, playContinuedOutsideZone, flurryAdjustedxGoals, low/medium/highDangerShots, low/medium/highDangerxGoals, low/medium/highDangerGoals, blocked_shot_attempts, penalityMinutes, penalties`.
- **Skater game-by-game**: 157 columns — `playerId, name, gameId, season, playerTeam, opposingTeam, home_or_away, gameDate, position, situation, icetime, shifts, gameScore`, then the full `I_F_*` (individual-for), `OnIce_F_*`/`OnIce_A_*` (on-ice for/against), `OffIce_F_*`/`OffIce_A_*` (off-ice for/against) blocks documented in the data dictionary, plus faceoff/TOI/shift-start-and-end breakdowns.
- **Line/pairing game-by-game**: 111 columns, structurally identical to the team file's `*For`/`*Against` block, keyed by `lineId` instead of `team`.
- **Shots**: 137 columns in the real 2024-25 file — 13 more than the 124 the data dictionary describes (see Q for what this implies about documentation currency).
- **Player bios**: 11 columns — `playerId, name, position, team, birthDate, weight, height, nationality, shootsCatches, primaryNumber, primaryPosition`.

## F. Data dictionary findings

Both dictionaries are real, substantive documents (not stubs), and both are **stale relative to the current data**:

- Players dictionary — last-modified 2025-05-04, one full season behind the current 2025-26 data.
- Shots dictionary — last-modified 2019-09-04, over six years stale. Its own embedded "Games With Missing Data" and "Shots per season" tables stop at the 2018-19 season, and the real 2024-25 shots file has 137 columns against the dictionary's documented 124 — 13 real columns (at minimum `wentToOT`, `wentToShootout`, `homeTeamScore`, `roadTeamCode`, `roadTeamScore`, `shotGoalProbability`, `gameOver`, `penaltyLength` among them, confirmed present in the live header) are **undocumented** in the shipped dictionary. `shotGoalProbability` appearing as a field distinct from `xGoal` in the same row is worth flagging explicitly: whether these are two different model outputs or a renamed/legacy duplicate is not explained anywhere in the available documentation — this is exactly the kind of thing Part 2's "verify rather than assume" instruction exists for, and I'm reporting it as an open question rather than guessing.
- The `situation` general-terms definition, the danger-zone thresholds (<8% / 8-20% / >=20%), and the `I_F_`/`OnIce_F_`/`OnIce_A_`/`OffIce_F_`/`OffIce_A_` prefix conventions are all documented clearly and match the real column names exactly — the dictionary's terminology section is trustworthy even where its column list has drifted.

## G. Row grain (verified against real downloaded data, not assumed)

| Dataset | Grain |
|---|---|
| Team season-level | One row per **team × situation** per season (32 teams × 5 situations = 160 rows for 2024-25, verified exactly) |
| Team game-by-game | One row per **team × game × situation** (confirmed: each game_id appears twice, once per team, ×5 situations = 10 rows/game) |
| Goalie game-by-game | One row per **goalie × game × situation** |
| Skater game-by-game | One row per **skater × game × situation** |
| Line/pairing game-by-game | One row per **line/pair × game × situation** |
| Shots | One row per **individual shot attempt** (unblocked only — saved-on-goal, missed, and goals; blocked shots excluded per the site's own description) |

**Situation values, confirmed directly from real downloaded rows** (not assumed from the dictionary text): `all, 5on5, 5on4, 4on5, other` — exactly five, exactly matching your speculated examples, for team-level, season-level, and goalie files. For lines, only `5on5` was observed in the sampled window (first ~5,000 of 83,228 rows for the 2024-25 season) — I did not confirm the other four situations are absent from line data; that's a partial finding, not a negative one, and I'm reporting it as such rather than overclaiming.

## H. Canonical keys

- **Team**: `team` abbreviation (season-level/game-by-game) — see the historical-code caveat in J below.
- **Player** (skater/goalie): `playerId`, confirmed to be **the NHL's own player ID** (matches the ID space used in our real NHL corpus's boxscores and rosters) — a direct join key, no crosswalk needed.
- **Game**: `gameId` / `game_id`, confirmed to be **the NHL's own game_id** in the identical 10-digit format our real NHL corpus uses (`season(4) + gameType(2) + sequence(4)`) — verified by exact match against 5 real games (see J). This is the single most important finding for architecture purposes: MoneyPuck requires no separate event-mapping/crosswalk layer against the NHL API corpus for games or players — a join is `game_id = game_id`, full stop.
- **Line/pair**: `lineId`, which is a **concatenation of the constituent players' NHL playerIds with no separator** (e.g. `847398684779528484153` decodes to three 7-digit NHL player IDs run together) — technically stable and reproducible, but not human-readable and brittle to parse (a naive fixed-width split only works because NHL player IDs happen to currently all be 7 digits; this should be treated as "decode via the roster, don't regex the concatenation" if ever implemented).

## I. Historical coverage

- Skater/goalie/line/team data: individual-season files exist for every season from **2008-09 through 2025-26** (18 seasons), plus multi-season combined archives (2008-2024) for each. `all_teams.csv` (game-by-game, all seasons) directly confirmed to contain distinct `season` values `2008` through `2025` inclusive.
- Shot data: individual-season files from **2007-08 through 2025-26** (19 seasons; one earlier than the player/team data), plus combined archives (`2007-2024`, `2018-2024`).
- **Regular season coverage**: confirmed present (`gameType`/`situation` structure applies uniformly).
- **Playoff coverage**: confirmed present in the SAME files — `all_teams.csv` carries a trailing `playoffGame` flag (0/1) rather than separating playoffs into their own download; all 3 of our real 2025-26 Stanley Cup Final games (`2025030412/413/414`) were found in it with `playoffGame=1`.
- **Missing/incomplete seasons**: none observed in the 2022-23 → 2025-26 range that matters for our real NHL corpus. The shots data dictionary's own stale table lists 4 specific "games with missing data" (2 in the 2008-09 season, 1 in 2007-08, 1 in 2009-10) — all from seasons well outside our corpus's 2022-23–2025-26 window, so not a live concern for this project, but worth carrying forward if this project ever reaches back further.
- Focused on our 4-season baseline specifically: 2022-23, 2023-24, 2024-25, and 2025-26 are all present as distinct `season` values (`2022`, `2023`, `2024`, `2025`) in the game-by-game team file.

## J. Cross-check vs. NHL corpus

**Result: zero discrepancies found**, across 5 sampled games spanning both a regular-season pair and all 3 real Stanley Cup Final games:

| game_id | NHL corpus (home/away, score) | MoneyPuck (home/away, score) | SOG match |
|---|---|---|---|
| 2024020010 | VGK 8 – COL 4 | VGK 8 – COL 4 | ✅ exact (VGK 21, COL 32) |
| 2024020023 | VGK 4 – STL 3 | VGK 4 – STL 3 | ✅ exact |
| 2025030412 | CAR 4 – VGK 3 (OT) | CAR 4 – VGK 3 | ✅ exact (CAR 26, VGK 26) |
| 2025030413 | VGK 5 – CAR 4 (2OT) | VGK 5 – CAR 4 | ✅ exact (VGK 35, CAR 33) |
| 2025030414 | VGK 3 – CAR 5 (REG) | VGK 3 – CAR 5 | ✅ exact (VGK 21, CAR 28) |

Goals, home/away assignment, and shots-on-goal all matched exactly on every sampled game (SOG cross-checked against the genuine boxscore JSON in `tmp_live_contract/`, not the results corpus, since the results corpus doesn't carry SOG). `game_id` matched directly with no fuzzy mapping. OT/SO classification wasn't directly cross-checked field-for-field here (MoneyPuck's team file doesn't carry a `period_type`-style column; that lives in the shots file as `wentToOT`/`wentToShootout`, which I confirmed exists as a column but did not pull a value for in this pass) — flagging this as **not yet verified** rather than assuming it agrees.

**No disagreement was found or silently resolved** — this is a small sample (5 games) and should not be read as a guarantee of zero disagreement at scale; it's real, honest evidence that the join key and the underlying numbers are trustworthy on the games checked, nothing more.

## K. Useful team fields

All of: `xGoalsFor/Against` (and the score/venue/flurry-adjusted variants), `corsiPercentage`, `fenwickPercentage`, shot attempts and shots-on-goal for/against, `low/medium/highDanger{Shots,xGoals,Goals}` for/against, `reboundxGoals`, situation-segmented rows enabling any 5v5-only or PP/PK-only slice, all keyed by real `game_id` and joinable to our corpus with zero translation.

## L. xG/shot-quality fields — see Part 3 classification below

| Capability | Status | Fields |
|---|---|---|
| xGF / xGA | **AVAILABLE** | `xGoalsFor`, `xGoalsAgainst` (team, per situation) |
| xG% | **AVAILABLE** | `xGoalsPercentage` (already computed as xGF/(xGF+xGA)) |
| 5v5 xGF/xGA/xG% | **AVAILABLE** | same fields, filtered to `situation == '5on5'` |
| Score-adjusted xG | **AVAILABLE** | `scoreVenueAdjustedxGoalsFor/Against`, `scoreAdjustedShotsAttempts` |
| Shots for/against | **AVAILABLE** | `shotsOnGoalFor/Against` |
| Shot attempts (Corsi) | **AVAILABLE** | `shotAttemptsFor/Against`, `corsiPercentage` |
| High/medium/low-danger chances | **AVAILABLE** | `low/medium/highDangerShotsFor/Against`, thresholds defined in the dictionary (<8%/8-20%/≥20% goal probability) |
| Rebound chances | **AVAILABLE** | `reboundsFor/Against`, `xReboundsFor/Against`, `reboundGoalsFor/Against` |
| Rush chances | **PARTIAL** | not a team-level field; exists per-shot in the shots file as `shotRush` (a boolean per shot) — would need aggregation, not a ready-made team stat |
| Shooting quality (offense) | **AVAILABLE** | derivable from danger-zone shot/xGoal splits `For` |
| Defensive shot quality (shot suppression quality, not just volume) | **AVAILABLE** | same fields `Against` |
| Recent rolling xG form | **PARTIAL** | not precomputed anywhere — the game-by-game file gives the raw per-game series; a rolling window would be our own computation on top of it |
| Opponent-adjusted team performance | **NOT AVAILABLE** | no opponent-strength adjustment exists in any MoneyPuck file; `opposingTeam` is present as an identifier only, not as an adjustment |

## M. Special-teams fields

| Capability | Status | Fields |
|---|---|---|
| PP opportunities | **PARTIAL** | not a direct count column; derivable by counting `situation == '5on4'` game-rows with `icetime > 0`, or from the shots file's penalty fields — not a ready-made "PP opportunities" number |
| PP goals | **AVAILABLE** | `goalsFor` filtered to `situation == '5on4'` |
| PP xG | **AVAILABLE** | `xGoalsFor` filtered to `situation == '5on4'` |
| PP shot generation | **AVAILABLE** | `shotsOnGoalFor`/`shotAttemptsFor` filtered to `5on4` |
| PP xG per minute/opportunity | **PARTIAL** | numerator available (`xGoalsFor` at 5on4); minutes available (`iceTime` at 5on4); "per opportunity" needs the PP-opportunity count noted as PARTIAL above |
| PK opportunities | **PARTIAL** | same caveat as PP opportunities, mirrored at `4on5` |
| PK goals against | **AVAILABLE** | `goalsAgainst` filtered to `situation == '4on5'` |
| PK xGA | **AVAILABLE** | `xGoalsAgainst` filtered to `4on5` |
| PK shot suppression | **AVAILABLE** | `shotsOnGoalAgainst`/`shotAttemptsAgainst` filtered to `4on5` |
| Shorthanded metrics (SH goals/xG for the PK team) | **AVAILABLE** | `goalsFor`/`xGoalsFor` filtered to `4on5` (the shorthanded team's own offense while killing a penalty) |

## N. Goalie fields

| Capability | Status | Fields |
|---|---|---|
| Save percentage | **AVAILABLE** (derived) | `(ongoal - goals) / ongoal` from goalie game-by-game data — not a precomputed column, but trivial to derive |
| Expected goals against | **AVAILABLE** | `xGoals` (goalie file; represents xG of shots faced) |
| Goals saved above expected (GSAx) | **AVAILABLE** (derived) | `xGoals - goals`, both present per goalie/game/situation — this is exactly the GSAx formula, just not precomputed under that name |
| Low/medium/high-danger save performance | **AVAILABLE** | `low/medium/highDangerShots`, `low/medium/highDangerxGoals`, `low/medium/highDangerGoals` all present at goalie grain |
| Shots faced | **AVAILABLE** | `ongoal`, `unblocked_shot_attempts` |
| Starts/appearances | **AVAILABLE** (derived) | countable via `icetime > 0` rows per goalie per game; no explicit "starter" flag exists (see below) |
| Workload | **AVAILABLE** | `icetime` per game, summable into rolling workload — same PIT caveat as this project's already-deferred goalie-workload slice applies (see below) |
| Rolling goalie form | **PARTIAL** | raw per-game series available; no precomputed rolling window |
| Rest between appearances | **AVAILABLE** (derived) | computable from `gameDate` gaps between a goalie's appearance rows |

**HISTORICAL GOALIE PERFORMANCE vs. HISTORICAL PREGAME STARTER KNOWLEDGE — kept clearly separate, per your instruction**: everything in the table above is postgame-observed performance (it only exists once the goalie has actually played). MoneyPuck's game-by-game goalie file has no equivalent of "this goalie was the announced/expected starter before puck drop" — a goalie's row for a given game is only populated because they appeared in it. This does **not** solve the problem this project already identified and deferred (`HIGH-VALUE FORWARD FEATURE / HISTORICAL EVALUATION BLOCKED BY PREGAME STARTER-IDENTITY AVAILABILITY`): using a MoneyPuck goalie-appeared-in-game row as a historical pregame feature would be exactly the same look-ahead leakage already flagged for the NHL boxscore-derived starter identity, just from a different data provider. That deferral stands unchanged.

## O. Player fields

| Capability | Status | Fields |
|---|---|---|
| Goals/assists/points | **AVAILABLE** | `I_F_goals`, `I_F_primaryAssists`+`I_F_secondaryAssists`, `I_F_points` |
| Individual xG | **AVAILABLE** | `I_F_xGoals` |
| Shots/attempts | **AVAILABLE** | `I_F_shotsOnGoal`, `I_F_shotAttempts`, `I_F_missedShots`, `I_F_blockedShotAttempts` |
| TOI | **AVAILABLE** | `icetime` |
| Situation-specific TOI | **AVAILABLE** | same `icetime` field, one row per `situation` |
| On-ice xGF | **AVAILABLE** | `OnIce_F_xGoals` |
| On-ice xGA | **AVAILABLE** | `OnIce_A_xGoals` |
| Relative xG metrics (on-ice vs. off-ice, i.e. a WOWY-style split) | **AVAILABLE** | `onIce_xGoalsPercentage` vs. `offIce_xGoalsPercentage` are both present per player/game — this is the raw ingredient for relative/impact metrics, not a finished "relative xG" statistic itself |
| High-danger involvement | **AVAILABLE** | `I_F_highDangerShots`, `I_F_highDangerxGoals`, `I_F_highDangerGoals`, plus the `OnIce_F_`/`OnIce_A_` equivalents |
| Rolling player performance | **PARTIAL** | raw per-game series available (236,120 rows for one season alone); no precomputed rolling window |

**Could this improve or replace the current simple PPG EWMA heuristic (`models/player_model.py`)?** Yes, directionally — this data supports a materially richer player-quality signal (shot-quality-adjusted individual production, on-ice impact split from off-ice, situation-aware usage) than the current model's raw points-per-game EWMA. **This is raw material, not a finished model.** Per your explicit instruction, none of this should be described as RAPM, GAR, or xGAR — those are specific statistical modeling techniques (regression-adjusted, replacement-value, or expected-value player-impact models) that would need to actually be built from these columns; MoneyPuck supplies the on-ice/off-ice/individual event counts and xG components those techniques are usually built from, nothing more, and nothing here is currently RAPM/GAR/xGAR by itself.

## P. Line/pair fields

| Capability | Status | Fields |
|---|---|---|
| Forward line combinations | **AVAILABLE** | one row per unique 3-player (or 2-player D-pair) combination per game, keyed by `lineId` |
| Defense pairs | **AVAILABLE** | same file/mechanism, distinguished by the constituent players' positions |
| Shared TOI | **AVAILABLE** | `icetime` at the line/pair grain (the time that exact combination played together) |
| xGF/xGA/xG% | **AVAILABLE** | `xGoalsFor/Against`, `xGoalsPercentage`, identical structure to the team file |
| Shot share (Corsi) | **AVAILABLE** | `corsiPercentage`, `shotAttemptsFor/Against` |
| Goal share | **AVAILABLE** | `goalsFor/Against` |

**Historical completed-game chemistry/performance vs. pregame confirmed lineup knowledge — kept separate, per your instruction**: a line/pair row exists in this data only because that combination *actually played together* in that game — it is exactly as postgame-derived as the goalie appearance data above, and for the identical reason must never be used as if the combination were known before puck drop. This directly extends the same caveat Part 7 asked for, and the same one already governing the deferred goalie-workload slice.

## Q. Historical xG version/temporal semantics

Investigated via `about.htm`'s own methodology section (fetched directly, not inferred): **the site states the xGoals model "was built on over 50,000 goals and 800,000 shots in NHL regular season and playoff games from the 2007-2008 to 2014-2015 season"** — i.e. a training window ending a decade before the current 2025-26 data the model is still scoring. The methodology page:

- Does **not** state a model version number or date stamp for the xGoals model specifically.
- Does **not** state whether the model has been retrained since 2014-15.
- Does **not** state whether historical shots get rescored when/if the model changes.
- Does **not** publish a changelog of methodology changes.
- Lists 15 concrete input features (shot distance, angle, type, rebound/rush context, man-advantage state, etc.) and confirms the model is gradient-boosted.

Answering your six specific sub-questions directly:

```
1. Are old shots rescored when MoneyPuck changes its xG model?         UNKNOWN
2. Are historical xG values generated with a current model?            UNKNOWN
3. Is a model version stored?                                          NO
4. Can the historical version be identified?                           NO
5. Does MoneyPuck publish methodology/version changes?                 NO (no changelog found)
6. Could a later-trained model have generated xG for earlier seasons?  UNKNOWN — plausible but unconfirmed
```

**Classification: UNKNOWN.** Not CONTEMPORANEOUS HISTORICAL METRIC (there's no evidence the model was re-run at the time each historical season happened — the description reads as one model trained once on 2007-15 data), and not confirmed as RETROSPECTIVELY STANDARDIZED either (that would require confirming the *same* model scored every season uniformly, which isn't stated — it's equally possible the model was quietly updated at some point and old shots left unrescored). Per your instruction: saying UNKNOWN here, not guessing. Practical implication if this data is ever used for xG-based modeling: don't assume xG values are comparable in the exact same way across a 2010 season and a 2025 season without separately investigating this further — but this does **not** block the narrower, more defensible uses (shots-on-goal, danger-zone shot classification by fixed public thresholds, goal/assist counts) which don't depend on cross-season xG-model consistency.

Separately worth flagging (from E/F above): the shots file has 13 columns beyond what its own dictionary documents, including a second goal-probability-shaped field (`shotGoalProbability`) alongside `xGoal`. Whether that's a second model, a legacy duplicate, or something else entirely is **unresolved** — genuinely UNKNOWN, not something I'm going to guess at.

## R. ARCHIVAL_RESEARCH limitations

Everything downloaded this turn is retrospective — pulled today, describing games that already happened, in most cases years ago. Per your instruction, every record from this review is classified **ARCHIVAL_RESEARCH**, never `LIVE_OBSERVED`, and nothing was written anywhere with a fabricated historical `observed_at_utc`/`received_at_utc`/`captured_at_utc`. Nothing from this turn was written into `nhl.db` or any PIT table at all — everything lives under `research/moneypuck_review/`, entirely separate, exactly mirroring how the real NHL results corpus was kept separate last turn. If this data is ever used for a retrospective model comparison, the same **RESEARCH AVAILABILITY POLICY: STRICT PRIOR-GAME-DATE** already established for the NHL results corpus applies identically here: a target game on date `D` may only use MoneyPuck rows with `game_date < D`, never same-day, never future, and never an eventual goalie/lineup identity as if it were known pregame (this is the same rule already stated in Parts N and P above).

## S. LIVE_OBSERVED forward policy

Going forward, if this project starts downloading MoneyPuck files on a recurring (e.g. daily) basis, each such download's `downloaded_at_utc` becomes the genuine knowledge-time — a file fetched at `2026-10-13 06:30 UTC` describing a game played on `2026-10-12` gives this system knowledge as of `2026-10-13 06:30 UTC`, not the game date, exactly as you specified in Part 13. That distinction (`ARCHIVAL_RESEARCH` for today's backward-looking review vs. `LIVE_OBSERVED` for a future prospectively-collected snapshot) is a property of *when the download happened relative to the event*, not of the file format — the same CSV schema serves both, and the daily-sync design in Z is built around preserving that distinction explicitly rather than inferring it.

## T. Licensing findings

Quoted verbatim from `data.htm`:

> "The data below is free to use for non-commercial purposes and by journalists for ad-hoc use. Please clearly credit MoneyPuck.com in all cases where you are showing anything using our data as an input. For other purposes please inquire by messaging moneypuck.com@gmail.com."
>
> "Non-approved scraping of the MoneyPuck website will be blocked. Please message us for approval before scraping any data not listed on this page."

And from the players data dictionary file itself (same language, restated): "No guarantees are made to the quality of the data... The data is free to use for non-commercial purposes and by journalists for ad-hoc use... For other purposes please inquire."

Breaking this down against your specific asks:
- **Non-commercial-use language**: explicit — "free to use for non-commercial purposes."
- **Attribution requirement**: explicit and unconditional — "clearly credit MoneyPuck.com in all cases."
- **Research/journalist language**: explicit — "journalists for ad-hoc use" is named specifically; general research use isn't named separately from "non-commercial."
- **Commercial-use restriction**: explicit — anything beyond non-commercial/journalistic ad-hoc use requires messaging `moneypuck.com@gmail.com` first.
- **Permission/contact requirement**: explicit — `moneypuck.com@gmail.com`, stated twice.
- **Scraping restriction**: explicit, but scoped to "data not listed on this page" — everything fetched this turn was a file this page directly links to and describes as downloadable, so this review's activity does not fall under that restriction as written. A future *automated recurring daily* fetch job would still be prudent to disclose/confirm given the "non-approved scraping...will be blocked" language, even though it targets already-listed files — that's a judgment call for you, not something I'm resolving on your behalf.

**This project's use case is a betting-and-pricing engine.** Whether "non-commercial" covers a personal/private betting-analysis tool that never charges anyone or displays MoneyPuck data publicly is genuinely ambiguous from this text alone, and I am not characterizing the data as unrestricted, nor concluding it's clearly permitted for this project's ultimate purpose. Per your explicit instruction, I have not contacted MoneyPuck and have not agreed to any terms on your behalf — this is a fact-finding summary only, and the commercial-use question is a decision for you.

## U. Daily-updated datasets

Confirmed via `Last-Modified` response headers (a real, server-reported fact — not inferred from the page's prose claim):

| File | Last-Modified |
|---|---|
| `all_teams.csv` (game-by-game team, all seasons) | 2026-06-15 10:33:02 GMT |
| `allPlayersLookup.csv` (player bios) | 2026-06-15 10:37:58 GMT |
| `teams.csv` (2024-25 season-level) | 2025-04-19 10:52:11 GMT |
| `MoneyPuckDataDictionaryForPlayers.csv` | 2025-05-04 03:24:18 GMT |
| `MoneyPuck_Shot_Data_Dictionary.csv` | 2019-09-04 02:25:18 GMT |

**Important honest caveat**: today (2026-08-27) falls in the NHL off-season — the 2025-26 season ended mid-June 2026 and the 2026-27 season doesn't start until October. Every file's most recent `Last-Modified` timestamp lands right around that mid-June end-of-season point, which is exactly what you'd expect whether or not the site truly updates nightly during an active season — **I have not observed an actual overnight file change myself** (there's nothing to observe right now), so "updated nightly" in this report is the site's own stated claim (`data.htm`: "Data for the 2025-2026 season is also available and updated nightly on this page"), not something I independently verified via a before/after diff. That verification can only genuinely happen once the 2026-27 season is underway.

## V. Recommended daily files

Ranked by what should actually be checked each morning during an active season, weighing usefulness against processing burden (W below):

1. **`all_teams.csv`** (team game-by-game, all seasons combined) — highest priority. Single file, ~126MB decoded but a plain CSV over HTTP with `Accept-Ranges: bytes` support (worth investigating incremental/range-based diffing later), directly answers the team-strength questions this project's model actually needs, and its `game_id`/`gameDate` let it join straight onto the real NHL corpus with no crosswalk.
2. **Goalie game-by-game (current season ZIP)** — small (~2.8MB decoded per season), directly supports the goalie-quality work this project has already flagged as high-value-but-deferred (once pregame starter identity is solved separately).
3. **Skater game-by-game (current season ZIP)** — much larger (~170MB decoded per season) but the highest-value dataset for eventually upgrading the player-quality model past the current PPG-EWMA heuristic.
4. **Line/pairing game-by-game (current season ZIP)** — moderate size (~45MB decoded per season); lower priority than skaters since this project has no line-chemistry feature planned yet.
5. **Shot-level data (current season ZIP)** — lowest priority for a *daily* feed specifically: by far the largest payload (~69MB decoded per season and growing daily throughout the season) for the least immediately-actionable data given this project doesn't have an xG-from-shots pipeline built, and Part Q's model-versioning uncertainty makes it a research/backfill target rather than a daily-check target.

**Do not check daily**: the season-level (not game-by-game) CSVs — game-by-game already contains everything they'd tell you, one level more granular; the multi-season combined archives — those are backfill files, not incremental feeds; player bios — biographical data changes rarely enough that a weekly or monthly check is more than sufficient.

## W. File/processing burden

| File | Approx. decoded size/season | Rows/season (2024-25 actuals where sampled) |
|---|---|---|
| Team game-by-game | (shared across all seasons in one 126MB file) | ~12,900/season (232,220 total ÷ 18 seasons) |
| Goalie game-by-game | 2.8 MB | 13,820 |
| Skater game-by-game | 170 MB | 236,120 |
| Line/pairing game-by-game | 45 MB | 83,228 (this is itself a partial count — see G's caveat about only sampling the first ~5,000 rows for situation values, though the full 83,228 row count was read directly from the decompressed file) |
| Shots | 69 MB | ~122,000 (per the site's own published count for 2024-25; I read the first ~52,000 rows and the exact header, then stopped deliberately rather than fully materializing all ~69MB, consistent with "do not bulk-download") |

None of these are trivially small. A genuine daily job touching all five files, every day of an ~8-month season, is a meaningful and recurring bandwidth/compute cost (skaters + shots alone are ~240MB/day if re-downloaded whole each time) — which is exactly why Z below treats "download and checksum before deciding whether to do anything else" as the very first step, so an unchanged file costs only a single fetch, not a full re-parse.

## X. Revision detection strategy

Natural keys available for `NEW`/`UNCHANGED`/`REVISED` classification, per dataset:

- **Team/goalie/skater/line game-by-game**: composite key `(gameId, playerId_or_team_or_lineId, situation)`. A file-level checksum (SHA-256 of the raw downloaded bytes) is the cheap first gate — if the whole file's hash matches the last accepted one, classify the whole batch `UNCHANGED` and stop, exactly as your Part 14 workflow describes. Only if the file-level hash differs does it need row-level diffing against the last accepted normalized snapshot, keyed by the composite key above, to tell `NEW` rows (a `gameId` never seen before) from `REVISED` rows (an existing key with different metric values — this happens in practice, since MoneyPuck does correct data after the fact, per its own "No guarantees are made to the quality of the data" disclaimer).
- **Shots**: key is `shotID` (already globally unique per the dictionary) — same two-tier strategy.
- **Season-level files**: key is `(team_or_playerId, season, situation)` — these get **fully republished** each time (they're season-to-date aggregates, not append-only), so a changed file should be treated as "the whole season's numbers may have shifted," not diffed row-by-row for individual field deltas unless that granularity is specifically wanted later.

## Y. Raw snapshot architecture (proposed, not built)

Exactly the structure you proposed, adopted as-is since it matches this project's existing `tmp_live_contract/`-style discipline of keeping raw, untouched provider payloads separate from normalized derivations:

```
data/raw/moneypuck/
    <dataset>/            # team_gamebygame, goalie_gamebygame, skater_gamebygame,
                           # line_gamebygame, shots, team_season, player_bios
        <season>/          # e.g. 2024, or "all" for combined files like all_teams.csv
            <download_timestamp>_<filename>
```

Each snapshot file is accompanied by a small sidecar record (JSON, one per raw file) capturing: `source_url`, `dataset`, `season`, `downloaded_at_utc`, `sha256`, `byte_size`, `validation_status` (`PENDING` / `VALID` / `REJECTED`, plus a reason on rejection). Never overwritten — a re-download that matches the last accepted checksum is recorded as a new sidecar entry with `validation_status: NO_CHANGE` pointing at the *existing* raw file rather than re-saving identical bytes, which keeps the append-only discipline without wasting storage on byte-identical duplicates.

## Z. Future daily sync architecture (proposed, not built)

Your 11-step workflow, concretized against the datasets above:

1. **Check monitored files** — the 5 recommended-daily datasets from V, one HTTP HEAD (or a lightweight ranged GET, given `Accept-Ranges: bytes` is confirmed supported on at least the `all_teams.csv` host) per dataset.
2. **Download current version** — full GET, same browser-fetch mechanism used throughout this review (this sandbox's own network still can't reach either host directly).
3. **Calculate checksum** — SHA-256 over the raw downloaded bytes, exactly as done for the dictionaries and season-level file in C above (correcting the hex-formatting bug noted there first).
4. **Compare to last accepted checksum** — read from the sidecar record for that dataset's most recent `VALID` snapshot.
5. **If unchanged** → record `NO_CHANGE`, stop for that dataset, no further processing.
6. **If changed** → archive the raw file immutably under Y's directory structure first, before anything else touches it.
7. **Validate schema** — column count and column-name set must match the last accepted schema exactly (or a explicitly-reviewed schema-version bump); fail loudly per Part 16 if not.
8. **Normalize records** — parse into the composite-key row shape from X.
9. **Classify** `NEW` / `UNCHANGED` / `REVISED` per row, against the last accepted normalized snapshot for that dataset.
10. **Cross-check basic game identity/results against the NHL API/real NHL corpus** — for any row whose `gameId` isn't already a known-good game in the real NHL results corpus, or whose score disagrees with it, flag rather than silently trust (this is the automatable version of the manual cross-check done in J above).
11. **Promote only if validation passes** — a schema failure, a cross-check disagreement above some threshold, or a corrupt/undecodable file all block promotion; the previously-accepted normalized state remains authoritative until a human resolves it.

## AA. NHL API/MoneyPuck architecture recommendation

**The candidate split you proposed is sound and directly supported by this review's evidence**: the real NHL API corpus should remain the sole source of canonical game identity, schedule, and final results (it's simpler, official, and already accepted/frozen for that purpose), while MoneyPuck supplies richer performance analytics (xG, shot quality, on-ice impact, special-teams detail) that key cleanly onto the NHL corpus via the shared `game_id`/`playerId` space confirmed in H and J. This isn't a demotion of MoneyPuck's own results fields (home/away score, SOG) — those matched perfectly in every sample — it's a matter of not needing two sources of truth for the same fact once one is already accepted and frozen. Where the two genuinely disagree in the future (Part 17's instruction), the NHL API corpus wins on identity/result facts, full stop, and the disagreement gets flagged rather than silently resolved in MoneyPuck's favor.

## AB. Minimum MoneyPuck datasets to ingest first

If/when this project moves from review to actual ingestion (a separate future decision, not part of this turn): **team game-by-game data** (`all_teams.csv`) alone, scoped to the same 2022-23 → 2025-26 window as the real NHL corpus. It's the smallest genuinely useful dataset (relative to skaters/shots), it directly supports the team-strength/xG modeling questions this project's roadmap has already flagged as next-tier, and its row grain (team × game × situation) maps onto exactly the unit this project's Elo/team-strength layer already operates on.

## AC. Datasets to defer

- **Shot-level data** — until the Part Q model-version question is either resolved or explicitly accepted as an open risk, and until there's an actual xG-from-shots feature on the roadmap to justify a ~69MB/season ingestion.
- **Skater/line game-by-game** — genuinely valuable (per O/P) but a much bigger ingestion (170MB + 45MB per season) that should wait until a specific player-model or line-chemistry slice is actually approved, not ingested speculatively.
- **Multi-season combined archives, player-career search tool** — not needed; the per-season files plus this project's own accumulation over time cover the same ground with less one-time ingestion risk.

## AD. Revised top-5 model roadmap

Scored 0-10 on PREDICTIVE VALUE / DATA QUALITY / POINT-IN-TIME RESEARCH FEASIBILITY / IMPLEMENTATION EFFICIENCY / (10 = LOWEST) OVERFITTING RISK:

| # | Candidate | Predictive Value | Data Quality | PIT Feasibility | Impl. Efficiency | Overfitting Risk (10=lowest risk) | Overall |
|---|---|---|---|---|---|---|---|
| 1 | Team xG/xGA (5v5, all situations) | 8 | 9 | 8 | 7 | 7 | **7.8** |
| 2 | Result-quality/MOV Elo (already designed last turn) | 6 | 9 | 9 | 9 | 8 | **8.2** |
| 3 | 5v5 xG% as a standalone team-strength signal | 7 | 9 | 8 | 7 | 7 | 7.6 |
| 4 | Score-adjusted xG | 6 | 8 | 8 | 7 | 8 | 7.4 |
| 5 | Special teams (PP/PK xG-based) | 6 | 7 | 7 | 5 | 6 | 6.2 |
| 6 | Recent-form xG (rolling) | 6 | 7 | 7 | 5 | 5 | 6.0 |
| 7 | Goalie GSAx/shot-quality-adjusted strength | 7 | 8 | 6 | 6 | 6 | 6.6 |
| 8 | Goalie workload | 7 | 8 | 2 (blocked — see below) | 6 | 6 | — not independently rankable while blocked |
| 9 | Player impact (beyond PPG EWMA) | 6 | 8 | 6 | 4 | 4 | 5.6 |
| 10 | Historical line chemistry | 4 | 7 | 5 | 4 | 4 | 4.8 |
| 11 | Schedule/rest/travel (already partially implemented) | 5 | 9 | 9 | 8 | 8 | 7.8 |
| 12 | Hybrid Elo + xG team rating | 8 | 8 | 7 | 5 | 5 | 6.6 |

**Top 5 by overall score**: Result-quality/MOV Elo (8.2), Team xG/xGA and Schedule/rest/travel (tied, 7.8), 5v5 xG% (7.6), Score-adjusted xG (7.4).

Notes on the scoring: goalie workload keeps its DATA QUALITY and PREDICTIVE VALUE scores high because MoneyPuck genuinely does supply strong goalie performance data (N above) — but its PIT feasibility score stays low and it's excluded from ranking for the same unchanged reason as last turn: MoneyPuck's goalie appearance data is exactly as postgame-derived as the NHL boxscore starter identity was, so it does not unblock the deferred slice. Team xG/xGA and 5v5 xG% score lower than the MOV Elo candidate on IMPLEMENTATION EFFICIENCY and OVERFITTING RISK specifically because they'd be a genuinely new model input requiring a new data-ingestion pipeline (MoneyPuck data has never been ingested into this project at all), a licensing decision (T above), and a real walk-forward validation exactly like the one already scoped for the Elo candidates — whereas the MOV Elo work is a small, already-fully-designed, already-real-data-validated change to an existing, already-ingested signal.

## AE. Recommended single next model implementation slice

**Do not switch away from RESULT-QUALITY/MOV ELO.** Use the evidence, as instructed, rather than assuming xG wins:

- The Elo candidates (A/B/C/D from last turn's report) are **fully designed, already point-in-time-safe by construction** (Step 3 of that report), and now have a **real, validated 5,248-game/4-season corpus ready to evaluate them against** — nothing new needs to be built or ingested to run that comparison; it's purely a matter of executing the walk-forward comparison that was already scoped and blocked only on data, and that block is now resolved.
- Team xG/xGA is a genuinely strong candidate for *later* — MoneyPuck's data quality, coverage, and game-id compatibility are all excellent (this review's main finding) — but adopting it as the *next* slice would mean building a brand-new MoneyPuck ingestion pipeline, resolving the licensing question in T, and running an equally rigorous real-data validation from scratch, none of which exists yet. That's a second, separate, larger project — not a smaller one than finishing the Elo work already in flight.
- Overfitting-risk and implementation-efficiency both favor finishing what's already staged before starting something bigger and newer, which is also consistent with this project's own established practice of narrow, one-slice-at-a-time development.

**Recommended next slice: execute the already-designed Result-Quality/MOV Elo walk-forward comparison (Steps 4-6 of the prior report) using the newly-built real NHL results corpus.** MoneyPuck ingestion becomes the natural *following* slice once that's delivered and reviewed.

## AF. Exact files/modules for the next slice (not created yet)

Per your explicit "do not create them yet" instruction, this is a preview of what the *next* (not this) turn would touch, based on the already-existing Step 2 candidate design:

- `models/elo_model.py` — extend `EloModel.update()`'s signature to optionally accept `final_period_type`, `home_score`, `away_score`, implementing Candidates B/C/D's multipliers alongside the unchanged Candidate A baseline path.
- `models/combined_model.py` — the single call site in `learn()` that currently calls `self.elo.update(home, away, home_won)` would pass the additional fields already present in the same `result` dict (per last turn's Step 3 analysis — no new data source needed).
- `config.py` — new constants for the candidate parameter grid (`OTSO_WEIGHT` candidates, `MOV_CAP` candidates), following the existing pattern of documented, comment-justified constants like `POINTS_PER_GAME_TO_ELO`.
- A new, separate **research/backtest script** (not a production module) that loads `research/real_nhl_results/normalized_regular_season_games.jsonl`, applies the STRICT PRIOR-GAME-DATE eligibility policy, and runs all 9 candidate parameterizations walk-forward, reporting Brier/log-loss/calibration per candidate per season — analogous to the existing `backtest.py` but against the real corpus instead of the synthetic demo dataset, and kept separate from production code exactly as the corpus itself was kept separate from `nhl.db`.
- New tests under `tests/` for whichever candidate (if any) is ultimately adopted, mirroring the existing `tests/test_elo_update_rule.py` pattern.

No code, config, or test file listed above was touched this turn.

---

## Final answers

```
CAN MONEYPUCK PROVIDE A REAL MULTI-SEASON NHL RESEARCH DATASET?
YES

CAN MONEYPUCK SUPPORT XG / SHOT-QUALITY TEAM MODELING?
YES

CAN MONEYPUCK SUPPORT SPECIAL-TEAMS MODELING?
YES (PP/PK goals, xG, and shot generation/suppression all AVAILABLE; PP/PK
"opportunity counts" specifically are PARTIAL -- not a ready-made column)

CAN MONEYPUCK SUPPORT BETTER GOALIE QUALITY MODELING?
YES (save%, GSAx-equivalent, and danger-zone save performance are all
AVAILABLE or directly derivable)

CAN MONEYPUCK SUPPORT PLAYER-LEVEL MODELING?
YES, as raw material -- not as a finished RAPM/GAR/xGAR model

CAN MONEYPUCK SUPPORT LINE / PAIRING RESEARCH?
YES, for historical completed-game chemistry only -- confirmed same
postgame-only caveat as goalie/lineup data

DOES MONEYPUCK SOLVE HISTORICAL PREGAME GOALIE CONFIRMATION?
NO

DOES MONEYPUCK SOLVE HISTORICAL INJURY/SCRATCH KNOWLEDGE?
NO -- not reviewed as a distinct dataset this turn (MoneyPuck publishes no
injury feed on data.htm at all), and even lineup/appearance data that does
exist is postgame-derived per the same caveat above

DOES MONEYPUCK SOLVE HISTORICAL DRAFTKINGS ODDS?
NO -- entirely unrelated data domain, not addressed by anything reviewed
this turn; the paid-odds problem remains exactly as deferred as before

SHOULD MONEYPUCK REPLACE A LARGE NHL API HISTORICAL ANALYTICS BACKFILL?
YES -- MoneyPuck's game-by-game team/skater/goalie/line files already ARE
the kind of historical analytics backfill a from-scratch NHL API
shot-by-shot reconstruction would otherwise require building; there is no
reason to duplicate that engineering effort

SHOULD THE REAL NHL CORPUS REMAIN OUR OFFICIAL RESULTS VALIDATION BASE?
YES

CAN MONEYPUCK BECOME A DAILY PERFORMANCE DATA FEED?
YES, with the caveat in U that "nightly updates" is currently the site's
own claim, not yet independently observed by this review (off-season)

CAN WE CREATE GENUINE MONEYPUCK PIT HISTORY GOING FORWARD?
YES -- via the ARCHIVAL_RESEARCH/LIVE_OBSERVED split in R/S, using our own
downloaded_at_utc as the honest knowledge-time for anything collected
from here forward, exactly as this project already does for its own PIT
architecture

SHOULD WE EVENTUALLY AUTOMATE DAILY MONEYPUCK DOWNLOADS?
YES, once: (a) the licensing question in T is resolved for this project's
actual use case, and (b) at least one MoneyPuck dataset has been through a
real ingestion + validation slice (not just this review)

CURRENT VERIFIED TEST BASELINE?
322 / 322

WHAT IS THE SINGLE BEST NEXT MODEL IMPLEMENTATION SLICE?
RESULT-QUALITY / MOV ELO -- execute the already-designed Steps 4-6 walk-
forward comparison against the real NHL results corpus. Not xG/shot-
quality team strength (strong candidate, but a new, larger, unstarted
ingestion project) and not another narrow feature.
```

Then STOP, per your instruction. No model code was changed. No daily sync was built. The real NHL corpus was not altered. Elo was not changed. No xG features were added. Paid-odds work was not restarted. No UI was built.
