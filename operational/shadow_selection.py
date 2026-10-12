"""
Shadow selection log: what the selector WOULD have chosen, recorded while automatic recording is paused (and nothing is staked).

Why. After the 2026-10 losing-streak postmortem the question that matters (is the model overstated on the legs the selector actually picks, the ones where it most disagrees with the price?)
cannot be answered from nine tickets, and there are no historical sportsbook prices to test it on. It can be answered forward: log every priced leg with the model's probability BEFORE the game,
log what each policy variant (operational/selection_variants.py) would pick, and score both against official results later (deploy/score_selection_shadow.py).

What it writes. One JSON line per distinct pool of priced legs to `selection_shadow.jsonl` (append-only, never edited): the time, the code version, every eligible priced leg
([game, player, market, threshold, price, probability, name, team, start, quote time, whether it passed the edge filter]) and each variant's tickets. A pool identical to the previous one is
not written again. It never touches the ledger, never stakes anything and never raises into the trader.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from operational import eastern_time as et
from operational import state_paths

NAME = "selection_shadow.jsonl"
STATE = "selection_shadow_state.json"


POOL_FIELDS = ["game_id", "player_id", "market_family", "threshold", "american_price", "probability", "name", "team", "game_start_utc", "quote_time_utc", "passed_edge_filter",
               "recent_hits", "recent_games", "model_probability", "market_probability", "record_shrunk", "final_estimate", "decision", "in_selected_ticket", "retrieved_at_utc"]


def _compact(l, in_ticket: set) -> list:
    """One priced leg as frozen at the time: the ORIGINAL probability (the model's, or the lower of two sources), the recent hit record, the model's calibrated probability, the market's probability,
    the final estimate a ticket would use and the selector's decision about the leg. Nothing here is ever rewritten after the games."""
    from research.real_market_parlay import anchor
    from research.real_market_parlay import engine as rmp
    final, decision = anchor.leg_decision(l)
    ident = rmp.leg_identity(l)
    return [str(l.game_id), str(l.participant_id), l.market_family, l.threshold, l.american_price, round(l.conservative_probability, 5), l.participant_name, l.team, l.game_start_utc,
            l.quote_updated_utc or l.captured_at_utc, bool(rmp.leg_has_edge(l)), l.empirical_hits, l.empirical_games, l.model_probability, l.market_probability, l.empirical_probability,
            None if final is None else round(final, 5), decision, ident in in_ticket, l.retrieved_at_utc]


def _ticket(c: dict) -> dict:
    return {"legs": [[l["id"], l["label"], l["price"], round(l["p"], 5)] for l in c["legs"]], "hit_probability": round(c["p"], 5), "price": round(c["price"], 1),
            "ev": round(c["ev"], 5), "ev_after_haircut": round(c["ev_haircut"], 5)}


GAPS_NAME = "selection_shadow_gaps.jsonl"
GAP_MIN = 90.0        # minutes without a look, inside the Eastern day's 08:00-23:00, that is written down as a gap (the Mac asleep, a job stopped or the logger down)


def note_gap(from_utc: str, to_utc: str, kind: str, reason: str, *, now: dt.datetime | None = None) -> dict:
    """Append one line to the gap notes (append-only, never edited). A gap is a stretch in which predictions that could have been frozen were NOT: it is stated, not filled in. Nothing is ever
    back-dated or reconstructed into the prediction log."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rec = {"type": "GAP", "from_utc": from_utc, "to_utc": to_utc, "kind": kind, "reason": reason, "noted_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = state_paths.path(GAPS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return rec


def gaps() -> list[dict]:
    path = state_paths.path(GAPS_NAME)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def gaps_on(et_date: str) -> list[dict]:
    """Gap notes that overlap the given Eastern day."""
    from zoneinfo import ZoneInfo
    z = ZoneInfo("America/New_York")
    day = dt.date.fromisoformat(et_date)
    lo = dt.datetime.combine(day, dt.time(0, 0), tzinfo=z).astimezone(dt.timezone.utc)
    hi = lo + dt.timedelta(days=1)
    out = []
    for g in gaps():
        a, b = (dt.datetime.fromisoformat(g[k].replace("Z", "+00:00")) for k in ("from_utc", "to_utc"))
        if a < hi and b > lo:
            out.append(g)
    return out


def _daytime_minutes(a: dt.datetime, b: dt.datetime) -> float:
    """Minutes of [a, b] that fall between 08:00 and 23:00 Eastern."""
    from zoneinfo import ZoneInfo
    z = ZoneInfo("America/New_York")
    total, t = 0.0, a
    while t < b:
        nxt = min(b, t + dt.timedelta(minutes=5))
        h = t.astimezone(z).hour
        if 8 <= h < 23:
            total += (nxt - t).total_seconds() / 60.0
        t = nxt
    return total


def _touch(state_path, now: dt.datetime, **extra) -> None:
    """Stamp the state file with the time of THIS look (even when nothing changed), so the watchdog can tell a quiet pool from a logger that has stopped."""
    try:
        cur = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        cur = {}
    prev = cur.get("checked_at_utc")
    if prev:
        try:
            a = dt.datetime.fromisoformat(prev.replace("Z", "+00:00"))
            b = now.astimezone(dt.timezone.utc)
            if (b - a).total_seconds() / 60.0 > GAP_MIN and _daytime_minutes(a, b) > GAP_MIN:
                note_gap(prev, b.strftime("%Y-%m-%dT%H:%M:%SZ"), "NO_CHECKS", "no prediction-log check for more than 90 minutes in the daytime: the machine was asleep, a job was stopped, or the logger was down. Nothing was frozen in this stretch.", now=now)
        except (ValueError, OverflowError):
            pass
    cur.update(extra)
    cur["checked_at_utc"] = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(cur))
    except OSError:
        pass


def record(legs: list, now: dt.datetime, code_version: str | None = None, proposed: list | None = None, *, recorded: bool = False, rejected: list | None = None,
           selector: dict | None = None) -> dict:
    """Append the pool and the selector's decisions unless nothing changed. `proposed` are the tickets the selector chose this cycle (`recorded` says whether they were staked);
    `rejected` the nearest tickets it refused, with the reason. Returns {"status": "WRITTEN"|"UNCHANGED"|"EMPTY", ...}."""
    from operational import selection_variants as sv
    from research.real_market_parlay import engine as rmp
    eligible = [l for l in legs if rmp.leg_is_eligible(l)]
    state_path = state_paths.path(STATE)
    if not eligible:
        _touch(state_path, now)
        return {"status": "EMPTY"}
    in_ticket = {rmp.leg_identity(l) for c in (proposed or []) for l in c.legs}
    pool = sorted((_compact(l, in_ticket) for l in eligible), key=lambda r: (r[0], r[1], r[2], r[3] or 0))
    tickets_sig = sorted("+".join(sorted(f"{rmp.leg_identity(l)}" for l in c.legs)) for c in (proposed or []))
    key = hashlib.sha256(json.dumps([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in pool] + [tickets_sig, bool(recorded)], separators=(",", ":")).encode()).hexdigest()[:20]
    try:
        last = json.loads(state_path.read_text()).get("pool_key")
    except (OSError, json.JSONDecodeError):
        last = None
    if last == key:
        _touch(state_path, now)
        return {"status": "UNCHANGED", "pool_key": key}
    cands = sv.candidates_from_legs(legs)
    variants = {name: {"label": rule["label"], "tickets": [_ticket(c) for c in sv.select(cands, rule)]} for name, rule in sv.VARIANTS.items()}
    rec = {"at_utc": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "et_date": et.eastern_today(now), "code_version": code_version, "pool_key": key,
           "selector": selector or {}, "model_versions": sorted({l.model_version for l in eligible if l.model_version}),
           "pool_fields": POOL_FIELDS, "pool": pool, "qualifying_tickets": len(cands), "variants": variants,
           "decisions": {"selected_tickets_recorded": bool(recorded), "rejected_tickets": rejected or []},
           "proposed_tickets": [{"legs": [[str(l.game_id), str(l.participant_id), l.market_family, l.threshold, l.american_price, l.conservative_probability, l.participant_name] for l in c.legs],
                                 "hit_probability": round(c.joint_probability, 5), "price": round(c.estimated_combo_price, 1), "ev": round(c.ev_estimated, 5), "recorded": bool(recorded)} for c in (proposed or [])]}
    path = state_paths.path(NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    _touch(state_path, now, pool_key=key, written_at_utc=rec["at_utc"])
    return {"status": "WRITTEN", "pool_key": key, "pool_legs": len(pool), "qualifying_tickets": len(cands)}


def read_all() -> list[dict]:
    path = state_paths.path(NAME)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 6 and sys.argv[1] == "note-gap":
        print(json.dumps(note_gap(sys.argv[2], sys.argv[3], sys.argv[4], " ".join(sys.argv[5:])), indent=1))
    else:
        print(json.dumps({"records": len(read_all()), "gaps": gaps()}, indent=1)[:2000])
