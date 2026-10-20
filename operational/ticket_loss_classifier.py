"""Why did a losing paper ticket lose? Classify every missed leg of every losing real-market paper ticket from REAL signals, then say what (if anything) to do about it.

Owner policy (2026-10-20): a losing paper ticket, a losing day or a losing streak is NOT a reason to pause recording. The paper system exists to generate live recommendations, record them, settle them, measure the
results and learn WHY a miss happened (variance, calibration, stale context, a bad or moved price, a role change, an identity problem, dependence). Recording pauses only for a true operational integrity defect:
stale data treated as fresh, an identity mapping failure that affects the ticket's truth, broken price ingestion, schema or data corruption, duplicated tickets, accounting corruption, or a model pipeline failure
that makes outputs untrustworthy.

What this module does:
  * classifies each missed leg with operational/daily_postmortem.classify_failure() (the 13-category taxonomy), supplying only signals that exist: the leg's frozen quote age and freshness status at entry, the
    official box score (did the player's team match the ticket; his ice time against his own recent average; his team's shots against its recent average). A signal that cannot be computed is reported as not
    evaluated; with nothing wrong in everything that WAS evaluated, a miss is NORMAL_VARIANCE (a 62% leg misses 38% of the time); with too little evaluated it is UNKNOWN. No cause is ever guessed.
  * assigns a ticket-level category and a recommended action (NO_ACTION, WATCH, INVESTIGATE, BUG_FIX, CHALLENGER_IDEA, HALT_MARKET) using the existing evidence gates of daily_postmortem.recommended_action_for_pattern.
    A confirmed integrity failure is a BUG_FIX at once and a HALT_MARKET recommendation when it repeats in the same market.

What it never does: pause recording, disable a market, change a threshold or touch a model. HALT_MARKET is a RECOMMENDATION for the owner or an operator; nothing here acts on it (pinned by tests).
"""
from __future__ import annotations

import datetime as dt
import json

from operational import daily_postmortem as dpm

RECENT_GAMES = 5
ROLE_CHANGE_RATIO = 0.65         # his ice time fell below this share of his own recent average: a role change the projection missed
TOI_PROJECTION_RATIO = 0.80      # ... or below this share: the ice-time projection was off
TEAM_SHOTS_RATIO = 0.80          # his team's shots fell below this share of its own recent average (shots and points legs)
INTEGRITY_CATEGORIES = (dpm.STALE_DATA, dpm.IDENTITY_LINEUP_ERROR, dpm.DATA_PIPELINE_ERROR)
HALT_REPEATS = 2                 # the same integrity category in two DISTINCT incidents in one market family within the window recommends halting that market (a recommendation only)
WINDOW_DAYS = 14                 # pattern counts use tickets settled in this window; an old miss never keeps a recommendation alive
PROXY_CATEGORIES = (dpm.ROLE_CHANGE_MISSED, dpm.TOI_PROJECTION_ERROR, dpm.TEAM_SHOT_ENVIRONMENT_ERROR)
PROXY_BASIS = "basis: the player's (or team's) own last-5-game average, not the model's projection (a ticket does not freeze it)"
PLAYER_MARKETS = ("PLAYER_SOG", "PLAYER_SOG_ALTERNATE", "PLAYER_POINTS", "PLAYER_GOALS", "PLAYER_ASSISTS")
SHOT_ENV_MARKETS = ("PLAYER_SOG", "PLAYER_SOG_ALTERNATE", "PLAYER_POINTS", "PLAYER_GOALS")
POLICY_NOTE = ("A losing ticket, a losing day or a losing streak never pauses recording. Only a confirmed integrity defect (stale data treated as fresh, an identity mapping failure that affects a ticket's truth, "
               "broken price ingestion, schema or data corruption, duplicated tickets, bankroll or accounting corruption, a model pipeline failure that makes outputs untrustworthy) can, and then by an owner or operator "
               "decision: HALT_MARKET is a recommendation, and nothing in the post-mortem pauses recording or disables a market by itself.")


def _latest_stat_rows(nhl_conn, player_id: str, before_date: str | None, limit: int) -> list[float]:
    """The player's ice time in his last `limit` games BEFORE `before_date` (latest revision of each game)."""
    rows = nhl_conn.execute(
        """SELECT p.toi_minutes FROM player_game_stats p JOIN games g ON g.game_id = p.game_id
           WHERE p.player_id = ? AND p.played = 1 AND p.toi_minutes > 0 AND (? IS NULL OR g.game_date < ?)
             AND p.revision_number = (SELECT MAX(q.revision_number) FROM player_game_stats q WHERE q.game_id = p.game_id AND q.player_id = p.player_id)
           ORDER BY g.game_date DESC LIMIT ?""", (str(player_id), before_date, before_date, limit)).fetchall()
    return [float(r[0]) for r in rows]


def _team_recent_shots(nhl_conn, team: str, before_date: str | None, limit: int) -> list[float]:
    rows = nhl_conn.execute(
        """SELECT t.sog FROM team_game_stats t JOIN games g ON g.game_id = t.game_id
           WHERE t.team_id = ? AND (? IS NULL OR g.game_date < ?)
             AND t.revision_number = (SELECT MAX(q.revision_number) FROM team_game_stats q WHERE q.game_id = t.game_id AND q.team_id = t.team_id)
           ORDER BY g.game_date DESC LIMIT ?""", (team, before_date, before_date, limit)).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def leg_diagnostics(nhl_conn, frozen_leg: dict, missed: dict) -> dict:
    """The real signals for one missed leg. {"signals": {name: value}, "evaluated": [names], "not_evaluated": {name: why}}."""
    from operational import quote_freshness as qf
    sig: dict = {}
    evaluated: list[str] = []
    missing: dict = {}

    # 1. the price's age at entry against the production freshness limit (a ticket should never have been recorded on a stale quote)
    age = frozen_leg.get("quote_age_min_at_entry")
    start = qf.parse_utc(frozen_leg.get("game_start_utc"))
    captured = qf.parse_utc(frozen_leg.get("captured_at_utc"))
    if age is not None and start is not None and captured is not None:
        hours = (start - captured).total_seconds() / 3600.0
        limit = qf.price_limit_min(hours)
        sig["quote_age_min_at_entry"], sig["freshness_limit_min"] = float(age), float(limit)
        sig["stale_at_entry"] = float(age) > float(limit) or str(frozen_leg.get("freshness_status") or "FRESH").upper() not in ("FRESH", "OK")
        evaluated.append("price_freshness_at_entry")
    else:
        missing["price_freshness_at_entry"] = "the ticket did not freeze the quote age or the game start"

    gid, pid, team, fam = frozen_leg.get("game_id") or missed.get("game_id"), frozen_leg.get("participant_id"), frozen_leg.get("team"), frozen_leg.get("market_family") or missed.get("market_family")
    gd = nhl_conn.execute("SELECT game_date FROM games WHERE game_id = ?", (int(gid),)).fetchone() if gid is not None else None
    sig["game_date"] = gd[0] if gd else None
    if fam == "GOALIE_SAVES":
        missing["starter_and_workload"] = "goalie legs: the starter and shots-against checks are not implemented, so nothing is concluded"
        return {"signals": sig, "evaluated": evaluated, "not_evaluated": missing}
    is_player = fam in PLAYER_MARKETS and pid is not None and str(pid).isdigit()
    if not is_player:
        sig["note"] = "team market: the player-level signals do not apply"
        return {"signals": sig, "evaluated": evaluated, "not_evaluated": missing}

    row = nhl_conn.execute("""SELECT p.team_id, p.toi_minutes, p.played FROM player_game_stats p WHERE p.game_id = ? AND p.player_id = ?
                              ORDER BY p.revision_number DESC LIMIT 1""", (int(gid), str(pid))).fetchone()
    game = nhl_conn.execute("SELECT game_date FROM games WHERE game_id = ?", (int(gid),)).fetchone()
    game_date = game[0] if game else None
    if row is None:
        missing["identity_and_ice_time"] = "no box-score row for this player in this game"
    else:
        # 2. identity: the official box score must place the player on the team the ticket named
        sig["box_score_team"] = row[0]
        if team and row[0]:                                   # both sides must be known: a missing team id is "not checked", never a mismatch
            sig["identity_match_status"] = "MATCHED" if row[0] == team else "UNMATCHED"
            evaluated.append("identity_against_box_score")
        else:
            missing["identity_against_box_score"] = "the ticket or the box score has no team id for this player"
        # 3. ice time against his own recent average
        recent = _latest_stat_rows(nhl_conn, str(pid), game_date, RECENT_GAMES)
        if row[1] and len(recent) >= 3:
            avg = sum(recent) / len(recent)
            sig["toi_actual"], sig["toi_recent_avg"], sig["toi_ratio"] = float(row[1]), round(avg, 2), round(float(row[1]) / avg, 3)
            evaluated.append("ice_time_vs_recent_average")
        else:
            missing["ice_time_vs_recent_average"] = "fewer than 3 prior games with ice time, or no ice time recorded"
    # 4. his team's shots against its recent average (shots and points legs)
    if fam in SHOT_ENV_MARKETS and team and gid is not None:
        t_row = nhl_conn.execute("SELECT sog FROM team_game_stats WHERE game_id = ? AND team_id = ? ORDER BY revision_number DESC LIMIT 1", (int(gid), team)).fetchone()
        recent_t = _team_recent_shots(nhl_conn, team, game_date, RECENT_GAMES)
        if t_row and t_row[0] is not None and len(recent_t) >= 3:
            avg_t = sum(recent_t) / len(recent_t)
            sig["team_shots_actual"], sig["team_shots_recent_avg"], sig["team_shots_ratio"] = float(t_row[0]), round(avg_t, 2), round(float(t_row[0]) / avg_t, 3)
            evaluated.append("team_shots_vs_recent_average")
        else:
            missing["team_shots_vs_recent_average"] = "no team shot totals for this game or fewer than 3 prior games"
    return {"signals": sig, "evaluated": evaluated, "not_evaluated": missing}


def classify_missed_leg(missed: dict, diag: dict) -> dict:
    """One missed leg to a category from the taxonomy, using only the signals in `diag`."""
    sig = diag["signals"]
    ctx: dict = {}
    if sig.get("stale_at_entry"):
        limit_h = sig["freshness_limit_min"] / 60.0
        ctx["data_freshness_hours"] = max(sig["quote_age_min_at_entry"] / 60.0, limit_h + 1.0 / 60.0)     # a non-FRESH status with an in-limit age still counts as past the limit
        ctx["stale_data_threshold_hours"] = limit_h
    if sig.get("identity_match_status") == "UNMATCHED":
        ctx["identity_match_status"] = "UNMATCHED"
    ratio = sig.get("toi_ratio")
    if ratio is not None and ratio < ROLE_CHANGE_RATIO:
        ctx["role_transition"] = True
    if ratio is not None and ratio < TOI_PROJECTION_RATIO:
        ctx["toi_actual_vs_projected_gap"] = sig["toi_actual"] - sig["toi_recent_avg"]
    tr = sig.get("team_shots_ratio")
    if tr is not None and tr < TEAM_SHOTS_RATIO:
        ctx["team_shot_environment_gap"] = sig["team_shots_actual"] - sig["team_shots_recent_avg"]
    anomaly = bool(ctx)
    ev = set(diag["evaluated"])
    # "everything that could be checked was clean" needs the checks that matter for the leg type: for a player leg the price age, the identity AND the ice time; for a team leg the price age
    checked = "price_freshness_at_entry" in ev and ((("identity_against_box_score" in ev) and ("ice_time_vs_recent_average" in ev)) or "note" in sig)
    if not anomaly and checked:
        ctx["residual"] = 0.0
    cat = dpm.classify_failure({"result_status": "LOSS", "is_combo": False, "market_family": missed.get("market_family")}, ctx)
    unknown_reason = None
    if cat == dpm.UNKNOWN and not anomaly:
        unknown_reason = "too few signals could be evaluated to call it variance or anything else: " + "; ".join(f"{k}: {v}" for k, v in diag["not_evaluated"].items())
    return {"category": cat, "signals": sig, "evaluated": diag["evaluated"], "not_evaluated": diag["not_evaluated"], "unknown_reason": unknown_reason,
            "integrity_defect": cat in INTEGRITY_CATEGORIES, "basis": PROXY_BASIS if cat in PROXY_CATEGORIES else None}


PRIORITY = (dpm.DATA_PIPELINE_ERROR, dpm.IDENTITY_LINEUP_ERROR, dpm.STALE_DATA, dpm.ROLE_CHANGE_MISSED, dpm.TOI_PROJECTION_ERROR, dpm.TEAM_SHOT_ENVIRONMENT_ERROR,
            dpm.MODEL_CALIBRATION, dpm.DEPENDENCE_ERROR, dpm.UNKNOWN, dpm.NORMAL_VARIANCE)


def _frozen_for(pm: dict, missed: dict) -> dict:
    for l in pm.get("frozen_legs") or []:
        if l.get("game_id") == missed.get("game_id") and l.get("market_family") == missed.get("market_family") and l.get("threshold") == missed.get("threshold") \
                and (missed.get("participant_id") is None or str(l.get("participant_id")) == str(missed.get("participant_id"))):
            return l
    return {}


def _incident(c: dict, missed: dict) -> tuple:
    """One real-world incident, however many tickets or thresholds it appears on: a player in a game (player legs) or a team in a game (team legs)."""
    who = str(missed.get("participant_id") or missed.get("participant_name"))
    return (c["game_id"], who)


def classify_all(postmortems: list[dict], nhl_conn, *, now: dt.datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    """Classifies every ticket post-mortem (attaches `classification` only after the whole pass succeeded) and returns {"classified_failures", "by_category", "by_action", "integrity_defects", "policy_note"}.
    Pattern counts use DISTINCT incidents (a player or team in a game) from tickets settled within `window_days`; an older ticket is still classified but contributes no pattern and carries no action."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=window_days)

    def in_window(pm: dict) -> bool:
        t = dpm_parse(pm.get("settled_at_utc"))
        return t is None or t >= cutoff

    work = []
    for pm in postmortems:
        leg_cls = []
        for missed in pm.get("missed_legs") or []:
            c = classify_missed_leg(missed, leg_diagnostics(nhl_conn, _frozen_for(pm, missed), missed))
            c.update(label=f"{missed.get('participant_name')} {missed.get('market_family')} {missed.get('threshold')}", market_family=missed.get("market_family"), game_id=missed.get("game_id"),
                     game_date=c["signals"].get("game_date"), incident=_incident({"game_id": missed.get("game_id")}, missed))
            leg_cls.append(c)
        work.append((pm, leg_cls))
    per_cat: dict[str, dict] = {}
    integrity: dict[tuple, dict] = {}
    for pm, leg_cls in work:
        if not in_window(pm):
            continue
        for c in leg_cls:
            e = per_cat.setdefault(c["category"], {"incidents": set(), "dates": set(), "markets": {}})
            if c["incident"] not in e["incidents"]:
                e["incidents"].add(c["incident"])
                e["markets"][c["market_family"]] = e["markets"].get(c["market_family"], 0) + 1
            e["dates"].add(c["game_date"] or c["game_id"])
            if c["integrity_defect"]:
                k = integrity.setdefault((c["category"], c["market_family"]), {"incidents": set(), "tickets": []})
                k["incidents"].add(c["incident"])
                k["tickets"].append(pm["ticket_id"])
    halts = {k for k, v in integrity.items() if len(v["incidents"]) >= HALT_REPEATS}
    order = (dpm.NO_ACTION, dpm.WATCH, dpm.INVESTIGATE, dpm.CHALLENGER_IDEA, dpm.BUG_FIX, dpm.HALT_MARKET)

    def action_for(c: dict) -> str:
        cat = c["category"]
        if cat in INTEGRITY_CATEGORIES:
            return dpm.HALT_MARKET if (cat, c["market_family"]) in halts else dpm.BUG_FIX
        e = per_cat.get(cat) or {"incidents": set(), "dates": set()}
        a = dpm.recommended_action_for_pattern(cat, occurrences=len(e["incidents"]), unique_game_dates=len(e["dates"]), explanation=f"{len(e['incidents'])} distinct incident(s) classified {cat}")
        if cat in PROXY_CATEGORIES and order.index(a) > order.index(dpm.WATCH):
            a = dpm.WATCH                                       # a proxy against the own recent average can never justify more than a watch (it fires on ordinary losses)
        return a

    for pm, leg_cls in work:
        for c in leg_cls:
            c["recommended_action"] = action_for(c) if in_window(pm) else dpm.NO_ACTION
            c["outside_window"] = not in_window(pm)
        top = min((c["category"] for c in leg_cls), key=lambda x: PRIORITY.index(x) if x in PRIORITY else len(PRIORITY)) if leg_cls else dpm.UNKNOWN
        act = dpm.NO_ACTION
        for c in leg_cls:
            act = max(act, c["recommended_action"], key=order.index)
        pm["classification"] = {"category": top, "legs": leg_cls, "integrity_defect": any(c["integrity_defect"] for c in leg_cls), "recommended_action": act}
        pm["why"] = f"[{top} · {act}] " + str(pm.get("why") or "")
    failures = []
    for cat, e in per_cat.items():
        act = max((c["recommended_action"] for _pm, lc in work for c in lc if c["category"] == cat and not c["outside_window"]), key=order.index, default=dpm.NO_ACTION)
        failures.append({"category": cat, "occurrences": len(e["incidents"]), "unique_game_dates": len(e["dates"]), "markets": e["markets"], "recommended_action": act,
                         "explanation": f"{len(e['incidents'])} distinct incident(s) classified {cat} on {len(e['dates'])} date(s) in the last {window_days} days" + (f" ({PROXY_BASIS})" if cat in PROXY_CATEGORIES else "")})
    return {"classified_failures": failures, "by_category": {k: len(v["incidents"]) for k, v in per_cat.items()},
            "by_action": {a: sum(1 for pm in postmortems if pm["classification"]["recommended_action"] == a) for a in dpm.RECOMMENDED_ACTIONS},
            "integrity_defects": [{"category": k[0], "market_family": k[1], "tickets": v["tickets"], "distinct_incidents": len(v["incidents"]),
                                   "recommended_action": dpm.HALT_MARKET if k in halts else dpm.BUG_FIX} for k, v in integrity.items()],
            "window_days": window_days, "policy_note": POLICY_NOTE, "tickets_classified": len(postmortems)}


def dpm_parse(value):
    from operational import quote_freshness as qf
    return qf.parse_utc(value)
