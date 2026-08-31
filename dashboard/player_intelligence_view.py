"""
Player Intelligence business logic (Preseason Interactive Product
sprint, Parts 38-63). Reuses dashboard/demo_data.py's real-model-output
architecture -- nothing here computes a new probability; it only
assembles/groups the real demo opportunities plus real actual-vs-
expected history for display.
"""
from __future__ import annotations

import statistics

from dashboard import demo_data as dd
from research.player_context_state import context_state as cs

NEXT5_OPPONENT_POOL = ["CGY", "SEA", "OTT", "PIT", "STL", "ANA", "NSH", "BUF"]


def find_player(player_id: str):
    for p in dd.build_demo_roster():
        if p.player_id == player_id:
            return p
    return None


def player_opportunities(player_id: str) -> list[dict]:
    return [o for o in dd.build_demo_opportunities() if o["player_id"] == player_id]


def group_opportunities(opps: list[dict]) -> dict[str, list[dict]]:
    """Part 47: Best Opportunities / Watchlist / Waiting / Passes."""
    groups = {"BEST": [], "WATCHLIST": [], "WAITING": [], "PASSES": []}
    for o in opps:
        if o["decision"] == "BET":
            groups["BEST"].append(o)
        elif o["decision"] == "WATCH":
            groups["WATCHLIST"].append(o)
        elif o["decision"] == "WAIT":
            groups["WAITING"].append(o)
        else:
            groups["PASSES"].append(o)
    return groups


def hero_summary(opps: list[dict]) -> dict | None:
    """Part 39: best available market, or NONE if nothing qualifies --
    never force a positive recommendation."""
    candidates = [o for o in opps if o["decision"] in ("BET", "WATCH")]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o["conservative_edge"])


def next_five_games(player) -> list[dict]:
    """Part 42/43: DEMO mode uses a deterministic SIMULATED schedule
    (real 2026-27 games beyond the one simulated slate date don't exist
    yet). Market prices for these future games are explicitly NOT
    POSTED -- never fabricated that far in advance (Part 42)."""
    rng = dd._rng_for(player.player_id, "next5")
    games = []
    date_cursor = dd.SIMULATED_DATE
    import datetime as _dt
    d = _dt.date.fromisoformat(date_cursor)
    # Sample opponents without replacement (Track 10: demo realism polish)
    # -- a real NHL team doesn't play the same opponent three times in an
    # isolated 5-game stretch; the pool has 8 teams, comfortably >= 5.
    opponents = list(NEXT5_OPPONENT_POOL)
    rng.shuffle(opponents)
    for i in range(5):
        d = d + _dt.timedelta(days=rng.choice([1, 2, 2, 3]))
        is_home = rng.random() > 0.5
        games.append({"date": d.isoformat(), "opponent": opponents[i],
                      "home_away": "HOME" if is_home else "AWAY",
                      "market_price": "NOT POSTED"})
    return games


def actual_vs_expected(player_id: str, prop: str = "sog", n: int = 5) -> dict | None:
    """Part 52: REAL recent actual production vs REAL model expectation
    -- both computed from the real corpus, nothing simulated here. Uses
    the player's real history strictly before the simulated slate date."""
    stack = dd._demo_context()
    engine = getattr(stack.ctx, prop)
    history = engine.index.history_as_of(player_id, dd.SIMULATED_DATE)
    if len(history) < n:
        return None
    recent = history[-n:]
    actual_total = sum(h[prop] for h in recent)
    from research.player_sog import features as pf
    baseline_rate = pf.rolling_mean(history, prop, 20) or 0
    expected_total = baseline_rate * n
    return {"n": n, "actual": actual_total, "expected": round(expected_total, 1),
            "residual": round(actual_total - expected_total, 1)}


def multi_window_trend(player_id: str, prop: str) -> dict:
    """Part 53: last-5 / last-10 / season rolling means for the given
    real stat, from real history."""
    stack = dd._demo_context()
    engine = getattr(stack.ctx, prop) if prop != "toi" else stack.ctx.sog
    history = engine.index.history_as_of(player_id, dd.SIMULATED_DATE)
    field = "icetime_seconds" if prop == "toi" else prop
    from research.player_sog import features as pf
    return {
        "last_5": pf.rolling_mean(history, field, 5),
        "last_10": pf.rolling_mean(history, field, 10),
        "season": pf.rolling_mean(history, field, len(history)) if history else None,
    }


def context_evidence(player_id: str, team: str, opponent: str) -> dict | None:
    """Part 56: recompute the SAME real evidence (form ratio, TOI ratio,
    frozen cutoffs) that determined the player's context state -- for
    the transparency panel, not a new signal."""
    stack = dd._demo_context()
    from dashboard import data_access as da
    results = da.load_json_safely("research/context_overlay_results.json")
    if results is None:
        return None
    out = {}
    for prop in ("goals", "points"):
        engine = getattr(stack.ctx, prop)
        history = engine.index.history_as_of(player_id, dd.SIMULATED_DATE)
        if len(history) < 10:
            continue
        from research.player_sog import features as pf
        baseline_rate = pf.rolling_mean(history, prop, 20)
        recent_rate = pf.rolling_mean(history, prop, 5)
        baseline_toi = pf.rolling_mean(history, "icetime_seconds", 20)
        recent_toi = pf.rolling_mean(history, "icetime_seconds", 10)
        form_ratio = cs.form_log_ratio(recent_rate, baseline_rate)
        toi_ratio = cs.toi_log_ratio(recent_toi, baseline_toi)
        cutoffs = results["props"][prop]
        eligible = (form_ratio is not None and form_ratio <= cutoffs["cold_cutoff"] and
                    toi_ratio is not None and toi_ratio <= cutoffs["toi_decline_cutoff"])
        out[prop] = {"form_ratio": form_ratio, "toi_ratio": toi_ratio,
                     "cold_cutoff": cutoffs["cold_cutoff"], "toi_decline_cutoff": cutoffs["toi_decline_cutoff"],
                     "eligible": eligible}
    return out or None
