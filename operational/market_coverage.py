"""
Market coverage audit: for each market the ticket workflow could use, which of
the six required components exist, and what is missing. Static facts come from
the code and the real archives (see docs/MARKET_COVERAGE_AUDIT.md for the
evidence); the per-run counts come from the diagnostics of the latest ticket
cycle. Nothing here enables a market: eligibility is decided by
research/real_market_parlay/engine.py::ALLOWED_MARKET_FAMILIES and the adapters.

Status values: OK, PARTIAL, MISSING.
"""
from __future__ import annotations

from research.real_market_parlay.engine import ALLOWED_MARKET_FAMILIES

COMPONENTS = ("prices", "identity_mapping", "projection", "context_confirmation", "eligibility", "settlement")

_ROWS = [
    {
        "market": "Shots on goal (alternate ladder 2+..5+)", "family": "PLAYER_SOG_ALTERNATE",
        "ontario_menu": "Market family listed on the DK Ontario menu (owner screenshots, 2026-09-29).",
        "prices": ("OK", "DraftKings player_shots_on_goal_alternate pulled by the prop sweeps and best_bets capture."),
        "identity_mapping": ("OK", "Name + team matched to the MoneyPuck/NHL player id."),
        "projection": ("OK", "player-rate-toi-v2 with a calibration fitted on a separate season: the calibrated probability beat both "
                             "baselines for shots 1+ to 5+ on the held-out 2025-26 season (docs/validation/skater_projection_validation.json). "
                             "Players with fewer than 20 prior games are not priced."),
        "context_confirmation": ("MISSING", "No lineup/injury feed. Proxy only: the player dressed in his team's last real "
                                            "game with 20+ games of history and 12+ expected minutes."),
        "eligibility": ("OK", "Contract verified; thresholds 2-5 validated."),
        "settlement": ("OK", "PLAYER_SOG_<k>PLUS via the outcome resolver; did-not-dress legs void under the documented rules."),
    },
    {
        "market": "Shots on goal (standard two-sided)", "family": "PLAYER_SOG",
        "ontario_menu": "Over/under shots listed on the DK Ontario menu.",
        "prices": ("PARTIAL", "Pulled occasionally (5 captures on 2026-10-06/07); not part of the regular capture."),
        "identity_mapping": ("OK", "Same mapping as the alternate ladder."),
        "projection": ("PARTIAL", "The standard-market leg builder still uses the retired research pipeline (corpus ends 2026-04-15), so it "
                                  "produces no legs; the v2 probabilities could price Over/Under lines but are not wired to this builder."),
        "context_confirmation": ("MISSING", "Same as above."),
        "eligibility": ("OK", "Contract verified."),
        "settlement": ("OK", "Same resolver."),
    },
    {
        "market": "Goalie saves", "family": "GOALIE_SAVES",
        "ontario_menu": "Saves ladders listed (observed 24+ to 34+). The goalie model is validated across 20+ to 35+, which covers that ladder.",
        "prices": ("OK", "player_total_saves pulled by the sweeps."),
        "identity_mapping": ("OK", "Goalie name + team."),
        "projection": ("OK", "goalie-team-v1: raw probabilities beat both baselines at every threshold 20+ to 35+ on the held-out 2025-26 "
                             "season; the fitted calibration did not help, so it is not used (docs/validation/goalie_team_validation.json)."),
        "context_confirmation": ("PARTIAL", "Daily Faceoff's public starting-goalies page is read automatically (operational/dailyfaceoff.py). A start counts as "
                                            "CONFIRMED only when the cited source is the team's own post; a beat reporter's \"Confirmed\" is kept as an expectation "
                                            "unless the owner opts in (on 2026-10-08, 1 of 11 Confirmed labels was team-sourced). A person can still record a "
                                            "confirmation with its source and time (operational/goalie_confirmations.py)."),
        "eligibility": ("PARTIAL", "The starter gate is unchanged and not bypassed: a leg exists only for a goalie with a recorded (team-post or manual) confirmation."),
        "settlement": ("OK", "GOALIE_SAVES_<k>PLUS; a goalie who does not play voids that leg."),
    },
    {
        "market": "Moneyline", "family": "MONEYLINE",
        "ontario_menu": "Listed (verified).",
        "prices": ("PARTIAL", "h2h is pulled every 2 minutes, but the T-35 evaluation only accepts a quote for its "
                              "evaluation time (about 35 minutes before puck drop); earlier it reports DATA_UNAVAILABLE "
                              "(no valid quote as of that time). That is today's actual exclusion reason."),
        "identity_mapping": ("OK", "Team abbreviations."),
        "projection": ("PARTIAL", "The T-35 Elo model (its band is a heuristic) drives pricing. The strength model (goalie-team-v1) shown on Games was compared with it "
                                  "chronologically on the same games (docs/validation/moneyline_model_comparison.json): nominally ahead in both folds, but the paired "
                                  "interval includes zero in both, and neither is shown to beat a sportsbook price. It is therefore NOT promoted. A versioned opt-in "
                                  "switch and a prospective shadow log (operational/moneyline_model_path.py) collect the market-inclusive evidence."),
        "context_confirmation": ("PARTIAL", "Starting goalies are never confirmed (no feed). Moneyline is NOT blocked by that: "
                                            "config.REQUIRE_GOALIE_CONFIRMATION is False, and an unconfirmed starter widens the "
                                            "model's confidence band 1.4x (UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING, a "
                                            "heuristic) before the edge/EV gates run."),
        "eligibility": ("PARTIAL", "Eligible whenever the T-35 evaluation has a valid quote and clears its own edge/EV "
                                   "gates; if the strict gate were ever enabled, the adapter reports WAIT."),
        "settlement": ("OK", "Final score including overtime/shootout."),
    },
    {
        "market": "Puck line", "family": "PUCK_LINE",
        "ontario_menu": "Listed (verified), plus alternate lines.",
        "prices": ("MISSING", "The spreads market is never requested from the odds provider."),
        "identity_mapping": ("OK", "Team abbreviations would reuse the moneyline mapping."),
        "projection": ("MISSING", "The Poisson margin model did NOT beat the base rate on held-out 2025-26 games (kept on record: docs/validation/goalie_team_validation.json). "
                                  "A direct-logistic alternative (puck-line-direct-v1) beats the base rate on two earlier development folds but has had no untouched "
                                  "evaluation: its parameters are frozen and it is scored only on 2026-27 games as they finish (docs/validation/puck_line_alternative.json). "
                                  "Not enabled."),
        "context_confirmation": ("PARTIAL", "Same unconfirmed-starter situation as moneyline (widened band, no gate)."),
        "eligibility": ("MISSING", "Not in the contract allowlist; no certified contract."),
        "settlement": ("MISSING", "No resolver for margin lines."),
    },
    {
        "market": "Points (1+, 2+)", "family": "PLAYER_POINTS",
        "ontario_menu": "Listed (verified), ladder 1-3. Only the 1+ and 2+ lines are modeled.",
        "prices": ("OK", "player_points (two-sided Over/Under at 0.5, sometimes 1.5) captured by best_bets "
                         "(12 captures on 2026-10-06/07)."),
        "identity_mapping": ("OK", "Name + team to player id."),
        "projection": ("OK", "player-rate-toi-v2 with calibration: points 1+ and 2+ beat both baselines on the held-out 2025-26 season."),
        "context_confirmation": ("MISSING", "No lineup/injury feed; same dressed-last-game proxy as shots."),
        "eligibility": ("OK", "Contract certified against a real archived payload (tests/fixtures/"
                              "draftkings_player_points_real_payload.json); thresholds 1 and 2 in the engine allowlist."),
        "settlement": ("OK", "PLAYER_POINTS_<k>PLUS (goals + assists) via the outcome resolver; did-not-dress legs void under "
                             "the documented rules."),
    },
    {
        "market": "Goals (anytime scorer)", "family": "PLAYER_GOALS",
        "ontario_menu": "Listed (verified).",
        "prices": ("PARTIAL", "player_goal_scorer_anytime is requested and available (DraftKings, one-sided Yes prices, 36-37 players a game; four real captures on "
                              "2026-10-05). Capture is added to the per-game call only when the credit month still balances after its cost of one credit per game "
                              "(operational/credit_allocation.py); on 2026-10-08 it does not (short 452 credits), so it is currently off."),
        "identity_mapping": ("OK", "Same mapping."),
        "projection": ("OK", "player-rate-toi-v2: goals 1+ beat both baselines on the held-out 2025-26 season; the probability feeds ticket legs."),
        "context_confirmation": ("PARTIAL", "Reported lineup and injury status from Daily Faceoff are shown on Players; pricing still uses the dressed-in-last-game proxy."),
        "eligibility": ("OK", "Contract certified against a real archived payload (tests/fixtures/draftkings_player_goal_scorer_real_payload.json); threshold 1 in the allowlist."),
        "settlement": ("OK", "PLAYER_GOALS_1PLUS via the outcome resolver (goals from the official boxscore); did-not-dress legs void under the documented rules."),
    },
]

PRICE_FEED_CAVEAT = ("Prices come from The Odds API's 'draftkings' bookmaker, the US feed. The Ontario menu "
                     "check is of market families only (owner screenshots, 2026-09-29); Ontario lines and prices can "
                     "differ and have not been verified.")


def audit(diagnostics: dict | None = None) -> dict:
    rows = []
    for row in _ROWS:
        entry = {"market": row["market"], "family": row["family"], "ontario_menu": row["ontario_menu"],
                 "in_ticket_allowlist": row["family"] in ALLOWED_MARKET_FAMILIES, "components": {}, "missing": []}
        for component in COMPONENTS:
            status, note = row[component]
            entry["components"][component] = {"status": status, "note": note}
            if status != "OK":
                entry["missing"].append(f"{component.replace('_', ' ')}: {note}")
        rows.append(entry)
    sources = ((diagnostics or {}).get("sources")) or {}
    return {"rows": rows, "price_feed_caveat": PRICE_FEED_CAVEAT, "last_cycle_sources": sources}


def render_markdown() -> str:
    a = audit()
    out = ["# Market coverage audit", "",
           "Which components exist for each market the ticket workflow could use. Generated by",
           "`python3 -m operational.market_coverage` from the table in that module (the same table Today shows",
           "under the technical toggle).",
           "Price evidence: archived Odds API payloads in `operational/odds_archive/live` (market keys requested on",
           "2026-10-06/07: `h2h`, `player_shots_on_goal_alternate`, `player_shots_on_goal`, `player_total_saves`,",
           "`player_points`, hits; one five-market diagnostic on 2026-10-05 included `player_goal_scorer_anytime`; never `spreads`).",
           "Ontario evidence: `research/dk_ontario_market_registry.py` (owner screenshots of the DK Ontario menu, 2026-09-29).",
           "", "> " + a["price_feed_caveat"], "",
           "Status: OK = exists, PARTIAL = exists with a stated limit, MISSING = does not exist.", "",
           "| Market | Prices | Identity | Projection | Context confirmation | Eligibility | Settlement | In ticket allowlist |",
           "|---|---|---|---|---|---|---|---|"]
    for r in a["rows"]:
        c = r["components"]
        out.append(f"| {r['market']} | " + " | ".join(c[k]["status"] for k in COMPONENTS)
                   + f" | {'yes' if r['in_ticket_allowlist'] else 'no'} |")
    out += ["", "## What is missing, per market", ""]
    for r in a["rows"]:
        out += [f"### {r['market']}", f"* Ontario menu: {r['ontario_menu']}"]
        out += [f"* {m}" for m in r["missing"]] or ["* nothing"]
        out.append("")
    out += ["## Consequences", "",
            "* Shots on goal and points (validated projection model with calibration) and moneyline (from about 35 minutes before",
            "  puck drop) can produce ticket legs.",
            "* Saves have a validated model but need a confirmed starting goalie (the team's own post read automatically, or a person records",
            "  the source and time). That gate is not bypassed. Moneyline is not blocked by missing goalie confirmation; it carries a heuristic",
            "  widened band instead.",
            "* Puck line is unmet: no prices requested, no validated margin model (the alternative is awaiting untouched 2026-27 evidence), no settlement resolver.",
            "* Anytime goals is built end to end (certified contract, leg, settlement) but its prices are captured only when the credit month balances; today it does not.",
            "* An empty board on a day with few priced shots legs is a correct result, not a bug.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    from pathlib import Path
    target = Path(__file__).resolve().parent.parent / "docs" / "MARKET_COVERAGE_AUDIT.md"
    target.write_text(render_markdown())
    print(f"wrote {target}")
