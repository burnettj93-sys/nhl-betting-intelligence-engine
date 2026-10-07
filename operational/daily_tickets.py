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
              "DraftKings Ontario. Rolling-form probabilities are experimental and uncalibrated.")

STATUS_RECOMMENDED = "RECOMMENDED"
STATUS_RECORDED = "RECORDED"
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

    add("MONEYLINE", "moneyline-t35-v1", adapter.moneyline_candidate_legs(nhl_conn, now=now))
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


# --------------------------------------------------------------- recording ----

def recorded_today(bankroll_conn, et_date: str) -> list[dict]:
    rows = bankroll_conn.execute(
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND market_id LIKE ? ORDER BY created_at_utc",
        (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%")).fetchall()
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


def record_tickets(bankroll_conn, combos: list[rmp.ParlayResult], now: dt.datetime) -> list[dict]:
    """Stake each selected ticket. Stops at the first INSUFFICIENT_FUNDS."""
    results = []
    version = code_version()
    for combo in combos:
        bet = pb.create_real_market_combo_paper_bet(
            bankroll_conn, {"status": "QUALIFIED", "combo": combo},
            event_start_utc=_earliest_start(combo), created_at_utc=now.isoformat(), code_version=version)
        results.append({"ticket_id": pb.compute_ticket_id(et.eastern_today(now), combo.legs), **bet})
        if bet["status"] == "INSUFFICIENT_FUNDS":
            break
    return results


# ------------------------------------------------------------------ state ----

def _rationale(combo_p: float, implied: float, ev_low: float, n_legs: int) -> str:
    return (f"{n_legs} legs from different games. Experimental model estimate {combo_p:.0%} against {implied:.0%} implied "
            f"by the prices (estimated combined price). Still positive after a {rmp.LEG_PROBABILITY_MARGIN:.0%}-point "
            f"policy haircut per leg (EV {ev_low:+.0%}); the haircut is a margin, not a calibration or proof of an edge.")


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
    return {
        "ticket_id": row["paper_bet_id"], "status": _status_for_row(row, now), "recorded": True,
        "legs": card_legs, "combined_decimal": round(decimal_price, 4),
        "combined_american": row["entry_odds"], "price_label": "Estimated combined price (product of leg prices)",
        "stake": stake, "potential_return": round(stake * decimal_price, 2),
        "potential_profit": round(stake * (decimal_price - 1.0), 2),
        "hit_probability": p, "ev_estimated": row.get("ev"),
        "rationale": _rationale(p, 1.0 / decimal_price if decimal_price else 0.0,
                                (p - rmp.LEG_PROBABILITY_MARGIN * len(card_legs)) * decimal_price - 1.0,
                                len(card_legs)),
        "recorded_at_utc": row["created_at_utc"], "event_start_utc": row.get("event_start_utc"),
        "result": {"status": row["result_status"], "profit_loss": row.get("profit_loss"),
                   "settled_at_utc": row.get("settled_at_utc"), "notes": row.get("notes"),
                   "settled_odds": (settlement or {}).get("settled_odds")} if row["result_status"] != "PENDING" else None,
        "alerts": [{"kind": a["kind"], "detail": a["detail"], "at": a["created_at_utc"]} for a in (alerts or [])],
    }


def ticket_from_combo(combo: rmp.ParlayResult, et_date: str) -> dict:
    legs = []
    for l in combo.legs:
        legs.append({
            "label": rmp.leg_label(l), "market_family": l.market_family, "game_id": l.game_id, "team": l.team,
            "opponent": l.opponent, "game_start_utc": l.game_start_utc, "american_price": l.american_price,
            "decimal_price": round(rmp.leg_decimal(l), 4), "price_captured_at_utc": l.captured_at_utc,
            "probability": l.conservative_probability, "model_version": l.model_version, "outcome": None})
    stake = pb.PAPER_BET_STAKE
    return {
        "ticket_id": pb.compute_ticket_id(et_date, combo.legs), "status": STATUS_RECOMMENDED, "recorded": False,
        "legs": legs, "combined_decimal": round(combo.combined_decimal, 4),
        "combined_american": combo.estimated_combo_price,
        "price_label": "Estimated combined price (product of leg prices)",
        "stake": stake, "potential_return": round(stake * combo.combined_decimal, 2),
        "potential_profit": round(stake * (combo.combined_decimal - 1.0), 2),
        "hit_probability": combo.joint_probability, "ev_estimated": combo.ev_estimated,
        "rationale": _rationale(combo.joint_probability, 1.0 / combo.combined_decimal, combo.ev_conservative,
                                len(combo.legs)),
        "recorded_at_utc": None, "event_start_utc": _earliest_start(combo), "result": None, "alerts": []}


def single_card(leg: rmp.ParlayLeg) -> dict:
    d = rmp.leg_decimal(leg)
    return {"label": rmp.leg_label(leg), "game_id": leg.game_id, "team": leg.team, "opponent": leg.opponent,
            "game_start_utc": leg.game_start_utc, "american_price": leg.american_price, "decimal_price": round(d, 4),
            "price_captured_at_utc": leg.captured_at_utc, "probability": leg.conservative_probability,
            "ev_estimated": leg.conservative_probability * d - 1.0, "model_version": leg.model_version,
            "staked": False}


def build_state(bankroll_conn, now: dt.datetime, *, recommended: list[rmp.ParlayResult], singles: list[rmp.ParlayLeg],
                empty_reason: str | None, diagnostics: dict, record_results: list[dict]) -> dict:
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
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND result_status IN ('WIN','LOSS','VOID') "
        "AND market_id NOT LIKE ? ORDER BY settled_at_utc DESC LIMIT ?",
        (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%", RECENT_SETTLED_LIMIT)).fetchall()]
    open_rows = [dict(r) for r in bankroll_conn.execute(
        "SELECT * FROM paper_bets WHERE track = ? AND is_combo = 1 AND result_status IN ('PENDING','UNRESOLVED') "
        "AND market_id NOT LIKE ? ORDER BY created_at_utc", (TRACK, f"REAL_MARKET_PARLAY:{et_date}:%")).fetchall()]
    other_alerts = pb.ticket_alerts(bankroll_conn, [r["paper_bet_id"] for r in settled + open_rows])
    return {
        "date_et": et_date, "generated_at_utc": now.isoformat(),
        "account": account, "slots": {"total": SLOT_COUNT, "used": slots_used, "empty": empty},
        "tickets": cards, "empty_slot_reason": empty_reason if empty else None, "notice": notice,
        "singles": [single_card(l) for l in singles],
        "earlier_open_tickets": [ticket_from_row(r, now, other_alerts.get(r["paper_bet_id"])) for r in open_rows],
        "recent_settled": [ticket_from_row(r, now, other_alerts.get(r["paper_bet_id"])) for r in settled],
        "label": FEED_LABEL, "policy": {
            "min_combined_decimal": rmp.MIN_COMBINED_DECIMAL, "min_estimated_ev": rmp.MIN_ESTIMATED_EV,
            "leg_probability_margin": rmp.LEG_PROBABILITY_MARGIN, "max_tickets_per_day": rmp.MAX_TICKETS_PER_DAY,
            "max_tickets_per_leg": rmp.MAX_TICKETS_PER_LEG, "max_tickets_per_game": rmp.MAX_TICKETS_PER_GAME,
            "stake": pb.PAPER_BET_STAKE},
        "diagnostics": diagnostics, "coverage": market_coverage.audit(diagnostics),
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
    opens = first - dt.timedelta(hours=best_bets.CAPTURE_HORIZON_H)
    first_et = first.astimezone(et.EASTERN).strftime("%-I:%M %p ET")
    if now < opens:
        return (f"{len(starts)} game(s) still to start today; the first puck drop is {first_et}. DraftKings player "
                f"prices are captured within {best_bets.CAPTURE_HORIZON_H:.0f} hours of puck drop (from about "
                f"{opens.astimezone(et.EASTERN).strftime('%-I:%M %p ET')}), so there is nothing priced to select from yet.")
    return base


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
    picked = rmp.select_tickets(legs, existing=existing, max_tickets=slots_left)
    singles = rmp.select_singles(legs)

    account = pb.account_state(bankroll_conn, TRACK)
    record_results = record_tickets(bankroll_conn, picked["tickets"], now) if picked["tickets"] else []
    if slots_left == 0:
        reason = f"All {SLOT_COUNT} of today's ticket slots are recorded."
    else:
        reason = picked["reason"]
        if not legs:
            reason = _no_legs_reason(nhl_conn, now)

    recorded_now = {r["ticket_id"] for r in record_results if r["status"] == "INSERTED"}
    still_recommended = [c for c in picked["tickets"]
                         if pb.compute_ticket_id(et_date, c.legs) not in recorded_now]
    diagnostics = {
        "legs_considered": len(legs), "pool_after_edge_filter": picked["pool_size"],
        "qualifying_tickets": picked["qualifying"], "sources": collected["sources"],
        "second_opinion": collected["second_opinion"], "starting_cash": account["available_cash"],
    }
    state = build_state(bankroll_conn, now, recommended=still_recommended, singles=singles, empty_reason=reason,
                        diagnostics=diagnostics, record_results=record_results)
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
    state = build_state(bankroll_conn, now, recommended=[], singles=[], empty_reason=previous.get("empty_slot_reason"),
                        diagnostics=previous.get("diagnostics", {}), record_results=[])
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
