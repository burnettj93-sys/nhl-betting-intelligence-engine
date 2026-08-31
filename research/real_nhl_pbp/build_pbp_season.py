"""
Parts 22-27: one-complete-regular-season ingestion, gated strictly on the
pilot passing (run_pilot_validation.run_all()["pilot_passed"] must be True
before this is ever invoked for real -- enforced in __main__ below, not
just documented).

Ingests every real regular-season game_id for one season (20252026 by
default, per Part 22's stated preference) from the project's existing
authoritative game list (research/real_nhl_results/normalized_regular_
season_games.jsonl), fetches each game's real play-by-play, and archives
it via raw_archive.archive_raw_pbp() -- the exact same archival path the
pilot already used and already proved idempotent. Games already archived
(e.g. the 30 pilot games, which are themselves part of this season) are
NOT re-fetched -- Part 24 idempotency, and Part 23's "do not hammer the
API" instruction both point the same direction.

One retry (after a short backoff) on a transient fetch failure; a game
that still fails after the retry is recorded, never silently skipped
(Part 37).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

from research.real_nhl_pbp import client, raw_archive

RESULTS_JSONL = os.path.join(os.path.dirname(__file__), "..", "real_nhl_results",
                              "normalized_regular_season_games.jsonl")
SEASON_MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "season_ingestion_manifest.json")

DEFAULT_SEASON = 20252026


def season_game_ids(season: int = DEFAULT_SEASON) -> list[int]:
    ids = []
    with open(RESULTS_JSONL) as f:
        for line in f:
            row = json.loads(line)
            if row["season"] == season:
                ids.append(row["game_id"])
    return sorted(ids)


def ingest_season(season: int = DEFAULT_SEASON, session=None, sleep_seconds: float = 0.3) -> dict:
    session = session or requests.Session()
    all_ids = season_game_ids(season)
    already_archived = set(raw_archive.archived_game_ids(str(season)))
    to_fetch = [gid for gid in all_ids if gid not in already_archived]

    retrieved = list(already_archived)
    failures = []
    retries = 0

    for gid in to_fetch:
        url = client.play_by_play_url(gid)
        data = None
        for attempt in (1, 2):
            try:
                data = client.fetch_play_by_play(session, gid)
                break
            except Exception as exc:  # noqa: BLE001 -- Part 37
                if attempt == 1:
                    retries += 1
                    time.sleep(1.0)
                    continue
                failures.append({"game_id": gid, "failure_stage": "FETCH", "error_reason": str(exc)})
        if data is None:
            continue

        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp_path = os.path.join(raw_archive.RAW_ROOT, f"_tmp_{gid}.json")
        os.makedirs(raw_archive.RAW_ROOT, exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        try:
            raw_archive.archive_raw_pbp(
                tmp_path, game_id=gid, season=str(data.get("season")),
                source_url=url, retrieved_at_utc=now,
            )
            retrieved.append(gid)
        except Exception as exc:  # noqa: BLE001
            failures.append({"game_id": gid, "failure_stage": "ARCHIVE", "error_reason": str(exc)})
        finally:
            os.remove(tmp_path)
        time.sleep(sleep_seconds)

    missing = sorted(set(all_ids) - set(retrieved))
    manifest = {
        "season": season,
        "games_in_authoritative_schedule": len(all_ids),
        "games_already_archived_before_run": len(already_archived),
        "games_requested_this_run": len(to_fetch),
        "games_retrieved_total": len(set(retrieved)),
        "games_missing": missing,
        "retries": retries,
        "failures": failures,
    }
    with open(SEASON_MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    from research.real_nhl_pbp.run_pilot_validation import run_all as run_pilot

    pilot_result = run_pilot()
    if not pilot_result["pilot_passed"]:
        raise SystemExit(
            "REFUSING to ingest a full season: pilot did not pass the acceptance gate "
            "(Part 22 explicit STOP condition). See pilot_validation_results.json."
        )
    print(f"Pilot gate open (pilot_passed=True, strict={pilot_result['pilot_passed_strict']}). "
          f"Proceeding to one-season ingestion.")
    result = ingest_season()
    print(json.dumps({k: v for k, v in result.items() if k != "failures"}, indent=2))
    if result["failures"]:
        print("FAILURES:", json.dumps(result["failures"], indent=2))
