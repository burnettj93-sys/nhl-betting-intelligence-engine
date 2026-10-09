"""
Selector audit: rebuild the candidate pool the engine saw at a past moment (archived DraftKings captures, the model as it stood before that day) and compare
what it chose with every qualifying alternative -- hit chance, combined price, estimated value, value after the uncertainty haircut, and the exposure rule that
blocked each higher-hit alternative. Also measures how uncertain the model's per-player probabilities are (2025-26 walk-forward) and tests the one rule change
that was suggested (every leg must individually survive the haircut) against the objective (hit chance at +100 or better).

Read-only: it makes no purchase and writes nothing but its report. Moneyline legs are not reconstructed (their prices are not archived by capture time); the
reconstructed pool is the player-prop pool, which is where all five tickets of 2026-10-08 came from.

Run: python3 deploy/audit_selector.py --as-of 2026-10-08T18:07:56Z --out docs/validation/selector_audit_2026-10-08.json
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import math
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import db  # noqa: E402
from operational import best_bets as bb  # noqa: E402
from operational import eastern_time as et  # noqa: E402
from research.product_models import history, projections as P, validate as V  # noqa: E402
from research.real_market_parlay import engine as rmp  # noqa: E402


def rebuild_pool(as_of: dt.datetime):
    conn = db.get_conn()

    def latest_asof(event_id, archive_dir=None, *, require_points=True, market=None):
        best = None
        for path in glob.glob(str(bb._archive_dir() / f"*events-{event_id}-odds*.json")):
            try:
                doc = json.loads(Path(path).read_text())
            except (OSError, json.JSONDecodeError):
                continue
            meta = doc.get("meta") or {}
            if market is not None and market not in (meta.get("market_filter") or ""):
                continue
            ts = meta.get("retrieved_at_utc")
            got = bb._parse_utc(ts) if ts else None
            if got is None or got > as_of:
                continue
            if best is None or got > best[0]:
                best = (got, doc["response"])
        return best

    def model_asof(conn, now, **kw):
        from features import point_in_time as pit
        date = et.eastern_today(now)
        rows = conn.execute("select game_id, home_team, away_team, scheduled_start_utc from games where game_date=?", (date,)).fetchall()
        today, games = {}, {}
        for r in rows:
            start = r["scheduled_start_utc"] if r["scheduled_start_utc"].endswith("Z") else r["scheduled_start_utc"] + "Z"
            today[r["home_team"]] = (r["away_team"], True, r["game_id"])
            today[r["away_team"]] = (r["home_team"], False, r["game_id"])
            games[str(r["game_id"])] = {"home": r["home_team"], "away": r["away_team"], "start_utc": start}
        stamp = as_of.strftime("%Y-%m-%dT%H:%M:%S")
        dressed = {}
        for t in today:
            row = conn.execute("select game_id from games where game_state='FINAL' and game_date < ? and (home_team=? or away_team=?) order by game_date desc, game_id desc limit 1", (date, t, t)).fetchone()
            dressed[t] = pit.team_players_who_played(conn, row[0], t, stamp) if row else set()
        curteam = pit.latest_team_by_player_since(conn, "2026-09-01", stamp)
        model = bb.compute_model_v2(today, dressed, curteam, date, rows=[r for r in history.skater_games() if r["date"] < date])
        return {"date": date, "games": games, "model": model}

    saved = (bb.latest_capture, bb.current_model)
    bb.latest_capture, bb.current_model = latest_asof, model_asof
    try:
        legs, report = bb.candidate_legs(conn, as_of)
    finally:
        bb.latest_capture, bb.current_model = saved
    return legs, report


def ev_low(l):
    return (l.conservative_probability - rmp.LEG_PROBABILITY_MARGIN) * rmp.leg_decimal(l) - 1


def leg_ev(l):
    return l.conservative_probability * rmp.leg_decimal(l) - 1


def label(c):
    return [rmp.leg_label(l) for l in c.legs]


def view(c, status=None, reason=None):
    return {"legs": label(c), "leg_prices": [l.american_price for l in c.legs], "leg_model_probabilities": [round(l.conservative_probability, 4) for l in c.legs],
            "leg_ev_after_haircut": [round(ev_low(l), 4) for l in c.legs], "hit_probability": round(c.joint_probability, 4), "estimated_price": round(c.estimated_combo_price),
            "ev_estimated": round(c.ev_estimated, 4), "ev_after_haircut": round(c.ev_conservative, 4), "status": status, "reason": reason}


def optimal_portfolio(q, k=5):
    def keys(c):
        ids = [rmp.leg_identity(l) for l in c.legs]
        return ids, {i[:2] for i in ids if i[2] != "MONEYLINE"}, [i[0] for i in ids]
    K = [keys(c) for c in q]
    best = [0.0, []]

    def ok(sel, j):
        leg, game, pl = {}, {}, {}
        for i in sel + [j]:
            ids, players, games = K[i]
            for x in ids:
                leg[x] = leg.get(x, 0) + 1
            for x in games:
                game[x] = game.get(x, 0) + 1
            for x in players:
                pl[x] = pl.get(x, 0) + 1
        return max(leg.values()) <= rmp.MAX_TICKETS_PER_LEG and max(game.values()) <= rmp.MAX_TICKETS_PER_GAME and max(pl.values()) <= rmp.MAX_TICKETS_PER_PLAYER

    def dfs(sel, start, total):
        if total > best[0]:
            best[0], best[1] = total, list(sel)
        if len(sel) == k:
            return
        for j in range(start, len(q)):
            if total + (k - len(sel)) * q[j].joint_probability <= best[0]:
                break
            if ok(sel, j):
                sel.append(j)
                dfs(sel, j + 1, total + q[j].joint_probability)
                sel.pop()
    dfs([], 0, 0.0)
    return best


def player_uncertainty():
    v = json.loads((REPO / "docs" / "validation" / "skater_projection_validation.json").read_text())
    cfg = P.ModelConfig(**{k: (tuple(x) if k == "opp_cap" else x) for k, x in v["chosen_config"].items()})
    final = V.model_probs(V.walk(cfg, (V.FINAL_SEASON,), history.skater_games(), with_baselines=False), v["dispersion"])
    out = {}
    for stat, k, lo, hi in (("shots", 2, 0.50, 0.62), ("points", 1, 0.38, 0.50)):
        a, b = v["platt_params"][f"{stat}>={k}"]
        sel = []
        for r in final:
            if r["n_prior"] < 100:
                continue
            p = V.apply_platt(r["p"][(stat, k)], a, b)
            if lo <= p < hi:
                sel.append((r.get("pid") or r.get("player_id"), p, 1 if r["actual"][stat] >= k else 0))
        n = len(sel)
        pm, om = sum(p for _, p, _ in sel) / n, sum(y for *_, y in sel) / n
        by = collections.defaultdict(list)
        for pid, p, y in sel:
            by[pid].append(y - p)
        res = [sum(x) / len(x) for x in by.values() if len(x) >= 5]
        mean = sum(res) / len(res)
        sd = math.sqrt(sum((x - mean) ** 2 for x in res) / (len(res) - 1))
        avg_n = sum(len(x) for x in by.values() if len(x) >= 5) / len(res)
        noise = math.sqrt(om * (1 - om) / avg_n)
        out[f"{stat}>={k}"] = {"band": [lo, hi], "predictions": n, "mean_predicted": round(pm, 4), "observed": round(om, 4), "bias_observed_minus_predicted": round(om - pm, 4),
                               "players_with_5plus": len(res), "sd_of_player_mean_residual": round(sd, 4), "binomial_noise_sd": round(noise, 4),
                               "between_player_model_error_sd": round(math.sqrt(max(sd ** 2 - noise ** 2, 0)), 4)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-10-08T18:07:56Z")
    ap.add_argument("--out", default=str(REPO / "docs" / "validation" / "selector_audit_2026-10-08.json"))
    args = ap.parse_args(argv)
    as_of = dt.datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    legs, report = rebuild_pool(as_of)
    pool = rmp._prepare_pool(legs)
    q = rmp._qualifying_tickets(pool)
    q.sort(key=lambda c: (-c.joint_probability, -c.ev_estimated, len(c.legs)))
    chosen = rmp.select_tickets(legs, max_tickets=5)
    rank = {frozenset(rmp.leg_identity(l) for l in c.legs): i + 1 for i, c in enumerate(q)}
    con = sqlite3.connect(f"file:{REPO / 'operational' / 'paper_bankroll.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    day = et.eastern_today(as_of)
    recorded = []
    for r in con.execute("select * from paper_bets where origin='AUTOMATIC' and created_at_utc >= ? and created_at_utc < ? and legs_json is not null order by created_at_utc",
                         (as_of.strftime("%Y-%m-%d") + "T00:00:00", (as_of + dt.timedelta(days=1)).strftime("%Y-%m-%d") + "T00:00:00")):
        k = frozenset((str(l["game_id"]), str(l["participant_id"]), l["market_family"], l["threshold"], l.get("side")) for l in json.loads(r["legs_json"]))
        recorded.append({"ticket_id": r["paper_bet_id"], "recorded_at_utc": r["created_at_utc"], "hit_probability": round(r["model_probability"], 4),
                         "estimated_price": round(r["entry_odds"]), "rank_by_hit_chance_among_qualifying": rank.get(k), "result": r["result_status"]})
    strict = [l for l in pool if ev_low(l) >= 0]
    strict_pick = rmp.select_tickets(strict, max_tickets=5)
    opt = optimal_portfolio(q)
    report_doc = {
        "as_of_utc": args.as_of, "eastern_date": day, "reconstruction": report, "pool_legs_with_positive_edge": len(pool), "games_in_pool": len({l.game_id for l in pool}),
        "qualifying_tickets": len(q),
        "pool_legs": [{"leg": rmp.leg_label(l), "price": l.american_price, "model_probability": round(l.conservative_probability, 4), "leg_ev": round(leg_ev(l), 4),
                       "leg_ev_after_haircut": round(ev_low(l), 4)} for l in sorted(pool, key=lambda l: -l.conservative_probability)],
        "recorded_tickets": recorded,
        "selection_report_today_rules": chosen["considered"],
        "top_qualifying_by_hit_chance": [view(c) for c in q[:30]],
        "portfolio": {"greedy_by_hit_chance_expected_hits": round(sum(c.joint_probability for c in chosen["tickets"]), 4),
                      "best_possible_expected_hits_under_the_same_limits": round(opt[0], 4),
                      "best_possible_set": [view(q[i]) for i in opt[1]]},
        "counterfactual_every_leg_must_survive_the_haircut": {
            "legs_surviving": f"{len(strict)} of {len(pool)}", "tickets": [view(c) for c in strict_pick["tickets"]],
            "expected_hits": round(sum(c.joint_probability for c in strict_pick["tickets"]), 4),
            "best_ticket_hit_chance": round(strict_pick["tickets"][0].joint_probability, 4) if strict_pick["tickets"] else None},
        "model_uncertainty_2025_26": player_uncertainty(),
    }
    Path(args.out).write_text(json.dumps(report_doc, indent=1))
    print(json.dumps({k: report_doc[k] for k in ("pool_legs_with_positive_edge", "qualifying_tickets", "recorded_tickets", "portfolio")}, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
