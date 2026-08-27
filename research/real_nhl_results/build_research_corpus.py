"""
Builds the SEPARATE real-NHL-results RESEARCH corpus from raw browser-captured
weekly schedule payloads under research/real_nhl_results/raw/<season>/.

This is deliberately NOT the production ingestion path (ingest/nhl_api.py /
features/point_in_time.py). It does not touch nhl.db. It does not write to
game_schedule_events / game_result_events. It exists only to support the
Elo-candidate research comparison, under the explicit RESEARCH AVAILABILITY
POLICY: STRICT PRIOR-GAME-DATE (see research/real_nhl_results/README.md).

Raw files are read but never mutated.
"""
from __future__ import annotations

import glob
import json
import math
from collections import defaultdict

RAW_GLOB = "research/real_nhl_results/raw/*/*.json"
TARGET_GAME_TYPE = 2  # regular season only, per instruction

# --- Pass 1: load every raw weekly file, walk every game in it ---------

all_game_sightings = defaultdict(list)   # game_id -> list of normalized dicts (one per raw sighting)
playoff_game_ids_seen = set()
preseason_game_ids_seen = set()
other_gametype_seen = defaultdict(int)
non_final_states = defaultdict(int)
missing_score_games = []
missing_period_type_games = []
raw_files_read = 0

def classify_period(period_descriptor):
    if not period_descriptor:
        return None
    pt = period_descriptor.get("periodType")
    if pt in ("REG", "OT", "SO"):
        return pt
    return pt  # return whatever it is, even if unexpected, for visibility

for path in sorted(glob.glob(RAW_GLOB)):
    raw_files_read += 1
    season_dir = path.split("/")[-2]
    with open(path) as f:
        payload = json.load(f)
    for day in payload.get("gameWeek", []):
        game_date = day.get("date")
        for g in day.get("games", []):
            game_id = g.get("id")
            season = g.get("season")
            game_type = g.get("gameType")
            game_state = g.get("gameState")
            home = g.get("homeTeam", {})
            away = g.get("awayTeam", {})
            start_utc = g.get("startTimeUTC")

            if game_type == 3:
                playoff_game_ids_seen.add(game_id)
            elif game_type == 1:
                preseason_game_ids_seen.add(game_id)
            elif game_type != 2:
                other_gametype_seen[game_type] += 1

            if game_type != TARGET_GAME_TYPE:
                continue  # regular season only for the normalized corpus

            record = {
                "game_id": game_id,
                "season": season,
                "gameType": game_type,
                "game_date": game_date,
                "scheduled_start_utc": start_utc,
                "home_team": home.get("abbrev"),
                "away_team": away.get("abbrev"),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "game_state": game_state,
                "period_type": classify_period(g.get("periodDescriptor")),
                "_source_file": path,
                "_source_season_dir": season_dir,
            }
            all_game_sightings[game_id].append(record)

# --- Pass 2: dedup by game_id, checking for conflicts ------------------

FINAL_STATES = {"OFF", "FINAL"}

final_records = {}
conflicts = []
unfinished_status_records = []
cancelled_or_postponed = []

for game_id, sightings in all_game_sightings.items():
    # a game's schedule/date window shouldn't repeat across our non-overlapping
    # weekly windows, but check for disagreement defensively anyway.
    if len(sightings) > 1:
        first = sightings[0]
        for other in sightings[1:]:
            if (other["home_team"] != first["home_team"] or
                other["away_team"] != first["away_team"] or
                other["game_date"] != first["game_date"] or
                other["scheduled_start_utc"] != first["scheduled_start_utc"] or
                other["gameType"] != first["gameType"] or
                other["home_score"] != first["home_score"] or
                other["away_score"] != first["away_score"]):
                conflicts.append({"game_id": game_id, "sightings": sightings})
                break

    rec = sightings[0]
    state = rec["game_state"]
    if state not in FINAL_STATES:
        if state in ("PPD", "CNCL", "SUSP"):
            cancelled_or_postponed.append(rec)
        else:
            unfinished_status_records.append(rec)
        continue

    final_records[game_id] = rec

# --- Pass 3: data-quality validation of final_records -------------------

valid_records = []
invalid_records = []

for game_id, rec in final_records.items():
    problems = []
    if not rec["game_id"]:
        problems.append("missing game_id")
    if rec["gameType"] != TARGET_GAME_TYPE:
        problems.append("gameType != 2")
    if not rec["home_team"]:
        problems.append("empty home_team")
    if not rec["away_team"]:
        problems.append("empty away_team")
    if rec["home_team"] == rec["away_team"]:
        problems.append("home_team == away_team")
    if not rec["game_date"]:
        problems.append("invalid game_date")
    if not rec["scheduled_start_utc"]:
        problems.append("invalid scheduled_start_utc")
    if rec["home_score"] is None:
        problems.append("missing home_score")
    if rec["away_score"] is None:
        problems.append("missing away_score")
    if rec["period_type"] not in ("REG", "OT", "SO"):
        problems.append(f"invalid period_type: {rec['period_type']}")

    if problems:
        rec["_problems"] = problems
        invalid_records.append(rec)
    else:
        valid_records.append(rec)

# --- Pass 4: per-season summary stats -----------------------------------

by_season = defaultdict(list)
for rec in valid_records:
    by_season[rec["season"]].append(rec)

season_summary = {}
for season, recs in sorted(by_season.items()):
    teams = set()
    for r in recs:
        teams.add(r["home_team"]); teams.add(r["away_team"])
    period_counts = defaultdict(int)
    margins = defaultdict(int)
    for r in recs:
        period_counts[r["period_type"]] += 1
        margins[abs(r["home_score"] - r["away_score"])] += 1
    dates = sorted(r["game_date"] for r in recs)
    season_summary[season] = {
        "num_games": len(recs),
        "num_teams": len(teams),
        "teams": sorted(teams),
        "period_type_counts": dict(period_counts),
        "goal_margin_distribution": dict(sorted(margins.items())),
        "earliest_game_date": dates[0] if dates else None,
        "latest_game_date": dates[-1] if dates else None,
    }

# --- Write normalized corpus (JSON Lines, one real game per line) -------

OUT_PATH = "research/real_nhl_results/normalized_regular_season_games.jsonl"
with open(OUT_PATH, "w") as f:
    for rec in sorted(valid_records, key=lambda r: (r["game_date"], r["game_id"])):
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        f.write(json.dumps(clean, sort_keys=True) + "\n")

# --- Write the quality/audit report as JSON for the delivery report -----

report = {
    "raw_files_read": raw_files_read,
    "unique_game_ids_seen_any_type": len(all_game_sightings),
    "playoff_game_ids_seen": len(playoff_game_ids_seen),
    "preseason_game_ids_seen": len(preseason_game_ids_seen),
    "other_gametype_counts": dict(other_gametype_seen),
    "duplicate_game_id_sightings": sum(1 for s in all_game_sightings.values() if len(s) > 1),
    "conflicting_duplicates": len(conflicts),
    "conflict_details": conflicts[:10],
    "non_final_regular_season_games": len(unfinished_status_records),
    "non_final_sample": unfinished_status_records[:10],
    "cancelled_or_postponed": len(cancelled_or_postponed),
    "cancelled_or_postponed_sample": cancelled_or_postponed[:10],
    "valid_regular_season_games": len(valid_records),
    "invalid_records": len(invalid_records),
    "invalid_sample": invalid_records[:10],
    "season_summary": season_summary,
}

with open("research/real_nhl_results/corpus_quality_report.json", "w") as f:
    json.dump(report, f, indent=2, sort_keys=True)

print(json.dumps({k: v for k, v in report.items() if k not in (
    "conflict_details", "non_final_sample", "cancelled_or_postponed_sample", "invalid_sample", "season_summary"
)}, indent=2))
print()
print("=== per-season summary ===")
for season, s in season_summary.items():
    print(season, "-> games:", s["num_games"], "teams:", s["num_teams"],
          "periods:", s["period_type_counts"], "dates:", s["earliest_game_date"], "..", s["latest_game_date"])
