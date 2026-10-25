"""How do parlay legs in the SAME GAME actually move together? Measured, not assumed. Read-only on the MoneyPuck logs on disk; writes one file.

    python3 deploy/validate_dependence.py --out docs/validation/dependence_lift.json

For every pair of legs of a type the engine can build, in games of 2025-26 and 2026-27 (out of time for the model's calibration), the LIFT is observed joint hits divided by the joint hits the model's own
probabilities predict if the two were independent: lift 1.0 = independent, above 1.0 = they tend to hit together. Pairs are weighted by the model probabilities (a parlay leg is a likely outcome), the interval is a
bootstrap over GAMES (the pairs in one game are not independent of each other), and cross-game pairs on the same date are the control (their lift should be 1.0). Team-result legs use the team model's pre-game win
probability. A lift is usable by the builder only with 1,000+ pairs and an interval that excludes nothing it needs: the builder multiplies pairwise lifts as an APPROXIMATION and labels it so.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
MIN_P = 0.45                                                         # parlay-leg territory (a leg the engine would use is a likely outcome)


def main() -> int:
    from deploy import build_eggy_evidence as B
    from research.product_models import history, live, matchup as M, projections as P
    from research.product_models.validate import apply_platt, walk
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    alphas, platt = val["dispersion"], val["platt_params"]
    team_rows = M.team_games()
    _, env = B.build_matchup(team_rows, log=lambda *x: None)         # {game_id: expanding-window home win probability}
    by_key = {(r["player_id"], r["date"]): r for r in rows}
    games = defaultdict(list)
    for w in walk(cfg, (2025, 2026), rows, with_baselines=False):
        r = by_key.get((w["pid"], w["date"]))
        if r is None:
            continue
        p_s = apply_platt(P.prob_at_least(2, w["mean"]["shots"], alphas["shots"]), *platt["shots>=2"])
        p_p = apply_platt(P.prob_at_least(1, w["mean"]["points"], alphas["points"]), *platt["points>=1"])
        games[(r["game_id"], r["date"])].append({"pid": w["pid"], "team": r["team"], "home": bool(r["home"]), "s": (p_s, int(w["actual"]["shots"] >= 2)), "p": (p_p, int(w["actual"]["points"] >= 1)), "date": w["date"]})
    # the team result for every game: from the walked players' own teams and the game's score is not in these rows, so use the team file's goals for/against by game id
    result = {}
    for t in team_rows:
        result[(str(t["g"]), t["t"])] = int(t["gf"] > t["ga"])
    acc = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0.0]))      # type -> game -> [pairs, sum p_a*p_b, sum y_a*y_b]

    def add(kind, gid, pa, ya, pb, yb):
        c = acc[kind][gid]
        c[0] += 1; c[1] += pa * pb; c[2] += ya * yb
    for (gid, date), ps in games.items():
        teams = sorted({x["team"] for x in ps})
        win_p = {}
        hp = env.get(str(gid))
        if hp is not None and len(teams) == 2:
            home = next((x["team"] for x in ps if x["home"]), None)
            if home:
                win_p = {home: hp, [t for t in teams if t != home][0]: 1 - hp}
        sl = [x for x in ps if x["s"][0] >= MIN_P]
        pl = [x for x in ps if x["p"][0] >= MIN_P]
        for x, y in itertools.combinations(sl, 2):
            add("SOG_SOG_TEAMMATES" if x["team"] == y["team"] else "SOG_SOG_OPPOSING", gid, x["s"][0], x["s"][1], y["s"][0], y["s"][1])
        for x, y in itertools.combinations(pl, 2):
            add("PTS_PTS_TEAMMATES" if x["team"] == y["team"] else "PTS_PTS_OPPOSING", gid, x["p"][0], x["p"][1], y["p"][0], y["p"][1])
        for x in sl:
            for y in pl:
                if x["pid"] == y["pid"]:
                    continue
                add("SOG_PTS_TEAMMATES" if x["team"] == y["team"] else "SOG_PTS_OPPOSING", gid, x["s"][0], x["s"][1], y["p"][0], y["p"][1])
        for team, wp in win_p.items():
            won = result.get((str(gid), team))
            if won is None or wp < MIN_P:
                continue
            for x in ps:
                same = x["team"] == team
                if x["s"][0] >= MIN_P:
                    add("ML_SOG_SAMETEAM" if same else "ML_SOG_OPPOSING", gid, wp, won, x["s"][0], x["s"][1])
                if x["p"][0] >= MIN_P:
                    add("ML_PTS_SAMETEAM" if same else "ML_PTS_OPPOSING", gid, wp, won, x["p"][0], x["p"][1])
    # the controls: the SAME market combination from DIFFERENT games on the same date. The model's own probabilities carry a common calibration bias (it under-predicts likely legs a little), which would read as
    # dependence between any two legs; a control pair has no shared game, so its lift is that bias alone, and the dependence of a same-game pair is its lift RELATIVE to its control.
    rng = random.Random(3)
    by_date = defaultdict(list)
    for (gid, date), ps in games.items():
        wins = []
        hp = env.get(str(gid))
        home = next((x["team"] for x in ps if x["home"]), None)
        teams = sorted({x["team"] for x in ps})
        if hp is not None and home and len(teams) == 2:
            for team, wp in ((home, hp), ([t for t in teams if t != home][0], 1 - hp)):
                won = result.get((str(gid), team))
                if won is not None and wp >= MIN_P:
                    wins.append((wp, won))
        by_date[date].append((gid, [x for x in ps if x["s"][0] >= MIN_P], [x for x in ps if x["p"][0] >= MIN_P], wins))
    for date, gl in by_date.items():
        for (g1, s1, p1, w1), (g2, s2, p2, w2) in itertools.combinations(gl, 2):
            for _ in range(6):
                if s1 and s2:
                    x, y = rng.choice(s1), rng.choice(s2); add("CONTROL_SOG_SOG", g1, x["s"][0], x["s"][1], y["s"][0], y["s"][1])
                if p1 and p2:
                    x, y = rng.choice(p1), rng.choice(p2); add("CONTROL_PTS_PTS", g1, x["p"][0], x["p"][1], y["p"][0], y["p"][1])
                if s1 and p2:
                    x, y = rng.choice(s1), rng.choice(p2); add("CONTROL_SOG_PTS", g1, x["s"][0], x["s"][1], y["p"][0], y["p"][1])
                if w1 and s2:
                    (wp, won), y = rng.choice(w1), rng.choice(s2); add("CONTROL_ML_SOG", g1, wp, won, y["s"][0], y["s"][1])
                if w1 and p2:
                    (wp, won), y = rng.choice(w1), rng.choice(p2); add("CONTROL_ML_PTS", g1, wp, won, y["p"][0], y["p"][1])
    out = {"method": __doc__.split("\n\n")[1].strip(), "min_p": MIN_P, "pairs": {}}
    stats = {}
    for kind, per in sorted(acc.items()):
        gids = list(per)
        tot = [sum(per[g][i] for g in gids) for i in range(3)]
        boots = []
        for _ in range(300):
            samp = [per[rng.choice(gids)] for _ in range(len(gids))]
            e = sum(x[1] for x in samp)
            boots.append(sum(x[2] for x in samp) / e if e else 1.0)
        stats[kind] = (tot, boots)
    control_of = {"SOG_SOG": "CONTROL_SOG_SOG", "PTS_PTS": "CONTROL_PTS_PTS", "SOG_PTS": "CONTROL_SOG_PTS", "ML_SOG": "CONTROL_ML_SOG", "ML_PTS": "CONTROL_ML_PTS"}
    for kind, (tot, boots) in stats.items():
        raw = tot[2] / tot[1]
        row = {"pairs": tot[0], "games": len(acc[kind]), "expected_joint_if_independent": round(tot[1] / tot[0], 4), "observed_joint": round(tot[2] / tot[0], 4), "raw_lift": round(raw, 3)}
        ck = control_of.get("_".join(kind.split("_")[:2]))
        if ck and not kind.startswith("CONTROL") and ck in stats:
            ct, cb = stats[ck]
            craw = ct[2] / ct[1]
            ratios = sorted(b / c for b, c in zip(boots, sorted(cb, key=lambda _: rng.random())))
            row.update(control_lift=round(craw, 3), dependence_lift=round(raw / craw, 3), ci95=[round(ratios[7], 3), round(ratios[292], 3)])
            row["usable"] = bool(tot[0] >= 1000)
            row["reading"] = ("positive dependence" if ratios[7] > 1.0 else ("negative dependence" if ratios[292] < 1.0 else "indistinguishable from independent"))
        out["pairs"][kind] = row
    Path(a.out).write_text(json.dumps(out, indent=1))
    for k, v in out["pairs"].items():
        print(f"{k:22s} pairs {v['pairs']:>7}  raw lift {v['raw_lift']:.3f}" + (f"  control {v['control_lift']:.3f}  DEPENDENCE {v['dependence_lift']:.3f} [{v['ci95'][0]:.3f}, {v['ci95'][1]:.3f}]  {v['reading']}" if "dependence_lift" in v else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
