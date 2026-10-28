# Starting-goalie sources (GoaliePost primary, GameDayTweets corroboration)

Module `operational/goalie_sources.py`; audit of 2026-10-28; owner brief: free, permitted, real CONFIRMED vs PROBABLE vs UNKNOWN before Eggy picks.

## Source audit (every source checked live on 2026-10-28; plain HTTP GETs, descriptive User-Agent, a handful of requests each)

| Source | Plain GET returns the data? | Statuses | Source time | robots.txt | Terms | Verdict |
|---|---|---|---|---|---|---|
| **GoaliePost** `goaliepost.com/` | **Yes.** The page's server-rendered payload (`self.__next_f`) carries each game: NHL game id, NHL player id, label, cited reporter + link, text, the entry's own epoch time. No login, no headless browser, no private endpoint. (An earlier note here said the data was not in the HTML; that was wrong, it was in script payloads I had stripped.) | CONFIRMED / LIKELY / PROBABLE | per entry (epoch) + per game | `Allow: /` (only `/private/` disallowed) | `/tos` is a privacy statement ("as is", "play nice"); no clause against automated reading and no grant either | **PRIMARY** |
| **GameDayTweets** `gamedaytweets.com/goalies` | Yes, plain HTML, one `blockquote` per reporter post per matchup (column = side, logo = teams) | none; posts only (plus an "Our Guess" that is never used) | a date only (no time of day) | only `/tweets/mark_deleted` disallowed | no terms page found (every guessed path 404; no footer link) | **CORROBORATION** (direct start language only) |
| **PuckLuck** `puckluck.com/starting-goalies` | No. The page shows only the legend (CONFIRMED / LIKELY / PROJECTED, "updated every 15 minutes"), even in a real browser; the list does not load. | (legend only) | none | no robots.txt (404) | the "Terms of Service" link 404s | **Not usable** (no data; lists PuckPedia as a partner) |
| **Daily Fantasy Fuel** | Yes (HTML) but it is a DFS salary/depth page: no CONFIRMED/EXPECTED/PROJECTED labels, no source | none | none | `Disallow: /lineup/*` | no scraping clause found | **Not a confirmation source** |
| **RotoWire** | n/a | n/a | n/a | n/a | Article VI prohibits automated means (scraping, bots, scripts, unauthorised APIs) without written consent | **Prohibited**; not used |
| **PuckPedia** | not fetched | CONFIRMED / PROJECTED (reported by the owner) | n/a | disallows named AI crawlers (incl. ClaudeBot) | terms page not reachable by the audit; owner reports a scraping prohibition | **Not used** (restriction not independently verified) |
| ESPN | (inspected once earlier) | Confirmed / Expected | n/a | n/a | Disney terms prohibit robots/scripts/scraping | **Not enabled** |
| Daily Faceoff | n/a | n/a | n/a | n/a | Nation Network terms prohibit scraping | **Not enabled** |
| Frozen Tools (Dobber) | news ticker and per-game previews carry no status labels; free text only | none | none | no robots.txt | no terms page, privacy policy only | **Not used** |

**Compliance position, stated plainly.** For GoaliePost and GameDayTweets, robots.txt permits ordinary access and no published term prohibits automated reading; neither site grants permission, and the project rule is not to infer permission from public pages. The module therefore reads politely (see below), is switchable with `NHL_ENGINE_GOALIE_SOURCES=OFF` (stops every request), and a courtesy permission request to Dobber Sports (GoaliePost's owner; contact form at dobberhockey.com/contact) is the owner's decision to send. Nothing is paid for; no login is used.

## Status semantics (never blurred)
- **CONFIRMED**: GoaliePost label CONFIRMED with a named source and link, an entry time not older than 30 hours and not in the future; or a GameDayTweets post with DIRECT start language naming the same goalie.
- **PROBABLE**: GoaliePost LIKELY or PROBABLE (raw label kept), or a CONFIRMED label that lacks a source / a time / is older than 30 hours.
- **UNKNOWN**: no entry, an unrecognised label, a future timestamp, or an entry older than 48 hours (STALE).
- **CONFLICT**: two sources name different goalies and the newer one cannot be shown to supersede (see below). Visible as SOURCE CONFLICT; uncertainty is retained.
- An inferred, projected, hedged ("will see how", "projecting", "expected", "game-time") or "Our Guess" starter is **never** CONFIRMED. The module has no inference model and does not call one.

Freshness of an entry: CURRENT (≤24 h), RECENT (≤48 h), STALE (older, and then UNKNOWN). The whole feed is fresh only if the last good fetch is ≤90 minutes old; otherwise every game is UNKNOWN (`FEED_STALE`).

## Conflict rules
- GoaliePost CONFIRMED vs a different goalie in a direct GameDayTweets post: CONFLICT.
- GoaliePost PROBABLE vs a different goalie in a direct post dated a LATER calendar day than the listing: the explicit post supersedes it (the replaced listing is stored). Same calendar day: CONFLICT, because GameDayTweets has no time of day.
- Two CONFIRMED goalies in GoaliePost for one team, or contradictory direct posts: CONFLICT.

## Time stamps
`source_at_utc` is the source's own clock (GoaliePost's entry epoch); `retrieved_at_utc` is the engine's fetch time. They are stored separately, and GameDayTweets' date is never used as a time.

## Storage and refresh
- `goalie_feed_state.json` (latest resolved picture per game) and `goalie_feed_history.jsonl` (append-only: who was expected, status, source, source time, retrieval time, previous goalie/status, conflict text). A row is written only when the goalie, status or conflict changes.
- Refresh: the 10-minute trader calls `goalie_sources.run_cycle`; a fetch happens at most every 20 minutes (10 when a game is within 3 hours: morning, midday, pregame, late pregame), and Eggy forces a refresh immediately before ranking when the last fetch is older than 10 minutes. A failure keeps the last good data (which then goes stale to UNKNOWN) and never stops the cycle. A test run never reaches either site.
- The module never writes `goalie_status_events`, so it cannot open the saves starter gate or any other existing consumer.

## How Eggy uses it (frozen policy untouched)
- One PERSONNEL / AVAILABILITY reason (`MATCHUP_PERSONNEL` family), WEAK strength, so it never carries a pick and never counts toward the evidence balance or the ranking score: both confirmed is a CASE FOR note ("goalie uncertainty removed"); probable / unknown / conflict is one CASE AGAINST note, however many facts lie behind it.
- Confidence: an UNKNOWN goalie or a SOURCE CONFLICT costs one category (HIGH to MEDIUM, MEDIUM to LOW); PROBABLE does not.
- The goalie picture (both goalies, statuses, freshness, source, source time, conflict, retrieval time, late-change flag) is stored in the decision, on every observation, and drives prospective signals `PICK_GOALIES_*` and `PICK_GOALIE_LATE_CHANGE`. No signal changes a decision before the 100-game threshold.
- Goalie status is context and uncertainty only, never a win-probability or price adjustment (the project's finding that goalie lift was insufficient is preserved). If a future team model uses the same starter, the model downgrade and this reason must be counted as one family.
- Injuries and lineups remain NOT AVAILABLE; no free permitted source has been found for them.

## Saves workflow
Confirmed starters do NOT unblock saves. The saves starter gate reads `source_schema` external observations (a separate mechanism this feed does not write), and saves are also gated by model-validated thresholds and contract certification (`NOT_MODEL_VALIDATED`, `CONTRACT_NOT_VERIFIED`). Starter confirmation was not the only blocker, and no betting policy was changed.
