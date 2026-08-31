"""
Live DK / Paper Bankroll completion sprint (2026-08-31), Parts 18-22:
real DraftKings MONEYLINE vs the real Elo win model, built ONLY from
archived Odds API evidence (data/raw/the_odds_api/live/) -- this module
never calls the live Odds API itself, so viewing the dashboard never
spends a credit. The archive was populated by a one-time, credit-metered
investigative probe (see LIVE_DK_PAPER_BANKROLL_COMPLETION_REPORT.md);
re-running that probe is a separate, deliberate action, not something a
page load triggers.

Reuses, never reimplements: research/generic_prop_pricing/provider_adapter.py's
verified MONEYLINE parser (the ONLY verified contract -- every other
market family returns CONTRACT_NOT_VERIFIED, on purpose, per Part 16),
research/live_sog_pricing/archive.py's real archive reader,
dashboard/game_detail_view.py::demo_win_model()'s real frozen-Elo win
probability (the SAME function the simulated demo slate already uses --
it needs only team abbreviations, not a live nhl.db game_id, which does
not exist yet for the 2026-27 schedule -- see the report's honest
disclosure of this), and pricing/odds_math.py's real no-vig/EV math.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from dashboard import data_access as da
from dashboard import game_detail_view as gdv
from pricing import odds_math as pm
from research.generic_prop_pricing.evaluator import decide, zone
from research.generic_prop_pricing import provider_adapter as pa
from research.live_sog_pricing import archive

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = REPO_ROOT / "data" / "raw" / "the_odds_api" / "live"

LIVE_SOURCE_LABEL = "LIVE — DRAFTKINGS"
SIMULATED_SOURCE_LABEL = "SIMULATED — DEMO ONLY"

# Part 20's own instruction ("never manipulate probabilities... to
# create an exciting demo") cuts both ways: a real edge computed from a
# stale input is just as misleading as a fabricated one if presented as
# actionable. The real historical corpus this Elo rating comes from
# currently ends well before today (STALE per System Health) -- for a
# game this far past the corpus's last known date, "the model" no
# longer reflects the current roster/season closely enough to trust for
# a real decision, even though the arithmetic itself is completely real
# and correctly computed. 30 days is a deliberately conservative,
# documented cutoff -- a genuinely fresh in-season Elo rating checked
# against next week's game would be nowhere near it; it exists to catch
# exactly this kind of multi-month gap, not to be reverse-engineered to
# force a particular outcome (every real event probed this sprint is
# ~166 days past the corpus end regardless of where between 1 and ~150
# days the cutoff is set).
MAX_ELO_STALENESS_DAYS = 30


def _elo_corpus_staleness_days(commence_time_iso: str) -> float | None:
    try:
        predictions = da.compute_baseline_predictions()
    except da.DataAvailabilityError:
        return None
    dates = da.available_dates(predictions)
    if not dates:
        return None
    corpus_end = dt.datetime.fromisoformat(dates[-1]).replace(tzinfo=dt.timezone.utc)
    commence = dt.datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
    return (commence - corpus_end).total_seconds() / 86400.0


def _iter_archived_h2h_markets():
    """Yields (event_id, NormalizedMoneylineMarket, retrieved_at_utc) for
    every archived probe response that actually parses as a verified
    MONEYLINE market -- every non-h2h archive (the much larger set of
    player-prop probes that found nothing, from this and prior sprints)
    naturally falls out via parse_the_odds_api_h2h_market() returning
    DATA_UNAVAILABLE, never crashes, never guessed."""
    if not ARCHIVE_DIR.exists():
        return
    for path in sorted(ARCHIVE_DIR.glob("*.json")):
        try:
            loaded = archive.load_archived(path)
        except (OSError, ValueError):
            continue
        event = loaded.get("response")
        if not isinstance(event, dict):
            continue
        result = pa.parse_the_odds_api_h2h_market(event)
        if result["status"] != "PARSED":
            continue
        yield event.get("id"), result["market"], loaded.get("meta", {}).get("retrieved_at_utc")


def load_latest_verified_moneyline_markets() -> dict[str, dict]:
    """One entry per event_id (the LATEST archived capture, by
    retrieved_at_utc, if the same event was probed more than once) --
    {"market": NormalizedMoneylineMarket, "retrieved_at_utc": str}."""
    latest: dict[str, dict] = {}
    for event_id, market, retrieved_at in _iter_archived_h2h_markets():
        if event_id is None:
            continue
        current = latest.get(event_id)
        if current is None or (retrieved_at or "") > (current["retrieved_at_utc"] or ""):
            latest[event_id] = {"market": market, "retrieved_at_utc": retrieved_at}
    return latest


def _decide_from_win_probability(model_prob: float, market_prob: float, current_price: float,
                                  opposing_price: float) -> dict:
    fair_price = pm.prob_to_american(model_prob)
    raw_edge = model_prob - market_prob
    ev = pm.expected_value(model_prob, current_price)
    max_price = pm.max_acceptable_price(model_prob, 0.02, opposing_price)
    # MONEYLINE has no separate "conservative" probability layer in this
    # engine (unlike the count-model props) -- the real Elo win
    # probability IS the model's own single output, so raw and
    # conservative edge/EV are identical here, never a fabricated second
    # number. Confidence for a real Elo rating with a full historical
    # corpus behind it is HIGH by the same standard used elsewhere
    # (>= 10 games of real history) -- Elo ratings require hundreds.
    action, reason = decide(raw_edge, ev, raw_edge, "HIGH", "CONFIRMED")
    return {"model_probability": model_prob, "market_no_vig_probability": market_prob,
            "fair_odds": fair_price, "raw_edge": raw_edge, "conservative_edge": raw_edge,
            "ev": ev, "max_acceptable_price": max_price, "decision": action,
            "decision_reason": reason, "zone": zone(raw_edge)}


def build_live_moneyline_comparisons() -> list[dict]:
    """Part 19: for every real, verified MONEYLINE market with a
    resolvable real Elo rating on both sides, a full model-vs-market
    comparison row, using the exact real decide() policy already used
    everywhere else in this engine. Returns rows with
    source=LIVE_SOURCE_LABEL -- never mixed into or mistaken for the
    simulated demo board.

    Staleness gate: if the Elo rating predates the event by more than
    MAX_ELO_STALENESS_DAYS, the decision is force-downgraded to WAIT
    regardless of the raw edge -- a real number computed from a rating
    that no longer reflects the current roster is not something this
    engine will present as actionable (see the module docstring)."""
    rows = []
    for event_id, entry in load_latest_verified_moneyline_markets().items():
        market = entry["market"]
        home, away = market.home_team_abbrev, market.away_team_abbrev
        win_model = gdv.demo_win_model(away, home)
        if win_model is None:
            rows.append({
                "event_id": event_id, "home_team": home, "away_team": away,
                "captured_at_utc": entry["retrieved_at_utc"], "source": LIVE_SOURCE_LABEL,
                "status": "DATA_UNAVAILABLE",
                "reason": "no real Elo rating available for one or both teams",
            })
            continue

        staleness_days = _elo_corpus_staleness_days(market.commence_time_utc) if market.commence_time_utc else None
        stale = staleness_days is not None and staleness_days > MAX_ELO_STALENESS_DAYS

        no_vig_home, no_vig_away = pm.no_vig_two_way(market.home_price, market.away_price)
        game_date = market.commence_time_utc[:10] if market.commence_time_utc else None
        home_row = {
            "event_id": event_id, "home_team": home, "away_team": away, "side": home, "team": home,
            "opponent": away, "captured_at_utc": entry["retrieved_at_utc"], "source": LIVE_SOURCE_LABEL,
            "status": "PRICED", "current_odds": market.home_price, "market": "MONEYLINE",
            "market_id": "MONEYLINE", "game_date": game_date, "event_start_utc": market.commence_time_utc,
            "commence_time_utc": market.commence_time_utc, "elo_staleness_days": staleness_days,
        }
        home_row.update(_decide_from_win_probability(
            win_model["home_win_p"], no_vig_home, market.home_price, market.away_price))
        away_row = {
            "event_id": event_id, "home_team": home, "away_team": away, "side": away, "team": away,
            "opponent": home, "captured_at_utc": entry["retrieved_at_utc"], "source": LIVE_SOURCE_LABEL,
            "status": "PRICED", "current_odds": market.away_price, "market": "MONEYLINE",
            "market_id": "MONEYLINE", "game_date": game_date, "event_start_utc": market.commence_time_utc,
            "commence_time_utc": market.commence_time_utc, "elo_staleness_days": staleness_days,
        }
        away_row.update(_decide_from_win_probability(
            win_model["away_win_p"], no_vig_away, market.away_price, market.home_price))

        if stale:
            for row in (home_row, away_row):
                row["decision"] = "WAIT"
                row["decision_reason"] = (
                    f"real edge computed ({row['raw_edge']:+.1%}), but the Elo rating behind it is "
                    f"{staleness_days:.0f} days older than this game (> {MAX_ELO_STALENESS_DAYS}-day "
                    f"policy) -- roster/season context may have changed too much to trust; never "
                    f"presented as a BET on a stale input")

        rows.extend([home_row, away_row])
    return rows
