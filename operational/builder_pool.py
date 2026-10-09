"""
The Paper Parlay Builder's price list: every DraftKings player line the engine currently holds for today's games, for a person to pick from.

This is NOT the model's recommendation list. The automatic tickets and Best Options keep their edge and +100 rules; a person building their own paper slip is making their own choice, so every
real, identifiable, unstarted line is offered with its actual price and (where the model has one) its estimated chance, and nothing is filtered by edge. What IS filtered, and why:

  * a price with no provider update time, or one the engine cannot tie to a player on either team's roster, is not offered (it could not be settled);
  * a player the roster sync reports as not ACTIVE is not offered;
  * a goalie-saves line is offered only for a goalie whose start is CONFIRMED (the same safeguard the model uses; there is no automatic source yet, so it is rare);
  * a game that has started is not offered.
Each market carries its own provider quote time. Freshness is judged against that time when a page is opened and again when an order is processed (150 minutes; 100 within two hours of puck
drop), so a stale line can be seen but not added. The engine writes this list every cycle, publishes it, and checks every submitted slip against it (operational/personal_logs.py).
"""
from __future__ import annotations

import datetime as dt
import json
import math

from operational import quote_freshness, state_paths

NAME = "builder_pool.json"
FAMILIES = {"SOG": ("PLAYER_SOG_ALTERNATE", "player_shots_on_goal_alternate", "Shots on goal"),
            "PTS": ("PLAYER_POINTS", "player_points", "Points"),
            "GOAL": ("PLAYER_GOALS", "player_goal_scorer_anytime", "Anytime goal"),
            "SAVES": ("GOALIE_SAVES", "player_total_saves", "Goalie saves")}
CODE_OF_FAMILY = {v[0]: k for k, v in FAMILIES.items()}
PROB_PREFIX = {"SOG": "SOG", "PTS": "PTS", "GOAL": "GOAL"}
MODEL_VERSION = "player-rate-toi-v2"


def _decimal(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def american_of(decimal: float) -> int:
    return round((decimal - 1.0) * 100.0) if decimal >= 2.0 else round(-100.0 / (decimal - 1.0))


def combined(prices: list[float]) -> dict:
    """The product of the legs' decimal prices and its American rounding. The return shown to a person is computed from the ROUNDED American price, which is what the ledger stores."""
    dec = math.prod(_decimal(p) for p in prices)
    am = american_of(dec)
    return {"decimal": dec, "american": am, "return_decimal": _decimal(am)}


# --------------------------------------------------------------------------- build ----

def _outcomes(payload: dict, market_key: str):
    for bm in payload.get("bookmakers") or []:
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets") or []:
            if m.get("key") == market_key:
                yield m.get("last_update") or bm.get("last_update"), m.get("outcomes") or []


def _roster_index(conn, team: str, now_iso: str) -> dict:
    """{normalised name: (player_id, position)} for the players the roster sync currently places on `team`."""
    from features import point_in_time as pit
    from operational import best_bets as bb
    out = {}
    for pid in pit.roster_ids_for_team(conn, team, now_iso):
        r = conn.execute("SELECT full_name, position FROM players WHERE player_id = ?", (pid,)).fetchone()
        if r and r["full_name"]:
            out[bb.norm_name(r["full_name"])] = (str(pid), r["position"])
    return out


def build(nhl_conn, now: dt.datetime) -> dict:
    from features import point_in_time as pit
    from operational import best_bets as bb, goalie_confirmations
    snap = bb.current_model(nhl_conn, now)
    games, model = snap["games"], snap["model"]
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
    events = bb._latest_events_listing()
    doc = {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date_et": snap["date"], "model_version": MODEL_VERSION, "games": {},
           "skipped": {"unmatched_prices": 0, "not_active": 0, "saves_unconfirmed": 0}, "families": {k: v[2] for k, v in FAMILIES.items()}}
    rosters: dict[str, dict] = {}
    for e in events:
        m = bb.match_game(e, games)
        if not m:
            continue
        gid, g = m
        start = bb.effective_start(e["commence_time"], g["start_utc"])
        if start <= now:
            continue
        for team in (g["home"], g["away"]):
            if team not in rosters:
                try:
                    rosters[team] = _roster_index(nhl_conn, team, now_iso)
                except Exception:  # noqa: BLE001 - no roster for a team: its players are simply not offered
                    rosters[team] = {}
        players: dict[str, dict] = {}
        for code, (family, key, _label) in FAMILIES.items():
            cap = bb.latest_capture(e["id"], market=key)
            if cap is None:
                continue
            retrieved, payload = cap
            for quote_updated, outs in _outcomes(payload, key):
                if not quote_updated:
                    continue
                for o in outs:
                    if o.get("price") is None or not o.get("description"):
                        continue
                    if code == "GOAL":
                        if o.get("name") != "Yes":
                            continue
                        k = 1
                    else:
                        if o.get("name") != "Over" or o.get("point") is None:
                            continue
                        k = int(float(o["point"]) + 0.5)
                    if not (1 <= k <= 60):
                        continue
                    nm = bb.norm_name(o["description"])
                    pid = team = pos = None
                    for t in (g["home"], g["away"]):
                        hit = rosters.get(t, {}).get(nm)
                        if hit:
                            pid, pos, team = hit[0], hit[1], t
                            break
                    if pid is None:
                        doc["skipped"]["unmatched_prices"] += 1
                        continue
                    if (code == "SAVES") != (pos == "G"):
                        continue                                             # saves are goalies' lines; every other market is a skater's
                    try:
                        status = pit.roster_status(nhl_conn, pid, now_iso)
                    except Exception:  # noqa: BLE001
                        status = "ACTIVE"
                    if status not in ("ACTIVE", None):
                        doc["skipped"]["not_active"] += 1
                        continue
                    if code == "SAVES":
                        try:
                            conf = goalie_confirmations.lookup(nhl_conn, gid, team, pid, now)
                        except Exception:  # noqa: BLE001
                            conf = None
                        if not conf or conf.get("status") != "CONFIRMED":
                            doc["skipped"]["saves_unconfirmed"] += 1
                            continue
                    entry = model.get(f"{nm}|{team}") or {}
                    prob = (entry.get("probs") or {}).get(f"{PROB_PREFIX[code]}{k}") if code in PROB_PREFIX else None
                    p = players.setdefault(pid, {"n": o["description"], "t": team, "m": {}})
                    mk = p["m"].setdefault(code, {"q": quote_updated, "r": retrieved.strftime("%Y-%m-%dT%H:%M:%SZ"), "l": []})
                    mk["l"].append([k, float(o["price"]), None if prob is None else round(float(prob), 4)])
        for p in players.values():
            for mk in p["m"].values():
                mk["l"].sort()
        if players:
            doc["games"][str(gid)] = {"away": g["away"], "home": g["home"], "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "event_id": e["id"],
                                      "players": dict(sorted(players.items(), key=lambda kv: kv[1]["n"]))}
    return doc


def path():
    return state_paths.path(NAME)


def save(doc: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    tmp.replace(p)


def load() -> dict | None:
    try:
        return json.loads(path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def refresh(nhl_conn, now: dt.datetime) -> dict:
    doc = build(nhl_conn, now)
    save(doc)
    return {"games": len(doc["games"]), "players": sum(len(g["players"]) for g in doc["games"].values()),
            "lines": sum(len(mk["l"]) for g in doc["games"].values() for p in g["players"].values() for mk in p["m"].values()), "skipped": doc["skipped"]}


# ----------------------------------------------------------------------- lookups ----

def find_line(pool: dict, game_id, participant_id, family: str, threshold) -> dict | None:
    """The current line for one leg: {price, prob, quote_updated, retrieved, start_utc, away, home, name, team} or None."""
    code = CODE_OF_FAMILY.get(family)
    g = (pool.get("games") or {}).get(str(game_id))
    if not g or code is None:
        return None
    p = g["players"].get(str(participant_id))
    mk = p and p["m"].get(code)
    if not mk:
        return None
    for k, price, prob in mk["l"]:
        if threshold is not None and int(k) == int(threshold):
            return {"price": price, "prob": prob, "quote_updated": mk["q"], "retrieved": mk["r"], "start_utc": g["start_utc"], "away": g["away"], "home": g["home"],
                    "name": p["n"], "team": p["t"], "event_id": g.get("event_id")}
    return None


def freshness(line: dict, now: dt.datetime) -> dict:
    """quote_freshness.assess against the limit that applies at this distance from puck drop (150 min; 100 within two hours)."""
    from operational import best_bets as bb
    start = quote_freshness.parse_utc(line["start_utc"])
    hours = (start - now).total_seconds() / 3600.0 if start else 0.0
    a = quote_freshness.assess(line["quote_updated"], line["retrieved"], now, bb.price_age_limit_min(hours))
    a["started"] = hours <= 0
    a["hours_to_start"] = round(hours, 2)
    a["usable"] = bool(a["fresh"]) and hours > 0
    return a


def leg_label(name: str, family: str, threshold) -> str:
    code = CODE_OF_FAMILY.get(family)
    if code is None:
        return f"{name} ({str(family)[:30]})"
    word = {"SOG": "shots on goal", "PTS": "point" if int(threshold) == 1 else "points", "GOAL": "anytime goal", "SAVES": "saves"}[code]
    return f"{name} anytime goal" if code == "GOAL" else f"{name} {int(threshold)}+ {word}"


def revalidate(accepted: dict, pool: dict, now: dt.datetime) -> dict:
    """Checks every leg of a submitted slip against the current price list. status: OK, CHANGED (a price moved; nothing is recorded and the new prices come back) or UNAVAILABLE
    (a leg has no current price, its price is stale, or its game has started). `legs` are the legs as the ledger will freeze them (the CURRENT prices)."""
    legs, changes, missing = [], [], []
    for a in accepted["legs"]:
        line = find_line(pool, a["game_id"], a["participant_id"], a["market_family"], a["threshold"])
        label = leg_label(a.get("participant_name") or a["participant_id"], a["market_family"], a["threshold"])
        if line is None:
            missing.append(f"{label}: no current DraftKings price on file")
            continue
        fr = freshness(line, now)
        if fr["started"]:
            missing.append(f"{label}: the game has started")
            continue
        if not fr["usable"]:
            missing.append(f"{label}: the price is {fr['status'].replace('_', ' ').lower()} ({fr['quote_age_min']} min old)")
            continue
        if abs(float(a["american_price"]) - line["price"]) > 1e-9:
            changes.append({"leg": label, "was": a["american_price"], "now": line["price"]})
        legs.append({"game_id": str(a["game_id"]), "event_id": line.get("event_id"), "market_family": a["market_family"], "participant_id": str(a["participant_id"]),
                     "participant_name": line["name"], "side": a["side"], "threshold": int(a["threshold"]), "american_price": line["price"],
                     "decimal_price": round(_decimal(line["price"]), 4), "sportsbook": "draftkings", "captured_at_utc": line["retrieved"], "retrieved_at_utc": line["retrieved"],
                     "quote_updated_utc": line["quote_updated"], "quote_age_min_at_entry": fr["quote_age_min"], "freshness_status": fr["status"], "team": line["team"],
                     "opponent": line["home"] if line["team"] == line["away"] else line["away"], "game_start_utc": line["start_utc"], "provider_start_utc": line["start_utc"],
                     "conservative_probability": line["prob"], "model_version": MODEL_VERSION if line["prob"] is not None else "none (the person's own selection)"})
    if missing:
        return {"status": "UNAVAILABLE", "legs": legs, "changes": changes, "reason": "UNAVAILABLE: " + "; ".join(missing) + ". Nothing was recorded.", "combined_american": None}
    c = combined([l["american_price"] for l in legs])
    return {"status": "CHANGED" if changes else "OK", "legs": legs, "changes": changes, "reason": None, "combined_american": c["american"]}
