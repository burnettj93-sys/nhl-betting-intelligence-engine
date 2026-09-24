"""
Starting-Goalie Certainty + Prop Contract Watch block (2026-09-24), Part
7: a deterministic verification pass over one real, flagged CONTRACT_
CANDIDATE payload (operational/prop_contract_candidates.jsonl, written by
operational/real_prop_orchestrator.py::flag_prop_contract_candidate_if_observed()).

This module NEVER writes to research/generic_prop_pricing/provider_
adapter.py::VERIFIED_CONTRACTS itself -- Part 6/7's explicit rule is that
verification requires a human to inspect the real payload and make that
call. What this module DOES do, deterministically and repeatably:
  1. Runs every structural check Part 7 lists (event mapping, identity,
     bookmaker, threshold, side, price, timestamps, standard-vs-
     alternate semantics) against the real payload, using the SAME
     already-tested parsers this project's real orchestrator uses --
     never a second, parallel parsing path.
  2. Reports PASS/FAIL per check, never silently skipping one.
  3. If every check PASSES, writes a sanitized fixture file (the real
     payload, provenance-labeled) and prints the exact next steps a human
     takes to finish certification (add the (sportsbook, canonical_market_id)
     tuple to VERIFIED_CONTRACTS, write the parser regression test) --
     it does not perform those steps itself.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


def certify_sog_payload(payload: dict, schedule: list[dict] | None = None) -> dict:
    """Runs every real Part-7 check for a PLAYER_SOG candidate payload.
    `schedule` defaults to a real nhl.db query if not supplied."""
    from research.live_sog_pricing import event_mapping, market_parser, player_mapping
    from research.player_sog import features as pf

    checks: dict[str, dict] = {}

    if schedule is None:
        import db
        conn = db.get_conn()
        schedule = [dict(r) for r in conn.execute(
            "SELECT game_id, home_team, away_team, game_date FROM games").fetchall()]
        conn.close()

    mapping = event_mapping.map_event_to_game(payload, schedule)
    checks["event_mapping"] = {"passed": mapping["status"] == "MATCHED", "detail": mapping}

    checks["bookmaker_present"] = {
        "passed": any(bm.get("key") == "draftkings" for bm in payload.get("bookmakers", [])),
        "detail": [bm.get("key") for bm in payload.get("bookmakers", [])],
    }

    quotes = market_parser.parse_event_odds_response(
        payload, standard_market_keys=(market_parser.STANDARD_MARKET_KEY,))
    checks["standard_vs_alternate_semantics"] = {
        "passed": all(q["market_key"] in (market_parser.STANDARD_MARKET_KEY, market_parser.ALTERNATE_MARKET_KEY)
                      for q in quotes),
        "detail": sorted({q["market_key"] for q in quotes}),
    }
    checks["threshold_side_price_parse"] = {
        "passed": bool(quotes) and all(
            q.get("price_american") is not None and q.get("side") in ("OVER", "UNDER", "OVER_MILESTONE")
            for q in quotes),
        "detail": {"quotes_parsed": len(quotes)},
    }
    checks["timestamps_present"] = {
        "passed": bool(quotes) and all(q.get("bookmaker_last_update_utc") for q in quotes),
        "detail": {"sample": quotes[0].get("bookmaker_last_update_utc") if quotes else None},
    }

    player_index = player_mapping.build_player_index(pf.load_sog_corpus())
    home_abbrev = event_mapping.normalize_team_name(payload.get("home_team", ""))
    away_abbrev = event_mapping.normalize_team_name(payload.get("away_team", ""))
    identity_results = [player_mapping.map_player(q["player_name_raw"], home_abbrev, away_abbrev, player_index)
                         for q in quotes]
    checks["player_identity"] = {
        "passed": bool(identity_results) and all(r["status"] == "MATCHED" for r in identity_results),
        "detail": [{"name": q["player_name_raw"], "status": r["status"]}
                   for q, r in zip(quotes, identity_results)],
    }

    return _finalize(checks, market_key="PLAYER_SOG", canonical_market_id="PLAYER_SOG",
                      fixture_name="draftkings_player_shots_on_goal_CANDIDATE.json", payload=payload)


def certify_saves_payload(payload: dict, schedule: list[dict] | None = None) -> dict:
    """Runs every real Part-7 check for a GOALIE_SAVES candidate payload
    -- identical structural checks to SOG, substituting goalie identity
    (research/goalie_intelligence's real starter corpus) for player
    identity, since the market shape itself is the same documented
    Over/Under contract."""
    from research.live_sog_pricing import event_mapping, market_parser, player_mapping
    from operational.real_prop_orchestrator import _build_goalie_identity_index

    checks: dict[str, dict] = {}

    if schedule is None:
        import db
        conn = db.get_conn()
        schedule = [dict(r) for r in conn.execute(
            "SELECT game_id, home_team, away_team, game_date FROM games").fetchall()]
        conn.close()

    mapping = event_mapping.map_event_to_game(payload, schedule)
    checks["event_mapping"] = {"passed": mapping["status"] == "MATCHED", "detail": mapping}

    checks["bookmaker_present"] = {
        "passed": any(bm.get("key") == "draftkings" for bm in payload.get("bookmakers", [])),
        "detail": [bm.get("key") for bm in payload.get("bookmakers", [])],
    }

    quotes = market_parser.parse_event_odds_response(
        payload, standard_market_keys=(market_parser.SAVES_MARKET_KEY,))
    checks["standard_vs_alternate_semantics"] = {
        "passed": all(q["market_key"] == market_parser.SAVES_MARKET_KEY for q in quotes),
        "detail": sorted({q["market_key"] for q in quotes}),
    }
    checks["threshold_side_price_parse"] = {
        "passed": bool(quotes) and all(
            q.get("price_american") is not None and q.get("side") in ("OVER", "UNDER") for q in quotes),
        "detail": {"quotes_parsed": len(quotes)},
    }
    checks["timestamps_present"] = {
        "passed": bool(quotes) and all(q.get("bookmaker_last_update_utc") for q in quotes),
        "detail": {"sample": quotes[0].get("bookmaker_last_update_utc") if quotes else None},
    }

    goalie_index = _build_goalie_identity_index()
    home_abbrev = event_mapping.normalize_team_name(payload.get("home_team", ""))
    away_abbrev = event_mapping.normalize_team_name(payload.get("away_team", ""))
    identity_results = [player_mapping.map_player(q["player_name_raw"], home_abbrev, away_abbrev, goalie_index)
                         for q in quotes]
    checks["goalie_identity"] = {
        "passed": bool(identity_results) and all(r["status"] == "MATCHED" for r in identity_results),
        "detail": [{"name": q["player_name_raw"], "status": r["status"]}
                   for q, r in zip(quotes, identity_results)],
    }

    return _finalize(checks, market_key="GOALIE_SAVES", canonical_market_id="GOALIE_SAVES",
                      fixture_name="draftkings_player_total_saves_CANDIDATE.json", payload=payload)


def _finalize(checks: dict, *, market_key: str, canonical_market_id: str, fixture_name: str,
              payload: dict) -> dict:
    all_passed = all(c["passed"] for c in checks.values())
    result = {
        "market_key": market_key, "certified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "checks": checks, "all_checks_passed": all_passed,
        "verified_contracts_updated": False,  # NEVER done automatically -- Part 6/7
        "fixture_written": None, "next_steps": None,
    }
    if not all_passed:
        result["next_steps"] = ("One or more checks failed -- do NOT add to VERIFIED_CONTRACTS. "
                                 "Inspect the failing check(s) above against the real payload.")
        return result

    fixture_path = FIXTURES_DIR / fixture_name
    sanitized = {
        "_provenance": (f"REAL candidate payload for {market_key}, deterministically verified by "
                        f"operational/prop_contract_certification.py on {result['certified_at_utc']} -- "
                        f"every structural check passed. NOT yet added to VERIFIED_CONTRACTS; a human "
                        f"must still review this fixture and the check detail above before doing so."),
        **payload,
    }
    fixture_path.write_text(json.dumps(sanitized, indent=2, sort_keys=True))
    result["fixture_written"] = str(fixture_path)
    result["next_steps"] = (
        f"All structural checks passed. To finish certification: (1) a human reviews "
        f"{fixture_path} and this report's check detail; (2) if satisfied, manually add "
        f"(\"draftkings\", {canonical_market_id!r}) to research/generic_prop_pricing/"
        f"provider_adapter.py::VERIFIED_CONTRACTS; (3) write a parser regression test against "
        f"{fixture_path.name} (mirroring tests/test_generic_prop_pricing.py::TestMoneylineContractParity's "
        f"existing pattern for MONEYLINE). This function never performs steps 2-3 itself.")
    return result
