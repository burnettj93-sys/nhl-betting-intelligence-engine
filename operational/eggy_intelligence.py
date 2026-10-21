"""
Eggy's intelligence review: understand the hockey game, understand the player, understand the market, find out WHY the model disagrees with the market, argue against the bet, and only then decide.

    UNDERSTAND THE GAME -> THE PLAYER -> THE MARKET -> WHY WE DISAGREE -> ARGUE AGAINST OURSELVES -> DOES THE THESIS SURVIVE? -> RECORD / LOWER PRIORITY / PASS

Nothing here ingests data, calls a provider or adds a market. It reads what the platform already holds -- the MoneyPuck team game-by-game file (2008 to today), the skater game logs and the validated
skater model, the goalie model, the schedule, the prices -- and the evidence file docs/validation/eggy_evidence.json (deploy/build_eggy_evidence.py) that says, from history, how reliable each of those
estimates has been. It produces, for every candidate bet, a review card (a plain dict, stored with the bet) that answers: why this team or player, why tonight, why this market, why this price, who is most
likely to win, what the model believes, what the market believes, why they disagree, what supports the bet, what argues against it, what history says, what data is missing, and what would make Eggy pass.

THERE IS NO "INTELLIGENCE SCORE". A score would need weights, and no history can tell us what they should be. Every judgement below is a rule on a named, printed quantity, and each rule's threshold is either
an existing engine constant (the 3-point edge the main engine already uses), a statistical convention (two standard errors), or a design choice written next to it. Where a number can be validated from
history (the team model's reliability, a skater probability's reliability for a player like this) it is read from the evidence file and shown with its sample size; where it cannot (is the market wrong
about a moneyline?) the card says UNVALIDATED instead of inventing a weight.

Ranking is therefore lexicographic (see `rank_key`): the action first, then the edge class, the contradiction level, how well-calibrated the estimate is, the edge, the hit chance. Nothing is traded off
against anything else.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass, field

from operational import eastern_time as et

VERSION = "eggy-intel-v1"
LEVELS = ("LOW", "MODERATE", "HIGH", "SEVERE")                       # contradiction
CAL_LEVELS = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")               # calibration confidence
ACTIONS = ("RECORD", "LOWER_PRIORITY", "PASS")

EDGE_MIN = 0.03                  # the main engine's own minimum edge (config.MIN_CONSERVATIVE_EDGE, 3 probability points)
DISAGREE_LARGE_DEFAULT = 0.06    # about one standard deviation of (model - market) over the prospective sample (0.062); replaced by the evidence file's value
Z_SIGNIFICANT = 2.0              # two standard errors
MIN_CELL_N = 300                 # fewer historical analogues than this is too few to adjust anything
LONGSHOT_IMPLIED = 0.05          # a price implying 5% or less (+1900 and longer)
LONGSHOT_MIN_PLAYER_GAMES = 100  # a tail probability needs a long record behind it
THIN_PLAYER_GAMES = 40
SAME_GAME_SOFT_CAP = 3           # bets per game before further ones in that game wait behind every other candidate (design choice: one game is one cluster of dependent outcomes)
RECENT_GAMES = 6

_STAT = {"PLAYER_SOG_ALTERNATE": "shots", "PLAYER_SOG": "shots", "PLAYER_POINTS": "points", "PLAYER_GOALS": "goals"}


# ------------------------------------------------------------------------------------------------ small helpers
def pct(p, digits: int = 1) -> str:
    return "n/a" if p is None else f"{p * 100:.{digits}f}%"


def pts(p) -> str:
    return "n/a" if p is None else f"{p * 100:+.1f} pts"


def implied_from_american(a: float) -> float:
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def american_from_prob(p: float) -> float:
    p = min(max(p, 0.001), 0.999)
    return round(-100.0 * p / (1.0 - p)) if p >= 0.5 else round(100.0 * (1.0 - p) / p)


def _level_index(level: str) -> int:
    return LEVELS.index(level)


def combine_contradiction(flags: list[dict]) -> str:
    """Contradiction level from the flags that fired. One HIGH or two MODERATE flags make it HIGH; two HIGH flags (or any SEVERE) make it SEVERE; one MODERATE makes it MODERATE; otherwise LOW. A rule on
    counts, not a weighted sum: compounding doubts is the point, and no history says how much each one is worth."""
    lv = [f["level"] for f in flags]
    if "SEVERE" in lv or lv.count("HIGH") >= 2:
        return "SEVERE"
    if "HIGH" in lv or lv.count("MODERATE") >= 2:
        return "HIGH"
    if "MODERATE" in lv:
        return "MODERATE"
    return "LOW"


def calibration_level(n: int | None, pred: float | None, obs: float | None, se: float | None) -> str:
    """How far an estimate can be trusted in this region, from history: HIGH when the analogues are plentiful (1000+) and the model's average sat within two standard errors of what happened; MEDIUM when
    there are 300+ and it did, LOW otherwise; INSUFFICIENT when there is no analogue."""
    if not n or pred is None or obs is None:
        return "INSUFFICIENT"
    se = se or math.sqrt(max(obs * (1 - obs), 1e-9) / n)
    ok = abs(pred - obs) <= Z_SIGNIFICANT * se
    if n >= 1000 and ok:
        return "HIGH"
    if n >= MIN_CELL_N and ok:
        return "MEDIUM"
    return "LOW"


def decision_confidence(contradiction: str, calibration: str, completeness: float) -> str:
    if contradiction in ("HIGH", "SEVERE") or calibration in ("LOW", "INSUFFICIENT") or completeness < 0.6:
        return "LOW"
    if contradiction == "LOW" and calibration == "HIGH" and completeness >= 0.8:
        return "HIGH"
    return "MEDIUM"


# ------------------------------------------------------------------------------------------------ context
@dataclass
class Context:
    """Everything a review reads, built once per cycle (or per replay instant). `as_of` is the Eastern date of the games: every state is built from data strictly BEFORE it."""
    now: dt.datetime
    as_of: str
    evidence: dict | None
    team_state: object | None = None
    skater_state: dict | None = None
    by_player: dict = field(default_factory=dict)
    games: dict = field(default_factory=dict)             # internal game_id -> {"home","away","start_utc"}
    goalie_live: dict | None = None
    notes: list = field(default_factory=list)             # why a part of the context is missing
    shadow: dict = field(default_factory=dict)


def build_context(nhl_conn, now: dt.datetime, *, as_of: str | None = None, skater_rows: list | None = None, team_rows: list | None = None, evidence: dict | None = None,
                  with_goalie: bool = True) -> Context:
    from research.product_models import history, live, matchup
    as_of = as_of or et.eastern_today(now)
    ev = evidence if evidence is not None else matchup.load_evidence()
    ctx = Context(now=now, as_of=as_of, evidence=ev)
    if ev is None:
        ctx.notes.append("the evidence file docs/validation/eggy_evidence.json is missing (run deploy/build_eggy_evidence.py)")
    try:
        rows = team_rows if team_rows is not None else matchup.team_games()
        if rows:
            ctx.team_state = matchup.build_state(rows, through_date=as_of)
        else:
            ctx.notes.append("no MoneyPuck team file on disk: the matchup model is unavailable")
    except Exception as exc:  # noqa: BLE001
        ctx.notes.append(f"matchup state failed: {exc.__class__.__name__}: {exc}")
    try:
        srows = skater_rows if skater_rows is not None else history.skater_games()
        srows = [r for r in srows if r["date"] < as_of]
        ctx.skater_state = live.build(srows)
        by = defaultdict(list)
        for r in srows:
            by[r["player_id"]].append(r)
        ctx.by_player = by
    except Exception as exc:  # noqa: BLE001
        ctx.notes.append(f"skater state failed: {exc.__class__.__name__}: {exc}")
    if with_goalie:
        try:
            from research.product_models import team_goalie as tg
            grows = [r for r in history.goalie_games() if r["date"] < as_of]
            ctx.goalie_live = tg.build_live(grows)
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"goalie state failed: {exc.__class__.__name__}: {exc}")
    if nhl_conn is not None:
        try:
            for r in nhl_conn.execute("SELECT game_id, home_team, away_team, scheduled_start_utc FROM games WHERE game_date = ?", (as_of,)).fetchall():
                ctx.games[str(r["game_id"])] = {"home": r["home_team"], "away": r["away_team"], "start_utc": r["scheduled_start_utc"]}
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"schedule failed: {exc.__class__.__name__}")
    return ctx


STALE_DATA_DAYS = 5              # the newest game in a data file may be at most this many days before the date being reviewed (the league never goes quiet longer than that inside the learning window)


def _days_before(as_of: str, newest: str | None) -> int | None:
    try:
        return (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(newest)).days if newest else None
    except ValueError:
        return None


def usable(ctx: Context) -> tuple[bool, str]:
    """The intelligence layer is usable when the evidence and both states exist and are current. A cohort that cannot review does not fall back to unreviewed ranking: it records nothing and says why.
    Stale state is an integrity matter (a review of last week's form is not a review): a team or skater file whose newest game is more than STALE_DATA_DAYS old makes the layer unusable."""
    problems = []
    if ctx.evidence is None:
        problems.append("evidence file missing")
    if ctx.team_state is None:
        problems.append("team state unavailable")
    else:
        age = _days_before(ctx.as_of, getattr(ctx.team_state, "newest", None))
        if age is not None and age > STALE_DATA_DAYS:
            problems.append(f"team data is {age} days old (newest game {ctx.team_state.newest})")
    if ctx.skater_state is None:
        problems.append("skater state unavailable")
    else:
        age = _days_before(ctx.as_of, ctx.skater_state.get("newest_game_date"))
        if age is not None and age > STALE_DATA_DAYS:
            problems.append(f"skater data is {age} days old (newest game {ctx.skater_state.get('newest_game_date')})")
    return (not problems), "; ".join(problems + ctx.notes[:3])


# ------------------------------------------------------------------------------------------------ evidence lookups
def _cells(ctx: Context, market: str) -> list:
    return (((ctx.evidence or {}).get("skater") or {}).get("cells") or {}).get(market) or []


def _band_of(edges: list, v: float) -> tuple:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return (lo, hi)
    return (edges[-2], edges[-1])


def analogue_cells(ctx: Context, market: str, p: float, grp: str, toi: float, pp: float, team_p: float | None) -> list[dict]:
    """The historical cells a player like this falls in, most general to most specific, each with its sample, what the model averaged and what happened.
    Each is a dict {"dimension","label","n","pred","obs","se","z"} with z = (obs - pred) / se (negative = the model over-predicted)."""
    sk = (ctx.evidence or {}).get("skater") or {}
    pe = (sk.get("p_bands") or {}).get(market)
    if not pe:
        return []
    pb = _band_of(pe, p)
    want = {"p": ("p", *pb), "grp": ("grp", grp, *pb), "toi": ("toi", grp, *_band_of(sk["toi_bands"], toi), *pb), "pp": ("pp", grp, *_band_of(sk["pp_bands"], pp), *pb)}
    if team_p is not None:
        want["env"] = ("env", *_band_of(sk["env_bands"], team_p), *pb)
    labels = {"p": f"any player, model {pct(pb[0], 0)}-{pct(min(pb[1], 1.0), 0)}", "grp": f"{'forwards' if grp == 'F' else 'defensemen'}, model {pct(pb[0], 0)}-{pct(min(pb[1], 1.0), 0)}"}
    out = []
    index = {tuple(c[:-3]): c for c in _cells(ctx, market)}
    for dim, key in want.items():
        c = index.get(tuple(key))
        if not c:
            continue
        n, pred, obs = c[-3], c[-2], c[-1]
        se = math.sqrt(max(obs * (1 - obs), 1e-9) / n) if n else None
        if dim == "toi":
            label = f"{'forwards' if grp == 'F' else 'defensemen'} expected to play {key[2]}-{key[3]} min, model {pct(pb[0], 0)}-{pct(min(pb[1], 1.0), 0)}"
        elif dim == "pp":
            label = f"{'forwards' if grp == 'F' else 'defensemen'} with {key[2]:g}-{key[3]:g} expected power-play min, model {pct(pb[0], 0)}-{pct(min(pb[1], 1.0), 0)}"
        elif dim == "env":
            label = f"team win probability {pct(key[1], 0)}-{pct(min(key[2], 1.0), 0)}, model {pct(pb[0], 0)}-{pct(min(pb[1], 1.0), 0)}"
        else:
            label = labels[dim]
        out.append({"dimension": dim, "label": label, "n": n, "pred": pred, "obs": obs, "se": round(se, 4) if se else None, "z": round((obs - pred) / se, 2) if se else None})
    return out


def best_adjustment(cells: list[dict]) -> dict | None:
    """The strongest evidence that the model's probability is wrong for a player like this, in either direction: the cell with the largest |z| among those with 300+ analogues and |z| >= 2.
    One adjustment, never a product of several (they overlap). factor = what happened / what the model averaged. A factor above 1 means the model ran LOW for such players (a price above our estimate may
    then be the model's fault, not the market's); below 1 means it ran HIGH."""
    cand = [c for c in cells if c["n"] >= MIN_CELL_N and c["z"] is not None and abs(c["z"]) >= Z_SIGNIFICANT and c["pred"] > 0]
    if not cand:
        return None
    c = max(cand, key=lambda x: abs(x["z"]))
    return {**c, "factor": round(c["obs"] / c["pred"], 4)}


# ------------------------------------------------------------------------------------------------ player facts
def _hit_rates(rows: list, stat: str, k: int, season_start: str) -> dict:
    vals = [(r["date"], r[stat]) for r in rows]

    def rate(sub):
        n = len(sub)
        h = sum(1 for _d, v in sub if v >= k)
        return {"hits": h, "n": n, "rate": (h / n) if n else None}
    return {"career": rate(vals[-150:]), "season": rate([x for x in vals if x[0] >= season_start]), "last30": rate(vals[-30:]), "last10": rate(vals[-10:])}


def _player(ctx: Context, pid: str) -> dict | None:
    return (ctx.skater_state or {}).get("players", {}).get(str(pid))


def _season_start(as_of: str) -> str:
    y = int(as_of[:4])
    return f"{y if int(as_of[5:7]) >= 8 else y - 1}-09-01"


def team_view(ctx: Context, home: str, away: str) -> dict | None:
    from research.product_models import matchup
    if ctx.team_state is None:
        return None
    try:
        return matchup.view(ctx.team_state, ctx.evidence, home, away, ctx.as_of)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}


def _game_sides(ctx: Context, leg) -> tuple[str | None, str | None]:
    g = ctx.games.get(str(leg.game_id))
    if g:
        return g["home"], g["away"]
    if leg.market_family == "MONEYLINE":
        return (leg.team, leg.opponent) if leg.side == "HOME" else (leg.opponent, leg.team)
    return None, None


# ------------------------------------------------------------------------------------------------ market block
def market_block(leg, estimate_used: float, model_p: float | None, opponent_implied: float | None = None) -> dict:
    implied = implied_from_american(float(leg.american_price))
    no_vig = leg.market_probability if leg.market_family == "MONEYLINE" else None
    return {"price": float(leg.american_price), "implied_with_margin": round(implied, 4), "no_vig": None if no_vig is None else round(float(no_vig), 4),
            "margin_note": ("two-sided market: the margin is removed to get the no-vig probability" if no_vig is not None else
                            "one-sided market: only this side is posted, so the price's implied probability INCLUDES the bookmaker's margin (unknown, so the true market probability is lower)"),
            "estimate_used": round(estimate_used, 4), "model": None if model_p is None else round(model_p, 4), "edge_vs_price": round(estimate_used - implied, 4),
            "model_edge_vs_price": None if model_p is None else round(model_p - implied, 4), "edge_label": "MODEL EDGE AT LAST OBSERVED PRICE",
            "breakeven_price_for_3pt_edge": american_from_prob(max(estimate_used - EDGE_MIN, 0.002))}


# ------------------------------------------------------------------------------------------------ the card
def _completeness(items: list[tuple[str, bool]]) -> dict:
    present = [n for n, ok in items if ok]
    missing = [n for n, ok in items if not ok]
    return {"present": present, "missing": missing, "k": len(present), "n": len(items), "fraction": round(len(present) / len(items), 3) if items else 0.0}


def _card_shell(leg, label: str, kind: str) -> dict:
    from research.real_market_parlay import engine as rmp
    return {"version": VERSION, "kind": kind, "bet": {"label": rmp.bet_label(leg.participant_name, leg.market_family, leg.threshold, leg.american_price), "player_or_team": leg.participant_name,
                                                      "market": rmp.market_name(leg.market_family, leg.threshold), "market_key": rmp.MARKET_KEYS.get(leg.market_family), "market_family": leg.market_family,
                                                      "threshold": leg.threshold, "side": leg.side, "price": float(leg.american_price), "game_id": str(leg.game_id), "team": leg.team,
                                                      "opponent": leg.opponent, "event_start_utc": leg.game_start_utc}}


def _finish(card: dict, flags: list[dict], cal: str, comp: dict, thesis: dict, policy: dict) -> dict:
    """Contradiction, decision confidence and the action, from the rules in `policy` (already evaluated into booleans by the caller)."""
    contradiction = combine_contradiction(flags)
    card["contradiction"] = {"level": contradiction, "flags": flags}
    card["calibration_confidence"] = card.get("calibration_confidence") or {"level": cal}
    card["data_completeness"] = comp
    card["thesis"] = thesis
    card["decision_confidence"] = decision_confidence(contradiction, card["calibration_confidence"]["level"], comp["fraction"])
    return card


# ----------------------------------------------------------------------------- moneyline
def review_moneyline(ctx: Context, leg, estimate_basis: str, estimate_used: float) -> dict:
    from research.product_models import matchup
    home, away = _game_sides(ctx, leg)
    card = _card_shell(leg, "", "MONEYLINE")
    side_team = leg.participant_id
    opp = away if side_team == home else home
    mv = team_view(ctx, home, away) if home and away else None
    implied = implied_from_american(float(leg.american_price))
    M = float(leg.market_probability) if leg.market_probability else estimate_used
    opp_implied = implied * (1.0 - M) / M if M else None
    opp_price = None if opp_implied is None else american_from_prob(opp_implied)
    ev = ((ctx.evidence or {}).get("matchup") or {})
    pros = ev.get("prospective_vs_market") or {}
    pll = pros.get("log_loss") or {}
    market_ahead = bool(pll.get("M") is not None and pll.get("H") is not None and pll["M"] <= pll["H"])          # on the games priced so far the market has been at least as good a guide as this model
    large = max(DISAGREE_LARGE_DEFAULT * 0.9, pros.get("disagreement_sd") or DISAGREE_LARGE_DEFAULT)
    card["bet"]["opponent_price_implied"] = opp_price
    card["bet"]["matchup"] = f"{away} @ {home}"
    flags: list[dict] = []
    if not mv or not mv.get("available"):
        card.update({"most_likely_outcome": "unknown: no team model", "hockey_view": {"available": False, "reason": (mv or {}).get("reason") or "; ".join(ctx.notes) or "no team state"}})
        card["market"] = market_block(leg, estimate_used, None)
        card["probabilities"] = {"model": None, "market_no_vig": round(M, 4), "disagreement": None}
        card["thesis"] = {"type": "NONE", "statement": "No hockey view could be built, so there is no thesis.", "strength": "NONE"}
        card["contradiction"] = {"level": "SEVERE", "flags": [{"name": "NO_HOCKEY_VIEW", "level": "SEVERE", "text": "no team model output for this game"}]}
        card["calibration_confidence"] = {"level": "INSUFFICIENT"}
        card["data_completeness"] = _completeness([("team model", False), ("market both sides", opp_implied is not None)])
        card["decision_confidence"] = "LOW"
        card["action"] = {"decision": "PASS", "reasons": ["no hockey view: Eggy does not bet a game it cannot assess"]}
        return card
    p_home = mv["p_home"]
    H = p_home if side_team == home else 1.0 - p_home
    D = H - M
    hockey_fav = mv["favourite"]
    p_fav = mv["p_favourite"]
    side_is_hockey_fav = side_team == hockey_fav
    market_fav = side_team if M >= 0.5 else opp
    elo = leg.model_probability
    an = matchup.analogues(ctx.evidence, H)
    cal = calibration_level(an and an["n"], an and an["predicted"], an and an["observed"], an and an["standard_error"])
    # --- thesis
    if D >= EDGE_MIN:
        ttype, strength = "MODEL_SEES_VALUE", ("STRONG" if D >= large else "MODERATE")
        statement = (f"The hockey model gives {side_team} {pct(H)} against the market's {pct(M)} ({pts(D)}): it sees {side_team} as underpriced"
                     f"{'' if side_is_hockey_fav else ', although it still rates ' + hockey_fav + ' the better team'}. This is an EXPERIMENT in whether the model's disagreement means anything: the model has not been shown to beat the market.")
    elif D <= -EDGE_MIN:
        ttype, strength = "MODEL_SAYS_OVERPRICED", "NONE"
        statement = f"The hockey model gives {side_team} {pct(H)} against the market's {pct(M)} ({pts(D)}): the model thinks {side_team} is OVERPRICED."
    elif side_is_hockey_fav and M >= 0.5:
        ttype, strength = "LIKELY_WINNER_AT_FAIR_PRICE", "WEAK"
        statement = f"{side_team} is the better team by the hockey model ({pct(H)}) and the market agrees ({pct(M)}): the likely winner at a fair price. No edge; the bookmaker's margin is the cost."
    else:
        ttype, strength = "UNDERDOG_AT_FAIR_PRICE", "NONE"
        statement = (f"{side_team} is the weaker team by the hockey model ({pct(H)}) and the market agrees ({pct(M)}): a fair-priced underdog. The only argument for it is the price; the hockey picture favours {hockey_fav}.")
    # --- flags (each names the quantity it is a rule on)
    if D >= EDGE_MIN and market_ahead:
        k = pros.get("weight_on_disagreement") or {}
        flags.append({"name": "MARKET_BEAT_THE_MODEL", "level": "MODERATE", "text": (f"on the {pros.get('games')} games priced so far the market's no-vig probability had log loss {pll['M']} against this model's {pll['H']}: when they disagree the market has been "
                                                                                    f"the better guide (a small sample; the weight a disagreement deserves is {k.get('best')}, 95% range {k.get('ci95')}). The model's disagreement has not been shown to mean anything")})
    if D >= large:
        flags.append({"name": "MARKET_MAY_KNOW_MORE", "level": "HIGH", "text": f"the model is {pts(D)} above the market, at least the usual size of a model-market gap ({pts(large)}): the market may have injury, goalie or lineup news the model lacks"})
    elif D >= EDGE_MIN:
        flags.append({"name": "MARKET_MAY_KNOW_MORE", "level": "MODERATE", "text": f"the model is {pts(D)} above the market: a gap worth explaining before trusting it"})
    if D >= EDGE_MIN and not side_is_hockey_fav:
        flags.append({"name": "HOCKEY_PICTURE_AGAINST", "level": "MODERATE", "text": f"the model still rates {hockey_fav} the better team ({pct(p_fav)}); the case for {side_team} is only that the market overrates {hockey_fav}"})
    if D < EDGE_MIN and not side_is_hockey_fav:
        flags.append({"name": "HOCKEY_PICTURE_AGAINST", "level": "HIGH", "text": f"{hockey_fav} is the better team ({pct(p_fav)}) and the price is fair: nothing argues for {side_team} but the odds"})
    if D <= -EDGE_MIN:
        flags.append({"name": "MODEL_SAYS_OVERPRICED", "level": "SEVERE" if D <= -large else "HIGH", "text": f"the model puts {side_team} {pts(-D)} BELOW the market"})
    votes = mv.get("indicator_votes") or {}
    if votes.get(side_team, 0) < votes.get(opp, 0) and D >= EDGE_MIN:
        flags.append({"name": "INDICATORS_FAVOUR_OPPONENT", "level": "MODERATE", "text": f"more of the indicators favour {opp} ({votes.get(opp, 0)}) than {side_team} ({votes.get(side_team, 0)})"})
    if mv["rest"].get(side_team, 7) <= 1 < mv["rest"].get(opp, 7):
        flags.append({"name": "BACK_TO_BACK", "level": "LOW", "text": f"{side_team} played yesterday and {opp} did not (already in the model)"})
    elo_worst = bool(pll and "E" in pll and pll["E"] >= max(pll.values()))
    # --- case for / against
    ind = mv["indicators"]
    me, th = ind[side_team], ind[opp]
    case_for, case_against = [], []
    if D >= EDGE_MIN:
        case_for.append(f"model {pct(H)} vs market {pct(M)}: {pts(D)} (the engine's own threshold for a meaningful edge is {pct(EDGE_MIN, 0)})")
    for d in mv["drivers"]:
        if d["feature"] in ("gd", "xgd", "b2b") and d.get("favours") == side_team and abs(d["log_odds_toward_home"]) >= 0.03:
            case_for.append(f"{d['label']} favours {side_team}")
    xg_fav = side_team if (mv["xg_net_log_odds_toward_home"] > 0) == (side_team == home) else opp
    if abs(mv["xg_net_log_odds_toward_home"]) >= 0.03:
        (case_for if xg_fav == side_team else case_against).append(f"net expected-goal strength favours {xg_fav}")
    case_for.append(f"decayed goal differential per game: {side_team} {me['gd']:+.2f} (league rank {me['ranks']['gd']}), {opp} {th['gd']:+.2f} (rank {th['ranks']['gd']})")
    if not side_is_hockey_fav:
        case_against.append(f"{hockey_fav} is the better team on the model's own terms ({pct(p_fav)}, a {mv['gap'].lower()} gap)")
    if ttype == "UNDERDOG_AT_FAIR_PRICE":
        case_against.append("the price is fair, so the expected value is the bookmaker's margin, a loss on average")
    if ttype == "MODEL_SAYS_OVERPRICED":
        case_against.append(f"our own model says {side_team} is overpriced by {pts(-D)}")
    if elo is not None and abs(elo - H) >= 0.05:
        case_against.append(f"Elo, the old moneyline model, says {pct(elo)} for {side_team}, {pts(elo - H)} from the hockey model. Elo is rebuilt from this season's results only (no earlier seasons), so after a handful of games its ratings are mostly noise" + ("; it has the worst record of every estimate priced so far" if elo_worst else "") + " and plays no part in this decision")
    if me["games"] < 8:
        case_against.append(f"{side_team} has played only {me['games']} games this season; the model leans on earlier seasons")
    if me["ranks"]["gd"] > 22 and me["ranks"]["xgd"] <= 11:
        case_for.append(f"{side_team}'s results lag its chance quality: goal differential rank {me['ranks']['gd']}, expected-goal rank {me['ranks']['xgd']}")
    if me["ranks"]["gd"] <= 8 and me["ranks"]["xgd"] >= 22:
        case_against.append(f"{side_team}'s results flatter its chance quality: goal differential rank {me['ranks']['gd']}, expected-goal rank {me['ranks']['xgd']}")
    # --- historical
    historical = []
    if an:
        historical.append({"kind": "TEAM_MODEL_RELIABILITY", "text": f"In {an['seasons']}, when the model gave a side about {an['band']}, that side won {pct(an['observed'])} of {an['n']:,} (the model averaged {pct(an['predicted'])}; standard error {pct(an['standard_error'])})", **an})
    if pros:
        k = pros.get("weight_on_disagreement") or {}
        ll = pros["log_loss"]
        models = {n: v for n, v in ll.items() if n != "M"}
        best_name = min(models, key=models.get)
        verdict = ("The market was at least as good as every model" if ll["M"] <= models[best_name] else
                   f"{(pros.get('names') or {}).get(best_name, best_name)} was ahead of the market by {ll['M'] - models[best_name]:.4f} log loss, a margin this sample cannot distinguish from luck")
        historical.append({"kind": "MODEL_VS_MARKET_PROSPECTIVE", "text": (f"On the {pros['games']} games priced since {pros['first_logged']}, log loss: market {ll['M']}, this model {ll['H']}, goalie-team strength {ll['S']}, Elo {ll['E']}. "
                                                                          f"{verdict}; the weight a model's disagreement deserves is {k.get('best')} with a 95% range of {k.get('ci95')}: too few games to say. UNVALIDATED.")})
    sh = _market_analogue(ctx, M)
    if sh:
        historical.append(sh)
    # --- the card
    pass_when = [f"the model-market gap on {side_team} falls below {pct(EDGE_MIN, 0)} (it is {pts(D)} now)", f"the price moves so that {side_team}'s no-vig probability rises above the model's {pct(H)} (about {american_from_prob(H)})",
                 "a starting-goalie or lineup report that favours the opponent (neither team's starter is confirmed: the confirmation feed is off)", f"{opp} not being the better team on the next refresh of the team data"]
    comp = _completeness([("team game-by-game data (2008 to date)", True), ("both sides of the market", opp_implied is not None), ("team model evidence file", ev.get("coefficients") is not None), ("rest / back-to-back", True),
                          ("confirmed starting goalies", False), ("injuries and lineup", False), ("this season's games", me["games"] >= 8 and th["games"] >= 8)])
    card.update({
        "most_likely_outcome": f"{hockey_fav} wins ({pct(p_fav)} by the hockey model; the market says {pct(max(M, 1 - M))} for {market_fav})",
        "why_tonight": [f"{away} at {home}; {mv['gap'].lower()} gap in favour of {hockey_fav}", f"rest: {home} {mv['rest'][home]}d, {away} {mv['rest'][away]}d",
                        f"last five: {home} {', '.join(('W' if g['gf'] > g['ga'] else 'L') for g in ind[home]['last10'])}; {away} {', '.join(('W' if g['gf'] > g['ga'] else 'L') for g in ind[away]['last10'])}"],
        "hockey_view": {"available": True, "favourite": hockey_fav, "p_favourite": p_fav, "gap": mv["gap"], "drivers": mv["drivers"], "indicators": ind, "model": mv["model"], "data_through": mv["data_through"],
                        "games_in_state": mv["games_in_state"], "indicator_votes": votes},
        "probabilities": {"model": round(H, 4), "market_no_vig": round(M, 4), "disagreement": round(D, 4), "elo_not_used": None if elo is None else round(float(elo), 4), "estimate_used_for_edge": round(estimate_used, 4), "estimate_basis": estimate_basis},
        "market": market_block(leg, estimate_used, H, opp_implied),
        "why_they_disagree": _why_disagree_moneyline(mv, side_team, opp, D),
        "case_for": case_for, "case_against": case_against, "historical": historical,
        "model_state": {"matchup_model": mv["model"], "evidence_version": ev.get("version"), "team_data_through": mv["data_through"], "skater_state_through": (ctx.skater_state or {}).get("newest_game_date"),
                        "elo": f"Elo ({'moneyline-t35-v1'}) sees only this season's results (no prior seasons loaded): excluded from the decision"},
        "pass_conditions": pass_when,
    })
    thesis = {"type": ttype, "statement": statement, "strength": strength}
    _finish(card, flags, cal, comp, thesis, {})
    card["calibration_confidence"] = {"level": cal, "evidence": an, "note": "the team model's probabilities are calibrated; whether its DISAGREEMENT with the market means anything is unvalidated" if ttype == "MODEL_SEES_VALUE" else None}
    card["decision_confidence"] = decision_confidence(card["contradiction"]["level"], cal, comp["fraction"])
    if ttype == "MODEL_SEES_VALUE":
        card["decision_confidence"] = "LOW" if card["decision_confidence"] == "HIGH" else card["decision_confidence"]          # an unvalidated edge never earns HIGH
    card["action"] = _moneyline_action(ttype, card["contradiction"]["level"], cal, comp["fraction"], side_is_hockey_fav, D)
    card["claims"] = [{"id": "favourite", "text": f"{hockey_fav} is the better team", "value": hockey_fav, "p": p_fav}, {"id": "disagreement", "text": f"model {pct(H)} vs market {pct(M)}", "value": D},
                      {"id": "rest", "text": "rest days", "value": mv["rest"]}]
    return card


def _market_analogue(ctx: Context, M: float) -> dict | None:
    """From the moneyline shadow log: the games whose market probability was close to this one's: how often that side won. Small samples are shown as such."""
    try:
        from operational import moneyline_model_path as mmp
        rows = mmp._read_log()
    except Exception:  # noqa: BLE001
        return None
    if not rows or ctx.skater_state is None:
        return None
    return None if len(rows) < 20 else {"kind": "MARKET_ANALOGUE", "text": f"The shadow log holds {len(rows)} priced sides; at this sample size a market-probability analogue is too thin to quote (see the prospective comparison above)."}


def _why_disagree_moneyline(mv: dict, side: str, opp: str, D: float) -> list[str]:
    if abs(D) < EDGE_MIN:
        return [f"They do not disagree: the model and the market are within {pct(EDGE_MIN, 0)} of each other on {side}."]
    out = []
    for d in mv["drivers"]:
        v = d["log_odds_toward_home"]
        if d["feature"] != "home_ice" and abs(v) >= 0.03:
            out.append(f"the model counts {d['label']} as worth {abs(v):.2f} log-odds in favour of {d.get('favours')}; a market that weighs it differently, or knows news the model lacks, would price the game differently")
    out.append("the market also knows what the model cannot see here: injuries, confirmed goalies, line changes (those feeds are not available to Eggy)")
    return out


def _moneyline_action(ttype: str, contradiction: str, cal: str, completeness: float, side_is_fav: bool, D: float) -> dict:
    reasons = []
    if ttype in ("UNDERDOG_AT_FAIR_PRICE", "MODEL_SAYS_OVERPRICED"):
        reasons.append("no thesis for this side" if ttype == "UNDERDOG_AT_FAIR_PRICE" else "the model thinks this side is overpriced")
        return {"decision": "PASS", "reasons": reasons}
    if contradiction == "SEVERE":
        return {"decision": "PASS", "reasons": ["severe contradiction"]}
    if cal in ("INSUFFICIENT",) or completeness < 0.5:
        return {"decision": "PASS", "reasons": ["too little evidence to judge"]}
    if ttype == "MODEL_SEES_VALUE":
        if contradiction in ("LOW", "MODERATE") and cal in ("HIGH", "MEDIUM"):
            return {"decision": "RECORD", "reasons": ["the model sees value and the thesis survives scrutiny; recorded as an experiment in whether model-market disagreement predicts"]}
        return {"decision": "LOWER_PRIORITY", "reasons": [f"the model sees value but the contradiction is {contradiction} or the calibration is {cal}"]}
    return {"decision": "LOWER_PRIORITY", "reasons": ["a likely winner at a fair price: a low-variance control sample, no edge"]}


# ----------------------------------------------------------------------------- player props
def review_player(ctx: Context, leg, estimate_basis: str, estimate_used: float) -> dict:
    """Shots, points and goals: the calibrated skater model, recalibrated by what happened to players like this where history is clear, argued against with the player's role, record and game environment."""
    from research.product_models import live
    stat = _STAT.get(leg.market_family)
    k = int(leg.threshold or 1)
    market = f"{stat}>={k}"
    card = _card_shell(leg, "", "PLAYER")
    p = _player(ctx, leg.participant_id)
    home, away = _game_sides(ctx, leg)
    opp = leg.opponent
    if p is None or stat is None:
        card.update({"most_likely_outcome": "unknown: no player state", "probabilities": {"model": None}, "market": market_block(leg, estimate_used, None)})
        card["thesis"] = {"type": "NONE", "statement": "Eggy has no state for this player, so there is no thesis.", "strength": "NONE"}
        card["contradiction"] = {"level": "SEVERE", "flags": [{"name": "NO_PLAYER_STATE", "level": "SEVERE", "text": "the skater model has no current-season state for this player"}]}
        card["calibration_confidence"] = {"level": "INSUFFICIENT"}
        card["data_completeness"] = _completeness([("player state", False)])
        card["decision_confidence"] = "LOW"
        card["action"] = {"decision": "PASS", "reasons": ["no player state: Eggy does not bet a player it cannot assess"]}
        return card
    proj = live.project_matchup(ctx.skater_state, str(leg.participant_id), opp)
    ex = (proj or {}).get("expected") or {}
    probs = (proj or {}).get("probabilities", {}).get(market) or {}
    p_model = probs.get("calibrated")
    games_obs = (proj or {}).get("games_observed") or p["games_total"]
    rows = ctx.by_player.get(str(leg.participant_id)) or []
    hr = _hit_rates(rows, stat, k, _season_start(ctx.as_of)) if rows else None
    grp = "D" if p["position_group"] == "D" else "F"
    mv = team_view(ctx, home, away) if home and away else None
    team = leg.team or p["team"]
    team_p = None
    if mv and mv.get("available"):
        team_p = mv["p_home"] if team == home else mv["p_away"]
    toi_e, pp_e = ex.get("toi"), ex.get("toi_pp")
    cells = analogue_cells(ctx, market, p_model, grp, toi_e or 0.0, pp_e or 0.0, team_p) if (p_model is not None and toi_e is not None) else []
    adj = best_adjustment(cells)
    # The estimate is the calibrated model recalibrated by history where history is clear (either direction); otherwise the leg's own estimate (for shots, the player's record held to the model).
    p_used = estimate_used if (adj is None or p_model is None) else round(min(max(p_model * adj["factor"], 0.001), 0.99), 4)
    implied = implied_from_american(float(leg.american_price))
    edge = p_used - implied
    # --- player facts
    recent = p["recent_games"][:RECENT_GAMES]
    r_toi, r_pp = p["recent_toi"], p["recent_toi_pp"]
    season = p["season_totals"]
    # --- most likely outcome
    likely_yes = p_used >= 0.5
    most_likely = (f"{leg.participant_name} reaches {market_name_short(stat, k)} ({pct(p_used)})" if likely_yes else f"{leg.participant_name} does NOT reach {market_name_short(stat, k)} ({pct(1 - p_used)}); this is a {'longshot' if p_used < 0.25 else 'less-likely-than-not'} bet")
    # --- flags
    flags: list[dict] = []
    if stat == "shots" and leg.empirical_probability is not None and leg.model_probability is not None:
        gap = leg.model_probability - leg.empirical_probability
        if gap >= 0.10:
            flags.append({"name": "MODEL_ABOVE_RECORD", "level": "HIGH", "text": f"model {pct(leg.model_probability)} vs his last-{leg.empirical_games}-game record {pct(leg.empirical_probability)} ({leg.empirical_hits} hits): {pts(gap)}"})
        elif gap >= 0.05:
            flags.append({"name": "MODEL_ABOVE_RECORD", "level": "MODERATE", "text": f"model {pct(leg.model_probability)} vs his record {pct(leg.empirical_probability)}: {pts(gap)}"})
    if stat in ("goals", "points") and hr and hr["career"]["n"] >= 40 and hr["career"]["rate"]:
        ratio = p_model / hr["career"]["rate"] if p_model else None
        if ratio and ratio >= 1.6:
            flags.append({"name": "MODEL_ABOVE_RECORD", "level": "HIGH", "text": f"model {pct(p_model)} is {ratio:.1f}x his own rate over his last {hr['career']['n']} games ({hr['career']['hits']} of {hr['career']['n']}, {pct(hr['career']['rate'])})"})
        elif ratio and ratio >= 1.3:
            flags.append({"name": "MODEL_ABOVE_RECORD", "level": "MODERATE", "text": f"model {pct(p_model)} is {ratio:.1f}x his own rate over his last {hr['career']['n']} games ({pct(hr['career']['rate'])})"})
    if toi_e is not None and r_toi and toi_e - r_toi >= 2.0:
        flags.append({"name": "ROLE_ASSUMPTION", "level": "MODERATE", "text": f"the model expects {toi_e:.1f} min but he has played {r_toi:.1f} lately"})
    if stat in ("goals", "points") and pp_e is not None and r_pp is not None and pp_e - r_pp >= 0.75:
        flags.append({"name": "POWER_PLAY_ASSUMPTION", "level": "MODERATE", "text": f"the model expects {pp_e:.2f} power-play min but he has had {r_pp:.2f} lately"})
    if stat in ("goals", "points") and grp == "F" and p.get("usage_tier") == 4:
        flags.append({"name": "LOW_USAGE", "level": "MODERATE", "text": "inferred fourth-line usage (ice time ranks in the bottom third of his team's forwards)"})
    env_in_estimate = bool(adj and adj["dimension"] == "env")
    if stat in ("goals", "points") and team_p is not None and team_p < 0.35:
        flags.append({"name": "GAME_ENVIRONMENT", "level": "LOW" if env_in_estimate else "MODERATE", "text": f"{team} is a clear underdog tonight ({pct(team_p)} to win): fewer goals for its skaters on average" + (" (history for such teams is already in the estimate)" if env_in_estimate else "")})
    elif stat in ("goals", "points") and team_p is not None and team_p < 0.42:
        flags.append({"name": "GAME_ENVIRONMENT", "level": "LOW", "text": f"{team} is an underdog tonight ({pct(team_p)} to win)"})
    if adj and adj["factor"] < 1.0:
        flags.append({"name": "HISTORY_SAYS_MODEL_RUNS_HIGH", "level": "HIGH" if adj["z"] <= -3 else "MODERATE", "text": f"for {adj['label']} the model averaged {pct(adj['pred'])} and {pct(adj['obs'])} happened ({adj['n']:,} cases, z {adj['z']:+.1f}): the model's {pct(p_model)} is recalibrated to {pct(p_used)}"})
    elif adj:
        flags.append({"name": "HISTORY_SAYS_MODEL_RUNS_LOW", "level": "LOW", "text": f"for {adj['label']} the model averaged {pct(adj['pred'])} and {pct(adj['obs'])} happened ({adj['n']:,} cases, z {adj['z']:+.1f}): the model's {pct(p_model)} is recalibrated up to {pct(p_used)}; part of any gap to the price is the model's, not the market's"})
    if games_obs < 20:
        flags.append({"name": "THIN_HISTORY", "level": "HIGH", "text": f"only {games_obs} games of history"})
    elif games_obs < THIN_PLAYER_GAMES:
        flags.append({"name": "THIN_HISTORY", "level": "MODERATE", "text": f"only {games_obs} games of history"})
    if p_used >= 1.5 * implied and implied < 0.15:
        flags.append({"name": "MODEL_FAR_ABOVE_MARKET", "level": "HIGH" if p_used >= 2.0 * implied else "MODERATE", "text": f"our {pct(p_used)} is {p_used / implied:.1f}x the {pct(implied)} the price implies (that figure already includes the bookmaker's margin, so the market's own chance is lower still)"})
    if edge <= -0.05:
        flags.append({"name": "PRICE_ABOVE_ESTIMATE", "level": "HIGH", "text": f"the price implies {pct(implied)}, {pts(-edge)} above our {pct(p_used)}: the market sees more than we do (or charges a large margin)"})
    longshot = implied <= LONGSHOT_IMPLIED
    if longshot:
        flags.append({"name": "LONGSHOT_TAIL", "level": "MODERATE", "text": f"a price of {leg.american_price:+.0f} implies {pct(implied)}: small errors in a tail probability become large relative edges"})
    # --- calibration confidence
    best_cell = None
    spec = [c for c in cells if c["dimension"] in ("toi", "pp", "grp", "p") and c["n"] >= MIN_CELL_N]
    if spec:
        best_cell = {c["dimension"]: c for c in spec}.get("toi") or {c["dimension"]: c for c in spec}.get("pp") or spec[-1]
    if adj is not None:
        cal = "HIGH" if adj["n"] >= 1000 else "MEDIUM"          # recalibrated to what happened to 300+ (1000+) players like this: the estimate IS the observed rate
    elif best_cell:
        cal = calibration_level(best_cell["n"], best_cell["pred"], best_cell["obs"], best_cell["se"])
    else:
        cal = "INSUFFICIENT"
    if stat == "shots" and cal in ("INSUFFICIENT", "LOW") and leg.empirical_games and leg.empirical_games >= 20 and estimate_basis == "ANCHORED_RECORD_ESTIMATE":
        cal = "MEDIUM"          # the shots estimate is the player's own record held to the model: validated on 2023-2025 (docs/validation/anchor_calibration.json)
    if games_obs < 20:
        cal = "LOW" if cal == "HIGH" else cal
    if longshot and not (cal == "HIGH" and games_obs >= LONGSHOT_MIN_PLAYER_GAMES):
        cal = "LOW" if cal in ("HIGH", "MEDIUM") else cal
    # --- thesis
    if edge >= EDGE_MIN:
        ttype, strength = "MODEL_SEES_VALUE", ("STRONG" if edge >= 2 * EDGE_MIN else "MODERATE")
        statement = (f"{leg.participant_name}'s {market_name_short(stat, k)} is priced as {pct(implied)} but the calibrated model{' (recalibrated by history)' if adj else ''} says {pct(p_used)}: {pts(edge)} at the last observed price"
                     f"{' (the price implies the book margin, so the true gap is larger)' if leg.market_family != 'MONEYLINE' else ''}. It is a longshot that loses most of the time; the case is value, not likelihood." if p_used < 0.25 else
                     f"{leg.participant_name} is likely to reach {market_name_short(stat, k)} ({pct(p_used)}) and the price implies only {pct(implied)}: {pts(edge)}.")
    elif edge > -EDGE_MIN:
        ttype, strength = "FAIR_PRICED", "WEAK"
        statement = f"The price ({pct(implied)}) and our estimate ({pct(p_used)}) are within {pct(EDGE_MIN, 0)} of each other: fair, no edge."
    else:
        ttype, strength = "PRICE_ABOVE_ESTIMATE", "NONE"
        statement = f"The price implies {pct(implied)} but we estimate {pct(p_used)}: the market is paying less than our estimate says it should (EV negative by {pts(-edge)})."
    # --- case for / against
    case_for, case_against = [], []
    case_for.append(f"calibrated model {pct(p_model)} (raw {pct(probs.get('raw'))}); estimate used {pct(p_used)} ({estimate_basis.replace('_', ' ').lower()}) vs price-implied {pct(implied)}")
    case_for.append(f"expected {stat} per game {ex.get(stat, 0):.2f}; season {season.get(stat, 0):g} in {season.get('games', 0)} games; last {len(recent)} {', '.join(str(int(g[stat if stat in ('shots', 'goals') else 'goals'])) for g in recent)} ({stat if stat != 'points' else 'goals'})") if stat != "points" else \
        case_for.append(f"expected points per game {ex.get('points', 0):.2f}; season {season.get('points', 0):g} points in {season.get('games', 0)} games")
    if hr:
        case_for.append(f"reached {market_name_short(stat, k)} in {hr['career']['hits']} of his last {hr['career']['n']} games ({pct(hr['career']['rate'])}), {hr['last30']['hits']} of last {hr['last30']['n']}, {hr['last10']['hits']} of last {hr['last10']['n']}, {hr['season']['hits']} of {hr['season']['n']} this season")
    case_for.append(f"expected {toi_e:.1f} min on ice (recent {r_toi:.1f}), power play {pp_e:.2f} min (recent {r_pp:.2f}); inferred usage tier {p.get('usage_tier')}{' / power-play unit ' + str(p.get('pp_usage')) if p.get('pp_usage') else ''}")
    for c in cells:
        if c["dimension"] in ("toi", "pp", "grp", "p", "env") and c["n"] >= MIN_CELL_N:
            case_for.append(f"history: {c['label']}: model averaged {pct(c['pred'])}, {pct(c['obs'])} happened ({c['n']:,} cases)")
    case_against.extend(f["text"] for f in flags if f["level"] in ("MODERATE", "HIGH", "SEVERE"))
    if mv and mv.get("available") and stat in ("goals", "points", "shots"):
        opp_ind = mv["indicators"].get(opp)
        if opp_ind:
            case_against.append(f"opponent {opp}: expected-goal differential rank {opp_ind['ranks']['xgd']} of 32, goal differential rank {opp_ind['ranks']['gd']}; tonight's win probability for {team}: {pct(team_p)}")
    if not case_against:
        case_against.append("no specific contrary evidence was found; the usual variance of a single game applies")
    # --- historical
    historical = [{"kind": "SKATER_ANALOGUE", **c, "text": f"{c['label']}: the model averaged {pct(c['pred'])}; {pct(c['obs'])} happened ({c['n']:,} cases; 2025-26 and 2026-27 to date)"} for c in cells]
    # --- data
    comp = _completeness([("skater model state", True), ("games of history", games_obs >= 20), ("ice-time history", bool(rows)), ("power-play role (inferred from ice time)", p.get("recent_toi_pp") is not None),
                          ("team model for the game", bool(mv and mv.get("available"))), ("historical analogues", bool(cells)), ("hit-rate record", bool(hr and hr["career"]["n"] >= 20)),
                          ("price observation", True), ("confirmed lineup", False), ("injury news", False)])
    pass_when = [f"his expected ice time falls below {max((toi_e or 0) - 2, 0):.0f} minutes or his role changes", f"the price shortens so that the edge falls under {pct(EDGE_MIN, 0)} (about {american_from_prob(max(p_used - EDGE_MIN, 0.002)):+.0f} or shorter)",
                 "a lineup report that removes him from the power play or the top lines", f"a scratch or injury report for {leg.participant_name}"]
    card.update({
        "most_likely_outcome": most_likely,
        "why_tonight": [f"{leg.team} vs {opp}" + (f"; {team} win probability {pct(team_p)} ({mv['gap'].lower()} gap, favourite {mv['favourite']})" if mv and mv.get("available") else ""),
                        f"{leg.participant_name} ({p['position']}) is expected to play {toi_e:.1f} min, {pp_e:.2f} on the power play"],
        "player_view": {"position": p["position"], "games_total": p["games_total"], "usage_tier": p.get("usage_tier"), "pp_usage": p.get("pp_usage"), "season": season, "expected": ex, "recent_toi": round(r_toi, 2),
                        "recent_pp": round(r_pp, 2), "last_games": recent, "hit_rates": hr},
        "hockey_view": ({"available": True, "favourite": mv["favourite"], "p_favourite": mv["p_favourite"], "gap": mv["gap"], "team_win_probability": team_p, "data_through": mv["data_through"], "model": mv["model"]} if mv and mv.get("available") else {"available": False}),
        "probabilities": {"model": p_model, "model_raw": probs.get("raw"), "estimate_used": p_used, "leg_estimate_before_recalibration": round(estimate_used, 4), "estimate_basis": estimate_basis, "recalibration": adj},
        "market": market_block(leg, p_used, p_model),
        "why_they_disagree": _why_disagree_prop(leg, p_used, implied, edge, adj, stat),
        "case_for": case_for, "case_against": case_against, "historical": historical,
        "model_state": {"skater_model": "player-rate-toi-v2 + Platt calibration", "skater_state_through": ctx.skater_state.get("newest_game_date"), "team_model": (mv or {}).get("model"), "evidence_version": (ctx.evidence or {}).get("version"),
                        "estimate_basis": estimate_basis},
        "pass_conditions": pass_when,
        "claims": [{"id": "expected_toi", "text": "expected ice time", "value": toi_e}, {"id": "expected_pp", "text": "expected power-play minutes", "value": pp_e}, {"id": "expected_stat", "text": f"expected {stat}", "value": ex.get(stat)},
                   {"id": "team_win_probability", "text": f"{team} to win", "value": team_p}],
    })
    thesis = {"type": ttype, "statement": statement, "strength": strength}
    _finish(card, flags, cal, comp, thesis, {})
    card["calibration_confidence"] = {"level": cal, "evidence": best_cell, "note": "longshot: needs HIGH calibration and a 100-game record" if longshot else None}
    card["decision_confidence"] = decision_confidence(card["contradiction"]["level"], cal, comp["fraction"])
    card["action"] = _prop_action(ttype, flags, cal, comp["fraction"], p_used, edge, longshot, games_obs)
    return card


def market_name_short(stat: str, k: int) -> str:
    return {"shots": f"{k}+ shots on goal", "goals": "1+ goals (anytime goal scorer)", "points": f"{k}+ points"}.get(stat, f"{k}+ {stat}")


def _why_disagree_prop(leg, p_used: float, implied: float, edge: float, adj: dict | None, stat: str) -> list[str]:
    if abs(edge) < EDGE_MIN:
        return ["They do not disagree by more than the engine's 3-point threshold."]
    if edge > 0:
        out = [f"our estimate is {pts(edge)} above what the price implies; the model prices opportunity from ice time, power play and recent rates, the market prices the player's name, the line and the opponent as a whole"]
        out.append("a one-sided market's price includes the bookmaker's margin, which makes the true gap larger, not smaller" if leg.market_family != "MONEYLINE" else "")
    else:
        out = [f"the price implies {pts(-edge)} more than our estimate: either the market knows something (role, matchup, news) or its margin is large on this line"]
    if adj:
        out.append(f"history lowered our estimate: {adj['label']} ({adj['n']:,} cases, the model averaged {pct(adj['pred'])}, {pct(adj['obs'])} happened)")
    return [o for o in out if o]


def _prop_action(ttype: str, flags: list[dict], cal: str, completeness: float, p_used: float, edge: float, longshot: bool, games_obs: int) -> dict:
    """The decision rules for a player prop, in order. Each names the quantity it rests on; the p >= 25% and edge >= -10 pts limits for the 'market sees more' experiment are design choices (below
    them the outcome is too rare to teach anything, or the price is so far above our estimate that the margin, not information, explains it)."""
    contradiction = combine_contradiction(flags)
    if contradiction == "SEVERE":
        return {"decision": "PASS", "reasons": ["severe contradiction"]}
    if cal == "INSUFFICIENT" or completeness < 0.5 or games_obs < 20:
        return {"decision": "PASS", "reasons": ["too little evidence to judge (no calibration analogue, missing data or a thin record)"]}
    if ttype == "MODEL_SEES_VALUE":
        if contradiction in ("LOW", "MODERATE") and cal in ("HIGH", "MEDIUM") and not (longshot and not (cal == "HIGH" and games_obs >= LONGSHOT_MIN_PLAYER_GAMES)):
            return {"decision": "RECORD", "reasons": ["the estimate beats the price by the engine's threshold and the thesis survives scrutiny"]}
        return {"decision": "LOWER_PRIORITY", "reasons": [f"an edge exists but the contradiction is {contradiction} and the calibration {cal}{' (a longshot needs HIGH calibration and a 100-game record)' if longshot else ''}"]}
    if ttype == "FAIR_PRICED":
        if contradiction in ("LOW", "MODERATE") and cal in ("HIGH", "MEDIUM"):
            return {"decision": "LOWER_PRIORITY", "reasons": ["estimate and price agree and the estimate is well calibrated: a calibration control, no edge"]}
        return {"decision": "PASS", "reasons": [f"no edge, and the contradiction is {contradiction} / calibration {cal}: nothing to learn from"]}
    # PRICE_ABOVE_ESTIMATE: the market sees more than the estimate does
    ex_price = combine_contradiction([f for f in flags if f["name"] not in ("PRICE_ABOVE_ESTIMATE", "MODEL_FAR_ABOVE_MARKET")])
    if p_used >= 0.25 and edge >= -0.10 and cal in ("HIGH", "MEDIUM") and ex_price in ("LOW", "MODERATE"):
        return {"decision": "LOWER_PRIORITY", "reasons": ["the market prices this above our estimate by a modest amount and the estimate is well calibrated: an experiment in whether the model underestimates this kind of player (a negative-edge learning sample)"]}
    return {"decision": "PASS", "reasons": ["the price is far above our estimate (or the outcome too rare, or the estimate too uncertain) to learn from"]}


# ----------------------------------------------------------------------------- goalie saves
def review_saves(ctx: Context, leg, estimate_basis: str, estimate_used: float) -> dict:
    card = _card_shell(leg, "", "GOALIE_SAVES")
    implied = implied_from_american(float(leg.american_price))
    edge = estimate_used - implied
    card.update({"most_likely_outcome": f"{leg.participant_name} makes {leg.threshold}+ saves ({pct(estimate_used)})" if estimate_used >= 0.5 else f"{leg.participant_name} makes fewer than {leg.threshold} saves ({pct(1 - estimate_used)})",
                 "probabilities": {"model": round(estimate_used, 4), "estimate_used": round(estimate_used, 4), "estimate_basis": estimate_basis}, "market": market_block(leg, estimate_used, estimate_used),
                 "case_for": [f"goalie model {pct(estimate_used)} vs price-implied {pct(implied)}"], "case_against": ["the starter is not confirmed by an official feed (Eggy's feed is off); the adapter's own starter gate still applies"],
                 "historical": [], "why_tonight": [f"{leg.team} vs {leg.opponent}"], "why_they_disagree": [], "pass_conditions": ["the starter changes"], "model_state": {"goalie_model": "goalie-team-v1"}, "claims": []})
    card["thesis"] = {"type": "FAIR_PRICED" if abs(edge) < EDGE_MIN else ("MODEL_SEES_VALUE" if edge > 0 else "PRICE_ABOVE_ESTIMATE"), "statement": f"estimate {pct(estimate_used)} vs price {pct(implied)} ({pts(edge)})", "strength": "WEAK"}
    card["contradiction"] = {"level": "MODERATE", "flags": [{"name": "STARTER_UNCONFIRMED", "level": "MODERATE", "text": "starting goalie not officially confirmed"}]}
    card["calibration_confidence"] = {"level": "LOW"}
    card["data_completeness"] = _completeness([("goalie model", True), ("confirmed starter", False)])
    card["decision_confidence"] = "LOW"
    card["action"] = {"decision": "LOWER_PRIORITY" if edge >= EDGE_MIN else "PASS", "reasons": ["goalie saves are reviewed lightly (no historical analogue tables yet): at most lower priority"]}
    return card


# ------------------------------------------------------------------------------------------------ the one-glance answers
def standard_answers(card: dict) -> dict:
    """The answers a reader should get from ANY bet at a glance (owner standard 2026-10-21): why this team or player, why tonight, why this market, why this price, who is most likely to win, what the model
    believes, what the market believes, why they disagree, what supports the bet, what argues against it, what history says, what data is missing, and what would make Eggy pass. Every answer is assembled from
    the card's own fields (nothing new is computed here), so the card and its summary can never disagree."""
    mk = card.get("market") or {}
    pr = card.get("probabilities") or {}
    bet = card.get("bet") or {}
    thesis = (card.get("thesis") or {}).get("statement") or "no thesis"
    kind = card.get("kind")
    price = bet.get("price")
    implied = mk.get("implied_with_margin")
    be = mk.get("breakeven_price_for_3pt_edge")
    price_text = None if price is None or implied is None else (f"{price:+.0f} implies {pct(implied)}"
                                                                  + (f"; with the bookmaker's margin removed the market says {pct(mk['no_vig'])}" if mk.get("no_vig") is not None else ", and a one-sided price INCLUDES the bookmaker's margin, so the market's own chance is lower")
                                                                  + (f". A 3-point edge on our estimate would need a price near {be:+.0f}" if be is not None else ""))
    hv = card.get("hockey_view") or {}
    if kind == "MONEYLINE":
        why_market = ("The moneyline is a two-sided market, so the bookmaker's margin can be removed and the market's own probability compared with the hockey model's. It is the one market where Eggy's team model "
                      "speaks directly (it has not been shown to beat the market).")
        believes = None if pr.get("model") is None else f"{pct(pr['model'])} for {bet.get('player_or_team')} (hockey model {hv.get('model', '')}; a {str(hv.get('gap', '')).lower()} gap in favour of {hv.get('favourite')})"
        mkt = None if pr.get("market_no_vig") is None else f"{pct(pr['market_no_vig'])} for {bet.get('player_or_team')} (no-vig from both sides; {pct(implied)} with the margin)"
        who = card.get("most_likely_outcome")
    elif kind == "PLAYER":
        fam = bet.get("market_family")
        why_market = {"PLAYER_SOG_ALTERNATE": "A shots threshold depends on ice time and shot volume, which are steadier from game to game than scoring, so it is the skater market Eggy can estimate with the least noise. Only the Over side is posted, so the price includes the margin.",
                      "PLAYER_SOG": "A shots threshold depends on ice time and shot volume, which are steadier from game to game than scoring.",
                      "PLAYER_GOALS": "An anytime-goal market asks for one rare event, so a single game says little; the goal model was calibrated on held-out seasons, including the tails, but the edge it can show is small against the price's margin.",
                      "PLAYER_POINTS": "Points depend on goals and assists, rarer and noisier than shots, and on the power-play role."}.get(fam, "This market is reviewed lightly.")
        pv = card.get("player_view") or {}
        ex = pv.get("expected") or {}
        believes = None if pr.get("model") is None else (f"{pct(pr['model'])} from the calibrated skater model; estimate used {pct(pr.get('estimate_used'))}"
                                                          + (f" (history recalibrated it: {pr['recalibration']['label']})" if pr.get("recalibration") else "")
                                                          + (f"; expects {ex.get('toi'):.1f} min on ice, {ex.get('toi_pp'):.2f} on the power play" if ex.get("toi") is not None and ex.get("toi_pp") is not None else ""))
        mkt = None if implied is None else f"{pct(implied)} implied by {price:+.0f}, margin included"
        who = (card.get("most_likely_outcome") or "") + (f". In the game, {hv['favourite']} is the likelier winner ({pct(hv['p_favourite'])})" if hv.get("available") and hv.get("favourite") else "")
    else:
        why_market = "This market is reviewed lightly."
        believes = None if pr.get("model") is None else f"{pct(pr['model'])}"
        mkt = None if implied is None else f"{pct(implied)} implied, margin included"
        who = card.get("most_likely_outcome")
    team_or_player = thesis
    pv = card.get("player_view")
    if pv:
        team_or_player += f" Role: {pv.get('position')}, expected {((pv.get('expected') or {}).get('toi') or 0):.1f} min on ice, usage tier {pv.get('usage_tier')}, power-play unit {pv.get('pp_usage') or 'none inferred'}."
    dc = card.get("data_completeness") or {}
    return {"why_this_team_or_player": team_or_player, "why_tonight": "; ".join(card.get("why_tonight") or []) or "no game context", "why_this_market": why_market,
            "why_this_price": price_text or "no price", "who_is_most_likely_to_win": who or "unknown", "what_the_model_believes": believes or "no model view", "what_the_market_believes": mkt or "no market view",
            "why_they_disagree": " ".join(card.get("why_they_disagree") or []) or "no disagreement to explain", "what_supports_it": card.get("case_for") or [], "what_argues_against_it": card.get("case_against") or [],
            "what_history_says": [h.get("text") for h in card.get("historical") or [] if h.get("text")] or ["no historical analogue for this bet"], "what_data_is_missing": dc.get("missing") or [],
            "what_would_make_eggy_pass": card.get("pass_conditions") or []}


# ------------------------------------------------------------------------------------------------ entry point
def review_leg(ctx: Context, leg, estimate_basis: str, estimate_used: float) -> dict:
    """The review card for one candidate. Never raises: a failure is a PASS card that says so (the cohort never records a bet it could not review)."""
    try:
        if leg.market_family == "MONEYLINE":
            card = review_moneyline(ctx, leg, estimate_basis, estimate_used)
        elif leg.market_family in _STAT:
            card = review_player(ctx, leg, estimate_basis, estimate_used)
        elif leg.market_family == "GOALIE_SAVES":
            card = review_saves(ctx, leg, estimate_basis, estimate_used)
        else:
            raise ValueError(f"no review for market family {leg.market_family}")
        pr = card.get("probabilities") or {}
        card["estimate_used"] = pr.get("estimate_used") if pr.get("estimate_used") is not None else (pr.get("estimate_used_for_edge") if pr.get("estimate_used_for_edge") is not None else round(estimate_used, 4))
        card["model_probability"] = pr.get("model")
        card["answers"] = standard_answers(card)
        return card
    except Exception as exc:  # noqa: BLE001
        card = _card_shell(leg, "", "ERROR")
        card.update({"most_likely_outcome": "unknown", "thesis": {"type": "NONE", "statement": f"the review failed: {exc.__class__.__name__}: {exc}", "strength": "NONE"},
                     "contradiction": {"level": "SEVERE", "flags": [{"name": "REVIEW_FAILED", "level": "SEVERE", "text": f"{exc.__class__.__name__}: {exc}"}]}, "calibration_confidence": {"level": "INSUFFICIENT"},
                     "data_completeness": {"present": [], "missing": ["review"], "k": 0, "n": 1, "fraction": 0.0}, "decision_confidence": "LOW", "case_for": [], "case_against": [str(exc)], "historical": [],
                     "action": {"decision": "PASS", "reasons": ["the review could not be completed"]}})
        card["answers"] = standard_answers(card)
        return card


# ------------------------------------------------------------------------------------------------ ranking
EDGE_ORDER = {"POSITIVE_EDGE": 0, "NEUTRAL_UNCERTAIN_EDGE": 1, "LEARNING_SAMPLE_NEGATIVE_EDGE": 2}
POSITIVE, NEUTRAL, NEGATIVE = "POSITIVE_EDGE", "NEUTRAL_UNCERTAIN_EDGE", "LEARNING_SAMPLE_NEGATIVE_EDGE"


def edge_for_class(card: dict) -> tuple[float, str]:
    """(the edge the class is judged on, what it means). A prop: the estimate used less the price's implied probability. A moneyline: the hockey model's probability less the price's implied
    probability (the market-anchored estimate would always read as the bookmaker's margin, a quantity that scales with the price and says nothing about the hockey)."""
    mk = card.get("market") or {}
    if card.get("kind") == "MONEYLINE" and mk.get("model_edge_vs_price") is not None:
        return mk["model_edge_vs_price"], "hockey model less price (unvalidated against the market)"
    return mk.get("edge_vs_price", 0.0), "estimate less price"


def edge_class_of(card: dict) -> tuple[str, str]:
    """POSITIVE_EDGE / NEUTRAL_UNCERTAIN_EDGE / LEARNING_SAMPLE_NEGATIVE_EDGE with its reason. POSITIVE needs an edge of 3+ points AND a calibration of at least MEDIUM (an edge on an estimate nobody can
    vouch for is uncertain, not positive); NEGATIVE is an edge of -3 points or worse; anything between is NEUTRAL."""
    edge, what = edge_for_class(card)
    cal = (card.get("calibration_confidence") or {}).get("level")
    if edge <= -EDGE_MIN:
        return NEGATIVE, f"{what}: {pts(edge)}, below -{EDGE_MIN * 100:.0f} points: recorded as a learning sample"
    if edge >= EDGE_MIN:
        if cal in ("HIGH", "MEDIUM"):
            return POSITIVE, f"{what}: {pts(edge)} with {cal} calibration"
        return NEUTRAL, f"{what}: {pts(edge)} but the calibration is {cal}, so the edge is uncertain"
    return NEUTRAL, f"{what}: {pts(edge)}, within {EDGE_MIN * 100:.0f} points of zero"


def rank_key(card: dict, edge_class: str, estimate: float, edge: float, key: str) -> tuple:
    """Lexicographic: action, edge class, contradiction, calibration confidence, hit chance (to the nearest point), edge, then the bet's key (so ties are deterministic). No weights: nothing is traded off against
    anything else. Hit chance comes before edge because, among bets of the same quality, the likelier event is the better learning sample (less variance per bet), and edges within a class differ by noise."""
    return (ACTIONS.index(card["action"]["decision"]), EDGE_ORDER.get(edge_class, 3), _level_index(card["contradiction"]["level"]), CAL_LEVELS.index(card["calibration_confidence"]["level"]), -round(estimate, 2), -round(edge, 4), key)


def compact(card: dict) -> dict:
    """The card as stored with the bet: everything, with floats rounded so the JSON stays small and stable."""
    def r(o):
        if isinstance(o, float):
            return round(o, 4)
        if isinstance(o, dict):
            return {k: r(v) for k, v in o.items()}
        if isinstance(o, list):
            return [r(v) for v in o]
        return o
    return r(card)
