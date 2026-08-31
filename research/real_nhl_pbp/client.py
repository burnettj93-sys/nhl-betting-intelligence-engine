"""
Minimal, self-contained client for the real, public, keyless NHL
play-by-play endpoint. Mirrors the request/error-handling shape already
used in ingest/nhl_api.py's _get_json() (session.get -> raise_for_status
-> .json(), fail loudly on a non-dict response) but is deliberately NOT
imported from ingest/ -- this slice builds strictly under research/ and
must not create a dependency on production ingestion code.

No API key: api-web.nhle.com is a public NHL endpoint (confirmed live in
this session -- see NHL_PLAY_BY_PLAY_FOUNDATION_REPORT.md Section A).
"""
from __future__ import annotations

BASE_URL = "https://api-web.nhle.com/v1"


class PbpApiError(RuntimeError):
    """Raised when a play-by-play response is missing fields this module
    depends on, or is not the JSON object shape expected -- fail loudly
    rather than silently normalizing partial/wrong data."""


def play_by_play_url(game_id: int) -> str:
    return f"{BASE_URL}/gamecenter/{game_id}/play-by-play"


def fetch_play_by_play(session, game_id: int) -> dict:
    url = play_by_play_url(game_id)
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise PbpApiError(f"expected a JSON object from {url}, got {type(data)}")
    if "plays" not in data or not isinstance(data["plays"], list):
        raise PbpApiError(f"response from {url} has no plays[] list")
    return data
