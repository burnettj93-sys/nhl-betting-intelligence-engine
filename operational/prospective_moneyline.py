"""The prospective record of the team model against the market: does the engine's hockey probability deserve trust where it disagrees with the sportsbook? Measured on every finished game the engine priced.

For each game the moneyline shadow log priced (operational/moneyline_model_path.py: the no-vig market probability, Elo and the strength model at decision time) this adds the hockey model's pregame probability
(the team model every review uses, its state built from games strictly BEFORE the date) and the result, once, into an append-only ledger (`prospective_moneyline.jsonl`). From that ledger:

  * home-side log loss of the market, the hockey model, the strength model and Elo, and the paired difference (market minus hockey) with its z;
  * every side by how far the hockey model sat from the market (below by 6+, 3 to 6, within 3, above by 3 to 6, above by 6+): how often the side won against what the MARKET said, with n and a z;
  * a TRUST STATE per gap band and overall: MARKET_PROBABLY_BETTER, DISAGREEMENT_HAS_SIGNAL, DISAGREEMENT_IS_OVERCONFIDENCE, MODEL_AND_MARKET_AGREE, or INSUFFICIENT_EVIDENCE.

Nothing is retrained and no policy changes by itself: the state is read by the review (decision layer D) and published in the measured record, so that trust in a disagreement is earned by evidence as the sample grows.
"""
from __future__ import annotations

import json
import math

from operational import state_paths

LEDGER_NAME = "prospective_moneyline.jsonl"
MIN_GAMES = 100                      # an overall verdict needs this many finished games
MIN_GAMES_PER_BAND = 100             # a gap band needs this many finished GAMES (independent observations; the threshold is unchanged, the count is now of games, not of mirrored sides)
MAX_NEW_DATES_PER_CALL = 4           # building a team state costs seconds: a long backlog is worked off over several calls
Z = 2.0
# INDEPENDENT OBSERVATIONS. One game is ONE observation: its two sides are the same event seen from both ends (when the home side wins the away side loses), so counting both sides would double the apparent sample
# and report the same fact twice in a band and its mirror. Each game is oriented to the side the model likes MORE than the market (the side whose gap is positive) and falls in one band by how much more.
BANDS = (("within 3 pts of the market", 0.0, 0.03), ("model likes a side 3 to 6 pts more than the market", 0.03, 0.06), ("model likes a side 6+ pts more than the market", 0.06, 9.0))


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _path():
    return state_paths.path(LEDGER_NAME)


def read_ledger() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def refresh(nhl_conn, read_log=None, team_rows=None, evidence=None) -> dict:
    """Add every newly finished priced game to the ledger (idempotent: a game is recorded once). Returns {'added', 'total', 'pending_dates'}."""
    from operational import moneyline_model_path as mmp
    from research.product_models import matchup as M
    shadow = (read_log or mmp._read_log)()
    have = {r["game_id"] for r in read_ledger()}
    games: dict = {}
    for r in shadow:
        g = games.setdefault(str(r["game_id"]), {"home": r["home"], "away": r["away"], "date": r["game_date"], "sides": {}})
        g["sides"][r["selection"]] = r                                     # the latest decision-time row for each side
    todo = {}
    for gid, g in games.items():
        if gid in have or len(g["sides"]) != 2:
            continue
        gm = nhl_conn.execute("SELECT game_state, home_score, away_score FROM games WHERE game_id = ?", (int(gid),)).fetchone() if nhl_conn is not None else None
        if gm is None or gm["game_state"] != "FINAL" or gm["home_score"] == gm["away_score"]:
            continue
        todo.setdefault(g["date"], []).append((gid, g, gm))
    added, rows = 0, team_rows
    ev = evidence if evidence is not None else M.load_evidence()
    dates = sorted(todo)[:MAX_NEW_DATES_PER_CALL]
    if dates and rows is None:
        rows = M.team_games()
    new = []
    for d in dates:
        st = M.build_state(rows, through_date=d)
        for gid, g, gm in todo[d]:
            v = M.view(st, ev, g["home"], g["away"], d)
            h, a = g["sides"].get(g["home"]), g["sides"].get(g["away"])
            if not (v and v.get("available")) or h is None or a is None or h.get("market_no_vig") is None:
                continue
            new.append({"game_id": gid, "date": d, "home": g["home"], "away": g["away"], "hockey_home": round(v["p_home"], 5), "market_home": round(float(h["market_no_vig"]), 5),
                        "elo_home": h.get("elo"), "strength_home": h.get("strength"), "home_won": int(gm["home_score"] > gm["away_score"])})
            added += 1
    if new:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            for r in new:
                fh.write(json.dumps(r) + "\n")
    return {"added": added, "total": len(have) + added, "pending_dates": max(len(todo) - len(dates), 0)}


def summarise(rows: list[dict]) -> dict:
    """Pure: the measured record and the trust states from ledger rows."""
    n = len(rows)
    out: dict = {"games": n, "min_games_for_a_verdict": MIN_GAMES}
    if not n:
        out["state"] = "INSUFFICIENT_EVIDENCE"
        return out
    ll = {k: sum(_ll(r[f"{k}_home"], r["home_won"]) for r in rows if r.get(f"{k}_home") is not None) / max(1, sum(1 for r in rows if r.get(f"{k}_home") is not None))
          for k in ("market", "hockey", "strength", "elo")}
    d = [_ll(r["market_home"], r["home_won"]) - _ll(r["hockey_home"], r["home_won"]) for r in rows]          # positive: the hockey model beat the market on that game
    m = sum(d) / n
    se = math.sqrt(max(sum((x - m) ** 2 for x in d) / n, 1e-12) / n)
    z = m / se
    out.update({"home_side_log_loss": {k: round(v, 4) for k, v in ll.items()}, "paired_market_minus_hockey_log_loss": round(m, 5), "se": round(se, 5), "z": round(z, 2),
                "home_win_rate": round(sum(r["home_won"] for r in rows) / n, 3)})
    bands = {}
    for name, lo, hi in BANDS:
        sides = []                                                            # one entry per GAME: the side the model likes more than the market
        for r in rows:
            gap = r["hockey_home"] - r["market_home"]
            p_h, p_m, y = (r["hockey_home"], r["market_home"], r["home_won"]) if gap >= 0 else (1 - r["hockey_home"], 1 - r["market_home"], 1 - r["home_won"])
            if lo <= abs(gap) < hi:
                sides.append((p_h, p_m, y))
        k = len(sides)
        if not k:
            bands[name] = {"games": 0, "state": "INSUFFICIENT_EVIDENCE"}
            continue
        var = sum(p_m * (1 - p_m) for _, p_m, _ in sides)
        zz = (sum(y for *_, y in sides) - sum(p_m for _, p_m, _ in sides)) / math.sqrt(var) if var else None
        zh = (sum(y for *_, y in sides) - sum(p_h for p_h, _, _ in sides)) / math.sqrt(sum(p_h * (1 - p_h) for p_h, _, _ in sides)) if sides else None
        if lo == 0.0:
            state = "MODEL_AND_MARKET_AGREE"
        elif k < MIN_GAMES_PER_BAND or zz is None or zh is None:
            state = "INSUFFICIENT_EVIDENCE"
        elif zz >= Z:
            state = "DISAGREEMENT_HAS_SIGNAL"                                  # the side the model liked more won MORE than the market said
        elif zh <= -Z:
            state = "DISAGREEMENT_IS_OVERCONFIDENCE"                           # the model's number was not borne out: the side won about as often as the market said, far less than the model said
        else:
            state = "INSUFFICIENT_EVIDENCE"
        bands[name] = {"games": k, "games_needed": MIN_GAMES_PER_BAND, "mean_hockey": round(sum(p_h for p_h, *_ in sides) / k, 4), "mean_market": round(sum(p_m for _, p_m, _ in sides) / k, 4),
                       "observed_win_rate": round(sum(y for *_, y in sides) / k, 4), "z_wins_vs_market_expectation": None if zz is None else round(zz, 2),
                       "z_wins_vs_hockey_expectation": None if zh is None else round(zh, 2), "state": state}
    out["by_gap_band"] = bands
    if n < MIN_GAMES:
        out["state"] = "INSUFFICIENT_EVIDENCE"
    elif z <= -Z:
        out["state"] = "MARKET_PROBABLY_BETTER"
    elif z >= Z:
        out["state"] = "MODEL_MAY_HAVE_EDGE"
    else:
        out["state"] = "MODEL_AND_MARKET_NOT_DISTINGUISHABLE"
    out["reading"] = ("positive z: the hockey model's pregame probabilities beat the market's no-vig probabilities on log loss; the model counts as leading only at z >= 2 over " + str(MIN_GAMES)
                      + "+ games; until then the market's probability is the estimate and the model's disagreement is shown, not trusted")
    return out


def band_state(summary: dict | None, gap: float) -> str:
    """The trust state for a side whose hockey probability is `gap` above (+) or below (-) the market's. A side the model likes LESS is the other end of the same game: its state is the band's state."""
    for name, lo, hi in BANDS:
        if lo <= abs(gap) < hi:
            return ((summary or {}).get("by_gap_band") or {}).get(name, {}).get("state", "INSUFFICIENT_EVIDENCE")
    return "INSUFFICIENT_EVIDENCE"
