"""
Goalie and team-strength model (version `goalie-team-v1`), built to be tested chronologically.

From the observed goalie game logs (MoneyPuck, four completed seasons plus the current season to date) it derives
  * team strength: decayed goal differential per game, shrunk toward zero;
  * goalie quality: decayed save percentage over shots faced, shrunk toward the league rate;
  * shot environment: the shots a team's goalie faces = team's decayed shots allowed blended with the opponent's decayed
    shots taken, relative to the league average;
  * expected saves / goals against for a goalie in a named matchup, as a negative-binomial count around
    expected-shots x save-percentage, with an 80% range;
  * the home team's win probability, with and without the named goalies, from a logistic fitted on a separate
    calibration season;
  * a start-likelihood estimate (how often the goalie starts given recent usage and rest) -- an ESTIMATE of usage, never
    a confirmation.

A game result is the sum of each team's goalie goals against; shootout games (8% of games, level after overtime) have no
winner in this source, so they are left out of the win-probability fit and score, and the report says so.

Splits (declared before looking at any result): train = 2022-23 seasons (state warm-up and dispersion), calibration =
2024-25 (logistic / Platt fits), final evaluation = 2025-26 (scored once, never fitted to).
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import history
from .projections import nb_pmf

MODEL_VERSION = "goalie-team-v1"
REPO = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO / "docs" / "validation" / "goalie_team_validation.json"

TRAIN_SEASONS, CALIB_SEASON, FINAL_SEASON = (2022, 2023), 2024, 2025
SAVE_THRESHOLDS = tuple(range(20, 36))
SV_PRIOR_SHOTS = 600.0          # pseudo-shots at the league save rate
SV_HALF_LIFE_SHOTS = 1500.0     # weight of shots fades by this many shots faced
STRENGTH_HALF_LIFE_GAMES = 25.0
STRENGTH_K_GAMES = 10.0
SHOT_HALF_LIFE_GAMES = 30.0
SHOT_K_GAMES = 8.0
MIN_STARTER_TOI = 30.0          # minutes: a goalie who played at least this long is treated as having started


def _clip(p: float, lo: float = 1e-4) -> float:
    return min(1 - lo, max(lo, p))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class _Goalie:
    shots: float = 0.0          # decayed shots faced
    saves: float = 0.0
    starts: int = 0
    games: int = 0
    team: str = ""
    name: str = ""
    last_date: str = ""
    recent: list = field(default_factory=list)        # (date, opp, sa, saves, ga, started) most recent last
    recent_saves: list = field(default_factory=list)


class TeamGoalieState:
    """Walk-forward state. `consume_day()` takes one date's finished games after they have been predicted."""

    def __init__(self):
        self.goalies: dict[str, _Goalie] = {}
        self.league_shots = 0.0
        self.league_saves = 0.0
        self.league_games = 0.0       # team-games
        self.strength: dict[str, list] = defaultdict(lambda: [0.0, 0.0])        # [decayed goal diff, decayed games]
        self.sa_allowed: dict[str, list] = defaultdict(lambda: [0.0, 0.0])      # [decayed shots faced, decayed games]
        self.sa_taken: dict[str, list] = defaultdict(lambda: [0.0, 0.0])
        self.last_game: dict[str, str] = {}
        self.team_starters: dict[str, list] = defaultdict(list)                 # recent (date, goalie_id) per team
        self._lam_s = 0.5 ** (1.0 / STRENGTH_HALF_LIFE_GAMES)
        self._lam_sh = 0.5 ** (1.0 / SHOT_HALF_LIFE_GAMES)
        self._lam_sv = 0.5 ** (1.0 / SV_HALF_LIFE_SHOTS)

    # ---------------------------------------------------------------- updates
    def consume_day(self, rows: list[dict]) -> None:
        games: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            games[r["game_id"]].append(r)
        for gid, grs in games.items():
            by_team: dict[str, list[dict]] = defaultdict(list)
            for r in grs:
                by_team[r["team"]].append(r)
            if len(by_team) != 2:
                continue
            totals = {t: {"ga": sum(x["goals_against"] for x in v), "sa": sum(x["shots_against"] for x in v)}
                      for t, v in by_team.items()}
            for team, v in by_team.items():
                opp = v[0]["opp"]
                gf, ga = totals[opp]["ga"], totals[team]["ga"]
                st = self.strength[team]
                st[0] = self._lam_s * st[0] + (gf - ga)
                st[1] = self._lam_s * st[1] + 1.0
                a = self.sa_allowed[team]
                a[0] = self._lam_sh * a[0] + totals[team]["sa"]
                a[1] = self._lam_sh * a[1] + 1.0
                t = self.sa_taken[team]
                t[0] = self._lam_sh * t[0] + totals[opp]["sa"]
                t[1] = self._lam_sh * t[1] + 1.0
                self.league_shots += totals[team]["sa"]
                self.league_games += 1.0
                self.last_game[team] = v[0]["date"]
                starter = max(v, key=lambda x: x["toi"])
                self.team_starters[team] = (self.team_starters[team] + [(v[0]["date"], starter["player_id"])])[-12:]
                for x in v:
                    g = self.goalies.get(x["player_id"])
                    if g is None:
                        g = self.goalies[x["player_id"]] = _Goalie()
                    saves = x["shots_against"] - x["goals_against"]
                    decay = self._lam_sv ** x["shots_against"]
                    g.shots = g.shots * decay + x["shots_against"]
                    g.saves = g.saves * decay + saves
                    g.games += 1
                    started = x["player_id"] == starter["player_id"] and x["toi"] >= MIN_STARTER_TOI
                    g.starts += 1 if started else 0
                    g.team, g.name, g.last_date = team, x["name"], x["date"]
                    g.recent = (g.recent + [(x["date"], opp, x["shots_against"], saves, x["goals_against"], started, x["toi"])])[-10:]
                    self.league_saves += saves

    # --------------------------------------------------------------- features
    @property
    def league_sv(self) -> float:
        return self.league_saves / self.league_shots if self.league_shots else 0.905

    @property
    def league_sa(self) -> float:
        return self.league_shots / self.league_games if self.league_games else 29.0

    def goalie_sv(self, goalie_id: str | None) -> float:
        lg = self.league_sv
        g = self.goalies.get(goalie_id) if goalie_id else None
        if g is None:
            return lg
        return (g.saves + SV_PRIOR_SHOTS * lg) / (g.shots + SV_PRIOR_SHOTS)

    def team_strength(self, team: str) -> float:
        s, n = self.strength[team] if team in self.strength else (0.0, 0.0)
        return s / (n + STRENGTH_K_GAMES)

    def expected_shots_faced(self, team: str, opp: str) -> float:
        """Shots the `team`'s goalie faces against `opp`."""
        L = self.league_sa
        k = SHOT_K_GAMES
        a_sum, a_n = self.sa_allowed[team] if team in self.sa_allowed else (0.0, 0.0)
        t_sum, t_n = self.sa_taken[opp] if opp in self.sa_taken else (0.0, 0.0)
        allowed = (a_sum + k * L) / (a_n + k)
        taken = (t_sum + k * L) / (t_n + k)
        return max(15.0, allowed + taken - L)

    def saved_goals_per_game(self, goalie_id: str | None, team: str, opp: str) -> float:
        """Goals a goalie is expected to save per game relative to a league-average goalie facing the same shots."""
        return (self.goalie_sv(goalie_id) - self.league_sv) * self.expected_shots_faced(team, opp)

    def rest_days(self, team: str, date: str) -> int | None:
        last = self.last_game.get(team)
        if not last:
            return None
        import datetime as dt
        return (dt.date.fromisoformat(date) - dt.date.fromisoformat(last)).days

    def goalie_projection(self, goalie_id: str, team: str, opp: str, alpha_saves: float) -> dict:
        sa = self.expected_shots_faced(team, opp)
        sv = self.goalie_sv(goalie_id)
        mean_saves = sa * sv
        return {"expected_shots_against": sa, "save_pct": sv, "expected_saves": mean_saves,
                "expected_goals_against": sa * (1 - sv), "alpha": alpha_saves}

    def start_features(self, team: str, goalie_id: str, date: str) -> dict:
        recent = self.team_starters.get(team, [])
        last10 = recent[-10:]
        share = (sum(1 for _, gid in last10 if gid == goalie_id) / len(last10)) if last10 else 0.0
        rest = self.rest_days(team, date)
        prev_started = bool(recent) and recent[-1][1] == goalie_id
        return {"share_last10": share, "back_to_back": rest == 1, "prev_started": prev_started, "n_recent": len(last10)}


# ------------------------------------------------------------------ distributions

def saves_quantile_range(mean: float, alpha: float, lo_q: float = 0.1, hi_q: float = 0.9) -> tuple[int, int]:
    cum, lo, hi = 0.0, None, None
    for k in range(0, 80):
        cum += nb_pmf(k, mean, alpha)
        if lo is None and cum >= lo_q:
            lo = k
        if hi is None and cum >= hi_q:
            hi = k
            break
    return lo or 0, hi or 0


def prob_saves_at_least(k: int, mean: float, alpha: float) -> float:
    return max(0.0, min(1.0, 1.0 - sum(nb_pmf(i, mean, alpha) for i in range(k))))


def fit_alpha(pairs: list[tuple[float, float]]) -> float:
    num = den = 0.0
    for m, a in pairs:
        num += (a - m) ** 2 - m
        den += m * m
    return max(0.0, num / den) if den else 0.0


def fit_logistic(rows: list[tuple[list[float], int]], iters: int = 60, ridge: float = 1e-3) -> list[float]:
    """Newton-Raphson logistic regression; the first feature should be a constant 1."""
    d = len(rows[0][0])
    w = [0.0] * d
    for _ in range(iters):
        g = [0.0] * d
        h = [[0.0] * d for _ in range(d)]
        for x, y in rows:
            p = _sigmoid(sum(wi * xi for wi, xi in zip(w, x)))
            for i in range(d):
                g[i] += (p - y) * x[i]
                for j in range(d):
                    h[i][j] += p * (1 - p) * x[i] * x[j]
        for i in range(d):
            g[i] += ridge * w[i]
            h[i][i] += ridge
        step = _solve(h, g)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def fit_platt(pairs: list[tuple[float, int]]) -> tuple[float, float]:
    w = fit_logistic([([1.0, _logit(p)], y) for p, y in pairs])
    return w[1], w[0]


def apply_platt(p: float, a: float, b: float) -> float:
    return _sigmoid(a * _logit(p) + b)


def metrics(pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    if not n:
        return {"n": 0}
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ll = -sum(math.log(_clip(p) if y else _clip(1 - p)) for p, y in pairs) / n
    return {"n": n, "base_rate": round(sum(y for _, y in pairs) / n, 4), "brier": round(brier, 5),
            "log_loss": round(ll, 5), "mean_pred": round(sum(p for p, _ in pairs) / n, 4)}


def calibration_table(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    out = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(p, y) for p, y in pairs if lo <= p < hi or (i == bins - 1 and p == 1.0)]
        if len(sel) >= 20:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "mean_pred": round(sum(p for p, _ in sel) / len(sel), 4),
                        "observed": round(sum(y for _, y in sel) / len(sel), 4)})
    return out


# ------------------------------------------------------------------ the walk

def _by_date(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        days[r["date"]].append(r)
    return sorted(days.items())


def walk(rows: list[dict], seasons: tuple[int, ...]) -> tuple[list[dict], list[dict], TeamGoalieState]:
    """Walks all rows in date order; before consuming a day, records features for the seasons asked about.
    Returns (goalie_records, game_records, final_state)."""
    state = TeamGoalieState()
    goalie_recs, game_recs = [], []
    for date, day in _by_date(rows):
        games: dict[int, list[dict]] = defaultdict(list)
        for r in day:
            games[r["game_id"]].append(r)
        for gid, grs in games.items():
            if grs[0]["season"] not in seasons:
                continue
            by_team: dict[str, list[dict]] = defaultdict(list)
            for r in grs:
                by_team[r["team"]].append(r)
            if len(by_team) != 2:
                continue
            totals = {t: sum(x["goals_against"] for x in v) for t, v in by_team.items()}
            starters = {t: max(v, key=lambda x: x["toi"]) for t, v in by_team.items()}
            home = next((t for t, v in by_team.items() if v[0]["home"]), None)
            if home is None:
                continue
            away = next(t for t in by_team if t != home)
            for team, opp in ((home, away), (away, home)):
                s = starters[team]
                if s["toi"] < MIN_STARTER_TOI:
                    continue
                sa = state.expected_shots_faced(team, opp)
                sv = state.goalie_sv(s["player_id"])
                prior_games = state.goalies[s["player_id"]].games if s["player_id"] in state.goalies else 0
                g = state.goalies.get(s["player_id"])
                recent_saves = [x[3] for x in (g.recent if g else []) if x[5]][-10:]
                goalie_recs.append({
                    "game_id": gid, "date": date, "player_id": s["player_id"], "team": team, "opp": opp,
                    "exp_sa": sa, "sv": sv, "mean_saves": sa * sv, "exp_ga": sa * (1 - sv),
                    "actual_saves": s["shots_against"] - s["goals_against"], "actual_sa": s["shots_against"],
                    "actual_ga": s["goals_against"], "prior_games": prior_games,
                    "recent_saves_mean": (sum(recent_saves) / len(recent_saves)) if len(recent_saves) >= 3 else None,
                    "season": s["season"],
                    "start_features": state.start_features(team, s["player_id"], date)})
            hs, as_ = starters[home], starters[away]
            sg_h = state.saved_goals_per_game(hs["player_id"], home, away)
            sg_a = state.saved_goals_per_game(as_["player_id"], away, home)
            # the same quantity for the team's usual goalie is unknowable without a lineup; use league-average goalie
            diff = state.team_strength(home) - state.team_strength(away)
            winner = None if totals[home] == totals[away] else (home if totals[away] > totals[home] else away)   # totals = goals AGAINST
            game_recs.append({"game_id": gid, "date": date, "season": grs[0]["season"], "home": home, "away": away,
                              "strength_diff": diff, "goalie_diff": sg_h - sg_a,
                              "home_win": None if winner is None else int(winner == home),
                              "shootout": winner is None})
        state.consume_day(day)
    return goalie_recs, game_recs, state


def run() -> dict:
    t0 = time.time()
    rows = history.goalie_games()
    seasons_all = TRAIN_SEASONS + (CALIB_SEASON, FINAL_SEASON)
    grec, gmrec, _ = walk(rows, seasons_all)
    train_g = [r for r in grec if r["season"] in TRAIN_SEASONS and r["prior_games"] >= 0]
    alpha = fit_alpha([(r["mean_saves"], r["actual_saves"]) for r in train_g])
    calib_g = [r for r in grec if r["season"] == CALIB_SEASON]
    final_g = [r for r in grec if r["season"] == FINAL_SEASON]

    def probs(recs, a=alpha):
        return [{**r, "p": {k: prob_saves_at_least(k, r["mean_saves"], a) for k in SAVE_THRESHOLDS}} for r in recs]

    cal_p = probs(calib_g)
    platt = {}
    for k in SAVE_THRESHOLDS:
        pairs = [(r["p"][k], int(r["actual_saves"] >= k)) for r in cal_p]
        if 0.02 < sum(y for _, y in pairs) / len(pairs) < 0.98:
            platt[k] = fit_platt(pairs)
    fin_p = probs(final_g)

    # baselines: league-mean NB; goalie's last-10-start mean NB (needs >= 3 starts)
    league_mean = sum(r["actual_saves"] for r in calib_g) / len(calib_g)
    markets = {}
    for k in SAVE_THRESHOLDS:
        if k not in platt:
            continue
        common = [r for r in fin_p if r["recent_saves_mean"] is not None]
        y = lambda r: int(r["actual_saves"] >= k)    # noqa: E731
        model_raw = [(r["p"][k], y(r)) for r in common]
        model_platt = [(apply_platt(r["p"][k], *platt[k]), y(r)) for r in common]
        base_form = [(prob_saves_at_least(k, max(r["recent_saves_mean"], 1.0), alpha), y(r)) for r in common]
        base_league = [(prob_saves_at_least(k, league_mean, alpha), y(r)) for r in common]
        markets[f"saves>={k}"] = {
            "common_rows": len(common), "model_raw": metrics(model_raw), "model_platt": metrics(model_platt),
            "baseline_goalie_last10_mean": metrics(base_form), "baseline_league_mean": metrics(base_league),
            "calibration_platt": calibration_table(model_platt), "platt": [round(platt[k][0], 4), round(platt[k][1], 4)]}

    def coverage(recs):
        hit = sum(1 for r in recs if saves_quantile_range(r["mean_saves"], alpha)[0] <= r["actual_saves"]
                  <= saves_quantile_range(r["mean_saves"], alpha)[1])
        return round(hit / len(recs), 4)

    mae = {"saves_model": round(sum(abs(r["mean_saves"] - r["actual_saves"]) for r in final_g) / len(final_g), 4),
           "saves_league_mean": round(sum(abs(league_mean - r["actual_saves"]) for r in final_g) / len(final_g), 4),
           "shots_against_model": round(sum(abs(r["exp_sa"] - r["actual_sa"]) for r in final_g) / len(final_g), 4),
           "goals_against_model": round(sum(abs(r["exp_ga"] - r["actual_ga"]) for r in final_g) / len(final_g), 4),
           "goals_against_league": round(sum(abs(sum(x["exp_ga"] for x in final_g) / len(final_g) - r["actual_ga"])
                                             for r in final_g) / len(final_g), 4), "n": len(final_g)}
    range80 = {"nominal": 0.8, "calibration_season": coverage(cal_p), "final_season": coverage(fin_p)}

    # win probability: logistic on [1, strength_diff] and [1, strength_diff, goalie_diff]
    cal_games = [r for r in gmrec if r["season"] == CALIB_SEASON and r["home_win"] is not None]
    fin_games = [r for r in gmrec if r["season"] == FINAL_SEASON and r["home_win"] is not None]
    w_base = fit_logistic([([1.0], r["home_win"]) for r in cal_games])
    w_strength = fit_logistic([([1.0, r["strength_diff"]], r["home_win"]) for r in cal_games])
    w_full = fit_logistic([([1.0, r["strength_diff"], r["goalie_diff"]], r["home_win"]) for r in cal_games])

    def score(wv, feat):
        return metrics([(_sigmoid(sum(a * b for a, b in zip(wv, feat(r)))), r["home_win"]) for r in fin_games])

    win = {"games_scored": len(fin_games), "shootouts_excluded": sum(1 for r in gmrec if r["season"] == FINAL_SEASON and r["shootout"]),
           "home_rate_baseline": score(w_base, lambda r: [1.0]),
           "strength_only": score(w_strength, lambda r: [1.0, r["strength_diff"]]),
           "strength_and_named_goalies": score(w_full, lambda r: [1.0, r["strength_diff"], r["goalie_diff"]]),
           "strength_and_goalies_tied": score(w_strength + [w_strength[1]], lambda r: [1.0, r["strength_diff"], r["goalie_diff"]]) if False else
               metrics([(_sigmoid(w_strength[0] + w_strength[1] * (r["strength_diff"] + r["goalie_diff"])), r["home_win"]) for r in fin_games]),
           "tied_note": "Goalie term tied to the strength coefficient: a goal saved per game counts like a goal of differential per game (no extra fitted parameter).",
           "coefficients": {"intercept": round(w_full[0], 4), "strength_diff": round(w_full[1], 4), "goalie_saved_goals": round(w_full[2], 4)},
           "coefficients_strength_only": [round(x, 4) for x in w_strength],
           "calibration_full": calibration_table([(_sigmoid(sum(a * b for a, b in zip(w_full, [1.0, r["strength_diff"], r["goalie_diff"]]))),
                                                   r["home_win"]) for r in fin_games], bins=5)}

    # start likelihood: logistic on usage / rest, trained on train seasons, scored on the final season
    def start_rows(recs):
        return [([1.0, r["start_features"]["share_last10"], float(r["start_features"]["back_to_back"]),
                  float(r["start_features"]["prev_started"]), float(r["start_features"]["back_to_back"] and r["start_features"]["prev_started"])],
                 1) for r in recs]
    # positive examples are starters; negatives are team-games where that goalie was on the team's recent list but did not start
    start_report = _start_validation(rows)

    report = {"model_version": MODEL_VERSION, "splits": {"train": list(TRAIN_SEASONS), "calibration": CALIB_SEASON, "final_evaluation": FINAL_SEASON},
              "alpha_saves": round(alpha, 4), "league_mean_saves_calibration": round(league_mean, 3),
              "saves_markets": markets, "expected_value_mae_final": mae, "saves_80pct_range_coverage": range80,
              "win_probability": win, "start_likelihood": start_report, "rows": {"goalie_calibration": len(calib_g), "goalie_final": len(final_g)},
              "params": {"sv_prior_shots": SV_PRIOR_SHOTS, "sv_half_life_shots": SV_HALF_LIFE_SHOTS,
                         "strength_half_life_games": STRENGTH_HALF_LIFE_GAMES, "strength_k_games": STRENGTH_K_GAMES,
                         "shot_half_life_games": SHOT_HALF_LIFE_GAMES, "shot_k_games": SHOT_K_GAMES},
              "win_coefficients": {"full": w_full, "strength_only": w_strength, "base": w_base},
              "platt_params": {f"saves>={k}": list(v) for k, v in platt.items()},
              "elapsed_seconds": round(time.time() - t0, 1)}
    return report


def _start_validation(rows: list[dict]) -> dict:
    """Does a simple usage/rest model beat 'the goalie with the most recent starts starts' on held-out seasons?"""
    state = TeamGoalieState()
    samples = []         # (season, features, started)
    for date, day in _by_date(rows):
        teams: dict[str, list[dict]] = defaultdict(list)
        for r in day:
            teams[r["team"]].append(r)
        for team, rs in teams.items():
            starter = max(rs, key=lambda x: x["toi"])
            recent_ids = [gid for _, gid in state.team_starters.get(team, [])[-10:]]
            if len(recent_ids) < 5:
                continue
            for gid in set(recent_ids) | {starter["player_id"]}:
                if gid not in state.goalies or state.goalies[gid].team != team:
                    continue
                f = state.start_features(team, gid, date)
                samples.append((starter["season"], f, int(gid == starter["player_id"])))
        state.consume_day(day)

    def x(f):
        return [1.0, f["share_last10"], float(f["back_to_back"]), float(f["prev_started"]),
                float(f["back_to_back"] and f["prev_started"])]
    tr = [(x(f), y) for s, f, y in samples if s in TRAIN_SEASONS]
    ca = [(x(f), y) for s, f, y in samples if s == CALIB_SEASON]
    fi = [(x(f), y) for s, f, y in samples if s == FINAL_SEASON]
    w = fit_logistic(tr + ca)
    base_rate = sum(y for _, y in tr + ca) / len(tr + ca)
    share_only = fit_logistic([([1.0, xi[1]], y) for xi, y in tr + ca])
    return {"samples_final": len(fi),
            "model": metrics([(_sigmoid(sum(a * b for a, b in zip(w, xi))), y) for xi, y in fi]),
            "share_of_last10_only": metrics([(_sigmoid(share_only[0] + share_only[1] * xi[1]), y) for xi, y in fi]),
            "base_rate": metrics([(base_rate, y) for _, y in fi]),
            "coefficients": [round(v, 4) for v in w], "note": "Estimates how likely each recently used goalie is to start; it is not a confirmation."}


# ------------------------------------------------------------------ live use

def load_validation(path: Path = OUT_PATH) -> dict:
    return json.loads(path.read_text())


def saves_verdicts(validation: dict) -> dict:
    """Per saves threshold: does the (raw) model beat both baselines on log loss on the held-out season?"""
    out = {}
    for key, m in validation["saves_markets"].items():
        model = m["model_raw"]["log_loss"]
        base = {"goalie's last-10-start average": m["baseline_goalie_last10_mean"]["log_loss"],
                "league-average saves": m["baseline_league_mean"]["log_loss"]}
        out[key] = {"verdict": "BEATS_BASELINES" if all(model < v for v in base.values()) else "DOES_NOT_BEAT_BASELINES",
                    "model_log_loss": model, "baselines_log_loss": base, "rows": m["common_rows"], "brier": m["model_raw"]["brier"],
                    "base_rate": m["model_raw"]["base_rate"], "platt_log_loss": m["model_platt"]["log_loss"],
                    "probability_used": "raw (the fitted calibration did not improve the held-out season)"
                    if m["model_platt"]["log_loss"] >= model else "raw"}
    return out


def build_live(rows: list[dict] | None = None, validation: dict | None = None) -> dict:
    """The goalie/team state after every observed game, with the fitted parameters from the validation report."""
    validation = validation or load_validation()
    rows = rows if rows is not None else history.goalie_games()
    state = TeamGoalieState()
    newest = ""
    for date, day in _by_date(rows):
        state.consume_day(day)
        newest = date
    return {"state": state, "newest_game_date": newest, "alpha": validation["alpha_saves"],
            "strength_coef": validation["win_probability"]["coefficients_strength_only"],
            "start_coef": validation["start_likelihood"]["coefficients"], "validation": validation}


def start_probabilities(live: dict, team: str, date: str, *, limit: int = 3) -> list[dict]:
    """Estimated chance each recently used goalie of `team` starts on `date`, normalised across the candidates."""
    state: TeamGoalieState = live["state"]
    recent = state.team_starters.get(team, [])
    ids = []
    for _d, gid in reversed(recent[-10:]):
        if gid not in ids:
            ids.append(gid)
    cands = [g for g in ids if g in state.goalies and state.goalies[g].team == team]
    w = live["start_coef"]
    scored = []
    for gid in cands:
        f = state.start_features(team, gid, date)
        x = [1.0, f["share_last10"], float(f["back_to_back"]), float(f["prev_started"]), float(f["back_to_back"] and f["prev_started"])]
        scored.append((gid, _sigmoid(sum(a * b for a, b in zip(w, x))), f))
    total = sum(p for _, p, _ in scored) or 1.0
    scored.sort(key=lambda t: -t[1])
    return [{"goalie_id": gid, "start_probability": p / total, "raw_probability": p, "features": f} for gid, p, f in scored[:limit]]


def win_probability(live: dict, home: str, away: str, home_goalie: str | None = None, away_goalie: str | None = None) -> dict:
    """Home win probability (regulation + overtime + shootout combined, the sportsbook meaning).
    `base` is the validated strength-only model. With goalies named, `with_goalies` adds each goalie's expected goals saved
    per game against that opponent, tied to the strength coefficient; held-out validation showed this did NOT improve the
    forecast, so it is offered as a scenario, never as the model's probability."""
    state: TeamGoalieState = live["state"]
    b0, b1 = live["strength_coef"]
    diff = state.team_strength(home) - state.team_strength(away)
    base = _sigmoid(b0 + b1 * diff)
    out = {"base": base, "strength_diff_goals_per_game": diff}
    if home_goalie and away_goalie:
        gd = state.saved_goals_per_game(home_goalie, home, away) - state.saved_goals_per_game(away_goalie, away, home)
        out["with_goalies"] = _sigmoid(b0 + b1 * (diff + gd))
        out["goalie_edge_goals_per_game"] = gd
    return out


def main() -> dict:
    rep = run()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rep, indent=1, sort_keys=True))
    summary = {k: rep[k] for k in ("model_version", "alpha_saves", "expected_value_mae_final", "saves_80pct_range_coverage", "elapsed_seconds")}
    summary["win_probability"] = {k: rep["win_probability"][k] for k in ("games_scored", "home_rate_baseline", "strength_only", "strength_and_named_goalies", "coefficients")}
    summary["start_likelihood"] = {k: rep["start_likelihood"][k] for k in ("samples_final", "model", "share_of_last10_only", "base_rate")}
    summary["saves_ge_26"] = rep["saves_markets"].get("saves>=26")
    print(json.dumps(summary, indent=1))
    return rep


if __name__ == "__main__":
    main()
