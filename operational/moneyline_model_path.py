"""
Which model prices MONEYLINE tickets, and a prospective record that lets the two candidates be compared on live data.

Why the validated strength model (`goalie-team-v1`) is displayed while Elo (`moneyline-t35-v1`) prices tickets: the strength model was
validated for the product pages; the ticket path predates it and was left untouched. docs/validation/moneyline_model_comparison.json
now compares them chronologically on the same games. Result: the strength model is nominally ahead in both folds but its paired
confidence interval includes zero in both, and neither is shown to beat a sportsbook price (no historical prices exist). That is not
evidence enough to switch, so the default stays Elo.

Two mechanisms, both versioned and tested:
  * the SHADOW LOG (always on): for every moneyline candidate the engine prices, one append-only row per (day, game, selection) holds the
    Elo probability, the strength-model probability and the market's no-vig probability at decision time. After games finish,
    `scoreboard()` scores all three on the same games -- a prospective, market-inclusive comparison that no backtest here can give;
  * an OPT-IN SWITCH, `NHL_ENGINE_MONEYLINE_MODEL=strength-v1`: ticket legs then use the strength model's probability with the same
    conservative band the Elo path applies. The switch is INERT until the evidence supports it: asking for it is not enough, and neither is a
    nominally lower log loss. It takes effect only after the scoreboard has MIN_GAMES_FOR_A_CLAIM finished game-sides and the paired 95%
    interval of (strength - Elo) log loss lies entirely below zero (`active_model`, `scoreboard`).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os

from operational import state_paths

ENV = "NHL_ENGINE_MONEYLINE_MODEL"
ELO, STRENGTH = "elo", "strength-v1"
VERSIONS = {ELO: "moneyline-t35-v1", STRENGTH: "moneyline-strength-v1"}
LOG_NAME = "moneyline_shadow.jsonl"
MIN_GAMES_FOR_A_CLAIM = 150          # below this the scoreboard states "too few games" instead of a verdict
_live_cache: dict = {}


EVIDENCE_NAME = "moneyline_model_evidence.json"


def evidence() -> dict:
    """The latest prospective verdict (written by scoreboard()); {} until there is one."""
    p = state_paths.path(EVIDENCE_NAME)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def active_model() -> str:
    """Elo unless BOTH the owner asked for the strength model AND the prospective evidence supports it. Asking is not enough: the switch is inert
    until the shadow scoreboard has MIN_GAMES_FOR_A_CLAIM finished game-sides and the paired 95% interval of (strength - Elo) log loss lies below zero."""
    value = (os.environ.get(ENV) or ELO).strip().lower()
    if value == STRENGTH and evidence().get("supports_strength_model") is True:
        return STRENGTH
    return ELO


def version() -> str:
    return VERSIONS[active_model()]


def strength_probability(home: str, away: str, rows=None) -> float | None:
    """P(home wins) from goalie-team-v1 strength-only coefficients; None when a team is unknown to the model."""
    from research.product_models import history, team_goalie as tg
    if rows is None and state_paths.under_test():
        return None                                   # a test run never rebuilds the model from the real game logs
    key = "live"
    if rows is not None or key not in _live_cache:
        _live_cache[key] = tg.build_live(rows if rows is not None else history.goalie_games())
    live = _live_cache[key]
    try:
        return float(tg.win_probability(live, home, away)["base"])
    except Exception:  # noqa: BLE001 - a team the model has never seen
        return None


def apply_switch(report, selection_is_home: bool, home: str, away: str, rows=None) -> tuple[float | None, float | None]:
    """(true, conservative) probabilities for the selection under the active model; Elo's own pair when the switch is off."""
    elo_true, elo_cons = report.model_true_probability, report.model_conservative_probability
    if active_model() != STRENGTH or elo_true is None or elo_cons is None:
        return elo_true, elo_cons
    p_home = strength_probability(home, away, rows)
    if p_home is None:
        return elo_true, elo_cons
    p = p_home if selection_is_home else 1.0 - p_home
    return p, max(p - (elo_true - elo_cons), 0.001)


def _log_path():
    return state_paths.path(LOG_NAME)


def _read_log() -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def record_shadow(now: dt.datetime, game_id: str, game_date: str, home: str, away: str, selection: str, elo_true: float | None,
                  strength_selection: float | None, market_no_vig: float | None, price: float | None) -> bool:
    """Appends one row per (ET day, game, selection); returns False if that row already exists or nothing could be compared."""
    if elo_true is None or strength_selection is None:
        return False
    key = f"{game_date}|{game_id}|{selection}"
    if any(r["key"] == key for r in _read_log()):
        return False
    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"key": key, "logged_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "game_id": str(game_id), "game_date": game_date, "home": home,
           "away": away, "selection": selection, "elo": round(elo_true, 5), "strength": round(strength_selection, 5),
           "market_no_vig": None if market_no_vig is None else round(market_no_vig, 5), "price": price,
           "active_model": active_model()}
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")
    return True


def _ll(p: float, y: int) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def scoreboard(conn) -> dict:
    """Scores every shadow row whose game is FINAL (result known) -- the same games for every candidate."""
    rows = _read_log()
    scored = []
    for r in rows:
        g = conn.execute("SELECT game_state, home_team, home_score, away_score FROM games WHERE game_id = ?", (int(r["game_id"]),)).fetchone()
        if g is None or g["game_state"] != "FINAL" or g["home_score"] is None or g["home_score"] == g["away_score"]:
            continue
        home_won = g["home_score"] > g["away_score"]
        y = int(home_won if r["selection"] == r["home"] else not home_won)
        scored.append((r, y))
    n = len(scored)
    out = {"logged": len(rows), "scored": n, "min_games_for_a_claim": MIN_GAMES_FOR_A_CLAIM, "versions": VERSIONS}
    if n:
        def mean(vals):
            return round(sum(vals) / len(vals), 5)
        out["log_loss"] = {"elo": mean([_ll(r["elo"], y) for r, y in scored]), "strength": mean([_ll(r["strength"], y) for r, y in scored])}
        with_market = [(r, y) for r, y in scored if r["market_no_vig"] is not None]
        if with_market:
            out["log_loss"]["market_no_vig"] = mean([_ll(r["market_no_vig"], y) for r, y in with_market])
            out["market_games"] = len(with_market)
    out["verdict"] = ("TOO_FEW_GAMES" if n < MIN_GAMES_FOR_A_CLAIM else "ENOUGH_GAMES_FOR_OWNER_DECISION")
    supports = False
    if n >= MIN_GAMES_FOR_A_CLAIM:
        from research import elo_comparison as ec
        d = ec.paired_bootstrap_delta([_ll(r["elo"], y) for r, y in scored], [_ll(r["strength"], y) for r, y in scored])
        out["strength_minus_elo"] = {k: round(d[k], 5) for k in ("point_delta", "ci_low", "ci_high")}
        supports = d["ci_high"] < 0.0
        out["verdict"] = "STRENGTH_SUPPORTED" if supports else "NOT_DISTINGUISHABLE_OR_WORSE"
    out["supports_strength_model"] = supports
    out["scored_at_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        p = state_paths.path(EVIDENCE_NAME)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"supports_strength_model": supports, "scored": n, "verdict": out["verdict"], "scored_at_utc": out["scored_at_utc"]}))
    except OSError:
        pass
    return out
