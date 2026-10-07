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
        "projection": ("PARTIAL", "Rolling-form model (last 20/60 games, current season) works. The validated "
                                   "research model is blocked: its corpus ends 2026-04-15 (CORPUS_STALE in the sweep logs)."),
        "context_confirmation": ("MISSING", "No lineup/injury feed. Proxy only: the player dressed in his team's last real "
                                            "game with 20+ games of history and 12+ minutes of ice time."),
        "eligibility": ("OK", "Contract verified; thresholds 2-5 validated."),
        "settlement": ("OK", "PLAYER_SOG_<k>PLUS via the outcome resolver; did-not-dress legs void under the documented rules."),
    },
    {
        "market": "Shots on goal (standard two-sided)", "family": "PLAYER_SOG",
        "ontario_menu": "Over/under shots listed on the DK Ontario menu.",
        "prices": ("PARTIAL", "Pulled occasionally (5 captures on 2026-10-06/07); not part of the regular capture."),
        "identity_mapping": ("OK", "Same mapping as the alternate ladder."),
        "projection": ("PARTIAL", "Validated model only; stale corpus, so no legs are produced today."),
        "context_confirmation": ("MISSING", "Same as above."),
        "eligibility": ("OK", "Contract verified."),
        "settlement": ("OK", "Same resolver."),
    },
    {
        "market": "Goalie saves", "family": "GOALIE_SAVES",
        "ontario_menu": "Saves ladders listed (observed 24+ to 34+). The model validates only 20+ and 25+, which are not "
                        "DK Ontario's lines.",
        "prices": ("OK", "player_total_saves pulled by the sweeps."),
        "identity_mapping": ("OK", "Goalie name + team."),
        "projection": ("PARTIAL", "Validated for 20+/25+ only; the Ontario ladder (24+, 26+, 28+, ...) is untested."),
        "context_confirmation": ("MISSING", "Starting goalie is never confirmed (no feed), so the starter gate blocks every "
                                            "saves leg. This gate is not bypassed."),
        "eligibility": ("MISSING", "Blocked by the starter gate."),
        "settlement": ("OK", "GOALIE_SAVES_<k>PLUS; a goalie who does not play voids that leg."),
    },
    {
        "market": "Moneyline", "family": "MONEYLINE",
        "ontario_menu": "Listed (verified).",
        "prices": ("PARTIAL", "h2h is pulled every 2 minutes, but the T-35 evaluation only accepts a quote for its "
                              "evaluation time (about 35 minutes before puck drop); earlier it reports DATA_UNAVAILABLE "
                              "(no valid quote as of that time). That is today's actual exclusion reason."),
        "identity_mapping": ("OK", "Team abbreviations."),
        "projection": ("PARTIAL", "T-35 Elo-based model; its uncertainty band is a heuristic, never calibrated."),
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
        "projection": ("MISSING", "No goal-margin model."),
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
        "projection": ("PARTIAL", "Locked points model blended with the last-60 hit rate, current-season data. A research "
                                  "model, not validated against live results."),
        "context_confirmation": ("MISSING", "No lineup/injury feed; same dressed-last-game proxy as shots."),
        "eligibility": ("OK", "Contract certified against a real archived payload (tests/fixtures/"
                              "draftkings_player_points_real_payload.json); thresholds 1 and 2 in the engine allowlist."),
        "settlement": ("OK", "PLAYER_POINTS_<k>PLUS (goals + assists) via the outcome resolver; did-not-dress legs void under "
                             "the documented rules."),
    },
    {
        "market": "Goals (anytime scorer)", "family": "ANYTIME_GOALSCORER",
        "ontario_menu": "Listed (verified).",
        "prices": ("MISSING", "player_goal_scorer_anytime is never requested."),
        "identity_mapping": ("OK", "Same mapping."),
        "projection": ("PARTIAL", "A goals research model exists but is not wired into pricing."),
        "context_confirmation": ("MISSING", "Same proxy only."),
        "eligibility": ("MISSING", "No contract certification."),
        "settlement": ("PARTIAL", "The resolver supports goals, but the ledger does not map the leg."),
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
           "`player_points`, hits; never `spreads` or a goal-scorer market).",
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
            "* Shots on goal and points (EXPERIMENTAL rolling-form model; the validated shots model joins when its corpus is",
            "  fresh) and moneyline (from about 35 minutes before puck drop) can produce ticket legs.",
            "* Saves are blocked by the starter-certainty gate (no starting-goalie confirmation exists). That gate is not",
            "  bypassed. Moneyline is not blocked by missing goalie confirmation; it carries a heuristic widened band instead.",
            "* Points are enabled (certified contract, settlement mapping, rolling model). Puck line and goals are not enabled",
            "  to fill slots: they lack prices, models and/or settlement.",
            "* An empty board on a day with few priced shots legs is a correct result, not a bug.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    from pathlib import Path
    target = Path(__file__).resolve().parent.parent / "docs" / "MARKET_COVERAGE_AUDIT.md"
    target.write_text(render_markdown())
    print(f"wrote {target}")
