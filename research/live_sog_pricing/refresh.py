"""
The explicit "REFRESH LIVE ODDS" action (Part: "support explicit REFRESH
LIVE ODDS... do not poll continuously... dashboard must read cached
normalized market state"). Run manually:

    python3 -m research.live_sog_pricing.refresh

Never called automatically by the dashboard or by the test suite -- both
read the cached board snapshot this writes
(research/live_sog_board_cache.json), never the network.

Credit-conscious by construction: only fetches per-event odds for events
starting within NEAR_TERM_WINDOW_DAYS (default 3) -- DraftKings does not
post player props more than a few days out, so querying farther-out
events would spend credits for a result already known to be empty (see
PLAYER_SOG_LIVE_PRICING_REPORT.md Section A/G for the real Phase A
finding: the soonest real NHL event this slice observed was 32 days out
with zero bookmakers posted).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research import elo_comparison as ec
from research.player_sog import features as pf
from research.player_sog.live_projection import project_player_sog
from research.run_player_sog_model import build_team_schedules, NHL_CORPUS_PATH
from research.live_sog_pricing import client, archive, event_mapping, player_mapping, market_parser, pricing
from research.live_sog_pricing import observation_ledger as ledger
from pricing import odds_math

NEAR_TERM_WINDOW_DAYS = 3
BOARD_CACHE_PATH = REPO_ROOT / "research" / "live_sog_board_cache.json"


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def refresh(near_term_days: int = NEAR_TERM_WINDOW_DAYS, max_events: int = 12) -> dict:
    """Returns a summary dict (events considered, events queried, quotes
    parsed, observations priced/appended, credit usage observed) --
    never raises past the caller; any real API failure is captured in
    the summary as `api_error` and the board cache is left untouched
    (Part: "fail clearly... do not crash dashboard... do not fall back
    to fake cached prices without clearly labeling cache age")."""
    summary = {"refreshed_at_utc": _now_utc().isoformat(), "events_seen": 0,
               "events_in_near_term_window": 0, "events_queried_for_odds": 0,
               "quotes_parsed": 0, "observations_priced": 0, "observations_appended": 0,
               "api_error": None, "requests_used_last_seen": None, "requests_remaining_last_seen": None}

    r_events = client.get_nhl_events()
    if not r_events.ok:
        summary["api_error"] = r_events.error
        return summary
    archive.archive_result(r_events, event_id=None, market_filter=None, bookmaker_filter=None)
    summary["requests_used_last_seen"] = r_events.requests_used
    summary["requests_remaining_last_seen"] = r_events.requests_remaining

    events = r_events.data
    summary["events_seen"] = len(events)
    now = _now_utc()
    near_term = []
    for e in events:
        try:
            commence = dt.datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if 0 <= (commence - now).total_seconds() <= near_term_days * 86400:
            near_term.append(e)
    summary["events_in_near_term_window"] = len(near_term)
    near_term = sorted(near_term, key=lambda e: e["commence_time"])[:max_events]

    if not near_term:
        _write_board_cache([], summary)
        return summary

    real_games = ec.load_corpus(str(NHL_CORPUS_PATH))
    sog_rows = pf.load_sog_corpus()
    player_index = player_mapping.build_player_index(sog_rows)
    sog_index = pf.PlayerHistoryIndex(sog_rows)
    team_schedules = build_team_schedules(real_games)
    totals = pf.build_team_game_totals(sog_rows)
    opponent_allowed = pf.build_opponent_allowed_history(totals)
    import statistics
    league_avg_sog_allowed = statistics.fmean(v["sog_for"] for v in totals.values())

    results = json.load(open(REPO_ROOT / "research" / "player_sog_results.json"))
    weights = [results["stage_weights"][results["headline_stage"]][n] for n in results["config"]["feature_names"]]
    alpha = results["negbinom_alpha_fitted"] if results["negbinom_alpha_fitted"] > 0.01 else None

    board_rows = []
    for event in near_term:
        r_odds = client.get_event_odds(event["id"])
        summary["events_queried_for_odds"] += 1
        summary["requests_used_last_seen"] = r_odds.requests_used
        summary["requests_remaining_last_seen"] = r_odds.requests_remaining
        if not r_odds.ok:
            continue
        archive.archive_result(r_odds, event_id=event["id"],
                                market_filter="player_shots_on_goal,player_shots_on_goal_alternate",
                                bookmaker_filter="draftkings")
        if not r_odds.data.get("bookmakers"):
            continue

        quotes = market_parser.parse_event_odds_response(r_odds.data)
        summary["quotes_parsed"] += len(quotes)
        if not quotes:
            continue

        mapping = event_mapping.map_event_to_game(event, real_games)
        home_abbrev = event_mapping.normalize_team_name(event["home_team"])
        away_abbrev = event_mapping.normalize_team_name(event["away_team"])

        pairs = market_parser.group_standard_two_sided(quotes)
        for (ev_id, book, player_raw, point, market_ts), pair in pairs.items():
            row = _price_pair(pair, event, mapping, home_abbrev, away_abbrev, player_index,
                               sog_rows, sog_index, team_schedules, opponent_allowed, league_avg_sog_allowed,
                               weights, alpha, results)
            if row is not None:
                board_rows.append(row)
                summary["observations_priced"] += 1
                if ledger.append_observation(_to_ledger_row(row), path=ledger.LEDGER_PATH):
                    summary["observations_appended"] += 1

    _write_board_cache(board_rows, summary)
    return summary


def _price_pair(pair, event, mapping, home_abbrev, away_abbrev, player_index, sog_rows, sog_index,
                 team_schedules, opponent_allowed, league_avg_sog_allowed, weights, alpha, results) -> dict | None:
    over_q, under_q = pair.get("over"), pair.get("under")
    any_q = over_q or under_q
    if any_q is None:
        return None

    if mapping["status"] != "MATCHED":
        return {"status": mapping["status"], "reason": mapping["reason"], "player_name_raw": any_q["player_name_raw"],
                "provider_event_id": event["id"], "market": any_q["market_key"], "layer": "event_mapping"}

    pmap = player_mapping.map_player(any_q["player_name_raw"], home_abbrev, away_abbrev, player_index)
    if pmap["status"] != "MATCHED":
        return {"status": pmap["status"], "reason": pmap["reason"], "player_name_raw": any_q["player_name_raw"],
                "provider_event_id": event["id"], "market": any_q["market_key"], "layer": "player_mapping"}

    player_id = pmap["player_id"]
    prediction_date = event["commence_time"][:10]
    # figure out which side (home/away) this player's team actually is,
    # using the mapping index's own most-recent-team record
    candidates = player_index.get(player_mapping.normalize_name(any_q["player_name_raw"]), [])
    recent_team = next((c["most_recent_team"] for c in candidates if c["player_id"] == player_id), home_abbrev)
    team = recent_team if recent_team in (home_abbrev, away_abbrev) else home_abbrev
    opponent = away_abbrev if team == home_abbrev else home_abbrev
    # NHL season labeling: games from July onward belong to the season
    # STARTING that year (e.g. Sep 2026 -> 20262027); games Jan-June
    # belong to the season that started the PRIOR year.
    year, month = int(prediction_date[:4]), int(prediction_date[5:7])
    season_start_year = year if month >= 7 else year - 1
    season = season_start_year * 10000 + (season_start_year + 1)

    view = project_player_sog(sog_rows, sog_index, team_schedules,
                               opponent_allowed, league_avg_sog_allowed, weights, alpha,
                               player_id, team, opponent, prediction_date, season)
    if view["status"] != "PROJECTED_ACTIVE":
        return {"status": view["status"], "player_id": player_id, "provider_event_id": event["id"],
                "player_name_raw": any_q["player_name_raw"], "layer": "model_projection"}

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    quote_ts = (over_q or under_q).get("bookmaker_last_update_utc")
    quote_age_minutes = (odds_math.hours_between(quote_ts, now_iso) * 60.0 if quote_ts else 0.0)
    hours_to_puck_drop = max(0.0, odds_math.hours_between(now_iso, event["commence_time"]))

    priced_side = "over" if over_q else "under"
    q = over_q or under_q
    opposing_price = (under_q or {}).get("price_american") if priced_side == "over" else (over_q or {}).get("price_american")
    report = pricing.price_observation(
        side=q["side"], point=q.get("point"), milestone_threshold=q.get("milestone_threshold"),
        price_american=q["price_american"], opposing_price_american=opposing_price,
        probs=view["probs"], conservative_probs=view["conservative_probs"],
        confidence=view["confidence"], lineup_status="PROJECTED/UNCONFIRMED",
        quote_age_minutes=quote_age_minutes, hours_to_puck_drop=hours_to_puck_drop)

    report.update({"player_id": player_id, "player_name_raw": any_q["player_name_raw"],
                   "nhl_game_id": mapping["game_id"], "provider_event_id": event["id"],
                   "market": q["market_key"], "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return report


def _to_ledger_row(row: dict) -> dict:
    obs_id = ledger.make_observation_id(row["observed_at_utc"], row["provider_event_id"],
                                         row["player_id"], row["market"], row["side"], row["point"])
    import hashlib
    return {
        "observation_id": obs_id, "observed_at_utc": row["observed_at_utc"],
        "nhl_game_id": row["nhl_game_id"], "provider_event_id": row["provider_event_id"],
        "player_id": row["player_id"], "player": row["player_name_raw"], "market": row["market"],
        "point": row.get("point"), "side": row["side"], "draftkings_price": row["draftkings_price"],
        "market_raw_probability": row["market_raw_probability"],
        "market_no_vig_probability": row["market_no_vig_probability"],
        "model_probability": row["model_probability"], "conservative_probability": row["conservative_probability"],
        "fair_price": row["model_fair_price"], "conservative_fair_price": row["conservative_fair_price"],
        "raw_edge": row["raw_edge"], "conservative_edge": row["conservative_edge"],
        "raw_ev": row["raw_ev"], "conservative_ev": row["conservative_ev"],
        "confidence": row["confidence"], "lineup_status": row["lineup_status"], "decision": row["action"],
        "source_raw_payload_sha256": hashlib.sha256(row["provider_event_id"].encode()).hexdigest(),
    }


def _write_board_cache(rows: list[dict], summary: dict, path: Path = BOARD_CACHE_PATH) -> None:
    with open(path, "w") as f:
        json.dump({"summary": summary, "board": rows}, f, indent=2, sort_keys=True, default=str)


if __name__ == "__main__":
    result = refresh()
    print(json.dumps(result, indent=2))
