"""
Daily review of the paper account: what was predicted and priced, what happened, what it did to the account, whether anything
was a defect or just variance, and what (if anything) the evidence supports changing.

For every ticket settled on the review date it joins, in one place:
  * the FROZEN entry: legs, prices, the provider's quote time, model probabilities and version, combined price, stake, origin;
  * the RESULT: each leg's outcome and the actual official value, the ticket outcome, profit/loss;
  * the ACCOUNT EFFECT: cash, open stakes and equity after it;
  * the CLOSING price of each leg when an archived capture before the game exists (otherwise the reason it is missing);
  * DEFECT checks that are about the system, not the dice: a leg priced without a quote time, a quote older than its limit at
    entry, a ticket recorded after puck drop, a ticket outcome that disagrees with its leg outcomes, an unresolved leg;
  * a VARIANCE reading: the chance the ticket would lose given its own probability, so a loss at 40% hit chance is not called a flaw.

Calibration is cumulative over every settled leg, split by origin (automatic and manually added tickets are never pooled in the
evaluation of the engine) and by market. A change is PROPOSED only when the evidence clears a stated bar (at least MIN_LEGS settled
legs in the market and a gap of at least Z_BAR standard errors); otherwise the review says why it proposes nothing. Nothing here
retunes a model, a threshold or a policy: proposals are text for a person to act on.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path

from operational import eastern_time as et
from operational import paper_bankroll as pb

MIN_LEGS = 30
Z_BAR = 2.0
ENTRY_QUOTE_LIMIT_MIN = 150.0
MARKET_KEYS = {"PLAYER_SOG_ALTERNATE": "player_shots_on_goal_alternate", "PLAYER_POINTS": "player_points"}
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports" / "daily"


def _parse(s):
    if not s:
        return None
    t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def closing_price(leg: dict) -> dict:
    """The last archived DraftKings capture before puck drop for this leg's line, or the reason there is none."""
    key = MARKET_KEYS.get(leg.get("market_family"))
    if key is None:
        return {"american": None, "reason": f"closing capture not implemented for {leg.get('market_family')}"}
    if not leg.get("event_id"):
        return {"american": None, "reason": "no provider event id was frozen with this leg"}
    try:
        from operational import best_bets
        found = best_bets.latest_capture(leg["event_id"], market=key)
    except Exception as exc:  # noqa: BLE001 - the review must not fail on a missing archive
        return {"american": None, "reason": f"archive unreadable ({type(exc).__name__})"}
    if found is None:
        return {"american": None, "reason": "no archived capture for this event"}
    captured, payload = found
    start = _parse(leg.get("game_start_utc"))
    if start is not None and captured > start:
        return {"american": None, "reason": "the newest archived capture is after puck drop"}
    want = best_bets.norm_name(leg.get("participant_name", ""))
    for bm in payload.get("bookmakers", []):
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets", []):
            if m.get("key") != key:
                continue
            for o in m.get("outcomes", []):
                if o.get("name") == "Over" and o.get("point") is not None and int(o["point"] + 0.5) == int(leg.get("threshold") or -1) \
                        and best_bets.norm_name(o.get("description", "")) == want:
                    return {"american": o.get("price"), "captured_at_utc": captured.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "quote_updated_utc": m.get("last_update") or bm.get("last_update")}
    return {"american": None, "reason": "the line was not in the last capture before puck drop"}


def ticket_review(bet: dict, nhl_conn, account_after: dict | None) -> dict:
    legs = json.loads(bet.get("legs_json") or "[]")
    settlement = json.loads(bet["settlement_json"]) if bet.get("settlement_json") else {}
    by_key = {}
    for r in settlement.get("leg_results", []):
        l = r.get("leg", {})
        by_key[(str(l.get("game_id")), str(l.get("participant_id")), l.get("market_family"), l.get("threshold"))] = r
    created = _parse(bet["created_at_utc"])
    defects, rows = [], []
    for l in legs:
        r = by_key.get((str(l["game_id"]), str(l["participant_id"]), l["market_family"], l.get("threshold")), {})
        hit = r.get("outcome_hit")
        outcome = "WIN" if hit else ("LOSS" if hit is False and r.get("status") == "RESOLVED" else (r.get("outcome") or r.get("status") or "UNRESOLVED"))
        cl = closing_price(l)
        row = {"label": f"{l.get('participant_name')} {l.get('threshold')}+ ({l['market_family']})", "game_id": l["game_id"],
               "recorded_price": l.get("american_price"), "quote_updated_utc": l.get("quote_updated_utc"),
               "quote_age_min_at_entry": l.get("quote_age_min_at_entry"), "model_probability": l.get("conservative_probability"),
               "model_version": l.get("model_version"), "actual_value": r.get("actual_value"), "outcome": outcome,
               "closing": cl, "price_vs_close": (None if cl.get("american") is None or l.get("american_price") is None
                                                  else ("beat the close" if _dec(l["american_price"]) > _dec(cl["american"]) else "worse than the close"
                                                        if _dec(l["american_price"]) < _dec(cl["american"]) else "same as the close"))}
        rows.append(row)
        if not l.get("quote_updated_utc"):
            defects.append({"kind": "NO_QUOTE_TIMESTAMP", "severity": "NOTE", "detail": f"{row['label']}: the provider's quote time was not frozen with this leg (the ticket was recorded before that field existed; the price itself is frozen)."})
        elif l.get("quote_age_min_at_entry") is not None and l["quote_age_min_at_entry"] > ENTRY_QUOTE_LIMIT_MIN:
            defects.append({"kind": "STALE_QUOTE_AT_ENTRY", "detail": f"{row['label']}: quote was {l['quote_age_min_at_entry']:.0f} min old at entry."})
        start = _parse(l.get("game_start_utc"))
        if start is not None and created is not None and created > start:
            defects.append({"kind": "RECORDED_AFTER_START", "detail": f"{row['label']}: ticket recorded after puck drop."})
        if outcome not in ("WIN", "LOSS", "VOID"):
            defects.append({"kind": "UNRESOLVED_LEG", "detail": f"{row['label']}: result {outcome}."})
    status = bet["result_status"]
    outcomes = [r["outcome"] for r in rows]
    if status == "WIN" and any(o == "LOSS" for o in outcomes):
        defects.append({"kind": "SETTLEMENT_MISMATCH", "detail": "ticket marked won but a leg is recorded as lost"})
    if status == "LOSS" and outcomes and all(o in ("WIN", "VOID") for o in outcomes):
        defects.append({"kind": "SETTLEMENT_MISMATCH", "detail": "ticket marked lost but no leg is recorded as lost"})
    p = bet.get("conservative_probability") or bet.get("model_probability")
    for d in defects:
        d.setdefault("severity", "DEFECT")
    reading = "DEFECT" if any(d["severity"] == "DEFECT" for d in defects) else ("VARIANCE" if p is not None else "UNKNOWN")
    variance = None
    if p is not None:
        variance = (f"The ticket's own hit chance was {p:.0%}, so a loss was expected {1 - p:.0%} of the time."
                    if status == "LOSS" else f"The ticket's own hit chance was {p:.0%}.")
    return {"ticket_id": bet["paper_bet_id"], "origin": bet.get("origin") or "AUTOMATIC", "status": status,
            "recorded_at_utc": bet["created_at_utc"], "settled_at_utc": bet.get("settled_at_utc"), "stake": bet["stake"],
            "profit_loss": bet.get("profit_loss"), "combined_american": bet["entry_odds"], "model_hit_probability": p,
            "legs": rows, "defects": defects, "reading": reading, "variance_note": variance,
            "provenance": json.loads(bet["provenance_json"]) if bet.get("provenance_json") else None,
            "account_after": account_after}


def _dec(a: float) -> float:
    return 1 + (a / 100 if a > 0 else 100 / abs(a))


def calibration(bets: list[dict]) -> dict:
    """Cumulative leg-level calibration per origin and market from settled tickets' stored leg results."""
    out: dict = {}
    for b in bets:
        if b["result_status"] not in ("WIN", "LOSS", "VOID"):
            continue
        origin = b.get("origin") or "AUTOMATIC"
        settlement = json.loads(b["settlement_json"]) if b.get("settlement_json") else {}
        frozen = {(str(l["game_id"]), str(l["participant_id"]), l["market_family"], l.get("threshold")): l for l in json.loads(b.get("legs_json") or "[]")}
        for r in settlement.get("leg_results", []):
            leg = r.get("leg", {})
            key = (str(leg.get("game_id")), str(leg.get("participant_id")), leg.get("market_family"), leg.get("threshold"))
            f = frozen.get(key)
            if f is None or r.get("status") != "RESOLVED" or r.get("outcome_hit") is None:
                continue
            bucket = out.setdefault(origin, {}).setdefault(leg.get("market_family"), {"legs": 0, "expected": 0.0, "hits": 0, "brier": 0.0, "var": 0.0})
            p, y = f["conservative_probability"], 1 if r["outcome_hit"] else 0
            bucket["legs"] += 1
            bucket["expected"] += p
            bucket["hits"] += y
            bucket["brier"] += (p - y) ** 2
            bucket["var"] += p * (1 - p)
    for origin in out.values():
        for b in origin.values():
            b["brier"] = b["brier"] / b["legs"] if b["legs"] else None
            se = math.sqrt(b["var"]) if b["var"] > 0 else None
            b["z"] = (b["hits"] - b["expected"]) / se if se else None
            b["expected"] = round(b["expected"], 2)
            del b["var"]
    return out


def proposals(cal: dict) -> list[dict]:
    """Evidence-gated suggestions for a person to consider. Never applied automatically."""
    out = []
    for origin, markets in cal.items():
        for market, b in markets.items():
            if b["legs"] < MIN_LEGS:
                out.append({"origin": origin, "market": market, "action": "NONE", "evidence": f"{b['legs']} settled leg(s) — below the {MIN_LEGS}-leg minimum; no conclusion about calibration is drawn."})
            elif b["z"] is not None and abs(b["z"]) >= Z_BAR:
                direction = "over-predicted" if b["z"] < 0 else "under-predicted"
                out.append({"origin": origin, "market": market, "action": "REVIEW_CALIBRATION", "evidence":
                            f"{b['hits']} hits against {b['expected']} expected over {b['legs']} legs (z = {b['z']:+.1f}); the model {direction} this market. "
                            "A person should re-run the validation on the new data before any change."})
            else:
                out.append({"origin": origin, "market": market, "action": "NONE", "evidence": f"{b['hits']} hits against {b['expected']} expected over {b['legs']} legs (z = {b['z']:+.1f}): within chance."})
    return out


def build_review(bankroll_conn, nhl_conn, now: dt.datetime | None = None, *, review_date: str | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    review_date = review_date or (dt.date.fromisoformat(et.eastern_today(now)) - dt.timedelta(days=1)).isoformat()
    bets = pb.query_paper_bets(bankroll_conn, track="REAL_MARKET_PAPER")
    settled_that_day = [b for b in bets if b.get("settled_at_utc") and et.eastern_date_of(b["settled_at_utc"]) == review_date
                        and b["result_status"] in ("WIN", "LOSS", "VOID")]
    account = pb.account_state(bankroll_conn, "REAL_MARKET_PAPER")
    tickets = [ticket_review(b, nhl_conn, account) for b in settled_that_day]
    cal = calibration(bets)
    by_origin = {o: [t for t in tickets if t["origin"] == o] for o in pb.ORIGINS}
    open_now = [b for b in bets if b["result_status"] in ("PENDING", "UNRESOLVED")]
    return {"review_date_et": review_date, "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "account": account,
            "origins": pb.origin_performance(bankroll_conn, "REAL_MARKET_PAPER"),
            "tickets": tickets, "tickets_by_origin": {o: [t["ticket_id"] for t in ts] for o, ts in by_origin.items()},
            "defects": [dict(d, ticket_id=t["ticket_id"]) for t in tickets for d in t["defects"] if d["severity"] == "DEFECT"],
            "notes": [dict(d, ticket_id=t["ticket_id"]) for t in tickets for d in t["defects"] if d["severity"] != "DEFECT"],
            "variance": [{"ticket_id": t["ticket_id"], "note": t["variance_note"]} for t in tickets if t["reading"] == "VARIANCE" and t["variance_note"]],
            "calibration": cal, "proposals": proposals(cal),
            "open_tickets": [{"ticket_id": b["paper_bet_id"], "origin": b.get("origin") or "AUTOMATIC", "status": b["result_status"]} for b in open_now],
            "policy": {"min_legs_for_a_conclusion": MIN_LEGS, "z_bar": Z_BAR, "auto_retune": False}}


def ticket_postmortem_markdown(t: dict) -> str:
    lines = [f"# Postmortem — ticket {t['ticket_id']} ({t['status'].title()})", "",
             f"- Origin: {t['origin']} · recorded {t['recorded_at_utc']} · settled {t.get('settled_at_utc')}",
             f"- Stake ${t['stake']:.2f}, estimated combined price {t['combined_american']:+.0f}, model hit chance "
             f"{(t['model_hit_probability'] or 0):.0%}, result {t['profit_loss'] if t['profit_loss'] is not None else 'n/a'}",
             f"- Reading: **{t['reading']}** — {t.get('variance_note') or ''}", "", "| Leg | Recorded price | Quote updated | Model chance | Actual | Outcome | Closing |", "|---|---|---|---|---|---|---|"]
    for l in t["legs"]:
        cl = l["closing"]
        lines.append(f"| {l['label']} | {l['recorded_price']:+.0f} | {l['quote_updated_utc'] or 'not frozen'} | "
                     f"{(l['model_probability'] or 0):.0%} | {l['actual_value']} | {l['outcome']} | "
                     f"{cl['american'] if cl.get('american') is not None else 'n/a — ' + cl.get('reason', '')} |")
    lines += ["", "## Defect checks"] + ([f"- {d['severity']} {d['kind']}: {d['detail']}" for d in t["defects"]] or ["- none found"])
    return "\n".join(lines) + "\n"


def write_report(review: dict, out_dir: Path = REPORTS_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in review["tickets"]:
        p = out_dir / f"ticket_postmortem_{t['ticket_id']}.md"
        p.write_text(ticket_postmortem_markdown(t))
        paths.append(p)
    return paths
