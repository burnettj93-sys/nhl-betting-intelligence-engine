"""Eggy's pick record: the daily decision (a PICK or a PASS) locked once per Eastern day, every game OBSERVED at decision time (both markets, with the signals), the results, the decision-quality grade (independent of
the result), and the signal trust that accumulates prospectively. Its own append-only file; nothing here touches the learning-parlay ledger.

  eggy_pick_ledger.jsonl   events: DECISION (immutable per date), OBSERVATION (one per game at decision time), OUTCOME (final score for an observation, closing proxy for a pick)
  eggy_pick_postmortem.json  the derived record: pairings of decision quality with result, signals, the pass audit
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import Counter, defaultdict

from operational import eastern_time as et
from operational import eggy_intelligence as ei
from operational import eggy_pick as ep
from operational import state_paths

LEDGER = "eggy_pick_ledger.jsonl"
POSTMORTEM = "eggy_pick_postmortem.json"
MIN_FOR_DECISION = 100         # a signal drives a decision only after this many independent games (the moneyline record's own bar)
MIN_FOR_DISPLAY = 30
Z = ei.Z_SIGNIFICANT


def _path(name):
    return state_paths.path(name)


def read() -> list[dict]:
    p = _path(LEDGER)
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _append(events: list[dict]) -> None:
    p = _path(LEDGER)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        for e in events:
            fh.write(json.dumps(e, default=str, sort_keys=True) + "\n")


# ------------------------------------------------------------------------------------------------ observations (one per game)
def observation(c_by_market: dict, game: dict, decided_at: str) -> dict:
    """One game as Eggy saw it: both markets, both sides, the signals. `c_by_market`: {(side, market): candidate}."""
    home, away = game["home"], game["away"]
    g = lambda side, mk, k: (c_by_market.get((side, mk)) or {}).get(k)
    return {"type": "OBSERVATION", "date": game["date"], "game_id": str(game["game_id"]), "matchup": f"{away} @ {home}", "home": home, "away": away, "start_utc": game.get("start_utc"), "decided_at_utc": decided_at,
            "ml": {side: {"price": g(side, "MONEYLINE", "price"), "market": g(side, "MONEYLINE", "market_probability_market"), "model": g(side, "MONEYLINE", "model_probability"),
                          "moved": g(side, "MONEYLINE", "movement_toward_side"), "trust": g(side, "MONEYLINE", "trust_state")} for side in (home, away)},
            "pl": {side: {"line": g(side, "PUCK_LINE", "line"), "price": g(side, "PUCK_LINE", "price"), "market": g(side, "PUCK_LINE", "market_probability_market"), "model": g(side, "PUCK_LINE", "model_probability")} for side in (home, away)},
            "rest_days": g(home, "MONEYLINE", "rest_days"), "freshness": g(home, "MONEYLINE", "price_freshness") and g(home, "MONEYLINE", "price_freshness")["status"]}


def settle(nhl_conn, now: dt.datetime) -> dict:
    """Attach final scores to observations and the result to the pick, once their games are final."""
    events = read()
    done_obs = {e["game_id"] + e["date"] for e in events if e["type"] == "OUTCOME" and e.get("kind") == "OBSERVATION"}
    done_pick = {e["date"] for e in events if e["type"] == "OUTCOME" and e.get("kind") == "PICK"}
    new = []
    for e in events:
        if e["type"] not in ("OBSERVATION", "DECISION"):
            continue
        gid = e["game_id"] if e["type"] == "OBSERVATION" else (((e.get("decision_doc") or {}).get("pick") or {}).get("game_id"))
        if gid is None:
            continue
        if e["type"] == "OBSERVATION" and e["game_id"] + e["date"] in done_obs:
            continue
        if e["type"] == "DECISION" and e["date"] in done_pick:
            continue
        r = nhl_conn.execute("SELECT home_team, away_team, home_score, away_score, game_state, final_period_type FROM games WHERE game_id = ?", (int(gid),)).fetchone()
        if not r or r["game_state"] != "FINAL" or r["home_score"] is None or r["away_score"] is None:
            continue
        margin = int(r["home_score"]) - int(r["away_score"])
        if e["type"] == "OBSERVATION":
            new.append({"type": "OUTCOME", "kind": "OBSERVATION", "date": e["date"], "game_id": gid, "home_score": r["home_score"], "away_score": r["away_score"], "home_margin": margin, "period_type": r["final_period_type"],
                        "settled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
        else:
            pick = e["decision_doc"]["pick"]
            side_margin = margin if pick["side"] == r["home_team"] else -margin
            if pick["market"] == "MONEYLINE":
                res = "WIN" if side_margin > 0 else "LOSS"
            else:
                res = "WIN" if (side_margin >= 2 if pick["line"] < 0 else side_margin > -2) else "LOSS"
            new.append({"type": "OUTCOME", "kind": "PICK", "date": e["date"], "game_id": gid, "result": res, "side_margin": side_margin, "home_score": r["home_score"], "away_score": r["away_score"],
                        "settled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    if new:
        _append(new)
    return {"settled": len(new)}


# ------------------------------------------------------------------------------------------------ decision quality, independent of the result
def grade(decision_doc: dict) -> dict:
    """GOOD / QUESTIONABLE / BAD for a PICK, from what was known when it was made (the stored decision): never the result. BAD: a defect the standard should have prevented. QUESTIONABLE: a doubt the evidence supports.
    A PASS is not graded as a bet (it carries a pass audit instead)."""
    if decision_doc.get("decision") != "PICK":
        return {"label": "NO_BET", "reasons": ["a PASS places no bet"], "criteria": []}
    p = decision_doc["pick"]
    bad, doubt = [], []
    if p["price_freshness"]["status"] != "FRESH":
        bad.append(("STALE_PRICE", "the price was not FRESH at decision"))
    if p["trust_state"] != "DISAGREEMENT_HAS_SIGNAL" and p["supported_probability"] != p["market_probability_market"]:
        bad.append(("UNPROVEN_EDGE_CLAIMED", "a model probability was used as the edge without a proven disagreement"))
    need = ep.required_edge(p["price_class"])
    if need is None or p["edge"] < need:
        bad.append(("EDGE_OR_PRICE_POLICY", "the edge or the price was outside the policy"))
    if (p.get("data_completeness") or 0) < 0.5 or p.get("calibration") in ("INSUFFICIENT", None):
        bad.append(("DATA_OR_CALIBRATION", "data completeness or calibration was inadequate"))
    if not p["balance"]["materially_outweighs"] or p["balance"]["against_strong"]:
        bad.append(("CASE_NOT_BALANCED", "the case against was not outweighed"))
    if bad:
        return {"label": "BAD_DECISION", "reasons": [t for _, t in bad], "criteria": [c for c, _ in bad]}
    if p["balance"]["against_moderate"]:
        doubt.append(("MODERATE_AGAINST", f"{p['balance']['against_moderate']} moderate reason(s) against stood"))
    if p["price_class"] == "SHOULDER":
        doubt.append(("SHOULDER_PRICE", "the price was outside the preferred region"))
    if p.get("calibration") == "MEDIUM":
        doubt.append(("CALIBRATION_MEDIUM", "calibration was MEDIUM, not HIGH"))
    if p.get("confidence") == "LOW":
        doubt.append(("LOW_CONFIDENCE", "the evidence category was LOW"))
    if doubt:
        return {"label": "QUESTIONABLE_DECISION", "reasons": [t for _, t in doubt], "criteria": [c for c, _ in doubt]}
    return {"label": "GOOD_DECISION", "reasons": ["a proven disagreement at a fresh price in the preferred region, adequate data and calibration, the case for outweighing the case against"], "criteria": []}


# ------------------------------------------------------------------------------------------------ prospective signal trust
def _settled_observations(events: list[dict]) -> list[dict]:
    out = {e["game_id"] + e["date"]: e for e in events if e["type"] == "OUTCOME" and e.get("kind") == "OBSERVATION"}
    res = []
    for e in events:
        if e["type"] == "OBSERVATION" and e["game_id"] + e["date"] in out:
            res.append({**e, **{k: out[e["game_id"] + e["date"]][k] for k in ("home_score", "away_score", "home_margin")}})
    return res


SIGNALS = {
    "ML_MODEL_GAP_3PLUS": "the moneyline side the hockey model rates 3+ points above the market",
    "PL_MODEL_GAP_3PLUS": "the puck-line side the model's goal-margin mapping rates 3+ points above the market",
    "ML_MOVED_TOWARD_1PT": "the moneyline side the market moved toward by 1+ point since the day's first capture",
    "ML_MOVED_AWAY_1PT": "the moneyline side the market moved away from by 1+ point since the day's first capture",
    "REST_ADVANTAGE": "the side that is rested when its opponent is on a back-to-back",
}


def signal_bets(o: dict):
    """(signal, expected probability, 1/0 outcome) for every oriented side a signal flags in one settled observation: ONE game gives each signal at most one flagged side per market."""
    home, away, m = o["home"], o["away"], o["home_margin"]
    won = {home: 1 if m > 0 else 0, away: 1 if m < 0 else 0}
    sm = {home: m, away: -m}
    out = []
    for side in (home, away):
        ml, pl = o["ml"].get(side) or {}, o["pl"].get(side) or {}
        if ml.get("market") is not None and ml.get("model") is not None:
            if ml["model"] - ml["market"] >= ei.EDGE_MIN:
                out.append(("ML_MODEL_GAP_3PLUS", ml["market"], won[side]))
            if (ml.get("moved") or 0) >= 0.01:
                out.append(("ML_MOVED_TOWARD_1PT", ml["market"], won[side]))
            if (ml.get("moved") or 0) <= -0.01:
                out.append(("ML_MOVED_AWAY_1PT", ml["market"], won[side]))
        if pl.get("market") is not None and pl.get("model") is not None and pl["model"] - pl["market"] >= ei.EDGE_MIN and pl.get("line") is not None:
            out.append(("PL_MODEL_GAP_3PLUS", pl["market"], 1 if (sm[side] >= 2 if pl["line"] < 0 else sm[side] > -2) else 0))
    rd = o.get("rest_days") or {}
    for side, opp in ((home, away), (away, home)):
        if rd.get(opp) is not None and rd.get(side) is not None and rd[opp] <= 1 < rd[side] and (o["ml"].get(side) or {}).get("market") is not None:
            out.append(("REST_ADVANTAGE", o["ml"][side]["market"], won[side]))
    return out


def signal_trust(events: list[dict] | None = None) -> dict:
    events = events if events is not None else read()
    acc = defaultdict(lambda: [0, 0.0, 0.0, 0.0])                      # n, wins, sum p, sum p(1-p)
    for o in _settled_observations(events):
        for name, p, y in signal_bets(o):
            a = acc[name]
            a[0] += 1; a[1] += y; a[2] += p; a[3] += p * (1 - p)
    out = {}
    for name, text in SIGNALS.items():
        n, w, ep_, var = acc[name]
        z = None if n == 0 or var <= 0 else round((w - ep_) / math.sqrt(var), 2)
        if n < MIN_FOR_DISPLAY:
            state = "ACCUMULATING"
        elif z is not None and z >= Z:
            state = "HAS_SIGNAL"
        elif z is not None and z <= -Z:
            state = "FAILS"
        else:
            state = "NO_EVIDENCE"
        decision_state = "INSUFFICIENT_EVIDENCE" if n < MIN_FOR_DECISION else ("DISAGREEMENT_HAS_SIGNAL" if z is not None and z >= Z else ("DISAGREEMENT_IS_OVERCONFIDENCE" if z is not None and z <= -Z else "INSUFFICIENT_EVIDENCE"))
        out[name] = {"describes": text, "games": n, "wins": int(w), "expected_wins_at_the_market_price": round(ep_, 2), "z": z, "state": state, "decision_state": decision_state, "games_needed_to_drive_a_decision": MIN_FOR_DECISION}
    return out


def puck_line_trust(events: list[dict] | None = None) -> dict:
    s = signal_trust(events).get("PL_MODEL_GAP_3PLUS") or {}
    return {"state": s.get("decision_state", "INSUFFICIENT_EVIDENCE"), "games": s.get("games", 0)}


# ------------------------------------------------------------------------------------------------ the record
def record(events: list[dict] | None = None) -> dict:
    events = events if events is not None else read()
    outs = {e["date"]: e for e in events if e["type"] == "OUTCOME" and e.get("kind") == "PICK"}
    days, grid = [], Counter()
    for e in events:
        if e["type"] != "DECISION":
            continue
        d = e["decision_doc"]
        g = grade(d)
        o = outs.get(e["date"])
        row = {"date": e["date"], "decision": d["decision"], "decided_at_utc": d["decided_at_utc"]}
        if d["decision"] == "PICK":
            p = d["pick"]
            row.update(pick={k: p[k] for k in ("matchup", "side", "market", "line", "price", "model_probability", "market_probability_market", "gap", "trust_state", "edge", "confidence", "price_class")}, grade=g,
                       result=(o or {}).get("result", "PENDING"), final=None if not o else f"{o['home_score']}-{o['away_score']}")
            if o:
                grid[(g["label"], o["result"])] += 1
        else:
            row.update(pass_reason=d.get("pass_reason"), closest=(d.get("ranked") or [None])[0])
        days.append(row)
    picks = [r for r in days if r["decision"] == "PICK"]
    settled = [r for r in picks if r["result"] in ("WIN", "LOSS")]
    return {"days": days[-30:], "picks": len(picks), "passes": sum(1 for r in days if r["decision"] == "PASS"), "settled": len(settled), "wins": sum(1 for r in settled if r["result"] == "WIN"),
            "losses": sum(1 for r in settled if r["result"] == "LOSS"), "decision_quality_vs_result": [{"decision": k[0], "result": k[1], "n": v} for k, v in sorted(grid.items())],
            "note": "hit or miss only: no stake, return or ROI is claimed for the side pick either (no closing prices or bankroll are modelled)"}


def snapshot_section(today: str | None = None) -> dict:
    events = read()
    today = today or et.eastern_today(dt.datetime.now(dt.timezone.utc))
    cur = next((e for e in reversed(events) if e["type"] == "DECISION" and e["date"] == today), None)
    sig = signal_trust(events)
    return {"policy": {k: ep.POLICY[k] for k in ("policy_id", "markets", "price_policy", "max_outputs_per_day")} | {"digest": ep.policy_digest()}, "date_et": today, "today": None if not cur else cur["decision_doc"],
            "record": record(events), "signals": sig, "observed_games": sum(1 for e in events if e["type"] == "OBSERVATION"), "settled_games": len(_settled_observations(events)), "availability": ep.AVAILABILITY}


# ------------------------------------------------------------------------------------------------ the cycle
def run_cycle(nhl_conn, now: dt.datetime) -> dict:
    """One trader cycle: settle what is final, and (once a day, when the pregame pull is in and before the first game) make and lock the day's decision."""
    from operational import learning_cohort as lc
    out = {"settlement": settle(nhl_conn, now)}
    today = et.eastern_today(now)
    events = read()
    if any(e["type"] == "DECISION" and e["date"] == today for e in events):
        out["status"] = "DECIDED"
        _write_postmortem(events)
        return out
    rows = nhl_conn.execute("SELECT game_id, home_team, away_team, scheduled_start_utc FROM games WHERE game_date = ? ORDER BY scheduled_start_utc", (today,)).fetchall()
    games = []
    for r in rows:
        if not r["scheduled_start_utc"]:
            continue
        t = dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        games.append({"game_id": r["game_id"], "home": r["home_team"], "away": r["away_team"], "start_utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "start": t, "date": today})
    if not games:
        out["status"] = "NO_GAMES"
        return out
    cap = lc._capture_context(nhl_conn, today, now)
    first_start = cap.get("first_start")
    captures = ep.league_captures(today, now)
    from operational import capture_timeline as ct
    target = None if first_start is None else first_start - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN)
    ready = bool(captures and target is not None and captures[-1]["at"] >= target - dt.timedelta(minutes=10))
    last_chance = None if first_start is None else first_start - dt.timedelta(minutes=ct.LAST_CHANCE_MIN)
    if not ready and (last_chance is None or now < last_chance):
        out["status"] = "WAITING_FOR_THE_PREGAME_PULL"
        return out
    upcoming = [g for g in games if g["start"] > now]
    if not ready or not upcoming:
        doc = ep.decide([], now=now, why_no_prices="no current league pull (moneyline + puck line) was observed before the first game: Eggy does not decide on prices it cannot call current" if not ready else "every game has started")
        _append([{"type": "DECISION", "date": today, "decision_doc": doc}])
        out["status"] = "PASS_NO_PRICES"
        _write_postmortem(read())
        return out
    ctx = ei.build_context(nhl_conn, now, live_overlay=True)
    ok, why = ei.usable(ctx)
    if not ok:
        out["status"] = "INTELLIGENCE_UNAVAILABLE"
        out["reason"] = why
        return out
    trust_pl = puck_line_trust(events)
    cands, by_game = [], defaultdict(dict)
    for g in upcoming:
        for c in ep.evaluate_game(ctx, g, captures, now, cap, trust_pl):
            cands.append(c)
            by_game[g["game_id"]][(c["side"], c["market"])] = c
    doc = ep.decide(cands, now=now)
    obs = [observation(by_game[g["game_id"]], g, doc["decided_at_utc"]) for g in upcoming if by_game.get(g["game_id"])]
    _append([{"type": "DECISION", "date": today, "game_id": None, "decision_doc": json.loads(json.dumps(doc, default=str))}] + obs)
    out["status"] = doc["decision"]
    _write_postmortem(read())
    return out


def _write_postmortem(events: list[dict]) -> None:
    try:
        p = _path(POSTMORTEM)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "record": record(events), "signals": signal_trust(events)}, indent=1, default=str))
    except OSError:
        pass


def postmortem_refresh(nhl_conn, now: dt.datetime) -> dict:
    """The daily postmortem step (operational/daily_postmortem.py): settle everything final and rebuild Eggy's own postmortem document (pairings, pass audit, signal trust)."""
    out = settle(nhl_conn, now)
    _write_postmortem(read())
    return out
