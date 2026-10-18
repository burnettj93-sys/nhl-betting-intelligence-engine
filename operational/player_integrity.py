"""
Player identity and matchup integrity: is this player really on this team, in this game, and is this name unambiguous?

Prices arrive with a player NAME only; the model knows players by id and team. Three defects can put the wrong person on a card or in a slip:
  * AMBIGUOUS NAME   two players with the same normalised name on one team (Vancouver has two Elias Pettersons): a name join keeps one of them and prices the wrong person. Both are withdrawn.
  * ROSTER MISMATCH  the player is not on the team's CURRENT official NHL roster (a stale roster sync: waived, traded, reassigned): the selection is withdrawn.
  * WRONG GAME       the player's team is neither side of the game the card is for.
The official roster is the NHL's own `roster/{team}/current` feed (free, no credits), fetched at most every ROSTER_MAX_AGE_MIN per team and kept with its fetch time; a failed fetch keeps the last good copy and
says so. Without a usable official roster a selection is UNVERIFIED: it is still analysed and shown (labelled), but it can never be RECORDED (daily_tickets.revalidate_before_recording).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from typing import Callable

from operational import state_paths

ROSTER_URL = "https://api-web.nhle.com/v1/roster/{team}/current"
ROSTER_STATE = "official_rosters.json"
AMBIGUOUS_STATE = "player_integrity.json"
MAX_FETCHES_PER_CALL = 10     # the NHL API rate-limits bursts (HTTP 429): at most this many teams per cycle, paced; the rest are fetched on the next cycle
ROSTER_MAX_AGE_MIN = 180        # re-fetch a team's official roster when the copy is older than this
USABLE_MAX_AGE_H = 36           # an official roster older than this is not evidence
OK, MISMATCH, UNVERIFIED, AMBIGUOUS, WRONG_GAME = "OK", "MISMATCH", "UNVERIFIED", "AMBIGUOUS", "WRONG_GAME"


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def official_names(conn) -> dict[str, str]:
    """{player_id: full name} from the NHL-sourced players table. The MoneyPuck game logs drop non-ASCII letters ("Lafrenire", "Sttzle", "Slafkovsk"), so a name taken from them never matches the
    provider's "Alexis Lafrenière": the player's prices were silently unmatched. The players table carries the NHL's own spelling and is the name of record."""
    try:
        return {str(r[0]): r[1] for r in conn.execute("SELECT player_id, full_name FROM players WHERE full_name IS NOT NULL")}
    except Exception:  # noqa: BLE001
        return {}


def names_compatible(db_name: str, official: str) -> bool:
    """Same person's name despite dropped characters: equal when normalised, or the (shorter, mangled) one is a subsequence of the other with at most 4 characters missing."""
    a, b = norm(db_name), norm(official)
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(long_) - len(short) > 4:
        return False
    it = iter(long_)
    return all(ch in it for ch in short)


def _load(name: str) -> dict:
    try:
        return json.loads(state_paths.path(name).read_text())
    except (OSError, ValueError):
        return {}


def _save(name: str, doc: dict) -> None:
    p = state_paths.path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(p)


def fetch_roster(team: str, *, timeout: float = 10.0) -> dict[str, str]:
    """{player_id: full name} from the NHL's current roster for one team. Raises on any failure (the caller keeps its last good copy)."""
    import requests
    last = None
    for _ in range(2):
        try:
            r = requests.get(ROSTER_URL.format(team=team), timeout=timeout)
            r.raise_for_status()
            d = r.json()
            out = {str(p["id"]): f"{p['firstName']['default']} {p['lastName']['default']}" for g in ("forwards", "defensemen", "goalies") for p in d.get(g, [])}
            if not out:
                raise ValueError("empty roster")
            return out
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"{type(last).__name__}: {last}")


def refresh_official(teams, now: dt.datetime | None = None, *, fetch: Callable | None = None, max_age_min: float = ROSTER_MAX_AGE_MIN, max_fetches: int = MAX_FETCHES_PER_CALL, pause_s: float | None = None) -> dict:
    """Bring the official rosters of `teams` up to date (each at most every `max_age_min`). Never raises; a failed team keeps its previous copy and records the error. Returns {team: status}."""
    if state_paths.under_test() and fetch is None:
        return {}                                   # a unit test never reaches the network
    now = now or dt.datetime.now(dt.timezone.utc)
    pause = (0.4 if fetch is None else 0.0) if pause_s is None else pause_s
    fetch = fetch or fetch_roster
    import time
    doc = _load(ROSTER_STATE)
    out = {}
    fetched = 0
    # the stalest copies first, so a rate-limited burst never starves the same teams
    for t in sorted(set(teams), key=lambda x: (doc.get(x) or {}).get("fetched_at_utc") or ""):
        cur = doc.get(t) or {}
        try:
            age = (now - dt.datetime.fromisoformat(cur["fetched_at_utc"].replace("Z", "+00:00"))).total_seconds() / 60.0 if cur.get("fetched_at_utc") else None
        except (KeyError, ValueError):
            age = None
        if age is not None and age < max_age_min and not cur.get("error"):
            out[t] = "CURRENT"
            continue
        if fetched >= max_fetches:
            out[t] = "DEFERRED_TO_NEXT_CYCLE"
            continue
        fetched += 1
        if pause and fetched > 1:
            time.sleep(pause)
        try:
            doc[t] = {"fetched_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "players": fetch(t), "error": None}
            out[t] = "FETCHED"
        except Exception as exc:  # noqa: BLE001
            doc[t] = {**cur, "error": f"{type(exc).__name__}: {str(exc)[:120]}", "error_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            out[t] = "FAILED_KEPT_LAST_GOOD" if cur.get("players") else "FAILED_NO_COPY"
    _save(ROSTER_STATE, doc)
    return out


def verdict(player_id, team: str, now: dt.datetime | None = None, *, name: str | None = None, rosters: dict | None = None) -> dict:
    """OK when the player's id is on `team`'s current official roster (and, when a name is given, the names agree); MISMATCH when the roster is current and does not hold him (or holds another
    person under that id); UNVERIFIED when no usable official roster exists."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rec = (rosters if rosters is not None else _load(ROSTER_STATE)).get(team) or {}
    stamp = rec.get("fetched_at_utc")
    try:
        age_h = (now - dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds() / 3600.0 if stamp else None
    except ValueError:
        age_h = None
    if not rec.get("players") or age_h is None or age_h > USABLE_MAX_AGE_H:
        return {"status": UNVERIFIED, "detail": f"no usable official {team} roster" + ("" if age_h is None else f" (last fetched {age_h:.0f} h ago)")}
    official = rec["players"].get(str(player_id))
    if official is None:
        return {"status": MISMATCH, "detail": f"id {player_id} is not on {team}'s official roster (fetched {stamp})"}
    if name and not names_compatible(name, official):
        return {"status": MISMATCH, "detail": f"id {player_id} is {official} on the official {team} roster, not {name}"}
    return {"status": OK, "detail": f"on the official {team} roster ({stamp})"}


def duplicate_names(names_by_id: dict) -> dict[str, list[str]]:
    """{normalised name: [ids]} for every name held by more than one id in a team's roster map {id: full name}."""
    seen: dict[str, list[str]] = {}
    for pid, nm in names_by_id.items():
        seen.setdefault(norm(nm), []).append(str(pid))
    return {k: v for k, v in seen.items() if len(v) > 1}


def record_ambiguous(date: str, items: list[dict]) -> None:
    """Names withdrawn because two players share them on one team: [{"team", "name", "ids"}], kept for the board and the audit (one list per day, replaced at each model build)."""
    doc = _load(AMBIGUOUS_STATE)
    doc[date] = items
    for old in sorted(k for k in doc if k < date)[:-3]:
        del doc[old]
    _save(AMBIGUOUS_STATE, doc)


def ambiguous_for(date: str) -> list[dict]:
    return _load(AMBIGUOUS_STATE).get(date) or []


def is_ambiguous(date: str, team: str, name: str) -> bool:
    n = norm(name)
    return any(a["team"] == team and norm(a["name"]) == n for a in ambiguous_for(date))


def check_selection(player_id, name: str, team: str, game: dict, date: str, now: dt.datetime | None = None, *, rosters: dict | None = None) -> dict:
    """One selection on one game's card: WRONG_GAME (his team is not in this game), AMBIGUOUS (shared name on his team), else the official-roster verdict."""
    if team not in (game.get("home"), game.get("away")):
        return {"status": WRONG_GAME, "detail": f"{name} is listed for {team}, not {game.get('away')} at {game.get('home')}"}
    if is_ambiguous(date, team, name):
        return {"status": AMBIGUOUS, "detail": f"two players named {name} are on {team}: the name cannot be matched to one person"}
    return verdict(player_id, team, now, name=name, rosters=rosters)


ENV_GATE = "NHL_ENGINE_ROSTER_GATE"


def recording_gate_enabled() -> bool:
    """On unless NHL_ENGINE_ROSTER_GATE=off (the test suite turns it off for machinery tests that have no official rosters; tests of the gate turn it on)."""
    import os
    return os.environ.get(ENV_GATE, "on").strip().lower() != "off"


def blocking(status: str) -> bool:
    """A selection that must be WITHDRAWN (shown nowhere as a pick, never in a slip). UNVERIFIED is shown but cannot be recorded."""
    return status in (MISMATCH, AMBIGUOUS, WRONG_GAME)
