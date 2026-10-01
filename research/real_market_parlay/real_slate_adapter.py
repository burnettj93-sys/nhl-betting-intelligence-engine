"""
Real-Slate Parlay Adapter (Real-Slate Parlay Certification block, 2026-09-29).
Converts REAL, currently-stored production data for MONEYLINE and
PLAYER_SOG_ALTERNATE into research.real_market_parlay.engine.ParlayLeg
objects. Never a second eligibility system: every hard gate this module
checks is either the SAME check research/real_market_parlay/engine.py's own
leg_is_eligible() will re-verify, or an already-existing, already-tested
production function --
  MONEYLINE: pricing/engine.py::evaluate_moneyline_for_game() (the real T-35
    decision engine -- unchanged, untouched, this module never re-derives
    its freshness/goalie-confirmation/pricing logic).
  PLAYER_SOG_ALTERNATE: research/live_sog_pricing/market_parser.py +
    event_mapping.py + player_mapping.py (real, tested parsing/identity) and
    research/player_sog/live_projection.py::project_player_sog() (the real
    model) -- the exact same setup operational/real_prop_orchestrator.py::
    run_real_sog_recommendations() already builds, reused verbatim rather
    than re-derived, only assembled around the CERTIFIED alternate-ladder
    shape that orchestrator does not yet consume (a known, separate wiring
    gap -- see provider_adapter.VERIFIED_CONTRACTS's own comment).

NO DEMO / SYNTHETIC FALLBACK (hard rule): if real data is unavailable,
unresolved, unverified, or stale, the candidate is EXCLUDED with a real,
specific reason -- never substituted, never guessed, never backfilled from a
demo or historical source. Every function here is read-only over already-
stored data (nhl.db, already-retained archive files) -- none of them ever
makes a new sportsbook API request.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path

from pricing import odds_math
from research.generic_prop_pricing import provider_adapter as pa
from research.generic_prop_pricing.line_mapping import NonHalfPointLineError, SOG_ACTIONABLE_THRESHOLDS, line_to_threshold
from research.real_market_parlay.engine import ParlayLeg

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _price_freshness(captured_at_utc: str | None, game_start_utc: str, now: dt.datetime) -> dict:
    """Reuses pricing/odds_math.py's own dynamic, time-to-puck-drop-sensitive
    staleness policy (odds_math.dynamic_max_staleness_minutes /
    config.ODDS_STALENESS_TIERS) -- the SAME function pricing/engine.py's
    real MONEYLINE decision engine already uses -- never a second, looser
    staleness rule invented for SOG."""
    if not captured_at_utc:
        return {"fresh": False, "reason": "NO_PRICE_TIMESTAMP", "age_minutes": None, "max_age_minutes": None}
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    hours_to_puck_drop = odds_math.hours_between(now_iso, game_start_utc)
    if hours_to_puck_drop <= 0:
        return {"fresh": False, "reason": "EVENT_ALREADY_STARTED", "age_minutes": None, "max_age_minutes": None}
    max_age = odds_math.dynamic_max_staleness_minutes(hours_to_puck_drop)
    age_minutes = odds_math.hours_between(captured_at_utc, now_iso) * 60.0
    fresh = 0 <= age_minutes <= max_age
    return {"fresh": fresh, "reason": None if fresh else "STALE_PRICE",
            "age_minutes": round(age_minutes, 1), "max_age_minutes": max_age}


def moneyline_candidate_legs(conn, now: dt.datetime | None = None) -> tuple[list[ParlayLeg], list[dict]]:
    """Real, current MONEYLINE candidates for every SCHEDULED game, using the
    REAL, unmodified T-35 decision engine (pricing.engine.evaluate_moneyline_for_game)
    -- this function itself already enforces real freshness (dynamic staleness
    policy) and goalie-confirmation; this adapter adds NO second freshness or
    confirmation check, it only translates a real, already-gated BetReport
    into a ParlayLeg or records why it was excluded. Read-only: never calls
    record_observation() or record_paper_bet() -- T-35's own production
    behavior is completely untouched by this exercise."""
    from run_slate import build_prediction_for_game
    from pricing import engine as pricing_engine
    from operational import eastern_time as et

    now = now or dt.datetime.now(dt.timezone.utc)
    legs: list[ParlayLeg] = []
    excluded: list[dict] = []

    contract_verified = pa.is_contract_verified("draftkings", "MONEYLINE")

    # Production Gap Closure sprint (2026-09-30): this previously scanned
    # EVERY scheduled game in the database regardless of date, while the
    # visible Today slate (dashboard/real_today_view.py) and this same
    # trader's own SOG leg pool are both scoped to today's real Eastern
    # hockey day. A real MONEYLINE leg from a game several days out could
    # get staked into "today's" parlay even though no human viewing Today
    # would ever call it part of today's slate -- restricted to the SAME
    # ET calendar date every other real "today" surface in this project uses.
    today_et = et.eastern_today(now)
    scheduled_games = conn.execute(
        "SELECT game_id, scheduled_start_utc FROM games WHERE game_state = 'SCHEDULED' AND game_date = ? "
        "ORDER BY game_date, game_id", (today_et,)).fetchall()

    for row in scheduled_games:
        game_id, scheduled_start_utc = row["game_id"], row["scheduled_start_utc"]
        # Checked against the DB's own real scheduled_start_utc BEFORE calling
        # build_prediction_for_game(): that function raises for a game whose
        # start has already passed (it can no longer reconstruct a valid
        # point-in-time model state "as of" a future-relative-to-itself
        # moment), so this must never be deferred until after that call.
        if scheduled_start_utc and now >= dt.datetime.fromisoformat(scheduled_start_utc).replace(tzinfo=dt.timezone.utc):
            excluded.append({"identifier": f"game:{game_id}", "market_family": "MONEYLINE",
                              "reason": "EVENT_ALREADY_STARTED"})
            continue

        try:
            pred = build_prediction_for_game(conn, game_id)
        except Exception as exc:  # noqa: BLE001 -- one bad game must never abort the slate
            excluded.append({"identifier": f"game:{game_id}", "market_family": "MONEYLINE",
                              "reason": f"PREDICTION_ERROR: {exc.__class__.__name__}: {exc}"})
            continue

        if now >= dt.datetime.fromisoformat(pred.scheduled_start_utc).replace(tzinfo=dt.timezone.utc):
            excluded.append({"identifier": f"game:{game_id}", "market_family": "MONEYLINE",
                              "reason": "EVENT_ALREADY_STARTED"})
            continue

        label = f"{pred.away_team} @ {pred.home_team} ({pred.game_date[:10]})"
        reports = pricing_engine.evaluate_moneyline_for_game(conn, pred, label)
        for report in reports:
            identifier = f"game:{game_id}:{report.selection}"
            if report.action == "DATA_UNAVAILABLE":
                excluded.append({"identifier": identifier, "market_family": "MONEYLINE",
                                  "reason": f"DATA_UNAVAILABLE (includes stale/missing/suspended/post-start): "
                                            f"{report.action_reason}"})
                continue
            if report.action == "WAIT":
                excluded.append({"identifier": identifier, "market_family": "MONEYLINE",
                                  "reason": f"GOALIE_NOT_CONFIRMED: {report.action_reason}"})
                continue
            if report.model_conservative_probability is None or not (0.0 < report.model_conservative_probability < 1.0):
                excluded.append({"identifier": identifier, "market_family": "MONEYLINE",
                                  "reason": "CONSERVATIVE_PROBABILITY_UNAVAILABLE"})
                continue
            if not contract_verified:
                excluded.append({"identifier": identifier, "market_family": "MONEYLINE",
                                  "reason": "CONTRACT_NOT_VERIFIED"})
                continue

            snap_row = conn.execute("SELECT captured_at_utc FROM odds_snapshots WHERE id = ?",
                                     (report.odds_snapshot_id_selection,)).fetchone()
            captured_at_utc = snap_row["captured_at_utc"] if snap_row else None

            legs.append(ParlayLeg(
                game_id=str(game_id), event_id=None, market_family="MONEYLINE",
                participant_id=report.selection, participant_name=report.selection,
                side="HOME" if report.selection == pred.home_team else "AWAY", threshold=None,
                american_price=report.current_draftkings_price,
                conservative_probability=report.model_conservative_probability,
                sportsbook="draftkings", captured_at_utc=captured_at_utc,
                provider_contract_verified=True, model_threshold_eligible=True,
                identity_resolved=True, price_fresh=True, event_not_started=True,
            ))
    return legs, excluded


def _sog_model_inputs():
    """Builds the exact same real model-input setup
    operational/real_prop_orchestrator.py::run_real_sog_recommendations()
    already builds -- reused verbatim, never re-derived, so this adapter can
    never silently drift from the real, validated SOG model's real inputs."""
    from research import elo_comparison as ec
    from research.player_sog import features as pf
    from research.run_player_sog_model import NHL_CORPUS_PATH, build_team_schedules

    real_games = ec.load_corpus(str(NHL_CORPUS_PATH))
    sog_rows = pf.load_sog_corpus()
    sog_index = pf.PlayerHistoryIndex(sog_rows)
    team_schedules = build_team_schedules(real_games)
    totals = pf.build_team_game_totals(sog_rows)
    opponent_allowed = pf.build_opponent_allowed_history(totals)
    league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())

    results_path = REPO_ROOT / "research" / "player_sog_results.json"
    results = json.loads(results_path.read_text())
    weights = [results["stage_weights"][results["headline_stage"]][n] for n in results["config"]["feature_names"]]
    alpha = results["negbinom_alpha_fitted"] if results["negbinom_alpha_fitted"] > 0.01 else None
    return sog_rows, sog_index, team_schedules, opponent_allowed, league_avg_sog_allowed, weights, alpha


def sog_alternate_candidate_legs(conn, archive_payloads: list[dict],
                                  now: dt.datetime | None = None) -> tuple[list[ParlayLeg], list[dict]]:
    """`archive_payloads`: real, already-retained raw Odds API event-odds
    response dicts (never fetched here -- the caller sources these from
    operational/odds_archive/live/, per Section 9's STOP-before-spending
    rule). Real event mapping -> real player identity -> real threshold
    mapping -> real contract certification -> real model projection ->
    real freshness policy, in that order; any failure at any stage is
    reported and EXCLUDED, never guessed past."""
    from operational import eastern_time as et
    from operational.real_prop_orchestrator import _real_nhl_schedule
    from research.live_sog_pricing import event_mapping, market_parser, player_mapping
    from research.player_sog.live_projection import corpus_covers_date, project_player_sog

    now = now or dt.datetime.now(dt.timezone.utc)
    legs: list[ParlayLeg] = []
    excluded: list[dict] = []

    schedule = _real_nhl_schedule(conn)
    sog_rows, sog_index, team_schedules, opponent_allowed, league_avg_sog_allowed, weights, alpha = \
        _sog_model_inputs()
    player_index = player_mapping.build_player_index(sog_rows)

    for payload in archive_payloads:
        provider_event_id = payload.get("id")
        event_map = event_mapping.map_event_to_game(payload, schedule)
        if event_map["status"] != "MATCHED":
            excluded.append({"identifier": f"event:{provider_event_id}", "market_family": "PLAYER_SOG_ALTERNATE",
                              "reason": f"EVENT_{event_map['status']}: {event_map['reason']}"})
            continue
        game_id = event_map["game_id"]
        game_row = conn.execute("SELECT game_state, scheduled_start_utc FROM games WHERE game_id = ?",
                                 (game_id,)).fetchone()
        if game_row is None or game_row["game_state"] != "SCHEDULED":
            excluded.append({"identifier": f"event:{provider_event_id}", "market_family": "PLAYER_SOG_ALTERNATE",
                              "reason": "EVENT_ALREADY_STARTED_OR_UNKNOWN"})
            continue

        home_abbrev = event_mapping.normalize_team_name(payload.get("home_team", ""))
        away_abbrev = event_mapping.normalize_team_name(payload.get("away_team", ""))
        quotes = market_parser.parse_event_odds_response(payload)
        ladder = market_parser.group_alternate_ladder(quotes)

        for (prov_event_id, bookmaker, player_name_raw, market_last_update), by_point in ladder.items():
            identity_base = f"event:{prov_event_id}:{player_name_raw}"
            pmap = player_mapping.map_player(player_name_raw, home_abbrev, away_abbrev, player_index)
            if pmap["status"] != "MATCHED":
                excluded.append({"identifier": identity_base, "market_family": "PLAYER_SOG_ALTERNATE",
                                  "reason": f"IDENTITY_{pmap['status']}: {pmap['reason']}"})
                continue
            player_id = pmap["player_id"]

            candidates = player_index.get(player_mapping.normalize_name(player_name_raw), [])
            recent_team = next((c["most_recent_team"] for c in candidates if c["player_id"] == player_id),
                                home_abbrev)
            team = recent_team if recent_team in (home_abbrev, away_abbrev) else home_abbrev
            opponent = away_abbrev if team == home_abbrev else home_abbrev
            prediction_date = et.eastern_date_of(payload["commence_time"])
            year, month = int(prediction_date[:4]), int(prediction_date[5:7])
            season_start_year = year if month >= 7 else year - 1
            season = season_start_year * 10000 + (season_start_year + 1)

            for point, quote in by_point.items():
                identifier = f"{identity_base}:point={point}"
                try:
                    threshold = line_to_threshold(point)
                except NonHalfPointLineError as exc:
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": f"MALFORMED_LINE: {exc}"})
                    continue
                if threshold not in SOG_ACTIONABLE_THRESHOLDS:
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": f"MODEL_THRESHOLD_NOT_ACTIONABLE ({threshold}+ -- only "
                                                f"{sorted(SOG_ACTIONABLE_THRESHOLDS)} are validated)"})
                    continue

                parsed = pa.parse_the_odds_api_market(
                    quote, sportsbook=bookmaker, canonical_market_id="PLAYER_SOG_ALTERNATE",
                    event_id=prov_event_id, player_id=player_id)
                if parsed["status"] != "PARSED":
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": f"{parsed['status']}: {parsed.get('reason')}"})
                    continue
                market = parsed["market"]

                freshness = _price_freshness(market.captured_at_utc, payload["commence_time"], now)
                if not freshness["fresh"]:
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": f"{freshness['reason']} (age={freshness['age_minutes']}min, "
                                                f"allowed<={freshness['max_age_minutes']}min)"
                                                if freshness["age_minutes"] is not None else freshness["reason"]})
                    continue

                coverage = corpus_covers_date(team_schedules, team, prediction_date)
                if not coverage["covers"]:
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": "MODEL_CORPUS_STALE"})
                    continue

                view = project_player_sog(sog_rows, sog_index, team_schedules, opponent_allowed,
                                           league_avg_sog_allowed, weights, alpha, player_id, team, opponent,
                                           prediction_date, season)
                if view["status"] != "PROJECTED_ACTIVE":
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": f"MODEL_{view['status']}"})
                    continue
                conservative_prob = view["conservative_probs"].get(threshold)
                if conservative_prob is None or not (0.0 < conservative_prob < 1.0):
                    excluded.append({"identifier": identifier, "market_family": "PLAYER_SOG_ALTERNATE",
                                      "reason": "CONSERVATIVE_PROBABILITY_UNAVAILABLE"})
                    continue

                legs.append(ParlayLeg(
                    game_id=str(game_id), event_id=prov_event_id, market_family="PLAYER_SOG_ALTERNATE",
                    participant_id=player_id, participant_name=player_name_raw, side=market.side,
                    threshold=threshold, american_price=market.american_price,
                    conservative_probability=conservative_prob, sportsbook=bookmaker,
                    captured_at_utc=market.captured_at_utc, provider_contract_verified=True,
                    model_threshold_eligible=True, identity_resolved=True, price_fresh=True,
                    event_not_started=True,
                ))
    return legs, excluded
