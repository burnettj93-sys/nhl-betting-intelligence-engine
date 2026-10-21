"""
Team matchup model (version `eggy-matchup-v1`): who is the better hockey team tonight, by how much, and why.

It reads the MoneyPuck team game-by-game file that is ALREADY on disk (data/raw/moneypuck/team: every team-game from 2008-09 to today, with goals, expected goals, shot attempts, high-danger chances,
special-teams time) and the schedule. It needs no new data. Production strength (`goalie-team-v1`, research/product_models/team_goalie.py) uses four seasons of goalie logs and one signal, decayed goal
differential; this model adds what that data holds and the production model ignores, and every addition was chosen and tested chronologically (see docs/EGGY_INTELLIGENCE.md and
docs/validation/eggy_evidence.json, written by deploy/build_eggy_evidence.py):

  * four features, each the home team's value less the away team's, from state decayed over the team's recent games (half-life 30 games) and shrunk toward the league mean:
        gd   goal differential per game
        xgd  expected-goal differential per game (all situations)
        hd   high-danger expected-goal differential per game (it carries the opposite sign of xgd in the fit: the stable part of expected goals is the NON-high-danger part)
        b2b  back-to-back: 1 when the away team played yesterday and the home team did not, -1 for the reverse
  * a logistic fitted on every non-shootout game from 2009-10 to 2025-26 (seasons are labelled by their starting year in the evidence file: 2009 to 2025). The feature set was chosen on 2014-2016 only (the 30-game half-life and 10-game shrink are fixed constants chosen in exploratory work on those seasons); the final test (expanding window, each season predicted from the
    seasons before it, 2017-2025, 11,053 games) beat goal differential alone by 0.0054 log loss (95% interval entirely below zero) and beat the home-rate baseline by 0.0279.

What it is NOT: evidence of an edge over a sportsbook. No historical prices exist. On the 87 games priced since October 8 the market (log loss 0.6438) was slightly ahead of this model (0.6467);
the evidence file records that comparison and the interval is far too wide to conclude anything either way. The model is used to understand the matchup and to scrutinise disagreement with the
market, never as proof the market is wrong.
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import gzip
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
MODEL_VERSION = "eggy-matchup-v1"
TEAM_GLOB = str(REPO / "data" / "raw" / "moneypuck" / "team" / "2026" / "*_team_2026.csv")
EVIDENCE_PATH = REPO / "docs" / "validation" / "eggy_evidence.json"
CACHE_NAME = "matchup_team_games.jsonl.gz"
HALF_LIFE_GAMES = 30.0
SHRINK_GAMES = 10.0
MODEL_FEATURES = ("gd", "xgd", "hd", "b2b")
FIRST_FIT_SEASON = 2009                 # the first season only warms the state
# A franchise keeps its state across a rename or a move (MoneyPuck spelled four teams "N.J", "S.J", "T.B", "L.A" until 2020).
TEAM_ALIASES = {"N.J": "NJD", "S.J": "SJS", "T.B": "TBL", "L.A": "LAK", "ATL": "WPG", "ARI": "UTA", "PHX": "UTA"}

_NUMERIC = {"gf": ("all", "goalsFor"), "ga": ("all", "goalsAgainst"), "xgf": ("all", "xGoalsFor"), "xga": ("all", "xGoalsAgainst"), "hdf": ("all", "highDangerxGoalsFor"),
            "hda": ("all", "highDangerxGoalsAgainst"), "pnf": ("all", "penaltiesFor"), "pna": ("all", "penaltiesAgainst"), "cf5": ("5on5", "shotAttemptsFor"), "ca5": ("5on5", "shotAttemptsAgainst"),
            "xgf5": ("5on5", "xGoalsFor"), "xga5": ("5on5", "xGoalsAgainst"), "ppt": ("5on4", "iceTime"), "ppx": ("5on4", "xGoalsFor"), "pkt": ("4on5", "iceTime"), "pkx": ("4on5", "xGoalsAgainst")}


def alias(team: str) -> str:
    return TEAM_ALIASES.get(team, team)


# ------------------------------------------------------------------------------------------------ data
def _source_file() -> str | None:
    files = sorted(glob.glob(TEAM_GLOB))
    return files[-1] if files else None


def _iso(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _parse_csv(path: str) -> list[dict]:
    games: dict[tuple, dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sit = r["situation"]
            if sit not in ("all", "5on5", "5on4", "4on5"):
                continue
            key = (r["gameId"], r["team"])
            g = games.get(key)
            if g is None:
                g = games[key] = {"g": r["gameId"], "s": int(r["season"]), "d": _iso(r["gameDate"]), "t": alias(r["team"]), "o": alias(r["opposingTeam"]), "h": r["home_or_away"] == "HOME"}
            for short, (want, col) in _NUMERIC.items():
                if sit == want:
                    try:
                        g[short] = float(r[col] or 0.0)
                    except (ValueError, KeyError):
                        g[short] = 0.0
    out = [g for g in games.values() if "gf" in g and "ga" in g]
    out.sort(key=lambda g: (g["d"], g["g"], g["t"]))
    return out


def team_games(cache_dir: Path | None = None) -> list[dict]:
    """Every team-game on disk, compact, in date order. A gzip cache keyed by the source file's size and time spares re-reading the 120 MB CSV."""
    from operational import state_paths
    path = _source_file()
    if path is None:
        return []
    signature = json.dumps([path, os.path.getsize(path), int(os.path.getmtime(path))])
    cache = (cache_dir / CACHE_NAME) if cache_dir is not None else state_paths.path(CACHE_NAME)
    if cache.exists():
        try:
            with gzip.open(cache, "rt") as fh:
                if json.loads(fh.readline()).get("signature") == signature:
                    return [json.loads(line) for line in fh]
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    rows = _parse_csv(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        fh.write(json.dumps({"signature": signature}) + "\n")
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    tmp.replace(cache)
    return rows


# ------------------------------------------------------------------------------------------------ state
_METRICS = {
    "gd": lambda r: r["gf"] - r["ga"],
    "xgd": lambda r: r["xgf"] - r["xga"],
    "hd": lambda r: r["hdf"] - r["hda"],
    # supporting indicators (shown, not in the fitted model)
    "xgd5": lambda r: r["xgf5"] - r["xga5"],
    "cf5": lambda r: (r["cf5"] - r["ca5"]) / max(r["cf5"] + r["ca5"], 1.0),
    "pp": lambda r: (r["ppx"] / r["ppt"] * 3600.0) if r.get("ppt", 0) > 30 else None,
    "pk": lambda r: (r["pkx"] / r["pkt"] * 3600.0) if r.get("pkt", 0) > 30 else None,
    "fin": lambda r: r["gf"] - r["xgf"],
    "pen": lambda r: r["pna"] - r["pnf"],
}


class State:
    """Decayed per-team state, advanced one team-game at a time in date order."""

    def __init__(self, half_life: float = HALF_LIFE_GAMES, shrink: float = SHRINK_GAMES):
        self.lam = 0.5 ** (1.0 / half_life)
        self.k = shrink
        self.acc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        self.league = {m: [0.0, 0.0] for m in _METRICS}
        self.last: dict[str, str] = {}
        self.played: dict[str, int] = defaultdict(int)
        self.recent: dict[str, list] = defaultdict(list)             # (date, opp, gf, ga, home) of the last ten games
        self.newest = ""

    def update(self, r: dict) -> None:
        for m, fn in _METRICS.items():
            try:
                v = fn(r)
            except KeyError:
                v = None
            if v is None:
                continue
            a = self.acc[r["t"]][m]
            a[0] = self.lam * a[0] + v
            a[1] = self.lam * a[1] + 1.0
            self.league[m][0] += v
            self.league[m][1] += 1.0
        self.last[r["t"]] = r["d"]
        self.played[r["t"]] += 1
        self.recent[r["t"]] = (self.recent[r["t"]] + [(r["d"], r["o"], r["gf"], r["ga"], r["h"])])[-10:]
        self.newest = max(self.newest, r["d"])

    def est(self, team: str, metric: str) -> float:
        lm = self.league[metric][0] / self.league[metric][1] if self.league[metric][1] else 0.0
        s, n = self.acc[team][metric] if team in self.acc and metric in self.acc[team] else (0.0, 0.0)
        return (s + self.k * lm) / (n + self.k)

    def rest_days(self, team: str, date: str) -> int:
        last = self.last.get(team)
        if not last:
            return 7
        return (dt.date.fromisoformat(date) - dt.date.fromisoformat(last)).days


def build_state(rows: list[dict], through_date: str | None = None) -> State:
    """The state after every team-game dated strictly before `through_date` (all of them when None). The strictness is what makes an as-of replay honest."""
    st = State()
    for r in rows:
        if through_date is not None and r["d"] >= through_date:
            break
        st.update(r)
    return st


def features(st: State, home: str, away: str, date: str) -> dict:
    f = {m: st.est(home, m) - st.est(away, m) for m in ("gd", "xgd", "hd")}
    rh, ra = st.rest_days(home, date), st.rest_days(away, date)
    f["b2b"] = float(ra <= 1) - float(rh <= 1)
    f["_rest_home"], f["_rest_away"] = rh, ra
    return f


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def home_probability(coefs: dict, feats: dict) -> tuple[float, dict]:
    """(P(home wins), the log-odds each term contributes). `coefs` = {"const", "gd", "xgd", "hd", "b2b"}."""
    contrib = {"home_ice": coefs["const"]}
    for name in MODEL_FEATURES:
        contrib[name] = coefs[name] * feats[name]
    return sigmoid(sum(contrib.values())), contrib


# ------------------------------------------------------------------------------------------------ evidence
def load_evidence(path: Path = EVIDENCE_PATH) -> dict | None:
    """The evidence file, with `_sha256` (the hash of its exact bytes, not stored in the file) so that a review can name the evidence it stood on and the cohort's policy digest can pin it."""
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError):
        return None
    doc["_sha256"] = hashlib.sha256(raw).hexdigest()
    return doc


def evidence_sha256(path: Path = EVIDENCE_PATH) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _rank(st: State, metric: str, teams: list[str], reverse: bool = True) -> dict[str, int]:
    vals = sorted(teams, key=lambda t: st.est(t, metric), reverse=reverse)
    return {t: i + 1 for i, t in enumerate(vals)}


def _label_gap(p_fav: float, pct: dict | None) -> str:
    """How big the gap is, in words, from where this matchup sits among every matchup in the test seasons (the percentiles are in the evidence file)."""
    if not pct:
        return "UNRATED"
    if p_fav >= pct["p95"]:
        return "DOMINANT"
    if p_fav >= pct["p85"]:
        return "STRONG"
    if p_fav >= pct["p65"]:
        return "CLEAR"
    if p_fav >= pct["p35"]:
        return "SLIGHT"
    return "NEGLIGIBLE"


def view(st: State, evidence: dict | None, home: str, away: str, date: str) -> dict:
    """The matchup, synthesised: probability, favourite, the size of the gap, what drives it (log-odds by feature), every indicator for both teams with its league rank, rest, and the data it stands on."""
    teams = [t for t in st.acc if st.played[t] > 0]
    ev = (evidence or {}).get("matchup") or {}
    coefs = ev.get("coefficients")
    out = {"model": MODEL_VERSION, "home": home, "away": away, "date": date, "available": False, "data_through": st.newest, "games_in_state": {home: st.played.get(home, 0), away: st.played.get(away, 0)}}
    if not coefs or home not in st.acc or away not in st.acc:
        out["reason"] = "no fitted coefficients on file (run deploy/build_eggy_evidence.py)" if not coefs else "a team has no games in the team table"
        return out
    f = features(st, home, away, date)
    p_home, contrib = home_probability(coefs, f)
    fav, p_fav = (home, p_home) if p_home >= 0.5 else (away, 1.0 - p_home)
    ranks = {m: _rank(st, m, teams, reverse=(m not in ("pk", "pen"))) for m in ("gd", "xgd", "xgd5", "cf5", "pp", "pk", "fin", "pen")}
    ind = {}
    for t in (home, away):
        ind[t] = {m: round(st.est(t, m), 3) for m in ("gd", "xgd", "xgd5", "cf5", "pp", "pk", "fin", "pen")}
        ind[t]["ranks"] = {m: ranks[m][t] for m in ranks}
        ind[t]["games"] = st.played.get(t, 0)
        ind[t]["rest_days"] = st.rest_days(t, date)
        ind[t]["last10"] = [{"date": d, "opp": o, "gf": int(gf), "ga": int(ga), "home": hm} for d, o, gf, ga, hm in st.recent.get(t, [])[-5:]]
    drivers = []
    names = {"home_ice": "home ice", "gd": "goal differential (decayed)", "xgd": "expected-goal differential", "hd": "high-danger chances (offsets xGD)", "b2b": "back-to-back"}
    for k in ("home_ice", "gd", "xgd", "hd", "b2b"):
        v = contrib[k]
        drivers.append({"feature": k, "label": names[k], "log_odds_toward_home": round(v, 4), "favours": (home if v > 0 else away) if abs(v) > 1e-9 else None})
    # the two expected-goal terms are collinear; their net is the stable story
    xg_net = contrib["xgd"] + contrib["hd"]
    out.update({"available": True, "p_home": round(p_home, 4), "p_away": round(1 - p_home, 4), "favourite": fav, "p_favourite": round(p_fav, 4), "gap": _label_gap(p_fav, ev.get("favourite_percentiles")),
                "features": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()}, "drivers": drivers, "xg_net_log_odds_toward_home": round(xg_net, 4), "indicators": ind,
                "rest": {home: st.rest_days(home, date), away: st.rest_days(away, date)}, "version": ev.get("version")})
    # who the (validated) model features favour, versus the supporting indicators that are not in the model
    votes = {home: 0, away: 0}
    for k in ("gd", "xg_net"):
        v = contrib["gd"] if k == "gd" else xg_net
        if abs(v) > 0.02:
            votes[home if v > 0 else away] += 1
    for m in ("cf5", "xgd5"):
        d = st.est(home, m) - st.est(away, m)
        sd = ((ev.get("indicator_sd") or {}).get(m)) or 0.0
        if sd and abs(d) > 0.25 * sd:
            votes[home if d > 0 else away] += 1
    out["indicator_votes"] = votes
    return out


def analogues(evidence: dict | None, p_side: float) -> dict | None:
    """Historical test games where the model gave a side about this probability: how often that side won. `reliability_side` is built from the 2017-2025 expanding-window predictions."""
    rel = ((evidence or {}).get("matchup") or {}).get("reliability_side")
    if not rel:
        return None
    for b in rel:
        if b["lo"] <= p_side < b["hi"]:
            return {"band": f"{b['lo']:.2f}-{min(b['hi'], 1.0):.2f}", "n": b["n"], "predicted": b["pred"], "observed": b["obs"], "standard_error": b["se"], "seasons": ((evidence["matchup"].get("evaluation") or {}).get("test_seasons"))}
    return None
