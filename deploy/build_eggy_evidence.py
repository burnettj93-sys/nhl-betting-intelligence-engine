"""
Builds docs/validation/eggy_evidence.json: the historical evidence Eggy's intelligence review reads. Nothing here ingests data or calls anything: it reads files already on disk
(the MoneyPuck team and skater game-by-game files, the schedule/results, the moneyline shadow log) and writes one JSON file. Re-run after the MoneyPuck files change or a season ends.

    python3 -m deploy.build_eggy_evidence            # writes docs/validation/eggy_evidence.json

What it records (every number reproducible from the files; seeds fixed):
  matchup     the team model `eggy-matchup-v1` (research/product_models/matchup.py): how its feature set was chosen (2014-2016 only; the 30-game half-life and 10-game shrink were chosen in exploratory
              work on the same seasons and are fixed constants in matchup.py, not re-derived here), its fitted coefficients, its chronological test
              (expanding window over 2017-2025) against the home-rate baseline, goal differential alone and goal + expected-goal differential, reliability by probability band, the spread of matchup
              sizes (for naming a gap SLIGHT .. DOMINANT), and a prospective comparison with the market on the games priced since October 8.
  skater      calibration "cells" for the validated skater model's probabilities (goals, points, shots), from the held-out 2025-26 season plus the current season to date: predicted against observed
              by probability band, by position group, by expected ice time, by expected power-play time and by the team's own win probability. This is what a review reads to ask "when the model
              said about this, for a player like this, how often did it happen?".
Nothing here is profit evidence: there are no historical prices.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT = REPO / "docs" / "validation" / "eggy_evidence.json"
VERSION = "eggy-evidence-v1"
VALIDATION_SEASONS = (2014, 2015, 2016)          # the feature set is chosen on these only (the half-life and shrink are fixed constants of matchup.py)
TEST_SEASONS = tuple(range(2017, 2026))          # 2020-21 included: the season is short but real
CANDIDATES = {
    "gd": ("gd",), "gd+xgd": ("gd", "xgd"), "gd+xgd+b2b": ("gd", "xgd", "b2b"), "gd+xgd+hd+b2b": ("gd", "xgd", "hd", "b2b"),
    "gd+xgd+cf5+b2b": ("gd", "xgd", "cf5", "b2b"), "gd+xgd+pp+pk+b2b": ("gd", "xgd", "pp", "pk", "b2b"), "gd+xgd+fin+b2b": ("gd", "xgd", "fin", "b2b"),
}


def _np():
    import numpy as np
    return np


# ------------------------------------------------------------------------------------------ team model
def _paired(rows: list[dict]) -> list[dict]:
    by = defaultdict(dict)
    for r in rows:
        by[r["g"]]["h" if r["h"] else "a"] = r
    games = [g for g in by.values() if "h" in g and "a" in g]
    games.sort(key=lambda g: (g["h"]["d"], g["h"]["g"]))
    return games


def walk_features(rows: list[dict]) -> list[dict]:
    """One row per game: the as-of features for every candidate (gd, xgd, hd, cf5, pp, pk, fin, b2b), the outcome (None for a shootout) and who played."""
    from research.product_models import matchup as M
    games = _paired(rows)
    st = M.State()
    out = []
    i = 0
    while i < len(games):                      # a day at a time: every game of a date is featured from the state through the PREVIOUS date, exactly as the live review builds it
        d = games[i]["h"]["d"]
        j = i
        while j < len(games) and games[j]["h"]["d"] == d:
            j += 1
        day = games[i:j]
        for g in day:
            h, a = g["h"], g["a"]
            f = {m: st.est(h["t"], m) - st.est(a["t"], m) for m in ("gd", "xgd", "hd", "cf5", "pp", "pk", "fin", "xgd5")}
            rh, ra = st.rest_days(h["t"], h["d"]), st.rest_days(a["t"], a["d"])
            f["b2b"] = float(ra <= 1) - float(rh <= 1)
            y = None if h["gf"] == a["gf"] else int(h["gf"] > a["gf"])
            out.append({"game": h["g"], "date": h["d"], "season": h["s"], "home": h["t"], "away": a["t"], "y": y, "f": f})
        for g in day:
            st.update(g["h"])
            st.update(g["a"])
        i = j
    return out


def _fit(np, X, y, iters: int = 50, ridge: float = 1e-3):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + ridge * w
        H = (X.T * (p * (1 - p))) @ X + ridge * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return w


def _mat(np, rows, names):
    return np.array([[1.0] + [r["f"][n] for n in names] for r in rows]), np.array([r["y"] for r in rows], float)


def _ll(np, p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _expanding(np, F, names, seasons):
    """Each season predicted from the seasons before it. Losses cover non-shootout games; `preds` covers every game (a shootout still has a pregame probability)."""
    losses, preds = [], {}
    for S in seasons:
        tr = [r for r in F if r["season"] < S and r["season"] >= 2009 and r["y"] is not None]
        te_all = [r for r in F if r["season"] == S]
        te = [r for r in te_all if r["y"] is not None]
        Xtr, ytr = _mat(np, tr, names)
        w = _fit(np, Xtr, ytr)
        Xte, yte = _mat(np, te, names)
        losses.append(_ll(np, 1 / (1 + np.exp(-Xte @ w)), yte))
        for r in te_all:
            preds[r["game"]] = float(1 / (1 + np.exp(-(np.array([1.0] + [r["f"][n] for n in names]) @ w))))
    return np.concatenate(losses), preds


def _boot(np, delta, n=2000, seed=1337):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(delta), (n, len(delta)))
    means = delta[idx].mean(axis=1)
    return {"point": float(delta.mean()), "ci_low": float(np.percentile(means, 2.5)), "ci_high": float(np.percentile(means, 97.5))}


def build_matchup(rows: list[dict], log=print) -> tuple[dict, dict]:
    """Returns (the `matchup` evidence block, {game_id: expanding-window home win probability} for the skater environment cells)."""
    np = _np()
    from research.product_models import matchup as M
    F = walk_features(rows)
    Fy = [r for r in F if r["season"] >= M.FIRST_FIT_SEASON]          # includes shootout games (y None): the fits and losses skip them, the pregame probabilities keep them
    log(f"  team games {len(F)}, non-shootout {sum(1 for r in Fy if r['y'] is not None)}")
    # 1. choose the feature set on 2014-2016 only
    val = {}
    for name, names in CANDIDATES.items():
        L, _ = _expanding(np, Fy, names, VALIDATION_SEASONS)
        val[name] = round(float(L.mean()), 5)
    log(f"  validation 2014-2016: {val}")
    chosen = min(val, key=val.get)
    if CANDIDATES[chosen] != M.MODEL_FEATURES:
        raise SystemExit(f"the validation chose {chosen} {CANDIDATES[chosen]} but research/product_models/matchup.py hard-codes MODEL_FEATURES = {M.MODEL_FEATURES}: "
                         "change the model and this builder together (otherwise every moneyline would read as having no hockey view)")
    # 2. the test, only for the chosen set and the baselines
    test = {}
    base_losses = {}
    for name in ("gd", "gd+xgd", chosen):
        L, preds = _expanding(np, Fy, CANDIDATES[name], TEST_SEASONS)
        base_losses[name] = L
        test[name] = round(float(L.mean()), 5)
        if name == chosen:
            chosen_preds = preds
    home_rate = []
    for S in TEST_SEASONS:
        tr = [r["y"] for r in Fy if r["season"] < S and r["y"] is not None]
        te = [r for r in Fy if r["season"] == S and r["y"] is not None]
        rate = sum(tr) / len(tr)
        home_rate.append(_ll(np, np.full(len(te), rate), np.array([r["y"] for r in te], float)))
    base_losses["home_rate"] = np.concatenate(home_rate)
    test["home_rate"] = round(float(base_losses["home_rate"].mean()), 5)
    n_test = len(base_losses["gd"])
    evaluation = {"test_seasons": f"{TEST_SEASONS[0]}-{TEST_SEASONS[-1]} (expanding window: each season predicted from the seasons before it)", "n_games": n_test, "log_loss": test,
                  "delta_vs_home_rate": _boot(np, base_losses[chosen] - base_losses["home_rate"]), "delta_vs_gd_only": _boot(np, base_losses[chosen] - base_losses["gd"]),
                  "delta_vs_gd_plus_xgd": _boot(np, base_losses[chosen] - base_losses["gd+xgd"])}
    log(f"  test log loss {test}")
    # 3. final coefficients: every non-shootout game through 2025
    names = CANDIDATES[chosen]
    final_rows = [r for r in Fy if r["season"] <= 2025 and r["y"] is not None]
    Xf, yf = _mat(np, final_rows, names)
    w = _fit(np, Xf, yf)
    coefs = {"const": float(w[0]), **{n: float(v) for n, v in zip(names, w[1:])}}
    # 4. reliability (side level: every game counted from both teams' side) and matchup-size percentiles on the test predictions
    P, Y, Pfav = [], [], []
    for r in Fy:
        if r["season"] in TEST_SEASONS and r["y"] is not None and r["game"] in chosen_preds:
            p = chosen_preds[r["game"]]
            P += [p, 1 - p]
            Y += [r["y"], 1 - r["y"]]
            Pfav.append(max(p, 1 - p))
    P, Y = np.array(P), np.array(Y, float)
    edges = [0.0, .25, .30, .35, .40, .45, .50, .55, .60, .65, .70, .75, 1.0001]
    rel = []
    for lo, hi in zip(edges, edges[1:]):
        m = (P >= lo) & (P < hi)
        if m.sum():
            ob = float(Y[m].mean())
            rel.append({"lo": lo, "hi": round(hi, 4), "n": int(m.sum()), "pred": round(float(P[m].mean()), 4), "obs": round(ob, 4), "se": round(math.sqrt(ob * (1 - ob) / m.sum()), 4)})
    pf = np.array(Pfav)
    pct = {f"p{q}": round(float(np.percentile(pf, q)), 4) for q in (35, 50, 65, 85, 95)}
    ind_sd = {}
    for m_ in ("cf5", "xgd5"):
        vals = np.array([r["f"][m_] for r in Fy if r["season"] in TEST_SEASONS and r["y"] is not None])
        ind_sd[m_] = round(float(vals.std()), 5)
    feat_sd = {n: round(float(np.array([r["f"][n] for r in Fy if r["season"] in TEST_SEASONS and r["y"] is not None]).std()), 5) for n in names}
    # the current season's pregame probabilities from the final coefficients (the skater cells use them for 2026-27 games)
    for r in F:
        if r["season"] > 2025:
            chosen_preds[r["game"]] = float(1 / (1 + np.exp(-(w[0] + sum(c * r["f"][n] for c, n in zip(w[1:], names))))))
    # 5. the prospective comparison with the market (the games priced since October 8)
    prospective = _prospective(coefs, rows, log)
    block = {"model_version": M.MODEL_VERSION, "half_life_games": M.HALF_LIFE_GAMES, "shrink_games": M.SHRINK_GAMES, "features": list(names), "coefficients": {k: round(v, 5) for k, v in coefs.items()},
             "training": {"seasons": f"2009-2025 (2008-09 warms the state)", "n_games": len(final_rows)}, "feature_selection": {"validation_seasons": "2014-2016", "log_loss": val, "chosen": chosen},
             "evaluation": evaluation, "reliability_side": rel, "favourite_percentiles": pct, "indicator_sd": ind_sd, "feature_sd": feat_sd, "prospective_vs_market": prospective,
             "version": VERSION}
    return block, chosen_preds


def _prospective(coefs: dict, rows: list[dict], log=print) -> dict | None:
    """The market against this model on the games the shadow log priced and that have finished. Read-only on the log and the official database."""
    try:
        import db
        from operational import moneyline_model_path as mmp
        from research.product_models import matchup as M
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"}
    shadow = mmp._read_log()
    if not shadow:
        return None
    conn = db.get_conn()
    games = {}
    for r in shadow:
        g = games.setdefault(r["game_id"], {"home": r["home"], "away": r["away"], "date": r["game_date"], "sel": {}})
        g["sel"][r["selection"]] = r
    per_date_state: dict = {}
    out = []
    for gid, g in games.items():
        gm = conn.execute("SELECT game_state, home_score, away_score FROM games WHERE game_id = ?", (int(gid),)).fetchone()
        hs = g["sel"].get(g["home"])
        if gm is None or gm["game_state"] != "FINAL" or gm["home_score"] == gm["away_score"] or hs is None or hs.get("market_no_vig") is None:
            continue
        if g["date"] not in per_date_state:
            per_date_state[g["date"]] = M.build_state(rows, through_date=g["date"])
        st = per_date_state[g["date"]]
        f = M.features(st, g["home"], g["away"], g["date"])
        p_home, _ = M.home_probability(coefs, f)
        out.append({"y": int(gm["home_score"] > gm["away_score"]), "M": hs["market_no_vig"], "H": p_home, "S": hs["strength"], "E": hs["elo"]})
    conn.close()
    if not out:
        return None

    def ll(p, y):
        p = min(max(p, 1e-4), 1 - 1e-4)
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))
    res = {"games": len(out), "first_logged": min(g["date"] for g in games.values()), "last_logged": max(g["date"] for g in games.values()),
           "log_loss": {k: round(sum(ll(r[k], r["y"]) for r in out) / len(out), 4) for k in ("M", "H", "S", "E")}, "names": {"M": "market (no-vig)", "H": "eggy-matchup-v1", "S": "goalie-team-v1 (strength)", "E": "Elo"}}
    sides = [(r["H"] - r["M"]) for r in out] + [-(r["H"] - r["M"]) for r in out]
    mean = sum(sides) / len(sides)
    res["disagreement_sd"] = round(math.sqrt(sum((s - mean) ** 2 for s in sides) / len(sides)), 4)
    # the paired, per-game difference in log loss (market less model): positive when the model was the better guide. Only a difference well beyond its standard error counts as the model leading.
    d = [ll(r["M"], r["y"]) - ll(r["H"], r["y"]) for r in out]
    d_mean = sum(d) / len(d)
    d_se = math.sqrt(sum((x - d_mean) ** 2 for x in d) / (len(d) - 1) / len(d)) if len(d) > 1 else None
    res["paired_difference"] = {"market_minus_model_log_loss": round(d_mean, 5), "standard_error": None if d_se is None else round(d_se, 5), "z": None if not d_se else round(d_mean / d_se, 2), "games": len(d),
                                "reading": "positive: the model was the better guide; the model counts as leading only when z is at least 2"}
    lg = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))
    grid = [i / 50.0 for i in range(-150, 151)]          # the weight on the disagreement, from -3 to +3: wide enough that the interval is never the grid's edge
    loss = {k: sum(ll(1 / (1 + math.exp(-(lg(r["M"]) + k * (lg(r["H"]) - lg(r["M"]))))), r["y"]) for r in out) for k in grid}
    best = min(loss, key=loss.get)
    ok = [k for k in grid if loss[k] - loss[best] <= 1.92]
    res["weight_on_disagreement"] = {"best": round(best, 2), "ci95": [round(min(ok), 2), round(max(ok), 2)], "grid": [grid[0], grid[-1]],
                                     "reading": "k=0 trusts the market, k=1 trusts the model; with this few games the interval is far too wide to say either"}
    log(f"  prospective: {res['games']} games, log loss {res['log_loss']}, k {res['weight_on_disagreement']}")
    return res


# ------------------------------------------------------------------------------------------ skater cells
P_BANDS = {"goals>=1": [0, .03, .045, .06, .08, .10, .12, .15, .20, .25, .35, 1.01], "points>=1": [0, .15, .20, .25, .30, .35, .40, .50, 1.01], "points>=2": [0, .04, .07, .10, .15, .25, 1.01],
           "shots>=2": [0, .1, .2, .3, .4, .5, .6, .7, .8, 1.01], "shots>=3": [0, .05, .1, .2, .3, .4, .5, .6, 1.01], "shots>=4": [0, .03, .06, .1, .15, .2, .3, .5, 1.01], "shots>=5": [0, .02, .04, .07, .1, .15, .25, 1.01]}
TOI_BANDS = [0, 12, 14, 16, 18, 20, 99]
PP_BANDS = [0, 0.25, 1.0, 2.0, 99]
ENV_BANDS = [0, .35, .45, .55, .65, 1.01]


def _band(edges, v):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return (lo, hi)
    return (edges[-2], edges[-1])


def build_skater(env_preds: dict, rows_team: list[dict], log=print, before: str | None = None) -> dict:
    """The skater calibration cells. `before` (an ISO date) keeps only games strictly before it: the replay of the recalibration out of time (deploy/replay_prop_calibration.py) trains on the early part."""
    from research.product_models import history, live, projections as P
    from research.product_models.validate import apply_platt, walk
    t = time.time()
    rows = history.skater_games()
    val = live.load_validation()
    cfg = P.ModelConfig(**{k: (tuple(v) if k == "opp_cap" else v) for k, v in val["chosen_config"].items()})
    walked = walk(cfg, (2025, 2026), rows, with_baselines=False)
    idx = {(r["player_id"], r["date"]): r for r in rows}
    alphas, platt = val["dispersion"], val["platt_params"]
    # the team model's probability for each (game, team) side, from the expanding-window fits; the current season uses coefficients fit through 2025 (the evidence's own)
    log(f"  skater walk {len(walked)} rows in {time.time() - t:.1f}s")
    cells: dict = {m: defaultdict(lambda: [0, 0.0, 0.0]) for m in P_BANDS}
    n_rows = 0
    for w in walked:
        r = idx.get((w["pid"], w["date"]))
        if r is None or (before is not None and w["date"] >= before):
            continue
        p_home = env_preds.get(str(r["game_id"]))
        team_p = None if p_home is None else (p_home if r["home"] else 1.0 - p_home)
        for market, edges in P_BANDS.items():
            stat, k = market.split(">=")
            k = int(k)
            a, b = platt[f"{stat}>={k}"]
            p = apply_platt(P.prob_at_least(k, w["mean"][stat], alphas[stat]), a, b)
            y = 1 if w["actual"][stat] >= k else 0
            pb = _band(edges, p)
            grp = w["grp"]
            keys = [("p", pb), ("grp", (grp,) + pb), ("toi", (grp,) + _band(TOI_BANDS, w["toi"]) + pb), ("pp", (grp,) + _band(PP_BANDS, w["toi_pp"]) + pb)]
            if team_p is not None:
                keys.append(("env", _band(ENV_BANDS, team_p) + pb))
            for dim, key in keys:
                c = cells[market][(dim,) + tuple(key)]
                c[0] += 1
                c[1] += p
                c[2] += y
        n_rows += 1
    out: dict = {"seasons": "2025-26 (held out after the Platt fit on 2024-25) plus 2026-27 to date", "rows": n_rows, "p_bands": P_BANDS, "toi_bands": TOI_BANDS, "pp_bands": PP_BANDS, "env_bands": ENV_BANDS, "cells": {}}
    for market, d in cells.items():
        lst = []
        for key, (n, sp, sy) in d.items():
            if n < 30:
                continue
            lst.append([key[0], *key[1:], n, round(sp / n, 5), round(sy / n, 5)])
        out["cells"][market] = lst
    log(f"  skater cells: {sum(len(v) for v in out['cells'].values())}")
    return out


def main() -> int:
    from research.product_models import matchup as M
    t0 = time.time()
    rows = M.team_games()
    if not rows:
        print("no MoneyPuck team file on disk: nothing to build")
        return 1
    print(f"team games on disk: {len(rows)} ({rows[0]['d']} to {rows[-1]['d']})")
    print("matchup model ...")
    matchup, env_preds = build_matchup(rows)
    print("skater calibration cells ...")
    skater = build_skater(env_preds, rows)
    doc = {"version": VERSION, "built_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "data_through": rows[-1]["d"], "matchup": matchup, "skater": skater,
           "notes": ["No historical sportsbook prices exist: nothing here is profit evidence.", "The MoneyPuck team file covers 2008-09 to the current season; the skater files cover 2022-23 to the current season."]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB) in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
