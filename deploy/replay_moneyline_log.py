"""Replay of the moneyline shadow log: the OLD selection class against the NEW review, on the games the log priced and that have finished. Read-only; writes only the output file.

    python3 deploy/replay_moneyline_log.py --out docs/validation/eggy_moneyline_replay.json

The shadow log (operational/runtime/moneyline_shadow.jsonl) holds, for each priced game and each side: the DraftKings price, the no-vig market probability, Elo and the goalie-team strength model. For every
finished game this adds the hockey model's probability (state built from games strictly before the date) and runs the review on each side exactly as the cohort does.

The OLD rule it reconstructs (what the ledger rows of Oct 20 show): the estimate used was the market's no-vig probability, the edge was that estimate less the price's implied probability (margin included),
the class was NEUTRAL while the edge stayed within two points of zero and NEGATIVE beyond, and a NEUTRAL candidate was ranked above a NEGATIVE one. The old weighted score no longer exists, so the replay
does not rank inside a class; it shows the class split, which is the part of the old logic that carried no hockey information at all.

It measures calibration and the class split, never profit as a target: no historical closing prices exist, 87 games is a small sample, and nothing here validates an edge over the market.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FLOOR = -150.0
BAND = 0.02


def decimal(a: float) -> float:
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def old_class(no_vig: float, price: float) -> tuple[str, float]:
    edge = no_vig - 1.0 / decimal(price)
    if edge >= BAND:
        return "POSITIVE_EDGE", edge
    if edge > -BAND:
        return "NEUTRAL_UNCERTAIN_EDGE", edge
    return "LEARNING_SAMPLE_NEGATIVE_EDGE", edge


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def summarise(rows: list[dict], seed: int = 7) -> dict:
    """n, wins, expected wins by the market and by the hockey model, hit rate, flat-stake ROI at the quoted prices with a bootstrap interval. Small samples are shown as small."""
    n = len(rows)
    if not n:
        return {"n": 0}
    wins = sum(r["win"] for r in rows)
    pl = [(decimal(r["price"]) - 1.0) if r["win"] else -1.0 for r in rows]
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(pl) for _ in range(n)) / n for _ in range(2000))
    return {"n": n, "wins": wins, "hit_rate": round(wins / n, 4), "expected_wins_market": round(sum(r["no_vig"] for r in rows), 2), "expected_wins_hockey_model": round(sum(r["hockey"] for r in rows), 2),
            "average_price_decimal": round(sum(decimal(r["price"]) for r in rows) / n, 3), "flat_roi": round(sum(pl) / n, 4), "roi_95_interval": [round(boots[50], 3), round(boots[1949], 3)]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import db
    from operational import eggy_intelligence as ei
    from operational import moneyline_model_path as mmp
    from research.product_models import matchup as M
    from research.real_market_parlay.engine import ParlayLeg
    ev = M.load_evidence()
    team_rows = M.team_games()
    shadow = mmp._read_log()
    conn = db.get_conn()
    games: dict = {}
    for r in shadow:
        g = games.setdefault(r["game_id"], {"home": r["home"], "away": r["away"], "date": r["game_date"], "sides": {}})
        g["sides"][r["selection"]] = r
    states: dict = {}
    sides: list[dict] = []
    for gid, g in games.items():
        gm = conn.execute("SELECT game_state, home_score, away_score FROM games WHERE game_id = ?", (int(gid),)).fetchone()
        if gm is None or gm["game_state"] != "FINAL" or gm["home_score"] == gm["away_score"] or len(g["sides"]) != 2:
            continue
        if g["date"] not in states:
            states[g["date"]] = M.build_state(team_rows, through_date=g["date"])
        ctx = ei.Context(now=None, as_of=g["date"], evidence=ev, team_state=states[g["date"]])
        ctx.skater_state = {"players": {}, "newest_game_date": None}
        ctx.games = {str(gid): {"home": g["home"], "away": g["away"], "start_utc": None}}
        home_won = gm["home_score"] > gm["away_score"]
        for team, r in g["sides"].items():
            if r.get("market_no_vig") is None or r.get("price") is None:
                continue
            is_home = team == g["home"]
            opp = g["away"] if is_home else g["home"]
            leg = ParlayLeg(game_id=str(gid), event_id=None, market_family="MONEYLINE", participant_id=team, participant_name=team, side="HOME" if is_home else "AWAY", threshold=None, american_price=float(r["price"]),
                            conservative_probability=r["market_no_vig"], sportsbook="DRAFTKINGS", captured_at_utc=None, provider_contract_verified=True, model_threshold_eligible=True, identity_resolved=True,
                            price_fresh=True, event_not_started=True, team=team, opponent=opp, model_probability=r["elo"], market_probability=r["market_no_vig"])
            card = ei.review_leg(ctx, leg, "MARKET_NO_VIG", r["market_no_vig"])
            cls, edge_old = old_class(r["market_no_vig"], r["price"])
            sides.append({"game_id": gid, "date": g["date"], "team": team, "home": is_home, "price": r["price"], "no_vig": r["market_no_vig"], "elo": r["elo"], "strength": r["strength"],
                          "hockey": card["probabilities"]["model"], "win": int(home_won == is_home), "underdog": r["market_no_vig"] < 0.5, "eligible": r["price"] >= FLOOR, "old_edge": round(edge_old, 4), "old_class": cls,
                          "new_action": card["action"]["decision"], "new_thesis": card["thesis"]["type"], "new_contradiction": card["contradiction"]["level"]})
    conn.close()
    finished_games = len({s["game_id"] for s in sides})
    home = [s for s in sides if s["home"]]
    cal = {name: {"log_loss": round(sum(_ll(s[k], s["win"]) for s in home) / len(home), 4), "brier": round(sum((s[k] - s["win"]) ** 2 for s in home) / len(home), 4), "mean_prediction": round(sum(s[k] for s in home) / len(home), 4)}
           for name, k in (("market_no_vig", "no_vig"), ("hockey_model", "hockey"), ("strength_model", "strength"), ("elo", "elo"))}
    cal["home_win_rate"] = round(sum(s["win"] for s in home) / len(home), 4)
    # --- the mathematical reason: the old edge is the margin the book charges on THAT side, which grows with the side's probability
    overrounds = []
    for gid in {s["game_id"] for s in sides}:
        pair = [s for s in sides if s["game_id"] == gid]
        overrounds.append(sum(1.0 / decimal(s["price"]) for s in pair))
    mean_over = sum(overrounds) / len(overrounds)
    flip_p = BAND / (mean_over - 1.0)
    elig = [s for s in sides if s["eligible"]]
    split = {}
    for kind, pick in (("underdogs (market under 50%)", lambda s: s["underdog"]), ("favourites (market 50% or more)", lambda s: not s["underdog"])):
        grp = [s for s in elig if pick(s)]
        split[kind] = {"eligible_sides": len(grp), **{c: sum(1 for s in grp if s["old_class"] == c) for c in ("POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE")},
                       "mean_old_edge_pts": round(100 * sum(s["old_edge"] for s in grp) / max(len(grp), 1), 2)}
    by_class = {c: summarise([s for s in elig if s["old_class"] == c]) for c in ("POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE")}
    by_action = {act: summarise([s for s in elig if s["new_action"] == act]) for act in ("RECORD", "LOWER_PRIORITY", "PASS")}
    action_by_kind = {kind: {act: sum(1 for s in elig if s["new_action"] == act and pick(s)) for act in ("RECORD", "LOWER_PRIORITY", "PASS")}
                      for kind, pick in (("underdogs", lambda s: s["underdog"]), ("favourites", lambda s: not s["underdog"]))}
    buckets = {}
    for name, lo, hi in (("model above the market by 6+ pts", 0.06, 9.0), ("model above the market by 3 to 6 pts", 0.03, 0.06), ("within 3 pts of the market", -0.03, 0.03), ("model below the market by 3 to 6 pts", -0.06, -0.03),
                         ("model below the market by 6+ pts", -9.0, -0.06)):
        grp = [s for s in sides if lo <= s["hockey"] - s["no_vig"] < hi]
        var = sum(s["no_vig"] * (1 - s["no_vig"]) for s in grp)
        buckets[name] = {**summarise(grp), "z_wins_vs_market_expectation": (round((sum(s["win"] for s in grp) - sum(s["no_vig"] for s in grp)) / math.sqrt(var), 2) if var else None)}
    longshots = [s for s in elig if s["price"] >= 200]
    out = {"log_rows": len(shadow), "finished_games": finished_games, "sides_scored": len(sides), "eligible_sides_at_minus_150_or_better": len(elig), "calibration_home_side": cal,
           "the_mathematical_reason": {"mean_overround": round(mean_over, 4), "old_edge_formula": "old edge = no-vig p - 1/decimal(price) = -p x (overround - 1) for a proportional margin: it is the side's own share of the margin, linear in p, and carries no hockey information",
                                       "class_flips_at_no_vig_probability": round(flip_p, 4), "reading": f"with the log's mean overround of {mean_over:.4f} every side the market puts above {flip_p:.1%} reads as NEGATIVE (a margin of two points or more) and every side below it as NEUTRAL: favourites are demoted and underdogs promoted by arithmetic alone",
                                       "old_class_split_of_eligible_sides": split},
           "disagreement_buckets_all_sides": buckets, "old_classes_results": by_class, "new_actions_results": by_action, "new_actions_by_kind": action_by_kind,
           "longshots_plus_200_or_longer": {"eligible_sides": len(longshots), "old_class": {c: sum(1 for s in longshots if s["old_class"] == c) for c in ("POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE")},
                                            "new_action": {act: sum(1 for s in longshots if s["new_action"] == act) for act in ("RECORD", "LOWER_PRIORITY", "PASS")}, "results": summarise(longshots)},
           "caveats": ["A small sample: results by class are shown with their sizes and intervals and prove nothing about skill.", "The hockey model's disagreement threshold uses the evidence file's disagreement spread, which was measured on this same log (a minor look-ahead on one constant).",
                       "No historical closing prices exist: a flat stake at the logged price is not a measure of edge.", "Same-game sides are not independent: both sides of a game are one outcome."],
           "sides": sides}
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"{finished_games} finished games, {len(sides)} sides, {len(elig)} eligible (-150 or better)")
    print("calibration (home side):", json.dumps(cal))
    print("mean overround", round(mean_over, 4), "-> class flips at no-vig p =", round(flip_p, 4))
    print("old class split:", json.dumps(split))
    print("disagreement buckets:", json.dumps(buckets))
    print("old classes results:", json.dumps(by_class))
    print("new actions results:", json.dumps(by_action))
    print("new actions by kind:", json.dumps(action_by_kind))
    print("longshots:", json.dumps(out["longshots_plus_200_or_longer"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
