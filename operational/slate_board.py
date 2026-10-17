"""
The per-game board: for EVERY game of the Eastern day, four short analyses and what DraftKings actually offers, then ONE primary parlay.

    1. Most likely goal scorers      (the model's anytime-goal probability; the price shown beside it; likelihood is not value)
    2. Two best shots-on-goal values (the best supported threshold of each player from the offered 1+..5+ ladder, likelihood balanced against the price)
    3. Goalie saves                  (expected starter, expected saves and range, the offered line; conditional until the start is confirmed)
    4. Moneyline or puck line        (the better-supported market; "No recommended side" when neither is defensible)

Nothing here is a new model. Every number comes from the engine's existing, validated pieces: the skater projection (research/product_models), the goalie/team model, the corrected shots estimate
(anchor.shots_final_estimate, cap +0.02), DraftKings prices as archived with their provider update times, and the price-availability record that says why a price is missing. The primary parlay is
produced by the same selector as recording (operational/daily_tickets._primary_slip), so the board, the Best Options page and any recorded ticket cannot disagree.

Written by the 15-minute trader after the product state, published as the `slate_board` snapshot section, and read by the hosted page. Informational: recording is untouched by anything in this file.
"""
from __future__ import annotations

import datetime as dt
import json
import math

from operational import state_paths

NAME = "slate_board.json"
MIN_TOI = 12.0                    # expected minutes: below this a skater is a marginal role, not a pick
MIN_NHL_GAMES = 40                # the same bar the engine uses for pricing (operational.pricing_policy.MIN_GAMES_FOR_PRICING): fewer games and the projection over-predicts in testing
FALLBACK_HAIRCUT = 0.03           # used only when the policy cannot be loaded; the board then says so (doc["policy_error"]) instead of presenting it as the policy's


def _policy_haircut() -> tuple[float, str | None]:
    """(the policy's own per-leg haircut, None) or (the fallback, why). The board's 'after the haircut' figures are the slip's, or are labelled as not being."""
    try:
        from operational import ticket_policy
        return float(ticket_policy.checked_active().value_shade), None
    except Exception as exc:  # noqa: BLE001
        return FALLBACK_HAIRCUT, f"{exc.__class__.__name__}: {exc}"


def _haircut() -> float:
    return _policy_haircut()[0]
SHOTS_PICK_FLOOR = 0.45           # a threshold must be at least this likely to be offered as a "balanced" pick; below it the ladder is a longshot, not a value pick
SHOTS_PICK_MAX_IMPLIED = 0.80     # ...and not priced shorter than about -400: a 1+ at -4000 has almost no upside, so it is not the "balance of likelihood and price" the pick is for
TOP_SCORERS = 5
SAVES_MODEL_KEY = "goalie-saves"
STATUS_WORDS = {"POSTED": "priced", "NOT_POSTED": "not posted by DraftKings", "NOT_FETCHED": "not fetched", "BUDGET_BLOCKED": "budget blocked", "FETCH_ERROR": "fetch failed"}
MARKETS = (("player_shots_on_goal_alternate", "Shots (1+ to 5+)"), ("player_goal_scorer_anytime", "Anytime goal"), ("player_total_saves", "Goalie saves"))


# ------------------------------------------------------------------ small helpers

def _dec(american: float) -> float:
    from operational import builder_pool                    # the one decimal-price definition the builder and the slip use
    return builder_pool._decimal(american)


def _implied(american: float) -> float:
    return 1.0 / _dec(american)


def _fmt_price(a) -> str:
    return "—" if a is None else f"{int(round(a)):+d}"


def _n(v, digits: int = 1) -> str:
    """A number for text, or a dash when the projection does not have it (a thin-sample goalie must not break the whole board)."""
    return "—" if v is None else f"{v:.{digits}f}"


def _pct(p) -> str:
    return "—" if p is None else f"{p * 100:.0f}%"


def _ev(p: float, american: float) -> float:
    return p * _dec(american) - 1.0


def _ev_haircut(p: float, american: float, haircut: float | None = None) -> float:
    """Value with the policy's per-leg haircut, by the same function the selector uses for a ticket (daily_tickets.haircut_ev)."""
    from operational import daily_tickets
    return daily_tickets.haircut_ev([p], _dec(american), _haircut() if haircut is None else haircut)


def _eligible(p: dict) -> bool:
    proj = p.get("projection") or {}
    toi = ((proj.get("expected") or {}).get("toi")) or 0.0
    return bool(proj.get("pricing_eligible")) and toi >= MIN_TOI and (p.get("games_total") or 0) >= MIN_NHL_GAMES and p.get("position") != "G"


def _market_status(avail_game: dict, key: str, now: dt.datetime | None = None) -> dict:
    m = ((avail_game or {}).get("markets") or {}).get(key)
    if not m:
        return {"status": "NOT_FETCHED", "word": "not fetched", "detail": "never requested for this game today (the day's credits did not reach it, or its window has not opened)"}
    st = m.get("status") or "NOT_FETCHED"
    word, detail = STATUS_WORDS.get(st, st.lower()), m.get("detail")
    if st == "NOT_POSTED" and now is not None and m.get("checked_utc"):
        # "not posted" is only what DraftKings had posted when it was asked; an answer from an earlier day is not today's answer
        from operational import eastern_time as et, quote_freshness as qf
        checked = qf.parse_utc(m["checked_utc"])
        if checked is not None and et.eastern_today(checked) != et.eastern_today(now):
            word = "not posted when last asked (an earlier day); not asked again yet today"
    if st == "NOT_POSTED" and m.get("checked_utc") and "earlier day" not in word:
        from zoneinfo import ZoneInfo
        from operational import quote_freshness as qf
        asked = qf.parse_utc(m["checked_utc"])
        if asked is not None:
            word += f" (asked at {asked.astimezone(ZoneInfo('America/New_York')).strftime('%-I:%M %p')} ET)"
    return {"status": st, "word": word, "detail": detail, "checked_utc": m.get("checked_utc"), "quote_updated_utc": m.get("last_update"), "outcomes": m.get("outcomes")}


def _season_line(p: dict) -> str:
    s = p.get("season") or {}
    prior = p.get("prior_season") or {}
    cur = f"{s.get('games', 0)} GP, {s.get('goals', 0):.0f} G, {s.get('shots', 0):.0f} SOG this season" if s.get("games") else "no games this season"
    base = f"; last season {prior['games']} GP, {prior['goals_per_game']:.2f} G and {prior['shots_per_game']:.1f} SOG per game" if prior else "; no last-season NHL baseline"
    return cur + base


def _sample_note(p: dict) -> str | None:
    n = (p.get("season") or {}).get("games") or 0
    return f"only {n} games this season, so the prior seasons carry most of the estimate" if n < 10 else None


# ------------------------------------------------------------------ section 1: goal scorers

def scorers(game: dict, side_players: list[dict], pool_game: dict | None, opp_goalie: dict | None, haircut: float | None = None) -> list[dict]:
    rows = []
    for p in side_players:
        if not _eligible(p):
            continue
        proj = p["projection"]
        pg = (proj["probabilities"] or {}).get("goals>=1")
        if pg is None:
            continue
        exp = proj["expected"]
        price_info = None
        entry = ((pool_game or {}).get("players") or {}).get(str(p["player_id"]))
        goal = ((entry or {}).get("m") or {}).get("GOAL")
        if goal and goal.get("l"):
            k, price, _ = goal["l"][0]
            price_info = {"american": price, "implied": round(_implied(price), 4), "quote_updated_utc": goal.get("q"), "retrieved_at_utc": goal.get("r"),
                          "ev_estimated": round(_ev(pg, price), 4), "ev_after_haircut": round(_ev_haircut(pg, price, haircut), 4)}
        why = [f"model {_pct(pg)} to score", f"{exp.get('shots', 0):.1f} expected shots", f"{exp.get('toi', 0):.0f} min expected" + (f" ({exp.get('toi_pp', 0):.1f} on the power play)" if exp.get("toi_pp") else ""),
               _season_line(p)]
        if opp_goalie and (opp_goalie.get("season") or {}).get("save_pct"):
            sv = opp_goalie["season"]
            why.append(f"likely opposing goalie {opp_goalie['name']} (.{int(round(sv['save_pct'] * 1000)):03d} SV% in {sv.get('games', 0)} GP this season, start unconfirmed)")
        note = _sample_note(p)
        rows.append({"player_id": str(p["player_id"]), "name": p["name"], "team": p["team"], "position": p["position"], "probability": round(pg, 4), "expected_goals": exp.get("goals"),
                     "expected_shots": exp.get("shots"), "expected_toi": exp.get("toi"), "expected_toi_pp": exp.get("toi_pp"), "usage_tier": p.get("usage_tier"), "pp_usage": p.get("pp_usage"),
                     "price": price_info, "explanation": "; ".join(why) + (f". Note: {note}" if note else ""), "model_version": proj.get("model_version")})
    return rows


# ------------------------------------------------------------------ section 2: shots value

def shots_picks(game: dict, side_players: list[dict], pool_game: dict | None, haircut: float | None = None) -> dict:
    from research.real_market_parlay import anchor
    cands = []
    for p in side_players:
        if not _eligible(p):
            continue
        entry = ((pool_game or {}).get("players") or {}).get(str(p["player_id"]))
        sog = ((entry or {}).get("m") or {}).get("SOG")
        if not sog or not sog.get("l"):
            continue
        rec = sog.get("rec") or {}
        ladder = []
        for k, price, prob in sog["l"]:
            r = rec.get(str(k))
            final = r[2] if r and len(r) > 2 and r[2] is not None else None
            n_cur = r[3] if r and len(r) > 3 else None
            if final is None:
                continue                                              # no record-based estimate for this line: not offered as a pick (the model alone is not the ticket estimate)
            ladder.append({"threshold": int(k), "american": price, "model": prob, "recent_hits": r[0], "recent_games": r[1], "games_this_season": n_cur, "estimate": round(final, 4),
                           "implied": round(_implied(price), 4), "ev_estimated": round(_ev(final, price), 4), "ev_after_haircut": round(_ev_haircut(final, price, haircut), 4),
                           "supported_for_slip": final >= anchor.SHOTS_LEG_FLOOR})
        usable = [x for x in ladder if x["estimate"] >= SHOTS_PICK_FLOOR and x["implied"] <= SHOTS_PICK_MAX_IMPLIED]
        if not usable:
            continue
        best = max(usable, key=lambda x: (x["ev_after_haircut"], x["estimate"]))
        exp = (p["projection"] or {}).get("expected") or {}
        recent = p.get("recent_avg") or {}
        value_word = (f"value {best['ev_after_haircut']:+.0%} after the 3-point haircut" if best["ev_after_haircut"] > 0 else
                      f"no value at this price ({best['ev_after_haircut']:+.0%} after the haircut): the best balance on offer, not a bet worth making on its own")
        why = (f"{best['threshold']}+ at {_fmt_price(best['american'])} ({value_word}; the price implies {_pct(best['implied'])}): estimate {_pct(best['estimate'])} from {best['recent_hits']} of his last "
               f"{best['recent_games']} games" + (f" ({best['games_this_season']} this season)" if best.get("games_this_season") is not None else "") + f", model {_pct(best['model'])}. "
               f"{exp.get('shots', 0):.1f} expected shots on {exp.get('toi', 0):.0f} min; recent average {recent.get('shots', 0):.1f} shots; {_season_line(p)}.")
        cands.append({"player_id": str(p["player_id"]), "name": p["name"], "team": p["team"], "position": p["position"], "chosen": best, "ladder": sorted(ladder, key=lambda x: x["threshold"]),
                      "quote_updated_utc": sog.get("q"), "retrieved_at_utc": sog.get("r"), "explanation": why, "has_value": best["ev_after_haircut"] > 0, "expected_shots": exp.get("shots"), "expected_toi": exp.get("toi"),
                      "note": _sample_note(p)})
    cands.sort(key=lambda c: (-c["chosen"]["ev_after_haircut"], -c["chosen"]["estimate"]))
    picks = []
    teams_seen: dict[str, int] = {}
    for c in cands:
        if len(picks) == 2:
            break
        picks.append(c)
        teams_seen[c["team"]] = teams_seen.get(c["team"], 0) + 1
    out = {"picks": picks, "shortfall": None}
    if len(picks) < 2:
        n_elig = sum(1 for p in side_players if _eligible(p))
        out["shortfall"] = ("no shots prices for this game" if not cands and not ((pool_game or {}).get("players")) else
                            f"only {len(picks)} of {n_elig} eligible skaters has a priced threshold with an estimate of at least {SHOTS_PICK_FLOOR:.0%}")
    return out


# ------------------------------------------------------------------ section 3: goalies

def _saves_over_probability(proj: dict | None, line: float) -> float | None:
    if not proj:
        return None
    need = int(math.floor(line)) + 1                                    # a 24.5 line goes over on 25 or more saves
    return (proj.get("saves_probabilities") or {}).get(f"saves>={need}")


def goalie_block(side: str, game: dict, goalies: dict, saves_lines: dict, saves_verdict_ok: bool, haircut: float | None = None) -> dict:
    cands = ((game.get("goalies") or {}).get(side)) or []
    if isinstance(cands, dict) or not cands:
        return {"side": side, "team": game[side], "starter": None, "reason": "no expected-starter list on file for this team"}
    ranked = sorted(cands, key=lambda c: -(c.get("start_probability") or 0.0))
    top = ranked[0]
    g = goalies.get(str(top["player_id"])) or {}
    proj = g.get("projection") or {}
    conf = g.get("confirmation") or {}
    confirmed = (conf.get("status") or top.get("status")) == "CONFIRMED"
    line = (saves_lines or {}).get(str(top["player_id"]))
    out = {"side": side, "team": game[side], "starter": {"player_id": str(top["player_id"]), "name": g.get("name") or top.get("name"),
                                                         "start_probability": top.get("start_probability"), "status": conf.get("status") or top.get("status") or "UNCONFIRMED",
                                                         "source": conf.get("source"), "checked_at_utc": conf.get("checked_at_utc"),
                                                         "note": conf.get("note")},
           "alternate": ({"name": (goalies.get(str(ranked[1]["player_id"])) or {}).get("name") or ranked[1].get("name"), "start_probability": ranked[1].get("start_probability")} if len(ranked) > 1 else None),
           "projection": None, "line": None, "recommendation": None}
    if not proj:
        out["projection_unavailable"] = "No projection is available for this goalie yet (too few starts logged); nothing is estimated."
    if proj:
        season = g.get("season") or {}
        out["projection"] = {"expected_saves": proj.get("expected_saves"), "saves_range_80": proj.get("saves_range_80"), "expected_shots_against": proj.get("expected_shots_against"),
                             "save_pct_used": proj.get("save_pct_used"), "expected_goals_against": proj.get("expected_goals_against"), "opponent": proj.get("opponent"),
                             "season_save_pct": season.get("save_pct"), "season_games": season.get("games"), "model_version": proj.get("model_version"),
                             "explanation": (f"{proj.get('opponent')} are expected to put about {_n(proj.get('expected_shots_against'), 0)} shots on him; at a "
                                             f"{_n(None if proj.get('save_pct_used') is None else proj['save_pct_used'] * 100, 1)}% save rate that is about {_n(proj.get('expected_saves'), 0)} saves "
                                             f"(80% range {(proj.get('saves_range_80') or ['?', '?'])[0]}–{(proj.get('saves_range_80') or ['?', '?'])[1]}) and "
                                             f"{_n(proj.get('expected_goals_against'), 1)} goals against. Saves are not shots faced: the range covers shot volume and goals conceded.")}
    if line:
        p_over = _saves_over_probability(proj, line["line"])
        out["line"] = {**line, "p_over": p_over, "p_under": (None if p_over is None else round(1 - p_over, 4)),
                       "ev_over": (None if p_over is None or line.get("over") is None else round(_ev_haircut(p_over, line["over"], haircut), 4)),
                       "ev_under": (None if p_over is None or line.get("under") is None else round(_ev_haircut(1 - p_over, line["under"], haircut), 4))}
        if not confirmed:
            out["recommendation"] = {"side": None, "conditional": True, "text": "Conditional on the start: the starter is unconfirmed, so no saves selection is made and none goes in the slip."}
        elif not saves_verdict_ok or p_over is None:
            out["recommendation"] = {"side": None, "conditional": False, "text": "No recommendation: the model does not support this line (outside its modelled ladder or not validated)."}
        else:
            ev_o, ev_u = out["line"]["ev_over"], out["line"]["ev_under"]
            best = max((("OVER", ev_o), ("UNDER", ev_u)), key=lambda t: (t[1] if t[1] is not None else -9))
            out["recommendation"] = ({"side": best[0], "conditional": False, "ev_after_haircut": best[1], "text": f"{best[0].title()} {line['line']}: value {best[1]:+.0%} after the haircut"} if best[1] and best[1] > 0
                                     else {"side": None, "conditional": False, "text": "No recommendation: neither side has value after the haircut."})
    else:
        out["recommendation"] = {"side": None, "conditional": not confirmed, "text": ("No saves line is on file for this goalie. " + ("A projection is shown, conditional on the start." if not confirmed else ""))}
    return out


# ------------------------------------------------------------------ section 4: moneyline / puck line

def _quote_fresh(american_row: dict, now: dt.datetime, start_utc: str | None) -> bool:
    """A moneyline quote is fresh by its own provider time against the engine's price limit (operational/quote_freshness.price_limit_min, the limit its slip legs use); the snapshot row's
    status says only that the quote is the latest one. The hosted page repeats this judgement when it is opened (dashboard/pages/41_Slate_Board.py)."""
    from operational import quote_freshness as qf
    start = qf.parse_utc(start_utc)
    hours = (start - now).total_seconds() / 3600.0 if start is not None else 99.0
    return bool(qf.assess(american_row.get("quote_captured_at_utc"), None, now, qf.price_limit_min(hours), require_retrieval=False)["fresh"])


def side_block(game: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    ml = game.get("moneyline") or {}
    wp = game.get("win_probability") or {}
    home, away = ml.get("home"), ml.get("away")
    out = {"market": "Moneyline", "puck_line": {"status": "NOT_ASSESSED",
                                                 "text": ("Puck line (±1.5) not assessed: the cover model (puck-line-direct-v1) has no validated result yet (its prospective sample is still building), and no spreads price is fetched. "
                                                          "The better-supported market here is the moneyline.")}}
    if not home or not away or home.get("american") is None or away.get("american") is None:
        out.update(recommendation=None, reason="No moneyline prices on file for this game.")
        return out
    fresh = (home.get("status") == "ACTIVE" and away.get("status") == "ACTIVE" and _quote_fresh(home, now, game.get("start_utc")) and _quote_fresh(away, now, game.get("start_utc")))
    ih, ia = _implied(home["american"]), _implied(away["american"])
    nv_home = ih / (ih + ia)
    sides = {"home": {"team": game["home"], "american": home["american"], "no_vig": round(nv_home, 4), "model": wp.get("home"), "quote_captured_at_utc": home.get("quote_captured_at_utc")},
             "away": {"team": game["away"], "american": away["american"], "no_vig": round(1 - nv_home, 4), "model": wp.get("away"), "quote_captured_at_utc": away.get("quote_captured_at_utc")}}
    out["sides"] = sides
    out["fresh"] = fresh
    fav = "home" if nv_home >= 0.5 else "away"
    f = sides[fav]
    agrees = (f["model"] or 0.5) > 0.5
    if not fresh:
        out.update(recommendation=None, reason="The moneyline quote is past its freshness limit: shown for reference, not recommended.")
    elif f["no_vig"] < 0.55:
        out.update(recommendation=None, reason=f"No recommended side: the favourite ({f['team']}) is only {f['no_vig']:.0%} on the market's own no-vig price, a near coin flip.")
    elif not agrees:
        out.update(recommendation=None, reason=f"No recommended side: the market favours {f['team']} ({f['no_vig']:.0%}) but the strength model does not ({_pct(f['model'])}).")
    else:
        out.update(recommendation={"market": "Moneyline", "team": f["team"], "american": f["american"], "estimate": f["no_vig"], "implied_with_vig": round(_implied(f["american"]), 4),
                                   "model": f["model"], "quote_captured_at_utc": f["quote_captured_at_utc"],
                                   "explanation": (f"{f['team']} {_fmt_price(f['american'])}: the market's no-vig chance is {f['no_vig']:.0%} and the strength model "
                                                   f"agrees ({_pct(f['model'])}). The estimate used for a slip is the market's own chance, so the moneyline carries no edge of its own; it only completes a slip.")},
                   reason=None)
    return out


# ------------------------------------------------------------------ the document

FREEZABLE_STATES = ("STARTED", "LIVE", "FINAL")


def _frozen_block(previous_doc: dict | None, today: str, g: dict) -> tuple[dict, dict] | None:
    """A game that has started (or finished) keeps the analysis it had before puck drop, labelled as frozen, instead of collapsing to "not scheduled". It comes from the last board built for the same
    Eastern day while the game was still scheduled; it is never recomputed with in-game information and is not a current recommendation."""
    if g.get("state") not in FREEZABLE_STATES:                      # a postponed or cancelled game has no "pregame analysis as of puck drop"
        return None
    if not isinstance(previous_doc, dict) or previous_doc.get("date_et") != today:
        return None
    prev = next((x for x in previous_doc.get("games") or [] if isinstance(x, dict) and x.get("game_id") == g.get("game_id")), None)
    if prev is None or not (prev.get("coverage") or {}).get("analysis_available"):
        return None
    import copy
    prev = copy.deepcopy(prev)
    at = prev.get("frozen_at_utc") or previous_doc.get("generated_at_utc")
    cov = prev["coverage"]
    cov["frozen_at_utc"] = at
    cov["recommended"] = []                                         # nothing on a started game is a current recommendation
    # The frozen block is for reference: every recommendation-shaped field is made inert so no page path can show it as a current pick.
    note = "The game has started: the pregame view is kept for reference and is not a current recommendation."
    prev["side"] = {**(prev.get("side") or {}), "recommendation": None, "reason": note}
    for gb in prev.get("goalies") or []:
        gb["recommendation"] = {"side": None, "conditional": True, "text": note}
    for r in prev.get("scorers") or []:
        r["best_value_at_price"] = False
    for pick in ((prev.get("shots") or {}).get("picks") or []):
        pick["has_value"] = False
        if isinstance(pick.get("chosen"), dict):
            pick["chosen"]["supported_for_slip"] = False
    entry = {**prev, "state": g.get("state"), "start_et": g.get("start_et") or prev.get("start_et"), "frozen_at_utc": at, "coverage": cov}
    mk = cov.get("markets") or {}
    bump = {"analysed": 1, "shots_priced": 1 if (mk.get("player_shots_on_goal_alternate") or {}).get("status") == "POSTED" else 0,
            "goals_priced": 1 if (mk.get("player_goal_scorer_anytime") or {}).get("status") == "POSTED" else 0,
            "saves_priced": 1 if (mk.get("player_total_saves") or {}).get("status") == "POSTED" else 0,
            "moneyline_priced": 1 if cov.get("moneyline_priced") else 0, "with_recommendation": 0}
    return entry, bump


def build_doc(now: dt.datetime, product: dict, pool: dict | None, tickets: dict | None, availability: dict | None, saves_by_game: dict | None = None,
              previous_day: dict | None = None, saves_errors: dict | None = None, previous_doc: dict | None = None) -> dict:
    today = product.get("et_today")
    haircut, policy_error = _policy_haircut()
    games = [g for g in (product.get("games") or {}).get("games", []) if g.get("date_et") == today and g.get("type") == "REGULAR"]
    games.sort(key=lambda g: (g.get("start_utc") or "", g["game_id"]))
    players = product.get("players") or {}
    goalies = product.get("goalies") or {}
    details = product.get("game_details") or {}
    pool_games = (pool or {}).get("games") or {}
    avail_games = (availability or {}).get("games") or {}
    saves_ok = False
    for m in ((product.get("model_health") or {}).get("models") or []):
        if m.get("id") == SAVES_MODEL_KEY:
            summ = ((m.get("validation") or {}).get("summary") or {})
            saves_ok = bool(summ.get("markets_scored")) and summ.get("beating_baselines") == summ.get("markets_scored")
    out_games = []
    counts = {"scheduled": len(games), "analysed": 0, "shots_priced": 0, "goals_priced": 0, "saves_priced": 0, "moneyline_priced": 0, "with_recommendation": 0}
    def _one(g: dict):
        gid = g["game_id"]
        d = details.get(gid) or {}
        sides = d.get("sides") or {}
        scheduled = g.get("state") == "SCHEDULED"
        side_players = {s: [players[i] for i in ((sides.get(s) or {}).get("skater_ids") or []) if i in players] for s in ("home", "away")}
        all_players = side_players["home"] + side_players["away"]
        pg = pool_games.get(gid)
        gl_by_side = {}
        for s in ("home", "away"):
            cands = ((g.get("goalies") or {}).get(s)) or []
            if isinstance(cands, list) and cands:
                top = max(cands, key=lambda c: c.get("start_probability") or 0.0)
                gl_by_side[s] = goalies.get(str(top["player_id"]))
        analysis_ok = bool(scheduled and all_players and any(_eligible(p) for p in all_players))
        sc = []
        if analysis_ok:
            ranked = []
            for s_, opp in (("home", "away"), ("away", "home")):
                ranked.extend(scorers(g, side_players[s_], pg, gl_by_side.get(opp), haircut))           # the opposing goalie differs by team
            ranked.sort(key=lambda r: (-r["probability"], r["name"]))
            top = ranked[:TOP_SCORERS]
            priced = [r for r in ranked if r["price"]]
            best = max(priced, key=lambda r: r["price"]["ev_after_haircut"]) if priced else None
            for i_, r in enumerate(top, 1):
                r["rank"], r["outside_top"] = i_, False
                r["best_value_at_price"] = bool(best and r["player_id"] == best["player_id"] and best["price"]["ev_after_haircut"] > 0)
            sc = top
            if best and best["player_id"] not in {r["player_id"] for r in top}:
                sc = top + [{**best, "rank": None, "outside_top": True, "best_value_at_price": best["price"]["ev_after_haircut"] > 0}]
        shots = shots_picks(g, all_players, pg, haircut) if analysis_ok else {"picks": [], "shortfall": "game is not scheduled or has no projected skaters"}
        gblocks = [goalie_block(s, g, goalies, (saves_by_game or {}).get(gid, {}), saves_ok, haircut) for s in ("home", "away")] if scheduled else []
        side = side_block(g, now) if scheduled else {"market": "Moneyline", "recommendation": None, "reason": "Game not scheduled."}
        mk = {key: _market_status(avail_games.get(gid), key, now) for key, _ in MARKETS}
        ml_priced = bool((g.get("moneyline") or {}).get("home"))
        recommended, missing = [], []
        if sc and any(r["price"] and r.get("best_value_at_price") for r in sc):
            recommended.append("Goal scorer with value at its price")
        n_val = sum(1 for c in shots["picks"] if c["has_value"] and c["chosen"]["supported_for_slip"])
        if n_val:
            recommended.append(f"{n_val} shots pick(s) with value at the price")
        if side.get("recommendation"):
            recommended.append("Moneyline side")
        for b in gblocks:
            r = b.get("recommendation") or {}
            if r.get("side"):
                recommended.append(f"Saves {r['side'].lower()} ({b['team']})")
        if mk["player_shots_on_goal_alternate"]["status"] != "POSTED":
            missing.append({"item": "Shots prices", "reason": mk["player_shots_on_goal_alternate"]["word"] + (f": {mk['player_shots_on_goal_alternate']['detail']}" if mk["player_shots_on_goal_alternate"].get("detail") else "")})
        if mk["player_goal_scorer_anytime"]["status"] != "POSTED":
            missing.append({"item": "Anytime-goal prices", "reason": mk["player_goal_scorer_anytime"]["word"]})
        if (saves_errors or {}).get(gid):
            missing.append({"item": "Saves line", "reason": f"the archived saves capture could not be read: {saves_errors[gid]}"})
        elif mk["player_total_saves"]["status"] != "POSTED":
            missing.append({"item": "Saves line", "reason": mk["player_total_saves"]["word"] + " (a price is collected whether or not a starter is confirmed, credits permitting; a saves selection still needs a confirmed starter)"})
        for mw in (avail_games.get(gid) or {}).get("missed_windows") or []:
            missing.append({"item": "Pregame price capture", "reason": f"window missed ({mw.get('window', '').lower()}): " + str(mw.get("detail") or ""), "tags": mw.get("tags") or []})
        if not ml_priced:
            missing.append({"item": "Moneyline price", "reason": "no moneyline on file"})
        missing.append({"item": "Puck line price", "reason": "not requested (spreads are not part of the capture plan); the cover model is unvalidated"})
        if shots.get("shortfall"):
            missing.append({"item": "Second shots pick" if shots["picks"] else "Shots picks", "reason": shots["shortfall"]})
        cov = {"analysis_available": analysis_ok, "skaters_projected": sum(1 for p in all_players if (p.get("projection") or {}).get("pricing_eligible")),
               "skaters_eligible": sum(1 for p in all_players if _eligible(p)), "goalies_projected": sum(1 for b in gblocks if b.get("projection")),
               "team_model": bool((g.get("win_probability") or {}).get("home") is not None),
               "markets": mk, "moneyline_priced": ml_priced, "models": {"skater_shots": "SUPPORTED", "anytime_goal": "SUPPORTED", "goalie_saves": "SUPPORTED_NEEDS_CONFIRMED_STARTER" if saves_ok else "NOT_SUPPORTED",
                                                                        "moneyline": "STRENGTH_MODEL_AND_MARKET", "puck_line": "NOT_VALIDATED"},
               "recommended": recommended, "missing": missing}
        bump = {"analysed": 1 if analysis_ok else 0, "shots_priced": 1 if mk["player_shots_on_goal_alternate"]["status"] == "POSTED" else 0,
                "goals_priced": 1 if mk["player_goal_scorer_anytime"]["status"] == "POSTED" else 0, "saves_priced": 1 if mk["player_total_saves"]["status"] == "POSTED" else 0,
                "moneyline_priced": 1 if ml_priced else 0, "with_recommendation": 1 if recommended else 0}
        return ({"game_id": gid, "away": g["away"], "home": g["home"], "start_utc": g.get("start_utc"), "start_et": g.get("start_et"), "state": g.get("state"),
                 "scorers": sc, "shots": shots, "goalies": gblocks, "side": side, "coverage": cov}, bump)

    counts["failed_games"] = 0
    for g in games:
        try:
            entry, bump = _one(g)
        except Exception as exc:  # noqa: BLE001 - one game's failure must not stop the others; it is recorded on that game, counted, and surfaced (never silent)
            import traceback
            counts["failed_games"] += 1
            out_games.append({"game_id": g.get("game_id"), "away": g.get("away"), "home": g.get("home"), "start_utc": g.get("start_utc"), "start_et": g.get("start_et"), "state": g.get("state"),
                              "scorers": [], "shots": {"picks": [], "shortfall": "analysis failed for this game"}, "goalies": [], "side": {"market": "Moneyline", "recommendation": None, "reason": "analysis failed for this game"},
                              "error": f"{exc.__class__.__name__}: {exc}", "error_where": traceback.format_exc().strip().splitlines()[-3:],
                              "coverage": {"analysis_available": False, "markets": {}, "recommended": [], "missing": [{"item": "Analysis", "reason": f"failed: {exc.__class__.__name__}: {exc}"}]}})
            continue
        if not (entry.get("coverage") or {}).get("analysis_available") and g.get("state") != "SCHEDULED":
            frozen = _frozen_block(previous_doc, today, g)
            if frozen is not None:
                entry, bump = frozen
        for k, v in bump.items():
            counts[k] += v
        out_games.append(entry)
    primary = (tickets or {}).get("primary")
    doc = {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date_et": today, "counts": counts, "games": out_games, "primary": primary,
           "policy": (tickets or {}).get("ticket_policy"), "recording_paused": bool((tickets or {}).get("recording_paused")),
           "models": {"skaters": "player-rate-toi-v2 (corrected shots estimate, cap +0.02)", "goalies": "goalie-team-v1", "teams": "goalie-team-v1 strength model"},
           "previous_day": previous_day, "policy_error": policy_error, "haircut": round(haircut, 4), "input_errors": dict(saves_errors or {}), "notes": [
               "Likely to score is not the same as good value at the price: a goal scorer is shown with its price and its value after a 3-point haircut, and goals are never put in a slip.",
               "Thresholds are not 'safe': every probability is an estimate with the uncertainty the record's small sample implies.",
               "A same-game combination would need a quoted combined price; none is available from this feed, so the primary slip is always cross-game with a calculated price."]}
    return doc


# ------------------------------------------------------------------ IO and the live inputs

def _path():
    return state_paths.path(NAME)


def load() -> dict | None:
    try:
        return json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _saves_lines(pool: dict | None, now: dt.datetime, errors: dict | None = None) -> dict:
    """{game_id: {goalie_id: {"line", "over", "under", "quote_updated_utc"}}} from the newest archived saves capture of each game, for ANY goalie (the builder's list hides lines of unconfirmed
    goalies; the board shows them as conditional)."""
    from operational import best_bets as bb
    out: dict = {}
    for gid, g in ((pool or {}).get("games") or {}).items():
        eid = g.get("event_id")
        if not eid:
            continue
        try:
            cap = bb.latest_capture(eid, market="player_total_saves")
        except Exception as exc:  # noqa: BLE001 - kept observable: the game's coverage block and the document's input_errors say the capture could not be read (not "no line posted")
            if errors is not None:
                errors[gid] = f"{exc.__class__.__name__}: {exc}"
            cap = None
        if not cap:
            continue
        retrieved, payload = cap
        for bm in payload.get("bookmakers") or []:
            if bm.get("key") != "draftkings":
                continue
            for m in bm.get("markets") or []:
                if m.get("key") != "player_total_saves":
                    continue
                for o in m.get("outcomes") or []:
                    if o.get("price") is None or o.get("point") is None or not o.get("description") or o.get("name") not in ("Over", "Under"):
                        continue
                    rec = out.setdefault(gid, {}).setdefault(bb.norm_name(o["description"]), {"name": o["description"], "line": float(o["point"]), "quote_updated_utc": m.get("last_update") or bm.get("last_update")})
                    if o.get("name") == "Over":
                        rec["over"] = float(o["price"])
                    elif o.get("name") == "Under":
                        rec["under"] = float(o["price"])
    return out


def _by_goalie_id(saves_by_name: dict, goalies: dict) -> dict:
    from operational import best_bets as bb
    index = {bb.norm_name(g.get("name") or ""): pid for pid, g in goalies.items()}
    out = {}
    for gid, byname in saves_by_name.items():
        for nm, rec in byname.items():
            pid = index.get(nm)
            if pid:
                out.setdefault(gid, {})[pid] = rec
    return out


SCAN_STATE = "slate_board_scan.json"


def _day_games_in_log(day: str) -> tuple[set, set]:
    """(games with a shots leg, games with a moneyline leg) in the frozen-prediction log for one Eastern day, read INCREMENTALLY: only the bytes appended since the last refresh are examined, the
    position and the day's running result are kept in a small state file (so a restart continues, and a rotated or shortened log is rescanned). The log itself is never rewritten."""
    from operational import shadow_selection
    path = state_paths.path(shadow_selection.NAME)
    scan_path = state_paths.path(SCAN_STATE)
    try:
        size = path.stat().st_size
    except OSError:
        return set(), set()
    st = {}
    try:
        st = json.loads(scan_path.read_text())
    except (OSError, json.JSONDecodeError):
        st = {}
    if st.get("day") != day or int(st.get("offset", 0)) > size:
        st = {"day": day, "offset": 0, "shots": [], "moneyline": []}
    shots, ml = set(st.get("shots") or []), set(st.get("moneyline") or [])
    needle = f'"et_date":"{day}"'.encode()
    with open(path, "rb") as fh:
        fh.seek(int(st["offset"]))
        data = fh.read()
    cut = data.rfind(b"\n")
    if cut >= 0:                                           # complete lines only: a record being written is picked up on the next refresh
        for line in data[:cut + 1].splitlines():
            if needle in line:
                rec = json.loads(line)
                if rec.get("et_date") == day:
                    for row in rec.get("pool") or []:
                        (ml if row[2] == "MONEYLINE" else shots).add(str(row[0]))
        st["offset"] = int(st["offset"]) + cut + 1
    st["shots"], st["moneyline"] = sorted(shots), sorted(ml)
    try:
        tmp = scan_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        tmp.replace(scan_path)
    except OSError:
        pass                                               # the scan state is an optimisation: without it the next refresh rescans
    return shots, ml


def _previous_day_check(product: dict, today: str) -> dict | None:
    """Yesterday's slate against what was priced and evaluated: the count reconciliation behind 'reported 16 games, captured 3'. Priced = DraftKings shots prices posted and archived;
    evaluated = games that had at least one leg in the frozen-prediction log that day."""
    try:
        from operational import price_availability as pa, shadow_selection
        prev = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
        games = [g for g in (product.get("games") or {}).get("games", []) if g.get("date_et") == prev and g.get("type") == "REGULAR"]
        if not games:
            return None
        av = (pa.read(prev).get("games") or {})
        posted = {gid for gid, v in av.items() if ((v.get("markets") or {}).get("player_shots_on_goal_alternate") or {}).get("status") == "POSTED"}
        shots_eval, ml_eval = _day_games_in_log(prev)
        sched = {g["game_id"] for g in games}
        return {"date_et": prev, "scheduled": len(sched), "shots_priced": len(sched & posted), "shots_legs_evaluated_in_games": len(sched & shots_eval),
                "moneyline_legs_evaluated_in_games": len(sched & ml_eval), "not_priced": sorted(sched - posted),
                "note": ("Each game on that slate had analysis available from the projections; prices were bought for the games the day's odds credits covered (the free allowance is "
                         "about 11 credits a day), so the rest could be analysed but not priced. Games without prices are listed on the board with the reason.")}
    except Exception as exc:  # noqa: BLE001 - informational block: it reports its own failure instead of failing the board
        return {"error": f"{exc.__class__.__name__}: {exc}"}


def refresh(now: dt.datetime, *, product: dict | None = None, tickets: dict | None = None) -> dict:
    from operational import builder_pool, daily_tickets, price_availability as pa, product_data
    product = product or product_data.read_state() or {}
    pool = builder_pool.load()
    tickets = tickets if tickets is not None else daily_tickets.read_state()
    today = product.get("et_today")
    avail = pa.read(today) if today else {}
    saves_errors: dict = {}
    saves = _by_goalie_id(_saves_lines(pool, now, saves_errors), product.get("goalies") or {})
    doc = build_doc(now, product, pool, tickets, avail, saves, _previous_day_check(product, today) if today else None, saves_errors, previous_doc=load())
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str))
    tmp.replace(path)
    return {"status": "OK", "games": len(doc["games"]), "counts": doc["counts"]}
