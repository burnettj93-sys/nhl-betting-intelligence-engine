"""
Current-season goalie records from the NHL's public player pages (api-web.nhle.com/v1/player/<id>/landing):
games played/started, wins, losses, overtime losses, save percentage, goals-against average, shutouts, shots against.

These are the league's own numbers, shown as published. Nothing is estimated here. Each goalie's record is cached with the
time it was fetched; a failed fetch keeps the last good record and says so (`error`, `last_ok_utc`), so a page can state
exactly how old a number is and why it was not refreshed.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from operational import state_paths

CACHE_NAME = "nhl_goalie_landing.json"
URL = "https://api-web.nhle.com/v1/player/{pid}/landing"
MAX_AGE_HOURS = 6.0
SEASON_ID = 20262027


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_cache() -> dict:
    p = state_paths.path(CACHE_NAME)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    p = state_paths.path(CACHE_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, sort_keys=True))
    tmp.replace(p)


def parse_landing(doc: dict, season_id: int = SEASON_ID) -> dict:
    """The regular-season line for `season_id` from a landing document, or {'games': 0}."""
    row = None
    for s in doc.get("seasonTotals", []):
        if s.get("season") == season_id and s.get("gameTypeId") == 2 and s.get("leagueAbbrev") == "NHL":
            row = s
    base = {"team": doc.get("currentTeamAbbrev"), "catches": doc.get("shootsCatches"), "sweater": doc.get("sweaterNumber"),
            "headshot": doc.get("headshot")}
    if row is None:
        return {**base, "games": 0}
    return {**base, "games": row.get("gamesPlayed", 0), "starts": row.get("gamesStarted"), "wins": row.get("wins"),
            "losses": row.get("losses"), "ot_losses": row.get("otLosses"), "save_pct": row.get("savePctg"),
            "gaa": row.get("goalsAgainstAvg"), "shutouts": row.get("shutouts"), "shots_against": row.get("shotsAgainst"),
            "goals_against": row.get("goalsAgainst"), "toi": row.get("timeOnIce")}


def _fetch_one(pid: str, session=None) -> dict:
    import requests
    resp = (session or requests).get(URL.format(pid=pid), timeout=20)
    resp.raise_for_status()
    return parse_landing(resp.json())


def refresh(goalie_ids: list[str], *, now: dt.datetime | None = None, fetch=_fetch_one, max_age_hours: float = MAX_AGE_HOURS,
            workers: int = 6) -> dict:
    """Refreshes stale or missing records; returns the whole cache. A failure never discards the last good record."""
    now = now or _now()
    cache = load_cache()

    def due(pid):
        rec = cache.get(pid)
        if not rec or not rec.get("fetched_at_utc"):
            return True
        age = (now - dt.datetime.fromisoformat(rec["fetched_at_utc"].replace("Z", "+00:00"))).total_seconds() / 3600
        return age >= max_age_hours or bool(rec.get("error"))

    todo = [p for p in goalie_ids if due(p)]

    def work(pid):
        try:
            return pid, {"stats": fetch(pid), "fetched_at_utc": _iso(now), "last_ok_utc": _iso(now), "error": None}
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
            old = cache.get(pid) or {}
            return pid, {"stats": old.get("stats"), "fetched_at_utc": _iso(now), "last_ok_utc": old.get("last_ok_utc"),
                         "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for pid, rec in pool.map(work, todo):
            cache[pid] = rec
    if todo:
        _save_cache(cache)
    return cache
