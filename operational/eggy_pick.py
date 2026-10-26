"""EGGY PICK OF THE DAY: at most ONE NHL side per day, MONEYLINE or PUCK LINE, or PASS. Separate from the learning-parlay engine (operational/learning_cohort.py), which is untouched and keeps recording,
settling, postmortem-ing and learning. Eggy shares the NHL intelligence (the team model, the moneyline review, the prospective trust record, price freshness) and has its own decision policy and its own record.

THE STANDARD. A side is OFFICIAL only when ALL hold:
  1. its price is FRESH (operational/price_freshness.py) and inside the price policy: preferred region -130 to +130; a SHOULDER out to -160 / +160 needs twice the edge; beyond it Eggy does not hunt;
  2. it has a SUPPORTED probability that beats the price: the hockey model's probability counts only where the prospective record shows the model's disagreement with the market is real
     (`DISAGREEMENT_HAS_SIGNAL`, 100 independent games in the gap band, |z| >= 2: operational/prospective_moneyline.py; for the puck line, Eggy's own record of puck-line observations). Otherwise the market's no-vig
     probability is the best estimate, and a market-only estimate cannot beat the bookmaker's margin: there is no edge to claim, so there is no pick. The model liking a side more than the market is never trusted by default;
  3. the edge over the break-even price is at least the engine's edge threshold (3 points; 6 on a shoulder);
  4. the CASE FOR materially outweighs the CASE AGAINST (see `balance`).
Anything else is PASS, with the specific reason. PASS is the correct answer far more often than not, and until the trust record earns it the engine says so rather than manufacturing a pick. Every game is nevertheless
OBSERVED each day (both markets, with the signals, then the result) so that signals earn or lose trust prospectively (operational/eggy_pick_ledger.py): reverse line movement and handle splits are NOT AVAILABLE (no approved
source), and nothing here pretends otherwise.

MONEYLINE or PUCK LINE: each side is priced in both; the market with the larger SUPPORTED edge wins, ties go to the moneyline (the lower-variance bet). A higher payout is never a reason.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import glob
import json
import math
from pathlib import Path
from types import SimpleNamespace

from operational import eggy_intelligence as ei
from operational import price_freshness as pf

REPO = Path(__file__).resolve().parent.parent
POLICY = {"policy_id": "EGGY_SIDE_PICK_V1", "unit": "SIDE", "max_outputs_per_day": 1, "markets": ["MONEYLINE", "PUCK_LINE"], "no": ["player props", "player points", "shots", "parlays"],
          "price_policy": {"preferred_region_american": [-130, 130], "shoulder_american": [-160, 160], "edge_in_region": ei.EDGE_MIN, "edge_on_shoulder": 2 * ei.EDGE_MIN, "beyond_shoulder": "not hunted"},
          "supported_probability": "the hockey model's only where the prospective record shows the disagreement is real; otherwise the market's no-vig probability",
          "decision_time": "once a day, after the pregame league pull is observed and before the first game (or PASS: no current prices)", "freshness": "FRESH only (operational/price_freshness.py)",
          "pass": "the correct answer without a supported edge; never manufactured", "record": "own ledger, own postmortem, own signal trust (operational/eggy_pick_ledger.py)"}
PUCK = 1.5
CAL_PATH = REPO / "docs" / "validation" / "eggy_pick_calibration.json"


def policy_digest() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:16]


AVAILABILITY = {
    "AVAILABLE": ["moneyline and puck-line (+/-1.5) prices, DraftKings, from the league pulls (morning and pregame)", "movement between the day's first capture and the decision (the first capture is NOT the true opening line)",
                  "authoritative hockey probability (eggy-matchup-v1), expected-goal / goal / high-danger indicators with league ranks, rest and back-to-back, special teams, recent games",
                  "the prospective model-versus-market record (one observation per game)", "price freshness"],
    "NOT AVAILABLE": ["ticket percentages", "handle / money percentages", "ticket-versus-handle divergence", "reverse line movement and market resistance (they need the splits)", "cross-book disagreement (one book only)",
                      "the true opening line and the closing line (only the captures the engine took)", "confirmed starting goalies (no approved confirmation source feeds Eggy today)", "reliable lineup and injury information (no approved source)"],
    "note": "The Odds API (the only odds source) returns prices, not betting splits; scraping a splits site would be a terms-of-service risk and is not done."}


# ------------------------------------------------------------------------------------------------ the market
def _utc(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _implied(a: float) -> float:
    return ei.implied_from_american(float(a))


def _no_vig(a: float, b: float) -> float:
    ia, ib = _implied(a), _implied(b)
    return ia / (ia + ib)


def league_captures(day_et: str, now: dt.datetime, archive_dir: Path | None = None) -> list[dict]:
    """The day's league pulls (moneyline + spreads) observed at or before `now`, oldest first: [{'at': datetime, 'events': {event_id: {...}}}]. Read from the archive of real provider responses."""
    from research.live_sog_pricing.event_mapping import normalize_team_name
    d0 = dt.date.fromisoformat(day_et)
    out = []
    base = archive_dir or (REPO / "operational" / "odds_archive" / "live")
    for d in (d0, d0 + dt.timedelta(days=1)):
        for f in sorted(glob.glob(str(base / f"{d.strftime('%Y%m%d')}T*h2h+spreads.json"))):
            try:
                doc = json.loads(Path(f).read_text())
            except (OSError, ValueError):
                continue
            meta, resp = doc.get("meta") or {}, doc.get("response")
            if not isinstance(resp, list) or not meta.get("retrieved_at_utc"):
                continue
            at = _utc(meta["retrieved_at_utc"])
            if at > now:
                continue
            events = {}
            for e in resp:
                home, away = normalize_team_name(e.get("home_team", "")), normalize_team_name(e.get("away_team", ""))
                bm = next((b for b in e.get("bookmakers", []) if b.get("key") == "draftkings"), None)
                if not (home and away and bm):
                    continue
                m = {k["key"]: k for k in bm.get("markets", [])}
                ml = {o["name"]: o["price"] for o in (m.get("h2h") or {}).get("outcomes", [])}
                pl = {o["name"]: (o["price"], o.get("point")) for o in (m.get("spreads") or {}).get("outcomes", [])}
                events[e["id"]] = {"event_id": e["id"], "home": home, "away": away, "commence": e.get("commence_time"), "ml": {home: ml.get(e["home_team"]), away: ml.get(e["away_team"])},
                                   "pl": {home: pl.get(e["home_team"]), away: pl.get(e["away_team"])}}
            out.append({"at": at, "events": events})
    return sorted(out, key=lambda c: c["at"])


def calibration() -> dict:
    try:
        return json.loads(CAL_PATH.read_text())
    except (OSError, ValueError):
        return {}


def win_by_two(p_side: float) -> float | None:
    """P(side wins by 2+ goals | its model win probability), from the out-of-time calibration; None when the calibration file is missing."""
    c = (calibration().get("coefficients_live") or {})
    if "intercept" not in c:
        return None
    p = min(max(p_side, 1e-4), 1 - 1e-4)
    return 1 / (1 + math.exp(-(c["intercept"] + c["slope_on_logit_win_probability"] * math.log(p / (1 - p)))))


def puck_line_cover(p_side: float, point: float) -> float | None:
    """The hockey model's probability that a side covers its puck line: -1.5 covers by winning by 2+; +1.5 covers unless the OPPONENT wins by 2+."""
    if point < 0:
        return win_by_two(p_side)
    w = win_by_two(1.0 - p_side)
    return None if w is None else 1.0 - w


def price_class(american: float) -> str:
    lo, hi = POLICY["price_policy"]["preferred_region_american"]
    slo, shi = POLICY["price_policy"]["shoulder_american"]
    if lo <= american <= hi:
        return "IN_REGION"
    if slo <= american <= shi:
        return "SHOULDER"
    return "OUTSIDE"


def required_edge(klass: str) -> float | None:
    p = POLICY["price_policy"]
    return {"IN_REGION": p["edge_in_region"], "SHOULDER": p["edge_on_shoulder"]}.get(klass)


# ------------------------------------------------------------------------------------------------ candidates
def _leg(gid: str, event: dict, team: str, opp: str, price: float, no_vig: float, at: dt.datetime, start: str | None):
    from research.real_market_parlay.engine import ParlayLeg
    return ParlayLeg(game_id=str(gid), event_id=event["event_id"], market_family="MONEYLINE", participant_id=team, participant_name=team, side="HOME" if team == event["home"] else "AWAY", threshold=None,
                     american_price=float(price), conservative_probability=no_vig, sportsbook="draftkings", captured_at_utc=at.strftime("%Y-%m-%dT%H:%M:%SZ"), provider_contract_verified=True,
                     model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent=opp, game_start_utc=start, market_probability=no_vig)


def evaluate_game(ctx, game: dict, captures: list[dict], now: dt.datetime, capture_ctx: dict | None = None, trust_pl: dict | None = None) -> list[dict]:
    """Every candidate side of one game: both teams in both markets, with the hockey and market facts, the supported probability, the edge and the reasons it does or does not qualify."""
    gid, home, away = str(game["game_id"]), game["home"], game["away"]
    ev_now = next((c["events"][eid] for c in reversed(captures) for eid in c["events"] if c["events"][eid]["home"] == home and c["events"][eid]["away"] == away), None)
    if ev_now is None:
        return []
    at_now = next(c["at"] for c in reversed(captures) if any(e["home"] == home and e["away"] == away for e in c["events"].values()))
    first = next(((c["at"], e) for c in captures for e in c["events"].values() if e["home"] == home and e["away"] == away), None)
    mv = ei.team_view(ctx, home, away)
    out = []
    cap = capture_ctx or {}
    for team, opp in ((home, away), (away, home)):
        price, opp_price = ev_now["ml"].get(team), ev_now["ml"].get(opp)
        if price is None or opp_price is None or not (mv and mv.get("available")):
            continue
        m_nv = _no_vig(price, opp_price)
        p_model = mv["p_home"] if team == home else mv["p_away"]
        leg = _leg(gid, ev_now, team, opp, price, m_nv, at_now, game.get("start_utc"))
        card = ei.review_moneyline(ctx, leg, "MARKET_NO_VIG", m_nv)
        fresh = pf.classify(SimpleNamespace(market_family="MONEYLINE", retrieved_at_utc=at_now.strftime("%Y-%m-%dT%H:%M:%SZ"), quote_updated_utc=None, captured_at_utc=None, game_start_utc=game.get("start_utc"),
                                            provider_start_utc=None), now, wave_first_start=(cap.get("by_game") or {}).get(gid), first_wave_start=cap.get("first_start"), wave_pulls_funded=bool(cap.get("funded")))
        moved = None
        if first and first[0] < at_now and first[1]["ml"].get(team) is not None and first[1]["ml"].get(opp) is not None:
            moved = round(m_nv - _no_vig(first[1]["ml"][team], first[1]["ml"][opp]), 4)
        trust_state = (card.get("market_trust") or {}).get("disagreement_state") or "INSUFFICIENT_EVIDENCE"
        base = {"game_id": gid, "matchup": f"{away} @ {home}", "side": team, "opponent": opp, "home": home, "away": away, "start_utc": game.get("start_utc"), "capture_at_utc": at_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "first_capture_at_utc": None if not first else first[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "price_freshness": fresh, "model_win_probability": round(p_model, 4), "market_win_probability": round(m_nv, 4),
                "hockey_view": {k: mv.get(k) for k in ("favourite", "p_favourite", "gap", "drivers")}, "calibration": (card.get("calibration_confidence") or {}).get("level"),
                "data_completeness": (card.get("data_completeness") or {}).get("fraction"), "flags": [f["name"] for f in (card.get("contradiction") or {}).get("flags", [])], "card_thesis": (card.get("thesis") or {}).get("type"),
                "rest_days": {t: ((mv.get("indicators") or {}).get(t) or {}).get("rest_days") for t in (home, away)}, "model_trust_overall": (ctx.moneyline_trust or {}).get("state")}
        out.append({**base, "market": "MONEYLINE", "line": None, "price": float(price), "implied_with_margin": round(_implied(price), 4), "market_probability_market": round(m_nv, 4), "model_probability": round(p_model, 4),
                    "gap": round(p_model - m_nv, 4), "trust_state": trust_state, "movement_toward_side": moved, "card": {"case_for": card.get("case_for"), "case_against": card.get("case_against")}})
        pl = ev_now["pl"].get(team), ev_now["pl"].get(opp)
        if pl[0] and pl[1] and abs(pl[0][1]) == PUCK and pl[1][0]:
            (pp, pt), (op, _o) = pl[0], pl[1]
            pl_nv = _no_vig(pp, op)
            p_cov = puck_line_cover(p_model, pt)
            pl_move = None
            if first and first[1]["pl"].get(team) and first[1]["pl"].get(opp):
                f1, f2 = first[1]["pl"][team], first[1]["pl"][opp]
                if f1 and f2 and f1[1] == pt:
                    pl_move = round(pl_nv - _no_vig(f1[0], f2[0]), 4)
            if p_cov is not None:
                gap = p_cov - pl_nv
                out.append({**base, "market": "PUCK_LINE", "line": pt, "price": float(pp), "implied_with_margin": round(_implied(pp), 4), "market_probability_market": round(pl_nv, 4), "model_probability": round(p_cov, 4),
                            "gap": round(gap, 4), "trust_state": (trust_pl or {}).get("state") or "INSUFFICIENT_EVIDENCE", "movement_toward_side": pl_move, "card": {"case_for": card.get("case_for"), "case_against": card.get("case_against")}})
    for c in out:
        _qualify(c)
    return out


def _qualify(c: dict) -> None:
    """The supported probability, the edge and the reasons the candidate does or does not qualify (the standard, in order)."""
    c["price_class"] = price_class(c["price"])
    trusted = c["trust_state"] == "DISAGREEMENT_HAS_SIGNAL" and c["gap"] > 0
    c["supported_probability"] = c["model_probability"] if trusted else c["market_probability_market"]
    c["probability_basis"] = "HOCKEY MODEL (disagreement proven in this band)" if trusted else "MARKET NO-VIG (the model's disagreement is not proven)"
    c["edge"] = round(c["supported_probability"] - c["implied_with_margin"], 4)
    need = required_edge(c["price_class"])
    why = []
    if c["price_freshness"]["status"] != pf.FRESH:
        why.append(f"MARKET DATA QUALITY: the price is {c['price_freshness']['status']} ({c['price_freshness']['reason'][:90]})")
    if need is None:
        why.append(f"price {c['price']:+.0f} is outside the {POLICY['price_policy']['shoulder_american'][0]} to +{POLICY['price_policy']['shoulder_american'][1]} range Eggy hunts")
    if (c.get("data_completeness") or 0) < 0.6 or c.get("calibration") in ("INSUFFICIENT", "LOW", None):
        why.append(f"data completeness {c.get('data_completeness')} / calibration {c.get('calibration')} too weak to stand a pick on")
    if need is not None and c["edge"] < need:
        why.append(("no supported edge: " if not trusted else "edge too small: ") + f"{c['probability_basis'].split(' (')[0].lower()} {c['supported_probability']:.1%} against a break-even of {c['implied_with_margin']:.1%} = {c['edge'] * 100:+.1f} points (needs {need * 100:.0f})"
                   + ("" if trusted or abs(c["gap"]) < ei.EDGE_MIN else f"; the hockey model sees {c['model_probability']:.1%} ({c['gap'] * 100:+.1f} pts) but that disagreement is unproven ({c['trust_state'].replace('_', ' ').lower()})"))
    c["qualifies_before_the_case"] = not why
    c["not_qualifying_because"] = why


# ------------------------------------------------------------------------------------------------ the case for and against
def cases(c: dict) -> dict:
    """CASE FOR and CASE AGAINST as lists of {kind, text, strength}; strengths are STRONG / MODERATE / WEAK / UNPROVEN (listed, never counted)."""
    f, a = [], []
    trusted = c["trust_state"] == "DISAGREEMENT_HAS_SIGNAL" and c["gap"] > 0
    if c["gap"] >= ei.EDGE_MIN:
        if trusted:
            f.append({"kind": "MODEL_EDGE", "strength": "STRONG", "text": f"the hockey model gives {c['model_probability']:.1%} against the market's {c['market_probability_market']:.1%} ({c['gap'] * 100:+.1f} pts) and the prospective record says this size of disagreement is real"})
        else:
            f.append({"kind": "MODEL_EDGE", "strength": "UNPROVEN", "text": f"the hockey model gives {c['model_probability']:.1%} against the market's {c['market_probability_market']:.1%} ({c['gap'] * 100:+.1f} pts), but disagreement of this size has not been shown to beat the market (state: {c['trust_state']})"})
            a.append({"kind": "UNPROVEN_EDGE", "strength": "MODERATE", "text": "the case for this side rests on a model-versus-market gap the prospective record has not yet validated"})
    elif c["gap"] <= -ei.EDGE_MIN:
        a.append({"kind": "MARKET_STRONGER", "strength": "MODERATE", "text": f"the market ({c['market_probability_market']:.1%}) rates this side higher than our model ({c['model_probability']:.1%})"})
    else:
        f.append({"kind": "MARKET_AGREES", "strength": "WEAK", "text": f"model {c['model_probability']:.1%} and market {c['market_probability_market']:.1%} agree: no disagreement to exploit"})
    drv = [d for d in (c["hockey_view"].get("drivers") or []) if d.get("favours") == c["side"] and abs(d.get("log_odds_toward_home", 0)) >= 0.05]
    if len(drv) >= 2:
        f.append({"kind": "HOCKEY_INDICATORS", "strength": "MODERATE", "text": "the team model's drivers favour " + c["side"] + ": " + ", ".join(d["label"] for d in drv[:3])})
    elif c["hockey_view"].get("favourite") not in (None, c["side"]) and c["market"] == "MONEYLINE":
        a.append({"kind": "HOCKEY_FAVOURS_OPPONENT", "strength": "MODERATE", "text": f"the hockey model rates {c['opponent']} the better team ({c['hockey_view'].get('p_favourite'):.1%})"})
    rd = c["rest_days"]
    if rd.get(c["opponent"]) is not None and rd.get(c["side"]) is not None:
        if rd[c["opponent"]] <= 1 < rd[c["side"]]:
            f.append({"kind": "REST", "strength": "WEAK", "text": f"{c['opponent']} is on a back-to-back and {c['side']} is rested"})
        elif rd[c["side"]] <= 1 < rd[c["opponent"]]:
            a.append({"kind": "REST", "strength": "MODERATE", "text": f"{c['side']} is on a back-to-back and {c['opponent']} is rested"})
    mvt = c.get("movement_toward_side")
    if mvt is not None and abs(mvt) >= 0.01:
        (f if mvt > 0 else a).append({"kind": "LINE_MOVEMENT", "strength": "UNPROVEN", "text": f"the market moved {'toward' if mvt > 0 else 'away from'} {c['side']} by {abs(mvt) * 100:.1f} pts since the day's first capture (movement has not yet been shown to predict anything)"})
    fr = c["price_freshness"]
    if fr["status"] != pf.FRESH:
        a.append({"kind": "STALE_PRICE", "strength": "STRONG", "text": f"MARKET DATA QUALITY: {fr['reason']}"})
    if c["price_class"] == "SHOULDER":
        a.append({"kind": "PRICE_REGION", "strength": "MODERATE", "text": f"{c['price']:+.0f} is outside the preferred -130 to +130 region: it needs twice the edge"})
    elif c["price_class"] == "OUTSIDE":
        a.append({"kind": "PRICE_REGION", "strength": "STRONG", "text": f"{c['price']:+.0f} is beyond what Eggy hunts"})
    else:
        f.append({"kind": "PRICE_REGION", "strength": "WEAK", "text": f"{c['price']:+.0f} is inside the preferred -130 to +130 region"})
    if c["market"] == "PUCK_LINE":
        a.append({"kind": "PUCK_LINE_UNPROVEN", "strength": "MODERATE", "text": "the puck-line probability is the hockey model's win probability mapped through a historical goal-margin calibration; its agreement with the puck-line MARKET has no prospective record yet"}) if c["trust_state"] != "DISAGREEMENT_HAS_SIGNAL" else None
    a.append({"kind": "GOALIE", "strength": "WEAK", "text": "starting goalies are not confirmed by an approved source (not available to Eggy)"})
    a.append({"kind": "VIG", "strength": "WEAK", "text": f"the bookmaker's margin costs about {(c['implied_with_margin'] - c['market_probability_market']) * 100:.1f} points of break-even on this side"})
    return {"case_for": f, "case_against": a}


def balance(f: list[dict], a: list[dict]) -> dict:
    """Does the case FOR materially outweigh the case AGAINST? Ordinal and countable, never a weighted score: it needs at least one STRONG reason for, no STRONG reason against, and (strong + moderate for) greater than
    (2 x strong + moderate against). UNPROVEN and WEAK items are shown and not counted."""
    n = lambda xs, s: sum(1 for x in xs if x["strength"] == s)
    fs, fm, as_, am = n(f, "STRONG"), n(f, "MODERATE"), n(a, "STRONG"), n(a, "MODERATE")
    ok = fs >= 1 and as_ == 0 and (fs + fm) > (2 * as_ + am)
    return {"for_strong": fs, "for_moderate": fm, "against_strong": as_, "against_moderate": am, "materially_outweighs": bool(ok)}


def confidence(c: dict, bal: dict) -> str:
    """A calibrated evidence CATEGORY, never a percentage: HIGH needs a proven disagreement with a clear edge and nothing moderate against; MEDIUM a proven one with at most one; otherwise LOW."""
    if c["trust_state"] != "DISAGREEMENT_HAS_SIGNAL":
        return "LOW"
    if c["edge"] >= 2 * ei.EDGE_MIN and bal["against_moderate"] == 0 and c["calibration"] == "HIGH":
        return "HIGH"
    return "MEDIUM" if bal["against_moderate"] <= 1 else "LOW"


def decide(candidates: list[dict], *, now: dt.datetime, why_no_prices: str | None = None) -> dict:
    """The day's decision from the evaluated candidates: rank every qualifying side by its supported edge (ties: the moneyline), take the best whose case balances, else PASS with the specific reason."""
    base = {"policy_id": POLICY["policy_id"], "policy_digest": policy_digest(), "decided_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "availability": AVAILABILITY}
    if why_no_prices:
        return {**base, "decision": "PASS", "pass_reason": why_no_prices, "ranked": [], "candidates_examined": 0}
    for c in candidates:
        c.update(cases(c))
        c["balance"] = balance(c["case_for"], c["case_against"])
    ranked = sorted(candidates, key=lambda c: (c["price_class"] == "OUTSIDE" or c["price_freshness"]["status"] != pf.FRESH, -round(c["edge"], 2), -c["gap"], c["market"] != "MONEYLINE"))          # sides Eggy could hunt first; then the supported edge (without a proven disagreement every side sits at minus the margin, so the model's preference breaks the tie)
    qualifying = [c for c in ranked if c["qualifies_before_the_case"]]
    pick = next((c for c in qualifying if c["balance"]["materially_outweighs"]), None)
    shadow = [{k: c[k] for k in ("matchup", "side", "market", "line", "price", "model_probability", "market_probability_market", "gap", "trust_state", "supported_probability", "edge", "price_class", "not_qualifying_because")} | {"balance": c["balance"]} for c in ranked[:5]]
    if pick:
        pick["confidence"] = confidence(pick, pick["balance"])
        return {**base, "decision": "PICK", "pick": pick, "ranked": shadow, "candidates_examined": len(candidates)}
    if ranked:
        top = ranked[0]
        reason = ("; ".join(top["not_qualifying_because"][:2]) if top["not_qualifying_because"] else "the case for the best side does not materially outweigh the case against (" + json.dumps(top["balance"]) + ")")
        pass_reason = f"no side meets the standard. The closest, {top['side']} {top['market'].replace('_', ' ')} {top['price']:+.0f}: {reason}"
    else:
        pass_reason = "no game has a current moneyline and puck-line price"
    return {**base, "decision": "PASS", "pass_reason": pass_reason, "ranked": shadow, "candidates_examined": len(candidates),
            "what_would_change_it": "a side whose model-versus-market gap the prospective record has proven (100 independent games in its gap band, |z| >= 2) at a fresh price in the preferred region, with the case for outweighing the case against"}
