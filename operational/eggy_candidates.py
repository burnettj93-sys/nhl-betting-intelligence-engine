"""Eggy, stage A: INTELLIGENCE CANDIDATES. Stage B (the production recording policy, research/real_market_parlay/anchor.py via operational/daily_tickets.py) is unchanged.

The official selector only ever examined cross-game combinations of the legs that already had real prices, so on a morning before the player-prop captures it saw "0 player-prop legs" and examined six moneyline
pairs. That is a search failure, not an answer. This module looks at the whole real slate first, the way a bettor does: it finds the strongest GAME THESES (a strong favourite whose stars drive the offence, a high
shot-volume pair, a dominant team with primary scorers), builds the compatible legs from every real supported market (moneyline, shots, points, goals), and reports for each leg exactly what is known and what is
missing. It never records anything, never changes the production policy, and never lowers a bar.

What it does NOT do, on purpose:
  * It does not invent a joint probability. Same-game legs are dependent (a team wins more often when its stars shoot and score; one player's shots and points move together); no joint model has been validated here.
    Each pair is classified, and the only joint figures shown are the assumption-free Frechet bounds (the joint chance can never be below max(0, sum of the legs' chances - (n-1)) nor above the smallest leg).
  * It does not invent a price. The Odds API supplies single-leg lines only and no same-game-parlay price (its documentation lists no such market); DraftKings prices a same-game parlay with its own correlation
    model, so multiplying the legs' prices is not a price the book offers. The combined price is therefore reported as NOT AVAILABLE and no expected value is computed for a same-game candidate.
  * It does not activate any unvalidated market for recording. Points and goals legs are shown with the model's own validation verdict, and are marked as not used by the production recording policy.

Three daily outputs: OFFICIAL_EGGY_PICK (stage B produced a ticket), BEST_EGGY_CANDIDATE (a strong thesis exists but a certification gate stands in the way, each one named), or TRUE_PASS (no thesis meets the
support bar after examining the whole real slate; what was examined is listed).

The support score that ranks candidates is the geometric mean of the legs' estimates: a ranking aid, NOT a probability of the combination.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from itertools import combinations
from zoneinfo import ZoneInfo

OFFICIAL, BEST, PASS = "OFFICIAL_EGGY_PICK", "BEST_EGGY_CANDIDATE", "TRUE_PASS"
FLAGSHIP = "OFFICIAL_EGGY_FLAGSHIP_PARLAY"

# Eggy's OFFICIAL daily output (owner 2026-10-21): at most ONE flagship parlay, the learning pool's own flagship (operational/learning_cohort._flagship_public: the strongest GOOD, RECORD parlay of the
# day), or PASS. It replaces the primary slip (policy digest 1fad3ba03503cf94) as the official output; that code is kept, not used for the official pick. No selection happens here.
OFFICIAL_POLICY = {"policy_id": "EGGY_FLAGSHIP_PARLAY_V1", "unit": "FLAGSHIP_PARLAY", "max_outputs_per_day": 1, "source": "operational.learning_cohort._flagship_public",
                   "qualifying": {"construction_quality": "GOOD", "action": "RECORD"}, "no_qualifying_flagship": "PASS",
                   "price": "UNPRICED LEARNING PARLAY unless a real combined price exists; a combined price is never made up", "stake_return_roi": "none claimed",
                   "replaces_as_official_output": {"primary_slip_policy_digest": "1fad3ba03503cf94"}}


def official_policy_digest() -> str:
    import hashlib
    return hashlib.sha256(json.dumps(OFFICIAL_POLICY, sort_keys=True).encode()).hexdigest()[:16]


def flagship_state(status: dict | None, today_et: str | None) -> dict:
    """Eggy's flagship as the learning status already carries it, for the Eastern date `today_et`. {'state': 'OK', 'flagship': dict|None} or {'state': 'UNAVAILABLE', 'reason': ...} when the status is
    missing or is not from today (a stale file is never read as 'no flagship')."""
    if not status:
        return {"state": "UNAVAILABLE", "reason": "the learning status is not available"}
    try:
        built = dt.datetime.fromisoformat(str(status["generated_at_utc"]).replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except (KeyError, ValueError):
        return {"state": "UNAVAILABLE", "reason": "the learning status has no valid build time"}
    if today_et and built != today_et:
        return {"state": "UNAVAILABLE", "reason": f"the learning status is from {built}, not {today_et}"}
    fl = status.get("eggy_flagship")
    if fl and fl.get("construction_quality") == "GOOD" and fl.get("action") == "RECORD":
        return {"state": "OK", "flagship": fl}
    return {"state": "OK", "flagship": None}

FAVORITE_FLOOR = 0.55               # the moneyline floor of the production selector (anchor.MONEYLINE_LEG_FLOOR)
STAR_POINTS_FLOOR = 0.60            # a star's 1+ point model probability for the "strong favourite + star" thesis
LEG_FLOOR = 0.60                    # a shots leg is "well supported" from here (anchor.SHOTS_LEG_FLOOR)
SHOT_STAR_FLOOR = 0.70              # two skaters this likely to reach 2+ shots make a shot-volume thesis
SCORER_FLOOR = 0.30                 # a primary scorer's anytime-goal model probability
DOMINANT_FAVORITE = 0.60
MIN_SUPPORT = 0.60                  # a candidate is worth showing when its support score reaches the "well supported" mark
WEAK_GOALIE_SAVE_PCT = 0.895        # projected save percentage below this marks weak opposing goaltending (the projection is shrunk toward the league mean of about 0.905, so only a clearly lower value counts)
SHOT_ENV_FLOOR = 24.0               # a team whose top 12 skaters project this many shots: among the league's highest (2026-10-19 slate: league range 18.5 to 24.9)
TOP_PLAYERS = 8

FAMILY = {"SOG": ("PLAYER_SOG_ALTERNATE", "player_shots_on_goal_alternate", "shots on goal", "shots"),
          "PTS": ("PLAYER_POINTS", "player_points", "points", "points"),
          "GOAL": ("PLAYER_GOALS", "player_goal_scorer_anytime", "anytime goal", "goals")}
STAT_KEY = {"SOG": "shots>={k}", "PTS": "points>={k}", "GOAL": "goals>={k}"}
# Production recording policy (research/real_market_parlay/anchor.py): moneylines and shots legs only; points and anytime-goal legs are speculative and are not used.
MONITORED_FAMILIES = frozenset({"SOG", "ML"})


def _dec(american: float) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def _implied(american) -> float | None:
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    return 100.0 / (a + 100.0) if a > 0 else (-a) / (-a + 100.0) if a < 0 else None


def _parse(value):
    from operational import quote_freshness as qf
    return qf.parse_utc(value)


def _fresh(quoted_at, start_utc, now) -> tuple[bool, float | None]:
    from operational import quote_freshness as qf
    start = _parse(start_utc)
    hours = (start - now).total_seconds() / 3600.0 if start is not None else 99.0
    r = qf.assess(quoted_at, None, now, qf.price_limit_min(hours), require_retrieval=False)
    return bool(r["fresh"]), r.get("quote_age_min")


def _label_k(code: str, k: int) -> str:
    return {"SOG": f"{k}+ shots", "PTS": f"{k}+ point{'s' if k != 1 else ''}", "GOAL": "anytime goal"}[code]


# ------------------------------------------------------------------------------------------- market availability
def _plan_view(plan: dict | None, gid: str, key: str, now: dt.datetime) -> dict:
    """Why a market has not been asked for yet, from the day's credit plan (and the wave's own window)."""
    if not plan:
        return {"state": "NOT_FETCHED", "why": "no credit plan is on file for today"}
    from operational import capture_timeline as ct
    if gid in (plan.get("games_not_priced") or []):
        return {"state": "EXCLUDED_BY_CREDIT_PLAN", "why": f"not priced today: the plan funds player prices for {len(plan.get('games_priced') or [])} of {plan.get('games_today')} games; a game is left out when the day's credits do not reach it or its capture window has closed"}
    if key not in ((plan.get("game_markets") or {}).get(gid) or []):
        if key == "player_points":
            surplus = plan.get("surplus_credits")
            return {"state": "NOT_IN_CREDIT_PLAN", "why": f"points are an add-on bought only from surplus credits; today's surplus is {surplus if surplus is not None else 'unknown'} against 1 credit per game"}
        return {"state": "NOT_IN_CREDIT_PLAN", "why": "this market is not part of the planned capture for this game"}
    for w in plan.get("waves") or []:
        if gid in (w.get("games") or []):
            first = _parse(w.get("first_start_utc"))
            if first is not None:
                target = first - dt.timedelta(minutes=ct.CAPTURE_TARGET_MIN)
                last = _parse(w.get("last_chance_utc")) or (first - dt.timedelta(minutes=ct.LAST_CHANCE_MIN))
                when = target.astimezone(dt.timezone(dt.timedelta(hours=-4 if _is_dst(target) else -5))).strftime("%-I:%M %p ET")
                if now < target:
                    return {"state": "AWAITING_WAVE", "why": f"funded: captured with its wave at {when} ({ct.CAPTURE_TARGET_MIN} minutes before the wave's first puck drop; publication follows by 60 minutes before)"}
                if now > last:
                    return {"state": "MISSED_WINDOW", "why": f"the capture window (opened {when}) closed without a capture; nothing is bought late for a game that is about to start"}
                return {"state": "CAPTURE_DUE", "why": f"funded: its wave capture (due {when}) has not landed yet"}
    return {"state": "NOT_FETCHED", "why": "funded in the plan but no capture window is on file"}


def _is_dst(when: dt.datetime) -> bool:
    try:
        from zoneinfo import ZoneInfo
        return bool(when.astimezone(ZoneInfo("America/New_York")).dst())
    except Exception:  # noqa: BLE001
        return True


def explain_markets(game: dict, avail_game: dict | None, plan: dict | None, pool_game: dict | None, matrix: list[dict] | None, now: dt.datetime) -> list[dict]:
    """One row per market for one game: is there a price, and if not, exactly why, plus the market's contract and model status. Replaces the single word 'no price'."""
    gid = str(game["game_id"])
    out = []
    by_name = {m.get("market"): m for m in (matrix or [])}
    for code in ("SOG", "PTS", "GOAL"):
        family, key, label, _stat = FAMILY[code]
        players = [p for p in ((pool_game or {}).get("players") or {}).values() if code in (p.get("m") or {})]
        info = by_name.get({"SOG": "Shots on goal (alternate ladder)", "PTS": "Points (1+, 2+)", "GOAL": "Anytime goal"}[code]) or {}
        row = {"market": key, "label": label, "contract": info.get("contract", "UNKNOWN"), "model": info.get("validation"), "recording_policy": "USED" if code in MONITORED_FAMILIES else "NOT USED (speculative; no calibration evidence of the policy's kind)"}
        m = ((avail_game or {}).get("markets") or {}).get(key) or {}
        started = _parse(game.get("start_utc"))
        if started is not None and started <= now and not players:
            row.update(state="GAME_STARTED", why="the game has started; nothing is priced or bought after puck drop")
        elif players:
            quoted = max((p["m"][code]["q"] for p in players), default=None)
            fresh, age = _fresh(quoted, game.get("start_utc"), now)
            row.update(state="PRICED" if fresh else "STALE", why=f"{len(players)} players priced; quoted {quoted}" + ("" if fresh else f" ({age:.0f} min old: past the freshness limit)" if age is not None else ""), quote_updated_utc=quoted)
        elif m.get("status") == "POSTED":
            row.update(state="POSTED_NOT_USABLE", why="the provider returned the market but no line could be tied to a verified player on either roster (identity, roster status or the freshness rules removed them)")
        elif m.get("status") == "NOT_POSTED":
            row.update(state="NOT_POSTED_BY_BOOK", why="we asked and DraftKings had not posted it", checked_utc=m.get("checked_utc"))
        elif m.get("status") == "FETCH_ERROR":
            row.update(state="FETCH_FAILED", why=f"the request failed ({m.get('detail') or 'provider error'}); the next cycle asks again inside the window", checked_utc=m.get("checked_utc"))
        elif m.get("status") == "BUDGET_BLOCKED":
            row.update(state="EXCLUDED_BY_CREDIT_BUDGET", why=f"the credit plan did not cover it ({m.get('detail') or 'budget'})")
        else:
            row.update(_plan_view(plan, gid, key, now))
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------- legs
def _skater_view(p: dict) -> dict:
    proj = p.get("projection") or {}
    return {"player_id": str(p.get("player_id")), "name": p.get("name"), "team": p.get("team"), "position": p.get("position"),
            "exp": proj.get("expected") or {}, "prob": proj.get("probabilities") or {}, "games": p.get("games_total") or 0,
            "eligible": bool(proj.get("pricing_eligible"))}


def _price_of(pool_game: dict | None, pid: str, code: str, k: int) -> dict | None:
    mk = ((((pool_game or {}).get("players") or {}).get(pid) or {}).get("m") or {}).get(code)
    if not mk:
        return None
    for rung, price, prob in mk["l"]:
        if int(rung) == int(k):
            rec = (mk.get("rec") or {}).get(str(k))
            return {"american": price, "quote_updated_utc": mk["q"], "retrieved_at_utc": mk.get("r"), "model_in_pool": prob,
                    "recent": (f"{rec[0]} of {rec[1]}" if rec else None), "final_estimate": (rec[2] if rec else None)}
    return None


def player_leg(game: dict, sk: dict, code: str, k: int, pool_game: dict | None, avail_game: dict | None, plan: dict | None, matrix: list[dict] | None,
               verdicts: dict | None, now: dt.datetime) -> dict:
    family, key, label, stat = FAMILY[code]
    model_p = sk["prob"].get(STAT_KEY[code].format(k=k))
    price = _price_of(pool_game, sk["player_id"], code, k)
    explain = None
    outlook = "PRICED"
    gates = {}
    if price:
        fresh, age = _fresh(price["quote_updated_utc"], game.get("start_utc"), now)
        price["fresh"], price["age_min"] = fresh, age
        gates["real_price"], gates["fresh"], gates["offered_in_feed"] = True, fresh, True
        if not fresh:
            outlook = "STALE"
    else:
        row = next((r for r in explain_markets(game, avail_game, plan, pool_game, matrix, now) if r["market"] == key), {})
        state = row.get("state", "NOT_FETCHED")
        if state in ("PRICED", "STALE"):                 # the market is priced for other players of this game, but this line is not among them
            outlook, explain = "NO_LINE_FOR_THIS_PLAYER", "the market is priced for other players of this game but there is no usable line for this player (not offered, or removed by the identity, roster or freshness rules)"
        else:
            outlook, explain = state, f"no price on file ({state.replace('_', ' ').lower()}): {row.get('why', '')}"
        gates["real_price"], gates["offered_in_feed"], gates["fresh"] = False, None, None      # whether the book offers the line is unknown until the market is captured: never asserted either way
    verdict = (verdicts or {}).get(STAT_KEY[code].format(k=k)) or {}
    gates["model_support"] = model_p is not None and sk["eligible"]
    gates["identity_verified"] = True                       # the skater list the board uses is already integrity-filtered against the official rosters
    validated = verdict.get("verdict") == "BEATS_BASELINES"
    gates["model_validated_held_out"] = validated
    gates["recording_policy_uses_market"] = code in MONITORED_FAMILIES
    final = price["final_estimate"] if price and price.get("final_estimate") is not None else None
    est = final if final is not None else model_p
    if est is None:
        est_basis = "no model estimate"
    elif final is not None:
        est_basis = "the ticket estimate: recent record capped at the model's calibrated probability plus 0.02"
    else:
        est_basis = "the model's calibrated probability (a priced shots line also uses the player's recent record)" if code == "SOG" else "the model's calibrated probability"
    why = []
    if not gates["real_price"]:
        why.append(explain)
    elif not gates["fresh"]:
        why.append(f"the quote is {price['age_min']:.0f} minutes old, past the freshness limit" if price.get("age_min") is not None else "the quote has no usable time")
    if not gates["model_support"]:
        why.append("no supported model estimate for this player and line (sample or role too small)")
    if not validated:
        why.append(f"model validation verdict for {STAT_KEY[code].format(k=k)}: {verdict.get('verdict') or 'not available'}")
    if not gates["recording_policy_uses_market"]:
        why.append("the production recording policy does not use this market in a recorded ticket")
    return {"kind": "PLAYER", "price_outlook": outlook, "code": code, "market_key": key, "label": f"{sk['name']} {_label_k(code, k)}", "participant": sk["name"], "player_id": sk["player_id"], "team": sk["team"], "position": sk["position"],
            "threshold": k, "game_id": str(game["game_id"]), "price": price, "model_probability": None if model_p is None else round(model_p, 4), "recent_record": price["recent"] if price else None,
            "estimate": None if est is None else round(est, 4), "estimate_basis": est_basis, "implied_probability": None if not price else round(_implied(price["american"]) or 0, 4),
            "model_validation": verdict.get("verdict"), "ontario": "UNVERIFIED (the feed is DraftKings US via The Odds API; Ontario offerings and prices are not matched)", "gates": gates,
            "candidate_eligible": bool(gates["model_support"] and gates["identity_verified"]), "priced": bool(price), "usable_price": bool(price and gates["fresh"]),
            "validated": bool(validated and gates["model_support"]), "start_utc": game.get("start_utc"), "monitored_eligible": False, "exclusions": why}


def moneyline_leg(game: dict, side: str, now: dt.datetime) -> dict | None:
    sd = ((game.get("side") or {}).get("sides") or {}).get(side)
    if not sd or sd.get("american") is None:
        return None
    fresh, age = _fresh(sd.get("quote_captured_at_utc"), game.get("start_utc"), now)
    other = ((game.get("side") or {}).get("sides") or {}).get("away" if side == "home" else "home") or {}
    if other.get("quote_captured_at_utc") is not None:                       # the no-vig estimate rests on BOTH sides' quotes
        fresh = fresh and _fresh(other.get("quote_captured_at_utc"), game.get("start_utc"), now)[0]
    if (game.get("side") or {}).get("fresh") is False:
        fresh = False
    team = sd.get("team")
    why = [] if fresh else [f"the moneyline quote is {age:.0f} minutes old, past the freshness limit" if age is not None else "the moneyline quote has no usable time"]
    return {"kind": "MONEYLINE", "price_outlook": "PRICED" if fresh else "STALE", "code": "ML", "market_key": "h2h", "label": f"{team} to win", "participant": team, "player_id": None, "team": team, "position": None, "threshold": None, "game_id": str(game["game_id"]),
            "price": {"american": sd["american"], "quote_updated_utc": sd.get("quote_captured_at_utc"), "fresh": fresh, "age_min": age}, "model_probability": sd.get("model"),
            "recent_record": None, "estimate": sd.get("no_vig"), "estimate_basis": "the market's own no-vig probability (no model here beats a DraftKings moneyline)", "implied_probability": round(_implied(sd["american"]) or 0, 4),
            "model_validation": "STRENGTH_MODEL_AND_MARKET", "ontario": "UNVERIFIED (the feed is DraftKings US via The Odds API; Ontario offerings and prices are not matched)",
            "gates": {"real_price": True, "fresh": fresh, "offered_in_feed": True, "model_support": sd.get("model") is not None, "identity_verified": True, "model_validated_held_out": True, "recording_policy_uses_market": True},
            "candidate_eligible": True, "priced": True, "usable_price": bool(fresh), "validated": True, "start_utc": game.get("start_utc"), "monitored_eligible": False, "exclusions": why}


# ------------------------------------------------------------------------------------------- dependence
def classify_pair(a: dict, b: dict) -> dict:
    """What is known about how two legs of one game move together. Directional knowledge only: no number is invented."""
    same_player = a.get("player_id") and a.get("player_id") == b.get("player_id")
    ml = [x for x in (a, b) if x["code"] == "ML"]
    if same_player and {a["code"], b["code"]} == {"PTS", "SOG"}:
        return {"a": a["label"], "b": b["label"], "kind": "SAME_PLAYER_SHOTS_AND_POINTS", "direction": "POSITIVE", "strength": "strong",
                "note": "a player who shoots more creates more of the chances that become his goals and assists; the two legs usually hit together"}
    if same_player and {a["code"], b["code"]} == {"SOG", "GOAL"}:
        return {"a": a["label"], "b": b["label"], "kind": "SAME_PLAYER_SHOTS_AND_GOALS", "direction": "POSITIVE", "strength": "strong", "note": "a goal is a shot on goal: the goal leg hitting adds a shot toward the shots leg"}
    if same_player and {a["code"], b["code"]} == {"PTS", "GOAL"}:
        return {"a": a["label"], "b": b["label"], "kind": "NESTED", "direction": "IMPLIES", "strength": "total", "note": "a goal is a point: the goal leg hitting guarantees the points leg (redundant, not diversification)"}
    if ml and (a["team"] == b["team"]):
        other = b if ml[0] is a else a
        return {"a": a["label"], "b": b["label"], "kind": "TEAM_RESULT_AND_PLAYER_OFFENCE", "direction": "POSITIVE", "strength": "moderate",
                "note": f"{other['participant']} producing is part of how {ml[0]['team']} wins; a book prices the pair as correlated"}
    if a["team"] == b["team"] and a["code"] == b["code"] == "SOG":
        return {"a": a["label"], "b": b["label"], "kind": "TEAMMATES_SHOTS", "direction": "POSITIVE", "strength": "mild", "note": "teammates' shots share the team's possession, offensive-zone time and power plays"}
    if a["team"] == b["team"] and a["code"] in ("PTS", "GOAL") and b["code"] in ("PTS", "GOAL"):
        return {"a": a["label"], "b": b["label"], "kind": "TEAMMATES_SCORING", "direction": "POSITIVE", "strength": "moderate", "note": "teammates often share the same goal (one scores, the other assists)"}
    if a["team"] == b["team"]:
        return {"a": a["label"], "b": b["label"], "kind": "TEAMMATES_MIXED", "direction": "POSITIVE", "strength": "mild", "note": "both legs depend on the same team's offensive output"}
    return {"a": a["label"], "b": b["label"], "kind": "OPPOSING_TEAMS", "direction": "UNCLEAR", "strength": "unknown", "note": "legs on opposite teams: direction not established"}


def frechet(estimates: list[float]) -> dict | None:
    if not estimates or any(e is None for e in estimates):
        return None
    n = len(estimates)
    return {"low": round(max(0.0, sum(estimates) - (n - 1)), 4), "high": round(min(estimates), 4),
            "basis": "Frechet bounds: true for ANY dependence between the legs, so they say how little the legs alone pin down; they are not an estimate"}


def support_score(legs: list[dict]) -> float | None:
    est = [l["estimate"] for l in legs]
    if not est or any(e is None or e <= 0 for e in est):
        return None
    return round(math.exp(sum(math.log(e) for e in est) / len(est)), 4)


# ------------------------------------------------------------------------------------------- theses
def _top(team_players: list[dict], key: str, n: int = 2, floor: float = 0.0) -> list[dict]:
    pool = [p for p in team_players if p["eligible"] and p["prob"].get(key) is not None and p["prob"][key] >= floor]
    pool.sort(key=lambda p: (-p["prob"][key], p["name"] or ""))
    return pool[:n]


def _modifiers(game: dict, team: str, opp_goalie_save_pct: float | None, team_players: list[dict], opp_goalie_status: str | None = None) -> list[str]:
    ev = []
    if opp_goalie_save_pct is not None and opp_goalie_save_pct < WEAK_GOALIE_SAVE_PCT:
        ev.append(f"projected weak opposing goaltending: save percentage {opp_goalie_save_pct:.4f} (below {WEAK_GOALIE_SAVE_PCT:.3f}); starter {str(opp_goalie_status or 'status unknown').lower().replace('_', ' ')}")
    shots = sum((p["exp"].get("shots") or 0.0) for p in sorted(team_players, key=lambda p: -(p["exp"].get("shots") or 0.0))[:12])
    if shots >= SHOT_ENV_FLOOR:
        ev.append(f"projected shot environment among the league's highest: the team's top skaters project {shots:.1f} shots")
    return ev


def _thesis_sentence(key: str, team: str, names: list[str]) -> str:
    return {"STRONG_FAVORITE_STAR": f"{team} controls the matchup and its elite players drive the offence ({', '.join(names)}).",
            "HIGH_SHOT_VOLUME": f"{team}'s shooters generate volume: {' and '.join(names)} are both very likely to clear their shots lines.",
            "DOMINANT_TEAM_PRIMARY_SCORERS": f"{team} is a clear favourite and its primary scorers ({', '.join(names)}) are the likeliest goals.",
            "SPECIFIED": f"A combination specified for evaluation ({team}): {', '.join(names)}. Evaluated with exactly the rules every candidate gets; it is not recommended by being listed."}[key]


THESIS_TITLES = {"SPECIFIED": "SPECIFIED COMBINATION", "STRONG_FAVORITE_STAR": "STRONG FAVORITE + STAR INVOLVEMENT", "HIGH_SHOT_VOLUME": "HIGH SHOT-VOLUME STARS", "DOMINANT_TEAM_PRIMARY_SCORERS": "TEAM DOMINANCE + PRIMARY SCORERS"}


def _assemble(game: dict, thesis_key: str, team: str, legs: list[dict], evidence: list[str], names: list[str], bench: list[str] | None = None, funded: bool = True) -> dict | None:
    legs = [l for l in legs if l]
    if len(legs) < 2:
        return None
    est = [l["estimate"] for l in legs]
    score = support_score(legs)
    pairs = [classify_pair(a, b) for a, b in combinations(legs, 2)]
    blockers = ["SAME-GAME DEPENDENCE: no joint model has been validated for these legs, so no joint probability is claimed (the legs are positively dependent: " + "; ".join(sorted({p['kind'].lower().replace('_', ' ') for p in pairs})) + ")",
                "NO COMBINED PRICE: the feed carries single-leg lines only (The Odds API lists no same-game-parlay market); DraftKings prices same-game parlays with its own correlation model, and multiplying the legs' prices is not an offered price",
                "PRODUCTION POLICY: recorded tickets are cross-game only; a same-game ticket would need a validated joint model AND a quoted combined price"]
    for l in legs:
        for w in l["exclusions"]:
            blockers.append(f"{l['label']}: {w}")
    priced = sum(1 for l in legs if l.get("usable_price"))           # only a fresh quote counts as a price for the combination
    stale = [l["label"] for l in legs if l["priced"] and not l.get("usable_price")]
    validated = all(l.get("validated") for l in legs)
    return {"id": f"{game['game_id']}:{thesis_key}", "kind": "SAME_GAME", "game_id": str(game["game_id"]), "matchup": f"{game['away']} @ {game['home']}", "start_et": game.get("start_et"), "start_utc": game.get("start_utc"),
            "thesis": {"key": thesis_key, "title": THESIS_TITLES[thesis_key], "sentence": _thesis_sentence(thesis_key, team, names), "evidence": evidence, "team": team},
            "legs": legs, "other_compatible_legs": bench or [], "game_funded": funded, "support_score": score, "support_note": "geometric mean of the legs' estimates: a ranking aid, NOT a probability of the combination",
            "dependence": {"same_game": True, "pairs": pairs, "joint_model": "NOT_CERTIFIED", "joint_probability": None, "joint_bounds": frechet(est)},
            "combined_price": {"available": False, "american": None, "reason": "no same-game-parlay price exists in the feed and none is fabricated"},
            "expected_value": None, "priced_legs": priced, "stale_priced_legs": stale, "total_legs": len(legs), "complete_prices": priced == len(legs), "all_legs_validated": validated,
            "status": "AWAITING_PRICES" if priced < len(legs) else "NOT_CERTIFIED",
            "label": "EGGY SAME-GAME CANDIDATE — JOINT PROBABILITY NOT YET CERTIFIED", "monitored_paper_bet_eligible": False, "blockers": blockers,
            "to_certify": ["a validated same-game joint model for these leg types (research, not started)", "a real quoted same-game combined price (a provider that supplies one; not purchased)",
                           "real fresh prices for every leg", "the production policy extended to same-game tickets (owner decision)"]}


def candidates_for_game(game: dict, sides_players: dict, pool_game: dict | None, avail_game: dict | None, plan: dict | None, matrix: list[dict] | None,
                        verdicts: dict | None, goalies: dict, now: dt.datetime) -> tuple[list[dict], dict]:
    """The same-game candidates (at most one per thesis) for one scheduled game, and what was examined."""
    examined = {"game_id": str(game["game_id"]), "theses": []}
    sk = {s: [_skater_view(p) for p in sides_players.get(s, [])] for s in ("home", "away")}
    out = []
    sides = ((game.get("side") or {}).get("sides") or {})
    funded = True if not plan else (str(game["game_id"]) in (plan.get("games_priced") or []) or bool(((pool_game or {}).get("players"))))
    for s, opp in (("home", "away"), ("away", "home")):
        sd = sides.get(s) or {}
        team = sd.get("team") or game.get(s)
        nv = sd.get("no_vig")
        pl = sk[s]
        gl = next((b for b in (game.get("goalies") or []) if b.get("side") == opp), None) or {}
        save_pct = ((gl.get("projection") or {}).get("save_pct_used"))
        mods = _modifiers(game, team, save_pct, pl, ((gl.get("starter") or {}).get("status")))
        ctx = dict(game=game, pool_game=pool_game, avail_game=avail_game, plan=plan, matrix=matrix, verdicts=verdicts, now=now)

        def P(p, code, k):
            return player_leg(ctx["game"], p, code, k, ctx["pool_game"], ctx["avail_game"], ctx["plan"], ctx["matrix"], ctx["verdicts"], ctx["now"])
        ml = moneyline_leg(game, s, now)
        # 1. strong favourite + star involvement
        stars = _top(pl, "points>=1", 1, STAR_POINTS_FLOOR)
        t1 = {"thesis": "STRONG_FAVORITE_STAR", "team": team, "passed": False}
        if ml is not None and nv is not None and nv >= FAVORITE_FLOOR and stars:
            star = stars[0]
            shooters = [p for p in _top(pl, "shots>=2", 6, LEG_FLOOR) if p["player_id"] != star["player_id"]]
            legs = [ml, P(star, "PTS", 1)]
            if (star["prob"].get("shots>=2") or 0) >= LEG_FLOOR:
                legs.append(P(star, "SOG", 2))
            if shooters:
                legs.append(P(shooters[0], "SOG", 2))
            names = [star["name"]] + ([shooters[0]["name"]] if shooters else [])
            used = {star["player_id"]} | ({shooters[0]["player_id"]} if shooters else set())
            bench = [f"{p['name']} 2+ shots (model {p['prob']['shots>=2']:.0%})" for p in _top([q for q in pl if q["player_id"] not in used], "shots>=2", 4, 0.50)] \
                    + [f"{p['name']} 1+ point (model {p['prob']['points>=1']:.0%})" for p in _top([q for q in pl if q["player_id"] != star["player_id"]], "points>=1", 3, 0.45)]
            ev = [f"{team} no-vig win probability {nv:.0%} (price {sd.get('american'):+.0f})", f"{star['name']}: points>=1 model {star['prob']['points>=1']:.0%}, {(star['exp'].get('points') or 0):.2f} expected points"] + mods
            cand = _assemble(game, "STRONG_FAVORITE_STAR", team, legs, ev, names, bench, funded)
            if cand:
                out.append(cand)
                t1["passed"] = True
        else:
            t1["why_not"] = ("no moneyline price" if ml is None else f"favourite floor not met (no-vig {nv if nv is None else round(nv, 3)} < {FAVORITE_FLOOR})" if (nv is None or nv < FAVORITE_FLOOR) else f"no star with a points>=1 model probability of {STAR_POINTS_FLOOR:.0%} or more")
        examined["theses"].append(t1)
        # 2. high shot-volume stars
        top2 = _top(pl, "shots>=2", 2, SHOT_STAR_FLOOR)
        t2 = {"thesis": "HIGH_SHOT_VOLUME", "team": team, "passed": False}
        if len(top2) == 2:
            legs = []
            for p in top2:
                k = 3 if (p["prob"].get("shots>=3") or 0) >= 0.65 else 2
                legs.append(P(p, "SOG", k))
            cand = _assemble(game, "HIGH_SHOT_VOLUME", team, legs, [f"{p['name']}: shots>=2 model {p['prob']['shots>=2']:.0%}, {(p['exp'].get('shots') or 0):.1f} expected shots" for p in top2] + mods, [p["name"] for p in top2], funded=funded)
            if cand:
                out.append(cand)
                t2["passed"] = True
        else:
            t2["why_not"] = f"fewer than two skaters with a shots>=2 model probability of {SHOT_STAR_FLOOR:.0%} or more"
        examined["theses"].append(t2)
        # 3. dominant team + primary scorers
        scorers = _top(pl, "goals>=1", 2, SCORER_FLOOR)
        t3 = {"thesis": "DOMINANT_TEAM_PRIMARY_SCORERS", "team": team, "passed": False}
        if ml is not None and nv is not None and nv >= DOMINANT_FAVORITE and len(scorers) == 2:
            legs = [ml] + [P(p, "GOAL", 1) for p in scorers]
            cand = _assemble(game, "DOMINANT_TEAM_PRIMARY_SCORERS", team, legs, [f"{team} no-vig win probability {nv:.0%}"] + [f"{p['name']}: goal model {p['prob']['goals>=1']:.0%}" for p in scorers] + mods, [p["name"] for p in scorers], funded=funded)
            if cand:
                out.append(cand)
                t3["passed"] = True
        else:
            t3["why_not"] = "needs a dominant favourite (no-vig 60%+) and two skaters with a goal model probability of 30%+"
        examined["theses"].append(t3)
    return out, examined


def evaluate_combination(now: dt.datetime, *, game: dict, sides_players: dict, specs: list[dict], pool_game: dict | None, avail_game: dict | None, plan: dict | None, product: dict | None) -> dict:
    """Evaluate a SPECIFIED set of legs of one game with the same gates and the same honesty as any candidate (used for regression cases such as COL@PHI and for a person's own idea).
    specs: {"code": "ML", "team": "COL"} or {"code": "PTS"|"SOG"|"GOAL", "player": "Nathan MacKinnon", "k": 1}. A leg that cannot be resolved is reported, not dropped."""
    verdicts, matrix = _verdicts(product or {})
    sk = {s: [_skater_view(p) for p in sides_players.get(s, [])] for s in ("home", "away")}
    allp = sk["home"] + sk["away"]
    legs, unresolved = [], []
    for sp in specs:
        if sp["code"] == "ML":
            side = "home" if sp["team"] == game["home"] else "away" if sp["team"] == game["away"] else None
            leg = moneyline_leg(game, side, now) if side else None
        else:
            hit = [p for p in allp if (p["name"] or "").lower() == sp["player"].lower()]
            leg = player_leg(game, hit[0], sp["code"], int(sp["k"]), pool_game, avail_game, plan, matrix, verdicts, now) if hit else None
        (legs if leg else unresolved).append(leg or sp)
    funded = True if not plan else (str(game["game_id"]) in (plan.get("games_priced") or []) or bool((pool_game or {}).get("players")))
    names = [l["label"] for l in legs]
    team = next((l["team"] for l in legs if l["team"]), game.get("home"))
    cand = _assemble(game, "SPECIFIED", team, legs, [f"specified: {', '.join(names)}"], names, funded=funded)
    if cand is not None and unresolved:
        cand["unresolved_legs"] = unresolved
        cand["blockers"].insert(0, f"LEGS NOT FOUND for this game's verified skaters: {unresolved}")
    return cand or {"error": "fewer than two legs could be resolved", "unresolved_legs": unresolved}


# ------------------------------------------------------------------------------------------- the day
def _verdicts(product: dict) -> tuple[dict, list]:
    mh = (product or {}).get("model_health") or {}
    return mh.get("skater_verdicts") or {}, mh.get("market_matrix") or []


def build(now: dt.datetime, *, games: list[dict], players_by_game: dict, product: dict, pool: dict | None, availability: dict | None, plan: dict | None, primary: dict | None, flagship: dict | None = None) -> dict:
    """The stage-A document for the board: candidates, theses examined, per-market availability, and the daily output."""
    verdicts, matrix = _verdicts(product)
    goalies = (product or {}).get("goalies") or {}
    pool_games = (pool or {}).get("games") or {}
    avail_games = (availability or {}).get("games") or {}
    candidates, examined, availability_rows = [], [], {}
    n_examined = 0
    for g in games:
        gid = str(g["game_id"])
        start = _parse(g.get("start_utc"))
        if g.get("state") != "SCHEDULED" or (start is not None and start <= now):
            continue                                        # started or finished: nothing is priced or recommended for it
        n_examined += 1
        try:
            availability_rows[gid] = {"matchup": f"{g['away']} @ {g['home']}", "start_et": g.get("start_et"),
                                      "markets": explain_markets(g, avail_games.get(gid), plan, pool_games.get(gid), matrix, now),
                                      "withdrawn_for_identity": [w.get("name") for w in ((g.get("coverage") or {}).get("integrity") or {}).get("withdrawn", [])]}
            cands, ex = candidates_for_game(g, players_by_game.get(gid) or {}, pool_games.get(gid), avail_games.get(gid), plan, matrix, verdicts, goalies, now)
        except Exception as exc:  # noqa: BLE001 - one game's failure must not remove the rest of the stage-A search; it is reported, never read as "nothing to find"
            examined.append({"game_id": gid, "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        candidates.extend(cands)
        examined.append(ex)
    errors = [e for e in examined if e.get("error")]
    ranked = sorted((c for c in candidates if c["support_score"] is not None), key=lambda c: (not c["game_funded"], -c["support_score"], -c["priced_legs"], c["id"]))     # a game whose prices can still arrive today outranks one the credit plan excludes
    qualifying = [c for c in ranked if c["support_score"] >= MIN_SUPPORT and c["all_legs_validated"]]        # a leg whose model did not beat its baselines cannot headline a candidate
    primary = primary or {}
    ticket = primary.get("ticket")
    if flagship is not None:                                # the official output is the flagship parlay or PASS; the candidate search below is information only
        if flagship["state"] == "UNAVAILABLE":
            output, headline = "UNAVAILABLE", "Eggy's flagship could not be read"
        elif flagship.get("flagship"):
            output, headline = FLAGSHIP, "OFFICIAL EGGY FLAGSHIP PARLAY (UNPRICED)"
        else:
            output, headline = PASS, "TRUE PASS"
    elif ticket:
        output, headline = OFFICIAL, "OFFICIAL EGGY PICK"
    elif qualifying:
        output, headline = BEST, "NO CERTIFIED EGGY PICK TODAY"
    elif errors:
        output, headline = "UNAVAILABLE", "Eggy's candidate search was incomplete"        # never a TRUE PASS when part of the search failed
    else:
        output, headline = PASS, "TRUE PASS"
    best = qualifying[0] if output == BEST else None
    official = flagship is not None
    out = {"version": 1, "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date_et": (product or {}).get("et_today"), "daily_output": output, "headline": headline,
           "best_candidate": best, "flagship": (flagship or {}).get("flagship"), "official_policy": ({"policy_id": OFFICIAL_POLICY["policy_id"], "digest": official_policy_digest()} if official else None), "other_candidates": [c for c in ranked[:6] if best is None or c["id"] != best["id"]][:5],
           "stage_b": {"official_ticket": bool(ticket), "closest_cross_game": primary.get("closest"), "reason": primary.get("reason"), "strongest_singles": primary.get("strongest_singles")},
           "examined": {"games": n_examined, "theses_per_team": 3, "candidates": len(candidates), "support_floor": MIN_SUPPORT, "by_game": examined, "games_failed": len(errors)},
           "availability": availability_rows,
           "notes": ["Stage A looks at the whole real slate and never records anything; stage B (the production recording policy) is unchanged and is the only source of a recorded paper bet.",
                     "Joint probabilities and combined prices of same-game candidates are NOT claimed; see each candidate's dependence section.",
                     "The support score ranks candidates; it is not a probability and not value.",
                     "Moneyline legs are included without stage B's model-agreement requirement: a candidate is a thesis to look at, not a qualified pick."]}
    if official and output == "UNAVAILABLE":
        out["error"] = flagship["reason"]
    elif output == "UNAVAILABLE":
        out["error"] = f"{len(errors)} game(s) could not be searched: " + "; ".join(f"{e['game_id']}: {e['error']}" for e in errors[:3])
    return out
