"""
Price availability: for every game and market, WHY there is (or is not) a DraftKings price on file. Three different reasons used to look the same
("no price"), and only one of them is a fact about the bookmaker:

  POSTED          the provider returned this market for the game (n outcomes, last updated at T)
  NOT_POSTED      we asked and the provider returned nothing for it. The bookmaker has not posted it. A request that returns nothing costs 0 credits
                  (provider docs, and every archived empty answer: x-requests-last = 0), so this is a fact, not a guess.
  NOT_FETCHED     we have not asked (yet): the slot for this game has not opened, or the job has not run since it opened
  BUDGET_BLOCKED  we did not ask because the day's credit plan did not cover it. Whether the bookmaker has posted it is then unknown.
  FETCH_ERROR     we asked and the request failed (network, rate limit, bad response)

State is one small document per Eastern day (`price_availability.json`, newest check per game and market) plus an append-only history
(`price_availability_history.jsonl`: every check with hours to puck drop), which is the evidence for "how early does DraftKings post each market".
Nothing here spends a credit or changes what any ticket may use; it only records and explains.
"""
from __future__ import annotations

import datetime as dt
import json

from operational import state_paths

STATE_NAME = "price_availability.json"
HISTORY_NAME = "price_availability_history.jsonl"

POSTED, NOT_POSTED, NOT_FETCHED, BUDGET_BLOCKED, FETCH_ERROR = "POSTED", "NOT_POSTED", "NOT_FETCHED", "BUDGET_BLOCKED", "FETCH_ERROR"
STATUSES = (POSTED, NOT_POSTED, NOT_FETCHED, BUDGET_BLOCKED, FETCH_ERROR)

WORDS = {
    POSTED: "Posted by DraftKings",
    NOT_POSTED: "Not posted by DraftKings yet",
    NOT_FETCHED: "Not fetched yet",
    BUDGET_BLOCKED: "Not fetched — odds-credit budget",
    FETCH_ERROR: "Fetch failed",
}


def _stamp(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify_payload(payload: dict | None, requested: list[str]) -> dict[str, dict]:
    """{market_key: {"status", "outcomes", "last_update"}} for one provider event payload (None = nothing usable came back)."""
    out = {m: {"status": NOT_POSTED, "outcomes": 0, "last_update": None} for m in requested}
    if not isinstance(payload, dict):
        return out
    for bm in payload.get("bookmakers") or []:
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets") or []:
            key = m.get("key")
            n = len(m.get("outcomes") or [])
            if key in out and n:
                out[key] = {"status": POSTED, "outcomes": n, "last_update": m.get("last_update") or bm.get("last_update")}
    return out


KEEP_DAYS = 4


def _doc() -> dict:
    try:
        d = json.loads(state_paths.path(STATE_NAME).read_text())
        if isinstance(d.get("days"), dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"days": {}}


def _load(day: str) -> dict:
    return _doc()["days"].get(day) or {"day": day, "games": {}}


def _save(day_doc: dict) -> None:
    try:
        whole = _doc()
        whole["days"][day_doc["day"]] = day_doc
        for old in sorted(whole["days"])[:-KEEP_DAYS]:
            del whole["days"][old]
        p = state_paths.path(STATE_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(whole, sort_keys=True))
        tmp.replace(p)
    except OSError:
        pass


def tomorrow_last_check() -> dt.datetime | None:
    from operational import quote_freshness as qf
    return qf.parse_utc(_doc().get("tomorrow_last_check_utc"))


def mark_tomorrow_checked(now: dt.datetime) -> None:
    try:
        whole = _doc()
        whole["tomorrow_last_check_utc"] = _stamp(now)
        p = state_paths.path(STATE_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(whole, sort_keys=True))
        tmp.replace(p)
    except OSError:
        pass


def record(day: str, game_id: str, market: str, status: str, now: dt.datetime, *, hours_to_start: float | None = None, outcomes: int = 0,
           last_update: str | None = None, detail: str | None = None, game_date: str | None = None, matchup: str | None = None, start_utc: str | None = None) -> None:
    """Remember the newest answer for (game, market) and append it to the history. Never raises: bookkeeping must not break a job."""
    if status not in STATUSES:
        raise ValueError(status)
    try:
        doc = _load(day)
        g = doc["games"].setdefault(str(game_id), {"markets": {}})
        if matchup:
            g["matchup"] = matchup
        if game_date:
            g["game_date"] = game_date
        if start_utc:
            g["start_utc"] = start_utc
        g["markets"][market] = {"status": status, "checked_utc": _stamp(now), "outcomes": outcomes, "last_update": last_update, "detail": detail}
        # a status that is only a statement about our own plan must not overwrite a real answer from the provider the same day
        _save(doc)
        if status in (POSTED, NOT_POSTED):
            with open(state_paths.path(HISTORY_NAME), "a") as f:
                f.write(json.dumps({"at": _stamp(now), "day": day, "game_id": str(game_id), "market": market, "status": status, "outcomes": outcomes,
                                    "hours_to_start": None if hours_to_start is None else round(hours_to_start, 2)}) + "\n")
    except OSError:
        pass


def note_unfetched(day: str, game_id: str, market: str, status: str, now: dt.datetime, *, detail: str | None = None, **kw) -> None:
    """NOT_FETCHED / BUDGET_BLOCKED / FETCH_ERROR: kept only when the provider has not already answered for this market today (a real
    POSTED / NOT_POSTED answer is more informative than a statement about our own plan, and stays on file with its time)."""
    doc = _load(day)
    cur = ((doc["games"].get(str(game_id)) or {}).get("markets") or {}).get(market)
    if cur and cur["status"] in (POSTED, NOT_POSTED) and status != FETCH_ERROR:
        return
    if cur and cur["status"] == status and status != FETCH_ERROR:
        return                      # the same statement again changes nothing; keep the time it was first made
    record(day, game_id, market, status, now, detail=detail, **kw)


def read(day: str) -> dict:
    return _load(day)


def history() -> list[dict]:
    out = []
    try:
        for line in state_paths.path(HISTORY_NAME).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def posting_lead_hours(rows: list[dict]) -> dict[str, dict]:
    """Per market, from the checks on file: the longest lead (hours before puck drop) at which it was POSTED, and the shortest lead at which it
    was seen NOT_POSTED. The first says 'posted at least this early'; the second 'was still missing this close to the game'."""
    out: dict[str, dict] = {}
    for r in rows:
        h = r.get("hours_to_start")
        if h is None:
            continue
        m = out.setdefault(r["market"], {"posted_earliest_h": None, "not_posted_latest_h": None, "checks": 0})
        m["checks"] += 1
        if r["status"] == POSTED:
            m["posted_earliest_h"] = h if m["posted_earliest_h"] is None else max(m["posted_earliest_h"], h)
        else:
            m["not_posted_latest_h"] = h if m["not_posted_latest_h"] is None else min(m["not_posted_latest_h"], h)
    return out


def summary(day: str, game_ids: list[str] | None = None) -> dict:
    """The published block: per game and market status counts and the per-game table."""
    doc = _load(day)
    games = doc["games"]
    if game_ids is not None:
        games = {g: v for g, v in games.items() if g in set(game_ids)}
    counts = {s: 0 for s in STATUSES}
    for g in games.values():
        for m in g["markets"].values():
            counts[m["status"]] += 1
    return {"day": day, "games": games, "counts": counts, "lead_hours": posting_lead_hours(history()), "words": WORDS}


def sentence(day: str, game_ids: list[str] | None = None) -> str:
    """One plain sentence on the state of today's prices, naming the reason for every game that has none."""
    doc = _load(day)["games"]
    ids = list(game_ids) if game_ids is not None else list(doc)
    if not ids:
        return ""
    posted = [g for g in ids if any(m["status"] == POSTED for m in (doc.get(g) or {}).get("markets", {}).values())]
    rest = [g for g in ids if g not in posted]

    def worst(g):
        st = {m["status"] for m in (doc.get(g) or {}).get("markets", {}).values()}
        for s in (BUDGET_BLOCKED, FETCH_ERROR, NOT_POSTED, NOT_FETCHED):
            if s in st:
                return s
        return NOT_FETCHED
    reasons = {}
    for g in rest:
        reasons[worst(g)] = reasons.get(worst(g), 0) + 1
    parts = [f"DraftKings player prices are on file for {len(posted)} of {len(ids)} games"]
    why = {BUDGET_BLOCKED: "not looked at (the odds-credit plan did not cover an early look; a pregame price follows for the games it does cover)", NOT_POSTED: "not posted by DraftKings yet",
           NOT_FETCHED: "not fetched yet", FETCH_ERROR: "a fetch failed"}
    if reasons:
        parts.append("; " + ", ".join(f"{n} {why[k]}" for k, n in reasons.items()))
    return "".join(parts) + "."


# ----------------------------------------------------------------- the morning update ----

MORNING_DONE, MORNING_PARTIAL, MORNING_NOT_YET, MORNING_MISSED, MORNING_NO_GAMES, MORNING_BUDGET_ONLY = "DONE", "PARTIAL", "NOT_YET", "MISSED", "NO_GAMES", "BUDGET_ONLY"
MORNING_GRACE_MIN = 45        # the first look is due at 08:00 ET; the trader runs every 15 minutes, so it should land by ~08:15; 08:45 is already late


def morning_status(now: dt.datetime, day: str | None = None) -> dict:
    """Did today's morning update happen? Judged from the availability record alone (no network): a game counts as LOOKED AT if, at or after 08:00 ET today, the
    provider answered for it (POSTED or NOT_POSTED) or the plan recorded why it was not fetched (BUDGET_BLOCKED). {state, looked, total, ...}."""
    from operational import eastern_time as et
    day = day or et.eastern_today(now)
    return morning_status_from(_load(day)["games"], now, day)


def morning_status_from(games: dict, now: dt.datetime, day: str) -> dict:
    """The same judgement on a games record handed in (the hosted pages hold the published record, not the engine's file)."""
    from operational import capture_schedule as sched, quote_freshness as qf
    start = sched._localize(dt.date.fromisoformat(day), *sched.MORNING_START_ET)
    # a game that starts within SLOT_CLOSE_HOURS of 08:00 has no morning window (its pregame look is the first), so it is not part of "the morning update"
    games = {k: g for k, g in games.items()
             if not g.get("start_utc") or (qf.parse_utc(g["start_utc"]) - dt.timedelta(hours=sched.SLOT_CLOSE_HOURS)) > start}
    out = {"day": day, "starts_at_utc": _stamp(start), "total": len(games), "looked": 0, "posted": 0, "not_posted": 0, "budget_blocked": 0,
           "first_look_utc": None, "last_look_utc": None, "state": MORNING_NO_GAMES}
    if not games:
        return out
    firsts, lasts = [], []
    for g in games.values():
        looks = [(qf.parse_utc(m.get("checked_utc")), m["status"]) for m in g["markets"].values()]
        looks = [(t, s) for t, s in looks if t is not None and t >= start and s in (POSTED, NOT_POSTED, BUDGET_BLOCKED, FETCH_ERROR)]
        if not looks:
            continue
        out["looked"] += 1
        firsts.append(min(t for t, _ in looks))
        lasts.append(max(t for t, _ in looks))
        statuses = {s for _, s in looks}
        if POSTED in statuses:
            out["posted"] += 1
        elif NOT_POSTED in statuses:
            out["not_posted"] += 1
        elif BUDGET_BLOCKED in statuses:
            out["budget_blocked"] += 1
    if firsts:
        out["first_look_utc"], out["last_look_utc"] = _stamp(min(firsts)), _stamp(max(lasts))
    if now < start:
        out["state"] = MORNING_NOT_YET
    elif out["looked"] == out["total"]:
        # "looked at" includes games the plan could not afford; a morning in which DraftKings was never actually asked about any game is not a done morning
        out["state"] = MORNING_DONE if (out["posted"] + out["not_posted"]) else MORNING_BUDGET_ONLY
    elif out["looked"]:
        out["state"] = MORNING_PARTIAL
    else:
        out["state"] = MORNING_MISSED if now >= start + dt.timedelta(minutes=MORNING_GRACE_MIN) else MORNING_NOT_YET
    return out


def observations(day: str, limit: int = 60) -> list[dict]:
    """Every provider answer on file for games of `day` (newest last): when each market was asked about, for which game, how long before puck drop, and whether DraftKings had posted it.
    These are OBSERVATIONS at those times, not a finding about DraftKings' practice."""
    rows = [r for r in history() if r.get("day") == day]
    games = _load(day)["games"]
    out = []
    for r in rows[-limit:]:
        g = games.get(r["game_id"]) or {}
        out.append({"at": r["at"], "game": g.get("matchup") or r["game_id"], "market": r["market"], "status": r["status"], "outcomes": r.get("outcomes", 0),
                    "hours_to_start": r.get("hours_to_start")})
    return out
