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
