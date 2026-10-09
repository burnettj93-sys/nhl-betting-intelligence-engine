"""
A short, honest "why this selection" line for a leg on an option or ticket card, built only from the published product
state (the player's or goalie's record there): role, recent production and sample, what the model expects, the price and
the main uncertainty. It never changes a probability or a selection; it only says what the evidence is.
"""
from __future__ import annotations

from operational.pricing_policy import MIN_GAMES_FOR_PRICING

SKATER_STAT = {"PLAYER_SOG_ALTERNATE": "shots", "PLAYER_SOG": "shots", "PLAYER_POINTS": "points", "PLAYER_GOALS": "goals"}
STAT_WORD = {"shots": "shots", "points": "points", "goals": "goals"}
SMALL_SAMPLE_GAMES = 60          # above the pricing floor but still short; the card names it as an uncertainty


def _games_label(n: int) -> str:
    return f"{n} NHL game{'s' if n != 1 else ''}"


def skater_context(player: dict, leg: dict) -> dict | None:
    stat = SKATER_STAT.get(leg.get("market_family"))
    if stat is None or not player:
        return None
    pr = player.get("projection") or {}
    games = pr.get("games_observed", player.get("games_total", 0))
    season = player.get("season") or {}
    recent = player.get("recent_games") or []
    recent_n = len(recent)
    reported = player.get("reported") or {}
    pp_word = {1: "high", 2: "some"}.get(player.get("pp_usage"))
    games_used = player.get("role_games")
    basis = f"inferred from ice time in his last {games_used} game(s)" if games_used else "inferred from ice time"
    if player.get("usage_tier"):
        role = (f"Estimated {('defense' if player.get('position') == 'D' else 'forward')} usage tier {player['usage_tier']}, "
                + (f"estimated power-play usage: {pp_word}" if pp_word else "no regular power-play time") + f" ({basis}; an estimate, not an assigned line or PP unit)")
    else:
        role = "Role not established"
    lines = [role]
    if reported.get("status", "REPORTED") == "REPORTED" and (reported.get("line") or reported.get("pp")):
        lines.append(f"Reported by {reported.get('reported_by') or reported.get('source') or 'a lineup source'}: line {reported.get('line') or '—'}, PP unit {reported.get('pp') or '—'}")
    else:
        lines.append("No reported line or PP unit (no permitted lineup source is connected)")
    if season.get("games"):
        lines.append(f"This season {season['games']} GP: {int(season.get('goals', 0))} G, {int(season.get('assists', 0))} A, {int(season.get('shots', 0))} shots")
    if recent_n:
        field = {"shots": "shots", "goals": "goals"}.get(stat)
        if stat == "points":
            total = sum(int(r["goals"]) + int(r["assists"]) for r in recent)
        else:
            total = sum(int(r[field]) for r in recent)
        lines.append(f"Last {recent_n} games: {total} {STAT_WORD[stat]}")
    exp = (pr.get("expected") or {}).get(stat)
    if exp is not None:
        lines.append(f"Model expects {exp:.2f} {STAT_WORD[stat]} (ice time {pr['expected']['toi']:.0f} min)")
    lines.append(f"Sample: {_games_label(int(games))} in the model history (2022-23 onward)")
    if games < MIN_GAMES_FOR_PRICING:
        risk = f"Fewer than {MIN_GAMES_FOR_PRICING} NHL games: the model over-predicts this much history, so this is not a confident recommendation."
    elif games < SMALL_SAMPLE_GAMES:
        risk = f"Only {games} NHL games of history: estimates are less certain than for established players."
    else:
        risk = "Main uncertainty: ordinary single-game variance; the model chance is a calibrated estimate, not a guarantee."
    return {"lines": lines, "uncertainty": risk, "games": int(games), "low_sample": games < MIN_GAMES_FOR_PRICING}


def goalie_context(goalie: dict, leg: dict) -> dict | None:
    if leg.get("market_family") != "GOALIE_SAVES" or not goalie:
        return None
    pr = goalie.get("projection") or {}
    season = goalie.get("season") or {}
    lines = []
    if season.get("games"):
        lines.append(f"This season {season['games']} GP ({season.get('starts', '?')} starts), SV% {season.get('save_pct')}, GAA {season.get('gaa')}")
    if pr.get("expected_saves") is not None:
        lines.append(f"Model expects {pr['expected_saves']} saves on {pr['expected_shots_against']} shots against")
    conf = goalie.get("confirmation") or {}
    start = goalie.get("start") or {}
    if str(conf.get("status", "")).upper() == "CONFIRMED":
        lines.append("Starter confirmed by a recognised source")
        risk = "Main uncertainty: shot volume in a single game."
    else:
        p = start.get("probability")
        lines.append("Starter NOT confirmed" + (f" (estimated {p:.0%} to start)" if p is not None else ""))
        risk = "Starter not confirmed: if another goalie starts this selection has no action or loses its basis."
    return {"lines": lines, "uncertainty": risk, "games": int((pr.get("sample") or {}).get("games", 0)), "low_sample": False}


def for_leg(leg: dict, players: dict, goalies: dict) -> dict | None:
    pid = str(leg.get("participant_id"))
    if leg.get("market_family") == "GOALIE_SAVES":
        return goalie_context(goalies.get(pid), leg)
    return skater_context(players.get(pid), leg)
