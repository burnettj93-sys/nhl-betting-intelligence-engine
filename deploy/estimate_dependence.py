"""
How strongly do the outcomes of different players move together? (Read-only; reads the skater game logs the model is built from.)

The postmortem's streak probability treats legs as independent except where tickets share a leg. This estimates how much that leaves out, from history rather than from a guess:

  * same game, opposite teams / same team / different games on the same date: the average correlation between two players' "surprises" (what happened minus what that player's own trailing
    30-game rate said to expect), for the markets the tickets use (points 1+, shots 2+, shots 3+, goals 1+);
  * the same player in two markets (his shots 2+ and his points 1+).

A surprise is (hit - p) / sqrt(p(1-p)) with p the player's own prior rate (at least 20 and at most 30 prior games), so a star and a fourth-liner are compared with themselves. These are approximations of the
dependence a model that already knows the player's rate cannot remove; they are NOT the model's own residuals (those are not stored for past games), and a game-level effect that the model captures
(an opponent that gives up shots) would be smaller still. The standard error comes from resampling whole games, because players in a game are not independent of each other.

Run: python3 deploy/estimate_dependence.py [--logs operational/runtime/product_skater_games.jsonl.gz] [--since 2024-10-01] --out docs/validation/dependence_estimates_2026-10-10.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MARKETS = {"points>=1": lambda r: r["points"] >= 1, "shots>=2": lambda r: r["shots"] >= 2, "shots>=3": lambda r: r["shots"] >= 3, "goals>=1": lambda r: r["goals"] >= 1}
MIN_PRIOR, WINDOW = 20, 30


def load(path: Path, since: str) -> list[dict]:
    rows = []
    with gzip.open(path, "rt") as f:
        for i, line in enumerate(f):
            if i == 0 and '"signature"' in line[:20]:
                continue
            r = json.loads(line)
            if r.get("pos") == "G" or r["toi"] < 8.0:
                continue
            rows.append(r)
    rows.sort(key=lambda r: (r["player_id"], r["date"], r["game_id"]))
    return rows


def surprises(rows: list[dict], since: str) -> list[dict]:
    """Each row gets, per market, a standardised surprise from the player's own prior rate. Rows before `since` only feed the trailing window."""
    out = []
    hist: dict = defaultdict(list)
    for r in rows:
        h = hist[r["player_id"]]
        if len(h) >= MIN_PRIOR and r["date"] >= since:
            recent = h[-WINDOW:]
            z = {}
            for m, f in MARKETS.items():
                p = sum(1 for x in recent if f(x)) / len(recent)
                p = min(max(p, 0.04), 0.96)
                z[m] = ((1.0 if f(r) else 0.0) - p) / math.sqrt(p * (1 - p))
            out.append({"game": r["game_id"], "date": r["date"], "team": r["team"], "player": r["player_id"], "z": z})
        h.append(r)
    return out


def pair_stat(rows: list[dict], market: str, kind: str) -> tuple[float, int, list]:
    """Average product of two players' surprises over pairs of the given kind, with per-cluster (game or date) sums for the resampling error."""
    clusters = []
    if kind in ("same_team", "opposite_teams"):
        by_game = defaultdict(list)
        for r in rows:
            by_game[r["game"]].append(r)
        for g, rs in by_game.items():
            s = n = 0.0
            by_team = defaultdict(list)
            for r in rs:
                by_team[r["team"]].append(r["z"][market])
            teams = list(by_team.values())
            if kind == "same_team":
                for t in teams:
                    tot, sq, k = sum(t), sum(x * x for x in t), len(t)
                    s += (tot * tot - sq) / 2
                    n += k * (k - 1) / 2
            elif len(teams) == 2:
                s += sum(teams[0]) * sum(teams[1])
                n += len(teams[0]) * len(teams[1])
            if n:
                clusters.append((s, n))
    elif kind == "different_games_same_date":
        by_date = defaultdict(lambda: defaultdict(list))
        for r in rows:
            by_date[r["date"]][r["game"]].append(r["z"][market])
        for d, games in by_date.items():
            allz = [x for g in games.values() for x in g]
            tot, sq = sum(allz), sum(x * x for x in allz)
            all_pairs = (tot * tot - sq) / 2
            within = sum((sum(g) ** 2 - sum(x * x for x in g)) / 2 for g in games.values())
            n_all = len(allz) * (len(allz) - 1) / 2
            n_within = sum(len(g) * (len(g) - 1) / 2 for g in games.values())
            if n_all - n_within > 0:
                clusters.append((all_pairs - within, n_all - n_within))
    total_s = sum(c[0] for c in clusters)
    total_n = sum(c[1] for c in clusters)
    return (total_s / total_n if total_n else float("nan")), int(total_n), clusters


def resample_se(clusters: list, reps: int = 300, seed: int = 20261010) -> float:
    rng = random.Random(seed)
    k = len(clusters)
    vals = []
    for _ in range(reps):
        s = n = 0.0
        for _ in range(k):
            c = clusters[rng.randrange(k)]
            s += c[0]
            n += c[1]
        vals.append(s / n if n else 0.0)
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def same_player_cross_market(rows: list[dict], a: str, b: str) -> dict:
    xs = [(r["z"][a], r["z"][b]) for r in rows]
    n = len(xs)
    ma, mb = sum(x for x, _ in xs) / n, sum(y for _, y in xs) / n
    cov = sum((x - ma) * (y - mb) for x, y in xs) / n
    va, vb = sum((x - ma) ** 2 for x, _ in xs) / n, sum((y - mb) ** 2 for _, y in xs) / n
    if va <= 0 or vb <= 0:
        return {"pairs": n, "correlation": None}
    return {"pairs": n, "correlation": round(cov / math.sqrt(va * vb), 4)}


def estimate(rows: list[dict], since: str) -> dict:
    z = surprises(rows, since)
    out = {"since": since, "player_games": len(z), "games": len({r["game"] for r in z}), "players_per_game": round(len(z) / max(len({r["game"] for r in z}), 1), 1),
           "method": "average product of standardised surprises (hit minus the player's own prior-30-game rate, over sqrt(p(1-p))) between two players; 0 = unrelated", "markets": {}}
    for m in MARKETS:
        res = {}
        for kind in ("opposite_teams", "same_team", "different_games_same_date"):
            val, n, clusters = pair_stat(z, m, kind)
            res[kind] = {"correlation": round(val, 4), "se": round(resample_se(clusters), 4), "pairs": n}
        out["markets"][m] = res
    out["same_player_two_markets"] = {"shots>=2 with points>=1": same_player_cross_market(z, "shots>=2", "points>=1"), "shots>=3 with points>=1": same_player_cross_market(z, "shots>=3", "points>=1"),
                                      "shots>=2 with goals>=1": same_player_cross_market(z, "shots>=2", "goals>=1")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=str(REPO / "operational" / "runtime" / "product_skater_games.jsonl.gz"))
    ap.add_argument("--since", default="2024-10-01")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rep = estimate(load(Path(a.logs), a.since), a.since)
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1))
    print(f"{rep['player_games']} player-games in {rep['games']} games since {a.since}")
    for m, v in rep["markets"].items():
        print(m.ljust(11), "  ".join(f"{k}: {x['correlation']:+.3f} (se {x['se']:.3f})" for k, x in v.items()))
    print({k: v["correlation"] for k, v in rep["same_player_two_markets"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
