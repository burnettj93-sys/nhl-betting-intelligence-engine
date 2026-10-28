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
MOVE_PATH = REPO / "docs" / "validation" / "eggy_movement_profile.json"


def movement_profile() -> dict:
    """The outcome-free profile of ordinary same-day moneyline movement (deploy/build_eggy_movement_profile.py): the percentiles that say what counts as unusual."""
    try:
        return json.loads(MOVE_PATH.read_text())
    except (OSError, ValueError):
        return {"abs_changes": [], "percentiles": {"p50": 0.0087, "p75": 0.0195, "p90": 0.0297}, "games": 0, "rules": {"min_span_hours_between_first_and_last": 4.0}}


_MP = movement_profile()
POLICY = {"policy_id": "EGGY_SIDE_PICK_V2", "unit": "SIDE", "max_outputs_per_day": 1, "markets": ["MONEYLINE", "PUCK_LINE"], "no": ["player props", "player points", "shots", "parlays"],
          "decision_hierarchy": ["A market baseline (no-vig prior)", "B hockey support", "C price quality against the evidence-weighted probability", "D market behaviour (movement across captures)", "E model-market gap, weighted by its prospective trust",
                                 "F case for versus case against"],
          "evidence_structures": {"B": "SUPPORTED_MARKET_GAP", "C": "MARKET_SITUATIONAL_OPPORTUNITY"},
          "consensus_quality": "a STATE (market and hockey agree this is a strong, well-priced side), never a pick on its own: it needs an independent edge family beside it",
          "evidence_families": {"HOCKEY_MODEL": "one family: team probability, xG / goal differential, high-danger, home ice and every other input of the same team model",
                                "MARKET_PRICE": "one family: the price, its movement, direction and size (the same market fact expressed twice counts once)",
                                "SITUATIONAL": "rest advantage, back-to-back, schedule (only where independent of the team model)", "MATCHUP_PERSONNEL": "PERSONNEL / AVAILABILITY, one family: starting-goalie identity and status (CONFIRMED / PROBABLE / UNKNOWN / SOURCE CONFLICT, from GoaliePost with GameDayTweets corroboration when the owner has enabled that feed) and player availability from attributable recorded reports (named player, team, explicit status, named source and time), classed MATERIAL / MODERATE / DEPTH from the engine's own ice-time and scoring data. Automatic injury data is NOT AVAILABLE: no injury page is read automatically (NHL.com, CBS and FOX prohibit it in their terms). Personnel is context and uncertainty, never a win-probability adjustment and never an edge by itself; the facts of one underlying absence or one goalie question are one reason, and the family counts once",
                                "PROVEN_SIGNAL": "a model-market disagreement that has earned prospective trust"},
          "edge_thesis": "every pick must say why this side at this price beats accepting the market as correct: a PROVEN_SIGNAL, or the hockey family together with at least one independent EDGE family (supported market movement or a situational advantage); the hockey model and the market agreeing is a description of probability, not an edge",
          "movement_cap": "until the movement regime itself shows signal, movement may support a thesis but cannot produce a HIGH-confidence pick",
          "price_policy": {"preferred_region_american": [-130, 130], "preferred_region_status": "HEURISTIC preference (a ranking factor and a reason against when outside); not validated",
                           "extreme_guard_american": [-200, 200], "extreme_guard_status": "HEURISTIC: Eggy does not hunt extreme prices (a -300 favourite is not what it is for); not validated",
                           "no_edge_requirement": "a pick does not need a positive supported edge: it is labelled honestly and pays the margin; extreme prices are ranked last and cap the confidence at LOW"},
          "model_weight": {"proven": 1.0, "failing": 0.0, "accumulating": "the blend estimate k of the prospective record (market + k (model - market)), bounded to [0, 0.5]", "status": "k is measured; the bounds are heuristic"},
          "balance": {"rule": "no STRONG reason against; at least two INDEPENDENT evidence families supporting at MODERATE or better (each family counts once, at its strongest); (strong + moderate for, one per family) >= (2 x strong + moderate against) + 2", "status": "HEURISTIC ordinal rule, not a weighted score"},
          "thresholds": {"model_gap_material": ei.EDGE_MIN, "consensus_agreement_max_gap": 0.05, "consensus_min_market": 0.50, "consensus_min_model": 0.52, "situational_min_edge": -0.01, "status": "HEURISTIC except the engine's own edge threshold"},
          "movement": {"method": "PERCENTILE CATEGORIES of the engine's own history of ordinary same-day moneyline movement (outcome-free; prices only), applied to the team's moneyline no-vig probability across the day's captures",
                       "profile": {"games": _MP.get("games"), "snapshots_before": _MP.get("snapshots_before"), "p50": _MP["percentiles"]["p50"], "p75": _MP["percentiles"]["p75"], "p90": _MP["percentiles"]["p90"]},
                       "classes": {"NOISE": "|change| below p50: ordinary drift, not shown as evidence", "MODEST": "p50 to p75: shown, never counted", "NOTABLE": "p75 and above: counts as the market-price edge family (MODERATE)",
                                   "EXCEPTIONAL": "p90 and above: counts the same (movement is never STRONG until its own regime proves out)", "UNRELIABLE": "fewer than two captures or captures less than the profile's minimum span apart"},
                       "status": "the percentiles are measured; calling the upper quartile 'unusual' is a CONVENTION; predictive value is not claimed and is measured prospectively"},
          "ranking": {"order": ["unusable market (stale / missing / invalid) is rejected", "a side with major evidence AGAINST (a STRONG reason against, or the hockey model favouring the opponent) ranks behind", "a real edge thesis ranks ahead",
                                "then the QUALITY score: independent families once each (support minus against), calibration, uncertainty, conservative edge, data completeness, and a bounded near-even price preference", "the moneyline wins a tie, then the price nearer even money"],
                      "quality_weights": {"strong": 2.0, "moderate": 1.0, "calibration_medium": -0.5, "calibration_below": -1.0, "data_incomplete": -0.5, "uncertainty_per_probability_point": -0.1, "conservative_edge_per_probability_point": 0.2,
                                          "outside_preferred": -0.75, "extreme": -2.0}, "status": "HEURISTIC ordinal weights: price is bounded below one MODERATE step so it cannot dominate a clearly better case"},
          "decision_time": "once a day, after the pregame league pull is observed and before the first game (or PASS: no current prices)", "freshness": "FRESH only (operational/price_freshness.py)",
          "daily_bet": "EXACTLY ONE bet every NHL slate day with a valid fresh market, moneyline or puck line, chosen from the whole ranked slate; weak evidence lowers the confidence, it never removes the bet",
          "pass": "only when there is no NHL game, no valid fresh sportsbook market, or a technical / data failure prevents a legitimate evaluation", "record": "own ledger, own postmortem, own evidence-structure and signal trust (operational/eggy_pick_ledger.py)"}
PUCK = 1.5
CAL_PATH = REPO / "docs" / "validation" / "eggy_pick_calibration.json"
STRUCTURES = ("SUPPORTED_MARKET_GAP", "MARKET_SITUATIONAL_OPPORTUNITY")
PRIORITY = {"SUPPORTED_MARKET_GAP": 0, "MARKET_SITUATIONAL_OPPORTUNITY": 1}
KIND_FAMILY = {"MARKET_BASELINE": "MARKET_PRICE", "PRICE_MOVEMENT": "MARKET_PRICE", "PRICE_REGION": "MARKET_PRICE", "PRICE_QUALITY": "MARKET_PRICE", "MARGIN": "MARKET_PRICE", "STALE_PRICE": "MARKET_PRICE",
               "HOCKEY_STRENGTH": "HOCKEY_MODEL", "HOCKEY_FAVOURS_OPPONENT": "HOCKEY_MODEL", "INDEPENDENT_READS_AGREE": "HOCKEY_MODEL", "MODEL_EDGE": "HOCKEY_MODEL", "MARKET_STRONGER": "HOCKEY_MODEL",
               "MODEL_OVERCONFIDENT": "HOCKEY_MODEL", "PUCK_LINE_CALIBRATION": "HOCKEY_MODEL", "REST_ADVANTAGE": "SITUATIONAL", "REST_DISADVANTAGE": "SITUATIONAL", "DATA_COMPLETENESS": "DATA", "GOALIE_CONFIRMED": "MATCHUP_PERSONNEL", "GOALIE_UNCERTAIN": "MATCHUP_PERSONNEL", "GOALIE_UNKNOWN": "MATCHUP_PERSONNEL", "GOALIE_CONFLICT": "MATCHUP_PERSONNEL",
               "PERSONNEL_ABSENCE_OPPONENT": "MATCHUP_PERSONNEL", "PERSONNEL_AVAILABILITY_AGAINST": "MATCHUP_PERSONNEL", "PERSONNEL_UNKNOWN": "MATCHUP_PERSONNEL"}


def policy_digest() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:16]


AVAILABILITY = {
    "AVAILABLE": ["moneyline and puck-line (+/-1.5) prices, DraftKings, from the league pulls (morning and pregame)", "every captured price per side across the day: earliest, current, direction, size and time of movement (the first capture is NOT the true opening line)",
                  "authoritative hockey probability (eggy-matchup-v1), expected-goal / goal / high-danger indicators with league ranks, rest and back-to-back, special teams, recent games",
                  "the prospective model-versus-market record (one observation per game)", "price freshness"],
    "NOT AVAILABLE": ["ticket percentages", "handle / money percentages", "ticket-versus-handle divergence", "reverse line movement and market resistance (they need the splits)", "cross-book disagreement (one book only)",
                      "the true opening line and the closing line (only the captures the engine took)", "automatic injury and lineup information (no free source permits automated reading; only attributable recorded reports are used, and a team with none is shown as NOT AVAILABLE, never as healthy)", "game total / scoring environment (only moneyline and puck-line markets are captured)", "a validated expected-goal-margin or variance estimate beyond the goal-margin calibration of the win probability"],
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


def classify_move(abs_change: float, span_hours: float, captures: int) -> tuple[str, float]:
    """(class, percentile) of one move against the profile of ordinary same-day moves."""
    pr = _MP["percentiles"]
    ch = _MP.get("abs_changes") or []
    pctile = round(sum(1 for x in ch if x <= abs_change) / len(ch), 3) if ch else None
    if captures < 2 or span_hours < float((_MP.get("rules") or {}).get("min_span_hours_between_first_and_last", 4.0)):
        return "UNRELIABLE", pctile
    return ("NOISE" if abs_change < pr["p50"] else "MODEST" if abs_change < pr["p75"] else "NOTABLE" if abs_change < pr["p90"] else "EXCEPTIONAL"), pctile


def movement(captures: list[dict], home: str, away: str, team: str, market: str, point: float | None = None) -> dict | None:
    """The side's price across every capture of the day: earliest and current price and no-vig probability, the change, its direction and size, when the largest step happened, and how unusual the size is (the
    percentile category against the profile of ordinary same-day moves, with its reliability). None with a single capture."""
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
    span = (series[-1][0] - series[0][0]).total_seconds() / 3600
    klass, pct = classify_move(abs(d), span, len(series))
    return {"earliest_price": series[0][1], "earliest_at_utc": series[0][0].strftime("%Y-%m-%dT%H:%M:%SZ"), "current_price": series[-1][1], "current_at_utc": series[-1][0].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "earliest_no_vig": round(series[0][2], 4), "current_no_vig": round(series[-1][2], 4), "change": round(d, 4), "direction": "TOWARD" if d > 0 else ("AWAY" if d < 0 else "FLAT"),
            "magnitude_points": round(abs(d) * 100, 1), "largest_step_at_utc": big[0].strftime("%Y-%m-%dT%H:%M:%SZ"), "captures": len(series), "span_hours": round(span, 1), "class": klass, "percentile": pct}


def price_class(american: float) -> str:
    lo, hi = POLICY["price_policy"]["preferred_region_american"]
    elo, ehi = POLICY["price_policy"]["extreme_guard_american"]
    if lo <= american <= hi:
        return "IN_PREFERRED_REGION"
    return "OUTSIDE_PREFERRED" if elo <= american <= ehi else "EXTREME"


def evaluate_game(ctx, game: dict, captures: list[dict], now: dt.datetime, capture_ctx: dict | None = None, trust_pl: dict | None = None, blend: dict | None = None, structure_state: dict | None = None, goalies: dict | None = None, personnel: dict | None = None) -> list[dict]:
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
                "rest_days": {t: ((mv.get("indicators") or {}).get(t) or {}).get("rest_days") for t in (home, away)}, "model_trust_overall": (ctx.moneyline_trust or {}).get("state"), "blend": blend, "goalies": goalies,
                "personnel": None if personnel is None else {"side": personnel.get(team), "opponent": personnel.get(opp)}}
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
                            "trust_state": (trust_pl or {}).get("state") or "INSUFFICIENT_EVIDENCE", "movement": movement(captures, home, away, team, "MONEYLINE"),
                            "own_market_movement": movement(captures, home, away, team, "PUCK_LINE", pt)})
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
    c["eligible"] = c["price_freshness"]["status"] == pf.FRESH                                     # the only hard gate for the daily bet: a valid fresh market
    cs = cases(c)
    c.update(cs)
    c["balance"] = balance(cs["case_for"], cs["case_against"])
    c["edge_families"] = edge_families(c)
    c["structure"], c["structure_reasons"] = classify_structure(c, structure_state)
    c["edge_thesis"] = edge_thesis(c)
    c["qualifies"] = bool(not blockers and c["structure"] != "NONE")
    c["not_qualifying_because"] = blockers + ([] if c["structure"] != "NONE" else c["structure_reasons"])


# ------------------------------------------------------------------------------------------------ the case for and against
def _item(kind, category, strength, text):
    family = "PROVEN_SIGNAL" if (kind == "MODEL_EDGE" and strength == "STRONG") else KIND_FAMILY.get(kind, category)
    return {"kind": kind, "category": category, "family": family, "strength": strength, "text": text}


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
    if mv and mv["direction"] != "FLAT" and mv.get("class") in ("MODEST", "NOTABLE", "EXCEPTIONAL"):
        big = mv["class"] in ("NOTABLE", "EXCEPTIONAL")
        txt = (f"the team's moneyline price moved {mv['direction'].lower()} {c['side']} by {mv['magnitude_points']} pts ({mv['earliest_price']:+.0f} at {mv['earliest_at_utc'][11:16]}Z to {mv['current_price']:+.0f} at {mv['current_at_utc'][11:16]}Z): "
               f"a {mv['class']} move, larger than {(mv.get('percentile') or 0):.0%} of ordinary same-day moves; market information, not called sharp money, and not yet validated as predictive")
        (f if mv["direction"] == "TOWARD" else a).append(_item("PRICE_MOVEMENT", "MARKET_BEHAVIOUR", "MODERATE" if big else "WEAK", txt))
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
    it = goalie_item(c)
    if it:
        (f if it["kind"] == "GOALIE_CONFIRMED" else a).append(it)
    pf_, pa_ = personnel_items(c)
    f.extend(pf_), a.extend(pa_)
    return {"case_for": f, "case_against": a}


def _goalie_lines(pick: dict) -> dict:
    g = pick.get("goalies")
    if not g:
        return {}
    from operational import goalie_sources
    return {**goalie_sources.describe(g), "state": g["state"], "retrieved_at_utc": g.get("retrieved_at_utc")}


def _names(ps: list[dict]) -> str:
    return ", ".join(f"{p['name']} ({p['position'] or '?'}, {p['status'].replace('_', ' ')}; {p['source']}, {p['source_at_utc'][5:16].replace('T', ' ')}Z)" for p in ps)


def personnel_items(c: dict) -> tuple[list[dict], list[dict]]:
    """PERSONNEL / AVAILABILITY from attributable injury reports: AT MOST ONE reason FOR (the opponent is missing important players) and ONE AGAINST (this side is missing them, or an important player on
    either team is in doubt or the sources conflict), however many facts lie behind each. Several MATERIAL absences on one team make that one reason STRONGER, never a second reason. Never a probability change
    and never an edge: it is evidence, uncertainty and confidence. Silent when no injury report is wired; a team with no recorded report is stated NOT AVAILABLE, never healthy."""
    p = c.get("personnel")
    if p is None:
        return [], []
    own, opp = p["side"] or {"coverage": "NONE", "material_absent": [], "moderate_absent": [], "material_uncertain": [], "conflicts": [], "relevant": []}, p["opponent"] or {"coverage": "NONE", "material_absent": [], "moderate_absent": [], "material_uncertain": [], "conflicts": [], "relevant": []}
    f, a = [], []
    strength = lambda v: "STRONG" if len(v["material_absent"]) >= 2 else ("MODERATE" if v["material_absent"] else ("WEAK" if v["moderate_absent"] else None))
    s_opp = strength(opp)
    if s_opp:
        f.append(_item("PERSONNEL_ABSENCE_OPPONENT", "SITUATION", s_opp, f"{c['opponent']} is missing {_names(opp['material_absent'] + opp['moderate_absent'])}: evidence for {c['side']}, not a priced edge"))
    reasons, s_own = [], strength(own)
    if s_own:
        reasons.append((s_own, f"{c['side']} is missing {_names(own['material_absent'] + own['moderate_absent'])}"))
    doubt = own["material_uncertain"] + opp["material_uncertain"]
    if doubt:
        reasons.append(("WEAK", f"an important player is in doubt ({_names(doubt)}): the picture may change before puck drop"))
    conflicts = [x for x in own["conflicts"] + opp["conflicts"] if x["impact"] == "MATERIAL"]
    if conflicts:
        reasons.append(("WEAK", "SOURCE CONFLICT on " + "; ".join(f"{x['name']} ({x['conflict']})" for x in conflicts) + ": uncertainty is retained"))
    if reasons:
        top = max((r[0] for r in reasons), key=lambda k: {"WEAK": 0, "MODERATE": 1, "STRONG": 2}[k])
        a.append(_item("PERSONNEL_AVAILABILITY_AGAINST", "SITUATION", top, "; ".join(r[1] for r in reasons)))
    if own["coverage"] == "NONE" and opp["coverage"] == "NONE":
        a.append(_item("PERSONNEL_UNKNOWN", "DATA", "WEAK", "injury information is not available for either team: no attributable report was recorded, so absences are unknown, not absent"))
    return f, a


def personnel_uncertain(c: dict) -> bool:
    """An important player in doubt, or a material-player source conflict, on either team (one confidence step with the goalie question, never two)."""
    p = c.get("personnel")
    if not p:
        return False
    return any(v and (v["material_uncertain"] or any(x["impact"] == "MATERIAL" for x in v["conflicts"])) for v in (p["side"], p["opponent"]))


def _personnel_lines(pick: dict) -> dict:
    p = pick.get("personnel")
    if not p:
        return {}
    from operational import personnel as pers
    return {v["team"]: pers.describe(v) for v in (p["side"], p["opponent"]) if v}


def goalie_item(c: dict) -> dict | None:
    """ONE personnel reason for the game's starting goalies, however many facts lie behind it (probable + not confirmed + unknown is still one reason). Context and uncertainty only: WEAK, so it never
    carries a pick and never counts toward the balance; a source conflict or an unknown goalie also caps confidence (see confidence)."""
    g = c.get("goalies")
    if not g:
        return _item("GOALIE_UNKNOWN", "DATA", "WEAK", "starting goalies are unknown: no goalie feed was available at decision time")
    sides = g["sides"]
    names = {t: (sides[k]["goalie"] or "no goalie reported") for k, t in (("AWAY", g["away"]), ("HOME", g["home"]))}
    detail = "; ".join(f"{t} {names[t]} {sides[k]['status']}" for k, t in (("AWAY", g["away"]), ("HOME", g["home"])))
    chg = " A listed starter changed after it was first seen." if g.get("late_change") else ""
    st = g["state"]
    if st == "BOTH_CONFIRMED":
        return _item("GOALIE_CONFIRMED", "DATA", "WEAK", f"both starting goalies are confirmed ({detail}): goalie uncertainty is removed.{chg}")
    if st == "SOURCE_CONFLICT":
        why = next((sides[k]["conflict"] for k in sides if sides[k]["conflict"]), "sources disagree")
        return _item("GOALIE_CONFLICT", "DATA", "WEAK", f"SOURCE CONFLICT on a starting goalie ({why}); uncertainty is retained ({detail}).{chg}")
    if st == "UNKNOWN_GOALIE":
        return _item("GOALIE_UNKNOWN", "DATA", "WEAK", f"a starting goalie is unknown or the listing is stale ({detail}); uncertainty is retained.{chg}")
    return _item("GOALIE_UNCERTAIN", "DATA", "WEAK", f"a starting goalie is only probable, not confirmed ({detail}).{chg}")


def balance(f: list[dict], a: list[dict]) -> dict:
    """Does the case FOR materially exceed the case AGAINST? Ordinal and countable (a HEURISTIC rule, not a weighted score). Reasons FOR are grouped into INDEPENDENT FAMILIES and each family counts ONCE, at its strongest
    item: several facts from one team model (or one price expressed twice) are one reason. Rule: no STRONG reason against; MODERATE-or-better support from at least two families; and (strong + moderate for, one per family)
    at least (2 x strong + moderate against) + 2. UNPROVEN and WEAK items are shown and not counted."""
    rank = {"STRONG": 2, "MODERATE": 1}
    best = {}
    for x in f:
        fam = x.get("family") or x.get("category")
        if x["strength"] in rank and rank[x["strength"]] > rank.get(best.get(fam), 0):
            best[fam] = x["strength"]
    fs, fm = sum(1 for v in best.values() if v == "STRONG"), sum(1 for v in best.values() if v == "MODERATE")
    n = lambda xs, k: sum(1 for x in xs if x["strength"] == k)
    as_, am = n(a, "STRONG"), n(a, "MODERATE")
    ok = as_ == 0 and len(best) >= 2 and (fs + fm) >= (2 * as_ + am) + 2
    return {"for_strong": fs, "for_moderate": fm, "against_strong": as_, "against_moderate": am, "supporting_categories": sorted(best), "supporting_families": sorted(best), "materially_outweighs": bool(ok)}


# ------------------------------------------------------------------------------------------------ the evidence structures
def hockey_support(c: dict) -> bool:
    """The hockey family stands behind the side: the team model is not below the market and at least two of its drivers favour the side."""
    drv = [d for d in (c["hockey_view"].get("drivers") or []) if d.get("favours") == c["side"] and abs(d.get("log_odds_toward_home", 0)) >= 0.05]
    return c["model_probability"] >= c["market_probability_market"] - 0.02 and len(drv) >= 2


def consensus_quality(c: dict) -> bool:
    """A STATE, not a pick: the market and the hockey model broadly agree this is a strong side at a near-even price. It describes probability; it is not evidence that the price is attractive."""
    th = POLICY["thresholds"]
    return bool(c["market_probability_market"] >= th["consensus_min_market"] and c["model_probability"] >= th["consensus_min_model"] and abs(c["gap"]) <= th["consensus_agreement_max_gap"]
                and c["price_class"] == "IN_PREFERRED_REGION" and hockey_support(c))


def edge_families(c: dict) -> dict:
    """The INDEPENDENT EDGE families that argue the price understates the side, and those that argue against it. Edge families: PROVEN_SIGNAL (a model-market gap with earned prospective trust), MARKET_PRICE movement toward
    the side by an UNUSUAL amount (the profile's upper quartile or more: a single price tick is not enough), SITUATIONAL (a rest advantage). MATCHUP_PERSONNEL is not available. The hockey model, the market's agreement and the price quality are NOT edge
    families: they restate probability."""
    th = POLICY["thresholds"]
    mvm = c.get("movement") or {}
    present, against = [], []
    if c["trust_state"] == "DISAGREEMENT_HAS_SIGNAL" and c["gap"] >= th["model_gap_material"] and c["edge"] >= ei.EDGE_MIN:
        present.append({"family": "PROVEN_SIGNAL", "text": f"a model-market gap of {c['gap'] * 100:+.1f} pts that the prospective record has proven"})
    unusual = mvm.get("class") in ("NOTABLE", "EXCEPTIONAL")
    if mvm.get("direction") == "TOWARD" and unusual:
        present.append({"family": "MARKET_PRICE", "text": f"the price moved toward the side by {mvm['magnitude_points']} pts ({mvm['earliest_price']:+.0f} to {mvm['current_price']:+.0f}), a {mvm['class']} move ({(mvm.get('percentile') or 0):.0%} percentile of ordinary same-day moves); prospective, not yet validated, not called sharp money"})
    if any(i["kind"] == "REST_ADVANTAGE" for i in c["case_for"]):
        present.append({"family": "SITUATIONAL", "text": next(i["text"] for i in c["case_for"] if i["kind"] == "REST_ADVANTAGE")})
    if mvm.get("direction") == "AWAY" and unusual:
        against.append({"family": "MARKET_PRICE", "text": f"the price moved AWAY from the side by {mvm['magnitude_points']} pts, a {mvm['class']} move"})
    if any(i["kind"] == "REST_DISADVANTAGE" for i in c["case_against"]):
        against.append({"family": "SITUATIONAL", "text": next(i["text"] for i in c["case_against"] if i["kind"] == "REST_DISADVANTAGE")})
    return {"present": present, "against": against}


def classify_structure(c: dict, structure_state: dict | None = None) -> tuple[str, list[str]]:
    """The edge thesis: WHY is this side at this price better than accepting the market as correct? TYPE B (a proven model-market gap with a positive edge) or TYPE C (the hockey family AND at least one independent edge
    family, with a price no worse than the evidence supports). Consensus quality on its own, or any number of facts from the hockey model, is NONE. A structure the record has shown to FAIL does not qualify."""
    th = POLICY["thresholds"]
    bal = c["balance"]
    ef = c["edge_families"]
    reasons = []
    if not bal["materially_outweighs"]:
        reasons.append("the case for does not materially exceed the case against (" + ", ".join(f"{k} {v}" for k, v in bal.items() if k not in ("supporting_categories", "supporting_families")) + f"; independent families supporting {bal['supporting_families']})")
    failed = lambda s: ((structure_state or {}).get(s) or {}).get("decision_state") == "DISAGREEMENT_IS_OVERCONFIDENCE"
    kinds = []
    names = [e["family"] for e in ef["present"]]
    if "PROVEN_SIGNAL" in names:
        kinds.append("SUPPORTED_MARKET_GAP")
    elif c["gap"] >= th["model_gap_material"]:
        reasons.append(f"the model's {c['gap'] * 100:+.1f}-pt gap is unproven ({c['trust_state'].replace('_', ' ').lower()}): context only")
    if hockey_support(c) and any(n in ("MARKET_PRICE", "SITUATIONAL") for n in names) and not ef["against"] and c["edge"] >= th["situational_min_edge"]:
        kinds.append("MARKET_SITUATIONAL_OPPORTUNITY")
    else:
        if not hockey_support(c):
            reasons.append("the hockey picture does not support this side (the team model must not be below the market, with at least two drivers behind it)")
        if not any(n in ("MARKET_PRICE", "SITUATIONAL") for n in names):
            reasons.append("NO EDGE THESIS: " + ("market and hockey model agree, which describes probability, not an attractive price" if consensus_quality(c) else "no independent edge family (supported market movement or a situational advantage) beside the hockey picture"))
        if ef["against"]:
            reasons.append("an independent family argues against: " + "; ".join(e["text"] for e in ef["against"]))
        if c["edge"] < th["situational_min_edge"]:
            reasons.append(f"the price is {abs(c['edge']) * 100:.1f} pts worse than the evidence supports (limit {abs(th['situational_min_edge']) * 100:.0f})")
    kinds = [k for k in kinds if not failed(k)]
    if bal["materially_outweighs"] and kinds:
        return min(kinds, key=lambda k: PRIORITY[k]), []
    return "NONE", reasons


def edge_thesis(c: dict) -> dict:
    """The answer to: why is this side / price better than simply accepting the market as correct? Also whether its supporting families are genuinely independent (more than one distinct family)."""
    fams = ["HOCKEY_MODEL"] if hockey_support(c) else []
    fams += [e["family"] for e in c["edge_families"]["present"]]
    fams = sorted(set(fams))
    if c["structure"] == "NONE" and not c["edge_families"]["present"]:
        txt = "none: " + ("the market and the hockey model agree this is a good side, which is confirmation of probability, not a reason the price is attractive" if consensus_quality(c) else "no independent reason to prefer this side over the market's own price")
    else:
        txt = "; ".join(e["text"] for e in c["edge_families"]["present"]) or "none"
    claim = "a positive evidence-weighted edge of %.1f pts is supported" % (c["edge"] * 100) if c["edge"] > 0 and "PROVEN_SIGNAL" in fams else "no positive expected value is claimed: the bookmaker's margin is the cost of the bet"
    return {"families": fams, "independent": len(fams) >= 2, "thesis": txt, "expected_value_claim": claim, "consensus_quality": consensus_quality(c)}


def confidence(c: dict, structure_state: dict | None = None) -> str:
    """A calibrated evidence CATEGORY, never a percentage. Proven market gap with nothing moderate against -> HIGH; a proven gap, or a consensus / situational pick with three supporting categories, at most one moderate
    reason against and HIGH calibration -> MEDIUM; otherwise LOW. A structure whose prospective record has proven itself is lifted one category; one that has failed does not qualify at all."""
    bal = c["balance"]
    if c["structure"] == "NONE" or c["price_class"] == "EXTREME" or c["blockers"]:                  # a best-available bet with no edge thesis, an extreme price or weak data is honestly LOW
        return "LOW"
    if c["structure"] == "SUPPORTED_MARKET_GAP":
        lvl = "HIGH" if bal["against_moderate"] == 0 and c["calibration"] == "HIGH" and c["edge"] >= 2 * ei.EDGE_MIN else "MEDIUM"
    elif len(bal["supporting_families"]) >= 3 and bal["against_moderate"] <= 1 and c["calibration"] == "HIGH":
        lvl = "MEDIUM"
    else:
        lvl = "LOW"
    if ((structure_state or {}).get(c["structure"]) or {}).get("decision_state") == "DISAGREEMENT_HAS_SIGNAL":
        lvl = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "HIGH"}[lvl]
    proven = any(e["family"] == "PROVEN_SIGNAL" for e in c["edge_families"]["present"])
    moved_proven = ((structure_state or {}).get("MOVE_TOWARD_NOTABLE") or {}).get("decision_state") == "DISAGREEMENT_HAS_SIGNAL"
    if not proven and not moved_proven and lvl == "HIGH":               # movement may support a thesis; until its own regime shows signal it cannot make a HIGH-confidence pick
        lvl = "MEDIUM"
    if (c.get("goalies") or {}).get("state") in ("SOURCE_CONFLICT", "UNKNOWN_GOALIE") or personnel_uncertain(c):      # personnel uncertainty (one family, one step): an unknown starter, a source conflict, or an important player in doubt costs one category (a PROBABLE starter does not)
        lvl = {"LOW": "LOW", "MEDIUM": "LOW", "HIGH": "MEDIUM"}[lvl]
    return lvl


def net_case(c: dict) -> int:
    b = c["balance"]
    return b["for_strong"] * 2 + b["for_moderate"] - b["against_strong"] * 2 - b["against_moderate"]


def major_against(c: dict) -> bool:
    """Major evidence AGAINST the side: a STRONG reason against, or the hockey model rating the opponent clearly better."""
    return c["balance"]["against_strong"] > 0 or any(x["kind"] == "HOCKEY_FAVOURS_OPPONENT" and x["strength"] in ("STRONG", "MODERATE") for x in c["case_against"])


def quality(c: dict) -> dict:
    """The BETTING QUALITY score (HEURISTIC ordinal weights, `POLICY['ranking']`): independent evidence families once each (support minus against), then calibration, uncertainty, conservative edge and data
    completeness, then a bounded near-even price preference smaller than one MODERATE reason, so price can never dominate a clearly better hockey / market case. Returns the parts so the ranking can be explained."""
    w = POLICY["ranking"]["quality_weights"]
    b = c["balance"]
    support = b["for_strong"] * w["strong"] + b["for_moderate"] * w["moderate"]
    against = b["against_strong"] * w["strong"] + b["against_moderate"] * w["moderate"]
    cal = 0.0 if c.get("calibration") == "HIGH" else (w["calibration_medium"] if c.get("calibration") == "MEDIUM" else w["calibration_below"])
    data = w["data_incomplete"] if (c.get("data_completeness") or 0) < 0.7 else 0.0
    unc = w["uncertainty_per_probability_point"] * c["probability_uncertainty"] * 100
    edge = w["conservative_edge_per_probability_point"] * c["conservative_edge"] * 100
    price = 0.0 if c["price_class"] == "IN_PREFERRED_REGION" else (w["outside_preferred"] if c["price_class"] == "OUTSIDE_PREFERRED" else w["extreme"])
    total = support - against + cal + data + unc + edge + price
    return {"total": round(total, 2), "support": support, "against": against, "calibration": cal, "data": data, "uncertainty": round(unc, 2), "conservative_edge": round(edge, 2), "price": price}


def research_order(c: dict) -> tuple:
    """How Eggy ranks every side of the slate for the ONE daily bet (nothing that happened after the decision is an input), in this order of reasoning: an unusable market (stale / missing) is rejected; major evidence
    AGAINST ranks a side behind; a real EDGE THESIS ranks ahead; then the BETTING QUALITY score (hockey, market, calibration, uncertainty, price preference); the moneyline wins a tie; then the price nearer even money."""
    return (not c["eligible"], major_against(c), (PRIORITY.get(c["structure"], 9) if c["qualifies"] else 9), -quality(c)["total"], c["market"] != "MONEYLINE", abs(c["market_probability_market"] - 0.5))


def rank_reason(a: dict, b: dict) -> str:
    """Why `a` ranks above `b`: the first step of the ranking order where they differ, with the quality components."""
    la, lb = bet_label(a), bet_label(b)
    if a["eligible"] != b["eligible"]:
        return f"{la} has a usable fresh price and {lb} does not"
    if major_against(a) != major_against(b):
        return f"{lb} carries major evidence against it (a STRONG reason, or the hockey model favouring the opponent) and {la} does not"
    ta, tb = (PRIORITY.get(a["structure"], 9) if a["qualifies"] else 9), (PRIORITY.get(b["structure"], 9) if b["qualifies"] else 9)
    if ta != tb:
        return f"{la} has an edge thesis ({a['structure'].replace('_', ' ').lower()}) and {lb} {'has a weaker one' if b['qualifies'] else 'has none'}"
    qa, qb = quality(a), quality(b)
    if qa["total"] != qb["total"]:
        diffs = [f"{k} {qa[k]:+.2f} vs {qb[k]:+.2f}" for k in ("support", "against", "calibration", "data", "uncertainty", "conservative_edge", "price") if abs(qa[k] - qb[k]) >= 0.01]
        return f"{la} has the higher betting-quality score ({qa['total']:+.2f} vs {qb['total']:+.2f}: " + ", ".join(diffs[:5]) + ")"
    if a["market"] != b["market"]:
        return f"equal quality; the moneyline wins the tie with the puck line"
    return f"equal quality; {la} is the price nearer even money"


def summarize(c: dict, siblings: list[dict] | None = None, structure_state: dict | None = None) -> dict:
    """One candidate as the card and the replay show it: the probabilities, the trust, the hockey support, the evidence against, the movement and its classification, the evidence families, the edge thesis (or none),
    the same team's other market for comparison, the confidence the bet would carry, and the betting-quality score."""
    keep = ("matchup", "side", "market", "line", "price", "model_probability", "model_probability_raw", "market_probability_market", "gap", "trust_state", "model_weight", "supported_probability", "edge", "conservative_edge",
            "probability_uncertainty", "price_class", "structure", "qualifies", "eligible", "edge_thesis", "not_qualifying_because", "balance", "movement", "calibration", "calibration_note")
    hockey = [x for x in c["case_for"] if x["family"] in ("HOCKEY_MODEL", "PROVEN_SIGNAL", "SITUATIONAL")]
    market = [x for x in c["case_for"] if x["family"] == "MARKET_PRICE"]
    other = next((o for o in (siblings or []) if o["game_id"] == c["game_id"] and o["side"] == c["side"] and o["market"] != c["market"]), None)
    mv = c.get("movement") or {}
    return {**{k: c.get(k) for k in keep}, "hockey_case": [x["text"] for x in hockey], "hockey_support": hockey_support(c), "market_price_case": [x["text"] for x in market],
            "case_against": [x["text"] for x in c["case_against"] if x["strength"] != "WEAK"] or [x["text"] for x in c["case_against"][:2]], "major_evidence_against": major_against(c),
            "movement_classification": mv.get("class") or "NONE (a single capture)", "movement_direction": mv.get("direction"), "movement_percentile": mv.get("percentile"),
            "evidence_families": sorted({e["family"] for e in c["edge_families"]["present"]} | ({"HOCKEY_MODEL"} if hockey_support(c) else set())), "quality": quality(c), "confidence_if_chosen": confidence(c, structure_state) if c["eligible"] else None,
            "other_market_same_team": None if other is None else {"bet": bet_label(other), "market_probability": other["market_probability_market"], "model_probability": other["model_probability"], "conservative_edge": other["conservative_edge"],
                                                                   "probability_uncertainty": other["probability_uncertainty"], "quality": quality(other)["total"]},
            "price_freshness": c["price_freshness"]["status"]}


def bet_label(c: dict) -> str:
    if c["market"] == "MONEYLINE":
        return f"{c['side']} ML {c['price']:+.0f}"
    return f"{c['side']} {c['line']:+.1f} {c['price']:+.0f}"


def market_choice(pick: dict, candidates: list[dict]) -> dict:
    """WHY THIS MARKET: the same team in the other market, compared on the probability of the bet winning, the price, the conservative edge and the calibration uncertainty. Never chosen for a bigger payout or a higher hit rate alone."""
    other = next((c for c in candidates if c["game_id"] == pick["game_id"] and c["side"] == pick["side"] and c["market"] != pick["market"]), None)
    row = lambda c: {"bet": bet_label(c), "win_probability_market": c["market_probability_market"], "win_probability_evidence_weighted": c["supported_probability"], "model_probability": c["model_probability"], "price": c["price"],
                     "break_even": c["implied_with_margin"], "conservative_edge": c["conservative_edge"], "probability_uncertainty": c["probability_uncertainty"], "price_class": c["price_class"], "case_net": net_case(c)}
    if other is None:
        return {"chosen": row(pick), "other": None, "why": f"only the {pick['market'].replace('_', ' ').lower()} has a valid price for this team"}
    chosen_first = research_order(pick) <= research_order(other)
    cmp_ = (f"outright win probability {pick['market_probability_market']:.1%} market / {pick['supported_probability']:.1%} evidence-weighted for the {pick['market'].replace('_', ' ').lower()}, "
            f"against {other['market_probability_market']:.1%} / {other['supported_probability']:.1%} for the {other['market'].replace('_', ' ').lower()}; price {pick['price']:+.0f} vs {other['price']:+.0f}; "
            f"probability uncertainty {pick['probability_uncertainty'] * 100:.1f} vs {other['probability_uncertainty'] * 100:.1f} pts; calibration {pick.get('calibration')} vs {other.get('calibration')}")
    return {"chosen": row(pick), "other": row(other), "why": (rank_reason(pick, other) if chosen_first else "the other market ranked higher on this team but the bet is chosen across the whole slate") + ". Compared on: " + cmp_
            + ". Not chosen for a bigger payout or a higher hit rate alone."}


def why_number_one(pick: dict, ranked: list[dict]) -> str:
    th = pick["edge_thesis"]
    if pick["structure"] != "NONE":
        head = f"EDGE THESIS ({pick['structure'].replace('_', ' ').lower()}): {th['thesis']}"
    else:
        head = "BEST AVAILABLE, not a proven edge: no independent edge family cleared the bar on today's slate, so this is the strongest all-round bet on the board (hockey, market, calibration, uncertainty and price together), at LOW confidence"
    runner = next((c for c in ranked if c is not pick and c["eligible"]), None)
    return head + "." + (f" Why it ranks above the next side: {rank_reason(pick, runner)}." if runner else "")


def decide(candidates: list[dict], *, now: dt.datetime, why_no_prices: str | None = None, structure_state: dict | None = None) -> dict:
    """The day's decision: rank every side of the slate by `research_order` (nothing that happened after the decision is an input) and take the best: EXACTLY ONE bet whenever a valid fresh market exists. Weak evidence lowers the
    confidence; it does not remove the bet. PASS only for: no valid fresh market, or a technical / data failure (and 'no game' upstream). The top three researched sides are always reported."""
    base = {"policy_id": POLICY["policy_id"], "policy_digest": policy_digest(), "decided_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "availability": AVAILABILITY}
    if why_no_prices:
        return {**base, "decision": "PASS", "pass_reason": why_no_prices, "pass_kind": "NO_VALID_FRESH_MARKET", "ranked": [], "top_three": [], "candidates_examined": 0}
    ranked = sorted(candidates, key=research_order)
    top = []
    for i, c in enumerate(ranked[:3]):
        row = summarize(c, candidates, structure_state)
        row["why_ranked_above_the_next"] = rank_reason(c, ranked[i + 1]) if i + 1 < len(ranked) else None
        row["rank"] = i + 1
        top.append(row)
    pick = next((c for c in ranked if c["eligible"]), None)
    if pick:
        pick["confidence"] = confidence(pick, structure_state)
        pick["pick_basis"] = "EDGE_THESIS" if pick["qualifies"] else "BEST_AVAILABLE"
        pick["label"] = "BEST AVAILABLE — LOW CONFIDENCE" if pick["pick_basis"] == "BEST_AVAILABLE" else f"EDGE THESIS — {pick['confidence']} CONFIDENCE"
        mc = market_choice(pick, candidates)
        pick["card"] = {"bet": bet_label(pick), "confidence": pick["confidence"], "label": pick["label"], "why_number_one": why_number_one(pick, ranked), "hockey_case": [x["text"] for x in pick["case_for"] if x["family"] in ("HOCKEY_MODEL", "PROVEN_SIGNAL", "SITUATIONAL")],
                        "market_case": [x["text"] for x in pick["case_for"] + pick["case_against"] if x["family"] == "MARKET_PRICE" and x["kind"] != "MARGIN"], "why_this_market": mc, "starting_goalies": _goalie_lines(pick), "personnel_availability": _personnel_lines(pick),
                        "case_against": [x["text"] for x in pick["case_against"] if x["strength"] != "WEAK"] or [x["text"] for x in pick["case_against"][:2]]}
        return {**base, "decision": "PICK", "pick": pick, "ranked": [summarize(c, candidates, structure_state) for c in ranked[:5]], "top_three": top, "candidates_examined": len(candidates)}
    reason = ("a technical / data failure: no side could be evaluated" if not candidates else "no side of the slate has a FRESH current price, so no legitimate bet can be evaluated: "
              + "; ".join(sorted({c["price_freshness"]["reason"][:80] for c in candidates})[:2]))
    return {**base, "decision": "PASS", "pass_reason": reason, "pass_kind": "NO_VALID_FRESH_MARKET" if candidates else "TECHNICAL_OR_DATA_FAILURE", "ranked": [summarize(c, candidates, structure_state) for c in ranked[:5]], "top_three": top, "candidates_examined": len(candidates)}
