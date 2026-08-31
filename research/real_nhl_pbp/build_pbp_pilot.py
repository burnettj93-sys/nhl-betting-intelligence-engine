"""
Part 19: builds the bounded, deliberately-diverse real play-by-play pilot.

Selects 30 real game_ids from research/real_nhl_results/normalized_regular_
season_games.jsonl (the project's existing authoritative real-game-id
source -- see MEMORY of prior slices; no game_id here is invented) for the
20252026 season, stratified to include regulation, overtime, and shootout
games, plus high-scoring and high-margin regulation games chosen to raise
the odds of capturing power-play goals, empty-net goals, and multi-goalie
appearances -- exactly the diverse case coverage Part 19 requires.

Fetches each game's real play-by-play from the live NHL API and archives
the raw, unmodified response via raw_archive.archive_raw_pbp(). Paced at
0.3s between requests (matches the sleep(0.2) convention already used in
ingest/nhl_api.py) -- 30 games is a deliberately small contract-proving
pilot, not a bulk ingestion.

Deterministic selection: fixed random seed (20252026) so re-running this
script selects the identical 30 games every time.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import time

import requests

from research.real_nhl_pbp import client, raw_archive

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "..", "real_nhl_results",
                             "normalized_regular_season_games.jsonl")
PILOT_MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "pilot_manifest.json")

PILOT_SEASON = 20252026


def select_pilot_games(season: int = PILOT_SEASON) -> list[dict]:
    games = []
    with open(RESULTS_PATH) as f:
        for line in f:
            row = json.loads(line)
            if row["season"] == season:
                games.append(row)

    reg = [g for g in games if g["period_type"] == "REG"]
    ot = [g for g in games if g["period_type"] == "OT"]
    so = [g for g in games if g["period_type"] == "SO"]

    for g in reg:
        g["total_goals"] = g["home_score"] + g["away_score"]
        g["margin"] = abs(g["home_score"] - g["away_score"])

    high_scoring = sorted(reg, key=lambda g: -g["total_goals"])[:6]
    blowouts = [g for g in sorted(reg, key=lambda g: -g["margin"]) if g["margin"] >= 4][:6]
    excluded_ids = {g["game_id"] for g in high_scoring} | {g["game_id"] for g in blowouts}

    rnd = random.Random(season)
    baseline = rnd.sample([g for g in reg if g["game_id"] not in excluded_ids], 6)
    ot_sample = rnd.sample(ot, 7)
    so_sample = rnd.sample(so, 5)

    pilot = high_scoring + blowouts + baseline + ot_sample + so_sample
    by_id = {g["game_id"]: g for g in pilot}
    return sorted(by_id.values(), key=lambda g: g["game_id"])


def run_pilot_ingestion(session=None) -> dict:
    session = session or requests.Session()
    games = select_pilot_games()

    fetched = []
    failures = []
    for game in games:
        gid = game["game_id"]
        url = client.play_by_play_url(gid)
        try:
            data = client.fetch_play_by_play(session, gid)
        except Exception as exc:  # noqa: BLE001 -- Part 37: record, don't skip silently
            failures.append({"game_id": gid, "failure_stage": "FETCH", "error_reason": str(exc)})
            continue

        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp_path = os.path.join(raw_archive.RAW_ROOT, f"_tmp_{gid}.json")
        os.makedirs(raw_archive.RAW_ROOT, exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        try:
            season = str(data.get("season"))
            dest, prov = raw_archive.archive_raw_pbp(
                tmp_path, game_id=gid, season=season, source_url=url,
                retrieved_at_utc=now,
            )
            fetched.append({
                "game_id": gid, "season": season, "archived_path": dest,
                "num_plays": len(data.get("plays", [])),
                "expected_period_type": game["period_type"],
                "expected_home_score": game["home_score"],
                "expected_away_score": game["away_score"],
            })
        finally:
            os.remove(tmp_path)
        time.sleep(0.3)

    manifest = {
        "pilot_season": PILOT_SEASON,
        "games_selected": len(games),
        "games_fetched": len(fetched),
        "games_failed": len(failures),
        "fetched": fetched,
        "failures": failures,
    }
    with open(PILOT_MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


if __name__ == "__main__":
    result = run_pilot_ingestion()
    print(json.dumps({k: v for k, v in result.items() if k not in ("fetched", "failures")}, indent=2))
