"""EGGY PICK OF THE DAY: at most ONE NHL side per day, MONEYLINE or PUCK LINE, or PASS. Separate from the learning-parlay engine (operational/learning_cohort.py), which is untouched and keeps recording,
settling, postmortem-ing and learning. Eggy shares the NHL intelligence (the team model, the moneyline review, the prospective trust record, price freshness) and has its own decision policy and its own record.

THE DECISION HIERARCHY (the whole body of evidence, no single source decides):
  A. MARKET BASELINE   the fresh no-vig sportsbook probability is the prior, and an extremely strong one.
  B. HOCKEY SUPPORT    the validated team intelligence (strength, expected-goal / goal / high-danger profile, home ice, rest and back-to-back, opponent context) supports or contradicts the side.
  C. PRICE QUALITY     the offered moneyline or puck line against the EVIDENCE-WEIGHTED probability: the market's, moved toward the hockey model's by the weight the model has earned.
  D. MARKET BEHAVIOUR  price movement across the day's captures (earliest, current, direction, size, when): information, not a rule; no betting splits exist here and none are invented.
  E. MODEL-MARKET GAP  an important signal whose WEIGHT is its prospective trust: PROVEN -> the model's probability, and a STRONG reason; ACCUMULATING / INSUFFICIENT -> the weight the record's own blend estimate
                       supports (never above one half), UNPROVEN supporting context that cannot carry a pick alone; FAILS -> no weight, and a reason AGAINST trusting the model.
  F. CASE FOR / AGAINST evaluated together. Nothing is mandatory except a FRESH price, adequate data and calibration, and a price that is not extreme.

THREE VALID EVIDENCE STRUCTURES (a pick must be one; each needs quantitative support, never narrative alone):
  TYPE B  SUPPORTED MARKET GAP        the model rates the side materially above the market AND the prospective record has proven that disagreement. Extra confidence.
  TYPE A  CONSENSUS QUALITY           market and hockey intelligence agree the side is the better team (both at least a clear lean, within five points of each other) at a price in the preferred region, with the hockey
                                      drivers behind it. The model does not have to beat the market. No positive expected value is CLAIMED: the bookmaker's margin is the cost and the card says so.
  TYPE C  MARKET / SITUATIONAL        the hockey picture is sound (not below the market, drivers favour the side) AND at least two measured, independent facts make the price unusually attractive: the market has moved toward
          OPPORTUNITY                 the side since the first capture, a rest advantage, an unproven model gap of 3+ points, or (puck line) a conservative cover probability 3+ points above the line's no-vig.
A pick also needs a case FOR that materially exceeds the case AGAINST (see `balance`). PASS is valid and frequent; it is never manufactured.

MONEYLINE or PUCK LINE: compared on the CONSERVATIVE supported edge (edge less the probability's own uncertainty); the puck line carries the larger uncertainty (its probability is the model's win probability mapped
through a goal-margin calibration that over-predicts by up to ~4.7 points in one bucket out of time). A larger nominal edge on the less trustworthy model does not win. Ties go to the moneyline.
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
POLICY = {"policy_id": "EGGY_SIDE_PICK_V2", "unit": "SIDE", "max_outputs_per_day": 1, "markets": ["MONEYLINE", "PUCK_LINE"], "no": ["player props", "player points", "shots", "parlays"],
          "decision_hierarchy": ["A market baseline (no-vig prior)", "B hockey support", "C price quality against the evidence-weighted probability", "D market behaviour (movement across captures)", "E model-market gap, weighted by its prospective trust",
                                 "F case for versus case against"],
          "evidence_structures": {"B": "SUPPORTED_MARKET_GAP", "A": "CONSENSUS_QUALITY", "C": "MARKET_SITUATIONAL_OPPORTUNITY"},
          "price_policy": {"preferred_region_american": [-130, 130], "preferred_region_status": "HEURISTIC preference (a ranking factor and a reason against when outside); not validated",
                           "extreme_guard_american": [-200, 200], "extreme_guard_status": "HEURISTIC: Eggy does not hunt extreme prices (a -300 favourite is not what it is for); not validated",
                           "no_edge_requirement": "a pick does not need a positive supported edge: consensus and situational picks pay the margin and are labelled so"},
          "model_weight": {"proven": 1.0, "failing": 0.0, "accumulating": "the blend estimate k of the prospective record (market + k (model - market)), bounded to [0, 0.5]", "status": "k is measured; the bounds are heuristic"},
          "balance": {"rule": "no STRONG reason against; at least two evidence categories supporting at MODERATE or better; (strong + moderate for) >= (2 x strong + moderate against) + 2", "status": "HEURISTIC ordinal rule, not a weighted score"},
          "thresholds": {"model_gap_material": ei.EDGE_MIN, "consensus_agreement_max_gap": 0.05, "consensus_min_market": 0.50, "consensus_min_model": 0.52, "movement_material": 0.01, "situational_min_edge": -0.01, "status": "HEURISTIC except the engine's own edge threshold"},
          "decision_time": "once a day, after the pregame league pull is observed and before the first game (or PASS: no current prices)", "freshness": "FRESH only (operational/price_freshness.py)",
          "pass": "valid and never manufactured", "record": "own ledger, own postmortem, own evidence-structure and signal trust (operational/eggy_pick_ledger.py)"}
PUCK = 1.5
CAL_PATH = REPO / "docs" / "validation" / "eggy_pick_calibration.json"
STRUCTURES = ("SUPPORTED_MARKET_GAP", "CONSENSUS_QUALITY", "MARKET_SITUATIONAL_OPPORTUNITY")
PRIORITY = {"SUPPORTED_MARKET_GAP": 0, "MARKET_SITUATIONAL_OPPORTUNITY": 1, "CONSENSUS_QUALITY": 2}


def policy_digest() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:16]


AVAILABILITY = {
    "AVAILABLE": ["moneyline and puck-line (+/-1.5) prices, DraftKings, from the league pulls (morning and pregame)", "every captured price per side across the day: earliest, current, direction, size and time of movement (the first capture is NOT the true opening line)",
                  "authoritative hockey probability (eggy-matchup-v1), expected-goal / goal / high-danger indicators with league ranks, rest and back-to-back, special teams, recent games",
                  "the prospective model-versus-market record (one observation per game)", "price freshness"],
    "NOT AVAILABLE": ["ticket percentages", "handle / money percentages", "ticket-versus-handle divergence", "reverse line movement and market resistance (they need the splits)", "cross-book disagreement (one book only)",
                      "the true opening line and the closing line (only the captures the engine took)", "confirmed starting goalies (no approved confirmation source feeds Eggy today)", "reliable lineup and injury information (no approved source)"],
    "note": "The Odds API (the only odds source) returns prices, not betting splits; scraping a splits site would be a terms-of-service risk and is not done. Price movement is market information; it is not called sharp money."}


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


# ------------------------------------------------------------------------------------------------ the model's weight
def blend_estimate(rows: list[dict]) -> dict:
    """The record's own estimate of how much weight the hockey model deserves: k in  p = market + k (model - market), by maximum likelihood over the finished priced games (one observation per game), with a
    likelihood-ratio 95% interval. k = 0 trusts the market, k = 1 the model. It is the evidence behind the weight; the weight itself is bounded (see `weight_for`)."""
    if not rows:
        return {"games": 0, "k_hat": None, "ci95": None}
    def ll(k):
        t = 0.0
        for r in rows:
            m, h, y = r["market_home"], r["hockey_home"], r["home_won"]
            p = min(max(m + k * (h - m), 1e-4), 1 - 1e-4)
            t += y * math.log(p) + (1 - y) * math.log(1 - p)
        return t
    ks = [i / 20 for i in range(-20, 41)]
    ls = [ll(k) for k in ks]
    best = max(range(len(ks)), key=lambda i: ls[i])
    ci = [k for k, l in zip(ks, ls) if l >= ls[best] - 1.92]
    return {"games": len(rows), "k_hat": ks[best], "ci95": [min(ci), max(ci)]}


def weight_for(trust_state: str, gap: float, blend: dict | None) -> tuple[float, str]:
    """(the weight the model's probability deserves for this side, why). PROVEN and above the market -> 1; FAILING -> 0; otherwise the blend estimate, bounded to [0, 0.5]."""
    if trust_state == "DISAGREEMENT_HAS_SIGNAL" and gap > 0:
        return 1.0, "the prospective record has proven this size of disagreement: the model's probability is used"
    if trust_state == "DISAGREEMENT_IS_OVERCONFIDENCE":
        return 0.0, "the prospective record shows the model overconfident in this band: it gets no weight"
    k = (blend or {}).get("k_hat")
    if k is None:
        return 0.0, "no record yet: the market's probability stands"
    return round(min(max(k, 0.0), 0.5), 3), f"the record's blend estimate is k = {k:.2f} ({(blend or {}).get('games', 0)} games, interval {(blend or {}).get('ci95')}): the model gets that weight, never more than half"


# ------------------------------------------------------------------------------------------------ the puck line's own uncertainty
def pl_uncertainty(p_side: float, point: float) -> dict:
    """The puck-line probability, ADJUSTED CONSERVATIVELY for the out-of-time over-prediction of the goal-margin calibration in the relevant win-probability bucket, and its uncertainty. History may lower a probability, never
    raise it (the engine's own rule): a bucket that over-predicted lowers the cover probability by the bias; the uncertainty is the larger of that bias and the calibration standard error."""
    raw = puck_line_cover(p_side, point)
    cal = (calibration().get("out_of_time_2025") or {}).get("calibration_by_team_win_probability") or []
    p_for_bucket = p_side if point < 0 else 1.0 - p_side                   # -1.5 covers by the side winning by 2+; +1.5 fails when the OPPONENT wins by 2+
    bias = 0.0
    for b in cal:
        lo, hi = (float(x) for x in b["team_win_probability"].split("-"))
        if lo <= p_for_bucket < hi + (1e-9 if hi >= 1 else 0):
            bias = b["predicted_win_by_2plus"] - b["observed"]
            n = b["n"]
            se = math.sqrt(max(b["observed"] * (1 - b["observed"]), 1e-4) / max(n, 1))
            break
    else:
        se = 0.03
    if raw is None:
        return {"raw": None}
    adj = -max(bias, 0.0) if point < 0 else -max(-bias, 0.0)               # +1.5: an UNDER-predicted opponent margin overstates the cover
    return {"raw": raw, "adjusted": raw + adj, "bias_in_bucket": round(bias, 4), "uncertainty": round(max(abs(bias), se, 0.015), 4), "calibration": "MEDIUM"}


# ------------------------------------------------------------------------------------------------ candidates
def _leg(gid: str, event: dict, team: str, opp: str, price: float, no_vig: float, at: dt.datetime, start: str | None):
    from research.real_market_parlay.engine import ParlayLeg
    return ParlayLeg(game_id=str(gid), event_id=event["event_id"], market_family="MONEYLINE", participant_id=team, participant_name=team, side="HOME" if team == event["home"] else "AWAY", threshold=None,
                     american_price=float(price), conservative_probability=no_vig, sportsbook="draftkings", captured_at_utc=at.strftime("%Y-%m-%dT%H:%M:%SZ"), provider_contract_verified=True,
                     model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent=opp, game_start_utc=start, market_probability=no_vig)


def movement(captures: list[dict], home: str, away: str, team: str, market: str, point: float | None = None) -> dict | None:
    """The side's price across every capture of the day: earliest and current price and no-vig probability, the change, its direction and size, and when the largest step happened. None with a single capture."""
    opp = away if team == home else home
    series = []
    for c in captures:
        e = next((e for e in c["events"].values() if e["home"] == home and e["away"] == away), None)
        if not e:
            continue
        a, b = (e["ml"].get(team), e["ml"].get(opp)) if market == "MONEYLINE" else (e["pl"].get(team), e["pl"].get(opp))
        if market == "PUCK_LINE":
            if not a or not b or a[1] != point:
                continue
            a, b = a[0], b[0]
        if a is None or b is None:
            continue
        series.append((c["at"], a, _no_vig(a, b)))
    if len(series) < 2:
        return None
    steps = [(series[i + 1][0], series[i + 1][2] - series[i][2]) for i in range(len(series) - 1)]
    big = max(steps, key=lambda s_: abs(s_[1]))
    d = series[-1][2] - series[0][2]
    return {"earliest_price": series[0][1], "earliest_at_utc": series[0][0].strftime("%Y-%m-%dT%H:%M:%SZ"), "current_price": series[-1][1], "current_at_utc": series[-1][0].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "earliest_no_vig": round(series[0][2], 4), "current_no_vig": round(series[-1][2], 4), "change": round(d, 4), "direction": "TOWARD" if d >= 0.01 else ("AWAY" if d <= -0.01 else "FLAT"),
            "magnitude_points": round(abs(d) * 100, 1), "largest_step_at_utc": big[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "captures": len(series)}


def price_class(american: float) -> str:
    lo, hi = POLICY["price_policy"]["preferred_region_american"]
    elo, ehi = POLICY["price_policy"]["extreme_guard_american"]
    if lo <= american <= hi:
        return "IN_PREFERRED_REGION"
    return "OUTSIDE_PREFERRED" if elo <= american <= ehi else "EXTREME"


def evaluate_game(ctx, game: dict, captures: list[dict], now: dt.datetime, capture_ctx: dict | None = None, trust_pl: dict | None = None, blend: dict | None = None, structure_state: dict | None = None) -> list[dict]:
    """Every candidate side of one game: both teams in both markets, with the hockey and market facts, the evidence-weighted probability, the price quality, the cases and the evidence structure."""
    gid, home, away = str(game["game_id"]), game["home"], game["away"]
    ev_now = next((c["events"][eid] for c in reversed(captures) for eid in c["events"] if c["events"][eid]["home"] == home and c["events"][eid]["away"] == away), None)
    if ev_now is None:
        return []
    at_now = next(c["at"] for c in reversed(captures) if any(e["home"] == home and e["away"] == away for e in c["events"].values()))
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
        trust_state = (card.get("market_trust") or {}).get("disagreement_state") or "INSUFFICIENT_EVIDENCE"
        ml_se = ((card.get("calibration_confidence") or {}).get("evidence") or {}).get("standard_error") or 0.0104
        base = {"game_id": gid, "matchup": f"{away} @ {home}", "side": team, "opponent": opp, "home": home, "away": away, "start_utc": game.get("start_utc"), "capture_at_utc": at_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "price_freshness": fresh, "model_win_probability": round(p_model, 4), "hockey_view": {k: mv.get(k) for k in ("favourite", "p_favourite", "gap", "drivers")}, "calibration": (card.get("calibration_confidence") or {}).get("level"),
                "data_completeness": (card.get("data_completeness") or {}).get("fraction"), "flags": [f["name"] for f in (card.get("contradiction") or {}).get("flags", [])], "card_thesis": (card.get("thesis") or {}).get("type"),
                "rest_days": {t: ((mv.get("indicators") or {}).get(t) or {}).get("rest_days") for t in (home, away)}, "model_trust_overall": (ctx.moneyline_trust or {}).get("state"), "blend": blend}
        out.append({**base, "market": "MONEYLINE", "line": None, "price": float(price), "implied_with_margin": round(_implied(price), 4), "market_probability_market": round(m_nv, 4), "model_probability": round(p_model, 4),
                    "model_probability_raw": round(p_model, 4), "probability_uncertainty": round(float(ml_se), 4), "calibration_note": None, "trust_state": trust_state, "movement": movement(captures, home, away, team, "MONEYLINE")})
        pl = ev_now["pl"].get(team), ev_now["pl"].get(opp)
        if pl[0] and pl[1] and abs(pl[0][1]) == PUCK and pl[1][0]:
            (pp, pt), (op, _o) = pl[0], pl[1]
            pl_nv = _no_vig(pp, op)
            u = pl_uncertainty(p_model, pt)
            if u.get("raw") is not None:
                out.append({**base, "market": "PUCK_LINE", "line": pt, "price": float(pp), "implied_with_margin": round(_implied(pp), 4), "market_probability_market": round(pl_nv, 4), "model_probability": round(u["adjusted"], 4),
                            "model_probability_raw": round(u["raw"], 4), "probability_uncertainty": u["uncertainty"], "calibration": "MEDIUM", "calibration_note": f"the goal-margin calibration over-predicted by {u['bias_in_bucket'] * 100:+.1f} points in this probability bucket out of time; the cover probability is lowered by any over-prediction",
                            "trust_state": (trust_pl or {}).get("state") or "INSUFFICIENT_EVIDENCE", "movement": movement(captures, home, away, team, "PUCK_LINE", pt)})
    for c in out:
        _qualify(c, structure_state)
    return out


def _qualify(c: dict, structure_state: dict | None = None) -> None:
    """The evidence-weighted probability, the price quality, the hard blockers, the cases and the evidence structure."""
    c["price_class"] = price_class(c["price"])
    c["gap"] = round(c["model_probability"] - c["market_probability_market"], 4)
    w, why_w = weight_for(c["trust_state"], c["gap"], c.get("blend"))
    c["model_weight"], c["model_weight_why"] = w, why_w
    c["supported_probability"] = round(c["market_probability_market"] + w * c["gap"], 4)
    c["edge"] = round(c["supported_probability"] - c["implied_with_margin"], 4)                   # may be negative: the margin is the cost of a consensus pick
    c["conservative_edge"] = round(c["edge"] - c["probability_uncertainty"], 4)
    c["margin_cost"] = round(c["implied_with_margin"] - c["market_probability_market"], 4)
    blockers = []
    if c["price_freshness"]["status"] != pf.FRESH:
        blockers.append(f"MARKET DATA QUALITY: the price is {c['price_freshness']['status']} ({c['price_freshness']['reason'][:90]})")
    if c["price_class"] == "EXTREME":
        blockers.append(f"price {c['price']:+.0f} is extreme: Eggy does not hunt prices beyond {POLICY['price_policy']['extreme_guard_american'][0]} / +{POLICY['price_policy']['extreme_guard_american'][1]} (heuristic)")
    if (c.get("data_completeness") or 0) < 0.6 or c.get("calibration") in ("INSUFFICIENT", "LOW", None):
        blockers.append(f"data completeness {c.get('data_completeness')} / calibration {c.get('calibration')} too weak to stand a pick on")
    c["blockers"] = blockers
    cs = cases(c)
    c.update(cs)
    c["balance"] = balance(cs["case_for"], cs["case_against"])
    c["structure"], c["structure_reasons"] = classify_structure(c, structure_state)
    c["qualifies"] = bool(not blockers and c["structure"] != "NONE")
    c["not_qualifying_because"] = blockers + ([] if c["structure"] != "NONE" else c["structure_reasons"])


# ------------------------------------------------------------------------------------------------ the case for and against
def _item(kind, category, strength, text):
    return {"kind": kind, "category": category, "strength": strength, "text": text}


def cases(c: dict) -> dict:
    """CASE FOR and CASE AGAINST: items {kind, category, strength, text}. Categories: MARKET, HOCKEY, PRICE, MARKET_BEHAVIOUR, MODEL, SITUATION, DATA. Strengths: STRONG / MODERATE / WEAK / UNPROVEN (shown, never counted)."""
    f, a = [], []
    M, H, g = c["market_probability_market"], c["model_probability"], c["gap"]
    proven = c["trust_state"] == "DISAGREEMENT_HAS_SIGNAL" and g > 0
    failing = c["trust_state"] == "DISAGREEMENT_IS_OVERCONFIDENCE"
    # --- market baseline
    if M >= 0.55:
        f.append(_item("MARKET_BASELINE", "MARKET", "MODERATE", f"the market (no-vig {M:.1%}) rates this side a clear favourite"))
    elif M >= 0.50:
        f.append(_item("MARKET_BASELINE", "MARKET", "WEAK", f"the market (no-vig {M:.1%}) leans to this side"))
    elif M < 0.45:
        a.append(_item("MARKET_BASELINE", "MARKET", "WEAK", f"the market (no-vig {M:.1%}) rates this side the underdog"))
    # --- hockey support
    drv = [d for d in (c["hockey_view"].get("drivers") or []) if d.get("favours") == c["side"] and abs(d.get("log_odds_toward_home", 0)) >= 0.05]
    label = c["hockey_view"].get("gap")
    fav = c["hockey_view"].get("favourite")
    if c["market"] == "MONEYLINE" or c["market"] == "PUCK_LINE":
        if fav == c["side"] and len(drv) >= 2:
            strength = "STRONG" if label in ("STRONG", "DOMINANT") else "MODERATE"
            f.append(_item("HOCKEY_STRENGTH", "HOCKEY", strength, f"the team model rates {c['side']} the better team ({c['hockey_view'].get('p_favourite'):.1%}, a {str(label).lower()} gap); drivers: " + ", ".join(d["label"] for d in drv[:3])))
        elif fav not in (None, c["side"]):
            a.append(_item("HOCKEY_FAVOURS_OPPONENT", "HOCKEY", "STRONG" if label in ("STRONG", "DOMINANT") else "MODERATE", f"the team model rates {c['opponent']} the better team ({c['hockey_view'].get('p_favourite'):.1%}, a {str(label).lower()} gap)"))
    if H >= M - 0.02 and abs(g) <= 0.05 and min(M, H) >= 0.50:
        f.append(_item("INDEPENDENT_READS_AGREE", "MODEL", "MODERATE", f"the market ({M:.1%}) and the hockey model ({H:.1%}) are independent reads and agree within {abs(g) * 100:.1f} points"))
    # --- model-market gap, by trust
    if g >= ei.EDGE_MIN:
        if proven:
            f.append(_item("MODEL_EDGE", "MODEL", "STRONG", f"the model gives {H:.1%} against the market's {M:.1%} ({g * 100:+.1f} pts) and the prospective record says disagreement of this size is real"))
        else:
            f.append(_item("MODEL_EDGE", "MODEL", "UNPROVEN", f"the model gives {H:.1%} against the market's {M:.1%} ({g * 100:+.1f} pts); not yet proven (state: {c['trust_state'].replace('_', ' ').lower()}), so it carries weight {c['model_weight']:.2f}, not the pick"))
    elif g <= -ei.EDGE_MIN:
        a.append(_item("MARKET_STRONGER", "MODEL", "MODERATE", f"the market ({M:.1%}) rates this side higher than our model ({H:.1%})"))
    if failing:
        a.append(_item("MODEL_OVERCONFIDENT", "MODEL", "MODERATE", "the prospective record shows the model overconfident where it disagrees by this much: its disagreement is a reason to doubt the model, not the market"))
    # --- situation
    rd = c["rest_days"]
    if rd.get(c["opponent"]) is not None and rd.get(c["side"]) is not None:
        if rd[c["opponent"]] <= 1 < rd[c["side"]]:
            f.append(_item("REST_ADVANTAGE", "SITUATION", "MODERATE", f"{c['opponent']} is on a back-to-back and {c['side']} is rested"))
        elif rd[c["side"]] <= 1 < rd[c["opponent"]]:
            a.append(_item("REST_DISADVANTAGE", "SITUATION", "MODERATE", f"{c['side']} is on a back-to-back and {c['opponent']} is rested"))
    # --- market behaviour: information, not a rule
    mv = c.get("movement")
    if mv and mv["direction"] != "FLAT":
        txt = (f"the price moved {mv['direction'].lower()} {c['side']} by {mv['magnitude_points']} pts ({mv['earliest_price']:+.0f} at {mv['earliest_at_utc'][11:16]}Z to {mv['current_price']:+.0f} at {mv['current_at_utc'][11:16]}Z; "
               "movement is market information, not called sharp money, and has not yet been validated")
        (f if mv["direction"] == "TOWARD" else a).append(_item("PRICE_MOVEMENT", "MARKET_BEHAVIOUR", "MODERATE" if mv["magnitude_points"] >= 2.0 else "WEAK", txt + ")"))
    # --- price
    if c["price_class"] == "IN_PREFERRED_REGION":
        f.append(_item("PRICE_REGION", "PRICE", "WEAK", f"{c['price']:+.0f} is inside the preferred near-even region"))
    elif c["price_class"] == "OUTSIDE_PREFERRED":
        a.append(_item("PRICE_REGION", "PRICE", "MODERATE", f"{c['price']:+.0f} is outside the preferred near-even region (heuristic preference)"))
    if c["edge"] > 0:
        f.append(_item("PRICE_QUALITY", "PRICE", "MODERATE" if c["edge"] >= ei.EDGE_MIN else "WEAK", f"the evidence-weighted probability ({c['supported_probability']:.1%}) is above the break-even ({c['implied_with_margin']:.1%}) by {c['edge'] * 100:+.1f} pts"))
    a.append(_item("MARGIN", "PRICE", "WEAK", f"the bookmaker's margin costs about {c['margin_cost'] * 100:.1f} points of break-even on this side"))
    # --- freshness, data, calibration
    fr = c["price_freshness"]
    if fr["status"] != pf.FRESH:
        a.append(_item("STALE_PRICE", "DATA", "STRONG", f"MARKET DATA QUALITY: {fr['reason']}"))
    if c["market"] == "PUCK_LINE":
        a.append(_item("PUCK_LINE_CALIBRATION", "MODEL", "MODERATE", (c.get("calibration_note") or "the puck-line probability is mapped from the win probability through a goal-margin calibration") + f"; its uncertainty is {c['probability_uncertainty'] * 100:.1f} points and its agreement with the puck-line MARKET has no prospective record yet"))
    if (c.get("data_completeness") or 1) < 0.7:
        a.append(_item("DATA_COMPLETENESS", "DATA", "MODERATE", f"data completeness is {c.get('data_completeness')}"))
    a.append(_item("GOALIE", "DATA", "WEAK", "starting goalies are not confirmed by an approved source (not available to Eggy)"))
    return {"case_for": f, "case_against": a}


def balance(f: list[dict], a: list[dict]) -> dict:
    """Does the case FOR materially exceed the case AGAINST? Ordinal and countable (a HEURISTIC rule, not a weighted score): no STRONG reason against; reasons FOR at MODERATE or better from at least two distinct
    evidence CATEGORIES; and (strong + moderate for) at least (2 x strong + moderate against) + 2. UNPROVEN and WEAK items are shown and not counted."""
    n = lambda xs, s: sum(1 for x in xs if x["strength"] == s)
    fs, fm, as_, am = n(f, "STRONG"), n(f, "MODERATE"), n(a, "STRONG"), n(a, "MODERATE")
    cats = sorted({x["category"] for x in f if x["strength"] in ("STRONG", "MODERATE")})
    ok = as_ == 0 and len(cats) >= 2 and (fs + fm) >= (2 * as_ + am) + 2
    return {"for_strong": fs, "for_moderate": fm, "against_strong": as_, "against_moderate": am, "supporting_categories": cats, "materially_outweighs": bool(ok)}


# ------------------------------------------------------------------------------------------------ the evidence structures
def classify_structure(c: dict, structure_state: dict | None = None) -> tuple[str, list[str]]:
    """TYPE B, then C, then A (the strongest applicable); NONE with the reasons when none applies. A structure the prospective record has shown to FAIL does not qualify."""
    th = POLICY["thresholds"]
    M, H, g = c["market_probability_market"], c["model_probability"], c["gap"]
    bal = c["balance"]
    drv = [d for d in (c["hockey_view"].get("drivers") or []) if d.get("favours") == c["side"] and abs(d.get("log_odds_toward_home", 0)) >= 0.05]
    reasons = []
    if not bal["materially_outweighs"]:
        reasons.append("the case for does not materially exceed the case against (" + ", ".join(f"{k} {v}" for k, v in bal.items() if k != "supporting_categories") + f"; supporting categories {bal['supporting_categories']})")
    failed = lambda s: ((structure_state or {}).get(s) or {}).get("decision_state") == "DISAGREEMENT_IS_OVERCONFIDENCE"
    kinds = []
    if c["trust_state"] == "DISAGREEMENT_HAS_SIGNAL" and g >= th["model_gap_material"] and c["edge"] >= ei.EDGE_MIN:
        kinds.append("SUPPORTED_MARKET_GAP")
    else:
        reasons.append("no SUPPORTED market gap: " + (f"the model's {g * 100:+.1f}-pt gap is unproven ({c['trust_state'].replace('_', ' ').lower()})" if g >= th["model_gap_material"] else f"the model's gap is {g * 100:+.1f} pts"))
    mvm = c.get("movement") or {}
    toward = mvm.get("direction") == "TOWARD" and abs(mvm.get("change", 0)) >= th["movement_material"]
    away = mvm.get("direction") == "AWAY" and abs(mvm.get("change", 0)) >= th["movement_material"]
    facts = [x for x, on in (("price moved toward the side", toward), ("rest advantage", any(i["kind"] == "REST_ADVANTAGE" for i in c["case_for"])), ("evidence-weighted probability above break-even", c["edge"] > 0)) if on]
    if H >= M - 0.02 and len(drv) >= 2 and len(facts) >= 2 and not away and c["edge"] >= th["situational_min_edge"]:
        kinds.append("MARKET_SITUATIONAL_OPPORTUNITY")
    elif not (H >= M - 0.02 and len(drv) >= 2):
        reasons.append("the hockey picture does not support this side as a situational opportunity")
    else:
        reasons.append(f"no market / situational opportunity: {len(facts)} measured fact(s) of the two needed ({', '.join(facts) or 'none'})" + ("; the price moved AWAY from the side" if away else "") + (f"; the price is {abs(c['edge']) * 100:.1f} pts worse than the evidence supports (limit {abs(th['situational_min_edge']) * 100:.0f})" if c["edge"] < th["situational_min_edge"] else ""))
    if M >= th["consensus_min_market"] and H >= th["consensus_min_model"] and abs(g) <= th["consensus_agreement_max_gap"] and c["price_class"] == "IN_PREFERRED_REGION" and len(drv) >= 2:
        kinds.append("CONSENSUS_QUALITY")
    else:
        reasons.append(f"not a consensus side: market {M:.1%} / model {H:.1%} / gap {g * 100:+.1f} pts / price {c['price']:+.0f}" + ("" if len(drv) >= 2 else " / fewer than two hockey drivers behind it"))
    kinds = [k for k in kinds if not failed(k)]
    if bal["materially_outweighs"] and kinds:
        return min(kinds, key=lambda k: PRIORITY[k]), []
    return "NONE", reasons


def confidence(c: dict, structure_state: dict | None = None) -> str:
    """A calibrated evidence CATEGORY, never a percentage. Proven market gap with nothing moderate against -> HIGH; a proven gap, or a consensus / situational pick with three supporting categories, at most one moderate
    reason against and HIGH calibration -> MEDIUM; otherwise LOW. A structure whose prospective record has proven itself is lifted one category; one that has failed does not qualify at all."""
    bal = c["balance"]
    if c["structure"] == "SUPPORTED_MARKET_GAP":
        lvl = "HIGH" if bal["against_moderate"] == 0 and c["calibration"] == "HIGH" and c["edge"] >= 2 * ei.EDGE_MIN else "MEDIUM"
    elif len(bal["supporting_categories"]) >= 3 and bal["against_moderate"] <= 1 and c["calibration"] == "HIGH":
        lvl = "MEDIUM"
    else:
        lvl = "LOW"
    if ((structure_state or {}).get(c["structure"]) or {}).get("decision_state") == "DISAGREEMENT_HAS_SIGNAL":
        lvl = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "HIGH"}[lvl]
    return lvl


def research_order(c: dict) -> tuple:
    """How Eggy ranks sides: qualifying first, then the evidence structure (gap, situational, consensus), then the preferred price region, then the CONSERVATIVE supported edge (the less trustworthy probability does not win on a
    larger nominal edge), then the case balance; the moneyline wins a tie. Nothing that happened after the decision is an input."""
    b = c["balance"]
    net = b["for_strong"] * 2 + b["for_moderate"] - b["against_strong"] * 2 - b["against_moderate"]
    if not c["qualifies"]:                                             # among the sides that fail, the one with the best case is the most researched (a side the evidence opposes is not)
        return (True, 0, -net, c["price_class"] != "IN_PREFERRED_REGION", -round(c["conservative_edge"], 3), c["market"] != "MONEYLINE")
    return (False, PRIORITY.get(c["structure"], 9), c["price_class"] != "IN_PREFERRED_REGION", -round(c["conservative_edge"], 3), -net, c["market"] != "MONEYLINE")


def summarize(c: dict) -> dict:
    keep = ("matchup", "side", "market", "line", "price", "model_probability", "model_probability_raw", "market_probability_market", "gap", "trust_state", "model_weight", "supported_probability", "edge", "conservative_edge",
            "probability_uncertainty", "price_class", "structure", "qualifies", "not_qualifying_because", "balance", "movement", "calibration", "calibration_note")
    hockey = [x for x in c["case_for"] if x["category"] in ("HOCKEY", "MODEL", "SITUATION")]
    market = [x for x in c["case_for"] if x["category"] in ("MARKET", "MARKET_BEHAVIOUR", "PRICE")]
    return {**{k: c.get(k) for k in keep}, "hockey_case": [x["text"] for x in hockey], "market_price_case": [x["text"] for x in market], "case_against": [x["text"] for x in c["case_against"] if x["strength"] != "WEAK"] or [x["text"] for x in c["case_against"][:2]],
            "price_freshness": c["price_freshness"]["status"]}


def decide(candidates: list[dict], *, now: dt.datetime, why_no_prices: str | None = None, structure_state: dict | None = None) -> dict:
    """The day's decision: rank every side by `research_order` (nothing that happened after the decision is an input), take the best qualifying one, else PASS with the specific reasons; the top three researched sides are always reported."""
    base = {"policy_id": POLICY["policy_id"], "policy_digest": policy_digest(), "decided_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "availability": AVAILABILITY}
    if why_no_prices:
        return {**base, "decision": "PASS", "pass_reason": why_no_prices, "ranked": [], "top_three": [], "candidates_examined": 0}
    ranked = sorted(candidates, key=research_order)
    top = [summarize(c) for c in ranked[:3]]
    pick = next((c for c in ranked if c["qualifies"]), None)
    if pick:
        pick["confidence"] = confidence(pick, structure_state)
        return {**base, "decision": "PICK", "pick": pick, "ranked": [summarize(c) for c in ranked[:5]], "top_three": top, "candidates_examined": len(candidates)}
    if ranked:
        t = ranked[0]
        pass_reason = (f"no side meets the standard. The most researched side, {t['side']} {t['market'].replace('_', ' ')} {t['price']:+.0f}, does not qualify: " + "; ".join(t["not_qualifying_because"][:3])
                       + f". Total evidence for it: {t['balance']['for_strong']} strong / {t['balance']['for_moderate']} moderate; against: {t['balance']['against_strong']} strong / {t['balance']['against_moderate']} moderate.")
    else:
        pass_reason = "no game has a current moneyline and puck-line price"
    return {**base, "decision": "PASS", "pass_reason": pass_reason, "ranked": [summarize(c) for c in ranked[:5]], "top_three": top, "candidates_examined": len(candidates),
            "what_would_change_it": "a side where the hockey picture and the market agree it is the better team near even money, or where two measured, independent facts (movement toward it, a rest advantage, a gap the record has earned weight for) make the price unusually attractive, with the case for clearly exceeding the case against"}
