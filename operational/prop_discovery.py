"""
Prop contract DISCOVERY vs VERIFIED_PRODUCTION mode and the prop credit budget (Quota + Moneyline
Activation block, 2026-09-25).

Evidence (see docs/PROP_DISCOVERY_BUDGET.md): no `player_*` market has ever been returned by the Odds API
for NHL. The broad daily prop pull requested 7 market keys per event for all ~33 events; the provider
charges one credit per market that actually RETURNS data, so it cost 0 until DraftKings started posting
`alternate_team_totals` (2026-09-23) and then ~30 credits/day for a market no model uses. A request for a
market that is not posted costs 0 (231 event-requests, 2026-09-15..22, 0 credits).

Modes (derived from the contract registry; never set by hand):
  DISCOVERY            no PLAYER_SOG / GOALIE_SAVES contract is VERIFIED. Only the three desired keys are
                       ever requested, on at most DISCOVERY_SAMPLE_EVENTS representative events, under a hard
                       DISCOVERY_DAILY_BUDGET. The first appearance flags CANDIDATE_OBSERVED and STOPS --
                       nothing expands, nothing auto-verifies.
  VERIFIED_PRODUCTION  at least one contract is VERIFIED (a human added it to
                       provider_adapter.VERIFIED_CONTRACTS after the deterministic certification). Only
                       VERIFIED keys are requested; each event is swept at most once per stage per day.

Market state per key:  PENDING_LIVE_CONTRACT -> CANDIDATE_OBSERVED (logged in
operational/prop_contract_candidates.jsonl) -> VERIFIED (provider_adapter.VERIFIED_CONTRACTS only).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
from operational import state_paths as _sp

STATE_PATH = _sp.path("prop_discovery_state.json")

DISCOVERY_MARKETS = ("player_shots_on_goal", "player_shots_on_goal_alternate", "player_total_saves")
MARKET_TO_CONTRACT = {"player_shots_on_goal": "PLAYER_SOG", "player_shots_on_goal_alternate": "PLAYER_SOG",
                      "player_total_saves": "GOALIE_SAVES"}
SPORTSBOOK = "draftkings"

DISCOVERY, VERIFIED_PRODUCTION = "DISCOVERY", "VERIFIED_PRODUCTION"
PENDING, CANDIDATE, VERIFIED = "PENDING_LIVE_CONTRACT", "CANDIDATE_OBSERVED", "VERIFIED"

DISCOVERY_DAILY_BUDGET = 6        # hard cap on prop credits per UTC day while contracts are unverified
DISCOVERY_SAMPLE_EVENTS = 2
DISCOVERY_HORIZON_H = 36          # books post props close to game day: only look at games starting soon
STATE_KEEP_DAYS = 10


# ---- market state ---------------------------------------------------------------------------------------------
_REAL_EVENT_ID = re.compile(r"^[0-9a-f]{32}$")     # Odds API event ids; test fixtures use ids like "fixture-sog-evt-1"


def _candidate_keys(path: Path | None = None) -> set[str]:
    """Market keys with a REAL candidate record. The candidate log is append-only and (found 2026-09-25) had
    been polluted by test runs writing fixture payloads (`fixture-*` event ids) into the real file, so only
    records for real Odds API event ids count -- a fixture must never flip a market to CANDIDATE_OBSERVED."""
    from operational import real_prop_orchestrator as rpo
    path = path or rpo.PROP_CONTRACT_CANDIDATES_PATH
    keys = set()
    try:
        for line in Path(path).read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if _REAL_EVENT_ID.match(str(rec.get("event_id") or "")):
                keys.add(rec.get("market_key"))
    except OSError:
        pass
    return keys


def market_states(candidates_path: Path | None = None, verified_fn: Callable | None = None) -> dict[str, str]:
    from research.generic_prop_pricing import provider_adapter as pa
    verified_fn = verified_fn or pa.is_contract_verified
    cands = _candidate_keys(candidates_path)
    out = {}
    for key in DISCOVERY_MARKETS:
        if verified_fn(SPORTSBOOK, MARKET_TO_CONTRACT[key]):
            out[key] = VERIFIED
        elif key in cands:
            out[key] = CANDIDATE
        else:
            out[key] = PENDING
    return out


def mode(states: dict | None = None) -> str:
    states = states or market_states()
    return VERIFIED_PRODUCTION if VERIFIED in states.values() else DISCOVERY


def keys_in_state(state: str, states: dict | None = None) -> list[str]:
    return [k for k, v in (states or market_states()).items() if v == state]


def production_markets(states: dict | None = None) -> str:
    """Comma-separated market keys a VERIFIED_PRODUCTION request may ask for (verified only)."""
    return ",".join(keys_in_state(VERIFIED, states))


# ---- spend ledger + per-event sweep de-duplication --------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    try:
        st = json.loads((path or STATE_PATH).read_text())
        st.setdefault("days", {})
        st.setdefault("swept", {})
        return st
    except (OSError, ValueError):
        return {"days": {}, "swept": {}}


def _save(state: dict, path: Path | None, now: dt.datetime) -> None:
    path = path or STATE_PATH
    cutoff = (now - dt.timedelta(days=STATE_KEEP_DAYS)).date().isoformat()
    state["days"] = {d: v for d, v in state["days"].items() if d >= cutoff}
    state["swept"] = {k: v for k, v in state["swept"].items() if v[:10] >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def spent_today(now: dt.datetime | None = None, path: Path | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    return int((load_state(path)["days"].get(now.date().isoformat()) or {}).get("credits", 0))


def record_spend(credits: int, now: dt.datetime | None = None, path: Path | None = None, requests_made: int = 1) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    st = load_state(path)
    day = st["days"].setdefault(now.date().isoformat(), {"credits": 0, "requests": 0})
    day["credits"] += int(credits)
    day["requests"] += int(requests_made)
    _save(st, path, now)


def may_spend(now: dt.datetime | None = None, *, path: Path | None = None, planned: int = 1,
              remaining: int | None = None) -> dict:
    """Discovery budget + account reserve. In VERIFIED_PRODUCTION the account guard alone applies."""
    from operational import odds_quota
    now = now or dt.datetime.now(dt.timezone.utc)
    if mode() == DISCOVERY and spent_today(now, path) + planned > DISCOVERY_DAILY_BUDGET:
        return {"allow": False, "reason": "DISCOVERY_DAILY_BUDGET", "budget": DISCOVERY_DAILY_BUDGET}
    remaining = odds_quota.latest_remaining() if remaining is None else remaining
    return odds_quota.evaluate_spend(remaining, odds_quota.credits_spent_today(now) if remaining is not None else 0,
                                     odds_quota.days_left_in_cycle(now.date()), planned)


def already_swept(stage: str, event_id: str, now: dt.datetime | None = None, path: Path | None = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    ts = load_state(path)["swept"].get(f"{stage}:{event_id}")
    return bool(ts) and ts[:10] == now.date().isoformat()


def mark_swept(stage: str, event_id: str, now: dt.datetime | None = None, path: Path | None = None) -> None:
    now = now or dt.datetime.now(dt.timezone.utc)
    st = load_state(path)
    st["swept"][f"{stage}:{event_id}"] = now.isoformat()
    _save(st, path, now)


# ---- market observation ---------------------------------------------------------------------------------------
def desired_markets_in(payload: dict, keys=DISCOVERY_MARKETS) -> list[str]:
    """Desired market keys that returned real outcomes in one event payload (other markets are ignored)."""
    found = []
    for bm in (payload or {}).get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") in keys and market.get("outcomes") and market["key"] not in found:
                found.append(market["key"])
    return found


# ---- discovery run --------------------------------------------------------------------------------------------
def sample_events(events: list[dict], now: dt.datetime, n: int = DISCOVERY_SAMPLE_EVENTS,
                  horizon_h: float = DISCOVERY_HORIZON_H) -> list[dict]:
    """The `n` soonest upcoming events within the horizon (props are posted closest to game day)."""
    from operational import cloud_snapshot_schema as schema
    picked = []
    for e in events:
        t = schema.parse_utc(e.get("commence_time"))
        if t is None or t <= now or (t - now) > dt.timedelta(hours=horizon_h):
            continue
        picked.append((t, e))
    return [e for _, e in sorted(picked, key=lambda p: p[0])[:n]]


def run_discovery(now: dt.datetime | None = None, *, client=None, archive=None, flag_fn: Callable | None = None,
                  state_path: Path | None = None, states: dict | None = None, guard: dict | None = None) -> dict:
    """The low-cost contract watch behind `--mode=props`. Requests ONLY still-pending desired keys on at most
    DISCOVERY_SAMPLE_EVENTS events; stops at the first appearance. Never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    summary = {"mode": "props", "prop_mode": None, "run_at_utc": now.isoformat(), "ran": False, "reason": None,
               "events_seen": 0, "events_queried": 0, "credits_spent_this_run": 0, "remaining_quota_last_seen": None,
               "api_error": None, "market_states": None, "candidate_observed": [], "discovery_daily_budget": DISCOVERY_DAILY_BUDGET}
    try:
        states = states or market_states()
        summary["market_states"] = states
        summary["prop_mode"] = mode(states)
        if summary["prop_mode"] != DISCOVERY:
            summary["reason"] = "VERIFIED_PRODUCTION: selective sweeps run production polling, not discovery"
            return summary
        pending = keys_in_state(PENDING, states)
        if not pending:
            summary["reason"] = "WAITING_FOR_CERTIFICATION: every desired market already CANDIDATE_OBSERVED -- run the deterministic certification"
            return summary
        if client is None:
            from research.live_sog_pricing import client as _client, archive as _archive
            client, archive = _client, _archive
        if flag_fn is None:
            from operational import real_prop_orchestrator as rpo
            flag_fn = rpo.flag_prop_contract_candidate_if_observed

        gate = guard or may_spend(now, path=state_path)
        if not gate.get("allow"):
            summary["reason"] = f"DEFERRED: {gate.get('reason')}"
            return summary

        r_events = client.get_nhl_events()                     # free
        if not r_events.ok:
            summary["api_error"] = r_events.error
            return summary
        summary["ran"] = True
        summary["events_seen"] = len(r_events.data)
        summary["remaining_quota_last_seen"] = int(r_events.requests_remaining or 0)
        sample = sample_events(r_events.data, now)
        if not sample:
            summary["reason"] = f"NO_EVENT_WITHIN_{DISCOVERY_HORIZON_H}H (nothing to sample; 0 credits)"
            return summary

        markets = ",".join(pending)
        for event in sample:
            if not may_spend(now, path=state_path).get("allow"):
                summary["reason"] = "STOPPED: discovery budget reached"
                break
            r_odds = client.get_event_odds(event["id"], markets=markets)
            summary["events_queried"] += 1
            if not r_odds.ok:
                summary["api_error"] = r_odds.error
                continue
            archive.archive_result(r_odds, event_id=event["id"], market_filter=markets, bookmaker_filter=SPORTSBOOK)
            cost = int(r_odds.requests_last or 0)
            summary["credits_spent_this_run"] += cost
            summary["remaining_quota_last_seen"] = int(r_odds.requests_remaining or 0)
            record_spend(cost, now, state_path)
            payload = dict(r_odds.data or {})
            payload.setdefault("id", event["id"])
            for key in desired_markets_in(payload, tuple(pending)):
                flag_fn(key, [payload])               # append-only candidate log; NEVER verifies
                summary["candidate_observed"].append(key)
            if summary["candidate_observed"]:
                summary["reason"] = "CANDIDATE_OBSERVED: stopped (no expansion until certified)"
                break
        else:
            summary["reason"] = "ABSENT: desired markets not posted on the sampled events"
    except Exception as exc:  # noqa: BLE001
        summary["api_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return summary


# ---- reporting ------------------------------------------------------------------------------------------------
def status(now: dt.datetime | None = None) -> dict:
    """PROP_DISCOVERY_MODE / PROP_DAILY_BUDGET for readiness and diagnostics (read-only, no network)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    states = market_states()
    return {"mode": mode(states), "market_states": states, "daily_budget": DISCOVERY_DAILY_BUDGET,
            "spent_today": spent_today(now), "sample_events": DISCOVERY_SAMPLE_EVENTS}


def health_invariants() -> list[dict]:
    """Configuration-level health of the discovery budget (no network, no credits): every line must hold."""
    from operational import live_odds_daily_pull as lop
    checks = [
        ("hard daily cap <= 6 credits", DISCOVERY_DAILY_BUDGET <= 6),
        ("sampled events <= 2", DISCOVERY_SAMPLE_EVENTS <= 2),
        ("only the three desired markets are ever requested",
         set(DISCOVERY_MARKETS) == {"player_shots_on_goal", "player_shots_on_goal_alternate", "player_total_saves"}),
        ("daily job requests exactly the desired markets", set(lop.TARGET_MARKETS.split(",")) == set(DISCOVERY_MARKETS)),
        ("sweeps request only desired markets", set(lop.FIRST_SWEEP_MARKETS.split(",")) <= set(DISCOVERY_MARKETS)),
        ("no unrelated market (alternate_team_totals / team_totals) requested anywhere",
         not ({"alternate_team_totals", "team_totals"} & (set(lop.TARGET_MARKETS.split(",")) | set(lop.FIRST_SWEEP_MARKETS.split(",")) | set(DISCOVERY_MARKETS)))),
        ("verified mode is derived from the contract registry only", mode({k: PENDING for k in DISCOVERY_MARKETS}) == DISCOVERY
         and mode({**{k: PENDING for k in DISCOVERY_MARKETS}, "player_total_saves": CANDIDATE}) == DISCOVERY),
    ]
    return [{"check": name, "ok": bool(ok)} for name, ok in checks]
