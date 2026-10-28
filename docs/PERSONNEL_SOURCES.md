# Injury / personnel sources (audited 2026-10-28)

Rule applied: `docs/SOURCE_POLICY.md`. Nothing paid, nothing logged into, no ESPN / Daily Faceoff / RotoWire.

| Source | robots.txt | Written terms on automated reading | Data | Verdict |
|---|---|---|---|---|
| NHL.com (Status Report / news) | allows `/` | Terms of Service (nhl.com/info/terms-of-service): "Engage in unauthorized spidering, scraping, or harvesting of content or information, or use any other unauthorized automated means to compile information" | editorial articles, no structured injury feed; the public API has no injury data | **Rejected** (prohibited; no permission on file) |
| CBS Sports NHL injuries | allows `/nhl/injuries/` (GPTBot blocked) | Paramount Terms of Use (viacomcbs.legal/us/en/cbsi/terms-of-use): "Engage in unauthorized spidering, scraping, data mining or harvesting of Content, or use any other unauthorized automated means to gather data" | structured league-wide table, plain HTTP 200 | **Rejected** (prohibited) |
| FOX Sports NHL injuries | allows | foxsports.com/terms-of-use: "you may not copy, download, stream, scrape, capture ... or otherwise use any Content" | page loads, plain HTTP 200 | **Rejected** (prohibited) |
| ESPN, Daily Faceoff, RotoWire | - | restricted (Daily Faceoff / RotoWire documented earlier) | - | **Not automated** |
| General web search (late check) | n/a | n/a | a 2026-10-28 search for today's NHL injury and lineup news returned only 2025 / spring-2026 items | **Not viable as an automatic step** |

## What is implemented instead
`operational/personnel.py`: the engine reads no injury page. It accepts **attributable reports** (named player and team, one of OUT / IR / DAY_TO_DAY / QUESTIONABLE / GAME_TIME_DECISION / EXPECTED_TO_PLAY / EXPECTED_TO_RETURN / ACTIVE_CLEARED, a named source with link and the source's own timestamp, authority OFFICIAL / REPORTER / AGGREGATOR) recorded by a person with `python -m operational.personnel record ...`, plus **sweeps** (`sweep`: team checked against a source at a time). A person reading a public article is ordinary use; the engine does not automate it. Without reports a team is "injury information NOT AVAILABLE", never healthy.

Freshness (hours CURRENT / RECENT, else STALE and ignored): long-term (IR, or expected return of two weeks or more) 168 / 504; OUT 36 / 72; day-to-day, questionable, available 24 / 48; game-time decision 6 / 12; sweep 12 / 24. Conflicts: same source, newer wins; different sources at the same moment, more authoritative wins, equals are SOURCE CONFLICT; a newer but less authoritative contradiction is a conflict. A conflict is treated as uncertainty, never as out or fine.

Importance (engine data, last ten team games): forwards MATERIAL if top-6 in time on ice or top-3 in points per game, MODERATE top-9 / top-6 points; defence 4 / 6; else DEPTH; no data UNRATED. Power-play role is not measured (no PP ice time stored).

Eggy: at most one reason for (opponent missing important players) and one against (own absences, a material player in doubt, a conflict) per game, all in the PERSONNEL / AVAILABILITY family; MATERIAL = MODERATE, two or more = STRONG, MODERATE-impact = WEAK, doubt and conflict = WEAK. One confidence step is lost for a goalie unknown/conflict **or** a material player in doubt/conflict, never two. No probability, price, edge or ranking-formula change.
