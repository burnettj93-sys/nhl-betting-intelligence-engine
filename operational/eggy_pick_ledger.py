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

from operational import cloud_snapshot_schema as schema
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
def observation(cands: list[dict], game: dict, decided_at: str, structure_state: dict | None = None) -> dict:
    """One game as Eggy saw it: every candidate side in both markets, with the facts the evidence structures and signals are measured from."""
    sides = []
    for c in cands:
        mv = c.get("movement") or {}
        sides.append({"side": c["side"], "market": c["market"], "line": c["line"], "price": c["price"], "market_p": c["market_probability_market"], "model_p": c["model_probability"], "gap": c["gap"],
                      "supported_p": c["supported_probability"], "edge": c["edge"], "conservative_edge": c["conservative_edge"], "structure": c["structure"], "qualifies": c["qualifies"], "price_class": c["price_class"],
                      "confidence": ep.confidence(c, structure_state) if c["qualifies"] else None, "moved": mv.get("change"), "move_class": mv.get("class"), "move_percentile": mv.get("percentile"), "edge_family_names": [e["family"] for e in c["edge_families"]["present"]], "rest_advantage": any(i["kind"] == "REST_ADVANTAGE" for i in c["case_for"]),
                      "trust_state": c["trust_state"], "calibration": c.get("calibration"), "consensus": c["edge_thesis"]["consensus_quality"], "edge_families": c["edge_thesis"]["families"]})
    gv = next((c.get("goalies") for c in cands if c.get("goalies")), None) or {}
    return {"type": "OBSERVATION", "date": game["date"], "game_id": str(game["game_id"]), "matchup": f"{game['away']} @ {game['home']}", "home": game["home"], "away": game["away"], "start_utc": game.get("start_utc"),
            "decided_at_utc": decided_at, "goalie_state": gv.get("state") or "UNKNOWN_GOALIE", "late_goalie_change": bool(gv.get("late_change")),
            "goalies": {k: {x: v.get(x) for x in ("goalie", "status", "freshness", "source", "source_at_utc", "conflict")} for k, v in (gv.get("sides") or {}).items()},
            "personnel": _personnel_record(cands), "sides": sides}


_PERSON_FIELDS = ("player_id", "name", "position", "status", "freshness", "impact", "designation", "expected_return", "source", "source_url", "authority", "source_at_utc", "recorded_at_utc", "previous_status", "new_today", "conflict", "statement", "origin", "phase", "time_precision")


def _personnel_record(cands: list[dict]) -> dict | None:
    """Exactly what Eggy knew about player availability at decision time, per team: coverage, the sweep time, every decision-relevant player with source and times, the returns and the conflicts."""
    c = next((x for x in cands if x.get("personnel")), None)
    if c is None:
        return None
    from operational import personnel as pers
    try:
        out = {"_research_runs": pers.research_runs(pers.read_reports(), dt.datetime.now(dt.timezone.utc))}
    except Exception:                                                      # noqa: BLE001
        out = {"_research_runs": []}
    for v in (c["personnel"]["side"], c["personnel"]["opponent"]):
        if v:
            out[v["team"]] = {"coverage": v["coverage"], "swept_at_utc": v["swept_at_utc"], "relevant": [{k: p.get(k) for k in _PERSON_FIELDS} for p in v["relevant"]],
                              "returns": [{k: p.get(k) for k in _PERSON_FIELDS} for p in v["material_returns"]], "conflicts": [p["name"] for p in v["conflicts"]]}
    return out


def late_price(pick: dict) -> dict | None:
    """The latest price the engine captured for the pick's side before the game started (a late / closing PROXY: the true closing line is not available), and whether it moved for or against the bet since the decision."""
    try:
        start = dt.datetime.fromisoformat(pick["start_utc"].replace("Z", "+00:00"))
        caps = ep.league_captures(et.eastern_today(start), start)
        for c in reversed(caps):
            e = next((e for e in c["events"].values() if e["home"] == pick["home"] and e["away"] == pick["away"]), None)
            if not e:
                continue
            if pick["market"] == "MONEYLINE":
                q = e["ml"].get(pick["side"])
            else:
                pl = e["pl"].get(pick["side"]) or (None, None)
                q = pl[0] if pl[1] == pick["line"] else None
            if q is None:
                continue
            imp0, imp1 = ei.implied_from_american(pick["price"]), ei.implied_from_american(q)
            return {"price": q, "captured_at_utc": c["at"].strftime("%Y-%m-%dT%H:%M:%SZ"), "moved": "IN_FAVOUR" if imp1 > imp0 + 0.005 else ("AGAINST" if imp1 < imp0 - 0.005 else "FLAT"),
                    "note": "the latest captured price before the game, not the true closing line; IN_FAVOUR means the market moved toward the side after the decision"}
    except Exception:                                                  # a late price is a nicety for the postmortem, never a precondition
        return None
    return None


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
                        "late_price": late_price(pick), "settled_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    if new:
        _append(new)
    return {"settled": len(new)}


# ------------------------------------------------------------------------------------------------ decision quality, independent of the result
def grade(decision_doc: dict) -> dict:
    """GOOD / QUESTIONABLE / BAD for a PICK, from what was known when it was made (the stored decision): never the result. BAD: a defect the standard should have prevented. QUESTIONABLE: a doubt the evidence supports.
    A best-available bet with no edge thesis is acceptable when it is honestly labelled LOW and nothing else is wrong. A PASS places no bet (it carries its reason)."""
    if decision_doc.get("decision") != "PICK":
        return {"label": "NO_BET", "reasons": ["no bet was placed: " + str(decision_doc.get("pass_kind") or "PASS")], "criteria": []}
    p = decision_doc["pick"]
    bad, doubt = [], []
    if p["price_freshness"]["status"] != "FRESH":
        bad.append(("STALE_PRICE", "the price was not FRESH at decision"))
    if p.get("structure") == "SUPPORTED_MARKET_GAP" and p["trust_state"] != "DISAGREEMENT_HAS_SIGNAL":
        bad.append(("UNPROVEN_GAP_CLAIMED", "a supported market gap was claimed without a proven disagreement"))
    if p.get("confidence") == "HIGH" and not any(e["family"] == "PROVEN_SIGNAL" for e in (p.get("edge_families") or {}).get("present", [])):
        bad.append(("CONFIDENCE_OVERSTATED", "HIGH confidence without a proven signal"))
    if p.get("price_class") == "EXTREME" and p.get("confidence") != "LOW":
        bad.append(("EXTREME_PRICE_CONFIDENCE", "an extreme price carried more than LOW confidence"))
    if p["balance"]["against_strong"] and p.get("confidence") != "LOW":
        bad.append(("STRONG_AGAINST_IGNORED", "a STRONG reason against stood while confidence was above LOW"))
    if bad:
        return {"label": "BAD_DECISION", "reasons": [t for _, t in bad], "criteria": [c for c, _ in bad]}
    if p["balance"]["against_strong"]:
        doubt.append(("STRONG_AGAINST", "a STRONG reason against stood (confidence LOW)"))
    if p["balance"]["against_moderate"]:
        doubt.append(("MODERATE_AGAINST", f"{p['balance']['against_moderate']} moderate reason(s) against stood"))
    if p.get("price_class") in ("OUTSIDE_PREFERRED", "EXTREME"):
        doubt.append(("OUTSIDE_PREFERRED_PRICE", "the price was outside the preferred region"))
    if p.get("calibration") in ("MEDIUM", "LOW", "INSUFFICIENT", None) or (p.get("data_completeness") or 0) < 0.6:
        doubt.append(("CALIBRATION_OR_DATA", "calibration was below HIGH or the data was incomplete"))
    if p.get("pick_basis") == "BEST_AVAILABLE" and p.get("confidence") != "LOW":
        doubt.append(("BEST_AVAILABLE_NOT_LOW", "a best-available bet with no edge thesis was not labelled LOW"))
    if doubt:
        return {"label": "QUESTIONABLE_DECISION", "reasons": [t for _, t in doubt], "criteria": [c for c, _ in doubt]}
    return {"label": "GOOD_DECISION", "reasons": ["a fresh price, a recognised edge thesis or an honestly labelled best-available bet, adequate data and calibration, the case for outweighing the case against"], "criteria": []}


# ------------------------------------------------------------------------------------------------ prospective signal trust
def _settled_observations(events: list[dict]) -> list[dict]:
    out = {e["game_id"] + e["date"]: e for e in events if e["type"] == "OUTCOME" and e.get("kind") == "OBSERVATION"}
    res = []
    for e in events:
        if e["type"] == "OBSERVATION" and e["game_id"] + e["date"] in out:
            res.append({**e, **{k: out[e["game_id"] + e["date"]][k] for k in ("home_score", "away_score", "home_margin")}})
    return res


SIGNALS = {
    "SUPPORTED_MARKET_GAP": "evidence structure: a model-market gap the record has proven (Type B)",
    "CONSENSUS_QUALITY": "consensus-quality state (market and hockey agree on a strong near-even side); never a pick on its own",
    "MARKET_SITUATIONAL_OPPORTUNITY": "evidence structure: sound hockey picture plus measured market / situational facts (Type C)",
    "ML_MODEL_GAP_3PLUS": "the moneyline side the hockey model rates 3+ points above the market",
    "PL_MODEL_GAP_3PLUS": "the puck-line side the model's goal-margin mapping rates 3+ points above the market",
    "MOVE_TOWARD_NOTABLE": "the moneyline side the price moved toward by a NOTABLE or EXCEPTIONAL amount (upper quartile of ordinary same-day moves or more)",
    "MOVE_AWAY_NOTABLE": "the moneyline side the price moved away from by a NOTABLE or EXCEPTIONAL amount",
    "FAMILY_PROVEN_SIGNAL": "sides with a proven model-market signal present (evidence family)",
    "FAMILY_MARKET_PRICE": "sides with supported market movement present (evidence family)",
    "FAMILY_SITUATIONAL": "sides with a situational advantage present (evidence family)",
    "PICK_MONEYLINE": "daily bets that were moneylines",
    "PICK_PUCK_LINE": "daily bets that were puck lines",
    "PICK_IN_PREFERRED_PRICE": "daily bets priced inside the preferred near-even region",
    "PICK_OUTSIDE_PREFERRED_PRICE": "daily bets priced outside the preferred region",
    "REST_ADVANTAGE": "the side that is rested when its opponent is on a back-to-back",
    "PRICE_IN_PREFERRED": "a qualifying side priced inside the preferred near-even region",
    "PRICE_OUTSIDE_PREFERRED": "a qualifying side priced outside the preferred region",
    "QUALIFYING_MONEYLINE": "qualifying moneyline sides",
    "QUALIFYING_PUCK_LINE": "qualifying puck-line sides",
    "CONFIDENCE_HIGH": "qualifying sides graded HIGH confidence",
    "CONFIDENCE_MEDIUM": "qualifying sides graded MEDIUM confidence",
    "CONFIDENCE_LOW": "qualifying sides graded LOW confidence",
    "EGGY_PICK": "Eggy's daily bets (the whole record of the product)",
    "PICK_EDGE_THESIS": "daily bets that carried an edge thesis",
    "PICK_BEST_AVAILABLE": "daily bets chosen as the best available with no edge thesis (LOW confidence)",
    "PICK_GOALIES_BOTH_CONFIRMED": "daily bets where both starting goalies were CONFIRMED at decision time",
    "PICK_GOALIES_ONE_PROBABLE": "daily bets where a starting goalie was only PROBABLE (the other confirmed or probable) at decision time",
    "PICK_GOALIES_UNKNOWN": "daily bets where a starting goalie was UNKNOWN (no listing, or a stale one) at decision time",
    "PICK_OWN_MATERIAL_ABSENCE": "daily bets where the picked team was missing a MATERIAL player (fresh attributable report) at decision time",
    "PICK_OPPONENT_MATERIAL_ABSENCE": "daily bets where the opponent was missing a MATERIAL player (fresh attributable report) at decision time",
    "PICK_MATERIAL_PLAYER_IN_DOUBT": "daily bets where a MATERIAL player on either team was questionable / day-to-day / a game-time decision at decision time",
    "PICK_UNRESOLVED_GAME_TIME_DECISION": "daily bets where a MATERIAL player on either team was still a game-time decision when the pick was made",
    "PICK_MATERIAL_PLAYER_RETURN": "daily bets where a MATERIAL player had just returned from an absence on either team at decision time",
    "PICK_INJURY_SOURCE_CONFLICT": "daily bets where injury sources disagreed about a MATERIAL player at decision time",
    "PICK_NO_MATERIAL_PERSONNEL_ISSUE": "daily bets with injury coverage on record and no material absence, doubt or conflict at decision time",
    "PICK_INJURY_COVERAGE_NONE": "daily bets where no attributable injury report existed for either team at decision time (absences unknown)",
    "PICK_EDGE_THESIS_CLEAN_PERSONNEL": "edge-thesis bets with no goalie or material injury uncertainty at decision time",
    "PICK_EDGE_THESIS_PERSONNEL_ISSUE": "edge-thesis bets with a goalie or material injury uncertainty at decision time",
    "PICK_BEST_AVAILABLE_CLEAN_PERSONNEL": "best-available bets with no goalie or material injury uncertainty at decision time",
    "PICK_BEST_AVAILABLE_PERSONNEL_ISSUE": "best-available bets with a goalie or material injury uncertainty at decision time",
    "PICK_GOALIES_SOURCE_CONFLICT": "daily bets where the goalie sources disagreed at decision time",
    "PICK_GOALIE_LATE_CHANGE": "daily bets in games where a listed starter changed after it was first seen",
}


def signal_bets(o: dict):
    """(signal, expected probability at the market price, 1/0 outcome) for every side a signal flags in one settled observation."""
    home, away, m = o["home"], o["away"], o["home_margin"]
    sm = {home: m, away: -m}
    out = []
    for s in o.get("sides") or []:
        if s.get("market_p") is None:
            continue
        side = s["side"]
        if s["market"] == "MONEYLINE":
            y = 1 if sm[side] > 0 else 0
        elif s.get("line") is not None:
            y = 1 if (sm[side] >= 2 if s["line"] < 0 else sm[side] > -2) else 0
        else:
            continue
        p = s["market_p"]
        if (s.get("gap") or 0) >= ei.EDGE_MIN:
            out.append(("ML_MODEL_GAP_3PLUS" if s["market"] == "MONEYLINE" else "PL_MODEL_GAP_3PLUS", p, y))
        if s["market"] == "MONEYLINE":
            if s.get("move_class") in ("NOTABLE", "EXCEPTIONAL"):
                out.append(("MOVE_TOWARD_NOTABLE" if (s.get("moved") or 0) > 0 else "MOVE_AWAY_NOTABLE", p, y))
        if s.get("rest_advantage") and s["market"] == "MONEYLINE":
            out.append(("REST_ADVANTAGE", p, y))
        for fam in s.get("edge_family_names") or []:
            out.append(("FAMILY_" + fam, p, y))
        if s.get("picked"):
            out.append(("EGGY_PICK", p, y))
            out.append(("PICK_MONEYLINE" if s["market"] == "MONEYLINE" else "PICK_PUCK_LINE", p, y))
            out.append(("PICK_IN_PREFERRED_PRICE" if s["price_class"] == "IN_PREFERRED_REGION" else "PICK_OUTSIDE_PREFERRED_PRICE", p, y))
            out.append(("PICK_EDGE_THESIS" if s.get("qualifies") else "PICK_BEST_AVAILABLE", p, y))
            gs_ = o.get("goalie_state")
            if gs_:
                out.append(({"BOTH_CONFIRMED": "PICK_GOALIES_BOTH_CONFIRMED", "SOURCE_CONFLICT": "PICK_GOALIES_SOURCE_CONFLICT", "UNKNOWN_GOALIE": "PICK_GOALIES_UNKNOWN"}.get(gs_, "PICK_GOALIES_ONE_PROBABLE"), p, y))
            if o.get("late_goalie_change"):
                out.append(("PICK_GOALIE_LATE_CHANGE", p, y))
            pv = o.get("personnel")
            if pv is not None:
                pv = {k: v for k, v in pv.items() if not k.startswith("_")}
                teams = list(pv.values())
                rel = [x for t in teams for x in t["relevant"]]
                material = [x for x in rel if x["impact"] == "MATERIAL"]
                own_t = pv.get(s["side"]) or {"relevant": [], "coverage": "NONE"}
                opp_teams = [t for k, t in pv.items() if k != s["side"]]
                if any(x["impact"] == "MATERIAL" and x["status"] in ("OUT", "IR") for x in own_t["relevant"]):
                    out.append(("PICK_OWN_MATERIAL_ABSENCE", p, y))
                if any(x["impact"] == "MATERIAL" and x["status"] in ("OUT", "IR") for t in opp_teams for x in t["relevant"]):
                    out.append(("PICK_OPPONENT_MATERIAL_ABSENCE", p, y))
                doubt = any(x["status"] not in ("OUT", "IR") for x in material)
                if any(x["status"] == "GAME_TIME_DECISION" for x in material):
                    out.append(("PICK_UNRESOLVED_GAME_TIME_DECISION", p, y))
                conflict = any(x["conflict"] for x in material)
                if doubt:
                    out.append(("PICK_MATERIAL_PLAYER_IN_DOUBT", p, y))
                if any(t["returns"] for t in teams):
                    out.append(("PICK_MATERIAL_PLAYER_RETURN", p, y))
                if conflict:
                    out.append(("PICK_INJURY_SOURCE_CONFLICT", p, y))
                covered = any(t["coverage"] != "NONE" for t in teams)
                if not covered:
                    out.append(("PICK_INJURY_COVERAGE_NONE", p, y))
                elif not (material or conflict):
                    out.append(("PICK_NO_MATERIAL_PERSONNEL_ISSUE", p, y))
                issue = bool(doubt or conflict or any(x["status"] in ("OUT", "IR") for x in material) or gs_ in ("SOURCE_CONFLICT", "UNKNOWN_GOALIE"))
                out.append((("PICK_EDGE_THESIS_" if s.get("qualifies") else "PICK_BEST_AVAILABLE_") + ("PERSONNEL_ISSUE" if issue else "CLEAN_PERSONNEL"), p, y))
            if s.get("confidence"):
                out.append(("CONFIDENCE_" + s["confidence"], p, y)) if not s.get("qualifies") else None
        if s.get("consensus"):
            out.append(("CONSENSUS_QUALITY", p, y))
        if s.get("qualifies"):
            out.append((s["structure"], p, y))
            out.append(("PRICE_IN_PREFERRED" if s["price_class"] == "IN_PREFERRED_REGION" else "PRICE_OUTSIDE_PREFERRED", p, y))
            out.append(("QUALIFYING_MONEYLINE" if s["market"] == "MONEYLINE" else "QUALIFYING_PUCK_LINE", p, y))
            if s.get("confidence"):
                out.append(("CONFIDENCE_" + s["confidence"], p, y))
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
            row.update(pick={k: p[k] for k in ("matchup", "side", "market", "line", "price", "model_probability", "market_probability_market", "gap", "trust_state", "edge", "confidence", "price_class", "structure", "supported_probability", "pick_basis")}, card=p.get("card"), edge_thesis=p.get("edge_thesis"), label=p.get("label"), evidence_families=(p.get("edge_thesis") or {}).get("families"), late_price=(o or {}).get("late_price"), grade=g,
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
    return {"policy": {k: ep.POLICY[k] for k in ("policy_id", "markets", "price_policy", "max_outputs_per_day")} | {"digest": ep.policy_digest()}, "date_et": today, "today": None if not cur else schema.redact_news_credit(cur["decision_doc"]),
            "record": record(events), "signals": sig, "observed_games": sum(1 for e in events if e["type"] == "OBSERVATION"), "settled_games": len(_settled_observations(events)), "availability": ep.AVAILABILITY}


# ------------------------------------------------------------------------------------------------ the decision deadline and its guard
DECISION_DEADLINE_MIN = 30      # the OPERATIONAL deadline: the day's decision is recorded no later than this long before the first game (normal days decide ~75 min before). Past it the guard raises EGGY PICK NOT RECORDED BY DEADLINE; a late pull is still accepted until LAST_CHANCE_MIN (recovery), then the day is an honest PASS.
GUARD_NAME = "eggy_pick_guard.json"


def decision_deadline(first_start):
    return None if first_start is None else first_start - dt.timedelta(minutes=DECISION_DEADLINE_MIN)


def deadline_guard(nhl_conn, now: dt.datetime, *, events=None) -> dict:
    """EGGY PICK NOT RECORDED BY DEADLINE. A production failure, reported by the trader cycle itself (every 10 minutes) and the watchdog, never discovered hours later.
    state: NO_SLATE | WAITING (before the deadline) | RECORDED | RECORDED_NOT_PUBLISHED | MISSING_AT_DEADLINE. Read-only."""
    today = et.eastern_today(now)
    rows = nhl_conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ? AND scheduled_start_utc IS NOT NULL ORDER BY scheduled_start_utc", (today,)).fetchall()
    starts = []
    for r in rows:
        t = dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00"))
        starts.append(t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))
    base = {"date": today, "slate_games": len(starts), "checked_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if not starts:
        return {**base, "state": "NO_SLATE"}
    first = min(starts)
    deadline = decision_deadline(first)
    events = read() if events is None else events
    dec = next((e for e in reversed(events) if e["type"] == "DECISION" and e["date"] == today), None)
    caps = ep.league_captures(today, now)
    base.update(first_game_utc=first.strftime("%Y-%m-%dT%H:%M:%SZ"), decision_deadline_utc=deadline.strftime("%Y-%m-%dT%H:%M:%SZ"), latest_side_price_capture_utc=caps[-1]["at"].strftime("%Y-%m-%dT%H:%M:%SZ") if caps else None)
    try:
        from operational import ingestion_health
        base["latest_trader_run_utc"] = (ingestion_health.load_health().get("real_parlay_paper_trader") or {}).get("last_success_utc")
        pub = (ingestion_health.load_health().get("cloud_snapshot_publish") or {}).get("last_success_utc")
    except Exception:                                                       # noqa: BLE001 - the guard never fails the cycle
        pub = None
    if dec is not None:
        doc = dec["decision_doc"]
        base.update(decision=doc.get("decision"), decided_at_utc=doc.get("decided_at_utc"), decided_before_deadline=(doc.get("decided_at_utc") or "") <= deadline.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if doc.get("decision") == "PASS":
            base["reason"] = doc.get("pass_reason")
        if pub is not None and doc.get("decided_at_utc") and et_parse(pub) < et_parse(doc["decided_at_utc"]) and now >= et_parse(doc["decided_at_utc"]) + dt.timedelta(minutes=PUBLISH_GRACE_MIN):
            return {**base, "state": "RECORDED_NOT_PUBLISHED", "reason": f"the decision is in the ledger but the last successful publication ({pub}) predates it by more than {PUBLISH_GRACE_MIN} minutes: the hosted page does not show it"}
        return {**base, "state": "RECORDED"}
    if now < deadline:
        return {**base, "state": "WAITING"}
    why = "no decision row exists for the slate day"
    if not caps:
        why += "; no league side-price capture was observed today"
    elif caps[-1]["at"] < first - dt.timedelta(minutes=ct_target_plus()):
        why += "; the latest side-price capture is older than the pregame target"
    return {**base, "state": "MISSING_AT_DEADLINE", "headline": "EGGY PICK NOT RECORDED BY DEADLINE", "reason": why}


PUBLISH_GRACE_MIN = 45


def et_parse(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def ct_target_plus() -> int:
    from operational import capture_timeline as ct
    return ct.CAPTURE_TARGET_MIN + 10


def run_guard(nhl_conn, now: dt.datetime) -> dict:
    """The guard as the trader runs it: evaluate, persist the state for the watchdog and the dashboard, and raise ONE local notification per (date, failing state)."""
    g = deadline_guard(nhl_conn, now)
    p = state_paths.path(GUARD_NAME)
    try:
        prior = json.loads(p.read_text())
    except (OSError, ValueError):
        prior = {}
    failing = g["state"] in ("MISSING_AT_DEADLINE", "RECORDED_NOT_PUBLISHED")
    g["notified"] = bool(failing and prior.get("date") == g["date"] and prior.get("state") == g["state"] and prior.get("notified"))
    if failing and not g["notified"]:
        try:
            from operational import notify as _n
            g["notified"] = bool(_n.send("NHL engine: Eggy", f"{g.get('headline') or 'EGGY PICK NOT PUBLISHED'} ({g['date']}): {g.get('reason', '')}"[:200]))
        except Exception:                                                   # noqa: BLE001
            pass
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(g, sort_keys=True, indent=1))
    tmp.replace(p)
    return g


# ------------------------------------------------------------------------------------------------ the cycle
def _prospective_rows() -> list[dict]:
    from operational import prospective_moneyline as pm
    try:
        return pm.read_ledger()
    except Exception:                                                  # the moneyline record is an input, never a precondition
        return []


def run_cycle(nhl_conn, now: dt.datetime) -> dict:
    out = _run_cycle(nhl_conn, now)
    try:
        g = run_guard(nhl_conn, now)
        out["guard"] = {k: g.get(k) for k in ("state", "decision_deadline_utc", "headline", "reason")}
    except Exception as exc:                                               # noqa: BLE001 - the guard reports; it never stops the cycle
        out["guard"] = {"state": "GUARD_ERROR", "reason": f"{type(exc).__name__}: {exc}"[:120]}
    return out


def _run_cycle(nhl_conn, now: dt.datetime) -> dict:
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
        if last_chance is not None and now >= last_chance:                  # the core hockey data is missing and the last chance is here: an honest, recorded PASS (a technical failure), never a silent absence
            doc = ep.decide([], now=now, why_no_prices=f"a technical / data failure at the decision deadline: the hockey intelligence was unusable ({why})")
            doc["pass_kind"] = "TECHNICAL_OR_DATA_FAILURE"
            _append([{"type": "DECISION", "date": today, "decision_doc": doc}])
            out["status"] = "PASS_TECHNICAL_AT_DEADLINE"
            _write_postmortem(read())
            return out
        out["status"] = "INTELLIGENCE_UNAVAILABLE"
        out["reason"] = why
        return out
    trust_pl = puck_line_trust(events)
    structure_state = signal_trust(events)
    blend = ep.blend_estimate(_prospective_rows())
    from operational import goalie_sources as gsrc
    try:                                                                    # personnel refresh BEFORE the ranking: forced when the last fetch is older than the late-pregame interval
        gstate = gsrc.refresh(nhl_conn, now, force=((gsrc._age_min(gsrc.load_state().get("fetched_at_utc"), now) or 1e9) > gsrc.NEAR_INTERVAL_MIN))
    except Exception:                                                       # noqa: BLE001 - a goalie-feed failure is UNKNOWN goalies, never a stopped product
        gstate = {}
    ghist = gsrc.read_history()
    from operational import personnel as pers
    try:                                                                    # attributable injury reports as they stand at this moment (nothing is fetched: see docs/PERSONNEL_SOURCES.md)
        pstate = pers.resolve_all(nhl_conn, now)
        pviews = {t: pers.team_view(pstate, t) for g in upcoming for t in (g["home"], g["away"])}
    except Exception:                                                       # noqa: BLE001 - unreadable reports are unknown injuries, never a stopped product
        pviews = None
    cands, by_game = [], defaultdict(list)
    for g in upcoming:
        gview = None if gstate.get("status") == "DISABLED" else gsrc.game_view(gstate, g["game_id"], g["home"], g["away"], now, ghist)    # a feed the owner has not enabled is "no feed", not "unknown goalies"
        for c in ep.evaluate_game(ctx, g, captures, now, cap, trust_pl, blend, structure_state, goalies=gview, personnel=pviews):
            cands.append(c)
            by_game[g["game_id"]].append(c)
    doc = ep.decide(cands, now=now, structure_state=structure_state)
    obs = [observation(by_game[g["game_id"]], g, doc["decided_at_utc"], structure_state) for g in upcoming if by_game.get(g["game_id"])]
    if doc["decision"] == "PICK":                                      # the one bet is flagged in its game's observation, with its confidence, for the prospective record
        pk = doc["pick"]
        for o in obs:
            if o["game_id"] == str(pk["game_id"]):
                for sd in o["sides"]:
                    if sd["side"] == pk["side"] and sd["market"] == pk["market"]:
                        sd["picked"], sd["confidence"] = True, pk["confidence"]
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
