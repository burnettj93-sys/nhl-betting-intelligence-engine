"""
Central DEMO mode architecture (Preseason Interactive Product sprint,
Parts 1-24). One deterministic, mathematically-coherent demo dataset,
never scattered hard-coded UI examples.

KEY ARCHITECTURAL DECISION: demo player MODEL PROBABILITIES are NOT
fabricated. They are the real frozen models' own output for real NHL
player_ids (queried directly from the same corpora every research page
already uses), computed via the exact same ShadowContextStack /
ContextMarginalContext pipeline the rest of this project uses -- the
ONLY simulated inputs are the near-future schedule (2026-27 games don't
exist yet) and the sportsbook price (no live market exists yet). This
is the "REAL player identity + SIMULATED odds" pattern Part 4 explicitly
sanctions, taken as far as it can honestly go: even the probabilities
riding on that simulated matchup are real model output, not invented.

Deterministic: DEMO_SEED fixes every random draw (market-price
generation) so re-running produces an identical demo slate (Part 23).

STRICT SEPARATION (Part 21/22): nothing in this module ever imports or
calls operational.prospective_ledger. Demo data lives only in this
module's in-memory, cached structures -- never operational/
prospective_observations.db. See tests/test_demo_data.py for the
enforcement tests.
"""
from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

from pricing import odds_math as pm
from research.context_overlay.prediction_stack import ShadowContextStack
from research.live_sog_pricing.pricing import decide, zone
from research.player_props import decision_policy
from research.player_sog import count_models as cm
from research.player_sog import live_projection as plp

DEMO_SEED = 20260827
DEMO_MODE_LABEL = "DEMO MODE — REAL NHL ENTITIES / SIMULATED MARKETS & MODEL OUTPUTS"
LIVE_MODE_LABEL = "LIVE DATA"
SIMULATED_DATE = "2026-10-14"  # a plausible future NHL game night -- SIMULATED DATE, disclosed everywhere
SIMULATED_SEASON = 20262027

# Real team pairings for the simulated slate (Part 8: prefer 4-8 games).
DEMO_MATCHUPS = [
    ("EDM", "COL"), ("TOR", "BOS"), ("TBL", "NJD"),
    ("MIN", "CHI"), ("VAN", "WPG"), ("NYR", "DAL"),
]
DEMO_START_TIMES = ["7:00 PM ET", "7:30 PM ET", "8:00 PM ET", "7:00 PM ET", "9:00 PM ET", "10:00 PM ET"]

# Real player_ids, queried directly from research/player_sog/player_game_sog.jsonl
# (see the sprint's own data-discovery step) -- not invented.
NAMED_STAR_IDS: dict[str, tuple[str, str, str]] = {
    "8478402": ("Connor McDavid", "EDM", "C"),
    "8477934": ("Leon Draisaitl", "EDM", "C"),
    "8477492": ("Nathan MacKinnon", "COL", "C"),
    "8480069": ("Cale Makar", "COL", "D"),
    "8479318": ("Auston Matthews", "TOR", "C"),
    "8477956": ("David Pastrnak", "BOS", "R"),
    "8476453": ("Nikita Kucherov", "TBL", "R"),
    "8481559": ("Jack Hughes", "NJD", "C"),
    "8478864": ("Kirill Kaprizov", "MIN", "L"),
    "8484144": ("Connor Bedard", "CHI", "C"),
    "8480800": ("Quinn Hughes", "VAN", "D"),
}
# Real goalie_ids, queried from research/goalie_intelligence/actual_starters.jsonl
NAMED_GOALIE_IDS: dict[str, tuple[str, str]] = {
    "8476945": ("Connor Hellebuyck", "WPG"),
    "8478048": ("Igor Shesterkin", "NYR"),
    "8476883": ("Andrei Vasilevskiy", "TBL"),
    "8479979": ("Jake Oettinger", "DAL"),
}

DEMO_TEAMS = sorted({t for pair in DEMO_MATCHUPS for t in pair})
SUPPORTED_PROPS = ("sog", "goals", "assists", "points", "blocks")
PROP_MARKET_ID = {"sog": "PLAYER_SOG_3PLUS", "goals": "PLAYER_GOALS_1PLUS", "assists": "PLAYER_ASSISTS_1PLUS",
                   "points": "PLAYER_POINTS_1PLUS", "blocks": "PLAYER_BLOCKS_1PLUS"}
PROP_MARKET_FAMILY = {"sog": "SOG", "goals": "GOALS", "assists": "ASSISTS", "points": "POINTS", "blocks": "BLOCKED_SHOTS"}
PROP_THRESHOLD = {"sog": 3, "goals": 1, "assists": 1, "points": 1, "blocks": 1}


@dataclass
class DemoGame:
    game_id: str
    away: str
    home: str
    start_time: str
    date: str
    model_ready: str
    starter_ready: str
    market_ready: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class DemoPlayer:
    player_id: str
    name: str
    team: str
    opponent: str
    position: str
    is_home: bool


def _opponent_for(team: str) -> str | None:
    for away, home in DEMO_MATCHUPS:
        if team == away:
            return home
        if team == home:
            return away
    return None


@lru_cache(maxsize=1)
def build_demo_games() -> list[DemoGame]:
    """Part 8/9: SIMULATED NHL SLATE -- real teams, simulated date/games.
    Deterministic readiness assignment (Part 8: a realistic mix of
    readiness states, not all green)."""
    readiness_patterns = [
        ("READY", "CONFIRMED", "READY", []),
        ("READY", "PROJECTED", "READY", []),
        ("READY", "PROJECTED", "WAITING", ["Odds not yet posted for this matchup"]),
        ("READY", "UNCONFIRMED", "READY", ["Starter unconfirmed"]),
        ("READY", "CONFIRMED", "STALE", ["Market data stale (simulated)"]),
        ("WAIT", "PROJECTED", "DATA_UNAVAILABLE", ["Simulated MoneyPuck feed stale"]),
    ]
    games = []
    for i, ((away, home), start) in enumerate(zip(DEMO_MATCHUPS, DEMO_START_TIMES)):
        model_r, starter_r, market_r, warnings = readiness_patterns[i % len(readiness_patterns)]
        games.append(DemoGame(game_id=f"demo-{away}-{home}", away=away, home=home, start_time=start,
                               date=SIMULATED_DATE, model_ready=model_r, starter_ready=starter_r,
                               market_ready=market_r, warnings=warnings))
    return games


@lru_cache(maxsize=1)
def _demo_context() -> ShadowContextStack:
    """The one expensive object (loads all five frozen marginal corpora).
    Built once, cached for the process lifetime."""
    return ShadowContextStack()


@lru_cache(maxsize=1)
def _goalie_engine():
    from dashboard.goalie_saves_view import GoalieSavesEngine, load_results
    results = load_results()
    if results is None:
        return None
    return GoalieSavesEngine(results)


@lru_cache(maxsize=1)
def build_demo_roster() -> list[DemoPlayer]:
    """Part 5/6: real NHL player identities. Named stars (queried real
    IDs) plus real supporting cast queried directly from the corpus for
    each demo team -- not a hand-invented disconnected roster."""
    stack = _demo_context()
    rows = stack.ctx.sog.rows
    latest_season = max(r["season"] for r in rows)
    by_team_latest = {}
    for r in rows:
        if r["season"] != latest_season:
            continue
        by_team_latest.setdefault(r["team"], {})[r["player_id"]] = r

    roster: list[DemoPlayer] = []
    seen_ids = set()
    for pid, (name, team, pos) in NAMED_STAR_IDS.items():
        opp = _opponent_for(team)
        if opp is None:
            continue
        is_home = team == dict(DEMO_MATCHUPS).get(opp, None) or any(h == team for _a, h in DEMO_MATCHUPS)
        roster.append(DemoPlayer(pid, name, team, opp, pos, is_home))
        seen_ids.add(pid)

    # Part 6: also include real defensemen / middle-six / lower-volume
    # players per team, deterministically selected (sorted by player_id)
    # rather than hand-picked, so this reflects real corpus composition.
    for team in DEMO_TEAMS:
        opp = _opponent_for(team)
        if opp is None:
            continue
        candidates = sorted(
            (r for pid, r in by_team_latest.get(team, {}).items() if pid not in seen_ids),
            key=lambda r: r["player_id"])
        take = candidates[::max(1, len(candidates) // 4)][:3] if candidates else []
        for r in take:
            if r["player_id"] in seen_ids:
                continue
            is_home = any(h == team for _a, h in DEMO_MATCHUPS)
            roster.append(DemoPlayer(r["player_id"], r["player_name"], team, opp,
                                      r.get("position", "F"), is_home))
            seen_ids.add(r["player_id"])
    return roster


@lru_cache(maxsize=1)
def build_demo_goalies() -> list[dict]:
    """Real starter goalies for the demo teams -- named real goalies where
    available, else the team's most recent real starter from
    actual_starters.jsonl."""
    engine = _goalie_engine()
    stack = _demo_context()
    goalie_teams = {team: (gid, name) for gid, (name, team) in NAMED_GOALIE_IDS.items()}
    out = []
    for team in DEMO_TEAMS:
        opp = _opponent_for(team)
        if opp is None or team not in goalie_teams:
            continue
        gid, name = goalie_teams[team]
        thresholds = {"20+": "VALIDATED", "25+": "VALIDATED", "30+": "PARTIAL",
                      "35+": "REJECTED", "40+": "INSUFFICIENT_DATA"}
        proj = None
        if engine is not None:
            is_home = any(h == team for _a, h in DEMO_MATCHUPS)
            proj = engine.project(int(gid), team, opp, "home" if is_home else "away",
                                   0, SIMULATED_DATE, SIMULATED_SEASON)
        out.append({"goalie_id": gid, "name": name, "team": team, "opponent": opp,
                    "starter_status": "PROJECTED_STARTER", "starter_probability": 0.82,
                    "expected_saves": proj.get("expected_saves") if proj else None,
                    "confidence": proj.get("confidence") if proj else "MEDIUM",
                    "thresholds": thresholds})
    return out


def _rng_for(*key_parts) -> random.Random:
    """Deterministic per-entity RNG seeded from DEMO_SEED + a stable key
    (Part 23) -- same inputs always produce the same simulated price,
    across processes and across restarts.

    CRITICAL: must NOT use Python's builtin hash() here -- str/tuple
    hashing is randomized per-process (PYTHONHASHSEED) since Python 3.3
    for security reasons, so a hash()-based seed silently produces a
    DIFFERENT demo slate on every server restart despite looking
    deterministic within a single run. Found live in the browser: the
    "Best Available Market" for the same player changed across two
    consecutive server restarts. Fixed with a stable SHA-256 digest of
    the key parts instead."""
    raw = "|".join([str(DEMO_SEED)] + [str(k) for k in key_parts])
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def simulate_two_sided_market(true_prob: float, rng: random.Random) -> tuple[float, float]:
    """Part 14/16: a mathematically COMPLETE simulated two-sided market
    (both sides priced) with realistic vig and varied price levels --
    never a single invented number, never a flat -110 everywhere.

    Vig is applied by SCALING UP the two raw implied probabilities so
    they sum to (1 + vig) > 1 -- the standard sportsbook-margin
    construction, and the exact inverse of pm.no_vig_two_way's
    proportional de-vig (raw_a / (raw_a + raw_b)), so a caller who
    de-vigs this market recovers approximately `book_prob_yes`, not the
    model's own true_prob -- the market's simulated "opinion" is
    deliberately allowed to differ from the model's (Part 13's
    "internally coherent," not "internally identical to the model")."""
    vig = rng.uniform(0.04, 0.07)
    noise = rng.uniform(-0.05, 0.05)
    book_prob_yes = min(max(true_prob + noise, 0.03), 0.97)
    book_prob_no = 1 - book_prob_yes
    yes_odds = pm.prob_to_american(min(max(book_prob_yes * (1 + vig), 0.01), 0.99))
    no_odds = pm.prob_to_american(min(max(book_prob_no * (1 + vig), 0.01), 0.99))
    return yes_odds, no_odds


def _conservative_probability(prop: str, engine, mu: float | None, threshold: int, raw_p: float) -> float:
    """Mirrors research/player_sog/live_projection.py's own pattern
    exactly: shrink the COUNT-SCALE mu via cm.conservative_mu, THEN
    convert to a threshold probability -- never apply conservative_mu
    directly to an already-computed probability (that formula assumes a
    Poisson/NB rate, not a value in [0,1], and produces a wildly
    oversized haircut if misapplied that way).

    Points has no mu under the empirical-baseline champion -- per the
    already-disclosed architecture gap (CONTEXT_STATE_PROBABILITY_
    OVERLAY_REPORT.md Section AE), no conservative treatment is
    operationalized for Points, so conservative_probability is the
    identity here too, same as context_adjusted_probability defaults to
    raw when no overlay applies."""
    if mu is None:
        return raw_p
    alpha = getattr(engine, "alpha", None)
    eff_n = 20
    cons_mu = cm.conservative_mu(mu, eff_n)
    return cm.threshold_probabilities(cons_mu, alpha, thresholds=(threshold,))[threshold]


def _confidence_for(prop: str, engine, player_id: str, team: str, opponent: str, date: str) -> str:
    history = engine.index.history_as_of(player_id, date)
    if len(history) < 10:
        return "LOW"
    toi = [h["icetime_seconds"] for h in history[-10:]]
    stat = [h[prop] for h in history[-10:]]
    toi_cv = cm.coefficient_of_variation(toi) if toi else None
    stat_cv = cm.coefficient_of_variation(stat) if stat else None
    label, _drivers, _risks = cm.confidence_score(len(history), toi_cv, stat_cv, 20, 20, 0.9)
    return label


def player_activity_status(player_id: str, team: str, opponent: str) -> dict:
    """Preseason Closing sprint (Track 9): surfaces the REAL SOG engine's
    own activity gate -- never reimplemented -- so a real player with
    zero demo opportunities (e.g. PROJECTED_INACTIVE for insufficient
    recent team games) shows an honest, specific reason instead of a
    bare 'no qualifying market' message that reads like a bug."""
    stack = _demo_context()
    engine = stack.ctx.sog
    result = plp.project_player_sog(
        engine.rows, engine.index, engine.team_schedules, engine.opponent_allowed_history,
        engine.league_avg_sog_allowed, engine.weights, engine.alpha,
        player_id, team, opponent, SIMULATED_DATE, SIMULATED_SEASON,
    )
    return {"status": result.get("status"), "note": result.get("note")}


def build_demo_opportunities() -> list[dict]:
    """The core demo prop board (Part 11-18): real model probabilities
    for real players, real coherence/decision logic, simulated market
    prices. Deterministic across reruns."""
    stack = _demo_context()
    roster = build_demo_roster()
    game_by_teams = {(g.away, g.home): g for g in build_demo_games()}
    opportunities = []
    for player in roster:
        for prop in SUPPORTED_PROPS:
            engine = getattr(stack.ctx, prop)
            if prop in ("goals", "points"):
                result = stack.predict(player.player_id, player.team, player.opponent,
                                        SIMULATED_DATE, SIMULATED_SEASON)
                stage = result.get(prop)
                if stage is None:
                    continue
                raw_p = stage["raw_probability"]
                adj_p = stage["context_adjusted_probability"]
                coherent_p = stage["coherent_probability"]
                context_state = stage["context_state"]
                mu = stage.get("mu")
            else:
                pred = engine.predict(player.player_id, player.team, player.opponent,
                                       SIMULATED_DATE, SIMULATED_SEASON)
                if pred is None or pred["probs"].get(PROP_THRESHOLD[prop]) is None:
                    continue
                raw_p = pred["probs"][PROP_THRESHOLD[prop]]
                adj_p = raw_p
                coherent_p = raw_p
                context_state = None
                mu = pred.get("mu")

            conservative_p = _conservative_probability(prop, engine, mu, PROP_THRESHOLD[prop], raw_p)
            confidence = _confidence_for(prop, engine, player.player_id, player.team, player.opponent, SIMULATED_DATE)

            rng = _rng_for(player.player_id, prop)
            current_odds, opposing_odds = simulate_two_sided_market(coherent_p, rng)
            no_vig_prob, _ = pm.no_vig_two_way(current_odds, opposing_odds)
            fair_odds = pm.prob_to_american(coherent_p)
            raw_edge = coherent_p - no_vig_prob
            conservative_edge = conservative_p - no_vig_prob
            ev = pm.expected_value(conservative_p, current_odds)
            max_price = pm.max_acceptable_price(conservative_p, 0.02, opposing_odds)

            action, reason = decide(conservative_edge, ev, raw_edge, confidence, "PROJECTED")
            market_family = PROP_MARKET_FAMILY[prop]
            gated = decision_policy.gate_low_confidence(market_family, confidence, action, reason)
            final_decision, final_reason = gated["final_decision"], gated["policy_reason"] or reason

            # Demo-only readiness gate (NOT a change to the real decide()/
            # decision_policy pipeline): decide() itself doesn't currently
            # act on lineup/market readiness (see this sprint's report),
            # so this narrow, disclosed demo layer ties a game's simulated
            # market/starter readiness to its opportunities' decisions --
            # realistic operational behavior, and the only source of real
            # WAIT diversity in the demo board (Part 18).
            game = game_by_teams.get((player.team, player.opponent)) or game_by_teams.get((player.opponent, player.team))
            if game and final_decision in ("BET", "WATCH"):
                if game.market_ready != "READY":
                    final_decision, final_reason = "WAIT", f"simulated market readiness: {game.market_ready}"
                elif game.starter_ready == "UNCONFIRMED" and player.position == "G":
                    final_decision, final_reason = "WAIT", "simulated starter unconfirmed"

            opportunities.append({
                "player": player.name, "player_id": player.player_id, "team": player.team,
                "opponent": player.opponent, "market": market_family, "market_id": PROP_MARKET_ID[prop],
                "threshold": f"{PROP_THRESHOLD[prop]}+", "prop": prop,
                "raw_probability": raw_p, "context_adjusted_probability": adj_p,
                "coherent_probability": coherent_p, "conservative_probability": conservative_p,
                "context_state": context_state,
                "market_no_vig_probability": no_vig_prob, "fair_odds": fair_odds,
                "current_odds": current_odds, "max_acceptable_price": max_price,
                "raw_edge": raw_edge, "conservative_edge": conservative_edge, "ev": ev,
                "confidence": confidence, "decision": final_decision,
                "decision_reason": final_reason,
                "zone": zone(conservative_edge), "is_simulated_price": True,
            })
    return opportunities


def build_demo_market_movement(opportunities: list[dict] | None = None) -> list[dict]:
    """Part 82-86: deterministic simulated movement snapshots."""
    opportunities = opportunities if opportunities is not None else build_demo_opportunities()
    rows = []
    for o in opportunities[:12]:
        rng = _rng_for(o["player_id"], o["prop"], "movement")
        drift = rng.uniform(-15, 15)
        opening = o["current_odds"] + drift
        direction = "TOWARD MODEL" if abs(o["current_odds"] - o["fair_odds"]) < abs(opening - o["fair_odds"]) else "AWAY FROM MODEL"
        if abs(drift) < 2:
            direction = "NEUTRAL"
        rows.append({"player": o["player"], "market": f"{o['market']} {o['threshold']}",
                     "opening": opening, "current": o["current_odds"], "model_fair": o["fair_odds"],
                     "direction": direction, "is_simulated": True})
    return rows
