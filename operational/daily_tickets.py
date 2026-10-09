"""
The one NHL paper-ticket workflow.

Everything the user sees (Today), everything the ledger stakes, everything that
settles and everything a postmortem explains is the same ticket object with the
same persistent id (operational/paper_bankroll.py::compute_ticket_id):

  collect_candidate_legs()   validated leg sources -> ParlayLegs
  run_cycle()                select (engine.select_tickets) -> record ($10 each,
                             funds checked atomically) -> build_state()
  build_state()              ledger rows + current recommendations -> one JSON
                             document that Today, the cloud snapshot and the
                             morning review all read

A recommendation is not a bet. Until a ticket is recorded it is RECOMMENDED and
may change from one refresh to the next. Recording freezes its legs, lines,
prices, price timestamps, probabilities and model versions in legs_json and
stakes $10; after that, refreshes never rewrite it and never stake it again
(the deterministic ticket id is the ledger's idempotency key).

No new probabilities are invented here. Leg probabilities come from the
validated adapters (research/real_market_parlay/real_slate_adapter.py); shots
legs are additionally capped at the rolling-form model's probability
(operational/best_bets.py) and dropped when that model has no dress
confirmation for the player.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from operational import eastern_time as et
from operational import market_coverage
from operational import paper_bankroll as pb
from operational import state_paths
from research.real_market_parlay import engine as rmp

STATE_NAME = "tickets_state.json"
TRACK = "REAL_MARKET_PAPER"
SLOT_COUNT = rmp.MAX_TICKETS_PER_DAY
RECENT_SETTLED_LIMIT = 10

FEED_LABEL = ("US-feed paper experiment. Prices are DraftKings (US feed via The Odds API) and have NOT been matched to "
              "DraftKings Ontario. Player probabilities come from a projection model validated on held-out seasons "
              "(docs/MODEL_VALIDATION.md); they are estimates, not proof of an edge, and there are no historical "
              "DraftKings prices to test profitability against.")

STATUS_RECOMMENDED = "RECOMMENDED"
STATUS_RECORDED = "RECORDED"
STATUS_PROVISIONAL = "PROVISIONAL"
STATUS_PENDING = "PENDING"
STATUS_WON = "WON"
STATUS_LOST = "LOST"
STATUS_VOID = "VOID"
STATUS_UNRESOLVED = "UNRESOLVED"

_SOG_FAMILIES = ("PLAYER_SOG_ALTERNATE", "PLAYER_SOG")


def state_path() -> Path:
    return state_paths.path(STATE_NAME)


# ------------------------------------------------------------------ legs ----

def _game_info(nhl_conn, game_id: str) -> dict | None:
    row = nhl_conn.execute("SELECT home_team, away_team, scheduled_start_utc FROM games WHERE game_id = ?",
                           (game_id,)).fetchone()
    if row is None:
        return None
    start = row["scheduled_start_utc"]
    if start and not start.endswith("Z"):
        start += "Z"
    return {"home": row["home_team"], "away": row["away_team"], "start_utc": start}


def _adapter_legs(nhl_conn, now: dt.datetime) -> tuple[list[rmp.ParlayLeg], dict]:
    from operational.real_prop_orchestrator import _recent_archive_payloads
    from research.live_sog_pricing import market_parser
    from research.real_market_parlay import real_slate_adapter as adapter

    report: dict = {}
    legs: list[rmp.ParlayLeg] = []

    def add(name, version, produced):
        got, excluded = produced
        legs.extend(dataclasses.replace(l, model_version=version) for l in got)
        reasons: dict = {}
        for e in excluded:
            key = str(e.get("reason", "UNKNOWN")).split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        report[name] = {"legs": len(got), "excluded": len(excluded), "exclusion_reasons": reasons}

    from operational import moneyline_model_path
    add("MONEYLINE", moneyline_model_path.version(), adapter.moneyline_candidate_legs(nhl_conn, now=now))
    sog_alt = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=24.0, now=now)
    add("PLAYER_SOG_ALTERNATE", "sog-alternate-validated-v1",
        adapter.sog_alternate_candidate_legs(nhl_conn, sog_alt, now=now))
    sog_std = _recent_archive_payloads(market_parser.STANDARD_MARKET_KEY, max_age_hours=24.0, now=now)
    add("PLAYER_SOG", "sog-standard-validated-v1", adapter.sog_standard_candidate_legs(nhl_conn, sog_std, now=now))
    saves = _recent_archive_payloads(market_parser.SAVES_MARKET_KEY, max_age_hours=24.0, now=now)
    add("GOALIE_SAVES", "goalie-saves-validated-v1", adapter.goalie_saves_candidate_legs(nhl_conn, saves, now=now))
    return legs, report


def _merge_and_add_context(nhl_conn, adapter_legs: list[rmp.ParlayLeg], bb_legs: list[rmp.ParlayLeg]) -> tuple[list, dict]:
    """One pool from two leg sources.
      * Validated-adapter legs (moneyline, standard SOG, saves, and alternate SOG
        while its research corpus is fresh) keep their own gates untouched.
      * Shots legs from the rolling-form model (operational/best_bets.py) are
        the only source that works on current-season data, because the
        validated shots corpus ends in April 2026 (CORPUS_STALE in the sweep logs).
      * A shots leg present in both takes the LOWER probability. A validated shots leg
        with no rolling-form partner (no dress confirmation) is dropped."""
    bb_by_identity = {rmp.leg_identity(l): l for l in bb_legs}
    notes = {"shots_legs_rolling_model_only": 0, "shots_legs_both_sources_min": 0,
             "dropped_validated_shots_leg_without_dress_confirmation": 0}
    out: list[rmp.ParlayLeg] = []
    seen: set = set()
    for leg in adapter_legs:
        if leg.market_family in _SOG_FAMILIES:
            partner = bb_by_identity.get(rmp.leg_identity(leg))
            if partner is None:
                notes["dropped_validated_shots_leg_without_dress_confirmation"] += 1
                continue
            seen.add(rmp.leg_identity(leg))
            low = min(leg.conservative_probability, partner.conservative_probability)
            notes["shots_legs_both_sources_min"] += 1
            leg = dataclasses.replace(
                leg, conservative_probability=low, team=partner.team, opponent=partner.opponent,
                game_start_utc=partner.game_start_utc, model_version=f"{leg.model_version}+{partner.model_version}(min)")
            out.append(leg)
            continue
        info = _game_info(nhl_conn, leg.game_id)
        if info is None:
            continue
        team = opponent = None
        if leg.market_family == "MONEYLINE":
            team = leg.participant_id
            opponent = info["away"] if leg.participant_id == info["home"] else info["home"]
        out.append(dataclasses.replace(leg, team=team, opponent=opponent, game_start_utc=info["start_utc"]))
    for ident, leg in bb_by_identity.items():
        if ident not in seen:
            notes["shots_legs_rolling_model_only"] += 1
            out.append(leg)
    return out, notes


def collect_candidate_legs(nhl_conn, now: dt.datetime) -> dict:
    from operational import best_bets
    adapter_legs, report = _adapter_legs(nhl_conn, now)
    try:
        bb_legs, bb_report = best_bets.candidate_legs(nhl_conn, now)
    except Exception as exc:  # noqa: BLE001 -- no MoneyPuck history on disk etc.: report it, don't hide it
        bb_legs, bb_report = [], {"error": f"{exc.__class__.__name__}: {exc}"}
    legs, notes = _merge_and_add_context(nhl_conn, adapter_legs, bb_legs)
    report["ROLLING_FORM_SHOTS"] = bb_report
    return {"legs": legs, "sources": report, "second_opinion": notes}


# ------------------------------------------------- recording windows (reserved slots) ----

WAVE_SPAN_MIN = 90.0        # games whose puck drops fall within this of the wave's FIRST puck drop form one "wave" of the day
RESERVED_FOR_LATER = 2      # slots held back for later waves while any later wave has yet to start


def day_waves(nhl_conn, et_date: str) -> list[dict]:
    """The day's puck-drop waves from the schedule: [{"index", "start_utc", "end_utc", "game_ids"}] in time order."""
    rows = nhl_conn.execute("SELECT game_id, scheduled_start_utc FROM games WHERE game_date = ? AND game_state = 'SCHEDULED' "
                            "ORDER BY scheduled_start_utc", (et_date,)).fetchall()
    waves: list[dict] = []
    for r in rows:
        if not r["scheduled_start_utc"]:
            continue
        t = dt.datetime.fromisoformat(r["scheduled_start_utc"].replace("Z", "+00:00"))
        t = t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
        if waves and (t - waves[-1]["_first"]).total_seconds() / 60.0 <= WAVE_SPAN_MIN:
            waves[-1]["game_ids"].append(str(r["game_id"]))
            waves[-1]["_last"], waves[-1]["end_utc"] = t, t.isoformat()
        else:
            waves.append({"start_utc": t.isoformat(), "end_utc": t.isoformat(), "_first": t, "_last": t, "game_ids": [str(r["game_id"])]})
    for i, w in enumerate(waves):
        w["index"] = i
        del w["_last"], w["_first"]
    return waves


def wave_policy(waves: list[dict], now: dt.datetime, recorded_game_sets: list[set]):
    """(ticket_filter, info). A ticket belongs to the wave of its earliest game. While later waves have yet to start, an earlier
    wave may hold at most SLOT_COUNT minus (up to RESERVED_FOR_LATER) slots, so early prices cannot use up the whole day. This only
    ever removes a qualifying ticket from this cycle; it never lets one through that the ticket rules reject."""
    game_wave = {gid: w["index"] for w in waves for gid in w["game_ids"]}

    def wave_of(game_ids) -> int | None:
        idx = [game_wave[g] for g in game_ids if g in game_wave]
        return min(idx) if idx else None

    def later_waves_pending(i: int) -> int:
        return sum(1 for w in waves if w["index"] > i and dt.datetime.fromisoformat(w["start_utc"]) > now)

    def cap(i: int) -> int:
        return SLOT_COUNT - min(RESERVED_FOR_LATER, later_waves_pending(i))

    used: dict[int, int] = defaultdict(int)
    for gs in recorded_game_sets:
        w = wave_of(gs)
        if w is not None:
            used[w] += 1

    def ticket_filter(combo, already) -> str | None:
        w = wave_of({l.game_id for l in combo.legs})
        if w is None:
            return None
        taken = used[w] + sum(1 for t in already if wave_of({l.game_id for l in t.legs}) == w)
        if taken >= cap(w):
            return f"wave {w} already holds {taken} of its {cap(w)} slots; the rest are held for later puck drops"
        return None

    info = {"waves": [{"index": w["index"], "first_puck_drop_utc": w["start_utc"], "games": len(w["game_ids"]), "slot_cap": cap(w["index"]),
                       "recorded": used[w["index"]]} for w in waves], "reserved_for_later_waves": RESERVED_FOR_LATER,
            "span_minutes": WAVE_SPAN_MIN}
    return ticket_filter, info


# --------------------------------------------------------------- recording ----

def recorded_today(bankroll_conn, et_date: str, origin: str = "AUTOMATIC") -> list[dict]:
    """Tickets recorded for this Eastern day by one origin. The five daily automatic slots are counted from
    AUTOMATIC rows only: a ticket added by hand never takes one."""
    rows = bankroll_conn.execute(
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND market_id LIKE ? AND origin = ? "
        "ORDER BY created_at_utc", (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%", origin)).fetchall()
    return [dict(r) for r in rows]


def _identities(row: dict) -> list[tuple]:
    return [(l["game_id"], l["participant_id"], l["market_family"], l["threshold"], l.get("side"))
            for l in json.loads(row.get("legs_json") or "[]")]


def _earliest_start(combo: rmp.ParlayResult) -> str | None:
    starts = [l.game_start_utc for l in combo.legs if l.game_start_utc]
    return min(starts) if starts else None


def code_version() -> str:
    """Git commit the running code was loaded from (+dirty when tracked files differ
    from it), stored on every ticket so a result can be tied to the exact code."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001 -- never block recording because git is unavailable
        return "unknown"


def revalidate_before_recording(combo: rmp.ParlayResult, now: dt.datetime) -> list[str]:
    """Reasons a selected ticket may NOT be recorded right now, judged on the clock at the moment of recording (a cycle can spend minutes capturing prices after it
    selected). Every leg must be a fresh price: not provisional, the provider's own quote time inside the limit that applies at this distance from puck drop, and
    the game not started. A morning price can therefore never be the basis of a recorded ticket, however good the ticket looked."""
    from operational import best_bets, quote_freshness
    reasons = []
    for l in combo.legs:
        if getattr(l, "provisional", False):
            reasons.append(f"{rmp.leg_label(l)}: price is provisional (from an earlier look); it is re-checked on a pregame price first")
            continue
        start = best_bets.effective_start(l.provider_start_utc or l.game_start_utc, l.game_start_utc) if (l.provider_start_utc or l.game_start_utc) else None
        if start is not None and start <= now:
            reasons.append(f"{rmp.leg_label(l)}: game started")
            continue
        if start is None or not l.quote_updated_utc:
            continue                      # no timestamps to re-judge: the gate that built the leg already refused a missing or malformed quote time
        if best_bets._cp_enforced():
            from operational import capture_schedule as sched
            got = quote_freshness.parse_utc(l.retrieved_at_utc or l.captured_at_utc)
            if got is not None and got < start - dt.timedelta(hours=sched.PREGAME_HOURS + 0.05):
                reasons.append(f"{rmp.leg_label(l)}: price was retrieved before this game's pregame window opened")
                continue
        hours = (start - now).total_seconds() / 3600.0
        a = quote_freshness.assess(l.quote_updated_utc, l.retrieved_at_utc or l.captured_at_utc, now, best_bets.price_age_limit_min(hours))
        if not a["fresh"]:
            reasons.append(f"{rmp.leg_label(l)}: price no longer fresh at recording time ({a['status']}, {a.get('quote_age_min')} min old)")
    return reasons


def record_tickets(bankroll_conn, combos: list[rmp.ParlayResult], now: dt.datetime) -> list[dict]:
    """Stake each selected ticket. Stops at the first INSUFFICIENT_FUNDS."""
    results = []
    version = code_version()
    for combo in combos:
        # A ticket is recorded after the prices it freezes were retrieved: never stamp it earlier than its newest
        # price (the run's start time can precede a capture made during the same run).
        from operational import quote_freshness
        stamps = [quote_freshness.parse_utc(l.retrieved_at_utc or l.captured_at_utc) for l in combo.legs]
        created = max([now] + [t for t in stamps if t is not None and (t - now).total_seconds() <= 120.0])
        bet = pb.create_real_market_combo_paper_bet(
            bankroll_conn, {"status": "QUALIFIED", "combo": combo},
            event_start_utc=_earliest_start(combo), created_at_utc=created.isoformat(), code_version=version)
        results.append({"ticket_id": pb.compute_ticket_id(et.eastern_today(now), combo.legs), **bet})
        if bet["status"] == "INSUFFICIENT_FUNDS":
            break
    return results


# ------------------------------------------------------------------ state ----

def haircut_ev(leg_probabilities: list[float], decimal_price: float, margin: float) -> float:
    """EV with every leg's probability lowered by `margin`, exactly as the selector tests it
    (product of (p_i - margin) x combined decimal - 1)."""
    p = 1.0
    for q in leg_probabilities:
        p *= max(q - margin, 0.0)
    return p * decimal_price - 1.0


def _rationale(combo_p: float, implied: float, ev_low: float, n_legs: int, margin: float = rmp.LEG_PROBABILITY_MARGIN) -> str:
    return (f"{n_legs} legs from different games. Experimental model estimate {combo_p:.0%} against {implied:.0%} implied "
            f"by the prices (estimated combined price). EV after lowering each leg's probability by {margin:.0%} points: "
            f"{ev_low:+.1%}; the haircut is a policy margin, not a calibration or proof of an edge.")


def _status_for_row(row: dict, now: dt.datetime) -> str:
    status = row["result_status"]
    if status == "WIN":
        return STATUS_WON
    if status == "LOSS":
        return STATUS_LOST
    if status == "VOID":
        return STATUS_VOID
    if status == "UNRESOLVED":
        return STATUS_UNRESOLVED
    start = row.get("event_start_utc")
    if start:
        started = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=dt.timezone.utc)
        if now >= started:
            return STATUS_PENDING
    return STATUS_RECORDED


def ticket_from_row(row: dict, now: dt.datetime, alerts: list[dict] | None = None) -> dict:
    legs = json.loads(row.get("legs_json") or "[]")
    settlement = json.loads(row["settlement_json"]) if row.get("settlement_json") else None
    outcomes = {}
    if settlement:
        for r in settlement.get("leg_results", []):
            leg = r.get("leg", {})
            outcomes[(leg.get("game_id"), leg.get("participant_id"), leg.get("market_family"), leg.get("threshold"))] = \
                r.get("outcome")
    card_legs = []
    for l in legs:
        stub = rmp.ParlayLeg(
            game_id=l["game_id"], event_id=l.get("event_id"), market_family=l["market_family"],
            participant_id=l["participant_id"], participant_name=l["participant_name"], side=l.get("side") or "",
            threshold=l.get("threshold"), american_price=l["american_price"],
            conservative_probability=l["conservative_probability"], sportsbook=l.get("sportsbook", ""),
            captured_at_utc=l.get("captured_at_utc"), provider_contract_verified=True,
            model_threshold_eligible=True, identity_resolved=True, price_fresh=True, event_not_started=True)
        card_legs.append({
            "label": rmp.leg_label(stub), "market_family": l["market_family"], "game_id": l["game_id"],
            "participant_id": l["participant_id"], "participant_name": l["participant_name"],
            "provider_start_utc": l.get("provider_start_utc"),
            "retrieved_at_utc": l.get("retrieved_at_utc"), "quote_updated_utc": l.get("quote_updated_utc"),
            "quote_age_min_at_entry": l.get("quote_age_min_at_entry"), "freshness_status": l.get("freshness_status"),
            "team": l.get("team"), "opponent": l.get("opponent"), "game_start_utc": l.get("game_start_utc"),
            "american_price": l["american_price"], "decimal_price": l.get("decimal_price") or rmp.leg_decimal(stub),
            "price_captured_at_utc": l.get("captured_at_utc"), "probability": l["conservative_probability"],
            "model_version": l.get("model_version", ""), "code_version": l.get("code_version"),
            "outcome": outcomes.get((l["game_id"], l["participant_id"], l["market_family"], l.get("threshold")))})
    decimal_price = 1.0
    for l in card_legs:
        decimal_price *= l["decimal_price"]
    p = row.get("model_probability") or 0.0
    stake = row["stake"]
    margin = next((l["haircut_margin"] for l in legs if l.get("haircut_margin") is not None), rmp.LEG_PROBABILITY_MARGIN)
    ev_after = haircut_ev([l["probability"] for l in card_legs], decimal_price, margin)
    return {
        "ticket_id": row["paper_bet_id"], "status": _status_for_row(row, now), "recorded": True,
        "legs": card_legs, "combined_decimal": round(decimal_price, 4),
        "combined_american": row["entry_odds"], "price_label": "Estimated combined price (product of leg prices)",
        "stake": stake, "potential_return": round(stake * decimal_price, 2),
        "potential_profit": round(stake * (decimal_price - 1.0), 2),
        "hit_probability": p, "ev_estimated": row.get("ev"),
        "ev_after_haircut": ev_after, "haircut_margin": margin,
        "rationale": _rationale(p, 1.0 / decimal_price if decimal_price else 0.0, ev_after, len(card_legs), margin),
        "recorded_at_utc": row["created_at_utc"], "event_start_utc": row.get("event_start_utc"),
        "result": {"status": row["result_status"], "profit_loss": row.get("profit_loss"),
                   "settled_at_utc": row.get("settled_at_utc"), "notes": row.get("notes"),
                   "settled_odds": (settlement or {}).get("settled_odds")} if row["result_status"] != "PENDING" else None,
        "alerts": [{"kind": a["kind"], "detail": a["detail"], "at": a["created_at_utc"], "retracted": a.get("retracted_reason")} for a in (alerts or [])],
        "origin": row.get("origin") or "AUTOMATIC",
        "provenance": json.loads(row["provenance_json"]) if row.get("provenance_json") else None,
    }


def ticket_from_combo(combo: rmp.ParlayResult, et_date: str) -> dict:
    legs = []
    for l in combo.legs:
        legs.append({
            "label": rmp.leg_label(l), "market_family": l.market_family, "game_id": l.game_id,
            "participant_id": l.participant_id, "participant_name": l.participant_name,
            "provider_start_utc": l.provider_start_utc,
            "retrieved_at_utc": l.retrieved_at_utc, "quote_updated_utc": l.quote_updated_utc,
            "quote_age_min_at_entry": l.quote_age_min, "freshness_status": l.freshness_status or None, "team": l.team,
            "opponent": l.opponent, "game_start_utc": l.game_start_utc, "american_price": l.american_price,
            "decimal_price": round(rmp.leg_decimal(l), 4), "price_captured_at_utc": l.captured_at_utc,
            "probability": l.conservative_probability, "model_version": l.model_version, "outcome": None,
            "provisional": bool(getattr(l, "provisional", False))})
    stake = pb.PAPER_BET_STAKE
    return {
        "ticket_id": pb.compute_ticket_id(et_date, combo.legs), "status": STATUS_RECOMMENDED, "recorded": False,
        "legs": legs, "combined_decimal": round(combo.combined_decimal, 4),
        "combined_american": combo.estimated_combo_price,
        "price_label": "Estimated combined price (product of leg prices)",
        "stake": stake, "potential_return": round(stake * combo.combined_decimal, 2),
        "potential_profit": round(stake * (combo.combined_decimal - 1.0), 2),
        "hit_probability": combo.joint_probability, "ev_estimated": combo.ev_estimated,
        "ev_after_haircut": combo.ev_conservative, "haircut_margin": combo.leg_probability_margin,
        "rationale": _rationale(combo.joint_probability, 1.0 / combo.combined_decimal, combo.ev_conservative,
                                len(combo.legs), combo.leg_probability_margin),
        "recorded_at_utc": None, "event_start_utc": _earliest_start(combo), "result": None, "alerts": [],
        "origin": "AUTOMATIC", "provenance": None}


def exposure(cards: list[dict]) -> dict:
    """Which players and games sit on more than one of today's tickets. Tickets that share a leg's player or
    game win and lose together more often than independent tickets would; the estimated combined price and
    hit chance of each ticket assume its own legs are independent (they are always different games)."""
    players: dict = {}
    games: dict = {}
    for c in cards:
        for l in c["legs"]:
            p = players.setdefault(l["participant_id"], {"player": l["participant_name"], "tickets": []})
            if c["ticket_id"] not in p["tickets"]:
                p["tickets"].append(c["ticket_id"])
            g = games.setdefault(l["game_id"], {"game_id": l["game_id"], "matchup": " vs ".join(
                x for x in (l.get("team"), l.get("opponent")) if x) or l["game_id"], "tickets": []})
            if c["ticket_id"] not in g["tickets"]:
                g["tickets"].append(c["ticket_id"])
    fin = lambda d: sorted(({**v, "count": len(v["tickets"])} for v in d.values()),  # noqa: E731
                           key=lambda v: (-v["count"], v.get("player") or v.get("matchup")))
    recorded = [c for c in cards if c["recorded"] and c["status"] in (STATUS_RECORDED, STATUS_PENDING, STATUS_UNRESOLVED)]
    by_origin = {}
    for c in cards:
        by_origin[c.get("origin") or "AUTOMATIC"] = by_origin.get(c.get("origin") or "AUTOMATIC", 0) + 1
    return {"tickets_counted": len(cards), "recorded_stake_at_risk": round(sum(c["stake"] for c in recorded), 2),
            "tickets_by_origin": by_origin,
            "players": fin(players), "games": fin(games),
            "players_on_multiple_tickets": sum(1 for v in players.values() if len(v["tickets"]) > 1),
            "note": ("Tickets that share a player or a game are correlated: they tend to win or lose together, so the "
                     "open stake is less diversified than the ticket count suggests.")}


def single_card(leg: rmp.ParlayLeg) -> dict:
    d = rmp.leg_decimal(leg)
    return {"label": rmp.leg_label(leg), "game_id": leg.game_id, "team": leg.team, "opponent": leg.opponent,
            "game_start_utc": leg.game_start_utc, "american_price": leg.american_price, "decimal_price": round(d, 4),
            "price_captured_at_utc": leg.captured_at_utc, "probability": leg.conservative_probability,
            "ev_estimated": leg.conservative_probability * d - 1.0, "model_version": leg.model_version,
            "staked": False}


def mark_on_book(options: dict | None, bankroll_conn, et_date: str) -> dict | None:
    """Annotates each option with the AUTOMATIC model-book ticket that already holds the same bet, so the page can say so. It is information only: a
    person may still add the same bet to their own personal log (that never touches the model book)."""
    if not options:
        return options
    for opt in options.get("options", []):
        opt["on_book"] = None
        legs = opt["legs"]
        stub = [type("L", (), {"game_id": l["game_id"], "participant_id": l["participant_id"], "market_family": l["market_family"],
                               "threshold": l["threshold"], "side": l["side"]}) for l in legs]
        for tid in (pb.compute_ticket_id(et_date, stub),):
            row = bankroll_conn.execute("SELECT paper_bet_id, origin, result_status FROM paper_bets WHERE paper_bet_id = ?", (tid,)).fetchone()
            if row:
                opt["on_book"] = {"ticket_id": row["paper_bet_id"], "origin": row["origin"], "status": row["result_status"]}
                break
    return options


def build_state(bankroll_conn, now: dt.datetime, *, recommended: list[rmp.ParlayResult], singles: list[rmp.ParlayLeg],
                empty_reason: str | None, diagnostics: dict, record_results: list[dict], options: dict | None = None,
                provisional: dict | None = None) -> dict:
    et_date = et.eastern_today(now)
    rows = recorded_today(bankroll_conn, et_date)
    alerts = pb.ticket_alerts(bankroll_conn, [r["paper_bet_id"] for r in rows])
    cards = [ticket_from_row(r, now, alerts.get(r["paper_bet_id"])) for r in rows]
    recorded_ids = {c["ticket_id"] for c in cards}
    account = pb.account_state(bankroll_conn, TRACK)
    for combo in recommended:
        card = ticket_from_combo(combo, et_date)
        if card["ticket_id"] not in recorded_ids:
            cards.append(card)
    slots_used = len(cards)
    empty = max(0, SLOT_COUNT - slots_used)
    funds_blocked = any(r.get("status") == "INSUFFICIENT_FUNDS" for r in record_results) or \
        (account["available_cash"] < pb.PAPER_BET_STAKE)
    notice = None
    if funds_blocked:
        notice = (f"Available cash ${account['available_cash']:.2f} is below the ${pb.PAPER_BET_STAKE:.0f} stake, so "
                  f"no new ticket can be recorded. Tickets marked Recommended are not paper bets.")
        if empty:
            empty_reason = notice
    settled = [dict(r) for r in bankroll_conn.execute(
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND origin = 'AUTOMATIC' AND result_status IN ('WIN','LOSS','VOID') "
        "AND market_id NOT LIKE ? ORDER BY settled_at_utc DESC LIMIT ?",
        (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%", RECENT_SETTLED_LIMIT)).fetchall()]
    open_rows = [dict(r) for r in bankroll_conn.execute(
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND origin = 'AUTOMATIC' AND result_status IN ('PENDING','UNRESOLVED') "
        "AND market_id NOT LIKE ? ORDER BY created_at_utc", (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%")).fetchall()]
    other_alerts = pb.ticket_alerts(bankroll_conn, [r["paper_bet_id"] for r in settled + open_rows])
    return {
        "date_et": et_date, "generated_at_utc": now.isoformat(),
        "account": account, "slots": {"total": SLOT_COUNT, "used": slots_used, "empty": empty},
        "tickets": cards, "empty_slot_reason": empty_reason if empty else None, "notice": notice,
        "origins": pb.origin_performance(bankroll_conn, TRACK),
        "singles": [single_card(l) for l in singles],
        "exposure": exposure(cards + [c for c in [ticket_from_row(r, now) for r in open_rows]
                                                      if c["status"] in (STATUS_RECORDED, STATUS_PENDING, STATUS_UNRESOLVED)]),
        "earlier_open_tickets": [ticket_from_row(r, now, other_alerts.get(r["paper_bet_id"])) for r in open_rows],
        "recent_settled": [ticket_from_row(r, now, other_alerts.get(r["paper_bet_id"])) for r in settled],
        "label": FEED_LABEL, "policy": {
            "min_combined_decimal": rmp.MIN_COMBINED_DECIMAL, "min_estimated_ev": rmp.MIN_ESTIMATED_EV,
            "leg_probability_margin": rmp.LEG_PROBABILITY_MARGIN, "max_tickets_per_day": rmp.MAX_TICKETS_PER_DAY,
            "max_tickets_per_leg": rmp.MAX_TICKETS_PER_LEG, "max_tickets_per_game": rmp.MAX_TICKETS_PER_GAME,
            "stake": pb.PAPER_BET_STAKE},
        "diagnostics": diagnostics, "coverage": market_coverage.audit(diagnostics),
        "options": mark_on_book(options, bankroll_conn, et_date),
        "provisional": provisional or {"as_of_utc": now.isoformat(), "note": PROVISIONAL_NOTE, "provisional_legs": 0, "tickets": []},
    }


def _write_state(state: dict) -> bool:
    """Write atomically; True when the content (ignoring timestamps) changed."""
    import hashlib

    def digest(s):
        body = {k: v for k, v in s.items() if k not in ("generated_at_utc",)}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    path = state_path()
    old = read_state()
    changed = old is None or digest(old) != digest(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)
    return changed


def read_state() -> dict | None:
    path = state_path()
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------- cycle ----

def _capture_plan(nhl_conn, now: dt.datetime) -> list[dict]:
    from operational import best_bets
    try:
        games = best_bets.current_model(nhl_conn, now)["games"] if nhl_conn is not None else {}
        return best_bets.capture_plan(now, games=games)
    except Exception as exc:  # noqa: BLE001 -- diagnostics only
        return [{"error": f"{exc.__class__.__name__}: {exc}"}]


def _availability_block(nhl_conn, now: dt.datetime) -> dict:
    """Why each game's prices are or are not on file (operational/price_availability.py), today and tomorrow, plus the schedule that produced it."""
    from operational import capture_schedule as sched, price_availability as pa
    today = et.eastern_today(now)
    tomorrow = (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat()
    out = {"schedule": {"morning_from_et": "%02d:%02d" % sched.MORNING_START_ET, "midday_from_et": "%02d:%02d" % sched.MIDDAY_START_ET,
                        "pregame_minutes_before_puck_drop": round(sched.PREGAME_HOURS * 60), "slot_close_hours_before": sched.SLOT_CLOSE_HOURS,
                        "recheck_not_posted_minutes": sched.RECHECK_MINUTES, "tomorrow_check_et": "%02d:%02d" % sched.EVENING_CHECK_ET}}
    try:
        out["today"] = pa.summary(today)
        out["today"]["sentence"] = pa.sentence(today)
        out["tomorrow"] = pa.summary(tomorrow)
        out["tomorrow"]["sentence"] = pa.sentence(tomorrow)
        out["tomorrow_last_check_utc"] = (pa.tomorrow_last_check().isoformat() if pa.tomorrow_last_check() else None)
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        out["error"] = f"{exc.__class__.__name__}: {exc}"
    return out


def _no_legs_reason(nhl_conn, now: dt.datetime) -> str:
    base = "No eligible priced legs right now (no fresh prices for games that haven't started)."
    if nhl_conn is None:
        return base
    from operational import best_bets
    rows = nhl_conn.execute(
        "SELECT scheduled_start_utc FROM games WHERE game_date = ? AND game_state = 'SCHEDULED'",
        (et.eastern_today(now),)).fetchall()
    starts = sorted(s for s in (r["scheduled_start_utc"] for r in rows if r["scheduled_start_utc"])
                    if best_bets._parse_utc(s if s.endswith("Z") else s + "Z") > now)
    if not starts:
        return "No games left to start today."
    first = best_bets._parse_utc(starts[0] if starts[0].endswith("Z") else starts[0] + "Z")
    from operational import capture_schedule as sched, price_availability as pa
    opens = first - dt.timedelta(hours=sched.PREGAME_HOURS)
    first_et = first.astimezone(et.EASTERN).strftime("%-I:%M %p ET")
    if now < opens:
        try:
            ids = [str(r["game_id"]) for r in nhl_conn.execute("SELECT game_id FROM games WHERE game_date = ? AND game_state = 'SCHEDULED'", (et.eastern_today(now),)).fetchall()]
            avail = pa.sentence(et.eastern_today(now), ids)
        except Exception:  # noqa: BLE001
            avail = ""
        return (f"{len(starts)} game(s) still to start today; the first puck drop is {first_et}. A ticket is recorded only on prices fetched within "
                f"{sched.PREGAME_HOURS * 60:.0f} minutes of puck drop (from about {opens.astimezone(et.EASTERN).strftime('%-I:%M %p ET')} for the first game), so every "
                f"one is re-checked at the last moment. {avail} Until then the app shows provisional options built from the morning look.").strip()
    return base


AUDIT_NAME = "selection_audit.jsonl"
AUDIT_KEEP_DAYS_ROWS = 6


def _record_selection_audit(et_date: str, now: dt.datetime, picked: dict, slots_left: int) -> dict:
    """Why these tickets and not others: the qualifying tickets in hit-chance order, each marked SELECTED, or blocked and by which exposure limit.
    A cycle that recorded something is appended to a small per-day log (so the reasoning behind a recorded ticket is not lost when later cycles have nothing
    left to pick); the published document carries today's recording cycles plus the latest cycle."""
    import json as _json
    from operational import state_paths
    path = state_paths.path(AUDIT_NAME)
    latest = {"date_et": et_date, "at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "slots_open": slots_left, "pool_legs": picked["pool_size"],
              "qualifying": picked["qualifying"], "considered": picked.get("considered") or [], "selected": len(picked["tickets"])}
    try:
        if picked["tickets"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(_json.dumps(latest, sort_keys=True) + "\n")
        rows = [_json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    except (OSError, ValueError):
        rows = []
    return {"recording_cycles_today": [r for r in rows if r.get("date_et") == et_date][-AUDIT_KEEP_DAYS_ROWS:], "latest_cycle": latest}


PROVISIONAL_NOTE = ("Provisional: built from today's earlier look at DraftKings' prices. Nothing here is recorded and no slot is used. A ticket is recorded only after its prices are "
                    "re-fetched shortly before puck drop and it still qualifies on them; prices and chances can move or disappear before then.")


def _with_provisional_eligible(legs: list) -> list:
    """The leg list in which a provisional price counts as usable (for DISPLAY: options and provisional tickets). The recording pool never goes through this."""
    import dataclasses
    return [dataclasses.replace(l, price_fresh=True) if getattr(l, "provisional", False) else l for l in legs]


def _provisional_section(legs: list, existing: list, now: dt.datetime, et_date: str) -> dict:
    """Up to SLOT_COUNT tickets chosen exactly as the recording selector would choose them, but allowed to use today's earlier (provisional) prices. Never recorded,
    never counted against the five slots; exposure limits still count what is already recorded."""
    prov_legs = [l for l in legs if getattr(l, "provisional", False)]
    section = {"as_of_utc": now.isoformat(), "note": PROVISIONAL_NOTE, "provisional_legs": len(prov_legs), "tickets": []}
    if not prov_legs:
        return section
    try:
        picked = rmp.select_tickets(_with_provisional_eligible(legs), existing=existing, max_tickets=SLOT_COUNT)
    except Exception as exc:  # noqa: BLE001 - informational only
        section["error"] = f"{exc.__class__.__name__}: {exc}"
        return section
    for combo in picked["tickets"]:
        if not any(getattr(l, "provisional", False) for l in combo.legs):
            continue                      # a ticket entirely on fresh prices is the recording selector's business, not a provisional one
        card = ticket_from_combo(combo, et_date)
        card["status"] = STATUS_PROVISIONAL
        card["provisional"] = True
        card["oldest_quote_age_min"] = max((l.quote_age_min or 0.0) for l in combo.legs)
        section["tickets"].append(card)
    return section


def run_cycle(nhl_conn, bankroll_conn, now: dt.datetime, *, collected: dict | None = None) -> dict:
    """One selection + recording pass. Safe to repeat: recorded tickets are
    never rewritten and never staked twice."""
    et_date = et.eastern_today(now)
    existing_rows = recorded_today(bankroll_conn, et_date)
    existing = [_identities(r) for r in existing_rows]
    slots_left = max(0, SLOT_COUNT - len(existing_rows))

    collected = collected if collected is not None else collect_candidate_legs(nhl_conn, now)
    legs = collected["legs"]
    # Exposure limits count tickets already recorded today; they are never re-selected.
    try:
        waves = day_waves(nhl_conn, et_date) if nhl_conn is not None else []
    except Exception:  # noqa: BLE001 - without a schedule there is no reservation, never a failed cycle
        waves = []
    ticket_filter, wave_info = wave_policy(waves, now, [{i[0] for i in e} for e in existing])
    picked = rmp.select_tickets(legs, existing=existing, max_tickets=slots_left, ticket_filter=ticket_filter)
    singles = rmp.select_singles(legs)
    selection_audit = _record_selection_audit(et_date, now, picked, slots_left)

    account = pb.account_state(bankroll_conn, TRACK)
    revalidated, held_back = [], []
    for combo in picked["tickets"]:
        why = revalidate_before_recording(combo, now)
        (held_back if why else revalidated).append((combo, why))
    record_results = record_tickets(bankroll_conn, [c for c, _ in revalidated], now) if revalidated else []
    if slots_left == 0:
        reason = f"All {SLOT_COUNT} of today's ticket slots are recorded."
    else:
        reason = picked["reason"]
        if not legs:
            reason = _no_legs_reason(nhl_conn, now)

    recorded_now = {r["ticket_id"] for r in record_results if r["status"] == "INSERTED"}
    still_recommended = [c for c in picked["tickets"]
                         if pb.compute_ticket_id(et_date, c.legs) not in recorded_now]
    provisional = _provisional_section(legs, existing, now, et_date)
    diagnostics = {
        "legs_considered": len(legs), "pool_after_edge_filter": picked["pool_size"],
        "qualifying_tickets": picked["qualifying"], "funnel": rmp.selection_funnel(legs),
        "sources": collected["sources"],
        "second_opinion": collected["second_opinion"], "starting_cash": account["available_cash"],
        "capture_plan": _capture_plan(nhl_conn, now), "recording_policy": wave_info, "price_availability": _availability_block(nhl_conn, now),
        "selection_audit": selection_audit,
        "recording_revalidation": {"recorded_after_revalidation": len(revalidated), "held_back": [{"ticket_id": pb.compute_ticket_id(et_date, c.legs), "reasons": w} for c, w in held_back]},
    }
    from operational import player_options
    try:
        options = player_options.build_options(_with_provisional_eligible(legs), et_date)
    except Exception as exc:  # noqa: BLE001 - options are informational; never block recording
        options = {"date_et": et_date, "options": [], "persons": {}, "error": f"{exc.__class__.__name__}: {exc}"}
    options["generated_at_utc"] = now.isoformat()
    state = build_state(bankroll_conn, now, recommended=still_recommended, singles=singles, empty_reason=reason,
                        diagnostics=diagnostics, record_results=record_results, options=options, provisional=provisional)
    changed = _write_state(state)
    return {
        "eastern_date": et_date, "qualifying_tickets_found": picked["qualifying"],
        "newly_recorded": sum(1 for r in record_results if r["status"] == "INSERTED"),
        "already_recorded": sum(1 for r in record_results if r["status"] == "DUPLICATE"),
        "insufficient_funds": sum(1 for r in record_results if r["status"] == "INSUFFICIENT_FUNDS"),
        "results": record_results, "state_changed": changed, "reason": reason, "slots_left_before": slots_left,
    }


def refresh_state_only(bankroll_conn, now: dt.datetime) -> bool:
    """Rewrite the document from the ledger alone (after settlement), keeping
    the last recommendations/singles that were on file."""
    previous = read_state() or {}
    prev_options = previous.get("options") if previous.get("date_et") == et.eastern_today(now) else None
    prev_prov = previous.get("provisional") if previous.get("date_et") == et.eastern_today(now) else None
    state = build_state(bankroll_conn, now, recommended=[], singles=[], empty_reason=previous.get("empty_slot_reason"),
                        diagnostics=previous.get("diagnostics", {}), record_results=[], options=prev_options, provisional=prev_prov)
    # Carry forward unrecorded cards and singles so a settlement pass does not blank the board.
    recorded_ids = {c["ticket_id"] for c in state["tickets"]}
    for card in previous.get("tickets", []):
        if not card.get("recorded") and card["ticket_id"] not in recorded_ids \
                and previous.get("date_et") == state["date_et"]:
            state["tickets"].append(card)
    state["slots"]["used"] = len(state["tickets"])
    state["slots"]["empty"] = max(0, SLOT_COUNT - len(state["tickets"]))
    if previous.get("date_et") == state["date_et"]:
        state["singles"] = previous.get("singles", [])
    return _write_state(state)
