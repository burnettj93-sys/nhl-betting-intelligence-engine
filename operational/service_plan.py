"""
What the morning workflow costs in odds credits, what the existing allowance buys instead, and the smallest budget that meets the requirement.

Pure arithmetic on stated costs; no network call, nothing is purchased. The costs are the provider's own (docs/ODDS_BUDGET_CONFIGURATIONS.md, verified from archived
`x-requests-last` headers):

  * the league-wide moneyline call: 1 credit, however many games it returns (and it returns tomorrow's posted lines too)
  * one per-game player-props call: 1 credit for each market that RETURNS data (shots, points, anytime goals, saves); a market the bookmaker has not posted costs 0
  * the events listing: 0 credits

The requirement (owner, 2026-10-09): an initial update around 8 AM Eastern with the day's games, updated statistics, available DraftKings prices and provisional
recommendations; prices and recommendations refreshed during the day; a re-check before any automatic ticket is recorded; early prices for tomorrow, refreshed.
"MINIMUM" below is the cheapest design that does all of that for EVERY game: a morning look, a daytime look and the pregame price, each of every market the bookmaker has
posted at that hour, plus the moneyline pulls and tomorrow's check. Anything cheaper covers fewer games or fewer looks, and is listed as reduced service.
"""
from __future__ import annotations

import datetime as dt

# the provider's published tiers (docs/ODDS_BUDGET_CONFIGURATIONS.md, https://the-odds-api.com): (name, credits per month, USD per month)
TIERS = [("Starter (free)", 500, 0), ("20K", 20_000, 30), ("100K", 100_000, 59), ("5M", 5_000_000, 119), ("15M", 15_000_000, 249)]
DAYS_PER_MONTH = 30
CLUSTERS_PER_DAY = 3          # T-35 moneyline decision pulls per game day (observed 2-5)
RESERVE = 20

# markets posted at each look, by the evidence on file (docs/MORNING_WORKFLOW.md): shots and points in the morning, goals by midday, saves in the last hours
LOOKS = {
    "minimum": [("Morning look", ("shots", "points")), ("Daytime look", ("shots", "points")), ("Pregame price", ("shots", "points", "goals", "saves"))],
    "fuller": [("Morning look", ("shots", "points", "goals")), ("Daytime look", ("shots", "points", "goals")), ("Pregame price", ("shots", "points", "goals", "saves")),
               ("Last refresh before puck drop", ("shots", "points", "goals"))],
}
MONEYLINE_DISPLAY = {"minimum": 2, "fuller": 4}      # league-wide display pulls a day (one is the morning pull that also carries tomorrow's lines)
TOMORROW_CHECK = 1


def smallest_tier(credits_per_month: float) -> dict:
    need = credits_per_month + RESERVE
    for name, credits, usd in TIERS:
        if need <= credits:
            return {"name": name, "credits_per_month": credits, "usd_per_month": usd, "headroom_x": round(credits / max(credits_per_month, 1), 1)}
    return {"name": "none listed", "credits_per_month": None, "usd_per_month": None, "headroom_x": None}


def level(key: str, name: str, n_games: float) -> dict:
    looks = LOOKS[key]
    per_game = sum(len(m) for _, m in looks)
    per_day = n_games * per_game + CLUSTERS_PER_DAY + MONEYLINE_DISPLAY[key] + TOMORROW_CHECK
    per_month = per_day * DAYS_PER_MONTH
    return {"key": key, "name": name, "looks": [{"look": l, "markets": list(m), "credits_per_game": len(m)} for l, m in looks],
            "credits_per_game": per_game, "moneyline_per_day": CLUSTERS_PER_DAY + MONEYLINE_DISPLAY[key], "tomorrow_check_per_day": TOMORROW_CHECK,
            "credits_per_day_average": round(per_day, 1), "credits_per_month": round(per_month), "smallest_tier": smallest_tier(per_month),
            "fits_free_allowance": per_month + RESERVE <= TIERS[0][1]}


def under_allowance(D: float, games: int) -> dict:
    """What today's allowance buys, from the planner's own waterfall."""
    from operational import credit_planner as cp
    base = dt.datetime(2026, 1, 1, 23, 0, tzinfo=dt.timezone.utc)
    starts = {f"g{i}": base + dt.timedelta(minutes=20 * i) for i in range(games)}
    a = cp.allocate(D, starts)
    return {"games": games, "credits": D, "morning_games": len(a["morning_games"]), "morning_markets": a["morning_markets"], "pregame_games": len(a["games_priced"]),
            "goals_games": len(a["goals_games"]), "tomorrow_check": a["allowance"][cp.TOMORROW] > 0, "midday_refresh_credits": a["allowance"][cp.REFRESH],
            "morning_coverage_pct": round(100 * len(a["morning_games"]) / games) if games else 0,
            "pregame_coverage_pct": round(100 * len(a["games_priced"]) / games) if games else 0}


def report(*, games_per_day: float = 6.8, busiest_night: int = 16, listed_days: int = 15, remaining: int | None = None, days_left: float | None = None,
           D: float | None = None) -> dict:
    """The published block (Diagnostics and docs/MORNING_WORKFLOW.md). `D` is today's day budget under the existing allowance."""
    lv = [level("minimum", "Minimum that meets the requirement", games_per_day), level("fuller", "Fuller service (adds a last refresh and anytime goals earlier)", games_per_day)]
    out = {"inputs": {"games_per_game_day": games_per_day, "busiest_night": busiest_night, "listed_game_days": listed_days, "days_per_month": DAYS_PER_MONTH,
                      "clusters_per_day": CLUSTERS_PER_DAY, "reserve": RESERVE,
                      "costs": "league moneyline call 1 credit; per-game props 1 credit per market that returns data (an unposted market costs 0); events listing 0"},
           "tiers": [{"name": n, "credits_per_month": c, "usd_per_month": u} for n, c, u in TIERS],
           "levels": lv, "minimum_budget": {"credits_per_month": lv[0]["credits_per_month"], "credits_per_day_average": lv[0]["credits_per_day_average"],
                                            "busiest_night_credits": round(busiest_night * lv[0]["credits_per_game"] + CLUSTERS_PER_DAY + MONEYLINE_DISPLAY["minimum"] + TOMORROW_CHECK),
                                            "smallest_provider_tier": lv[0]["smallest_tier"]}}
    if D is not None:
        out["existing_allowance"] = {"remaining": remaining, "days_left": days_left, "credits_per_day": D, "month_total": 500,
                                     "meets_minimum": False,
                                     "reduced_service": [under_allowance(D, g) for g in (4, round(games_per_day), busiest_night)]}
    return out
