"""Does the goalie add predictive information the live probabilities do not already hold? Out-of-time, read-only, MoneyPuck files already on disk.

    python3 deploy/validate_goalie_signal.py --out docs/validation/goalie_signal.json

A goalie's quality is the decayed average of (expected goals against - goals against) over his own previous STARTS (strictly before the game; half-life 25 starts; shrunk toward zero with 10 starts of
average play). Two versions of who plays are tested: the ACTUAL starter (an upper bound: the engine would not always know him) and the team's PREVIOUS starter (what is knowable the day before).
If the actual starter does not help, a confirmed or expected starter cannot.

THE RULE, declared before the numbers: a goalie signal is integrated into a probability only if the 95% bootstrap interval of its paired log-loss improvement over the live probability is ENTIRELY ABOVE
ZERO out of time. Otherwise it is rejected and left out. Three questions:
  A. persistence (scoring environment): does a goalie's rating predict his NEXT start's goals-saved-above-expected? (slope and interval);
  B. team win probability: does goalie_diff add to the production team model's logit (gd, xgd, hd, b2b), seasons 2024 and 2025, each predicted from earlier seasons only?
  C. player points: does the opposing starter's rating add to the product model's points>=1 / >=2 probability, fitted on games before 2026-02-01 and scored after?
Calibration only: no historical prices exist, so nothing here is value, edge or profit.
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HALF_LIFE_STARTS, SHRINK_STARTS, MIN_PRIOR_STARTS = 25.0, 10.0, 5
RULE = {"integrate_only_if": "the 95% bootstrap interval of the paired log-loss improvement is entirely above zero", "bootstrap_draws": 2000}
SPLIT = "2026-02-01"


def goalie_starts() -> dict:
    """{game_id: {team: {'pid', 'gsax', 'date'}}}: the goalie with the most ice time for each team in each game, from every goalie file on disk."""
    files = sorted(glob.glob(str(REPO / "research" / "goalie_intelligence" / "raw" / "*.csv")))
    texts = [Path(f).read_text() for f in files]
    for z in sorted(glob.glob(str(REPO / "data" / "raw" / "moneypuck" / "goalie" / "2026" / "*.zip")))[-1:]:
        zf = zipfile.ZipFile(z)
        texts.append(zf.read(zf.namelist()[0]).decode())
    best: dict = {}
    for t in texts:
        for r in csv.DictReader(io.StringIO(t)):
            if r.get("situation") != "all":
                continue
            gid, team = int(r["gameId"]), r["playerTeam"]
            ice = float(r["icetime"] or 0)
            cur = best.setdefault(gid, {}).get(team)
            if cur is None or ice > cur["ice"]:
                best[gid][team] = {"pid": r["playerId"], "gsax": float(r["xGoals"]) - float(r["goals"]), "date": f"{r['gameDate'][:4]}-{r['gameDate'][4:6]}-{r['gameDate'][6:]}", "ice": ice}
    return best


def ratings(starts: dict) -> dict:
    """{game_id: {team: {'actual': rating of the starter, 'expected': rating of the team's previous starter, 'n_prior': starts before this one}}}, every rating as of the day BEFORE the game."""
    lam = 0.5 ** (1.0 / HALF_LIFE_STARTS)
    state: dict = {}                       # pid -> [weight, weighted sum, starts]
    prev: dict = {}                        # team -> previous starter pid
    out: dict = {}
    by_date: dict = defaultdict(list)
    for gid, teams in starts.items():
        for team, s in teams.items():
            by_date[s["date"]].append((gid, team, s))
    rate = lambda pid: (lambda w: w[1] / (w[0] + SHRINK_STARTS))(state.get(pid, [0.0, 0.0, 0]))
    for d in sorted(by_date):
        todays = by_date[d]
        for gid, team, s in todays:
            out.setdefault(gid, {})[team] = {"actual": rate(s["pid"]), "expected": rate(prev[team]) if team in prev else 0.0, "n_prior": state.get(s["pid"], [0, 0, 0])[2], "gsax": s["gsax"]}
        for gid, team, s in todays:
            w = state.setdefault(s["pid"], [0.0, 0.0, 0])
            w[0], w[1], w[2] = lam * w[0] + 1.0, lam * w[1] + s["gsax"], w[2] + 1
            prev[team] = s["pid"]
    return out


def _boot(np, delta, n=2000, seed=1337):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(delta), (n, len(delta)))
    m = delta[idx].mean(axis=1)
    return {"improvement": float(delta.mean()), "ci_low": float(np.percentile(m, 2.5)), "ci_high": float(np.percentile(m, 97.5)), "n": int(len(delta))}


def _verdict(b: dict) -> str:
    return "INTEGRATE" if b["ci_low"] > 0 else "REJECT: no demonstrated improvement out of time"


def test_persistence(np, R: dict, starts: dict) -> dict:
    xs, ys, blocks = [], [], []
    for gid, t in R.items():
        for team, r in t.items():
            if r["n_prior"] >= MIN_PRIOR_STARTS:
                xs.append(r["actual"]); ys.append(r["gsax"]); blocks.append(starts[gid][team]["date"][:7])
    x, y = np.array(xs), np.array(ys)
    slope = float(np.cov(x, y)[0, 1] / np.var(x, ddof=1))
    rng = np.random.default_rng(7)
    months = sorted(set(blocks))
    idx_by = {m: np.where(np.array(blocks) == m)[0] for m in months}
    sl = []
    for _ in range(1000):
        pick = np.concatenate([idx_by[months[i]] for i in rng.integers(0, len(months), len(months))])
        sl.append(np.cov(x[pick], y[pick])[0, 1] / np.var(x[pick], ddof=1))
    return {"question": "does a goalie's pre-game rating predict his next start's goals saved above expected?", "starts": len(x), "slope": round(slope, 4), "ci95": [round(float(np.percentile(sl, 2.5)), 4), round(float(np.percentile(sl, 97.5)), 4)],
            "correlation": round(float(np.corrcoef(x, y)[0, 1]), 4), "mean_gsax_per_start": round(float(y.mean()), 4), "sd_gsax_per_start": round(float(y.std()), 4),
            "verdict": "PERSISTENT (rating has information)" if np.percentile(sl, 2.5) > 0 else "NOT DEMONSTRATED: the rating's slope interval includes zero"}


def test_win_probability(np, R: dict) -> dict:
    from deploy import build_eggy_evidence as B
    from research.product_models import matchup as M
    F = B.walk_features(M.team_games())
    names = list(M.MODEL_FEATURES)
    seasons = sorted({r["season"] for r in F})
    w_by = {}
    def base_w(S):
        if S not in w_by:
            tr = [r for r in F if 2009 <= r["season"] < S and r["y"] is not None]
            X, y = B._mat(np, tr, names)
            w_by[S] = B._fit(np, X, y)
        return w_by[S]
    def rows_for(S):
        out = []
        for r in F:
            if r["season"] != S or r["y"] is None or r["game"] not in R and int(r["game"]) not in R:
                continue
            g = R.get(r["game"]) or R.get(int(r["game"]))
            if r["home"] not in g or r["away"] not in g:
                continue
            x = np.array([1.0] + [r["f"][n] for n in names])
            out.append({"logit": float(x @ base_w(S)), "y": r["y"], "act": g[r["home"]]["actual"] - g[r["away"]]["actual"], "exp": g[r["home"]]["expected"] - g[r["away"]]["expected"]})
        return out
    res = {}
    for kind in ("act", "exp"):
        deltas = []
        for S in (2024, 2025):
            train = [r for T in range(2023, S) for r in rows_for(T)]
            test = rows_for(S)
            A = lambda rs, cols: np.array([[1.0] + [r[c] for c in cols] for r in rs])
            y_tr, y_te = np.array([r["y"] for r in train], float), np.array([r["y"] for r in test], float)
            w0 = B._fit(np, A(train, ["logit"]), y_tr)
            w1 = B._fit(np, A(train, ["logit", kind]), y_tr)
            p0 = 1 / (1 + np.exp(-A(test, ["logit"]) @ w0))
            p1 = 1 / (1 + np.exp(-A(test, ["logit", kind]) @ w1))
            deltas.append(B._ll(np, p0, y_te) - B._ll(np, p1, y_te))
        b = _boot(np, np.concatenate(deltas))
        res["actual_starter" if kind == "act" else "previous_starter"] = {**{k: round(v, 5) if isinstance(v, float) else v for k, v in b.items()}, "verdict": _verdict(b), "coefficient_on_goalie_diff_last_fit": round(float(w1[-1]), 4)}
    return {"question": "does home-minus-away goalie rating add to the production team model's logit (seasons 2024 and 2025, each fitted on earlier seasons only)?", **res}


def test_points(np, R: dict) -> dict:
    from deploy import build_eggy_evidence as B
    from research.product_models import history, live, projections as P
    from research.product_models.validate import apply_platt, walk
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    walked = walk(cfg, (2025, 2026), rows, with_baselines=False)
    alpha, platt = val["dispersion"]["points"], val["platt_params"]
    pts = json.loads((REPO / "docs" / "validation" / "points_thresholds.json").read_text())
    idx = {(r["player_id"], r["date"]): r for r in rows if r["season"] >= 2025}
    res = {}
    for k in (1, 2):
        recs = []
        for w in walked:
            r = idx.get((w["pid"], w["date"]))
            g = R.get(int(r["game_id"])) if r else None
            if not g or r["opp"] not in g:
                continue
            raw = P.prob_at_least(k, w["mean"]["points"], alpha)
            p = apply_platt(raw, *platt[f"points>={k}"]) if f"points>={k}" in platt else raw
            rec = pts["thresholds"][str(k)].get("recalibration")
            if rec:
                p = apply_platt(raw, rec["a"], rec["b"])
            p = min(max(p, 1e-4), 1 - 1e-4)
            recs.append({"date": w["date"], "logit": math.log(p / (1 - p)), "y": 1.0 if w["actual"]["points"] >= k else 0.0, "act": g[r["opp"]]["actual"], "exp": g[r["opp"]]["expected"]})
        for kind in ("act", "exp"):
            tr, te = [r for r in recs if r["date"] < SPLIT], [r for r in recs if r["date"] >= SPLIT]
            A = lambda rs, cols: np.array([[1.0] + [r[c] for c in cols] for r in rs])
            ytr, yte = np.array([r["y"] for r in tr]), np.array([r["y"] for r in te])
            w0, w1 = B._fit(np, A(tr, ["logit"]), ytr), B._fit(np, A(tr, ["logit", kind]), ytr)
            d = B._ll(np, 1 / (1 + np.exp(-A(te, ["logit"]) @ w0)), yte) - B._ll(np, 1 / (1 + np.exp(-A(te, ["logit", kind]) @ w1)), yte)
            b = _boot(np, d)
            res[f"points>={k}_{'actual_opposing_starter' if kind == 'act' else 'previous_opposing_starter'}"] = {**{a: round(v, 5) if isinstance(v, float) else v for a, v in b.items()}, "verdict": _verdict(b),
                                                                                                                  "coefficient_on_opposing_goalie_rating": round(float(w1[-1]), 4), "fit_rows": len(tr)}
    return {"question": "does the OPPOSING starter's rating add to the product model's points probability (fitted before " + SPLIT + ", scored after)?", **res}


def main(argv=None) -> int:
    import numpy as np
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time()
    starts = goalie_starts()
    R = ratings(starts)
    out = {"method": "python3 deploy/validate_goalie_signal.py --out docs/validation/goalie_signal.json", "rule_declared_before_the_numbers": RULE, "rating": {"half_life_starts": HALF_LIFE_STARTS, "shrink_starts": SHRINK_STARTS, "min_prior_starts": MIN_PRIOR_STARTS},
           "games_with_both_starters": sum(1 for g in R.values() if len(g) == 2), "A_persistence": test_persistence(np, R, starts), "B_team_win_probability": test_win_probability(np, R), "C_player_points": test_points(np, R)}
    verdicts = [v["verdict"] for sect in (out["B_team_win_probability"], out["C_player_points"]) for v in sect.values() if isinstance(v, dict) and "verdict" in v]
    out["integrated"] = [n for n, v in {**{"win_" + k: v for k, v in out["B_team_win_probability"].items() if isinstance(v, dict)}, **{"points_" + k: v for k, v in out["C_player_points"].items() if isinstance(v, dict)}}.items() if v["verdict"] == "INTEGRATE"]
    out["decision"] = ("INTEGRATE the signals listed" if out["integrated"] else "REJECT: goalie information adds nothing demonstrable to team win probability or player points out of time; it stays out of the probabilities")
    out["elapsed_seconds"] = round(time.time() - t0)
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("A_persistence", "B_team_win_probability", "C_player_points", "decision", "elapsed_seconds")}, indent=1)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
