"""Daily postmortem, 14-day scorecard and end-of-experiment review for the POSTMORTEM_LEARNING_14D paper cohort (operational/learning_cohort.py).

READ-ONLY on the ledger and the official database; writes only its own JSON/markdown documents. Everything is computed from settled ticket results: funding events (the $500 start and any reset to $500) are
never performance and appear only in their own lines. A bet that is open, UNRESOLVED (did not play: the void rule is unverified) or VOID never counts as a win or a loss. Recommendations only: nothing here
changes a model, a threshold or a policy.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict

from operational import eastern_time as et
from operational import eggy_decisions as ed
from operational import intelligence_metrics as im
from operational import learning_cohort as lc
from operational import state_paths

POSTMORTEM_NAME = "learning_experiment_postmortem.json"
REPORT_NAME = "learning_experiment_final_report.json"
REPORT_MD = "learning_experiment_final_report.md"
PROB_BANDS = ((0.0, 0.45, "under 45%"), (0.45, 0.55, "45-55%"), (0.55, 0.65, "55-65%"), (0.65, 0.75, "65-75%"), (0.75, 0.85, "75-85%"), (0.85, 1.01, "85% and over"))
EDGE_BANDS = ((-1.0, 0.0, "negative"), (0.0, 0.03, "0 to 3 pts"), (0.03, 0.06, "3 to 6 pts"), (0.06, 0.10, "6 to 10 pts"), (0.10, 9.0, "10 pts and over"))
RANK_BANDS = ((1, 4, "ranks 1-3"), (4, 7, "ranks 4-6"), (7, 99, "ranks 7-10"))
FRESH_BANDS = ((0, 15, "0-15 min old"), (15, 40, "15-40 min old"), (40, 10 ** 6, "over 40 min old"))
MIN_SAMPLE = 30                    # below this a market's result is a WATCH, never a promotion or a verdict
SIGNIFICANT_Z = 2.0
SETTLED = ("WIN", "LOSS")


def _band(value, bands):
    if value is None:
        return "unknown"
    for lo, hi, label in bands:
        if lo <= value < hi:
            return label
    return "unknown"


def _model_p(r) -> float | None:
    return r["model_probability"] if r.get("model_probability") is not None else r.get("conservative_probability")


def rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM bets ORDER BY et_date, selection_rank").fetchall()]


def metrics(bets: list[dict]) -> dict:
    """The measures of a set of bets. Hit rate, P/L and ROI use settled wins/losses only; stakes of open and void bets are shown separately."""
    settled = [b for b in bets if b["result_status"] in SETTLED]
    wins = sum(1 for b in settled if b["result_status"] == "WIN")
    losses = len(settled) - wins
    voids = sum(1 for b in bets if b["result_status"] == "VOID")
    unresolved = sum(1 for b in bets if b["result_status"] == "UNRESOLVED")
    open_ = sum(1 for b in bets if b["result_status"] == "PENDING")
    staked = sum(b["stake"] for b in settled)
    pl = sum(b["profit_loss"] or 0.0 for b in settled)
    returned = sum(b["return_amount"] or 0.0 for b in settled)
    probs = [(_model_p(b), b) for b in settled if _model_p(b) is not None]
    exp_wins = sum(p for p, _ in probs)
    cons = [b["conservative_probability"] for b in settled if b["conservative_probability"] is not None]
    brier = (sum((p - (1.0 if b["result_status"] == "WIN" else 0.0)) ** 2 for p, b in probs) / len(probs)) if probs else None
    avg = lambda xs: (round(sum(xs) / len(xs), 4) if xs else None)
    dec = [b["decimal_price"] for b in bets]
    var = sum(p * (1 - p) for p, _ in probs)
    return {"placed": len(bets), "settled": len(settled), "wins": wins, "losses": losses, "voids": voids, "unresolved": unresolved, "open": open_,
            "hit_rate": round(wins / len(settled), 4) if settled else None, "staked": round(staked, 2), "returned": round(returned, 2), "profit_loss": round(pl, 2),
            "roi": round(pl / staked, 4) if staked else None, "average_decimal_odds": avg(dec),
            "average_american_odds_of_average_decimal": (round(((sum(dec) / len(dec)) - 1) * 100) if dec and sum(dec) / len(dec) >= 2 else (round(-100 / ((sum(dec) / len(dec)) - 1)) if dec else None)),
            "average_implied_probability": avg([b["implied_probability"] for b in bets]), "average_model_probability": avg([_model_p(b) for b in bets if _model_p(b) is not None]),
            "average_conservative_probability": avg(cons), "average_edge_at_placement": avg([b["edge"] for b in bets]),
            "expected_ev_per_stake": avg([b["conservative_probability"] * b["decimal_price"] - 1.0 for b in settled]),
            "expected_wins": round(exp_wins, 3), "expected_wins_conservative": round(sum(cons), 3), "actual_wins": wins, "calibration_delta": round(wins - exp_wins, 3) if settled else None,
            "calibration_z": round((wins - exp_wins) / math.sqrt(var), 2) if var > 0 else None, "brier": round(brier, 4) if brier is not None else None}


def breakdown(bets: list[dict], key) -> list[dict]:
    groups: dict = defaultdict(list)
    for b in bets:
        groups[key(b)].append(b)
    out = []
    for k, v in groups.items():
        m = metrics(v)
        out.append({"group": k, **{f: m[f] for f in ("placed", "settled", "wins", "losses", "hit_rate", "profit_loss", "roi", "expected_wins", "calibration_delta", "average_edge_at_placement")}})
    return sorted(out, key=lambda r: (-r["placed"], str(r["group"])))


def breakdowns(bets: list[dict]) -> dict:
    return {
        "edge_class": breakdown(bets, lambda b: b.get("edge_class") or "unknown"),
        "price_status": breakdown(bets, lambda b: b.get("price_status") or "unknown"),
        "market_type": breakdown(bets, lambda b: b["market_family"]),
        "price_band": breakdown(bets, lambda b: lc.band_of(b["american_price"])),
        "model_probability_band": breakdown(bets, lambda b: _band(_model_p(b), PROB_BANDS)),
        "edge_band": breakdown(bets, lambda b: _band(b["edge"], EDGE_BANDS)),
        "game": breakdown(bets, lambda b: f"{b['team']} vs {b['opponent']} (game {b['game_id']})"),
        "team": breakdown(bets, lambda b: b["team"] or "unknown"),
        "player": breakdown(bets, lambda b: b["participant_name"] or b["participant_id"]),
        "data_freshness": breakdown(bets, lambda b: _band(b["quote_age_min"], FRESH_BANDS)),
        "selection_rank": breakdown(bets, lambda b: _band(b["selection_rank"], RANK_BANDS)),
    }


def edge_class_table(bets: list[dict]) -> list[dict]:
    """Results by the edge class assigned at recording time (POSITIVE_EDGE / NEUTRAL_UNCERTAIN_EDGE / LEARNING_SAMPLE_NEGATIVE_EDGE), each with the full set of measures. The classes are never merged into one number."""
    out = []
    for cls in lc.EDGE_CLASSES:
        v = [b for b in bets if b.get("edge_class") == cls]
        m = metrics(v)
        out.append({"edge_class": cls, "n": m["placed"], "settled": m["settled"], "wins": m["wins"], "losses": m["losses"], "pushes_voids": m["voids"], "open": m["open"], "unresolved": m["unresolved"],
                    "hit_rate": m["hit_rate"], "average_decimal_odds": m["average_decimal_odds"], "average_american_odds": m["average_american_odds_of_average_decimal"],
                    "average_model_probability": m["average_model_probability"], "average_conservative_probability": m["average_conservative_probability"], "average_implied_probability": m["average_implied_probability"],
                    "average_estimated_edge": m["average_edge_at_placement"], "profit_loss": m["profit_loss"], "roi": m["roi"], "expected_wins": m["expected_wins"], "expected_wins_conservative": m["expected_wins_conservative"],
                    "actual_wins": m["actual_wins"], "calibration_delta": m["calibration_delta"], "calibration_z": m["calibration_z"], "brier": m["brier"]})
    return out


AGE_ORDER = [b[1] for b in lc.PRICE_AGE_BUCKETS] + ["120+ min", "unknown"]


def _metric_row(label_key: str, label: str, v: list[dict]) -> dict:
    m = metrics(v)
    return {label_key: label, "n": m["placed"], "settled": m["settled"], "wins": m["wins"], "losses": m["losses"], "pushes_voids": m["voids"], "hit_rate": m["hit_rate"], "profit_loss": m["profit_loss"],
            "roi": m["roi"], "average_model_probability": m["average_model_probability"], "average_implied_probability": m["average_implied_probability"], "average_estimated_edge": m["average_edge_at_placement"],
            "expected_wins": m["expected_wins"], "actual_wins": m["actual_wins"], "calibration_delta": m["calibration_delta"], "calibration_z": m["calibration_z"],
            "expected_ev_per_stake": m["expected_ev_per_stake"]}


def price_age_table(bets: list[dict], field: str = "price_age_at_recording_min") -> list[dict]:
    """Results by how old the last observed price was when the bet was recorded (0-5, 6-15, 16-30, 31-60, 61-120, 120+ minutes). Two questions are kept apart: SELECTION quality (hit rate against the model's
    probability: did the outcome occur as often as estimated) and PRICE/VALUE quality (actual ROI against the expected value at the last observed price). Nothing is assumed about which way age points."""
    out = []
    stored = field == "price_age_at_recording_min"          # the observation-age bucket was stored at recording time; the provider-age bucket is derived from its stored age
    for name in AGE_ORDER:
        v = [b for b in bets if (b.get("price_age_bucket") or lc.price_age_bucket(b.get(field))) == name] if stored else [b for b in bets if lc.price_age_bucket(b.get(field)) == name]
        if v:
            out.append(_metric_row("bucket", name, v))
    return out


def price_status_table(bets: list[dict]) -> list[dict]:
    return [_metric_row("price_status", st, [b for b in bets if b.get("price_status") == st]) for st in ("CURRENT", "RECENT", "LAST_KNOWN_PREGAME", "VERY_OLD_PREGAME")
            if any(b.get("price_status") == st for b in bets)]


def dependence_report(bets: list[dict]) -> dict:
    """Same-game dependence among the bets, never assumed away. For every game with 2+ bets: bets, expected and actual hits, and for each PAIR of bets in the game the observed share where both hit against the
    share expected if they were independent (the product of their probabilities). Also the shots-threshold ladder. Offered same-game parlay prices are not captured by this system, so none are claimed."""
    from itertools import combinations
    games: dict = defaultdict(list)
    for b in bets:
        if b["result_status"] in SETTLED:
            games[b["game_id"]].append(b)
    rows, pair_obs, pair_exp, pairs_n = [], 0, 0.0, 0
    for gid, v in games.items():
        if len(v) < 2:
            continue
        hit = lambda b: 1 if b["result_status"] == "WIN" else 0
        pe = sum(_model_p(a) * _model_p(b) for a, b in combinations(v, 2) if _model_p(a) is not None and _model_p(b) is not None)
        po = sum(hit(a) * hit(b) for a, b in combinations(v, 2))
        pair_obs, pair_exp, pairs_n = pair_obs + po, pair_exp + pe, pairs_n + len(list(combinations(v, 2)))
        rows.append({"game_id": gid, "bets": len(v), "expected_hits": round(sum(_model_p(b) or 0 for b in v), 2), "actual_hits": sum(hit(b) for b in v), "pairs": len(list(combinations(v, 2))),
                     "pairs_both_hit": po, "pairs_both_hit_if_independent": round(pe, 2), "players": sorted({b["participant_name"] or str(b["participant_id"]) for b in v})})
    shots = [b for b in bets if b["market_family"] in ("PLAYER_SOG", "PLAYER_SOG_ALTERNATE")]
    ladder = [_metric_row("threshold", f"{k}+ shots", [b for b in shots if b["threshold"] == k]) for k in sorted({b["threshold"] for b in shots if b["threshold"] is not None})]
    return {"games_with_multiple_bets": sorted(rows, key=lambda r: -r["bets"]), "pairs": pairs_n, "pairs_both_hit": pair_obs, "pairs_both_hit_if_independent": round(pair_exp, 2),
            "reading": ("not enough settled same-game pairs to say anything about dependence" if pairs_n < 20 else
                        f"{pair_obs} same-game pairs both hit against {pair_exp:.1f} expected if independent: " + ("more joint hits than independence predicts (positive dependence)" if pair_obs > pair_exp * 1.15
                                                                                                                   else "fewer joint hits than independence predicts" if pair_obs < pair_exp * 0.85 else "close to independence")),
            "shots_threshold_ladder": ladder,
            "not_captured": "One-plus shots ladders and offered same-game parlay prices are not captured by this system (1+ shots is not a validated threshold, and no parlay-price source exists); no parlay value is claimed."}


def daily_rows(bets: list[dict], today_day: int) -> list[dict]:
    """Day 1..14: recorded/target, W-L-P, hit rate, P/L, ROI, average odds, expected vs actual wins, calibration delta."""
    out = []
    by_day = defaultdict(list)
    for b in bets:
        by_day[b["experiment_day"]].append(b)
    for d in range(1, 15):
        v = by_day.get(d, [])
        m = metrics(v)
        out.append({"day": d, "date": (dt.date.fromisoformat(lc.START_DATE) + dt.timedelta(days=d - 1)).isoformat(), "recorded": len(v), "target": lc.DAILY_TARGET, "is_future": d > today_day,
                    "wins": m["wins"], "losses": m["losses"], "pushes_voids": m["voids"], "unresolved": m["unresolved"], "open": m["open"], "hit_rate": m["hit_rate"], "profit_loss": m["profit_loss"], "roi": m["roi"],
                    "average_decimal_odds": m["average_decimal_odds"], "expected_wins": m["expected_wins"], "actual_wins": m["actual_wins"], "calibration_delta": m["calibration_delta"]})
    return out


def calibration_table(bets: list[dict]) -> list[dict]:
    """Predicted against actual by probability band: the honest answer to 'was it calibrated here?' with the sample size beside it."""
    out = []
    for r in breakdown([b for b in bets if b["result_status"] in SETTLED], lambda b: _band(_model_p(b), PROB_BANDS)):
        grp = [b for b in bets if b["result_status"] in SETTLED and _band(_model_p(b), PROB_BANDS) == r["group"]]
        m = metrics(grp)
        out.append({"band": r["group"], "n": m["settled"], "predicted": round(m["expected_wins"] / m["settled"], 4) if m["settled"] else None, "actual": m["hit_rate"], "delta_wins": m["calibration_delta"], "z": m["calibration_z"]})
    return sorted(out, key=lambda r: r["band"])


# ----------------------------------------------------------------------------------------------- loss classification
def _synthetic_postmortems(losses: list[dict]) -> list[dict]:
    """A ticket post-mortem (the shape operational/ticket_loss_classifier takes) for each losing one-leg bet, from the frozen leg and the leg results stored at settlement."""
    from operational import outcome_resolver as resolver
    out = []
    for b in losses:
        leg = json.loads(b["leg_json"])
        stored = json.loads(b["settlement_json"]) if b.get("settlement_json") else {}
        results = stored.get("leg_results") or []
        r = results[0] if results else {}
        # The price's age is analysed on its own (price_age_table); it is not an integrity signal for a learning bet, so the classifier is given no freshness verdict to misread as "stale data".
        frozen = dict(leg, quote_age_min_at_entry=None, freshness_status="FRESH", game_id=b["game_id"])
        summary = {"participant_id": b["participant_id"], "market_family": b["market_family"], "participant_name": b["participant_name"], "threshold": b["threshold"], "side": b["side"],
                   "game_id": b["game_id"], "actual_value": r.get("actual_value"), "resolver_status": r.get("status"), "predicted_probability": b["conservative_probability"],
                   "recorded_price": b["american_price"], "model_version": b["model_version"]}
        hit_missed = r.get("status") == resolver.RESOLVED and not r.get("outcome_hit")
        out.append({"ticket_id": b["bet_id"], "paper_bet_id": b["bet_id"], "settled_at_utc": b["settled_at_utc"], "stake": b["stake"], "profit_loss": b["profit_loss"],
                    "conservative_probability": b["conservative_probability"], "recorded_price": b["american_price"], "model_version": b["model_version"],
                    "missed_legs": [summary] if hit_missed else [], "hit_legs": [], "other_legs": [] if hit_missed else [summary], "frozen_legs": [frozen],
                    "why": f"{lc.label_of(b)}: actual={r.get('actual_value')}, predicted {b['conservative_probability']:.0%}"})
    return out


def classify_losses(all_rows: list[dict], nhl_conn, now: dt.datetime) -> dict:
    """Every losing bet classified where the evidence allows (operational/ticket_loss_classifier.py: real signals, UNKNOWN when they are missing, a proxy never beyond WATCH). A loss with no
    classifiable evidence reads NORMAL_VARIANCE only when the classifier's own evidence rules say so. Nothing is changed by this."""
    from operational import ticket_loss_classifier as tlc
    losses = [b for b in all_rows if b["result_status"] == "LOSS"]
    pms = _synthetic_postmortems(losses)
    summary = tlc.classify_all(pms, nhl_conn, now=now, window_days=30)
    per_bet = {}
    for pm in pms:
        c = pm.get("classification") or {}
        per_bet[pm["ticket_id"]] = {"category": c.get("category", "UNKNOWN"), "recommended_action": c.get("recommended_action", "NO_ACTION"), "integrity_defect": c.get("integrity_defect", False),
                                    "detail": [{"category": x.get("category"), "signals": x.get("signals"), "explanation": x.get("explanation")} for x in c.get("legs") or []]}
    return {"per_bet": per_bet, "summary": {k: summary.get(k) for k in ("classified_failures", "by_category", "by_action", "integrity_defects")}, "policy_note": tlc.POLICY_NOTE}


# ----------------------------------------------------------------------------------------------- decision review
REVIEW_DAYS = 3                    # the hosted snapshot carries the full per-bet review of the last three Eastern days' bets; the postmortem document on disk carries all of them


def _card_of(b: dict) -> dict | None:
    try:
        return json.loads(b["intelligence_json"]) if b.get("intelligence_json") else None
    except (TypeError, ValueError):
        return None


def _load_rows() -> tuple[list, list]:
    """The MoneyPuck skater and team game files (cached on disk), for the facts of what happened. Missing files give empty lists: claims then read NOT_TESTABLE rather than guessed."""
    try:
        from research.product_models import history, matchup
        return history.skater_games(), matchup.team_games()
    except Exception:  # noqa: BLE001 - the postmortem must still be written
        return [], []


def build_reviews(all_rows: list[dict], nhl_conn, now: dt.datetime, previous: dict | None, integrity: dict | None = None) -> dict:
    """The review of every bet: the card it was recorded with (or, for a bet recorded before the review existed, the card reconstructed from its own day's data, labelled as such), the facts of what happened
    once its game is final, and the assessment of the DECISION apart from the outcome. A finished review is reused from the previous document; only new or newly settled bets are worked out."""
    prior = (previous or {}).get("bet_reviews") or {}
    out, todo = {}, []
    for b in all_rows:
        old = prior.get(b["bet_id"])
        settled = b["result_status"] in SETTLED
        if old and old.get("result_status") == b["result_status"] and (old.get("facts_complete") or not settled):
            out[b["bet_id"]] = old
        else:
            todo.append(b)
    if not todo:
        return out
    data: dict = {}

    def files() -> tuple:
        """The MoneyPuck files and the lookup of the todo games' rows, loaded once and only when a bet needs them (a bet still open needs neither)."""
        if "rows" not in data:
            skater_rows, team_rows = _load_rows()
            data["rows"] = (skater_rows, team_rows, ed.index_rows(skater_rows, team_rows, {b["game_id"] for b in todo}))
        return data["rows"]
    ctx_cache: dict = {}
    for b in todo:
        old = prior.get(b["bet_id"]) or {}
        card, source = _card_of(b), "RECORDED"
        if card is None:
            card, source = old.get("card"), "RECONSTRUCTED"
            if card is None:
                try:
                    skater_rows, team_rows, _ = files()
                    card = ed.reconstruct_card(nhl_conn, b, ctx_cache, team_rows=team_rows, skater_rows=skater_rows) if nhl_conn is not None else None
                except Exception as exc:  # noqa: BLE001
                    card = {"kind": "ERROR", "reconstruction_error": f"{exc.__class__.__name__}: {exc}"}
        facts = ed.facts_for(b, card, nhl_conn, files()[2]) if (card and b["result_status"] in SETTLED) else None
        out[b["bet_id"]] = {"bet_id": b["bet_id"], "et_date": b["et_date"], "label": lc.label_of(b), "result_status": b["result_status"], "source": source, "card": card, "facts": facts,
                            "facts_complete": ed.facts_are_complete(card, facts, bet_date=b["et_date"], now_date=now.astimezone(et.EASTERN).date().isoformat()),
                            "assessment": ed.assess(b, card, facts, integrity_defect=bool((integrity or {}).get(b["bet_id"])))}
    return out


PARLAY_WORD = {"GOOD": "GOOD PARLAY", "QUESTIONABLE": "QUESTIONABLE PARLAY", "BAD": "BAD PARLAY"}


def parlay_summaries(conn, nhl_conn, now: dt.datetime, previous: dict | None) -> list[dict]:
    """The PARLAY SUMMARY that sits above the leg-level reviews: legs, priced or not, the reference product (never a quote), the estimated combined probability where supportable (else the bounds and why not),
    hit or miss, the weakest leg, the leg(s) that killed it, the construction quality and its verdict (judged from the ticket as it was built, never from the result). Each leg keeps its own review: the card it
    was recorded with and the assessment of that leg's decision, from the same engine as every other bet."""
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM parlays ORDER BY et_date, parlay_rank").fetchall()]
    except sqlite3.OperationalError:
        return []
    prior = {x["parlay_id"]: x for x in (previous or {}).get("parlays") or []}
    out, data = [], {}
    for r in rows:
        old = prior.get(r["parlay_id"])
        if old and old.get("result_status") == r["result_status"] and (old.get("facts_complete") or r["result_status"] not in ("HIT", "MISS")) \
                and all(((x.get("assessment") or {}).get("version") == ed.ASSESS_VERSION) for x in old.get("leg_reviews") or []):
            out.append(old)
            continue
        legs = json.loads(r["legs_json"])
        st = json.loads(r["settlement_json"]) if r.get("settlement_json") else {}
        by_key = {x["key"]: x for x in st.get("leg_results") or []}
        settled = r["result_status"] in ("HIT", "MISS")
        if settled and "files" not in data:
            skater_rows, team_rows = _load_rows()
            data["files"] = (skater_rows, team_rows, ed.index_rows(skater_rows, team_rows, {int(l["leg"]["game_id"]) for q in rows for l in json.loads(q["legs_json"])}))
        reviews, complete = [], True
        for l in legs:
            L, card = l["leg"], l["card"]
            res = by_key.get(l["key"], {})
            bet = {"game_id": L["game_id"], "participant_id": L["participant_id"], "market_family": L["market_family"], "threshold": L.get("threshold"), "team": L.get("team"),
                   "result_status": res.get("outcome") or "PENDING"}
            facts = ed.facts_for(bet, card, nhl_conn, data["files"][2]) if settled and res.get("outcome") in ("WIN", "LOSS") else None
            reviews.append({"key": l["key"], "label": lc.ei_label(L), "player": L.get("participant_name"), "team": L.get("team"), "estimate": l["estimate"], "outcome": bet["result_status"], "facts": facts,
                            "assessment": ed.assess(bet, card, facts, parlay_leg=True)})
            complete = complete and (not settled or ed.facts_are_complete(card, facts, bet_date=r["et_date"], now_date=now.astimezone(et.EASTERN).date().isoformat()))
        pub = lc._parlay_public(r)
        out.append({**pub, "et_date": r["et_date"], "verdict": PARLAY_WORD.get(r["construction_quality"]), "priced": False,
                    "price_text": f"UNPRICED learning parlay (no sportsbook combined price exists here); reference product of the legs' quoted prices {r['reference_decimal']:.2f}x, a reference not a quote: no return or ROI is claimed",
                    "hit_text": {"HIT": "HIT: every leg won", "MISS": f"MISS: killed by {', '.join(st.get('killed_by') or [])}", "PENDING": "open"}.get(r["result_status"], r["result_status"]),
                    "leg_reviews": reviews, "facts_complete": complete})
    return out


LESSON_DAYS = 3                     # a role / ice-time lesson is a caution on that player for this many days (the overlay in operational/eggy_intelligence.py reads it)
ROLE_LESSONS = ("ROLE_CHANGE_MISSED", "TOI_PROJECTION_ERROR")


def _player_of(leg_review: dict) -> str | None:
    return leg_review.get("player") or ((leg_review.get("label") or "").split(" \u2014 ")[0] or None)


def build_lessons(conn, parlays: list[dict], reviews: dict, today: str, metrics: dict | None = None) -> dict:
    """What yesterday (and the two days before) taught, in a form tomorrow's review reads without anyone's memory: per Eastern date the flagship and its result, the legs that lost on several tickets, the
    weakest legs that failed, role / ice-time / power-play misses, calibration and game-environment misses, same-game (dependence) tickets, and good decisions that lost against bad ones that won. `cautions` is the
    list the review turns into a temporary caution on a named player. Facts only: nothing here changes a model parameter or a threshold."""
    floor = (dt.date.fromisoformat(today) - dt.timedelta(days=LESSON_DAYS)).isoformat()
    cautions, by_date = [], {}
    for day in sorted({p["et_date"] for p in parlays if p["et_date"] >= floor}):
        ts = [p for p in parlays if p["et_date"] == day]
        settled = [p for p in ts if p["result_status"] in ("HIT", "MISS")]
        lost_on = collections.defaultdict(list)                                  # leg key -> tickets it lost on
        classes, decisions = collections.Counter(), collections.Counter()
        weakest_failed, stronger_failed, same_game = [], 0, {"n": 0, "hit": 0, "miss": 0}
        for p in ts:
            weakest = (p.get("weakest_leg") or {}).get("label")
            for lr in p.get("leg_reviews") or []:
                a = lr.get("assessment") or {}
                cls = a.get("classification")
                if lr.get("outcome") == "LOSS":
                    lost_on[lr["key"]].append((p["parlay_id"], _player_of(lr)))
                    classes[cls or "UNKNOWN"] += 1
                    if p["result_status"] == "MISS" and weakest and lr.get("label") == weakest:
                        weakest_failed.append(_player_of(lr))
                label, out = (a.get("decision_quality") or {}).get("label"), lr.get("outcome")
                if label == ed.GOOD and out == "LOSS":
                    decisions["good_decision_lost"] += 1
                elif label == ed.BAD and out == "WIN":
                    decisions["bad_decision_won"] += 1
                claims = {c.get("id"): c.get("verdict") for c in a.get("claims") or []}
                kind = cls if cls in ROLE_LESSONS else ("PP_ROLE_MISS" if claims.get("expected_pp") == "FAILED" else None)
                if kind and out in ("WIN", "LOSS") and _player_of(lr):
                    cautions.append({"player": _player_of(lr), "team": lr.get("team"), "classification": kind, "date": day, "source": "PARLAY_LEG"})
            if p["result_status"] in ("HIT", "MISS") and p.get("dependence_level") != "ASSUMED_INDEPENDENT":
                same_game["n"] += 1
                same_game["hit" if p["result_status"] == "HIT" else "miss"] += 1
            if p["result_status"] == "MISS" and not any((p.get("weakest_leg") or {}).get("label") == lr.get("label") and lr.get("outcome") == "LOSS" for lr in p.get("leg_reviews") or []):
                stronger_failed += 1
        flagship = None
        try:
            f = lc._flagship_public(conn, day)                                  # the SAME selection the board uses: no second path
            flagship = {"legs": [x["label"] for x in f["legs"]], "result": f["result_status"]} if f else None
        except Exception:  # noqa: BLE001
            flagship = None
        repeated_keys = {k for k, v in lost_on.items() if len({t for t, _ in v}) >= 2}
        killed_by_repeated = sum(1 for p in ts if p["result_status"] == "MISS" and any(lr.get("outcome") == "LOSS" and lr.get("key") in repeated_keys for lr in p.get("leg_reviews") or []))
        by_date[day] = {"tickets": len(ts), "hit": sum(p["result_status"] == "HIT" for p in ts), "miss": sum(p["result_status"] == "MISS" for p in ts), "settled": len(settled),
                        "misses_killed_by_a_reused_leg": killed_by_repeated,
                        "flagship": flagship,
                        "repeated_killer_legs": sorted({names[0][1] for names in lost_on.values() if len({t for t, _ in names}) >= 2 and names[0][1]}),
                        "weakest_legs_that_failed": sorted({n for n in weakest_failed if n}), "misses_killed_by_a_stronger_leg": stronger_failed,
                        "lost_leg_classes": dict(classes), "decisions": dict(decisions), "same_game_tickets": same_game}
    for b in (reviews or {}).values():                                          # earlier straight-bet reviews keep their cautions for their three days
        a, bet = b.get("assessment") or {}, (b.get("card") or {}).get("bet") or {}
        if a.get("classification") in ROLE_LESSONS and b.get("et_date", "") >= floor and bet.get("player_or_team") and bet.get("team"):
            cautions.append({"player": bet["player_or_team"], "team": bet["team"], "classification": a["classification"], "date": b["et_date"], "source": "STRAIGHT_BET"})
    seen, unique = set(), []
    for c in cautions:
        k = (c["player"].lower(), c["team"], c["classification"], c["date"])
        if k not in seen:
            seen.add(k)
            unique.append(c)
    cal_cautions = list((metrics or {}).get("calibration_cautions") or [])
    from operational import eggy_intelligence as ei_
    from operational import lesson_state as ls
    states = ls.build(metrics, unique, cal_cautions, ei_.signal_evidence(), (metrics or {}).get("moneyline_trust"))
    return {"as_of": today, "window_days": LESSON_DAYS, "cautions": unique, "by_date": by_date, "calibration_cautions": cal_cautions, "lesson_state": states, "lesson_state_summary": ls.summary(states),
            "note": "facts from settled legs, read by tomorrow's review as temporary cautions; no model parameter or threshold is changed by them"}


def parlay_scorecard(conn, parlays: list[dict]) -> list[dict]:
    """The parlay product's own day-by-day record (the straight-bet scorecard is the earlier experiment's): per Eastern date the tickets recorded of the daily maximum, hits, misses, open, GOOD against
    QUESTIONABLE, and the day's flagship with its result. Hit or miss only: no stake, return or ROI exists for an unpriced parlay."""
    out = []
    for day in sorted({p["et_date"] for p in parlays}):
        ts = [p for p in parlays if p["et_date"] == day]
        q = lambda name: {"n": sum(1 for p in ts if p["construction_quality"] == name and p["result_status"] in ("HIT", "MISS")), "hit": sum(1 for p in ts if p["construction_quality"] == name and p["result_status"] == "HIT")}
        try:
            f = lc._flagship_public(conn, day)
            flagship = {"legs": [x["label"] for x in f["legs"]], "result": f["result_status"]} if f else None
        except Exception:  # noqa: BLE001
            flagship = None
        out.append({"date": day, "recorded": len(ts), "target": lc.daily_cap(), "hit": sum(p["result_status"] == "HIT" for p in ts), "miss": sum(p["result_status"] == "MISS" for p in ts),
                    "open": sum(p["result_status"] not in ("HIT", "MISS") for p in ts), "good": q("GOOD"), "questionable": q("QUESTIONABLE"), "flagship": flagship})
    return out


def combined_classification(old: dict | None, a: dict) -> tuple[str | None, str | None, str]:
    """(category, why, action) for a settled bet. The decision review's verdict, held to the older signal classifier's evidence: NORMAL_VARIANCE stands only when the review EARNED it and the classifier found no
    specific cause; a specific cause the signals found is never overruled; a loss with no review and no evidence is UNKNOWN, not variance."""
    old_cat = (old or {}).get("category")
    old_action = (old or {}).get("recommended_action") or "NO_ACTION"
    specific = old_cat not in (None, "UNKNOWN", "NORMAL_VARIANCE")
    cat, why, action = a.get("classification"), a.get("why"), a.get("recommended_action") or "NO_ACTION"
    if cat is None and not a:
        if old_cat == "NORMAL_VARIANCE":
            return "UNKNOWN", "no review of this bet exists, so a variance verdict is not earned", "WATCH"
        return old_cat, None, old_action
    if cat == "NORMAL_VARIANCE" and specific:
        return old_cat, f"the thesis was defensible, but the box score shows {old_cat.replace('_', ' ').lower()}: not earned variance. {why}", old_action
    if cat in ("UNKNOWN", None) and specific and a.get("outcome") == "LOSS":
        return old_cat, "the decision review found no cause; the signal classifier did (" + old_cat.replace("_", " ").lower() + ")", old_action
    return cat, why, action


# ----------------------------------------------------------------------------------------------- documents
def build(conn, nhl_conn, now: dt.datetime, *, for_date: str | None = None) -> dict:
    """The postmortem document: the previous Eastern day's bets (or `for_date`), the cumulative record, the scorecard and the bankroll, with loss classification."""
    ph = lc.phase(now)
    today = ph["today"]
    all_rows = rows(conn)
    target_date = for_date or (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    day_bets = [b for b in all_rows if b["et_date"] == target_date]
    cls = classify_losses(all_rows, nhl_conn, now) if nhl_conn is not None else {"per_bet": {}, "summary": {}, "policy_note": None}
    parlays = parlay_summaries(conn, nhl_conn, now, load()) if nhl_conn is not None else []
    reviews = build_reviews(all_rows, nhl_conn, now, load(), {k: v.get("integrity_defect") for k, v in cls["per_bet"].items()})
    bal = lc.balance(conn)
    cumulative = metrics(all_rows)
    intel = im.compute(conn, {"parlays": parlays}, now, nhl_conn)               # the measured record; its calibration cautions travel with the lessons
    final_class: dict = {}
    for b in all_rows:
        a = (reviews.get(b["bet_id"]) or {}).get("assessment") or {}
        final_class[b["bet_id"]] = combined_classification(cls["per_bet"].get(b["bet_id"]), a) if b["result_status"] in SETTLED else (None, None, "NO_ACTION")
    day_lines = []
    for b in day_bets:
        c = cls["per_bet"].get(b["bet_id"])
        a = (reviews.get(b["bet_id"]) or {}).get("assessment") or {}
        cat, why, action = final_class[b["bet_id"]]
        day_lines.append({"rank": b["selection_rank"], "market": b["market_family"], "bet": lc.label_of(b), "bet_id": b["bet_id"],
                          "price": b["american_price"], "model_probability": _model_p(b), "edge": b["edge"], "result": b["result_status"], "profit_loss": b["profit_loss"],
                          "edge_class": b.get("edge_class"), "price_status": b.get("price_status"), "price_age_min": b.get("price_age_at_recording_min"),
                          "decision": (a.get("decision_quality") or {}).get("label"), "decision_reasons": (a.get("decision_quality") or {}).get("reasons"), "lesson": (a.get("lesson") or {}).get("signal"),
                          "classification": cat or (c or {}).get("category"), "why": why, "action": action if cat else (c or {}).get("recommended_action")})
    assessments = [{**(reviews[b["bet_id"]]["assessment"]), "outcome": b["result_status"]} for b in all_rows if b["bet_id"] in reviews and (reviews[b["bet_id"]].get("assessment") or {}).get("decision_quality")]
    decision_review = {**ed.summarize(assessments), "bets_reviewed": len(assessments),
                       "reconstructed": sum(1 for r in reviews.values() if r.get("source") == "RECONSTRUCTED"),
                       "note": "OUTCOME (win/loss) and DECISION (good/questionable/bad) are separate results. The decision is judged from the pre-bet review alone; a good decision that lost is not a mistake, "
                               "and a bad decision that won is not skill. NORMAL_VARIANCE is used only when earned."}
    return {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cohort": lc.COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "phase": ph["phase"], "day": ph["day"],
            "postmortem_for": target_date, "day_metrics": metrics(day_bets), "day_edge_classes": edge_class_table(day_bets), "cumulative_edge_classes": edge_class_table(all_rows),
            "day_price_age": price_age_table(day_bets), "cumulative_price_age": price_age_table(all_rows), "cumulative_price_age_provider": price_age_table(all_rows, "provider_quote_age_at_recording_min"),
            "cumulative_price_status": price_status_table(all_rows), "dependence": dependence_report(all_rows), "day_bets": day_lines, "bet_reviews": reviews, "decision_review": decision_review, "parlays": parlays, "lessons": build_lessons(conn, parlays, reviews, today, intel), "intelligence_metrics": intel, "parlay_scorecard": parlay_scorecard(conn, parlays), "parlay_day": [p for p in parlays if p["et_date"] == target_date],
            "parlay_totals": {"recorded": len(parlays), "hit": sum(1 for p in parlays if p["result_status"] == "HIT"), "miss": sum(1 for p in parlays if p["result_status"] == "MISS"), "open": sum(1 for p in parlays if p["result_status"] == "PENDING"),
                              "note": "unpriced learning parlays: hit or miss only; no stake, return or ROI"}, "day_breakdowns": breakdowns(day_bets) if day_bets else {},
            "cumulative": cumulative, "cumulative_breakdowns": breakdowns(all_rows) if all_rows else {}, "calibration": calibration_table(all_rows),
            "scorecard": daily_rows(all_rows, ph["day"] if ph["phase"] == "ACTIVE" else (14 if ph["phase"] == "ENDED" else 0)),
            "bankroll": {**bal, "label": "separate experiment bankroll (not Eggy's paper bankroll)", "note": "betting P/L is from settled tickets only; resets and the initial funding are funding events, never profit"},
            "funding_events": [dict(r) for r in conn.execute("SELECT at_et, kind, balance_before, amount, balance_after, experiment_day, reason FROM funding_events ORDER BY id")],
            "loss_classification": cls["summary"], "loss_policy_note": cls["policy_note"],
            "closing_price_movement": "not available: closing prices are not captured for these bets, so no closing-line value is claimed",
            "daily_records": [dict(r) for r in conn.execute("SELECT et_date, experiment_day, target, recorded, slots_open, shortfall_json FROM daily_records ORDER BY et_date")]}


def write(doc: dict) -> None:
    p = state_paths.path(POSTMORTEM_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    tmp.replace(p)


def load() -> dict | None:
    try:
        return json.loads(state_paths.path(POSTMORTEM_NAME).read_text())
    except (OSError, ValueError):
        return None


def refresh(conn, nhl_conn, now: dt.datetime) -> dict:
    """Rebuild the postmortem document (the trader calls this when something settled, and the morning job at 07:30 ET) and, once the experiment is over and every bet has settled, the final report."""
    doc = build(conn, nhl_conn, now)
    write(doc)
    out = {"status": "OK", "postmortem_for": doc["postmortem_for"]}
    if lc.phase(now)["phase"] == "ENDED":
        final = final_report_if_ready(conn, nhl_conn, now, doc)
        out["final_report"] = final.get("status") if final else None
    return out


def load_final() -> dict | None:
    try:
        return json.loads(state_paths.path(REPORT_NAME).read_text())
    except (OSError, ValueError):
        return None


def _slim(card):
    """A card for the published snapshot: without `answers` (the app rebuilds them from the card), so the snapshot carries each review once and small."""
    return {k: v for k, v in card.items() if k != "answers"} if isinstance(card, dict) else card


def snapshot_section() -> dict:
    """What the hosted app shows: the status document plus a trimmed postmortem (scorecard, cumulative record, calibration, the main breakdowns, yesterday's bets) and the final report once it exists.
    Counts and prices of paper bets only; nothing here can place or change anything."""
    status = lc.load_status() or {"cohort": lc.COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "authorised": False, "phase": lc.phase()["phase"], "day": lc.phase()["day"], "days": 14,
                                  "target_per_day": lc.daily_cap(), "stake_usd": lc.STAKE, "odds_requirement": f"{int(lc.ODDS_FLOOR)} or better", "recorded_today": 0, "exposure_today_usd": 0.0,
                                  "start_date": lc.START_DATE, "end_date": lc.END_DATE}
    pm = load() or {}
    cb = pm.get("cumulative_breakdowns") or {}
    keep = {k: pm.get(k) for k in ("generated_at_utc", "postmortem_for", "day_metrics", "day_edge_classes", "cumulative_edge_classes", "day_price_age", "cumulative_price_age", "cumulative_price_age_provider", "cumulative_price_status", "dependence", "day_bets", "cumulative", "calibration", "scorecard", "bankroll", "funding_events", "loss_classification", "closing_price_movement")}
    keep["decision_review"] = pm.get("decision_review")
    since = (dt.date.fromisoformat(lc.phase()["today"]) - dt.timedelta(days=REVIEW_DAYS - 1)).isoformat()          # today and the REVIEW_DAYS - 1 days before it
    keep["bet_reviews"] = {k: {**v, "card": _slim(v.get("card"))} for k, v in (pm.get("bet_reviews") or {}).items() if (v.get("et_date") or "") >= since}
    status = dict(status)
    status["cards"] = {k: _slim(c) for k, c in (status.get("cards") or {}).items() if k not in keep["bet_reviews"]}          # one copy of each card in the published document
    keep["parlays"] = [{**p, "leg_reviews": [{**x, "facts": x.get("facts")} for x in p.get("leg_reviews") or []]} for p in (pm.get("parlays") or []) if (p.get("et_date") or "") >= since]
    keep["parlay_totals"] = pm.get("parlay_totals")
    keep["parlay_scorecard"] = pm.get("parlay_scorecard")
    keep["lessons"] = pm.get("lessons")
    keep["intelligence_metrics"] = pm.get("intelligence_metrics")
    keep["cumulative_breakdowns"] = {k: cb.get(k) for k in ("edge_class", "market_type", "price_band", "model_probability_band", "edge_band", "selection_rank", "data_freshness", "team")}
    keep["day_breakdowns"] = {k: v for k, v in (pm.get("day_breakdowns") or {}).items() if k in ("edge_class", "market_type", "price_band", "model_probability_band", "edge_band", "selection_rank", "data_freshness", "team", "player")}
    return {**status, "postmortem": keep, "final_report": load_final() if lc.phase()["phase"] == "ENDED" else None}          # a final report on disk is shown only once the window has really ended


# ----------------------------------------------------------------------------------------------- end-of-experiment review
def verdict_for_market(m: dict, calib_z: float | None) -> tuple[str, str]:
    """(recommendation, reason) for one market family from its own results only. Recommendations, never actions."""
    n = m["settled"]
    if n < MIN_SAMPLE:
        return "WATCH", f"only {n} settled bets (fewer than {MIN_SAMPLE}): too few to judge, keep collecting"
    if calib_z is not None and calib_z <= -SIGNIFICANT_Z:
        return "CHALLENGER_IDEA", f"won {m['wins']} against {m['expected_wins']:.1f} expected (z {calib_z}): systematically overconfident; a challenger model is worth building"
    if m["roi"] is not None and m["roi"] > 0 and (calib_z is None or abs(calib_z) < SIGNIFICANT_Z):
        return "PROMOTE_CANDIDATE", f"ROI {m['roi']:.1%} on {n} bets and calibrated (z {calib_z}): a candidate to carry into the normal policy, still subject to its own review"
    return "WATCH", f"ROI {m['roi']:.1%} on {n} bets, calibration z {calib_z}: no clear verdict"


def final_report_if_ready(conn, nhl_conn, now: dt.datetime, doc: dict | None = None) -> dict | None:
    """Written once, automatically, when the window has ended, every bet is settled (or the only open ones are UNRESOLVED did-not-plays) and the final postmortem for Nov 2 has been built."""
    if lc.phase(now)["phase"] != "ENDED":
        return None
    open_n = conn.execute("SELECT COUNT(*) FROM bets WHERE result_status = 'PENDING'").fetchone()[0]
    if open_n:
        return {"status": "WAITING", "reason": f"{open_n} bet(s) have not settled yet"}
    doc = doc or build(conn, nhl_conn, now)
    report = final_report(conn, nhl_conn, now, doc)
    p = state_paths.path(REPORT_NAME)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    state_paths.path(REPORT_MD).write_text(final_markdown(report))
    return {"status": "WRITTEN", "path": str(p)}


def final_report(conn, nhl_conn, now: dt.datetime, doc: dict) -> dict:
    all_rows = rows(conn)
    by_market = {r["group"]: r for r in doc["cumulative_breakdowns"].get("market_type", [])}
    cal = {r["band"]: r for r in doc["calibration"]}
    mk_metrics = {fam: metrics([b for b in all_rows if b["market_family"] == fam]) for fam in by_market}
    cls = doc["loss_classification"] or {}
    losses = doc["cumulative"]["losses"]
    cats = {}
    for item in cls.get("classified_failures") or []:
        cats[item["category"]] = item["occurrences"]
    total_class = sum(cats.values()) or 0

    def share(*names):
        return sum(cats.get(n, 0) for n in names)
    best = lambda key: sorted([r for r in doc["cumulative_breakdowns"].get(key, []) if r["settled"] >= 5 and r["roi"] is not None], key=lambda r: -r["roi"])
    verdicts = {fam: verdict_for_market(m, m["calibration_z"]) for fam, m in mk_metrics.items()}
    overconf = [b for b in doc["calibration"] if b["z"] is not None and b["z"] <= -SIGNIFICANT_Z and b["n"] >= 10]
    underconf = [b for b in doc["calibration"] if b["z"] is not None and b["z"] >= SIGNIFICANT_Z and b["n"] >= 10]
    wellcal = [b for b in doc["calibration"] if b["z"] is not None and abs(b["z"]) < SIGNIFICANT_Z and b["n"] >= 10]
    integrity = cls.get("integrity_defects") or []
    edge_classes = edge_class_table(all_rows)
    byc = {r["edge_class"]: r for r in edge_classes}
    p_, n_ = byc[lc.EDGE_POSITIVE], byc[lc.EDGE_NEGATIVE]
    if p_["settled"] < 30 or n_["settled"] < 30:
        edge_reading = (f"not enough settled bets to compare the classes (positive-edge {p_['settled']}, negative-edge {n_['settled']}; at least 30 each are needed, and even then ROI is noisy); "
                        "the classes are reported separately above and no conclusion is drawn")
    else:
        gap = (p_["roi"] or 0) - (n_["roi"] or 0)
        verdict = "positive-edge bets did outperform" if gap > 0 else "positive-edge bets did NOT outperform"
        edge_reading = (f"{verdict}: positive-edge ROI {p_['roi']:.1%} vs negative-edge ROI {n_['roi']:.1%} (gap {gap:+.1%}); positive-edge calibration z {p_['calibration_z']}, negative-edge z {n_['calibration_z']}. "
                        "With 30-ish bets per class a ROI gap of 30 points or more is within noise; same-game bets are correlated and the vig is not removed, so this is suggestive only")
    age_rows = [r for r in (doc.get("cumulative_price_age") or []) if r["settled"] >= 1]
    aged = [b for b in all_rows if b["result_status"] in SETTLED and b.get("price_age_at_recording_min") is not None]
    young = [b for b in aged if b["price_age_at_recording_min"] <= 30]
    old = [b for b in aged if b["price_age_at_recording_min"] > 30]
    my, mo = metrics(young), metrics(old)
    if my["settled"] < 30 or mo["settled"] < 30:
        age_reading = (f"not enough settled bets to compare price ages (observed within 30 min: {my['settled']}; older: {mo['settled']}; at least 30 each are needed). The buckets are reported separately and no conclusion is drawn")
    else:
        age_reading = (f"observed within 30 min: hit rate {my['hit_rate']:.1%} vs {my['expected_wins'] / my['settled']:.1%} expected, ROI {my['roi']:.1%}; older: hit rate {mo['hit_rate']:.1%} vs "
                       f"{mo['expected_wins'] / mo['settled']:.1%} expected, ROI {mo['roi']:.1%}. Selection quality (hit against expectation) and price value (ROI against the edge at the last observed price) are separate "
                       "questions; with this many bets a gap this size can be noise, so read it as suggestive")
    answers = {
        "1_markets_that_made_money": [{"market": f, "n": m["settled"], "profit_loss": m["profit_loss"], "roi": m["roi"]} for f, m in mk_metrics.items() if (m["profit_loss"] or 0) > 0],
        "2_markets_that_lost": [{"market": f, "n": m["settled"], "profit_loss": m["profit_loss"], "roi": m["roi"]} for f, m in mk_metrics.items() if (m["profit_loss"] or 0) <= 0],
        "3_well_calibrated": wellcal,
        "4_overconfident": overconf,
        "5_underconfident": underconf,
        "6_best_price_bands": best("price_band")[:3],
        "7_best_edge_bands": best("edge_band")[:3],
        "8_best_selection_ranks": best("selection_rank")[:3],
        "9_normal_variance": {"losses": losses, "classified": total_class, "normal_variance": cats.get("NORMAL_VARIANCE", 0), "share": round(cats.get("NORMAL_VARIANCE", 0) / total_class, 3) if total_class else None,
                              "earned_by_the_decision_review": (doc.get("decision_review") or {}).get("earned_normal_variance"),
                              "note": "NORMAL_VARIANCE in the decision review is earned (defensible thesis, calibration at least MEDIUM, correct data, no factual claim failed, the loss not rarer than 1 in 10 by Eggy's own estimate); "
                                      "the signal classifier's count above is its own evidence rule and is shown beside it"},
        "decision_quality_vs_outcome": doc.get("decision_review"),
        "10_role_context_data_issues": {"role_toi_team_shot_goalie_starter": share("ROLE_CHANGE_MISSED", "TOI_PROJECTION_ERROR", "TEAM_SHOT_ENVIRONMENT_ERROR", "GOALIE_WORKLOAD_ERROR", "STARTER_ERROR"),
                                        "data_identity_pipeline": share("STALE_DATA", "IDENTITY_LINEUP_ERROR", "DATA_PIPELINE_ERROR"), "unknown": cats.get("UNKNOWN", 0),
                                        "note": "role/ice-time/team-shot categories are proxies against the player's or team's own recent average and are capped at WATCH"},
        "11_old_or_moved_prices": {"market_moved_losses": cats.get("MARKET_MOVED", 0), "data_integrity_losses": cats.get("STALE_DATA", 0) + cats.get("DATA_PIPELINE_ERROR", 0),
                                   "answer": ("Price age is recorded and analysed separately (see does_price_age_matter and price_age_results); an old price is not treated as a data defect. Closing prices were not captured, so market movement "
                                              "against the recorded price cannot be measured directly")},
        "12_promote": [f for f, (v, _) in verdicts.items() if v == "PROMOTE_CANDIDATE"],
        "13_watch": [f for f, (v, _) in verdicts.items() if v == "WATCH"],
        "14_challenger_work": [f for f, (v, _) in verdicts.items() if v == "CHALLENGER_IDEA"],
        "15_halt": ([{"integrity_defect": d} for d in integrity] if integrity else "none: a market is only recommended for halting on a repeated integrity defect, and none was found"),
        "market_verdicts": {f: {"recommendation": v, "reason": r} for f, (v, r) in verdicts.items()},
        "edge_class_results": edge_classes,
        "did_positive_edge_outperform": edge_reading,
        "price_age_results": doc.get("cumulative_price_age"),
        "does_price_age_matter": age_reading,
        "same_game_dependence": doc.get("dependence"),
    }
    pos = [f for f, (v, _) in verdicts.items() if v == "PROMOTE_CANDIDATE"]

    answers["16_normal_paper_book_policy"] = {
        "recommendation": ("Keep the normal paper-book policy as it is unless the evidence below supports a change; this report recommends, it does not apply."
                           + (f" Candidates to consider for a wider normal policy: {', '.join(pos)} (each must still pass its own review)." if pos else " No market met the bar for promotion (at least 30 settled bets, positive ROI and no significant miscalibration).")),
        "evidence": {"cumulative_n": doc["cumulative"]["settled"], "cumulative_roi": doc["cumulative"]["roi"], "cumulative_expected_wins": doc["cumulative"]["expected_wins"], "actual_wins": doc["cumulative"]["wins"]},
        "caveat": "singles in the same game or on the same player are positively dependent, so the effective sample is smaller than the bet count; read the verdicts with that in mind"}
    return {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cohort": lc.COHORT, "window": f"{lc.START_DATE} to {lc.END_DATE}", "totals": doc["cumulative"], "bankroll": doc["bankroll"],
            "shortfall_days": [d for d in doc["scorecard"] if not d["is_future"] and d["recorded"] < d["target"]], "answers": answers, "recommendations_only": True,
            "statement": "Recommendations only. No model, threshold, policy or ticket was changed by this experiment or this report."}


def final_markdown(rep: dict) -> str:
    t, b = rep["totals"], rep["bankroll"]
    lines = [f"# {lc.COHORT}: final experiment report", "", f"Window {rep['window']} (paper only). Recommendations only; nothing was changed automatically.", "",
             f"- Bets placed {t['placed']}, settled {t['settled']}: {t['wins']}W-{t['losses']}L, {t['voids']} void, {t['unresolved']} unresolved",
             f"- Hit rate {t['hit_rate']}, expected wins {t['expected_wins']}, actual {t['actual_wins']}, Brier {t['brier']}",
             f"- Betting P/L ${t['profit_loss']} on ${t['staked']} staked (ROI {t['roi']})",
             f"- Experiment bankroll ${b['cash']} cash, {b['reset_count']} reset(s) adding ${b['reset_funding_added']} (funding, not profit)", ""]
    for k, v in rep["answers"].items():
        lines += [f"## {k}", "```json", json.dumps(v, indent=1, default=str), "```", ""]
    return "\n".join(lines)
