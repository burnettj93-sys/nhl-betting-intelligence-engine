# Source policy: when may the engine read a public page automatically?

## The rule (as the project has applied it, with where each part comes from)

1. **Public is not permitted.** A page being public, robots.txt allowing it and a low request rate do not establish permission. Source: the owner's standing instruction ("do not infer permission merely because content is public"); `operational/dailyfaceoff.py` ("robots.txt permission and a low request rate do NOT establish permission under those terms"); `docs/STARTING_GOALIE_SOURCE_AUDIT.md`.
2. **A written prohibition ends the question.** If a site's terms prohibit scraping or automated means, the source is not automated, whatever robots.txt says (Daily Faceoff / Nation Network: OFF by default; RotoWire: licence required; NHL.com, CBS, FOX: see `docs/PERSONNEL_SOURCES.md`).
3. **Permission is affirmative.** Only a licence, API terms, an explicit written permission or terms that expressly allow the use make a source ALLOWED. The owner's opt-in switch is an acknowledgment, not a grant (`docs/STARTING_GOALIE_SOURCE_AUDIT.md`).
4. **A permission signal that is present or changed is a stop** (`GOALIE_INTELLIGENCE_FOUNDATION_REPORT.md`: Goalie Post / Frozen Tools recorded a `Content-Signal: ... use=reference` as REQUIRES PERMISSION; `moneypuck_daily.py` treats a redirect to a licence page the same way).

## Applying it: robots permits, no prohibition found, no login or control bypassed, polite and infrequent

That fact pattern satisfies none of rules 1-4 affirmatively and breaks none of rule 2: it is **C. AMBIGUOUS -> default OFF**, kept in code, enabled only by the owner.

GoaliePost and GameDayTweets are in that class. Today (2026-10-28) goaliepost.com's robots.txt shows no Content-Signal line (only `Allow: /`, `Disallow: /private/`), but the project's earlier audit recorded one for the same company; the change is unexplained, which is a further reason to treat the status as ambiguous rather than allowed. `NHL_ENGINE_GOALIE_SOURCES=ON` turns the feed on; with it unset the module makes no request at all and Eggy treats the feed as "not wired" (no goalie view, no confidence effect).
