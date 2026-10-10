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


def _compact(l) -> list:
    from research.real_market_parlay import engine as rmp
    return [str(l.game_id), str(l.participant_id), l.market_family, l.threshold, l.american_price, round(l.conservative_probability, 5), l.participant_name, l.team, l.game_start_utc,
            l.quote_updated_utc or l.captured_at_utc, bool(rmp.leg_has_edge(l))]


def _ticket(c: dict) -> dict:
    return {"legs": [[l["id"], l["label"], l["price"], round(l["p"], 5)] for l in c["legs"]], "hit_probability": round(c["p"], 5), "price": round(c["price"], 1),
            "ev": round(c["ev"], 5), "ev_after_haircut": round(c["ev_haircut"], 5)}


def record(legs: list, now: dt.datetime, code_version: str | None = None) -> dict:
    """Append the pool and the variants' picks unless the pool is unchanged. Returns {"status": "WRITTEN"|"UNCHANGED"|"EMPTY", ...}."""
    from operational import selection_variants as sv
    from research.real_market_parlay import engine as rmp
    eligible = [l for l in legs if rmp.leg_is_eligible(l)]
    if not eligible:
        return {"status": "EMPTY"}
    pool = sorted((_compact(l) for l in eligible), key=lambda r: (r[0], r[1], r[2], r[3] or 0))
    key = hashlib.sha256(json.dumps([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in pool], separators=(",", ":")).encode()).hexdigest()[:20]
    state_path = state_paths.path(STATE)
    try:
        last = json.loads(state_path.read_text()).get("pool_key")
    except (OSError, json.JSONDecodeError):
        last = None
    if last == key:
        return {"status": "UNCHANGED", "pool_key": key}
    cands = sv.candidates_from_legs(legs)
    variants = {name: {"label": rule["label"], "tickets": [_ticket(c) for c in sv.select(cands, rule)]} for name, rule in sv.VARIANTS.items()}
    rec = {"at_utc": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "et_date": et.eastern_today(now), "code_version": code_version, "pool_key": key,
           "pool_fields": ["game_id", "player_id", "market_family", "threshold", "american_price", "probability", "name", "team", "game_start_utc", "quote_time_utc", "passed_edge_filter"],
           "pool": pool, "qualifying_tickets": len(cands), "variants": variants}
    path = state_paths.path(NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    state_path.write_text(json.dumps({"pool_key": key, "written_at_utc": rec["at_utc"]}))
    return {"status": "WRITTEN", "pool_key": key, "pool_legs": len(pool), "qualifying_tickets": len(cands)}


def read_all() -> list[dict]:
    path = state_paths.path(NAME)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
