"""
Player and goalie game history for the product models, from the MoneyPuck game-by-game files already on disk
(four completed seasons under research/player_sog/raw and research/goalie_intelligence/raw, plus the current
season's daily download under data/raw/moneypuck). One row per player-game with the stats the product shows:
time on ice (all, power play, penalty kill), shots, goals, assists, points, hits, blocked shots.

Everything here is observed history. Nothing is projected. Rows are cached in a compact gzip file keyed by the
source files' sizes and modification times, so a daily current-season refresh rebuilds only when a source changed.
"""
from __future__ import annotations

import csv
import glob
import gzip
import io
import json
import os
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SKATER_RAW = REPO / "research" / "player_sog" / "raw"
GOALIE_RAW = REPO / "research" / "goalie_intelligence" / "raw"
CURRENT_SKATER_GLOB = str(REPO / "data" / "raw" / "moneypuck" / "skater" / "2026" / "*_skater_2026.zip")
CURRENT_GOALIE_GLOB = str(REPO / "data" / "raw" / "moneypuck" / "goalie" / "2026" / "*_goalie_2026.zip")
HISTORY_SEASONS = (2022, 2023, 2024, 2025)          # MoneyPuck "season" = the year the season starts
CURRENT_SEASON = 2026

SKATER_FIELDS = ("player_id", "name", "game_id", "season", "team", "opp", "home", "date", "pos",
                 "toi", "toi_5v5", "toi_pp", "toi_pk", "shots", "goals", "assists", "points", "hits", "blocks")
GOALIE_FIELDS = ("player_id", "name", "game_id", "season", "team", "opp", "home", "date", "toi", "shots_against", "goals_against")


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except ValueError:
        return 0.0


def _date(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _skater_rows(stream) -> list[dict]:
    games: dict[tuple, dict] = {}
    for r in csv.DictReader(stream):
        key = (r["playerId"], r["gameId"])
        g = games.get(key)
        if g is None:
            g = games[key] = {
                "player_id": r["playerId"], "name": r["name"], "game_id": int(r["gameId"]), "season": int(r["season"]),
                "team": r["playerTeam"], "opp": r["opposingTeam"], "home": r["home_or_away"] == "HOME",
                "date": _date(r["gameDate"]), "pos": r["position"], "toi": 0.0, "toi_5v5": 0.0, "toi_pp": 0.0,
                "toi_pk": 0.0, "shots": 0.0, "goals": 0.0, "assists": 0.0, "points": 0.0, "hits": 0.0, "blocks": 0.0}
        sit = r["situation"]
        minutes = _f(r, "icetime") / 60.0
        if sit == "all":
            g["toi"] = minutes
            g["shots"] = _f(r, "I_F_shotsOnGoal")
            g["goals"] = _f(r, "I_F_goals")
            g["assists"] = _f(r, "I_F_primaryAssists") + _f(r, "I_F_secondaryAssists")
            g["points"] = _f(r, "I_F_points")
            g["hits"] = _f(r, "I_F_hits")
            g["blocks"] = _f(r, "shotsBlockedByPlayer")
        elif sit == "5on5":
            g["toi_5v5"] = minutes
        elif sit == "5on4":
            g["toi_pp"] = minutes
        elif sit == "4on5":
            g["toi_pk"] = minutes
    return [g for g in games.values() if g["toi"] > 0]


def _goalie_rows(stream) -> list[dict]:
    out = []
    for r in csv.DictReader(stream):
        if r["situation"] != "all" or _f(r, "icetime") <= 0:
            continue
        out.append({"player_id": r["playerId"], "name": r["name"], "game_id": int(r["gameId"]), "season": int(r["season"]),
                    "team": r["playerTeam"], "opp": r["opposingTeam"], "home": r["home_or_away"] == "HOME",
                    "date": _date(r["gameDate"]), "toi": _f(r, "icetime") / 60.0,
                    "shots_against": _f(r, "ongoal"), "goals_against": _f(r, "goals")})
    return out


def _zip_stream(path: str):
    z = zipfile.ZipFile(path)
    return io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")


def _latest(pattern: str) -> str | None:
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _signature(paths: list[str]) -> str:
    return json.dumps([(p, os.path.getsize(p), int(os.path.getmtime(p))) for p in paths])


def _cached(name: str, paths: list[str], build, cache_dir: Path | None = None) -> list[dict]:
    from operational import state_paths
    cache = state_paths.path(name) if cache_dir is None else cache_dir / name
    signature = _signature(paths)
    if cache.exists():
        with gzip.open(cache, "rt") as fh:
            header = json.loads(fh.readline())
            if header.get("signature") == signature:
                return [json.loads(line) for line in fh]
    rows = build()
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        fh.write(json.dumps({"signature": signature}) + "\n")
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    tmp.replace(cache)
    return rows


def skater_games(include_current: bool = True, cache_dir: Path | None = None) -> list[dict]:
    """Every skater game, completed seasons first then the current season, sorted by date."""
    paths = [str(SKATER_RAW / f"{s}.csv") for s in HISTORY_SEASONS if (SKATER_RAW / f"{s}.csv").exists()]
    current = _latest(CURRENT_SKATER_GLOB) if include_current else None
    if current:
        paths.append(current)

    def build():
        rows: list[dict] = []
        for p in paths:
            if p.endswith(".zip"):
                rows += _skater_rows(_zip_stream(p))
            else:
                with open(p, newline="", encoding="utf-8") as fh:
                    rows += _skater_rows(fh)
        rows.sort(key=lambda r: (r["date"], r["game_id"], r["player_id"]))
        return rows
    return _cached("product_skater_games.jsonl.gz", paths, build, cache_dir)


def goalie_games(include_current: bool = True, cache_dir: Path | None = None) -> list[dict]:
    paths = [str(GOALIE_RAW / f"{s}.csv") for s in HISTORY_SEASONS if (GOALIE_RAW / f"{s}.csv").exists()]
    current = _latest(CURRENT_GOALIE_GLOB) if include_current else None
    if current:
        paths.append(current)

    def build():
        rows: list[dict] = []
        for p in paths:
            if p.endswith(".zip"):
                rows += _goalie_rows(_zip_stream(p))
            else:
                with open(p, newline="", encoding="utf-8") as fh:
                    rows += _goalie_rows(fh)
        rows.sort(key=lambda r: (r["date"], r["game_id"], r["player_id"]))
        return rows
    return _cached("product_goalie_games.jsonl.gz", paths, build, cache_dir)
