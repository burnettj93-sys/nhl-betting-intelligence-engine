"""
Each game's last valid pregame analysis, kept so it survives the game starting (and the process restarting), plus recovery from genuine archives.

remember()   every board build stores, per game that is still SCHEDULED and was analysed, that game's board block under (Eastern day, game id). The last block stored before puck drop is the one kept.
recall()     the stored block for a game that has now started or finished.
recover()    for a started game with nothing stored: the newest published snapshot on the `cloud-data` branch (one commit per publication, so the history is a genuine archive of what the hosted
             board showed) in which that game was still scheduled and analysed. Read-only git operations on the local repository; nothing is recomputed or invented. When no archive holds it, the
             board says "pregame analysis unavailable" rather than "Game not scheduled".
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess

from operational import state_paths

NAME = "slate_board_pregame.json"
KEEP_DAYS = 3
MAX_COMMITS = 200


def _load() -> dict:
    try:
        return json.loads(state_paths.path(NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(doc: dict) -> None:
    try:
        p = state_paths.path(NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, default=str))
        tmp.replace(p)
    except OSError:
        pass


def remember(doc: dict) -> int:
    """Store the blocks of this board's still-scheduled, analysed games. Returns how many were stored."""
    day = doc.get("date_et")
    if not day:
        return 0
    store = _load()
    mine = store.setdefault(day, {})
    n = 0
    built = doc.get("generated_at_utc")
    for g in doc.get("games") or []:
        if g.get("start_utc") and built and str(built) >= str(g["start_utc"]):
            continue                                                  # the board was built at or after puck drop: this block is no longer a pregame block, whatever the database still says
        if g.get("state") == "SCHEDULED" and (g.get("coverage") or {}).get("analysis_available") and not g.get("frozen_at_utc"):
            mine[str(g["game_id"])] = {**g, "_stored_at_utc": doc.get("generated_at_utc")}
            n += 1
    for old in sorted(k for k in store if not k.startswith("_"))[:-KEEP_DAYS]:
        del store[old]
    _save(store)
    return n


def recall(day: str, game_id: str) -> dict | None:
    b = (_load().get(day) or {}).get(str(game_id))
    if not b:
        return None
    b = dict(b)
    stored = b.pop("_stored_at_utc", None)
    b["frozen_at_utc"] = stored
    b["frozen_source"] = "SAVED_PREGAME_BLOCK"
    return b


def _git(args: list[str], cwd, timeout: int = 60) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


LAST_RECOVERY = {"searched": False}      # whether the last recover() call could actually read the publication history (a timed-out fetch must not mark a game as searched)


def recover(day: str, game_ids: set[str], *, repo=None, ref: str = "origin/cloud-data", log_fn=None, show_fn=None, fetch: bool = True) -> dict[str, dict]:
    """{game_id: block} for the requested games, from the newest published snapshots in which each was still SCHEDULED and analysed (searching back at most MAX_COMMITS publications)."""
    from pathlib import Path
    repo = repo or Path(__file__).resolve().parent.parent
    LAST_RECOVERY["searched"] = False
    if not game_ids:
        return {}
    if fetch and log_fn is None:
        _git(["fetch", "--quiet", "origin", "cloud-data"], repo, 30)
    log = (log_fn or (lambda: _git(["log", "--format=%H", f"-n{MAX_COMMITS}", ref], repo)))()
    if not log:
        return {}
    LAST_RECOVERY["searched"] = True
    show = show_fn or (lambda sha: _git(["show", f"{sha}:current/snapshot.json"], repo, 60))
    found: dict[str, dict] = {}
    for sha in log.split():
        if len(found) == len(game_ids):
            break
        text = show(sha)
        if not text:
            continue
        try:
            board = (json.loads(text) or {}).get("slate_board") or {}
        except json.JSONDecodeError:
            continue
        if board.get("date_et") != day:
            if found:
                break                                                 # older than the day: nothing earlier can hold it
            continue
        for g in board.get("games") or []:
            gid = str(g.get("game_id"))
            if gid in game_ids and gid not in found and g.get("state") == "SCHEDULED" and (g.get("coverage") or {}).get("analysis_available"):
                found[gid] = {**g, "frozen_at_utc": board.get("generated_at_utc"), "frozen_source": "PUBLISHED_SNAPSHOT_ARCHIVE", "frozen_commit": sha[:10]}
    return found


def attempted(day: str) -> set[str]:
    return set((_load().get("_recovery_attempted") or {}).get(day) or [])


def mark_attempted(day: str, game_ids: set[str]) -> None:
    store = _load()
    meta = store.setdefault("_recovery_attempted", {})
    meta[day] = sorted(set(meta.get(day) or []) | set(game_ids))
    for old in sorted(meta)[:-KEEP_DAYS]:
        del meta[old]
    _save(store)
