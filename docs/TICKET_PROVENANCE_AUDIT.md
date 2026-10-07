# Provenance audit of the three recorded tickets (2026-10-07)

Recorded 18:53:33 UTC by the scheduled trader running `8b9c469e0d+dirty` (the edit checkout, before the pinned release
checkout existed). The tickets, their frozen legs and that provenance are unchanged and are not rewritten.

## Quote freshness (audit finding: retrieval time was used as the price time)

The frozen legs of these tickets carry only the retrieval time (`captured_at_utc`). Their provider quote times were
reconstructed from the archived Odds API payloads those legs name (`operational/odds_archive/live`, matched by event id
and retrieval timestamp) and compared with the recording time:

| Leg | Retrieved (UTC) | Provider market `last_update` (UTC) | Quote age when recorded |
|---|---|---|---|
| Ryan Leonard 2+ SOG (tickets `TDD065E3FE00331`, `T54F8E52D8B2D50`) | 18:53:34 | 18:53:06 | 0.5 min |
| Evgeni Malkin 2+ SOG (`TBDAC49EFEAAEDA`) | 18:53:34 | 18:53:06 | 0.5 min |
| Nazem Kadri 2+ SOG (`TDD065E3FE00331`, `TBDAC49EFEAAEDA`) | 18:32:39 | 18:32:27 | 21.1 min |
| Gabriel Landeskog 2+ SOG (`T54F8E52D8B2D50`) | 18:32:39 | 18:32:27 | 21.1 min |

The price-age limit that applied (game more than 2 h out) is 150 minutes. **No entry quote was stale; the defect did not
change the outcome for these tickets, so no audit flag is added to them.** The frozen legs simply predate the fields
`quote_updated_utc`, `retrieved_at_utc`, `quote_age_min_at_entry` and `freshness_status`; Today shows "quote time not
recorded" for them rather than implying one. Tickets recorded from now on freeze all four.

## Recording-time ordering

`created_at_utc` (18:53:33.19) is about 0.8 s earlier than the 18:53:34 retrieval stamp of two legs: the run's start time
was used as the recording time although the capture happened later in the same run. New tickets are stamped no earlier than
their newest price.
