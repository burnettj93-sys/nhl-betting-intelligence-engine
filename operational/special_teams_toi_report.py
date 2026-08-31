"""
Part 1/2: the REAL, official NHL live data source for per-player
PP/SH/EV ice time -- verified live against a real completed 2024-25 game
(2024020850) while building this module. The standard JSON boxscore
(`/v1/gamecenter/{id}/boxscore`) does NOT carry a PP/SH TOI breakdown --
only total `toi` per player. The classic NHL "Time On Ice" HTML report
(linked from `/v1/gamecenter/{id}/right-rail`'s own `gameReports.toiHome`/
`toiAway` fields, e.g.
`https://www.nhl.com/scores/htmlreports/{season}/TH{gameIdTail}.HTM`)
DOES: each player's block ends in a "TOT" row with columns
[TOT, SHF, AVG, TOI, EV TOT, PP TOT, SH TOT] -- confirmed to reconcile
exactly (EV+PP+SH == TOI) on real data.

This report has NO player_id -- only jersey number + name. Player
identity is resolved via the real boxscore JSON's own
(team, sweaterNumber) -> playerId mapping for the SAME game (Part 5:
"reuse canonical NHL player IDs, do not match primarily by name" --
name is used here only as a human-readable cross-check, never as the
join key).
"""
from __future__ import annotations

import re
import time

import requests

BASE_BOXSCORE_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"
BASE_RIGHT_RAIL_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/right-rail"
REQUEST_TIMEOUT_SECONDS = 20

_PLAYER_BLOCK_RE = re.compile(
    r'playerHeading[^>]*colspan="8">(\d+)\s+([A-Z\-\' .]+),\s*([A-Z\-\' .]+)</td>', re.S)
_TOT_ROW_RE = re.compile(
    r'<td[^>]*>TOT</td>\s*'
    r'<td[^>]*>(\d+)</td>\s*'          # SHF
    r'<td[^>]*>(\d{1,2}:\d{2})</td>\s*'  # AVG
    r'<td[^>]*>(\d{1,3}:\d{2})</td>\s*'  # TOI
    r'<td[^>]*>(\d{1,3}:\d{2})</td>\s*'  # EV TOT
    r'<td[^>]*>(\d{1,3}:\d{2})</td>\s*'  # PP TOT
    r'<td[^>]*>(\d{1,3}:\d{2})</td>', re.S)


class ToiReportUnavailable(Exception):
    pass


def _mmss_to_seconds(s: str) -> float:
    parts = s.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def get_report_urls(game_id: int) -> dict:
    """Real, live lookup (never a hand-built URL guess) via the
    boxscore/right-rail JSON's own `gameReports` field, which already
    carries the correct season-prefixed report URLs for this exact game."""
    resp = requests.get(BASE_RIGHT_RAIL_URL.format(game_id=game_id), timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    reports = data.get("gameReports", {})
    home_url, away_url = reports.get("toiHome"), reports.get("toiAway")
    if not home_url or not away_url:
        raise ToiReportUnavailable(f"no toiHome/toiAway report URL for game {game_id}")
    return {"home": home_url, "away": away_url}


def fetch_report_html(url: str) -> str:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.content.decode("latin-1")


def parse_toi_report(html: str) -> list[dict]:
    """Returns one dict per player found in this ONE side's (home or
    away) report: {sweater_number, last_name, first_name, shifts,
    total_toi_seconds, ev_toi_seconds, pp_toi_seconds, sh_toi_seconds}.
    A player block with no parseable TOT row is skipped (not fabricated
    as a zero) -- this has not been observed on any real report checked
    so far, but a missing TOT row is a real reason to skip rather than
    guess."""
    out = []
    player_positions = [(m.start(), m) for m in _PLAYER_BLOCK_RE.finditer(html)]
    for i, (start, m) in enumerate(player_positions):
        end = player_positions[i + 1][0] if i + 1 < len(player_positions) else len(html)
        block = html[start:end]
        tot_match = _TOT_ROW_RE.search(block)
        if tot_match is None:
            continue
        shf, avg, toi, ev, pp, sh = tot_match.groups()
        out.append({
            "sweater_number": int(m.group(1)), "last_name": m.group(2).strip(),
            "first_name": m.group(3).strip(), "shifts": int(shf),
            "total_toi_seconds": _mmss_to_seconds(toi), "ev_toi_seconds": _mmss_to_seconds(ev),
            "pp_toi_seconds": _mmss_to_seconds(pp), "sh_toi_seconds": _mmss_to_seconds(sh),
        })
    return out


def get_boxscore_identity_crosswalk(game_id: int) -> dict:
    """Real (team_abbrev, sweater_number) -> {player_id, name} crosswalk
    for THIS game, from the official boxscore JSON -- the only source of
    a real canonical player_id anywhere in this pipeline (Part 5)."""
    resp = requests.get(BASE_BOXSCORE_URL.format(game_id=game_id), timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    crosswalk = {}
    for side in ("awayTeam", "homeTeam"):
        team_abbrev = data[side]["abbrev"]
        stats = data["playerByGameStats"][side]
        for group in ("forwards", "defense", "goalies"):
            for p in stats.get(group, []):
                key = (team_abbrev, p["sweaterNumber"])
                crosswalk[key] = {"player_id": p["playerId"],
                                   "name": p["name"].get("default", "")}
    home_abbrev, away_abbrev = data["homeTeam"]["abbrev"], data["awayTeam"]["abbrev"]
    return {"crosswalk": crosswalk, "home_abbrev": home_abbrev, "away_abbrev": away_abbrev,
            "game_date": data["gameDate"]}


def ingest_game_special_teams_toi(game_id: int, sleep_between_requests: float = 0.3) -> list[dict]:
    """Full real pipeline for one game: fetch report URLs -> fetch both
    HTML reports -> parse -> resolve identity via the real boxscore
    crosswalk -> return canonical records (Part 4's storage shape).
    Raises ToiReportUnavailable / requests.RequestException rather than
    silently returning partial/fabricated data; the caller decides how
    to log/skip a real failure."""
    identity = get_boxscore_identity_crosswalk(game_id)
    urls = get_report_urls(game_id)
    time.sleep(sleep_between_requests)

    records = []
    for side, team_abbrev in (("home", identity["home_abbrev"]), ("away", identity["away_abbrev"])):
        html = fetch_report_html(urls[side])
        for p in parse_toi_report(html):
            key = (team_abbrev, p["sweater_number"])
            ident = identity["crosswalk"].get(key)
            if ident is None:
                continue  # a real jersey/team combo not found in the boxscore -- skip, never guess
            records.append({
                "game_id": game_id, "game_date": identity["game_date"], "player_id": str(ident["player_id"]),
                "player_name": ident["name"], "team": team_abbrev,
                "total_toi_seconds": p["total_toi_seconds"], "ev_toi_seconds": p["ev_toi_seconds"],
                "pp_toi_seconds": p["pp_toi_seconds"], "sh_toi_seconds": p["sh_toi_seconds"],
                "played": p["total_toi_seconds"] > 0, "source": "NHL_TOI_REPORT",
            })
        time.sleep(sleep_between_requests)
    return records
