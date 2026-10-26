"""
Decision review: did Eggy decide WELL, separately from whether the bet won.

Every bet has two results:
    OUTCOME   WIN / LOSS / PUSH            what the game did
    DECISION  GOOD / QUESTIONABLE / BAD / INSUFFICIENT_EVIDENCE   whether the choice was intelligent GIVEN WHAT WAS KNOWN BEFORE THE GAME

The decision is judged from the review card (operational/eggy_intelligence.py) alone, before the outcome is looked at. The outcome is then used for three things only: to check the card's factual claims
(was the ice time what we expected, did the power-play role exist, did the team the model favoured control the game?), to ask whether a loss is NORMAL VARIANCE (which has to be earned, below), and to
say what Eggy should and should not learn from it:

    GOOD decision, won        the expected reward of a good process: confidence unchanged
    GOOD decision, lost       NORMAL_VARIANCE only when it is earned; otherwise a named cause
    BAD decision, won         a LUCKY win: Eggy must NOT become more confident in this pattern
    BAD decision, lost        a process failure, not variance
    QUESTIONABLE              no reward and no punishment: a weaker decision either way

A loss is NORMAL_VARIANCE only if ALL hold: the thesis was defensible (a GOOD decision), the calibration was at least MEDIUM, the data was right (no integrity defect), no factual claim in the thesis
failed, and the outcome was not rarer than 1 in 10 by Eggy's own estimate (`SURPRISE_FLOOR`); the explanation then states every one of those. Otherwise the loss is not variance and carries the cause.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

from operational import eggy_intelligence as ei

ASSESS_VERSION = 4              # bump when the assessment logic changes: a stored review of an older version is rebuilt, never reused
SURPRISE_FLOOR = 0.10          # a loss the estimate called less than 10% likely is not explained by ordinary variance
TOI_HELD = 2.0                 # minutes below the expectation that still count as the expected role
TOI_FAILED = 3.0               # minutes below expectation
PP_MATERIAL = 0.75             # expected power-play minutes below this are not a claim worth checking
GOOD, QUESTIONABLE, BAD, INSUFFICIENT = "GOOD_DECISION", "QUESTIONABLE_DECISION", "BAD_DECISION", "INSUFFICIENT_EVIDENCE"
BAD_THESES = ("NONE", "UNDERDOG_AT_FAIR_PRICE", "MODEL_SAYS_OVERPRICED")


# ------------------------------------------------------------------------------------------------ decision quality (before the outcome)
def decision_quality(card: dict) -> dict:
    """GOOD / QUESTIONABLE / BAD / INSUFFICIENT_EVIDENCE from the card only, with the reasons."""
    if not card or card.get("kind") == "ERROR":
        return {"label": INSUFFICIENT, "reasons": ["there is no review of this bet"]}
    cal = (card.get("calibration_confidence") or {}).get("level")
    comp = (card.get("data_completeness") or {}).get("fraction", 0.0)
    contr = (card.get("contradiction") or {}).get("level")
    thesis = (card.get("thesis") or {}).get("type")
    action = (card.get("action") or {}).get("decision")
    if cal == "INSUFFICIENT" or comp < 0.5:
        return {"label": INSUFFICIENT, "reasons": [f"calibration {cal}, data completeness {comp:.0%}: not enough to judge the decision"]}
    if action == "PASS" or contr == "SEVERE" or thesis in BAD_THESES:
        why = []
        if thesis in BAD_THESES:
            why.append({"NONE": "there was no thesis", "UNDERDOG_AT_FAIR_PRICE": "the only argument was the price of a fair-priced underdog",
                        "MODEL_SAYS_OVERPRICED": "Eggy's own model said the side was overpriced"}[thesis])
        if contr == "SEVERE":
            why.append("the contradiction was SEVERE")
        if action == "PASS" and not why:
            why.extend(card["action"].get("reasons") or ["the review would have passed"])
        return {"label": BAD, "reasons": why}
    if action == "RECORD" and contr in ("LOW", "MODERATE") and cal in ("HIGH", "MEDIUM") and thesis == "MODEL_SEES_VALUE":
        return {"label": GOOD, "reasons": [f"a {thesis.lower().replace('_', ' ')} thesis that survived scrutiny (contradiction {contr}, calibration {cal})"]}
    return {"label": QUESTIONABLE, "reasons": [f"{(card.get('action') or {}).get('decision', '?').replace('_', ' ').lower()}: " + "; ".join((card.get("action") or {}).get("reasons") or [f"contradiction {contr}, calibration {cal}"])]}


UNAVAILABLE_ITEMS = ("confirmed lineup", "injury news", "confirmed starting goalies", "injuries and lineup")      # data no approved source supplies: its absence is a limitation of the engine, not a flaw of the leg
MIN_CELL_N = ei.MIN_CELL_N                                                                                          # an analogue cell of fewer cases than this adjusts nothing (the engine's own rule)


def _adjustment_the_evidence_required(card: dict, leg: dict | None, date: str | None) -> float:
    """The downward adjustment the VALIDATED flag evidence (docs/validation/player_signals.json, built from games before `date`) says a counted role / opportunity flag on this card was worth, where the review did
    not already apply it (a card with a `signal_adjustment` did). 0.0 when none applies or the evidence did not yet exist on `date`."""
    if card.get("signal_adjustment") or not leg or card.get("kind") != "PLAYER":
        return 0.0
    from operational import decision_artifacts as da
    through = ((da.manifest().get("artifacts") or {}).get("player_signals") or {}).get("data_through")
    if not through or not date or str(date) <= str(through):
        return 0.0                                                                                                   # the evidence was built from games up to `through`: it cannot judge a decision made before then
    stat = ei._STAT.get(leg.get("market_family"))
    if stat is None:
        return 0.0
    market = f"{stat}>={int(leg.get('threshold') or 1)}"
    ev = ei.signal_evidence()
    deltas = []
    for f in ei.counted_flags((card.get("contradiction") or {}).get("flags") or []):
        e = (ev.get(f["name"]) or {}).get(market)
        if e and f["level"] in ("MODERATE", "HIGH", "SEVERE") and str(e.get("verdict", "")).startswith("BEYOND_MODEL_LOWER") and e.get("live_adjustment") is not None:
            deltas.append(float(e["live_adjustment"]))
    return min(deltas, default=0.0)


def parlay_leg_quality(card: dict, context: dict | None = None) -> dict:
    """The quality of a PARLAY INGREDIENT's decision, reconstructed from what was knowable WHEN IT WAS MADE: the review card as recorded, the leg as recorded (its quote, its market) and the day's reuse. The
    outcome is not an input (there is no parameter for it) and `assess` passes none. This rubric is deliberately independent of the admission gate: it asks whether the admission was DEFENSIBLE, so it can find a
    leg the gate let in that it should not have.

    BAD (the admission was not defensible on pregame information):
      * the gate's own refusals (a counted HIGH contradiction, calibration below MEDIUM, an estimate under the 55% floor, a review PASS that is not merely a price judgement);
      * STALE MARKET ESTIMATE: a moneyline whose estimate IS its quote, from a quote the system itself classes stale;
      * THE ESTIMATE CONTRADICTS ITS OWN CALIBRATION EVIDENCE: the analogue cell (300+ cases) says the model ran high there (z <= -2) and the estimate sits two standard errors above what happened to those players;
      * EVIDENCE NOT APPLIED: validated flag evidence (built before the decision) says a counted role flag on the card cost this much, and subtracting it puts the estimate under the 55% floor;
      * UNSUPPORTED REGIME: fewer prior games for the player than the model's own validation population required (the walk skips players with fewer than 5);
      * TWO OR MORE AVAILABLE DATA ITEMS MISSING from the review (items no source supplies, such as a confirmed lineup, are not held against the leg).
    QUESTIONABLE (admitted, with a doubt the evidence supports): a counted MODERATE contradiction; an estimate between the floor and 62% (not strong enough to be the weakest leg of a GOOD ticket); an estimate of
    75% or more on calibration below HIGH; one available data item missing; a thin record (under 40 games); a leg staked on two tickets that carries role or model uncertainty (the same doubt twice).
    GOOD: none of the above, an estimate of at least 62%, calibration HIGH or MEDIUM.
    Price is reported, not graded: no combined price exists. INSUFFICIENT: the card is missing, a reconstruction, or its calibration / completeness cannot support any judgement."""
    from operational import learning_parlays as lp
    ctx = context or {}
    leg = ctx.get("leg") or {}
    if not card or card.get("kind") == "ERROR" or card.get("reconstructed"):
        return {"label": INSUFFICIENT, "basis": "PARLAY_LEG", "reasons": ["there is no review of this leg made at the time"], "criteria": []}
    cal = (card.get("calibration_confidence") or {}).get("level")
    comp = (card.get("data_completeness") or {}).get("fraction", 0.0)
    est = card.get("estimate_used")
    contr = ei.counted_contradiction(card)
    action = (card.get("action") or {}).get("decision")
    price_only_pass = action == "PASS" and card.get("kind") == "PLAYER" and (card.get("thesis") or {}).get("type") == "PRICE_ABOVE_ESTIMATE"
    if cal == "INSUFFICIENT" or comp < 0.5 or est is None:
        return {"label": INSUFFICIENT, "basis": "PARLAY_LEG", "reasons": [f"calibration {cal}, data completeness {comp:.0%}: not enough to judge the decision"], "criteria": []}
    est = float(est)
    bad, doubt = [], []
    if contr in ("HIGH", "SEVERE"):
        bad.append(("GATE_CONTRADICTION", f"a counted contradiction of {contr}"))
    if cal not in ("HIGH", "MEDIUM"):
        bad.append(("GATE_CALIBRATION", f"calibration {cal}"))
    if est < lp.MIN_LEG_P:
        bad.append(("GATE_ESTIMATE_FLOOR", f"an estimate of {est:.0%}, under the {lp.MIN_LEG_P:.0%} a parlay leg needs"))
    if action == "PASS" and not price_only_pass:
        bad.append(("GATE_REVIEW_PASS", "the review itself passed it"))
    if card.get("kind") == "MONEYLINE" and leg.get("freshness_status") == "STALE_QUOTE":
        bad.append(("STALE_MARKET_ESTIMATE", f"the estimate IS the market quote, and that quote was {leg.get('quote_age_min') or 0:.0f} minutes old (classed stale by the system)"))
    ev = (card.get("calibration_confidence") or {}).get("evidence") or {}
    se = ev.get("se", ev.get("standard_error"))
    if ev.get("z") is not None and se is not None and ev.get("n", 0) >= MIN_CELL_N and ev["z"] <= -ei.Z_SIGNIFICANT and ev.get("obs") is not None and est >= float(ev["obs"]) + ei.Z_SIGNIFICANT * float(se):
        bad.append(("CONTRADICTS_ITS_OWN_CALIBRATION", f"the {ev.get('n')}-case analogue cell says the model ran high there (z {ev['z']}) and observed {float(ev['obs']):.0%}, two standard errors under the {est:.0%} claimed"))
    need = _adjustment_the_evidence_required(card, leg, ctx.get("date"))
    if need < 0 and est + need < lp.MIN_LEG_P:
        bad.append(("EVIDENCE_NOT_APPLIED", f"validated evidence says the role flag on this card costs {abs(need) * 100:.1f} points; applied, the estimate is {est + need:.0%}, under the floor"))
    elif need < 0:
        doubt.append(("EVIDENCE_NOT_APPLIED", f"validated evidence says a role flag on this card costs {abs(need) * 100:.1f} points that the estimate did not take off"))
    games = leg.get("games_total") if leg.get("games_total") is not None else ((card.get("player_view") or {}).get("games_total"))
    if card.get("kind") == "PLAYER" and games is not None:
        from research.product_models.validate import MIN_PRIOR_GAMES
        if games < MIN_PRIOR_GAMES:
            bad.append(("UNSUPPORTED_REGIME", f"{games} prior games: fewer than the {MIN_PRIOR_GAMES} the model's validation required"))
        elif games < ei.THIN_PLAYER_GAMES:
            doubt.append(("THIN_RECORD", f"{games} games of record, under {ei.THIN_PLAYER_GAMES}"))
    missing = [m for m in ((card.get("data_completeness") or {}).get("missing") or []) if m not in UNAVAILABLE_ITEMS]
    if len(missing) >= 2:
        bad.append(("DATA_INCOMPLETE", f"{len(missing)} available data items missing from the review ({', '.join(missing)})"))
    elif missing:
        doubt.append(("DATA_INCOMPLETE", f"an available data item was missing from the review ({missing[0]})"))
    if bad:
        return {"label": BAD, "basis": "PARLAY_LEG", "reasons": [t for _, t in bad], "criteria": [c for c, _ in bad]}
    if contr == "MODERATE":
        doubt.append(("COUNTED_CONTRADICTION", "a counted MODERATE contradiction (" + ", ".join(f["family"] for f in ei.families_of(ei.counted_flags((card.get("contradiction") or {}).get("flags") or [])) if f["level"] != "LOW") + ")"))
    if est < lp.MIN_GOOD_WEAKEST:
        doubt.append(("ESTIMATE_MARGIN", f"an estimate of {est:.0%}, between the {lp.MIN_LEG_P:.0%} floor and {lp.MIN_GOOD_WEAKEST:.0%}"))
    if est >= 0.75 and cal != "HIGH":
        doubt.append(("CLAIM_EXCEEDS_CALIBRATION", f"{est:.0%} asserted on calibration {cal}"))
    if ctx.get("reuse_count", 1) >= 2:
        from operational import learning_parlays as lp_
        r = lp_.leg_risks(card)
        if r["role_risk"] or r["model_risk"]:
            doubt.append(("UNCERTAINTY_STAKED_TWICE", "the same role or model uncertainty sits on " + str(ctx["reuse_count"]) + " tickets"))
    if doubt:
        return {"label": QUESTIONABLE, "basis": "PARLAY_LEG", "reasons": [t for _, t in doubt], "criteria": [c for c, _ in doubt]}
    return {"label": GOOD, "basis": "PARLAY_LEG", "reasons": [f"a {est:.0%} estimate, calibration {cal}, no counted contradiction, no available data missing, a supported regime"], "criteria": []}


# ------------------------------------------------------------------------------------------------ facts about what happened
def index_rows(skater_rows: list | None, team_rows: list | None, game_ids) -> dict:
    """The MoneyPuck rows of just these games, keyed for lookup: {"skaters": {(player_id, game_id): row}, "teams": {game_id: {team: row}}}. One pass over the files, not one per bet."""
    want = {int(g) for g in game_ids}
    skaters = {(r["player_id"], r["game_id"]): r for r in (skater_rows or []) if r["game_id"] in want}
    teams: dict = {}
    for r in team_rows or []:
        try:
            g = int(r["g"])
        except (TypeError, ValueError):
            continue
        if g in want:
            teams.setdefault(g, {})[r["t"]] = r
    return {"skaters": skaters, "teams": teams}


def _skater_facts(bet: dict, nhl_conn, index: dict | None) -> dict | None:
    gid, pid = int(bet["game_id"]), str(bet["participant_id"])
    row = ((index or {}).get("skaters") or {}).get((pid, gid))
    if row:
        return {"source": "MoneyPuck game log", "toi": round(row["toi"], 1), "toi_pp": round(row["toi_pp"], 2), "shots": row["shots"], "goals": row["goals"], "assists": row["assists"], "points": row["points"]}
    if nhl_conn is not None:
        p = nhl_conn.execute("SELECT toi_minutes, goals, assists, shots, played FROM player_game_stats WHERE game_id = ? AND player_id = ? ORDER BY revision_number DESC LIMIT 1", (gid, pid)).fetchone()
        if p:
            return {"source": "NHL box score", "toi": None if p["toi_minutes"] is None else round(p["toi_minutes"], 1), "toi_pp": None, "shots": p["shots"], "goals": p["goals"], "assists": p["assists"],
                    "points": None if p["goals"] is None and p["assists"] is None else (p["goals"] or 0) + (p["assists"] or 0), "played": None if p["played"] is None else bool(p["played"])}
    return None


def _game_facts(bet: dict, nhl_conn, index: dict | None) -> dict | None:
    if nhl_conn is None:
        return None
    gid = int(bet["game_id"])
    g = nhl_conn.execute("SELECT home_team, away_team, home_score, away_score, final_period_type, game_state FROM games WHERE game_id = ?", (gid,)).fetchone()
    if not g or g["game_state"] != "FINAL" or g["home_score"] is None or g["away_score"] is None:
        return None
    out = {"home": g["home_team"], "away": g["away_team"], "home_score": g["home_score"], "away_score": g["away_score"], "period_type": g["final_period_type"], "winner": g["home_team"] if g["home_score"] > g["away_score"] else g["away_team"]}
    by = ((index or {}).get("teams") or {}).get(gid) or {}
    if len(by) == 2:
        out["team_stats"] = {t: {"xg_for": round(r["xgf"], 2), "xg_against": round(r["xga"], 2), "shot_attempts_5v5": [int(r["cf5"]), int(r["ca5"])], "high_danger_xg": [round(r["hdf"], 2), round(r["hda"], 2)]} for t, r in by.items()}
    return out


def facts_for(bet: dict, card: dict, nhl_conn, index: dict | None = None) -> dict | None:
    """What actually happened, as far as the data on disk says (None until the game is final)."""
    game = _game_facts(bet, nhl_conn, index)
    if game is None:
        return None
    out = {"game": game}
    if (card or {}).get("kind") == "PLAYER":
        out["player"] = _skater_facts(bet, nhl_conn, index)
    return out


FACTS_SETTLE_DAYS = 3          # the MoneyPuck game logs (power-play time, expected goals) land within a day or two; after this many days a review is final whatever has landed


def facts_are_complete(card: dict | None, facts: dict | None, *, bet_date: str | None = None, now_date: str | None = None) -> bool:
    """True when every fact the review can ever use has landed (a skater's MoneyPuck line, a game's two team rows), or the bet is old enough that waiting longer would not help. A review built from the
    official box score alone is provisional: the box score has no power-play time and no expected goals."""
    if facts is None:
        return False
    try:
        old_enough = bool(bet_date and now_date and (dt.date.fromisoformat(now_date) - dt.date.fromisoformat(bet_date)).days >= FACTS_SETTLE_DAYS)
    except ValueError:
        old_enough = False
    kind = (card or {}).get("kind")
    if kind == "PLAYER":
        landed = (facts.get("player") or {}).get("source") == "MoneyPuck game log"
    elif kind == "MONEYLINE":
        landed = bool((facts.get("game") or {}).get("team_stats"))
    else:
        landed = True
    return landed or old_enough


# ------------------------------------------------------------------------------------------------ the thesis, checked against what happened
def _claim(cid: str, text: str, stated, actual, verdict: str, note: str = "") -> dict:
    return {"id": cid, "claim": text, "stated": stated, "actual": actual, "verdict": verdict, "note": note}


def review_claims(card: dict, bet: dict, facts: dict | None) -> list[dict]:
    """HELD / PARTLY_HELD / FAILED / NOT_TESTABLE for each factual claim in the pre-bet thesis."""
    out: list[dict] = []
    if not facts:
        return [_claim("data", "what happened", None, None, "NOT_TESTABLE", "the game's data has not landed yet")]
    game = facts["game"]
    team = bet.get("team")
    if card.get("kind") == "MONEYLINE":
        hv = card.get("hockey_view") or {}
        fav = hv.get("favourite")
        ts = game.get("team_stats") or {}
        if fav and ts and fav in ts:
            other = [t for t in ts if t != fav][0]
            edge = ts[fav]["xg_for"] - ts[other]["xg_for"]
            fav_won = game["winner"] == fav
            if fav_won or edge >= 0.3:
                verdict, note = "HELD", (f"{fav} won" if fav_won else f"{fav} lost but out-chanced {other} (xG {ts[fav]['xg_for']:.2f} to {ts[other]['xg_for']:.2f})")
            elif edge <= -0.3:
                verdict, note = "FAILED", f"{other} won and out-chanced {fav} (xG {ts[other]['xg_for']:.2f} to {ts[fav]['xg_for']:.2f})"
            else:
                verdict, note = "PARTLY_HELD", f"{other} won a game the chances called close (xG {ts[fav]['xg_for']:.2f} to {ts[other]['xg_for']:.2f})"
            out.append(_claim("favourite", f"{fav} is the better team (p {hv.get('p_favourite', 0):.0%})", fav, game["winner"], verdict, note))
        elif fav:
            out.append(_claim("favourite", f"{fav} is the better team", fav, game["winner"], "HELD" if game["winner"] == fav else "PARTLY_HELD", "final score only: expected-goal data has not landed"))
        out.append(_claim("disagreement", "the model sees more in this side than the market does", (card.get("probabilities") or {}).get("disagreement"), game["winner"], "NOT_TESTABLE",
                          "one game cannot show whether a model-market gap means anything; the shadow log accumulates the evidence"))
        return out
    pf = facts.get("player")
    if not pf:
        return [_claim("player", "the player's game", None, None, "NOT_TESTABLE", "the player's game data has not landed yet")]
    if pf.get("played") is False or (pf.get("toi") == 0 and pf.get("source") == "NHL box score"):          # an unknown (NULL) value is not a did-not-play
        out.append(_claim("played", "the player plays", True, False, "FAILED", "the player did not play"))
        return out
    exp = {c["id"]: c.get("value") for c in card.get("claims") or []}
    et_, ep, es = exp.get("expected_toi"), exp.get("expected_pp"), exp.get("expected_stat")
    if et_ is not None and pf.get("toi") is not None:
        d = pf["toi"] - et_
        verdict = "HELD" if d >= -TOI_HELD else ("FAILED" if d <= -TOI_FAILED else "PARTLY_HELD")          # the claim is the opportunity he would get: more ice time than expected only supports the bet
        out.append(_claim("expected_toi", f"expected {et_:.1f} minutes on ice", round(et_, 1), pf["toi"], verdict, f"played {pf['toi']:.1f} ({d:+.1f})"))
    if ep is not None and ep >= PP_MATERIAL:
        if pf.get("toi_pp") is None:
            out.append(_claim("expected_pp", f"expected {ep:.2f} power-play minutes", round(ep, 2), None, "NOT_TESTABLE", "the box score carries no power-play time; the MoneyPuck game log lands later and completes this check"))
        else:
            verdict = "HELD" if pf["toi_pp"] >= 0.5 * ep else ("FAILED" if pf["toi_pp"] < 0.25 else "PARTLY_HELD")
            out.append(_claim("expected_pp", f"expected {ep:.2f} power-play minutes", round(ep, 2), pf["toi_pp"], verdict, f"had {pf['toi_pp']:.2f}"))
    stat = {"PLAYER_SOG_ALTERNATE": "shots", "PLAYER_SOG": "shots", "PLAYER_POINTS": "points", "PLAYER_GOALS": "goals"}.get(bet["market_family"])
    if stat and es is not None and pf.get(stat) is not None:
        out.append(_claim("expected_stat", f"expected {stat} {es:.2f}", round(es, 2), pf[stat], "INFORMATION", f"had {pf[stat]:g}"))
    tw = exp.get("team_win_probability")
    if tw is not None and team:
        won = game["winner"] == team
        out.append(_claim("team_result", f"{team} win probability {tw:.0%}", round(tw, 3), "won" if won else "lost", "INFORMATION", f"{game['home']} {game['home_score']}, {game['away']} {game['away_score']}"))
    return out


# ------------------------------------------------------------------------------------------------ what to learn
def learning_signal(quality: str, outcome: str, claims: list[dict], p_outcome: float | None, cal: str | None, integrity_defect: bool = False) -> dict:
    """The lesson: reward good decisions, punish bad ones, never reward luck or punish variance. `outcome` is WIN / LOSS / VOID / UNRESOLVED / PENDING."""
    failed = [c for c in claims if c["verdict"] == "FAILED"]
    if outcome not in ("WIN", "LOSS"):
        return {"signal": "NOT_SETTLED", "text": "no lesson until the bet settles", "confidence": "UNCHANGED", "variance": None}
    if quality == INSUFFICIENT:
        return {"signal": "INSUFFICIENT_EVIDENCE", "text": "the decision could not be judged; the result teaches nothing about it", "confidence": "UNCHANGED", "variance": None}
    if outcome == "WIN":
        if quality == GOOD:
            return {"signal": "GOOD_DECISION_WON", "text": "a good decision that won: the expected reward of a sound process. Confidence in the process stands.", "confidence": "UNCHANGED", "variance": None}
        if quality == BAD:
            return {"signal": "BAD_DECISION_WON_LUCK", "text": "a bad decision that won: this was LUCK. Eggy must not become more confident in this pattern, and the process behind the bet still needs fixing.", "confidence": "NOT_INCREASED", "variance": None}
        return {"signal": "QUESTIONABLE_DECISION_WON", "text": "a questionable decision that won: no reward; the win does not make the reasoning better.", "confidence": "NOT_INCREASED", "variance": None}
    # a loss
    if quality == BAD:
        if p_outcome is None:
            chance = ""
        elif 1.0 - p_outcome <= 0.5:
            chance = (f" The loss itself was the likelier outcome (Eggy gave the bet {1 - p_outcome:.0%} to win), so the loss is not what is being judged: the decision is, and the flaws in it were visible before the game.")
        else:
            chance = (f" Eggy gave the bet {1 - p_outcome:.0%} to win, so the loss was the less likely outcome, but what is being judged is the decision, and the flaws in it were visible before the game.")
        return {"signal": "BAD_DECISION_LOST", "text": "a bad decision that lost: a process failure, not variance." + chance, "confidence": "REDUCED_FOR_THIS_PATTERN", "variance": False}
    if quality == QUESTIONABLE:
        return {"signal": "QUESTIONABLE_DECISION_LOST", "text": "a questionable decision that lost: no punishment for the loss itself, but the doubts in the review were real.", "confidence": "UNCHANGED", "variance": False}
    reasons_against = []
    if cal not in ("HIGH", "MEDIUM"):
        reasons_against.append(f"calibration was {cal}")
    if integrity_defect:
        reasons_against.append("a data integrity defect")
    if failed:
        reasons_against.append("a factual claim failed: " + "; ".join(f"{c['claim']} ({c['note']})" for c in failed))
    if p_outcome is not None and p_outcome < SURPRISE_FLOOR:
        reasons_against.append(f"the loss was a {p_outcome:.0%} event by Eggy's own estimate (under {SURPRISE_FLOOR:.0%})")
    if reasons_against:
        return {"signal": "GOOD_DECISION_LOST_NOT_VARIANCE", "text": "a good decision that lost, but not explained by variance alone: " + "; ".join(reasons_against) + ". Investigate.", "confidence": "UNCHANGED_PENDING_INVESTIGATION", "variance": False}
    held = [c for c in claims if c["verdict"] == "HELD"]
    return {"signal": "GOOD_DECISION_LOST_NORMAL_VARIANCE", "variance": True, "confidence": "UNCHANGED",
            "text": ("NORMAL_VARIANCE, earned: the thesis was defensible, calibration was " + str(cal) + ", the data was right, " + (f"{len(held)} factual claim(s) held and none failed, " if claims else "no factual claim failed, ")
                     + (f"and Eggy itself gave the losing outcome a {p_outcome:.0%} chance" if p_outcome is not None else "and the loss was within what the estimate allowed")
                     + ". A good decision that lost to ordinary variance does not reduce confidence.")}


def process_category(card: dict, claims: list[dict]) -> tuple[str, str]:
    """(taxonomy category, why) for a loss that was NOT earned variance, from the review's own flags and the failed claims."""
    flags = {f["name"] for f in (card.get("contradiction") or {}).get("flags", [])}
    failed = {c["id"] for c in claims if c["verdict"] == "FAILED"}
    if "expected_toi" in failed or "played" in failed:
        return "TOI_PROJECTION_ERROR", "the ice time the thesis assumed did not happen"
    if "expected_pp" in failed:
        return "ROLE_CHANGE_MISSED", "the power-play role the thesis assumed did not exist"
    if card.get("kind") == "MONEYLINE" and (card.get("thesis") or {}).get("type") in ("UNDERDOG_AT_FAIR_PRICE", "MODEL_SAYS_OVERPRICED"):
        elo = (card.get("probabilities") or {}).get("elo_not_used")
        return "MODEL_CALIBRATION", ("the estimate that made the side look attractive was not the best available one" + (f" (Elo said {elo:.0%})" if elo else "") + "; the hockey model and the market did not agree with it, and that was known before the game")
    if flags & {"HISTORY_SAYS_MODEL_RUNS_HIGH", "MODEL_ABOVE_RECORD", "MODEL_FAR_ABOVE_MARKET"}:
        return "MODEL_CALIBRATION", "the pre-bet review flagged the model as running high for this kind of bet (history said so before the game)"
    if "GAME_ENVIRONMENT" in flags:
        return "TEAM_SHOT_ENVIRONMENT_ERROR", "the game environment the review flagged (a clear underdog) was not priced into the bet"
    return "UNKNOWN", "no specific cause could be established from the data"


def outcome_cause(card: dict, claims: list[dict], est) -> tuple[str, str]:
    """The cause of a LOSS, kept apart from the quality of the decision: a QUESTIONABLE or BAD decision can still lose to chance. (taxonomy category, why). A specific cause (role, ice time, power play, calibration,
    environment) is found first, from the review's own flags and the failed claims; only when none applies is the loss judged against what the estimate allowed. UNKNOWN is left for losses that facts and the
    estimate together do NOT explain, and INSUFFICIENT_FACTS for losses whose game facts have not landed: neither is a dumping ground, and neither is rewarded as variance."""
    cat, why = process_category(card, claims)
    if cat != "UNKNOWN":
        return cat, why
    held = [c for c in claims if c["verdict"] == "HELD"]
    partly = [c for c in claims if c["verdict"] == "PARTLY_HELD"]
    testable = [c for c in claims if c["verdict"] in ("HELD", "FAILED", "PARTLY_HELD")]
    if not testable:
        return "INSUFFICIENT_FACTS", "none of the facts the decision rested on can be tested yet (the game data has not landed, or the bet has no testable claim)"
    p_loss = None if est is None else 1.0 - float(est)
    if partly:
        return "UNKNOWN", "a fact the decision rested on held only in part (" + "; ".join(c["claim"] for c in partly[:2]) + "): chance cannot be assumed, investigate"
    if held and p_loss is not None and p_loss >= SURPRISE_FLOOR:
        return "OUTCOME_WITHIN_ESTIMATE", (f"the facts the decision rested on held ({', '.join(c['claim'] for c in held[:2])}) and the loss was a {p_loss:.0%} outcome by the estimate: chance explains the result; "
                                           "the decision's own doubts (its quality label) are judged separately")
    return "UNKNOWN", "the facts held but the loss was rarer than the estimate allowed, so chance alone is not an adequate explanation: investigate"


def assess(bet: dict, card: dict | None, facts: dict | None, integrity_defect: bool = False, parlay_leg: bool = False, context: dict | None = None) -> dict:
    """Everything the postmortem says about one bet's decision: quality, claims checked, the lesson, and the category/action when the loss is not earned variance. `integrity_defect` is the loss classifier's
    verdict that the bet's data was defective (stale data treated as fresh, an identity failure, a pipeline failure): such a loss is never earned variance."""
    q = parlay_leg_quality(card or {}, context) if parlay_leg else decision_quality(card or {})
    claims = review_claims(card or {}, bet, facts) if card else []
    est = (card or {}).get("estimate_used")
    outcome = bet.get("result_status")
    p_outcome = None if est is None or outcome not in ("WIN", "LOSS") else (est if outcome == "WIN" else 1.0 - est)
    cal = ((card or {}).get("calibration_confidence") or {}).get("level")
    lesson = learning_signal(q["label"], outcome, claims, p_outcome, cal, integrity_defect)
    cat, why, action = None, None, "NO_ACTION"
    if outcome == "LOSS":
        if lesson["variance"]:
            cat, why = "NORMAL_VARIANCE", lesson["text"]
        else:
            cat, why = outcome_cause(card or {}, claims, est)
            action = "INVESTIGATE" if q["label"] in (BAD, GOOD) or lesson["signal"] == "GOOD_DECISION_LOST_NOT_VARIANCE" else "WATCH"
            if cat in ("OUTCOME_WITHIN_ESTIMATE", "INSUFFICIENT_FACTS") and lesson["signal"] != "GOOD_DECISION_LOST_NOT_VARIANCE":
                action = "NO_ACTION" if cat == "OUTCOME_WITHIN_ESTIMATE" and q["label"] != BAD else "WATCH"
    elif outcome == "WIN" and q["label"] == BAD:
        cat, why, action = "LUCKY_WIN", lesson["text"], "WATCH"
    return {"version": ASSESS_VERSION, "decision_quality": q, "outcome": outcome, "claims": claims, "lesson": lesson, "classification": cat, "why": why, "recommended_action": action, "p_outcome": None if p_outcome is None else round(p_outcome, 4)}


# ------------------------------------------------------------------------------------------------ legacy bets (recorded before the review existed)
def reconstruct_card(nhl_conn, bet: dict, ctx_cache: dict, *, team_rows=None, skater_rows=None) -> dict | None:
    """The review card AS OF THE BET'S OWN DAY for a bet recorded before the review layer existed, from the leg the ledger froze. Labelled reconstructed: it says what the review would have said, not what
    the engine did say. `ctx_cache` {date: Context} is shared across bets."""
    from research.real_market_parlay.engine import ParlayLeg
    try:
        d = json.loads(bet["leg_json"])
    except (TypeError, ValueError):
        return None
    fields = ParlayLeg.__dataclass_fields__
    leg = ParlayLeg(**{k: v for k, v in d.items() if k in fields})
    date = bet["et_date"]
    if date not in ctx_cache:
        created = dt.datetime.fromisoformat(bet["created_at_utc"].replace("Z", "+00:00"))
        ctx_cache[date] = ei.build_context(nhl_conn, created, as_of=date, team_rows=team_rows, skater_rows=skater_rows)
    ctx = ctx_cache[date]
    card = ei.review_leg(ctx, leg, bet.get("probability_basis") or "SOURCE_ESTIMATE_ONLY", float(bet["conservative_probability"]))          # the estimate the old engine itself used for this bet
    card["reconstructed"] = True
    card["reconstruction_note"] = ("recorded before the intelligence review existed: this is the review as it would have run on that day's data, not the reasoning the engine used. "
                                   "The old engine ranked by a weighted score and used Elo as the moneyline model.")
    return card


def summarize(assessments: list[dict]) -> dict:
    """Decision quality against outcome, and what was learned."""
    grid: Counter = Counter()
    for a in assessments:
        grid[(a["decision_quality"]["label"], a["outcome"])] += 1
    return {"grid": [{"decision": k[0], "outcome": k[1], "n": v} for k, v in sorted(grid.items())],
            "lucky_wins": sum(1 for a in assessments if a["lesson"]["signal"] == "BAD_DECISION_WON_LUCK"),
            "earned_normal_variance": sum(1 for a in assessments if a["lesson"]["signal"] == "GOOD_DECISION_LOST_NORMAL_VARIANCE"),
            "process_failures": sum(1 for a in assessments if a["lesson"]["signal"] == "BAD_DECISION_LOST"),
            "not_variance": sum(1 for a in assessments if a["lesson"]["signal"] == "GOOD_DECISION_LOST_NOT_VARIANCE")}
