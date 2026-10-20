"""Daily postmortem, 14-day scorecard and end-of-experiment review for the POSTMORTEM_LEARNING_14D paper cohort (operational/learning_cohort.py).

READ-ONLY on the ledger and the official database; writes only its own JSON/markdown documents. Everything is computed from settled ticket results: funding events (the $500 start and any reset to $500) are
never performance and appear only in their own lines. A bet that is open, UNRESOLVED (did not play: the void rule is unverified) or VOID never counts as a win or a loss. Recommendations only: nothing here
changes a model, a threshold or a policy.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import defaultdict

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
        frozen = dict(leg, quote_age_min_at_entry=leg.get("quote_age_min"), game_id=b["game_id"])
        summary = {"participant_id": b["participant_id"], "market_family": b["market_family"], "participant_name": b["participant_name"], "threshold": b["threshold"], "side": b["side"],
                   "game_id": b["game_id"], "actual_value": r.get("actual_value"), "resolver_status": r.get("status"), "predicted_probability": b["conservative_probability"],
                   "recorded_price": b["american_price"], "model_version": b["model_version"]}
        hit_missed = r.get("status") == resolver.RESOLVED and not r.get("outcome_hit")
        out.append({"ticket_id": b["bet_id"], "paper_bet_id": b["bet_id"], "settled_at_utc": b["settled_at_utc"], "stake": b["stake"], "profit_loss": b["profit_loss"],
                    "conservative_probability": b["conservative_probability"], "recorded_price": b["american_price"], "model_version": b["model_version"],
                    "missed_legs": [summary] if hit_missed else [], "hit_legs": [], "other_legs": [] if hit_missed else [summary], "frozen_legs": [frozen],
                    "why": f"{b['participant_name']} {b['market_family']} {b['threshold']}+: actual={r.get('actual_value')}, predicted {b['conservative_probability']:.0%}"})
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


# ----------------------------------------------------------------------------------------------- documents
def build(conn, nhl_conn, now: dt.datetime, *, for_date: str | None = None) -> dict:
    """The postmortem document: the previous Eastern day's bets (or `for_date`), the cumulative record, the scorecard and the bankroll, with loss classification."""
    ph = lc.phase(now)
    today = ph["today"]
    all_rows = rows(conn)
    target_date = for_date or (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    day_bets = [b for b in all_rows if b["et_date"] == target_date]
    cls = classify_losses(all_rows, nhl_conn, now) if nhl_conn is not None else {"per_bet": {}, "summary": {}, "policy_note": None}
    bal = lc.balance(conn)
    cumulative = metrics(all_rows)
    day_lines = []
    for b in day_bets:
        c = cls["per_bet"].get(b["bet_id"])
        day_lines.append({"rank": b["selection_rank"], "market": b["market_family"], "bet": f"{b['participant_name']} {'' if b['threshold'] is None else str(b['threshold']) + '+'} ({b['team']} vs {b['opponent']})",
                          "price": b["american_price"], "model_probability": _model_p(b), "edge": b["edge"], "result": b["result_status"], "profit_loss": b["profit_loss"],
                          "edge_class": b.get("edge_class"), "classification": (c or {}).get("category"), "action": (c or {}).get("recommended_action")})
    return {"generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cohort": lc.COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "phase": ph["phase"], "day": ph["day"],
            "postmortem_for": target_date, "day_metrics": metrics(day_bets), "day_edge_classes": edge_class_table(day_bets), "cumulative_edge_classes": edge_class_table(all_rows), "day_bets": day_lines, "day_breakdowns": breakdowns(day_bets) if day_bets else {},
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


def snapshot_section() -> dict:
    """What the hosted app shows: the status document plus a trimmed postmortem (scorecard, cumulative record, calibration, the main breakdowns, yesterday's bets) and the final report once it exists.
    Counts and prices of paper bets only; nothing here can place or change anything."""
    status = lc.load_status() or {"cohort": lc.COHORT, "label": "14-DAY POSTMORTEM LEARNING EXPERIMENT", "authorised": False, "phase": lc.phase()["phase"], "day": lc.phase()["day"], "days": 14,
                                  "target_per_day": lc.DAILY_TARGET, "stake_usd": lc.STAKE, "odds_requirement": f"{int(lc.ODDS_FLOOR)} or better", "recorded_today": 0, "exposure_today_usd": 0.0,
                                  "start_date": lc.START_DATE, "end_date": lc.END_DATE}
    pm = load() or {}
    cb = pm.get("cumulative_breakdowns") or {}
    keep = {k: pm.get(k) for k in ("generated_at_utc", "postmortem_for", "day_metrics", "day_edge_classes", "cumulative_edge_classes", "day_bets", "cumulative", "calibration", "scorecard", "bankroll", "funding_events", "loss_classification", "closing_price_movement")}
    keep["cumulative_breakdowns"] = {k: cb.get(k) for k in ("edge_class", "market_type", "price_band", "model_probability_band", "edge_band", "selection_rank", "data_freshness", "team")}
    keep["day_breakdowns"] = {k: v for k, v in (pm.get("day_breakdowns") or {}).items() if k in ("edge_class", "market_type", "price_band", "model_probability_band", "edge_band", "selection_rank", "data_freshness", "team", "player")}
    return {**status, "postmortem": keep, "final_report": load_final()}


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
    if p_["settled"] < 10 or n_["settled"] < 10:
        edge_reading = (f"not enough settled bets to compare (positive-edge {p_['settled']}, negative-edge {n_['settled']}); the classes are reported separately above and no conclusion is drawn")
    else:
        gap = (p_["roi"] or 0) - (n_["roi"] or 0)
        edge_reading = (f"positive-edge ROI {p_['roi']:.1%} vs negative-edge ROI {n_['roi']:.1%} (gap {gap:+.1%}); positive-edge calibration z {p_['calibration_z']}, negative-edge z {n_['calibration_z']}. "
                        "A gap in the expected direction supports the edge calculation; a small sample, correlated same-game bets and the vig make this suggestive only")
    answers = {
        "1_markets_that_made_money": [{"market": f, "n": m["settled"], "profit_loss": m["profit_loss"], "roi": m["roi"]} for f, m in mk_metrics.items() if (m["profit_loss"] or 0) > 0],
        "2_markets_that_lost": [{"market": f, "n": m["settled"], "profit_loss": m["profit_loss"], "roi": m["roi"]} for f, m in mk_metrics.items() if (m["profit_loss"] or 0) <= 0],
        "3_well_calibrated": wellcal,
        "4_overconfident": overconf,
        "5_underconfident": underconf,
        "6_best_price_bands": best("price_band")[:3],
        "7_best_edge_bands": best("edge_band")[:3],
        "8_best_selection_ranks": best("selection_rank")[:3],
        "9_normal_variance": {"losses": losses, "classified": total_class, "normal_variance": cats.get("NORMAL_VARIANCE", 0), "share": round(cats.get("NORMAL_VARIANCE", 0) / total_class, 3) if total_class else None},
        "10_role_context_data_issues": {"role_toi_team_shot_goalie_starter": share("ROLE_CHANGE_MISSED", "TOI_PROJECTION_ERROR", "TEAM_SHOT_ENVIRONMENT_ERROR", "GOALIE_WORKLOAD_ERROR", "STARTER_ERROR"),
                                        "data_identity_pipeline": share("STALE_DATA", "IDENTITY_LINEUP_ERROR", "DATA_PIPELINE_ERROR"), "unknown": cats.get("UNKNOWN", 0),
                                        "note": "role/ice-time/team-shot categories are proxies against the player's or team's own recent average and are capped at WATCH"},
        "11_stale_or_moved_prices": {"stale_data_losses": cats.get("STALE_DATA", 0), "market_moved_losses": cats.get("MARKET_MOVED", 0),
                                     "answer": ("no loss was classified as stale data; closing prices were not captured, so market movement cannot be measured" if not cats.get("STALE_DATA") else "stale-data losses exist: see the integrity defects")},
        "12_promote": [f for f, (v, _) in verdicts.items() if v == "PROMOTE_CANDIDATE"],
        "13_watch": [f for f, (v, _) in verdicts.items() if v == "WATCH"],
        "14_challenger_work": [f for f, (v, _) in verdicts.items() if v == "CHALLENGER_IDEA"],
        "15_halt": ([{"integrity_defect": d} for d in integrity] if integrity else "none: a market is only recommended for halting on a repeated integrity defect, and none was found"),
        "market_verdicts": {f: {"recommendation": v, "reason": r} for f, (v, r) in verdicts.items()},
        "edge_class_results": edge_classes,
        "did_positive_edge_outperform": edge_reading,
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
