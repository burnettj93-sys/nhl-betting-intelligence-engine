"""
Postmortem of the model book's run of consecutive losses (owner request 2026-10-10). Read-only: the ledger is opened read-only, nothing is written to it, no network call is made
(the official box scores it checks against are the frozen extract docs/validation/postmortem_boxscores_2026-10-10.json; `--refresh-boxscores` re-fetches them with curl).

It answers, from the ledger's own frozen entry data and nothing else:
  1. which consecutive tickets lost (tickets vs single bets, model book vs personal, legs vs tickets),
  2. every leg's recorded price/probability/quote time against the official result (an independent settlement check),
  3. who repeats across the tickets (shared exposure),
  4. how likely the streak was GIVEN the recorded probabilities and the shared legs (exact, not a product of ticket probabilities),
  5. whether the results can distinguish a calibrated model from an overstated one (they cannot, and how many legs would),
  6. how the selector's candidates looked at entry (hit chance, margin after the haircut), and what each rule would have chosen from the SAME candidates at entry.

Run: python3 deploy/postmortem_losing_streak.py --out docs/validation/postmortem_2026-10-10.json
"""
from __future__ import annotations

import argparse
import copy
import math
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from operational import postmortem_math as pm  # noqa: E402

BOX = REPO / "docs" / "validation" / "postmortem_boxscores_2026-10-10.json"
SELECTOR_OCT8 = REPO / "docs" / "validation" / "selector_audit_2026-10-08.json"
SELECTION_OCT9 = REPO / "docs" / "validation" / "selection_audit_2026-10-09.jsonl"
VALIDATION = REPO / "docs" / "validation" / "skater_projection_validation.json"
LOW_SAMPLE = REPO / "docs" / "validation" / "low_sample_calibration.json"
LEDGER = REPO / "operational" / "paper_bankroll.db"
PROSPECTIVE = REPO / "operational" / "prospective_observations.db"


# ------------------------------------------------------------------ 1. the ledger ----

LEDGER_EXTRACT = REPO / "docs" / "validation" / "postmortem_ledger_extract_2026-10-10.json"
MONEYLINE_OBS = REPO / "docs" / "validation" / "moneyline_observations_2026-10-10.json"


def load_ledger(path=None, *, use_extract: bool = False) -> list[dict]:
    """The model ledger, opened READ-ONLY; or the frozen extract of it (identical content as of 2026-10-10) when the database is not on this machine or `use_extract` is set."""
    path = path or LEDGER
    if use_extract or not Path(path).exists():
        return json.loads(LEDGER_EXTRACT.read_text())["rows"]
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM paper_bets ORDER BY created_at_utc, paper_bet_id")]
    c.close()
    return rows


def kind_of(row: dict) -> str:
    if row["origin"] != "AUTOMATIC":
        return "PERSONAL (manually added; not in the model book)"
    return "PARLAY_TICKET" if row["is_combo"] else "SINGLE_BET"


def losing_streak(rows: list[dict]) -> dict:
    """The run of consecutive losses ending the model book's settled record (automatic bets only, in the order they were recorded)."""
    model = [r for r in rows if r["origin"] == "AUTOMATIC" and r["result_status"] in ("WIN", "LOSS", "VOID")]
    run = []
    for r in reversed(model):
        if r["result_status"] != "LOSS":
            break
        run.append(r)
    run.reverse()
    return {"model_book_settled": len(model), "wins": sum(r["result_status"] == "WIN" for r in model), "losses": sum(r["result_status"] == "LOSS" for r in model),
            "streak_length": len(run), "streak": [{"id": r["paper_bet_id"], "kind": kind_of(r), "recorded_utc": r["created_at_utc"], "entry_odds": round(r["entry_odds"], 1),
                                                    "model_probability": round(r["model_probability"], 4) if r["model_probability"] is not None else None,
                                                    "ev_at_entry": round(r["ev"], 4) if r["ev"] is not None else None, "stake": r["stake"], "profit_loss": round(r["profit_loss"], 2)} for r in run],
            "streak_parlay_tickets": sum(1 for r in run if r["is_combo"]), "streak_single_bets": sum(1 for r in run if not r["is_combo"])}


def bankroll_impact(rows: list[dict]) -> dict:
    model = [r for r in rows if r["origin"] == "AUTOMATIC"]
    settled = [r for r in model if r["result_status"] in ("WIN", "LOSS", "VOID")]
    pnl = round(sum(r["profit_loss"] for r in settled), 2)
    equity, peak, worst = 500.0, 500.0, 0.0
    curve = []
    for r in settled:                                   # recorded order; same-time tickets settle together, so only the end points are meaningful
        equity += r["profit_loss"]
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
        curve.append(round(equity, 2))
    streak = losing_streak(rows)
    return {"starting_balance": 500.0, "settled_pnl": pnl, "cash_now": round(500.0 + pnl - sum(r["stake"] for r in model if r["result_status"] in ("PENDING", "UNRESOLVED")), 2),
            "open_stakes": round(sum(r["stake"] for r in model if r["result_status"] in ("PENDING", "UNRESOLVED")), 2), "peak_equity": round(peak, 2),
            "largest_drawdown": round(worst, 2), "largest_drawdown_pct_of_peak": round(100 * worst / peak, 1), "lost_in_the_streak": round(-sum(s["profit_loss"] for s in streak["streak"]), 2),
            "personal_bets_excluded": [{"id": r["paper_bet_id"], "profit_loss": r["profit_loss"]} for r in rows if r["origin"] != "AUTOMATIC"]}


# ------------------------------------------------------------------ 2. settlement, checked independently ----

def _box() -> dict:
    return json.loads(BOX.read_text())["games"]


def _value(fam: str, s: dict) -> int:
    return int(s["sog"] if "SOG" in fam else (s["goals"] if "GOALS" in fam else s["points"]))


def leg_result(leg: dict, games: dict) -> dict:
    g = games.get(str(leg["game_id"]))
    s = g["skaters"].get(str(leg["participant_id"])) if g else None
    if not g or not s:
        return {"verified": False, "why": "game or player not in the official box score extract"}
    k = int(leg["threshold"])
    val = _value(leg["market_family"], s)
    return {"verified": True, "official_value": val, "hit": val >= k, "toi": s["toi"], "game_state": g["state"], "score": f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']} ({g['last_period']})"}


def ticket_check(row: dict, games: dict) -> dict:
    legs = json.loads(row["legs_json"] or "[]")
    results = [leg_result(l, games) for l in legs]
    if not all(r["verified"] for r in results):
        return {"id": row["paper_bet_id"], "verified": False}
    won = all(r["hit"] for r in results)
    return {"id": row["paper_bet_id"], "verified": True, "ledger_status": row["result_status"], "official_status": "WIN" if won else "LOSS", "agrees": (row["result_status"] == "WIN") == won,
            "legs": [{"player": l["participant_name"], "line": f"{l['market_family'].replace('PLAYER_', '').replace('_ALTERNATE', '')} {l['threshold']}+", "price": l["american_price"],
                      "model_probability": l.get("conservative_probability"), "quote_updated_utc": l.get("quote_updated_utc"), "retrieved_utc": l.get("retrieved_at_utc") or l.get("captured_at_utc"),
                      "quote_age_min_at_entry": l.get("quote_age_min_at_entry"), **r} for l, r in zip(legs, results)]}


def moneyline_check(row: dict, games: dict) -> dict:
    g = games.get(str(row["event_id"]))
    if not g:
        return {"id": row["paper_bet_id"], "verified": False}
    winner = g["home"] if g["home_score"] > g["away_score"] else g["away"]
    won = winner == row["team"]
    return {"id": row["paper_bet_id"], "verified": True, "ledger_status": row["result_status"], "official_status": "WIN" if won else "LOSS", "agrees": (row["result_status"] == "WIN") == won,
            "score": f"{g['away']} {g['away_score']} @ {g['home']} {g['home_score']} ({g['last_period']})", "bet": f"{row['team']} to win at {row['entry_odds']:+.0f}"}


# ------------------------------------------------------------------ 3. exposure ----

def _leg_ids(rows: list[dict]) -> list[list[dict]]:
    tickets = []
    for r in rows:
        if not r["is_combo"] or r["origin"] != "AUTOMATIC":
            continue
        t = []
        for l in json.loads(r["legs_json"] or "[]"):
            t.append({"id": f"{l['game_id']}:{l['participant_id']}:{l['market_family']}:{l['threshold']}", "game": str(l["game_id"]), "player": str(l["participant_id"]),
                      "market": l["market_family"], "p": float(l["conservative_probability"]), "name": l["participant_name"], "threshold": int(l["threshold"])})
        tickets.append({"ticket_id": r["paper_bet_id"], "created": r["created_at_utc"], "legs": t, "ticket_p": float(r["model_probability"]), "date": r["created_at_utc"][:10]})
    return tickets


def exposure_by_day(tickets: list[dict]) -> list[dict]:
    days: dict = {}
    for t in tickets:
        days.setdefault(t["date"], []).append(t)
    out = []
    for d, ts in sorted(days.items()):
        slots = [l for t in ts for l in t["legs"]]
        uniq = {l["id"] for l in slots}
        players: dict = {}
        for t in ts:
            for pid in {l["player"] for l in t["legs"]}:
                players[pid] = players.get(pid, 0) + 1
        names = {l["player"]: l["name"] for l in slots}
        games = {l["game"] for l in slots}
        out.append({"day_recorded_utc": d, "tickets": len(ts), "leg_slots": len(slots), "distinct_legs": len(uniq), "distinct_players": len(players), "distinct_games": len(games),
                    "players_on_more_than_one_ticket": {names[p]: n for p, n in players.items() if n > 1}, "most_tickets_any_one_player_is_on": max(players.values()) if players else 0})
    return out


# ------------------------------------------------------------------ 4. the streak, correctly ----

def streak_probability(tickets: list[dict], streak_ids: list[str], moneyline_ps: dict) -> dict:
    streak_tickets = [t for t in tickets if t["ticket_id"] in streak_ids]
    probs = [t["ticket_p"] for t in streak_tickets]
    res = {"parlay_tickets_in_the_streak": len(streak_tickets), "expected_wins_among_them": round(pm.expected_wins(probs), 3),
           "product_of_ticket_loss_chances_WRONG_independent": round(pm.naive_product_all_lose(probs), 4)}
    for label, scope in (("legs_shared_players_independent_across_markets", pm.INDEPENDENT_MARKETS), ("legs_shared_same_player_fully_dependent_across_markets", pm.SAME_PLAYER_TOGETHER)):
        res[label] = {"all_tickets_lose": round(pm.prob_all_lose([t["legs"] for t in streak_tickets], scope=scope), 4)}
        for shade in (0.03, 0.05, 0.08):
            res[label][f"if_every_leg_were_{int(shade * 100)}_points_lower"] = round(pm.prob_all_lose([t["legs"] for t in streak_tickets], scope=scope, shade=shade), 4)
    nine = {}
    for name, q in moneyline_ps.items():
        nine[name] = {"moneyline_win_probability": q, "all_streak_bets_lose": round(pm.prob_all_lose([t["legs"] for t in streak_tickets], extra_independent=[q]), 4)}
    res["including_the_moneyline_single"] = nine
    return res


DEPENDENCE_ESTIMATES = REPO / "docs" / "validation" / "dependence_estimates_2026-10-10.json"


def dependence_sensitivity(tickets: list[dict], streak_ids: list[str], n: int = 100_000) -> dict:
    """The streak probability under successively fuller dependence assumptions. EVERY figure is conditional on the recorded probabilities being the true ones (before any shading) and on the
    assumptions in the row; it is not the probability of the streak 'in reality'. Which dependencies each version includes is spelled out so the difference between them is visible."""
    st = [{"day": t["date"], "legs": t["legs"]} for t in tickets if t["ticket_id"] in streak_ids]
    legs = [t["legs"] for t in st]
    est = json.loads(DEPENDENCE_ESTIMATES.read_text()) if DEPENDENCE_ESTIMATES.exists() else {}
    phi = (est.get("same_player_two_markets") or {}).get("shots>=2 with points>=1", {}).get("correlation", 0.15)
    rows = [
        {"assumptions": "Tickets share legs (a leg on two tickets is one event; a player's 2+ and 3+ shots are one nested event). Different players independent. The same player's different markets independent.",
         "includes": ["shared legs", "nested lines"], "p_all_lose": round(pm.prob_all_lose(legs), 4), "method": "exact enumeration"},
        {"assumptions": f"+ the same player's different markets move together at the historical binary correlation ({phi:+.2f} for his shots 2+ and points 1+).",
         "includes": ["shared legs", "nested lines", "same player across markets"], "p_all_lose": round(pm.prob_all_lose_dependent(st, n=n, same_player_phi=phi), 4), "method": f"Monte Carlo, {n:,} draws"},
        {"assumptions": "+ the same player's different markets fully dependent (the upper bound for that kind of dependence).",
         "includes": ["shared legs", "nested lines", "same player across markets (bound)"], "p_all_lose": round(pm.prob_all_lose(legs, scope=pm.SAME_PLAYER_TOGETHER), 4), "method": "exact enumeration"},
    ]
    for sd in (0.02, 0.04, 0.06):
        rows.append({"assumptions": f"+ a shared daily shift in every leg's TRUE probability, Normal(0, {sd * 100:.0f} points): uncertainty about the model, common to all legs that day (not estimated; shown as a range).",
                     "includes": ["shared legs", "nested lines", "same player across markets", "day-level model uncertainty"],
                     "p_all_lose": round(pm.prob_all_lose_dependent(st, n=n, same_player_phi=phi, day_shift_sd=sd), 4), "method": f"Monte Carlo, {n:,} draws"})
    return {"label": "CONDITIONAL on the model's recorded probabilities being the true ones, and on the stated dependence structure. Not the probability of the streak 'in reality'.",
            "rows": rows,
            "historical_estimates": {"source": "deploy/estimate_dependence.py on 89,470 skater games since 2024-10-01 (docs/validation/dependence_estimates_2026-10-10.json)",
                                     "opposite_teams_same_game_points": (est.get("markets") or {}).get("points>=1", {}).get("opposite_teams"),
                                     "same_team_same_game_points": (est.get("markets") or {}).get("points>=1", {}).get("same_team"),
                                     "shots_2plus_any_pair_in_game_or_day": {k: (est.get("markets") or {}).get("shots>=2", {}).get(k) for k in ("opposite_teams", "same_team", "different_games_same_date")},
                                     "same_player_two_markets": est.get("same_player_two_markets")},
            "what_applies_to_these_tickets": ("Oct 8: the five tickets use five players in five different games, so same-game dependence between different players cannot arise; the only extra dependence is Bourque's "
                                              "shots and points. Oct 9: Protas (WSH) and Laba (NYR) share a game (opposite teams, correlation about -0.01); Rust and Kakko are in other games. Teammates on one ticket, "
                                              "where the history shows real dependence for points (+0.10), do not occur in any of the eight tickets."),
            "not_modelled": ["players in one game beyond the sign and size estimated above (about -0.01 between opponents, 0 across games on a date)",
                             "any common error in the model itself that does not shift every leg equally (shown only as the daily-shift range)",
                             "dependence between days (a model that is wrong in a persistent way)"]}


def sequence_probability(rows: list[dict], tickets: list[dict], ml_p: float, tail: int, wins_seen: int, n: int = 100_000) -> dict:
    bets = []
    by_id = {t["ticket_id"]: t for t in tickets}
    for r in rows:
        if r["origin"] != "AUTOMATIC" or r["result_status"] not in ("WIN", "LOSS"):
            continue
        bets.append({"legs": by_id[r["paper_bet_id"]]["legs"]} if r["paper_bet_id"] in by_id else {"p": ml_p})
    out = {"bets_in_the_record": len(bets)}
    for label, scope in (("players_independent_across_markets", pm.INDEPENDENT_MARKETS), ("same_player_fully_dependent", pm.SAME_PLAYER_TOGETHER)):
        out[label] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in pm.simulate(bets, tail=tail, wins_seen=wins_seen, n=n, scope=scope).items()}
    return out


# ------------------------------------------------------------------ 5. what the results can and cannot tell us ----

def unique_leg_outcomes(tickets: list[dict], games: dict) -> list[dict]:
    seen: dict = {}
    for t in tickets:
        for l in t["legs"]:
            if l["id"] in seen:
                continue
            gid, pid, fam, k = l["id"].split(":")
            r = leg_result({"game_id": gid, "participant_id": pid, "market_family": fam, "threshold": k}, games)
            seen[l["id"]] = {**l, "hit": bool(r.get("hit")), "official_value": r.get("official_value"), "toi": r.get("toi")}
    return list(seen.values())


def calibration_of_recorded_legs(legs: list[dict]) -> dict:
    def band(l):
        return "0.10-0.30" if l["p"] < 0.30 else ("0.30-0.50" if l["p"] < 0.50 else ("0.50-0.70" if l["p"] < 0.70 else "0.70+"))
    def fam(l):
        return "shots on goal" if "SOG" in l["market"] else ("points" if "POINTS" in l["market"] else "goals")
    out = {"all": _cal(legs), "by_market": {}, "by_probability_band": {}}
    for key, f, store in (("market", fam, "by_market"), ("band", band, "by_probability_band")):
        groups: dict = {}
        for l in legs:
            groups.setdefault(f(l), []).append(l)
        out[store] = {k: _cal(v) for k, v in sorted(groups.items())}
    pb = pm.poisson_binomial([l["p"] for l in legs])
    out["chance_of_this_few_hits_or_fewer_if_the_probabilities_are_right"] = round(pb["cdf"](sum(l["hit"] for l in legs)), 3)
    return out


def _cal(legs: list[dict]) -> dict:
    n = len(legs)
    return {"legs": n, "mean_predicted": round(sum(l["p"] for l in legs) / n, 3), "observed_hit_rate": round(sum(l["hit"] for l in legs) / n, 3), "expected_hits": round(sum(l["p"] for l in legs), 2),
            "observed_hits": sum(l["hit"] for l in legs)}


def evidence_strength(legs: list[dict]) -> dict:
    return {"likelihood_ratio_if_probabilities_were_too_high_by": {f"{int(d * 100)}_points": round(pm.likelihood_ratio(legs, d), 2) for d in (0.03, 0.05, 0.08)},
            "reading": "1.0 means the results cannot tell a calibrated model from an overstated one; a ratio between 1/3 and 3 is 'barely anything'.",
            "resolved_legs_needed_to_detect_a_5_point_overstatement_with_80_percent_power": pm.legs_needed_to_detect(0.05),
            "resolved_legs_needed_for_3_points": pm.legs_needed_to_detect(0.03), "unique_legs_so_far": len(legs)}


# ------------------------------------------------------------------ 6. the selector ----

def ticket_acceptance(tickets: list[dict], rows: list[dict]) -> list[dict]:
    by = {r["paper_bet_id"]: r for r in rows}
    out = []
    for t in tickets:
        r = by[t["ticket_id"]]
        p, d = t["ticket_p"], 1 + r["entry_odds"] / 100.0
        shaded = 1.0
        for l in t["legs"]:
            shaded *= max(l["p"] - 0.03, 0.0)
        out.append({"ticket": t["ticket_id"], "recorded_utc": t["created"][:16], "est_hit_chance": round(p, 3), "price": round(r["entry_odds"]), "ev_at_recorded_probabilities": round(p * d - 1, 3),
                    "ev_after_the_3_point_haircut": round(shaded * d - 1, 4), "result": r["result_status"]})
    return out


def oct9_candidates() -> dict:
    rec = [json.loads(l) for l in SELECTION_OCT9.read_text().splitlines() if l.strip()][0]
    return rec


def hit_floor_table(tickets: list[dict]) -> dict:
    out = {}
    for floor in (0.20, 0.25, 0.30, 0.35, 0.40):
        keep = [t for t in tickets if t["ticket_p"] >= floor]
        out[f">={int(floor * 100)}%"] = {"of_recorded_tickets_it_would_have_kept": f"{len(keep)} of {len(tickets)}", "kept_by_day": {d: sum(1 for t in keep if t["date"] == d) for d in sorted({t['date'] for t in tickets})}}
    return out


# ------------------------------------------------------------------ 7. replay of the original decisions ----
# The policy variants (operational/selection_variants.py) were written down BEFORE any replacement ticket's result was looked at: round numbers tied to the stated objective, to the measured
# model error and to exposure. They are not fitted to the nine losses.
from operational.selection_variants import VARIANTS, candidates_from_legs as _candidates, dec as _dec, select, shaded_probability as _shaded_p  # noqa: E402,F401
NAME_RE = [(re.compile(r"^(.+?) (\d)\+ shots on goal$"), "PLAYER_SOG_ALTERNATE"), (re.compile(r"^(.+?) (\d)\+ points?$"), "PLAYER_POINTS"), (re.compile(r"^(.+?) to score a goal \(anytime\)$"), "PLAYER_GOALS")]


def parse_label(label: str):
    for rx, fam in NAME_RE:
        m = rx.match(label)
        if m:
            return m.group(1), fam, (int(m.group(2)) if fam != "PLAYER_GOALS" else 1)
    return None


def _player_index(games: dict, date: str) -> dict:
    """(first initial, last name) -> [(player id, game id, team, opponent)] for the games of one date, from the official box scores."""
    idx: dict = {}
    for gid, g in games.items():
        if g["date"] != date:
            continue
        for pid, s in g["skaters"].items():
            opp = g["home"] if s["team"] == g["away"] else g["away"]
            ini, _, last = s["name"].partition(". ")
            idx.setdefault((ini, last), []).append((pid, gid, s["team"], opp, g["start_utc"]))
    return idx


def _resolve(name: str, idx: dict):
    first, _, last = name.partition(" ")
    hits = idx.get((first[:1], last)) or []
    return hits[0] if len(hits) == 1 else None


def oct8_pool(games: dict) -> list:
    from research.real_market_parlay import engine as rmp
    idx = _player_index(games, "2026-10-08")
    legs, missing = [], []
    for l in json.loads(SELECTOR_OCT8.read_text())["pool_legs"]:
        parsed = parse_label(l["leg"])
        hit = _resolve(parsed[0], idx) if parsed else None
        if not parsed or not hit:
            missing.append(l["leg"])
            continue
        pid, gid, team, opp, start = hit
        legs.append(rmp.ParlayLeg(game_id=gid, event_id=None, market_family=parsed[1], participant_id=pid, participant_name=parsed[0], side="OVER", threshold=parsed[2], american_price=l["price"],
                                  conservative_probability=l["model_probability"], sportsbook="draftkings", captured_at_utc=None, provider_contract_verified=True, model_threshold_eligible=True,
                                  identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent=opp, game_start_utc=start))
    return legs, missing


def oct9_pool(games: dict, recorded_leg_probs: dict) -> list:
    """The day's candidate legs, recovered from the 28 qualifying two-leg tickets the selection report stored: a leg's probability is (ticket hit chance / partner's probability),
    starting from the four legs whose probabilities are frozen in the ledger. Cross-checked below against the stored tickets."""
    from research.real_market_parlay import engine as rmp
    rec = oct9_candidates()
    known = dict(recorded_leg_probs)            # label -> p
    tickets = rec["considered"]
    for _ in range(6):
        for t in tickets:
            a, b = t["legs"]
            if a in known and b not in known:
                known[b] = t["hit_probability"] / known[a]
            elif b in known and a not in known:
                known[a] = t["hit_probability"] / known[b]
    prices = {}
    for t in tickets:
        for lab, pr in zip(t["legs"], t["leg_prices"]):
            prices[lab] = pr
    idx = _player_index(games, "2026-10-09")
    legs, missing = [], []
    for lab, pr in prices.items():
        parsed = parse_label(lab)
        hit = _resolve(parsed[0], idx) if parsed else None
        if not parsed or not hit or lab not in known:
            missing.append(lab)
            continue
        pid, gid, team, opp, start = hit
        legs.append(rmp.ParlayLeg(game_id=gid, event_id=None, market_family=parsed[1], participant_id=pid, participant_name=parsed[0], side="OVER", threshold=parsed[2], american_price=pr,
                                  conservative_probability=known[lab], sportsbook="draftkings", captured_at_utc=None, provider_contract_verified=True, model_threshold_eligible=True,
                                  identity_resolved=True, price_fresh=True, event_not_started=True, team=team, opponent=opp, game_start_utc=start))
    return legs, missing


def _portfolio(picked: list[dict], games: dict) -> dict:
    if not picked:
        return {"tickets": 0}
    tick_legs = [c["legs"] for c in picked]
    exp = sum(c["p"] for c in picked)
    d = [_dec(c["price"]) for c in picked]
    out = {"tickets": len(picked), "distinct_players": len({(l["game"], l["player"]) for t in tick_legs for l in t}), "distinct_legs": len({l["id"] for t in tick_legs for l in t}),
           "expected_winning_tickets": round(exp, 2), "chance_no_ticket_wins_exact_shared_legs": round(pm.prob_all_lose(tick_legs), 3),
           "chance_no_ticket_wins_if_independent_WRONG": round(pm.naive_product_all_lose([c["p"] for c in picked]), 3),
           "model_expected_profit_per_10_dollars_staked": round(sum(10 * (c["p"] * dd - 1) for c, dd in zip(picked, d)), 2),
           "expected_profit_if_every_leg_were_5_points_lower": round(sum(10 * (_shaded_p(c, 0.05) * dd - 1) for c, dd in zip(picked, d)), 2),
           "best_estimated_hit_chance": round(max(c["p"] for c in picked), 3), "worst_estimated_hit_chance": round(min(c["p"] for c in picked), 3),
           "tickets_chosen": [{"legs": [l["label"] if "label" in l else l["name"] for l in c["legs"]], "hit_chance": round(c["p"], 3), "price": round(c["price"]), "ev_after_3pt_haircut": round(c["ev_haircut"], 4)} for c in picked]}
    return out


def pool_calibration(games: dict, rows: list[dict]) -> dict:
    """Every candidate leg that had a positive edge on 2026-10-08 and 2026-10-09 (the pool the tickets were built from), scored against the official box scores -- not only the 15 legs that
    reached a ticket. Reconstructed from the stored selection reports; a leg that could not be matched to an official player is listed, not guessed."""
    ledger_legs = {}
    for r in rows:
        if r["is_combo"] and r["origin"] == "AUTOMATIC" and r["created_at_utc"].startswith("2026-10-09"):
            for l in json.loads(r["legs_json"]):
                th = int(l["threshold"])
                fam = {"PLAYER_POINTS": f"{l['participant_name']} {th}+ point", "PLAYER_SOG_ALTERNATE": f"{l['participant_name']} {th}+ shots on goal"}[l["market_family"]]
                ledger_legs[fam] = float(l["conservative_probability"])
    legs, missing = [], {}
    for day, builder in (("2026-10-08", lambda: oct8_pool(games)), ("2026-10-09", lambda: oct9_pool(games, ledger_legs))):
        got, miss = builder()
        missing[day] = miss
        for l in got:
            r = leg_result({"game_id": l.game_id, "participant_id": l.participant_id, "market_family": l.market_family, "threshold": l.threshold}, games)
            if r.get("verified"):
                legs.append({"day": day, "p": l.conservative_probability, "price": l.american_price, "implied": 100 / (l.american_price + 100) if l.american_price > 0 else -l.american_price / (-l.american_price + 100),
                             "market": "shots on goal" if "SOG" in l.market_family else ("points" if "POINTS" in l.market_family else "goals"), "hit": bool(r["hit"]), "game": l.game_id})
    def row(sel):
        n = len(sel)
        if not n:
            return {"legs": 0}
        pb = pm.poisson_binomial([x["p"] for x in sel])
        return {"legs": n, "model_said": round(sum(x["p"] for x in sel) / n, 3), "price_implied_before_margin": round(sum(x["implied"] for x in sel) / n, 3), "happened": round(sum(x["hit"] for x in sel) / n, 3),
                "expected_hits": round(pb["mean"], 1), "observed_hits": sum(x["hit"] for x in sel), "chance_of_this_few_or_fewer_if_right_ignoring_same_game_clustering": round(pb["cdf"](sum(x["hit"] for x in sel)), 3)}
    out = {"all_candidate_legs": row(legs), "by_market": {m: row([x for x in legs if x["market"] == m]) for m in sorted({x["market"] for x in legs})},
           "by_day": {d: row([x for x in legs if x["day"] == d]) for d in sorted({x["day"] for x in legs})},
           "by_probability_band": {name: row([x for x in legs if lo <= x["p"] < hi]) for name, lo, hi in (("under 30%", 0, .30), ("30-50%", .30, .50), ("50% and over", .50, 1.01)) if [x for x in legs if lo <= x["p"] < hi]},
           "legs_not_matched_to_an_official_player": missing,
           "reading": (f"Small and clustered (legs in one game share the game's scoring): a hint at most. Across the two evenings the model said {row(legs).get('model_said')} on average and "
                       f"{row(legs).get('happened')} happened ({row(legs).get('observed_hits')} hits against {row(legs).get('expected_hits')} expected). The points legs account for most of the gap "
                       f"({row([x for x in legs if x['market'] == 'points']).get('observed_hits')} hits against {row([x for x in legs if x['market'] == 'points']).get('expected_hits')} expected); shots legs were in line. "
                       "Six slices were looked at, so one that looks unusual is expected by chance alone; this is a thing to WATCH, not a finding. It is exactly the question the shadow log "
                       "(operational/shadow_selection.py) now collects forward, before the games, for every priced leg.")}
    return out


def pool_units(games: dict, rows: list[dict]) -> dict:
    """How many INDEPENDENT-ish units the candidate pool gives per night. A player's 2+, 3+, 4+ and 5+ shots are one nested event, so the unit is the player-and-market, not the leg row."""
    ledger_legs = {}
    for r in rows:
        if r["is_combo"] and r["origin"] == "AUTOMATIC" and r["created_at_utc"].startswith("2026-10-09"):
            for l in json.loads(r["legs_json"]):
                th = int(l["threshold"])
                lab = {"PLAYER_POINTS": f"{l['participant_name']} {th}+ point", "PLAYER_SOG_ALTERNATE": f"{l['participant_name']} {th}+ shots on goal"}[l["market_family"]]
                ledger_legs[lab] = float(l["conservative_probability"])
    out = {}
    for day, builder in (("2026-10-08", lambda: oct8_pool(games)), ("2026-10-09", lambda: oct9_pool(games, ledger_legs))):
        legs, _ = builder()
        out[day] = {"candidate_legs": len(legs), "distinct_player_markets": len({(l.game_id, l.participant_id, l.market_family) for l in legs}), "distinct_players": len({(l.game_id, l.participant_id) for l in legs}),
                    "games": len({l.game_id for l in legs})}
    return out


def evidence_targets(games: dict, rows: list[dict]) -> dict:
    """What the 620-leg and 150-game targets assume, what they test, and what they do NOT test. Every figure is computed here from the formulas in operational/postmortem_math.py."""
    units = pool_units(games, rows)
    nights = list(units.values())
    per_night = sum(u["distinct_player_markets"] for u in nights) / len(nights)
    games_per_night = sum(u["games"] for u in nights) / len(nights)
    est = json.loads(DEPENDENCE_ESTIMATES.read_text()) if DEPENDENCE_ESTIMATES.exists() else {}
    pts = (est.get("markets") or {}).get("points>=1", {})
    shots = (est.get("markets") or {}).get("shots>=2", {})
    icc_game_points = ((pts.get("same_team") or {}).get("correlation", 0.0) + (pts.get("opposite_teams") or {}).get("correlation", 0.0)) / 2
    icc_game_shots = ((shots.get("same_team") or {}).get("correlation", 0.0) + (shots.get("opposite_teams") or {}).get("correlation", 0.0)) / 2
    m_game = per_night / games_per_night
    base = pm.legs_needed(0.05)
    scenarios = []
    for label, icc_night in (("no shared nightly model error", 0.0), ("shared nightly model error, correlation 0.02", 0.02), ("shared nightly model error, correlation 0.05", 0.05)):
        deff = pm.design_effect(per_night, icc_night) * pm.design_effect(m_game, max(icc_game_points, 0.0))
        n = pm.legs_needed(0.05, deff=deff)
        scenarios.append({"assumption": label, "design_effect": round(deff, 2), "independent_units_needed": n, "priced_game_nights_at_the_measured_rate": round(n / per_night)})
    sd_models = 0.1146                                        # per-game sd of the paired log-loss difference, strength vs Elo, 1,312 held-out games (docs/validation/moneyline_model_comparison.json)
    sd_legacy = 0.1553                                        # per-game sd of (legacy win model - market no-vig) log loss over the 38 live games
    return {
        "the_620_figure": {
            "what_it_is": f"{base} resolved, independent legs: the sample needed to detect that probabilities are 5 points too high (one-sided 5% test, 80% power), using the outcome variance p(1-p) at p = 0.5.",
            "formula": "n = (1.645 + 0.8416)^2 * p(1-p) / 0.05^2",
            "assumptions": ["outcomes independent (they are not: see below)", "one pooled comparison of the average prediction with the observed rate (each market tested alone needs its own sample: points and shots separately is about twice as many)",
                            "p near 0.5, the largest variance, so it overstates the need for long shots and understates nothing", "a single fixed test, no allowance for looking at several bands or markets",
                            "the bias to detect is the same for every leg, but the selection effect (winner's curse) is larger for the legs with the biggest model-minus-price gap, so a pooled average understates it for the legs actually chosen"],
            "unit": "an independent player-and-market-and-night, NOT a leg row: a player's 2+, 3+, 4+ and 5+ shots resolve together (the Oct 8 pool had 26 leg rows but 19 player-markets and 17 players; Oct 9 had 15, 14 and 12)",
            "dependence_inflates_it": scenarios,
            "per_night_units_measured": units, "mean_player_markets_per_night": round(per_night, 1), "mean_games_priced_per_night": round(games_per_night, 1),
            "what_it_evaluates": ("the legs that PASS THE EDGE FILTER (the pool a ticket can be built from), not the handful of tickets actually chosen. That is the right thing to test for selection bias, and it is a proxy: the chosen legs are the "
                                  "extreme end of that pool, so the shadow scorer also reports the top edge slice separately. It is NOT the general player pool, where the held-out validation (46,622 player-games) already shows the "
                                  "calibrated model within about a point."),
            "so_in_practice": f"About {scenarios[0]['independent_units_needed']} to {scenarios[2]['independent_units_needed']} independent units, which at {per_night:.0f} a night is roughly {scenarios[0]['priced_game_nights_at_the_measured_rate']} to {scenarios[2]['priced_game_nights_at_the_measured_rate']} priced game nights, longer when fewer games are priced."},
        "the_150_figure": {
            "what_it_is": "MIN_GAMES_FOR_A_CLAIM = 150 finished game-sides in operational/moneyline_model_path.py: below it the scoreboard says 'too few games'; at or above it the strength model may replace Elo ONLY IF the paired 95% interval of (strength - Elo) log loss lies wholly below zero.",
            "it_is_a_floor_not_a_power_calculation": "It was set as a minimum before a verdict is allowed, not derived from an effect size. It compares two MODELS (strength vs Elo), not a model with the market, and not the bets.",
            "what_it_can_detect": {"per_game_sd_of_the_paired_log_loss_difference": sd_models, "source": "1,312 held-out games, docs/validation/moneyline_model_comparison.json (the paired interval implies this sd)",
                                   "smallest_gap_150_games_can_detect": round((1.645 + 0.8416) * sd_models / math.sqrt(150), 4), "gap_the_held_out_data_actually_shows": 0.0058,
                                   "games_needed_to_detect_that_gap": pm.games_needed_for_log_loss_gap(sd_models, 0.0058)},
            "against_the_market": {"per_game_sd_legacy_model_minus_market_log_loss_on_38_live_games": sd_legacy, "mean_gap_so_far": "+0.017 (the model is WORSE than the no-vig market)",
                                   "games_needed_to_detect_a_0.01_gap": pm.games_needed_for_log_loss_gap(sd_legacy, 0.01)},
            "selected_bets_are_a_different_and_harder_test": ("A moneyline bet is placed only where the model disagrees with the market. The live record shows 1 BET in 38 games; even at 1 game in 6 "
                                                              f"(illustrative) 150 games give about 25 bets, and to see a 5-point overstatement among BETS takes about {pm.legs_needed(0.05)} of them: about {round(pm.legs_needed(0.05) * 6):,} games "
                                                              f"at 1 in 6, about {round(pm.legs_needed(0.05) * 38):,} at the observed 1 in 38. A season has about 1,300 games. The moneyline singles cannot be validated forward in any practical time, "
                                                              "and there are no historical moneyline prices here to test them on."),
            "assumptions": ["independent games (reasonable; games on one night are very weakly related)", "one pooled comparison, one-sided 5%, 80% power", "evaluates ALL games, not the bets",
                            "the variance figures come from one held-out season and 38 live games"]},
    }


def policy_tradeoffs(games: dict, rows: list[dict]) -> dict:
    """What each element of the proposed policy does to the candidate tickets of the two audited days, counted from ENTRY INFORMATION ONLY (no result is used anywhere in this function),
    next to the validation facts that motivate it. The aim is to show cost and effect, not to pick whatever would have won."""
    ledger_legs = {}
    for r in rows:
        if r["is_combo"] and r["origin"] == "AUTOMATIC" and r["created_at_utc"].startswith("2026-10-09"):
            for l in json.loads(r["legs_json"]):
                th = int(l["threshold"])
                lab = {"PLAYER_POINTS": f"{l['participant_name']} {th}+ point", "PLAYER_SOG_ALTERNATE": f"{l['participant_name']} {th}+ shots on goal"}[l["market_family"]]
                ledger_legs[lab] = float(l["conservative_probability"])
    days = {}
    for day, builder in (("2026-10-08", lambda: oct8_pool(games)), ("2026-10-09", lambda: oct9_pool(games, ledger_legs))):
        legs, _ = builder()
        days[day] = _candidates(legs)

    def stats(c):
        n = len(c)
        if not n:
            return {"tickets": 0}
        pts = sum(1 for t in c if any("POINTS" in l["market"] or "GOALS" in l["market"] for l in t["legs"]))
        return {"tickets": n, "median_hit_chance": round(sorted(t["p"] for t in c)[n // 2], 3), "best_hit_chance": round(max(t["p"] for t in c), 3),
                "share_with_a_points_or_goals_leg": round(pts / n, 2), "median_margin_after_3pt_haircut": round(sorted(t["ev_haircut"] for t in c)[n // 2], 3)}

    def shaded_ok(t, shade):
        return _shaded_p(t, shade) * _dec(t["price"]) - 1.0 >= 0.0
    out = {"basis": "the tickets the rules in force on 2026-10-08/09 qualified (65-101 on Oct 8, 28 on Oct 9), reconstructed from the stored selection reports; entry information only", "days": {}}
    for day, c in days.items():
        out["days"][day] = {
            "qualified_under_the_old_rules": stats(c),
            **{f"hit_chance_at_least_{int(f * 100)}pct": stats([t for t in c if t["p"] >= f]) for f in (0.20, 0.25, 0.30, 0.35, 0.40)},
            **{f"edge_survives_{int(sh * 100)}pt_haircut": stats([t for t in c if shaded_ok(t, sh)]) for sh in (0.03, 0.05, 0.08)},
            "floor_30_and_5pt_haircut": stats([t for t in c if t["p"] >= 0.30 and shaded_ok(t, 0.05)])}
    v = json.loads(VALIDATION.read_text())["markets"]
    dens = {m: {b["bin"]: b["n"] for b in v[m]["calibration_raw"]} for m in ("shots>=2", "points>=1", "goals>=1")}
    sel = json.loads(SELECTOR_OCT8.read_text())["model_uncertainty_2025_26"]
    out["why_each_element"] = {
        "hit_chance_floor": {
            "what_it_means": "A ticket of two legs reaches 30% only if the legs average about 55% each (0.55 x 0.55 = 0.30); three legs need about 67% each. So a 30% floor is mostly a requirement for strong legs.",
            "validation_fact": ("The held-out season has the most data where such legs live: shots 2+ at 50-70% has " + f"{dens['shots>=2'].get('0.5-0.6', 0) + dens['shots>=2'].get('0.6-0.7', 0):,} player-games; "
                                f"points 1+ at 50-60% has {dens['points>=1'].get('0.5-0.6', 0):,} and above 50% is rare; goals 1+ above 40% has {dens['goals>=1'].get('0.4-0.5', 0)}. The raw model over-predicted below about 40% "
                                "(shots 2+ at 20-30%: said 25.8%, happened 20.7%) and under-predicted above 50%. The shipped model is calibrated on top of that; the bin-level table for the calibrated model is not stored."),
            "side_effect": "It removes most tickets with a points or goals leg, because those legs rarely exceed 55%. That is a consequence of the arithmetic, not a judgement about the market; the live signal on points legs is weak and unvalidated against prices.",
            "cost": "Fewer tickets and some empty days (Oct 9 would have been empty at 25% and at 30%).",
            "what_it_is_not": "Not validated. 30% is a reading of 'strong'; 25% and 35% are as defensible. The owner sets it."},
        "value_after_a_haircut": {
            "what_it_means": "The ticket must still show an edge after every leg's probability is lowered by 5 points.",
            "validation_fact": f"The measured between-player model error is {sel['shots>=2']['between_player_model_error_sd'] * 100:.1f} points for shots 2+ and {sel['points>=1']['between_player_model_error_sd'] * 100:.1f} for points 1+ "
                               f"(docs/validation/selector_audit_2026-10-08.json), so 5 points is about {5 / (sel['shots>=2']['between_player_model_error_sd'] * 100):.1f} and {5 / (sel['points>=1']['between_player_model_error_sd'] * 100):.1f} of a standard deviation: "
                               "a modest margin of safety, not a worst case. The 3-point haircut in force was about a third of a standard deviation.",
            "side_effect": "Alone it favours long shots (their edges are larger in relative terms): on Oct 9 it would have kept two tickets whose best hit chance was 8.4%. It only makes sense together with the floor.",
            "cost": "Fewer tickets.", "what_it_is_not": "Not validated. It is a stand-in for the model's uncertainty until the shadow log measures it on the legs that are actually selected."},
        "exposure_one_ticket_per_player": {
            "what_it_means": "A player may be on one ticket a day (the rules in force allowed two, and before 2026-10-09 any number).",
            "evidence": "Shared legs raise the chance that no ticket wins (Oct 8: 29.9% against 18.2%; Oct 9: 57.2% against 50.2%): the replay in this report. With one per player the shared-leg effect disappears by construction.",
            "cost": "Fewer tickets built from the same best legs: the book spreads over more players but the best player is used once.",
            "what_it_is_not": "It reduces concentration; it does not add value. It cannot make a ticket better, only stop one bad night sinking several tickets."},
        "five_slots_empty_allowed": {"what_it_means": "Up to five tickets; zero is a normal day. The page says why a slot is empty.", "evidence": "The count of five was never evidence-based; the earlier proposal of three is withdrawn."},
    }
    return out


def replay(rows: list[dict], games: dict) -> dict:
    out = {"variants_defined_before_any_replacement_result_was_viewed": {k: v["label"] for k, v in VARIANTS.items()}, "days": {}}
    ledger_legs = {}
    for r in rows:
        if r["is_combo"] and r["origin"] == "AUTOMATIC" and r["created_at_utc"].startswith("2026-10-09"):
            for l in json.loads(r["legs_json"]):
                th = int(l["threshold"])
                lab = {"PLAYER_POINTS": f"{l['participant_name']} {th}+ point", "PLAYER_SOG_ALTERNATE": f"{l['participant_name']} {th}+ shots on goal"}[l["market_family"]]
                ledger_legs[lab] = float(l["conservative_probability"])
    for day, builder, recorded_rule in (("2026-10-08", lambda: oct8_pool(games), "rules_on_oct_8"), ("2026-10-09", lambda: oct9_pool(games, ledger_legs), "current_code")):
        legs, missing = builder()
        cands = _candidates(legs)
        recorded = [r for r in rows if r["is_combo"] and r["origin"] == "AUTOMATIC" and r["created_at_utc"].startswith(day)]
        rec_sets = {frozenset(f"{l['game_id']}:{l['participant_id']}:{l['market_family']}:{l['threshold']}" for l in json.loads(r["legs_json"])) for r in recorded}
        as_ran = select(cands, VARIANTS["rules_on_oct_8"] if day == "2026-10-08" else VARIANTS["current_code"])
        reproduced = {frozenset(l["id"] for l in c["legs"]) for c in as_ran} == rec_sets
        d = {"candidate_legs_reconstructed": len(legs), "legs_not_reconstructable": missing, "qualifying_tickets_regenerated": len(cands),
             "the_replay_with_that_day's_rules_reproduces_the_tickets_that_were_recorded": reproduced, "recorded_tickets": len(recorded),
             "recorded_portfolio": _portfolio(as_ran, games), "variants": {}}
        for k, rule in VARIANTS.items():
            d["variants"][k] = {"label": rule["label"], **_portfolio(select(cands, rule), games)}
        out["days"][day] = d
    return out


def what_decided_each_ticket(checks: list[dict], rows: list[dict]) -> list[dict]:
    out = []
    for c in checks:
        if not c.get("verified") or "legs" not in c:
            continue
        missed = [f"{l['player']} {l['line']} (official {l['official_value']}; {l['toi']} on ice)" for l in c["legs"] if not l["hit"]]
        hit = [f"{l['player']} {l['line']} (official {l['official_value']})" for l in c["legs"] if l["hit"]]
        out.append({"ticket": c["id"], "result": c["official_status"], "legs_that_missed": missed, "legs_that_hit": hit})
    return out


def moneyline_evidence() -> dict:
    obs = json.loads(MONEYLINE_OBS.read_text())["observations"]
    y = [1 if o["result_status"] == "WIN" else 0 for o in obs]
    raw = [o["raw"] for o in obs]
    mk = [o["market_no_vig"] for o in obs]
    import math
    import statistics as st

    def ll(ps):
        return -sum(math.log(p if yy else 1 - p) for p, yy in zip(ps, y)) / len(y)
    bands = {}
    for lo, hi, name in ((0, 0.4, "market says underdog (under 40%)"), (0.4, 0.6, "market says near a coin flip (40-60%)"), (0.6, 1.01, "market says favourite (over 60%)")):
        sel = [o for o in obs if lo <= o["market_no_vig"] < hi]
        if sel:
            bands[name] = {"team_sides": len(sel), "market_probability": round(sum(o["market_no_vig"] for o in sel) / len(sel), 3), "model_probability": round(sum(o["raw"] for o in sel) / len(sel), 3),
                           "actual_win_rate": round(sum(o["result_status"] == "WIN" for o in sel) / len(sel), 3)}
    return {"team_sides": len(obs), "games": len({o["game_id"] for o in obs}), "log_loss_model": round(ll(raw), 4), "log_loss_market_no_vig": round(ll(mk), 4), "log_loss_coin_flip": round(ll([0.5] * len(y)), 4),
            "spread_of_model_probabilities_sd": round(st.pstdev(raw), 3), "spread_of_market_probabilities_sd": round(st.pstdev(mk), 3), "by_market_band": bands,
            "reading": "The legacy win model's probabilities are squeezed toward 50% (spread 0.063 against the market's 0.100). A model like that shows a large 'edge' on every big underdog by construction, "
                       "and on 12 underdog sides it said 44% where the market said 34% and 2 of 12 won. 38 games cannot settle it, but it is the opposite of evidence of an edge."}


def validation_evidence() -> dict:
    v = json.loads(VALIDATION.read_text())
    low = json.loads(LOW_SAMPLE.read_text())["slices_by_prior_games"]["80+"]
    sel = json.loads(SELECTOR_OCT8.read_text())["model_uncertainty_2025_26"]
    return {"held_out_season": "2025-26, scored once, walk-forward (docs/validation/skater_projection_validation.json)",
            "shipped_calibrated_model_players_with_80_plus_games": {k: low[k] for k in ("shots>=2", "shots>=3", "points>=1", "goals>=1")},
            "same_in_the_probability_bands_the_tickets_used": sel,
            "reading": "On 46,622 held-out player-games the calibrated model's average prediction is within about one point of what happened in every market the tickets used, and slightly UNDER-predicts in "
                       "the 50-62% shots and 38-50% points bands. That is evidence about the model on ALL players. It is not evidence about the subset the selector picks (the legs where the model most "
                       "disagrees with the price), and there are no historical sportsbook prices to test that subset on."}


def run(rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else load_ledger()
    games = _box()
    tickets = _leg_ids(rows)
    streak = losing_streak(rows)
    streak_ids = [s["id"] for s in streak["streak"]]
    checks = [ticket_check(r, games) for r in rows if r["is_combo"] and r["origin"] == "AUTOMATIC"] + [moneyline_check(r, games) for r in rows if r["origin"] == "AUTOMATIC" and not r["is_combo"]]
    legs = unique_leg_outcomes(tickets, games)
    ml = next((r for r in rows if r["origin"] == "AUTOMATIC" and not r["is_combo"]), None)
    ml_ps = {"model_raw": round(ml["model_probability"], 4), "model_conservative": round(ml["conservative_probability"], 4), "market_no_vig": round(ml["market_no_vig_probability"], 4)} if ml else {}
    report = {
        "as_of": "2026-10-10",
        "ledger": {"rows": len(rows), "by_kind": {k: sum(1 for r in rows if kind_of(r) == k) for k in sorted({kind_of(r) for r in rows})}},
        "streak": streak, "bankroll": bankroll_impact(rows),
        "independent_settlement_check": {"all_agree": all(c.get("agrees") for c in checks if c.get("verified")), "checked": sum(1 for c in checks if c.get("verified")),
                                         "not_verifiable": [c["id"] for c in checks if not c.get("verified")], "tickets": checks},
        "exposure_by_day": exposure_by_day(tickets),
        "streak_probability": streak_probability(tickets, streak_ids, ml_ps) if ml_ps else None,
        "streak_probability_dependence_sensitivity": dependence_sensitivity(tickets, streak_ids) if ml_ps else None,
        "sequence_probability_using_ml_conservative": sequence_probability(rows, tickets, ml_ps.get("model_conservative", 0.4), streak["streak_length"], streak["wins"]) if ml_ps else None,
        "recorded_legs": {"unique_legs": len(legs), "calibration": calibration_of_recorded_legs(legs), "evidence_strength": evidence_strength(legs), "legs": legs},
        "ticket_acceptance_at_entry": ticket_acceptance(tickets, rows), "hit_chance_floors_by_entry_information_only": hit_floor_table(tickets),
        "what_decided_each_ticket": what_decided_each_ticket(checks, rows),
        "validation_evidence": validation_evidence(), "moneyline_single_bet_evidence": moneyline_evidence(),
        "evidence_targets": evidence_targets(games, rows), "policy_tradeoffs_entry_information_only": policy_tradeoffs(games, rows),
        "candidate_pool_calibration_oct_8_and_9": pool_calibration(games, rows),
        "replay_of_the_original_decisions": replay(rows, games),
    }
    return report


def refresh_boxscores(game_ids: list[str]) -> None:
    out = {"source": "https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore (official NHL, read-only)", "games": {}}
    for gid in game_ids:
        raw = subprocess.run(["curl", "-s", "-m", "30", f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore"], capture_output=True, text=True).stdout
        b = json.loads(raw)
        g = {"date": b["gameDate"], "state": b["gameState"], "away": b["awayTeam"]["abbrev"], "home": b["homeTeam"]["abbrev"], "away_score": b["awayTeam"].get("score"), "home_score": b["homeTeam"].get("score"),
             "last_period": b.get("gameOutcome", {}).get("lastPeriodType"), "start_utc": b.get("startTimeUTC"), "skaters": {}}
        for side in ("homeTeam", "awayTeam"):
            for grp in ("forwards", "defense"):
                for x in b["playerByGameStats"][side][grp]:
                    g["skaters"][str(x["playerId"])] = {"name": x["name"]["default"], "team": b[side]["abbrev"], "toi": x["toi"], "sog": x["sog"], "goals": x["goals"], "assists": x["assists"], "points": x["points"]}
        out["games"][str(b["id"])] = g
    BOX.write_text(json.dumps(out, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--refresh-boxscores", action="store_true")
    ap.add_argument("--use-extract", action="store_true", help="read the frozen ledger extract instead of the live database")
    a = ap.parse_args()
    if a.refresh_boxscores:
        refresh_boxscores(sorted(_box().keys()))
    rep = run(load_ledger(use_extract=a.use_extract))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    s = rep["streak"]
    print(f"model book: {s['model_book_settled']} settled, {s['wins']}W-{s['losses']}L; the run of consecutive losses at the end is {s['streak_length']} "
          f"({s['streak_parlay_tickets']} parlay tickets + {s['streak_single_bets']} single bet)")
    print(f"independent settlement check: {rep['independent_settlement_check']['checked']} checked, all agree = {rep['independent_settlement_check']['all_agree']}")
    sp = rep["streak_probability"]
    if sp:
        print("P(all streak parlay tickets lose):", sp["legs_shared_players_independent_across_markets"]["all_tickets_lose"], "(shared legs, exact) vs", sp["product_of_ticket_loss_chances_WRONG_independent"], "(wrong: independent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
