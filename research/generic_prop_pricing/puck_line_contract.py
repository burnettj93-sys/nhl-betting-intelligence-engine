"""
Puck line (spreads) price shape: what a DraftKings `spreads` event must look like before anything may be built on it.

STATUS: UNCERTIFIED. No real DraftKings `spreads` payload exists in the archive (the market has never been requested), so
`("draftkings", "PUCK_LINE")` is deliberately NOT in `provider_adapter.VERIFIED_CONTRACTS`. This module only validates the shape the provider's
documentation describes -- two outcomes named for the home and away team, each with an American price and a signed point -- so that the moment one
real payload is archived (deploy/capture_puck_line_contract.py, one credit, run by the owner) it can be checked and, if it matches, certified by a
person adding the fixture and the contract entry. A shape check passing on a synthetic structure is NOT certification.
"""
from __future__ import annotations

STANDARD_POINTS = (-1.5, 1.5)


def validate_spreads_event(event: dict) -> dict:
    """{"status": "SHAPE_OK", "rows": [...]} or {"status": "MALFORMED", "reason": ...}. Never guesses."""
    home, away = event.get("home_team"), event.get("away_team")
    rows = []
    for bm in event.get("bookmakers") or []:
        if bm.get("key") != "draftkings":
            continue
        for m in bm.get("markets") or []:
            if m.get("key") != "spreads":
                continue
            outs = m.get("outcomes") or []
            if len(outs) != 2:
                return {"status": "MALFORMED", "reason": f"expected 2 outcomes, found {len(outs)}"}
            by = {o.get("name"): o for o in outs}
            if set(by) != {home, away}:
                return {"status": "MALFORMED", "reason": "outcome names are not the home and away teams"}
            for o in outs:
                if not isinstance(o.get("price"), (int, float)) or not isinstance(o.get("point"), (int, float)):
                    return {"status": "MALFORMED", "reason": "an outcome lacks a numeric price or point"}
            if by[home]["point"] != -by[away]["point"]:
                return {"status": "MALFORMED", "reason": "the two points are not opposite"}
            rows.append({"home": home, "away": away, "home_point": by[home]["point"], "home_price": by[home]["price"],
                         "away_point": by[away]["point"], "away_price": by[away]["price"], "last_update": m.get("last_update"),
                         "standard_line": by[home]["point"] in STANDARD_POINTS})
    if not rows:
        return {"status": "MALFORMED", "reason": "no draftkings spreads market in the event"}
    return {"status": "SHAPE_OK", "rows": rows}


def legs_from_event(event: dict, *, game_id: str, home_abbrev: str, away_abbrev: str, probability_for=None, now=None, captured_at_utc: str | None = None,
                    start_utc: str | None = None) -> list:
    """ParlayLegs for the standard +/-1.5 spread of one event. Built so that NOTHING can select them today:
      * `provider_contract_verified` is the live answer from VERIFIED_CONTRACTS (False until a real payload certifies the shape);
      * `model_threshold_eligible` is False unless a probability supplier is given AND `PUCK_LINE_MODEL_VALIDATED` is True (it is False: the prediction is unvalidated);
      * `PUCK_LINE` is not in the engine's ALLOWED_MARKET_FAMILIES.
    Prices come only from the quoted market; the probability, if any, comes only from the supplier (no supplier is wired anywhere)."""
    from research.generic_prop_pricing import provider_adapter
    from research.real_market_parlay.engine import ParlayLeg
    checked = validate_spreads_event(event)
    if checked["status"] != "SHAPE_OK":
        return []
    verified = provider_adapter.is_contract_verified("draftkings", "PUCK_LINE")
    legs = []
    for row in checked["rows"]:
        if not row["standard_line"]:
            continue
        for team_abbrev, price, point in ((home_abbrev, row["home_price"], row["home_point"]), (away_abbrev, row["away_price"], row["away_point"])):
            prob = probability_for(team_abbrev, point) if probability_for else None
            legs.append(ParlayLeg(game_id=str(game_id), event_id=event.get("id"), market_family="PUCK_LINE", participant_id=team_abbrev, participant_name=team_abbrev,
                                  side="TEAM", threshold=point, american_price=float(price), conservative_probability=prob if prob is not None else 0.0, sportsbook="draftkings",
                                  captured_at_utc=captured_at_utc, quote_updated_utc=row["last_update"], provider_contract_verified=verified,
                                  model_threshold_eligible=bool(PUCK_LINE_MODEL_VALIDATED and prob is not None), identity_resolved=True, price_fresh=False,
                                  event_not_started=False, team=team_abbrev, game_start_utc=start_utc, model_version="puck-line-unvalidated"))
    return legs


PUCK_LINE_MODEL_VALIDATED = False   # flips only after puck-line-direct-v1 is scored on the untouched 2026-27 games (research/product_models/puck_line_alternative.py)
