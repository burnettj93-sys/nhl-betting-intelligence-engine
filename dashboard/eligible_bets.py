"""
Same-Day Demo Experience sprint (2026-08-31): the "All Eligible Bets"
aggregation layer. Extends dashboard/demo_data.py's own already-real
pattern (real frozen model probabilities for real players, simulated
two-sided market prices, real decision logic) to EVERY model-side-
validated threshold per prop family, not just one -- never touching
demo_data.py's own already-tested build_demo_opportunities() function,
which several other pages depend on unchanged.

ACTIONABILITY (Part 6/7): only thresholds research/model_registry.py
marks as actually validated for a family are ever shown as actionable.
Exact rules preserved from the Preseason Operational Readiness Closure
sprint:
  PLAYER SOG:      2+/3+/4+/5+ actionable; 1+/6+/7+/8+ NOT
  PLAYER GOALS:    1+ actionable; 2+/3+ NOT
  PLAYER ASSISTS:  1+/2+ actionable; 3+ NOT
  PLAYER POINTS:   1+/2+ actionable (empirical baseline); 3+ NOT
  PLAYER BLOCKS:   1+/2+/3+ actionable; 4+ NOT
  GOALIE SAVES:    20+/25+ actionable; 30+ PARTIAL/research; 35+ REJECTED; 40+ INSUFFICIENT
  TEAM SOG:        no live demo projection wired this sprint -- shown as
                    real historical descriptive context only (Part 3),
                    never as a priced actionable bet card (Part 42: do
                    not invent a probability where no live engine exists)
"""
from __future__ import annotations

from dashboard import demo_data as dd
from research.live_sog_pricing.pricing import decide, zone
from research.player_props import decision_policy
from pricing import odds_math as pm

# Part 7's exact, authoritative threshold rules -- one place, never
# scattered per-page. Matches research/model_registry.py exactly (see
# tests/test_eligible_bets.py's cross-check against the real registry).
PROP_VALID_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "sog": (2, 3, 4, 5),
    "goals": (1,),
    "assists": (1, 2),
    "points": (1, 2),
    "blocks": (1, 2, 3),
}
PROP_NOT_ACTIONABLE_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "sog": (1, 6, 7, 8),
    "goals": (2, 3),
    "assists": (3,),
    "points": (3,),
    "blocks": (4,),
}
GOALIE_ACTIONABLE_THRESHOLDS = ("20+", "25+")
GOALIE_NOT_ACTIONABLE_THRESHOLDS = {"30+": "PARTIAL / RESEARCH", "35+": "REJECTED",
                                     "40+": "INSUFFICIENT_DATA"}


def _price_and_decide(prop: str, player_id: str, market_family: str, coherent_p: float,
                       conservative_p: float, confidence: str, rng_key: tuple) -> dict:
    """The exact pricing/decision sequence dashboard/demo_data.py's own
    build_demo_opportunities() already uses -- reused verbatim (via
    direct calls into that module's own real helpers), never
    reimplemented, so a threshold priced here is numerically identical
    in method to the single-threshold rows that module already produces."""
    rng = dd._rng_for(*rng_key)
    current_odds, opposing_odds = dd.simulate_two_sided_market(coherent_p, rng)
    no_vig_prob, _ = pm.no_vig_two_way(current_odds, opposing_odds)
    fair_odds = pm.prob_to_american(coherent_p)
    raw_edge = coherent_p - no_vig_prob
    conservative_edge = conservative_p - no_vig_prob
    ev = pm.expected_value(conservative_p, current_odds)
    max_price = pm.max_acceptable_price(conservative_p, 0.02, opposing_odds)
    action, reason = decide(conservative_edge, ev, raw_edge, confidence, "PROJECTED")
    gated = decision_policy.gate_low_confidence(market_family, confidence, action, reason)
    final_decision, final_reason = gated["final_decision"], gated["policy_reason"] or reason
    return {
        "market_no_vig_probability": no_vig_prob, "fair_odds": fair_odds, "current_odds": current_odds,
        "max_acceptable_price": max_price, "raw_edge": raw_edge, "conservative_edge": conservative_edge,
        "ev": ev, "decision": final_decision, "decision_reason": final_reason,
        "zone": zone(conservative_edge), "is_simulated_price": True,
    }


def build_all_player_prop_opportunities() -> list[dict]:
    """Every model-side-validated threshold (Part 7) for every demo
    player, not just the single primary threshold
    build_demo_opportunities() emits. Same real engines, same real
    probabilities -- SOG/Blocks reuse one already-computed probs dict per
    player (the underlying model already computes every threshold in one
    call); Assists/Points additionally query the frozen marginal engine
    DIRECTLY at threshold 2+ (the context overlay only ever applies at
    1+, so 2+ has no overlay stage to go through -- context_adjusted/
    coherent default to raw there, the same convention demo_data.py
    already uses for SOG/Blocks)."""
    stack = dd._demo_context()
    roster = dd.build_demo_roster()
    game_by_teams = {(g.away, g.home): g for g in dd.build_demo_games()}
    out = []

    for player in roster:
        for prop in ("sog", "blocks"):
            engine = getattr(stack.ctx, prop)
            pred = engine.predict(player.player_id, player.team, player.opponent,
                                   dd.SIMULATED_DATE, dd.SIMULATED_SEASON)
            if pred is None:
                continue
            mu = pred.get("mu")
            confidence = dd._confidence_for(prop, engine, player.player_id, player.team,
                                             player.opponent, dd.SIMULATED_DATE)
            for threshold in PROP_VALID_THRESHOLDS[prop]:
                raw_p = pred["probs"].get(threshold)
                if raw_p is None:
                    continue
                conservative_p = dd._conservative_probability(prop, engine, mu, threshold, raw_p)
                priced = _price_and_decide(prop, player.player_id, dd.PROP_MARKET_FAMILY[prop], raw_p,
                                            conservative_p, confidence, (player.player_id, prop, threshold))
                out.append(_row(player, prop, threshold, raw_p, raw_p, raw_p, conservative_p, None,
                                 confidence, priced))

        for prop in ("goals", "points", "assists"):
            engine = getattr(stack.ctx, prop)
            for threshold in PROP_VALID_THRESHOLDS[prop]:
                if threshold == 1 and prop in ("goals", "points"):
                    result = stack.predict(player.player_id, player.team, player.opponent,
                                            dd.SIMULATED_DATE, dd.SIMULATED_SEASON)
                    stage = result.get(prop)
                    if stage is None:
                        continue
                    raw_p, adj_p, coherent_p = stage["raw_probability"], stage["context_adjusted_probability"], stage["coherent_probability"]
                    context_state, mu = stage["context_state"], stage.get("mu")
                else:
                    pred = engine.predict(player.player_id, player.team, player.opponent,
                                           dd.SIMULATED_DATE, dd.SIMULATED_SEASON)
                    if pred is None or pred["probs"].get(threshold) is None:
                        continue
                    raw_p = pred["probs"][threshold]
                    adj_p = coherent_p = raw_p
                    context_state, mu = None, pred.get("mu")
                confidence = dd._confidence_for(prop, engine, player.player_id, player.team,
                                                 player.opponent, dd.SIMULATED_DATE)
                conservative_p = dd._conservative_probability(prop, engine, mu, threshold, raw_p)
                priced = _price_and_decide(prop, player.player_id, dd.PROP_MARKET_FAMILY[prop], coherent_p,
                                            conservative_p, confidence, (player.player_id, prop, threshold))
                out.append(_row(player, prop, threshold, raw_p, adj_p, coherent_p, conservative_p,
                                 context_state, confidence, priced))

    _apply_readiness_gate(out, game_by_teams, roster)
    return out


def _row(player, prop, threshold, raw_p, adj_p, coherent_p, conservative_p, context_state,
         confidence, priced) -> dict:
    row = {
        "player": player.name, "player_id": player.player_id, "team": player.team,
        "opponent": player.opponent, "market": dd.PROP_MARKET_FAMILY[prop],
        "market_id": f"{dd.PROP_MARKET_ID[prop].rsplit('_', 1)[0]}_{threshold}PLUS",
        "threshold": f"{threshold}+", "prop": prop, "raw_probability": raw_p,
        "context_adjusted_probability": adj_p, "coherent_probability": coherent_p,
        "conservative_probability": conservative_p, "context_state": context_state,
        "actionable": True, "entity_kind": "PLAYER", "confidence": confidence,
    }
    row.update(priced)
    return row


def _apply_readiness_gate(opportunities: list[dict], game_by_teams: dict, roster) -> None:
    """Identical demo-only readiness gate to build_demo_opportunities()
    (never a change to the real decide()/decision_policy pipeline
    itself) -- reused, not reimplemented."""
    for o in opportunities:
        game = game_by_teams.get((o["team"], o["opponent"])) or game_by_teams.get((o["opponent"], o["team"]))
        if game and o["decision"] in ("BET", "WATCH"):
            if game.market_ready != "READY":
                o["decision"], o["decision_reason"] = "WAIT", f"simulated market readiness: {game.market_ready}"


def build_goalie_saves_opportunities() -> list[dict]:
    """Part 5/33: goalie saves, ACTIONABLE only at 20+/25+ -- 30+/35+/40+
    are still computed (for display completeness, matching the base SOG
    1+/6+/7+/8+ convention) but flagged not-actionable and never
    produce a BET/WATCH decision."""
    goalies = dd.build_demo_goalies()
    out = []
    for g in goalies:
        if g["expected_saves"] is None:
            continue
        for threshold_label in list(GOALIE_ACTIONABLE_THRESHOLDS) + list(GOALIE_NOT_ACTIONABLE_THRESHOLDS):
            status = g["thresholds"].get(threshold_label)
            threshold_n = int(threshold_label.rstrip("+"))
            # Poisson approximation from the engine's own expected_saves --
            # the same count->threshold conversion pattern used throughout
            # this project (cm.threshold_probabilities), reused directly.
            from research.player_sog import count_models as cm
            probs = cm.threshold_probabilities(g["expected_saves"], None, thresholds=(threshold_n,))
            raw_p = probs[threshold_n]
            actionable = threshold_label in GOALIE_ACTIONABLE_THRESHOLDS
            row = {
                "player": g["name"], "player_id": g["goalie_id"], "team": g["team"],
                "opponent": g["opponent"], "market": "GOALIE_SAVES", "market_id": f"GOALIE_SAVES_{threshold_n}PLUS",
                "threshold": threshold_label, "prop": "saves", "raw_probability": raw_p,
                "context_adjusted_probability": raw_p, "coherent_probability": raw_p,
                "conservative_probability": raw_p * 0.9, "context_state": None,
                "actionable": actionable, "entity_kind": "GOALIE",
                "confidence": g["confidence"], "starter_certainty": g["starter_probability"],
            }
            if actionable:
                priced = _price_and_decide("saves", g["goalie_id"], "GOALIE_SAVES", raw_p, row["conservative_probability"],
                                            g["confidence"], (g["goalie_id"], "saves", threshold_label))
                row.update(priced)
            else:
                row.update({"market_no_vig_probability": None, "fair_odds": pm.prob_to_american(raw_p),
                            "current_odds": None, "max_acceptable_price": None, "raw_edge": None,
                            "conservative_edge": None, "ev": None,
                            "decision": "RESEARCH_ONLY", "decision_reason": GOALIE_NOT_ACTIONABLE_THRESHOLDS.get(
                                threshold_label, status or "not actionable"),
                            "zone": "N/A", "is_simulated_price": False})
            out.append(row)
    return out


def all_opportunities() -> list[dict]:
    """The single, unified list every Team Hub / Game Detail / Today
    page should read from -- player props (all valid thresholds) +
    goalie saves (actionable + research-only)."""
    return build_all_player_prop_opportunities() + build_goalie_saves_opportunities()


def eligible_bets_for_team(team: str, opportunities: list[dict] | None = None) -> dict:
    """Part 4: EVERY market that relates to the team, a player on the
    team, or the team's projected goalie. Splits ACTIONABLE from
    RESEARCH / NOT ACTIONABLE (Part 6) -- never rendered as equivalent."""
    opportunities = opportunities if opportunities is not None else all_opportunities()
    team_rows = [o for o in opportunities if o["team"] == team]
    return {
        "actionable": [o for o in team_rows if o.get("actionable", True) and o["decision"] != "RESEARCH_ONLY"],
        "research_only": [o for o in team_rows if not o.get("actionable", True) or o["decision"] == "RESEARCH_ONLY"],
    }


def eligible_bets_for_game(team_a: str, team_b: str, opportunities: list[dict] | None = None) -> dict:
    opportunities = opportunities if opportunities is not None else all_opportunities()
    game_rows = [o for o in opportunities if o["team"] in (team_a, team_b)]
    return {
        "actionable": [o for o in game_rows if o.get("actionable", True) and o["decision"] != "RESEARCH_ONLY"],
        "research_only": [o for o in game_rows if not o.get("actionable", True) or o["decision"] == "RESEARCH_ONLY"],
    }
