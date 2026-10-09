"""
Market-by-market audit, one row per market, with the six kinds of evidence kept apart (they are not interchangeable):

  data           is the input fresh and does it cover the market?
  contract       is the provider's market/identity contract verified (real payloads parse to the right player/team/line/side)?
  validation     does the model beat simple baselines on seasons it never saw? (predictive, NOT profitability)
  calibration    are its probabilities honest, and what is known to be off?
  betting value  is there evidence the market's prices can be beaten? (needs historical prices; there are none)
  live           what has actually happened on the paper book (legs, wins, losses, versus the model's own expected wins)

Passing tests or parsing a payload is not validation, and a few wins is not an edge; a row says which of the six it has and which it lacks.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

LIVE_FAMILIES = {"SHOTS": "PLAYER_SOG_ALTERNATE", "POINTS": "PLAYER_POINTS", "GOALS": "PLAYER_GOALS", "SAVES": "GOALIE_SAVES", "MONEYLINE": "MONEYLINE", "PUCK_LINE": "PUCK_LINE"}


def live_leg_stats(ledger: sqlite3.Connection | None) -> dict[str, dict]:
    """Resolved automatic-ticket legs by market family: {family: {"legs", "won", "lost", "open", "expected_wins"}}. A ledger that cannot be read gives zeros."""
    out: dict[str, dict] = defaultdict(lambda: {"legs": 0, "won": 0, "lost": 0, "open": 0, "expected_wins": 0.0})
    if ledger is None:
        return out
    try:
        rows = ledger.execute("SELECT legs_json, settlement_json FROM paper_bets WHERE track='REAL_MARKET_PAPER' AND origin='AUTOMATIC' AND legs_json IS NOT NULL").fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        legs = json.loads(r[0] if not isinstance(r, sqlite3.Row) else r["legs_json"] or "[]")
        sj = r[1] if not isinstance(r, sqlite3.Row) else r["settlement_json"]
        results = {}
        for x in (json.loads(sj).get("leg_results", []) if sj else []):
            lg = x.get("leg") or {}
            results[(lg.get("game_id"), lg.get("participant_id"), lg.get("market_family"), lg.get("threshold"))] = x.get("outcome")
        for l in legs:
            s = out[l["market_family"]]
            s["legs"] += 1
            s["expected_wins"] += float(l.get("conservative_probability") or 0.0)
            o = results.get((l.get("game_id"), l.get("participant_id"), l.get("market_family"), l.get("threshold")))
            s["won" if o == "WIN" else "lost" if o == "LOSS" else "open"] += 1
    return out


def _live_text(s: dict) -> str:
    if not s["legs"]:
        return "No ticket has used this market yet."
    return f"{s['legs']} leg(s) on automatic tickets: {s['won']} won, {s['lost']} lost, {s['open']} open; the model's own expected wins for them: {s['expected_wins']:.1f}. Too few to say anything about an edge."


def build(sk_verdicts: dict, saves_verdicts: dict, ledger: sqlite3.Connection | None, *, data_through: dict) -> list[dict]:
    from research.generic_prop_pricing import provider_adapter as pa
    live = live_leg_stats(ledger)

    def contract(family: str) -> str:
        try:
            return "VERIFIED" if pa.is_contract_verified("draftkings", family) else "NOT VERIFIED"
        except Exception:  # noqa: BLE001
            return "UNKNOWN"

    def verdict(vs: dict, keys: list[str]) -> str:
        got = [vs[k]["verdict"] for k in keys if k in vs]
        if not got:
            return "none"
        beat = sum(1 for g in got if g == "BEATS_BASELINES")
        return f"beats baselines on {beat} of {len(got)} thresholds (2025-26, never used to fit)"

    no_prices = "None. There are no historical sportsbook prices to test against, so profitability is not claimed."
    sk_cal = ("Platt-calibrated on 2024-25, tested on 2025-26. Known: over-predicts below 40 prior games (those players are not priced); under-predicted by about 3 points for "
              "veterans in the 50–62% band; individual-player error about 8–10 points (docs/SELECTOR_AUDIT.md).")
    rows = [
        {"market": "Shots on goal (alternate ladder)", "status": "WORKS (priced, limited coverage)", "data": f"MoneyPuck logs through {data_through.get('skaters')}; prices limited by the odds-credit plan",
         "contract": contract("PLAYER_SOG_ALTERNATE"), "validation": verdict(sk_verdicts, [f"shots>={k}" for k in (1, 2, 3, 4, 5)]), "calibration": sk_cal, "betting_value": no_prices,
         "live": _live_text(live["PLAYER_SOG_ALTERNATE"])},
        {"market": "Points (1+, 2+)", "status": "WORKS (priced, limited coverage)", "data": f"MoneyPuck logs through {data_through.get('skaters')}", "contract": contract("PLAYER_POINTS"),
         "validation": verdict(sk_verdicts, ["points>=1", "points>=2"]), "calibration": sk_cal, "betting_value": no_prices, "live": _live_text(live["PLAYER_POINTS"])},
        {"market": "Anytime goal", "status": "PARTIAL (priced only for the games the credit plan covers)", "data": f"MoneyPuck logs through {data_through.get('skaters')}", "contract": contract("PLAYER_GOALS"),
         "validation": verdict(sk_verdicts, ["goals>=1"]), "calibration": sk_cal, "betting_value": no_prices, "live": _live_text(live["PLAYER_GOALS"])},
        {"market": "Hits / blocks", "status": "DISPLAY ONLY", "data": f"MoneyPuck logs through {data_through.get('skaters')}", "contract": contract("PLAYER_HITS") + " (no market is requested)",
         "validation": verdict(sk_verdicts, ["hits>=1", "hits>=2", "hits>=3", "blocks>=1", "blocks>=2"]) + "; hits 1+ does NOT beat the baseline", "calibration": sk_cal,
         "betting_value": "Not applicable: never priced.", "live": "Never used on a ticket."},
        {"market": "Goalie saves", "status": "BLOCKED (needs a confirmed starter; no permitted automatic source)", "data": f"MoneyPuck goalie logs through {data_through.get('goalies')}; start confirmation: none automatic",
         "contract": contract("GOALIE_SAVES"), "validation": verdict(saves_verdicts, [f"saves>={k}" for k in range(20, 36)]),
         "calibration": "Raw probabilities (the fitted calibration did not improve held-out log loss); 80% range covered the result 83% of the time.", "betting_value": no_prices,
         "live": _live_text(live["GOALIE_SAVES"])},
        {"market": "Moneyline", "status": "PARTIAL (priced by the Elo path; strength model shadow-scored, not promoted)", "data": "NHL results to " + str(data_through.get("schedule_results"))[:10] + "; prices refreshed only by the T-35 decision pulls and one display refresh",
         "contract": contract("MONEYLINE"), "validation": "Elo path: heuristic band, not shown to beat a sportsbook price. Strength model: beats the home-rate baseline in testing; the Elo and strength models are indistinguishable on the same games.",
         "calibration": "Elo: not calibrated against prices. Strength model: 8 of 150 finished game-sides scored in the live shadow test (too few).", "betting_value": no_prices,
         "live": _live_text(live["MONEYLINE"])},
        {"market": "Puck line / spread", "status": "BLOCKED (never selected)", "data": "No puck-line prices are requested", "contract": "NOT VERIFIED (one real payload, 1 credit, plus more: see docs/PUCK_LINE_REQUIREMENTS.md)",
         "validation": "The Skellam margin model did NOT beat the base rate; the direct-logistic alternative is frozen and scored only on 2026-27 games (below its 300-game minimum).",
         "calibration": "None.", "betting_value": "None.", "live": "Never used on a ticket."},
    ]
    return rows
