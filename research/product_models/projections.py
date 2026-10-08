"""
Matchup projections for skaters: expected time on ice, power-play time, shots, goals, assists, points, hits and blocks,
and the probability of reaching a count threshold.

MODEL (version `player-rate-toi-v2`): expected stat = per-minute rate x expected minutes x opponent factor.
  * rate  : exponentially weighted stat-per-minute over the player's own previous games (across seasons), shrunk toward
            the position-group league rate with `k_minutes` pseudo-minutes. Only games BEFORE the one being projected.
  * minutes: exponentially weighted recent time on ice (and power-play time), shrunk toward the position-group average.
  * opponent: the opposing team's recent rate of ALLOWING that stat to skaters, relative to league, damped and capped.
  * counts : negative binomial around the expected value (dispersion fitted on a calibration season), so a threshold
             probability follows from the mean; a Platt calibration fitted on a SEPARATE season can be applied on top.

This is a baseline built to be tested, not a claim of edge: research/product_models/validate.py evaluates it
chronologically against simple baselines, and the live recommendation path only uses a probability source whose
validation record supports it (see docs/MODEL_VALIDATION.md).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

MODEL_VERSION = "player-rate-toi-v2"
STATS = ("shots", "goals", "assists", "points", "hits", "blocks")
POSITION_GROUPS = {"C": "F", "L": "F", "R": "F", "LW": "F", "RW": "F", "W": "F", "F": "F", "D": "D"}


def group_of(pos: str) -> str:
    return POSITION_GROUPS.get(pos, "F")


@dataclass(frozen=True)
class ModelConfig:
    rate_half_life_games: float = 30.0
    toi_half_life_games: float = 8.0
    k_minutes: float = 600.0          # pseudo-minutes of league-average play added to every player's rate
    k_toi_games: float = 3.0          # pseudo-games of the position average added to the minutes estimate
    opp_half_life_games: float = 20.0
    opp_beta: float = 0.5             # exponent applied to the opponent's relative allowance
    opp_cap: tuple[float, float] = (0.88, 1.12)
    opp_k_games: float = 6.0


@dataclass
class _PlayerState:
    games: int = 0
    w_rate: float = 0.0               # decayed number of games (rate window)
    minutes: float = 0.0              # decayed minutes
    totals: dict = field(default_factory=lambda: defaultdict(float))     # decayed stat totals
    w_toi: float = 0.0
    toi: float = 0.0
    toi_pp: float = 0.0
    last_toi: list = field(default_factory=list)
    last_date: str = ""
    team: str = ""
    name: str = ""
    pos: str = "F"


class SkaterModel:
    """Walks games in date order; `project()` is always as of the games already consumed."""

    def __init__(self, config: ModelConfig = ModelConfig()):
        self.cfg = config
        self.players: dict[str, _PlayerState] = {}
        self.league: dict[str, dict] = {g: defaultdict(float) for g in ("F", "D")}   # position group totals (all history)
        self.team_allowed: dict[str, dict] = defaultdict(lambda: defaultdict(float))  # decayed stats allowed per game
        self.team_games: dict[str, float] = defaultdict(float)
        self.league_allowed: dict = defaultdict(float)
        self.league_games: float = 0.0
        self._lam_rate = 0.5 ** (1.0 / config.rate_half_life_games)
        self._lam_toi = 0.5 ** (1.0 / config.toi_half_life_games)
        self._lam_opp = 0.5 ** (1.0 / config.opp_half_life_games)

    # ---- state updates -----------------------------------------------------------------------------------
    def consume_day(self, rows: list[dict]) -> None:
        """Update state with one date's finished games (call only after projecting that date)."""
        per_team_game: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
        for r in rows:
            st = self.players.get(r["player_id"])
            if st is None:
                st = self.players[r["player_id"]] = _PlayerState()
            lam, lam_t = self._lam_rate, self._lam_toi
            st.games += 1
            st.w_rate = lam * st.w_rate + 1.0
            st.minutes = lam * st.minutes + r["toi"]
            for s in STATS:
                st.totals[s] = lam * st.totals[s] + r[s]
            st.w_toi = lam_t * st.w_toi + 1.0
            st.toi = lam_t * st.toi + r["toi"]
            st.toi_pp = lam_t * st.toi_pp + r["toi_pp"]
            st.last_toi = (st.last_toi + [r["toi"]])[-10:]
            st.last_date, st.team, st.name, st.pos = r["date"], r["team"], r["name"], group_of(r["pos"])
            lg = self.league[group_of(r["pos"])]
            lg["minutes"] += r["toi"]
            lg["games"] += 1.0
            lg["toi_pp"] += r["toi_pp"]
            for s in STATS:
                lg[s] += r[s]
            agg = per_team_game[(r["game_id"], r["opp"])]          # stats the opponent's skaters recorded against r["opp"]
            for s in STATS:
                agg[s] += r[s]
        lam_o = self._lam_opp
        seen = set()
        for (game_id, defending_team), agg in per_team_game.items():
            ta = self.team_allowed[defending_team]
            for s in STATS:
                ta[s] = lam_o * ta[s] + agg[s]
            self.team_games[defending_team] = lam_o * self.team_games[defending_team] + 1.0
            for s in STATS:
                self.league_allowed[s] += agg[s]
            self.league_games += 1.0
            seen.add(defending_team)

    # ---- projections -------------------------------------------------------------------------------------
    def opponent_factor(self, opp_team: str, stat: str) -> float:
        if self.league_games <= 0 or self.team_games.get(opp_team, 0.0) <= 0:
            return 1.0
        league_rate = self.league_allowed[stat] / self.league_games
        if league_rate <= 0:
            return 1.0
        k = self.cfg.opp_k_games
        w = self.team_games[opp_team]
        team_rate = (self.team_allowed[opp_team][stat] + k * league_rate) / (w + k)
        factor = (team_rate / league_rate) ** self.cfg.opp_beta
        lo, hi = self.cfg.opp_cap
        return max(lo, min(hi, factor))

    def sample_size(self, player_id: str) -> int:
        st = self.players.get(player_id)
        return st.games if st else 0

    def project(self, player_id: str, opp_team: str | None = None, pos: str | None = None) -> dict | None:
        st = self.players.get(player_id)
        grp = st.pos if st else group_of(pos or "F")
        lg = self.league[grp]
        if lg["minutes"] <= 0:
            return None
        if st is None:
            return None
        prior_toi = lg["minutes"] / max(lg["games"], 1.0)
        prior_pp = lg["toi_pp"] / max(lg["games"], 1.0)
        k_t = self.cfg.k_toi_games
        exp_toi = (st.toi + k_t * prior_toi) / (st.w_toi + k_t)
        exp_pp = (st.toi_pp + k_t * prior_pp) / (st.w_toi + k_t)
        k = self.cfg.k_minutes
        out = {"toi": exp_toi, "toi_pp": exp_pp, "games_observed": st.games, "position_group": grp,
               "recent_toi": list(st.last_toi)}
        for s in STATS:
            league_rate = lg[s] / lg["minutes"]
            rate = (st.totals[s] + k * league_rate) / (st.minutes + k)
            factor = self.opponent_factor(opp_team, s) if opp_team else 1.0
            out[s] = rate * exp_toi * factor
        return out


# ---- count distribution ---------------------------------------------------------------------------------------

def nb_pmf(k: int, mean: float, alpha: float) -> float:
    """Negative binomial with Var = mean + alpha*mean^2 (Poisson as alpha -> 0)."""
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if alpha < 1e-9:
        return math.exp(-mean + k * math.log(mean) - math.lgamma(k + 1))
    r = 1.0 / alpha
    p = r / (r + mean)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1.0 - p))


def prob_at_least(threshold: int, mean: float, alpha: float) -> float:
    if threshold <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - sum(nb_pmf(i, mean, alpha) for i in range(threshold))))


def fit_dispersion(pairs: list[tuple[float, float]]) -> float:
    """Method-of-moments dispersion from (predicted_mean, actual) pairs: Var(actual - mean) = mean + alpha*mean^2."""
    num = den = 0.0
    for mean, actual in pairs:
        num += (actual - mean) ** 2 - mean
        den += mean * mean
    return max(0.0, num / den) if den > 0 else 0.0
