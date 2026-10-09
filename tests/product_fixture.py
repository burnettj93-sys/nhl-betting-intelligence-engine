"""A small, hand-built published-snapshot document for page tests (two teams, one game today, one final yesterday). No real data,
and nothing here can be mistaken for production content: ids and names are obviously test values."""
from __future__ import annotations

import copy

from operational import player_options as po
from tests.test_daily_tickets import board

GEN = "2026-10-08T12:00:00Z"


def player(pid, name, team, pos="C", line=1, pp=1, opp="MTL", gid="2026020900", with_projection=True, reported=None):
    proj = None
    if with_projection:
        proj = {"opponent": opp, "expected": {"shots": 2.4, "goals": 0.3, "assists": 0.4, "points": 0.7, "hits": 1.0, "blocks": 0.5, "toi": 19.5, "toi_pp": 2.9},
                "games_observed": 120, "limited_history": False, "pricing_eligible": True, "model_version": "player-rate-toi-v2",
                "probabilities": {k: 0.5 for k in ("shots>=1", "shots>=2", "shots>=3", "shots>=4", "shots>=5", "points>=1", "points>=2", "goals>=1", "assists>=1",
                                                   "hits>=1", "hits>=2", "hits>=3", "blocks>=1", "blocks>=2")}}
    return {"player_id": pid, "name": name, "team": team, "position": pos, "usage_tier": line, "pp_usage": pp, "reported": reported,
            "usage_source": "Inferred from time on ice in each player's last 4 game(s), ranked within " + team + ". An estimate of usage, not an assigned line or power-play unit.",
            "role_games": 4, "season": {"games": 4, "goals": 2.0, "assists": 3.0, "points": 5.0, "shots": 14.0, "hits": 6.0, "blocks": 2.0, "toi_avg": 19.0, "toi_pp_avg": 2.5},
            "recent_games": [{"date": "2026-10-06", "opp": opp, "home": True, "toi": 19.0, "toi_pp": 2.5, "shots": 3.0, "goals": 1.0, "assists": 0.0, "hits": 1.0, "blocks": 0.0}],
            "recent_avg": {"toi": 19.0, "toi_pp": 2.5, "shots": 3.0, "goals": 0.5, "assists": 0.5, "hits": 1.0, "blocks": 0.3}, "last_game_date": "2026-10-06", "games_total": 120,
            "next_game": {"game_id": gid, "date_et": "2026-10-08", "start_utc": "2026-10-08T23:00:00Z", "start_et": "7:00 PM ET", "opp": opp, "home": True},
            "projection": proj, "option_id": None}


def goalie(pid, name, team, opp="MTL", gid="2026020900"):
    return {"player_id": pid, "name": name, "team": team, "catches": "L", "sweater": 30,
            "season": {"games": 2, "starts": 2, "wins": 1, "losses": 1, "ot_losses": 0, "save_pct": 0.905, "gaa": 2.5, "shutouts": 0, "shots_against": 60, "goals_against": 6},
            "season_source": {"name": "NHL.com player page", "fetched_at_utc": GEN, "last_ok_utc": GEN, "error": None},
            "recent_starts": [{"date": "2026-10-06", "season": "current", "opp": opp, "shots_against": 30.0, "saves": 27.0, "goals_against": 3.0, "save_pct": 0.9, "started": True, "toi": 60.0}],
            "games_in_log": 80, "next_game": {"game_id": gid, "date_et": "2026-10-08", "start_utc": "2026-10-08T23:00:00Z", "start_et": "7:00 PM ET", "opp": opp, "home": True},
            "start": {"probability": 0.7, "basis": "x", "kind": "ESTIMATE", "back_to_back": False, "share_last10": 0.8},
            "projection": {"opponent": opp, "expected_shots_against": 29.0, "save_pct_used": 0.905, "expected_saves": 26.2, "expected_goals_against": 2.8, "goals_against_range_80": [1, 5],
                           "saves_range_80": [22, 31], "saves_probabilities": {f"saves>={k}": 0.5 for k in range(24, 35)}, "sample": {"games": 80, "starts": 70, "shots_faced_weighted": 1500},
                           "model_version": "goalie-team-v1",
                           "win_probability": {"team_win_probability": 0.55, "if_this_goalie_starts": 0.56, "goalie_effect_goals_per_game": 0.05, "status": "SCENARIO_NOT_VALIDATED", "note": "n"}},
            "confirmation": {"status": "UNCONFIRMED", "source": None, "checked_at_utc": None, "note": "No confirmation has been recorded."}}


def account(cash=500.0):
    return {"track": "REAL_MARKET_PAPER", "starting_bankroll": 500.0, "available_cash": cash, "open_stakes": 500.0 - cash, "open_tickets": 0, "equity": 500.0, "settled_pnl": 0.0, "tickets": 0}


def origin_block(**kw):
    base = {"tickets": 0, "settled": 0, "wins": 0, "losses": 0, "voids": 0, "pending": 0, "unresolved": 0, "open_stake": 0, "settled_stake": 0, "settled_pnl": 0, "roi": None, "hit_rate": None}
    base.update(kw)
    return base


LOG_CODE = "otter-maple-puck-4821"
LOG_KEY = "ABCDEFGHJKLMNPQRSTUV"          # normalised write key of the fixture log


def personal_logs_doc() -> dict:
    """One empty personal log (display name Casey) published under the hash of LOG_CODE."""
    from operational import log_signing, personal_logs as pl
    h = pl.code_hash(LOG_CODE)
    empty = pl.summarize([])
    return {"schema": 1, "generated_at_utc": GEN, "rules": {"stake_min": 1.0, "stake_max": 1000.0, "stake_default": 10.0, "code_min_length": 8},
            "logs": {h: {"display_name": "Casey", "created_at_utc": GEN, "write_pub": log_signing.public_key_hex(LOG_KEY, h), "summary": empty, "bets": [], "orders": []}}}


def snapshot(with_options=True) -> dict:
    games = [
        {"game_id": "2026020900", "season": "20262027", "season_label": "2026-27", "type": "REGULAR", "date_et": "2026-10-08", "date_in_db": "2026-10-08", "start_utc": "2026-10-08T23:00:00Z",
         "start_et": "7:00 PM ET", "state": "SCHEDULED", "home": "AAA", "away": "BBB", "home_score": None, "away_score": None, "period_type": None, "result_observed_at_utc": None,
         "tickets": [], "win_probability": {"home": 0.55, "away": 0.45, "model": "goalie-team-v1 strength model"},
         "moneyline": {"home": {"american": -120.0, "quote_captured_at_utc": GEN, "age_min": 5.0, "status": "ACTIVE"}, "away": {"american": 100.0, "quote_captured_at_utc": GEN, "age_min": 5.0, "status": "ACTIVE"},
                       "provider": "DraftKings"}, "goalies": {"home": [{"player_id": "G1", "name": "Test Goalie One", "start_probability": 0.7, "status": "UNCONFIRMED"}],
                                                              "away": [{"player_id": "G2", "name": "Test Goalie Two", "start_probability": 0.6, "status": "UNCONFIRMED"}]}},
        {"game_id": "2026020899", "season": "20262027", "season_label": "2026-27", "type": "REGULAR", "date_et": "2026-10-07", "date_in_db": "2026-10-07", "start_utc": "2026-10-07T23:00:00Z",
         "start_et": "7:00 PM ET", "state": "FINAL", "home": "BBB", "away": "AAA", "home_score": 3, "away_score": 2, "period_type": "OT", "result_observed_at_utc": "2026-10-08T02:00:00Z",
         "tickets": [], "goalies": {"home": {"player_id": "G2", "name": "Test Goalie Two", "shots_against": 30, "saves": 28, "goals_against": 2, "toi": 65.0},
                                    "away": {"player_id": "G1", "name": "Test Goalie One", "shots_against": 25, "saves": 22, "goals_against": 3, "toi": 63.0}}},
        {"game_id": "2025020001", "season": "20252026", "season_label": "2025-26", "type": "REGULAR", "date_et": "2025-11-25", "date_in_db": "2025-11-25", "start_utc": "2025-11-25T23:00:00Z",
         "start_et": "6:00 PM ET", "state": "FINAL", "home": "AAA", "away": "BBB", "home_score": 1, "away_score": 0, "period_type": "REG", "result_observed_at_utc": "2025-11-26T01:00:00Z", "tickets": []}]
    rec = {"gp": 1, "w": 1, "l": 0, "otl": 0, "gf": 3, "ga": 2, "last5": [{"game_id": "2026020899", "date_et": "2026-10-07", "opp": "BBB", "home": False, "gf": 2, "ga": 3, "result": "OTL", "period_type": "OT"}]}
    REPORTED = {"line": "F2", "pp": "PP1", "pk": None, "injury_status": None, "game_time_decision": False, "reported_by": "Test Reporter",
            "source_url": "https://example.test/report", "updated_at_utc": "2026-10-08T10:00:00Z", "fetched_at_utc": "2026-10-08T11:00:00Z",
            "source": "Daily Faceoff line combinations"}
    p = {"P1": player("P1", "Test Skater One", "AAA", reported=REPORTED), "P2": player("P2", "Test Skater Two", "BBB", pos="D", opp="AAA", line=1, pp=None),
         "P3": player("P3", "Test Skater Three", "AAA", with_projection=False)}
    g = {"G1": goalie("G1", "Test Goalie One", "AAA", opp="BBB"), "G2": goalie("G2", "Test Goalie Two", "BBB", opp="AAA")}
    sides = lambda team, sk, gl: {"team": team, "record": rec, "strength_rating": 0.1, "skater_ids": sk, "goalie_ids": gl}  # noqa: E731
    details = {"2026020900": {**games[0], "sides": {"home": sides("AAA", ["P1", "P3"], ["G1"]), "away": sides("BBB", ["P2"], ["G2"])}}}
    teams = {t: {"team": t, "record": {k: v for k, v in rec.items() if k != "last5"}, "last5": rec["last5"], "strength_rating": 0.1,
                 "upcoming": [{"game_id": "2026020900", "date_et": "2026-10-08", "start_et": "7:00 PM ET", "opp": "BBB" if t == "AAA" else "AAA", "home": t == "AAA", "state": "SCHEDULED"}],
                 "skaters": [{"player_id": "P1", "name": "Test Skater One", "position": "C", "usage_tier": 1, "pp_usage": 1, "reported": None, "toi_recent": 19.0, "season": p["P1"]["season"]}],
                 "goalies": [{"player_id": "G1", "name": "Test Goalie One", "season": g["G1"]["season"], "start": g["G1"]["start"], "confirmation": g["G1"]["confirmation"]}]} for t in ("AAA", "BBB")}
    tickets = {"account": account(), "slots": {"total": 5, "used": 0, "empty": 5}, "tickets": [], "earlier_open_tickets": [], "recent_settled": [],
               "empty_slot_reason": "Nothing qualifies yet.", "notice": None, "label": "US-feed paper experiment.", "generated_at_utc": GEN, "date_et": "2026-10-08",
               "policy": {"min_combined_decimal": 2.0, "min_estimated_ev": 0.05, "leg_probability_margin": 0.03, "max_tickets_per_day": 5, "max_tickets_per_leg": 2, "max_tickets_per_game": 3, "stake": 10.0},
               "exposure": {"tickets_counted": 0, "recorded_stake_at_risk": 0, "players": [], "games": [], "note": ""},
               "origins": {"AUTOMATIC": origin_block(), "ALL": origin_block()}, "singles": [], "diagnostics": {}, "coverage": {"rows": []}}
    if with_options:
        doc = po.build_options(board(4, price=-105, p=0.62), "2026-10-08")
        doc["generated_at_utc"] = GEN
        tickets["options"] = doc
        p["P1"]["option_id"] = doc["options"][0]["option_id"]
    mh = {"generated_at_utc": GEN, "models": [{"id": "skater-projection", "name": "Skater matchup projection", "version": "player-rate-toi-v2", "role": "LIVE", "purpose": "x",
                                               "data": {"source": "MoneyPuck", "through": "2026-10-06", "age_days": 2, "players_projected": 2},
                                               "validation": {"split": {"train": [2022, 2023]}, "rows": {"final": 10}, "summary": {"markets_scored": 14, "beating_baselines": 13, "not_beating": ["hits>=1"]},
                                                              "report": "docs/validation/skater_projection_validation.json", "method": "m"},
                                               "limits": ["a limit"], "markets": {"SHOTS": "PRICING_ACTIVE", "SAVES": "BLOCKED_NO_STARTER_CONFIRMATION"}}],
          "pipelines": [{"name": "Skater logs", "through": "2026-10-06", "source": "MoneyPuck"}], "market_coverage": {"rows": []}, "ticket_sources": {}}
    perf = {"account": account(), "summary": {"roi": None, "bankroll_history": []}, "answer": "x", "origins": {"AUTOMATIC": origin_block(), "MANUALLY_ADDED": origin_block(), "ALL": origin_block()},
            "breakdowns": {"ALL": {}, "AUTOMATIC": {}, "MANUALLY_ADDED": {}}, "bets": []}
    return {"schema_version": 2, "metadata": {"schema_version": 2, "generated_at": GEN, "generated_by": "test", "source_master_commit": "x", "engine_mode": "t", "data_as_of": GEN,
                                              "freshness": {"odds": GEN}},
            "tickets": tickets, "product_meta": {"schema_version": 1, "generated_at_utc": GEN, "et_today": "2026-10-08", "default_date": "2026-10-08",
                                                 "data_through": {"skaters": "2026-10-06", "goalies": "2026-10-06", "schedule_results": "2026-10-08T02:00:00Z"}, "current_season": "20262027"},
            "product_games": {"dates": sorted({x["date_et"] for x in games}), "games": games, "default_date": "2026-10-08", "et_today": "2026-10-08", "records": {}},
            "product_game_details": details, "product_players": p, "product_goalies": g, "product_teams": teams, "product_model_health": mh, "manual_orders": {"orders": [], "ontario_verifications": []}, "personal_logs": personal_logs_doc(),
            "performance": perf}


def product_state() -> dict:
    """The document operational/product_data.py writes (what the snapshot builder reads), built from the same hand-made sections."""
    snap = snapshot()
    meta = snap["product_meta"]
    return {**meta, "games": snap["product_games"], "game_details": snap["product_game_details"], "players": snap["product_players"],
            "goalies": snap["product_goalies"], "teams": snap["product_teams"], "model_health": snap["product_model_health"], "options": snap["tickets"].get("options")}
