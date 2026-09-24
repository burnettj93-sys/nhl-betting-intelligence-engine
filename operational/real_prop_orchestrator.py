"""
Live SOG + Saves Production Certification block (2026-09-24): the
minimum orchestration layer connecting REAL DraftKings player-prop
payloads through the EXISTING, already-tested SOG and Saves intelligence
stack into the real prospective ledger / paper bankroll -- exactly the
same "bridge, don't rebuild" pattern operational/real_recommendation_
orchestrator.py already established for MONEYLINE.

This module writes NO new model math, NO new pricing/decision logic, and
NO new contract-verification policy. Every piece below already exists
and is already tested:

  - Market wire-format parsing: research/live_sog_pricing/market_parser.py
    (player_shots_on_goal / player_shots_on_goal_alternate -- the exact,
    already-documented Odds API market keys, confirmed via
    research/player_props/registry.py's own odds_api_market_key fields;
    player_total_saves uses the identical Over/Under outcome shape, so
    the SAME standard-market parser is reused unchanged for it).
  - Contract verification gate: research/generic_prop_pricing/
    provider_adapter.py::is_contract_verified() -- VERIFIED_CONTRACTS
    contains exactly one entry, ("draftkings", "MONEYLINE"). Neither
    PLAYER_SOG nor GOALIE_SAVES has ever been added, because DraftKings
    has never once returned either market in this project's entire
    archive history (confirmed by an exhaustive scan of every real
    event-shaped file under data/raw/the_odds_api/live/, 2026-09-24 --
    see docs/LIVE_SOG_SAVES_CERTIFICATION.md). This orchestrator calls
    that real gate and NEVER hardcodes a verified flag itself -- so it
    fails closed to CONTRACT_NOT_VERIFIED for every real production run
    today, exactly matching that real fact.
  - Event/player/goalie identity resolution: research/live_sog_pricing/
    event_mapping.py::map_event_to_game() (fails closed: AMBIGUOUS or
    UNMATCHED, never a guess) and player_mapping.py::map_player() (same
    fail-closed guarantee, reused UNCHANGED for goalies too via
    _build_goalie_identity_index() below, which only adapts the
    real starter-history corpus's own row shape into the same
    {player_id, player_name, most_recent_team, most_recent_game_date}
    candidate shape map_player() already expects -- no new matching
    logic).
  - Model probabilities: research/player_sog/live_projection.py::
    project_player_sog() (SOG) and dashboard/goalie_saves_view.py::
    GoalieSavesEngine.project() + StarterProbabilityEngine.project()
    (Saves, including the starter-certainty gate).
  - Pricing/decision: research/generic_prop_pricing/evaluator.py::
    evaluate_prop() -- the market-family-agnostic generalization of
    research/live_sog_pricing/pricing.py::price_observation(), proven
    numerically identical to it for SOG (tests/test_generic_prop_pricing.
    py::TestSOGParity) and already validated for GOALIE_SAVES at exactly
    the (20, 25) actionable thresholds this block's own instructions
    specify (tests/test_generic_prop_pricing.py::
    Test06GoalsAssistsPointsSavesModelSideReadiness).

Only genuinely new code here: reading real archived payloads for these
two market keys, wiring identity+model+pricing together per quote, and
adapting a PRICED/eligible result into operational/prospective_recording.
py::record_observation()'s generic shape + operational/paper_bankroll.py
paper-bet creation -- following record_sog_board_row()'s own documented
"adapt that market's own real row schema into the generic prediction
dict" pattern, without calling that function directly (it is shaped for
research/live_sog_pricing/refresh.py's own disconnected board-row output,
not this orchestrator's evaluate_prop() output).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import db
from ingest.timestamps import normalize_utc_timestamp
from operational import paper_bankroll
from operational import prospective_ledger as pl
from operational import prospective_recording as pr
from research.generic_prop_pricing import evaluator as ge
from research.live_sog_pricing import event_mapping, market_parser, player_mapping
from research.live_sog_pricing.normalized_market_adapter import quote_to_normalized_market

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "data" / "raw" / "the_odds_api" / "live"

SOG_MARKET_ID = "PLAYER_SOG"
SAVES_MARKET_ID = "GOALIE_SAVES"
SOG_VALIDATED_THRESHOLDS = (2, 3, 4, 5)     # PLAYER_SOG_FOUNDATION_REPORT.md Section AI
SAVES_VALIDATED_THRESHOLDS = (20, 25)       # GOALIE_SAVES_VALIDATION_REPORT.md


def _real_nhl_schedule(conn) -> list[dict]:
    """The REAL, current nhl.db schedule (not the frozen historical
    research corpus research/live_sog_pricing/refresh.py uses for its own
    event-mapping schedule) -- event_mapping.map_event_to_game() only
    needs {"game_id", "home_team", "away_team", "game_date"}, and this
    project's own live-synced games table already has exactly that
    shape, kept current by the already-scheduled nhl_sync jobs."""
    rows = conn.execute("SELECT game_id, home_team, away_team, game_date FROM games").fetchall()
    return [dict(r) for r in rows]


def _build_goalie_identity_index() -> dict[str, list[dict]]:
    """Adapts research/goalie_intelligence/actual_starters.jsonl's own
    real row shape (starter_goalie_id/starter_goalie_name/team/game_date)
    into the EXACT candidate shape player_mapping.build_player_index()
    already produces from the SOG corpus -- so player_mapping.map_player()
    (fail-closed, team-context disambiguation, real duplicate-name
    handling) is reused completely UNCHANGED for goalie identity too.
    This is a data-shape adapter, not new identity-matching logic."""
    starters_path = REPO_ROOT / "research" / "goalie_intelligence" / "actual_starters.jsonl"
    latest_by_goalie: dict[str, dict] = {}
    with open(starters_path) as f:
        for line in f:
            row = json.loads(line)
            gid = row["starter_goalie_id"]
            if gid not in latest_by_goalie or row["game_date"] > latest_by_goalie[gid]["game_date"]:
                latest_by_goalie[gid] = {"player_name": row["starter_goalie_name"],
                                          "team": row["team"], "game_date": row["game_date"]}
    index: dict[str, list[dict]] = {}
    for gid, info in latest_by_goalie.items():
        key = player_mapping.normalize_name(info["player_name"])
        index.setdefault(key, []).append({
            "player_id": gid, "player_name": info["player_name"],
            "most_recent_team": info["team"], "most_recent_game_date": info["game_date"],
        })
    return index


def _recent_archive_payloads(market_key: str, sportsbook: str = "draftkings",
                              max_age_hours: float = 24.0, now: dt.datetime | None = None) -> list[dict]:
    """Reads every real archived Odds API event-odds response
    (data/raw/the_odds_api/live/*.json, written by the already-scheduled
    operational.live_odds_daily_pull prop-sweep jobs -- Part 15/34) whose
    real `meta.market_filter` requested `market_key` and whose real
    `meta.retrieved_at_utc` is within `max_age_hours`. Returns each
    file's raw `response` payload (the exact shape market_parser.py
    expects) -- never a demo/simulated payload. An empty result is the
    normal, expected outcome when nothing real has been captured
    recently, or (today) ever."""
    now = now or dt.datetime.now(dt.timezone.utc)
    payloads = []
    if not ARCHIVE_DIR.is_dir():
        return payloads
    for path in ARCHIVE_DIR.glob("*.json"):
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        meta = doc.get("meta") or {}
        if market_key not in (meta.get("market_filter") or ""):
            continue
        resp = doc.get("response")
        if not isinstance(resp, dict):
            continue
        retrieved_at = meta.get("retrieved_at_utc")
        if not retrieved_at:
            continue
        try:
            captured = dt.datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - captured).total_seconds() > max_age_hours * 3600.0:
            continue
        payloads.append(resp)
    return payloads


def _record_and_maybe_paper_bet(pl_conn, bankroll_conn, *, prediction: dict, priced: dict,
                                 checkpoint: str) -> dict:
    """Shared tail for both SOG and Saves: record the real immutable
    observation, then create a REAL_MARKET_PAPER bet iff evaluate_prop()
    actually returned action == "BET". Mirrors operational.
    real_recommendation_orchestrator._process_report()'s exact pattern."""
    record_result = pr.record_observation(pl_conn, prediction, is_demo=False, checkpoint=checkpoint)
    outcome = {"status": record_result["status"], "prediction_id": record_result.get("prediction_id"),
               "action": priced.get("action"), "paper_bet_created": False}
    if record_result["status"] not in ("INSERTED", "DUPLICATE"):
        outcome["reason"] = record_result.get("reason")
        return outcome

    if priced.get("action") == "BET":
        bet_result = paper_bankroll.record_paper_bet(
            bankroll_conn, track="REAL_MARKET_PAPER", price_source="LIVE_DRAFTKINGS",
            market_id=prediction["market_id"], entry_odds=prediction["odds_american"],
            event_id=prediction["game_id"], game_date=prediction["game_date"],
            player_id=prediction.get("player_id"), player_name_snapshot=prediction.get("player_name_snapshot"),
            team=prediction.get("team"), opponent=prediction.get("opponent"),
            market_family=prediction["market_family"], threshold=prediction["threshold"],
            side=prediction["side"], model_probability=prediction["raw_probability"],
            conservative_probability=prediction["conservative_probability"],
            market_no_vig_probability=prediction.get("market_no_vig_probability"),
            edge=priced.get("conservative_edge"), ev=priced.get("conservative_ev"),
            model_version=prediction.get("model_version"), prediction_checkpoint=checkpoint,
            event_start_utc=prediction["event_start_utc"])
        outcome["paper_bet_created"] = bet_result["status"] == "INSERTED"
        outcome["paper_bet_status"] = bet_result["status"]
        outcome["paper_bet_id"] = bet_result.get("paper_bet_id")
    return outcome


def _checkpoint_for(pl_conn, *, game_id, player_id, market_id, threshold, side) -> str:
    existing = pr.latest_checkpoint_row(pl_conn, game_id=game_id, player_id=player_id, market_id=market_id,
                                         threshold=threshold, side=side, checkpoint="PRIMARY_DAILY")
    return "MARKET_REFRESH" if existing else "PRIMARY_DAILY"


def run_real_sog_recommendations(nhl_conn=None, pl_conn=None, bankroll_conn=None,
                                  max_events: int = 50, payloads: list[dict] | None = None) -> dict:
    """The real SOG production path: real archived DraftKings
    player_shots_on_goal payloads -> verified event/player identity ->
    the existing SOG model -> the existing generic pricing/decision core
    -> a real immutable observation, and a real paper bet iff BET.
    Every genuinely unverified/unmatched/ineligible quote is reported,
    never silently dropped, but never recorded as a real recommendation
    (Part 4's fail-closed rule)."""
    owns_nhl = nhl_conn is None
    owns_pl = pl_conn is None
    owns_bankroll = bankroll_conn is None
    nhl_conn = nhl_conn or db.get_conn()
    pl_conn = pl_conn or pl.init_db()
    bankroll_conn = bankroll_conn or paper_bankroll.init_db()

    summary = {"status": "SUCCESS", "payloads_scanned": 0, "quotes_seen": 0,
               "contract_not_verified": 0, "identity_unmatched": 0, "not_model_validated": 0,
               "recommendations_recorded": 0, "paper_bets_created": 0, "results": [], "error": None}
    try:
        from research import elo_comparison as ec
        from research.player_sog import features as pf
        from research.player_sog.live_projection import project_player_sog
        from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH

        schedule = _real_nhl_schedule(nhl_conn)
        real_games = ec.load_corpus(str(NHL_CORPUS_PATH))
        sog_rows = pf.load_sog_corpus()
        player_index = player_mapping.build_player_index(sog_rows)
        sog_index = pf.PlayerHistoryIndex(sog_rows)
        team_schedules = build_team_schedules(real_games)
        totals = pf.build_team_game_totals(sog_rows)
        opponent_allowed = pf.build_opponent_allowed_history(totals)
        import statistics
        league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())

        results_path = REPO_ROOT / "research" / "player_sog_results.json"
        results = json.loads(results_path.read_text())
        weights = [results["stage_weights"][results["headline_stage"]][n] for n in results["config"]["feature_names"]]
        alpha = results["negbinom_alpha_fitted"] if results["negbinom_alpha_fitted"] > 0.01 else None
        model_version = results.get("model_version", "player_sog_v1")

        payloads = payloads if payloads is not None else _recent_archive_payloads(market_parser.STANDARD_MARKET_KEY)
        payloads = payloads[:max_events]
        summary["payloads_scanned"] = len(payloads)

        for event_payload in payloads:
            quotes = market_parser.parse_event_odds_response(event_payload)
            pairs = market_parser.group_standard_two_sided(quotes)
            for _key, pair in pairs.items():
                summary["quotes_seen"] += 1
                try:
                    result = _price_and_record_sog_pair(
                        nhl_conn, pl_conn, bankroll_conn, event_payload, pair, schedule,
                        player_index, sog_rows, sog_index, team_schedules, opponent_allowed,
                        league_avg_sog_allowed, weights, alpha, model_version)
                except Exception as exc:  # noqa: BLE001 -- one bad quote must never abort the batch
                    result = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "recorded": False}
                summary["results"].append(result)
                _tally(summary, result)
    except Exception as exc:  # noqa: BLE001 -- report, never crash the caller
        summary["status"] = "ERROR"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_nhl:
            nhl_conn.close()
        if owns_pl:
            pl_conn.close()
        if owns_bankroll:
            bankroll_conn.close()
    return summary


def _tally(summary: dict, result: dict) -> None:
    status = result.get("status")
    if status == ge.CONTRACT_NOT_VERIFIED:
        summary["contract_not_verified"] += 1
    elif status in ("UNMATCHED", "AMBIGUOUS"):
        summary["identity_unmatched"] += 1
    elif status == ge.NOT_MODEL_VALIDATED:
        summary["not_model_validated"] += 1
    elif result.get("recorded"):
        summary["recommendations_recorded"] += 1
    if result.get("paper_bet_created"):
        summary["paper_bets_created"] += 1


def _price_and_record_sog_pair(nhl_conn, pl_conn, bankroll_conn, event_payload, pair, schedule,
                                player_index, sog_rows, sog_index, team_schedules, opponent_allowed,
                                league_avg_sog_allowed, weights, alpha, model_version) -> dict:
    over_q, under_q = pair.get("over"), pair.get("under")
    any_q = over_q or under_q
    if any_q is None:
        return {"status": "DATA_UNAVAILABLE", "reason": "no quote in pair", "recorded": False}

    home_abbrev = event_mapping.normalize_team_name(event_payload.get("home_team", ""))
    away_abbrev = event_mapping.normalize_team_name(event_payload.get("away_team", ""))
    mapping = event_mapping.map_event_to_game(event_payload, schedule)
    if mapping["status"] != "MATCHED":
        return {"status": mapping["status"], "reason": mapping["reason"],
                "player_name_raw": any_q["player_name_raw"], "recorded": False}

    pmap = player_mapping.map_player(any_q["player_name_raw"], home_abbrev, away_abbrev, player_index)
    if pmap["status"] != "MATCHED":
        return {"status": pmap["status"], "reason": pmap["reason"],
                "player_name_raw": any_q["player_name_raw"], "recorded": False}

    player_id = pmap["player_id"]
    prediction_date = event_payload["commence_time"][:10]
    candidates = player_index.get(player_mapping.normalize_name(any_q["player_name_raw"]), [])
    recent_team = next((c["most_recent_team"] for c in candidates if c["player_id"] == player_id), home_abbrev)
    team = recent_team if recent_team in (home_abbrev, away_abbrev) else home_abbrev
    opponent = away_abbrev if team == home_abbrev else home_abbrev
    year, month = int(prediction_date[:4]), int(prediction_date[5:7])
    season_start_year = year if month >= 7 else year - 1
    season = season_start_year * 10000 + (season_start_year + 1)

    from research.player_sog.live_projection import project_player_sog
    view = project_player_sog(sog_rows, sog_index, team_schedules, opponent_allowed,
                               league_avg_sog_allowed, weights, alpha, player_id, team, opponent,
                               prediction_date, season)
    if view["status"] != "PROJECTED_ACTIVE":
        return {"status": view["status"], "player_id": player_id, "recorded": False}

    from research.live_sog_pricing.pricing import threshold_from_point
    q = over_q or under_q
    threshold = threshold_from_point(q["point"])
    side = "OVER" if over_q else "UNDER"
    opposing_price = (under_q or {}).get("price_american") if over_q else (over_q or {}).get("price_american")
    market, contract_verified = quote_to_normalized_market(
        q, market_family=SOG_MARKET_ID, canonical_market_id=f"{SOG_MARKET_ID}_{threshold}PLUS",
        threshold=threshold, side=side, opposing_price=opposing_price, player_id=player_id)

    priced = ge.evaluate_prop(
        market_family=SOG_MARKET_ID, model_validated_thresholds=SOG_VALIDATED_THRESHOLDS,
        threshold=threshold, side=side, probs=view["probs"], conservative_probs=view["conservative_probs"],
        confidence=view["confidence"], lineup_status="PROJECTED/UNCONFIRMED", market=market,
        provider_contract_verified=contract_verified)

    if priced["status"] != ge.PRICED:
        return {"status": priced["status"], "reason": priced.get("reason"), "player_id": player_id,
                "recorded": False}

    checkpoint = _checkpoint_for(pl_conn, game_id=str(mapping["game_id"]), player_id=player_id,
                                  market_id=f"{SOG_MARKET_ID}_{threshold}PLUS", threshold=f"{threshold}+",
                                  side=side)
    event_start_utc = normalize_utc_timestamp(event_payload["commence_time"])
    # created_at_utc/prediction_cutoff_utc/odds_captured_at_utc are pinned to
    # the REAL quote's own bookmaker_last_update_utc (when this observation
    # was actually captured) -- always strictly before event_start_utc in a
    # real scenario, matching operational.real_recommendation_orchestrator's
    # identical convention for MONEYLINE, and normalized through this
    # project's canonical timestamp function (never a bare ".replace('Z','')"
    # -- see real_odds_bridge.py's own documented bug for why that failed).
    observed_at_utc = normalize_utc_timestamp(q.get("bookmaker_last_update_utc")) or event_start_utc
    prediction = {
        "record_type": "MODEL_OBSERVATION", "model_id": "PLAYER_SOG",
        "event_start_utc": event_start_utc,
        "created_at_utc": observed_at_utc,
        "prediction_cutoff_utc": observed_at_utc,
        "game_id": str(mapping["game_id"]), "game_date": prediction_date,
        "player_id": player_id, "player_name_snapshot": any_q["player_name_raw"],
        "team": team, "opponent": opponent,
        "market_id": f"{SOG_MARKET_ID}_{threshold}PLUS", "market_family": "SOG",
        "threshold": f"{threshold}+", "side": side,
        "raw_probability": priced["model_probability"], "context_adjusted_probability": priced["model_probability"],
        "coherent_probability": priced["model_probability"], "conservative_probability": priced["conservative_probability"],
        "confidence": view["confidence"], "model_version": model_version,
        "sportsbook": "DraftKings", "market_key": q["market_key"], "line": q.get("point"),
        "odds_american": q["price_american"], "market_no_vig_probability": priced.get("market_no_vig_probability"),
        "odds_captured_at_utc": observed_at_utc,
        "prospective_status": priced["action"],
    }
    outcome = _record_and_maybe_paper_bet(pl_conn, bankroll_conn, prediction=prediction, priced=priced,
                                          checkpoint=checkpoint)
    outcome["recorded"] = outcome["status"] in ("INSERTED", "DUPLICATE")
    return outcome


# ---------------------------------------------------------------------
# GOALIE SAVES
# ---------------------------------------------------------------------

NO_STARTER_CONFIRMATION_SOURCE = (
    "no real starter-confirmation source exists yet (docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md) -- "
    "a projected/expected starter is never treated as a confirmed one for Saves, matching the "
    "identical goalie-confirmation discipline pricing/engine.py already enforces for MONEYLINE"
)


def _apply_starter_certainty_gate(priced: dict, *, matched_goalie_id: str,
                                   starter_projection: dict | None) -> dict:
    """Part 12: Saves is especially starter-sensitive, and this project
    has no real CONFIRMED-starter source for ANY market yet (Part 13,
    docs/CONTEXT_DATA_DEPENDENCY_AUDIT.md). A PROJECTED starter -- even
    the model's own top-ranked candidate -- is never assumed to be a
    CONFIRMED one, so this gate ALWAYS overrides a would-be BET/WATCH to
    WAIT today; it mirrors, rather than duplicates, pricing/engine.py's
    own MONEYLINE goalie-confirmation gate (WAIT unless CONFIRMED). Never
    touches a PASS (nothing to wait on) or an already-gated status
    (NOT_MODEL_VALIDATED/CONTRACT_NOT_VERIFIED/DATA_UNAVAILABLE). The
    real starter_projection is still recorded in the reason for
    diagnostic visibility, even though it can never itself satisfy the
    gate."""
    if priced["status"] != ge.PRICED or priced["action"] not in ("BET", "WATCH"):
        return priced
    top_candidate = None
    if starter_projection and starter_projection.get("candidates"):
        top_candidate = max(starter_projection["candidates"], key=lambda c: c[1])
    is_top_projected = bool(top_candidate) and str(top_candidate[0]) == str(matched_goalie_id)
    gated = dict(priced)
    gated["action"] = "WAIT"
    gated["action_reason"] = (
        f"would otherwise be {priced['action']} ({priced['action_reason'] or 'edge/EV clear'}), but "
        f"{NO_STARTER_CONFIRMATION_SOURCE} (model-projected top starter: "
        f"{'this goalie' if is_top_projected else 'a different goalie'})")
    return gated


def run_real_saves_recommendations(nhl_conn=None, pl_conn=None, bankroll_conn=None,
                                    max_events: int = 50, payloads: list[dict] | None = None) -> dict:
    """The real GOALIE SAVES production path: real archived DraftKings
    player_total_saves payloads -> verified event/goalie identity ->
    the existing GoalieSavesEngine + StarterProbabilityEngine -> the
    existing generic pricing/decision core -> a real immutable
    observation. A real paper bet is created iff BET -- which, per the
    starter-certainty gate above, can never happen until a real
    starter-confirmation source exists (Part 12/13); this is intentional
    and matches this project's identical MONEYLINE discipline, not a
    bug to route around."""
    owns_nhl = nhl_conn is None
    owns_pl = pl_conn is None
    owns_bankroll = bankroll_conn is None
    nhl_conn = nhl_conn or db.get_conn()
    pl_conn = pl_conn or pl.init_db()
    bankroll_conn = bankroll_conn or paper_bankroll.init_db()

    summary = {"status": "SUCCESS", "payloads_scanned": 0, "quotes_seen": 0,
               "contract_not_verified": 0, "identity_unmatched": 0, "not_model_validated": 0,
               "recommendations_recorded": 0, "paper_bets_created": 0, "results": [], "error": None}
    try:
        from dashboard.goalie_saves_view import GoalieSavesEngine, StarterProbabilityEngine, load_results, load_starter_results

        schedule = _real_nhl_schedule(nhl_conn)
        goalie_index = _build_goalie_identity_index()
        saves_results = load_results()
        starter_results = load_starter_results()
        if saves_results is None or starter_results is None:
            summary["status"] = "SUCCESS"
            summary["error"] = "goalie saves or starter model results not found on disk -- nothing to evaluate"
            return summary
        saves_engine = GoalieSavesEngine(saves_results)
        starter_engine = StarterProbabilityEngine(starter_results)
        model_version = saves_results.get("model_version", "goalie_saves_v1")

        payloads = payloads if payloads is not None else _recent_archive_payloads(market_parser.SAVES_MARKET_KEY)
        payloads = payloads[:max_events]
        summary["payloads_scanned"] = len(payloads)

        for event_payload in payloads:
            quotes = market_parser.parse_event_odds_response(
                event_payload, standard_market_keys=(market_parser.SAVES_MARKET_KEY,))
            pairs = market_parser.group_standard_two_sided(quotes, market_key=market_parser.SAVES_MARKET_KEY)
            for _key, pair in pairs.items():
                summary["quotes_seen"] += 1
                try:
                    result = _price_and_record_saves_pair(
                        pl_conn, bankroll_conn, event_payload, pair, schedule, goalie_index,
                        saves_engine, starter_engine, model_version)
                except Exception as exc:  # noqa: BLE001 -- one bad quote must never abort the batch
                    result = {"status": "ERROR", "reason": f"{exc.__class__.__name__}: {exc}", "recorded": False}
                summary["results"].append(result)
                _tally(summary, result)
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "ERROR"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        if owns_nhl:
            nhl_conn.close()
        if owns_pl:
            pl_conn.close()
        if owns_bankroll:
            bankroll_conn.close()
    return summary


def _price_and_record_saves_pair(pl_conn, bankroll_conn, event_payload, pair, schedule, goalie_index,
                                  saves_engine, starter_engine, model_version) -> dict:
    over_q, under_q = pair.get("over"), pair.get("under")
    any_q = over_q or under_q
    if any_q is None:
        return {"status": "DATA_UNAVAILABLE", "reason": "no quote in pair", "recorded": False}

    home_abbrev = event_mapping.normalize_team_name(event_payload.get("home_team", ""))
    away_abbrev = event_mapping.normalize_team_name(event_payload.get("away_team", ""))
    mapping = event_mapping.map_event_to_game(event_payload, schedule)
    if mapping["status"] != "MATCHED":
        return {"status": mapping["status"], "reason": mapping["reason"],
                "player_name_raw": any_q["player_name_raw"], "recorded": False}

    gmap = player_mapping.map_player(any_q["player_name_raw"], home_abbrev, away_abbrev, goalie_index)
    if gmap["status"] != "MATCHED":
        return {"status": gmap["status"], "reason": gmap["reason"],
                "player_name_raw": any_q["player_name_raw"], "recorded": False}

    goalie_id = gmap["player_id"]
    prediction_date = event_payload["commence_time"][:10]
    candidates = goalie_index.get(player_mapping.normalize_name(any_q["player_name_raw"]), [])
    recent_team = next((c["most_recent_team"] for c in candidates if c["player_id"] == goalie_id), home_abbrev)
    team = recent_team if recent_team in (home_abbrev, away_abbrev) else home_abbrev
    opponent = away_abbrev if team == home_abbrev else home_abbrev
    home_away = "home" if team == home_abbrev else "away"
    year, month = int(prediction_date[:4]), int(prediction_date[5:7])
    season_start_year = year if month >= 7 else year - 1
    season = season_start_year * 10000 + (season_start_year + 1)

    starter_projection = starter_engine.project(team, prediction_date)

    proj = saves_engine.project(int(goalie_id), team, opponent, home_away, int(mapping["game_id"]),
                                 prediction_date, season)
    if proj is None:
        return {"status": "INSUFFICIENT_HISTORY", "player_id": goalie_id, "recorded": False}

    threshold_probs = {t: proj[f"prob_{t}plus"] for t in (20, 25, 30, 35, 40)}
    # Same conservative-shrinkage convention as SOG/dashboard.eligible_bets.py's
    # goalie rows -- reused, not re-derived (see dashboard/eligible_bets.py's
    # own `conservative_probability = raw_p * 0.9` for the identical pattern).
    conservative_probs = {t: p * 0.9 for t, p in threshold_probs.items()}

    from research.live_sog_pricing.pricing import threshold_from_point
    q = over_q or under_q
    threshold = threshold_from_point(q["point"])
    side = "OVER" if over_q else "UNDER"
    opposing_price = (under_q or {}).get("price_american") if over_q else (over_q or {}).get("price_american")
    market, contract_verified = quote_to_normalized_market(
        q, market_family=SAVES_MARKET_ID, canonical_market_id=f"{SAVES_MARKET_ID}_{threshold}PLUS",
        threshold=threshold, side=side, opposing_price=opposing_price, goalie_id=goalie_id)

    priced = ge.evaluate_prop(
        market_family=SAVES_MARKET_ID, model_validated_thresholds=SAVES_VALIDATED_THRESHOLDS,
        threshold=threshold, side=side, probs=threshold_probs, conservative_probs=conservative_probs,
        confidence=proj["confidence"], lineup_status="PROJECTED/UNCONFIRMED", market=market,
        provider_contract_verified=contract_verified)

    if priced["status"] != ge.PRICED:
        return {"status": priced["status"], "reason": priced.get("reason"), "player_id": goalie_id,
                "recorded": False}

    priced = _apply_starter_certainty_gate(priced, matched_goalie_id=goalie_id,
                                            starter_projection=starter_projection)

    checkpoint = _checkpoint_for(pl_conn, game_id=str(mapping["game_id"]), player_id=goalie_id,
                                  market_id=f"{SAVES_MARKET_ID}_{threshold}PLUS", threshold=f"{threshold}+",
                                  side=side)
    event_start_utc = normalize_utc_timestamp(event_payload["commence_time"])
    observed_at_utc = normalize_utc_timestamp(q.get("bookmaker_last_update_utc")) or event_start_utc
    prediction = {
        "record_type": "MODEL_OBSERVATION", "model_id": "GOALIE_SAVES",
        "event_start_utc": event_start_utc,
        "created_at_utc": observed_at_utc,
        "prediction_cutoff_utc": observed_at_utc,
        "game_id": str(mapping["game_id"]), "game_date": prediction_date,
        "player_id": goalie_id, "player_name_snapshot": any_q["player_name_raw"],
        "team": team, "opponent": opponent,
        "market_id": f"{SAVES_MARKET_ID}_{threshold}PLUS", "market_family": "GOALIE_SAVES",
        "threshold": f"{threshold}+", "side": side,
        "raw_probability": priced["model_probability"], "context_adjusted_probability": priced["model_probability"],
        "coherent_probability": priced["model_probability"], "conservative_probability": priced["conservative_probability"],
        "confidence": proj["confidence"], "model_version": model_version,
        "sportsbook": "DraftKings", "market_key": q["market_key"], "line": q.get("point"),
        "odds_american": q["price_american"], "market_no_vig_probability": priced.get("market_no_vig_probability"),
        "odds_captured_at_utc": observed_at_utc,
        "prospective_status": priced["action"],
    }
    outcome = _record_and_maybe_paper_bet(pl_conn, bankroll_conn, prediction=prediction, priced=priced,
                                          checkpoint=checkpoint)
    outcome["recorded"] = outcome["status"] in ("INSERTED", "DUPLICATE")
    return outcome


if __name__ == "__main__":
    import json as _json
    sog_result = run_real_sog_recommendations()
    saves_result = run_real_saves_recommendations()
    print(json.dumps({"sog": sog_result, "saves": saves_result}, indent=2, default=str))
