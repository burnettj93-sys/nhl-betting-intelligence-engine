"""
The decision layer: takes a model prediction + DraftKings reference-book
pricing and turns it into a report and a BET / WAIT / PASS / DATA_UNAVAILABLE
action.

DraftKings reference-book policy (hard requirements):
  - DraftKings is the ONLY sportsbook this engine prices against. If a
    valid DraftKings quote isn't available for both sides of a market,
    the result is DATA_UNAVAILABLE — never a silent fallback to another
    book. config.ALLOW_SPORTSBOOK_FALLBACK exists only as an explicit,
    human-set escape hatch and defaults to False; nothing in this module
    reads it to auto-substitute a book.
  - Every report is labeled "DraftKings reference pricing", never
    "consensus" or "best market".

Goalie-confirmation policy:
  - Both starting goalies must be CONFIRMED (features/point_in_time.py) or
    the action is WAIT, with the reason stated — UNLESS
    config.ALLOW_BETTING_ON_EXPECTED_STARTER is explicitly set True, in
    which case an EXPECTED (not yet CONFIRMED) starter is allowed but
    still widens the model's uncertainty band (see models/goalie_model.py).

Threshold policy (spec item 9): a probability-point edge and a %-return EV
are different quantities. BET requires BOTH conservative_edge >=
config.MIN_CONSERVATIVE_EDGE AND expected_value >= config.MIN_EV.
maximum_acceptable_price is a third, separate concept — the worst price
that would still have cleared MIN_CONSERVATIVE_EDGE against the
conservative probability — displayed for reference, not used as the gate
itself (the gate is the two checks above, evaluated at the ACTUAL price).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import config
from features import point_in_time as pit
from pricing import odds_math


@dataclass
class BetReport:
    game_label: str
    market: str
    selection: str
    label: str = "DraftKings reference pricing"
    sportsbook: str | None = config.REFERENCE_SPORTSBOOK
    model_true_probability: float | None = None
    model_conservative_probability: float | None = None
    market_implied_probability: float | None = None
    market_no_vig_probability: float | None = None
    model_fair_price: float | None = None
    conservative_fair_price: float | None = None
    current_draftkings_price: float | None = None
    maximum_acceptable_draftkings_price: float | None = None
    raw_edge: float | None = None
    conservative_edge: float | None = None
    expected_value: float | None = None
    zone: str | None = None
    action: str = "PASS"
    action_reason: str = ""
    kelly_stake_fraction: float = 0.0
    odds_snapshot_id_selection: int | None = None
    odds_snapshot_id_opponent: int | None = None
    notes: list[str] = field(default_factory=list)

    def format(self) -> str:
        lines = [
            f"GAME: {self.game_label}",
            f"MARKET: {self.market}",
            f"SELECTION: {self.selection}",
            f"PRICING: {self.label} (sportsbook={self.sportsbook})",
            "",
        ]
        if self.action == "DATA_UNAVAILABLE":
            lines += [f"ACTION: DATA_UNAVAILABLE", f"REASON: {self.action_reason}"]
            return "\n".join(lines)
        lines += [
            f"MODEL TRUE PROBABILITY: {self.model_true_probability:.1%}",
            f"MODEL CONSERVATIVE PROBABILITY: {self.model_conservative_probability:.1%}",
            f"MARKET IMPLIED PROBABILITY: {self.market_implied_probability:.1%}",
            f"MARKET NO-VIG PROBABILITY: {self.market_no_vig_probability:.1%}",
            "",
            f"MODEL FAIR PRICE: {self.model_fair_price:+.0f}",
            f"CONSERVATIVE FAIR PRICE: {self.conservative_fair_price:+.0f}",
            f"CURRENT DRAFTKINGS PRICE: {self.current_draftkings_price:+.0f}",
            "MAXIMUM ACCEPTABLE DRAFTKINGS PRICE: "
            + (f"{self.maximum_acceptable_draftkings_price:+.0f}"
               if self.maximum_acceptable_draftkings_price is not None
               else "N/A (no valid price could satisfy the required edge)"),
            "",
            f"CONSERVATIVE EDGE (probability points): {self.conservative_edge:+.1%}",
            f"EXPECTED VALUE (% return at current price): {self.expected_value:+.1%}",
            f"RAW EDGE: {self.raw_edge:+.1%}",
            "",
            f"ZONE: {self.zone}",
            f"ACTION: {self.action}",
        ]
        if self.action_reason:
            lines.append(f"REASON: {self.action_reason}")
        if self.action == "BET":
            lines.append(f"SUGGESTED STAKE: {self.kelly_stake_fraction:.2%} of bankroll "
                         f"({config.KELLY_FRACTION_MULTIPLIER:.0%} Kelly, capped at "
                         f"{config.MAX_SINGLE_BET_BANKROLL_PCT:.1%})")
        if self.notes:
            lines.append("NOTES: " + "; ".join(self.notes))
        lines.append(
            "CONSERVATIVE PROBABILITY BASIS: heuristic maturity-based uncertainty "
            "band (NOT a statistically validated confidence interval) -- see "
            "config.BASE_UNCERTAINTY_BAND_HALF_WIDTH."
        )
        return "\n".join(lines)


def _zone(conservative_edge: float) -> str:
    if conservative_edge >= config.EDGE_GREEN:
        return "GREEN"
    if conservative_edge >= config.EDGE_LIGHT_GREEN:
        return "LIGHT GREEN"
    if conservative_edge >= config.EDGE_YELLOW:
        return "YELLOW"
    return "RED"


def _data_unavailable(game_label: str, selection: str, market: str, reason: str) -> BetReport:
    return BetReport(game_label=game_label, market=market, selection=selection,
                      action="DATA_UNAVAILABLE", action_reason=reason)


def evaluate_moneyline_for_game(
    conn: sqlite3.Connection, pred, game_label: str,
    allow_expected_starter: bool = config.ALLOW_BETTING_ON_EXPECTED_STARTER,
    max_staleness_minutes: float | None = None,
    bankroll_fraction_cap: float = config.MAX_SINGLE_BET_BANKROLL_PCT,
) -> list[BetReport]:
    """Full decision for both sides of a game's moneyline market, using
    `pred` (a models.combined_model.GamePrediction, already computed at the
    correct prediction_time_utc) and the DraftKings snapshots available as
    of that same prediction_time_utc. Returns [home_report, away_report].

    v2.1 (spec item 15): `max_staleness_minutes` defaults to None, which
    means "use the DYNAMIC, time-to-puck-drop-sensitive policy" (see
    odds_math.dynamic_max_staleness_minutes / config.ODDS_STALENESS_TIERS)
    computed from pred.scheduled_start_utc -- NOT the old flat
    config.MAX_ODDS_STALENESS_MINUTES window, which is too permissive
    close to puck drop. Pass an explicit float to override with a fixed
    window (e.g. for a test that doesn't care about staleness policy)."""
    game_id = pred.game_id
    home, away = pred.home_team, pred.away_team

    if max_staleness_minutes is None:
        hours_to_puck_drop = odds_math.hours_between(
            pred.prediction_time_utc, pred.scheduled_start_utc)
        max_staleness_minutes = odds_math.dynamic_max_staleness_minutes(hours_to_puck_drop)

    home_snap, away_snap = pit.latest_draftkings_two_sided(
        conn, game_id, "MONEYLINE", home, away, pred.prediction_time_utc, max_staleness_minutes
    )
    if home_snap is None or away_snap is None:
        reason = (f"no valid DraftKings MONEYLINE quote for both {home} and {away} "
                  f"as of {pred.prediction_time_utc} (missing/stale/suspended/post-start)")
        return [
            _data_unavailable(game_label, home, "MONEYLINE", reason),
            _data_unavailable(game_label, away, "MONEYLINE", reason),
        ]

    reports = []
    for selection, own_snap, opp_snap, model_prob, conservative_prob, goalie_status in (
        (home, home_snap, away_snap, pred.model_prob_home, pred.conservative_prob_home,
         pred.home_goalie_status),
        (away, away_snap, home_snap, 1 - pred.model_prob_home, 1 - pred.ci_high,
         pred.away_goalie_status),
    ):
        # Compute the full market comparison regardless of goalie status —
        # a WAIT report should still show what the model/market say, just
        # with the action overridden. Only DATA_UNAVAILABLE (no market
        # price at all, handled above) skips this.
        market_price = own_snap["price_american"]
        opp_price = opp_snap["price_american"]
        market_implied = odds_math.american_to_prob(market_price)
        no_vig_selection, _ = odds_math.no_vig_two_way(market_price, opp_price)

        model_fair_price = odds_math.prob_to_american(model_prob)
        conservative_fair_price = odds_math.prob_to_american(conservative_prob)
        # v2.1.1a spec item 3: max_acceptable_price now needs the CURRENT
        # opponent price -- the engine's edge is measured against a
        # two-sided no-vig probability, so the target side's breakeven
        # price is not opponent-independent. May legitimately return None
        # (see odds_math.max_acceptable_price's docstring).
        max_price = odds_math.max_acceptable_price(
            conservative_prob, config.MIN_CONSERVATIVE_EDGE, opp_price)

        raw_edge = model_prob - no_vig_selection
        conservative_edge = conservative_prob - no_vig_selection
        ev = odds_math.expected_value(conservative_prob, market_price)
        zone = _zone(conservative_edge)

        goalie_ok = (goalie_status == "CONFIRMED" or
                     (allow_expected_starter and goalie_status == "EXPECTED"))
        # both teams' goalies matter to a moneyline bet on either side
        other_status = pred.away_goalie_status if selection == home else pred.home_goalie_status
        other_ok = (other_status == "CONFIRMED" or
                    (allow_expected_starter and other_status == "EXPECTED"))

        notes = []
        if not (goalie_ok and other_ok):
            unconfirmed = []
            if not goalie_ok:
                unconfirmed.append(f"{selection} goalie status={goalie_status}")
            if not other_ok:
                unconfirmed.append(f"opponent goalie status={other_status}")
            action = "WAIT"
            reason = "starting goalie not confirmed: " + "; ".join(unconfirmed)
        else:
            if goalie_status != "CONFIRMED":
                notes.append(f"{selection} starter status={goalie_status} (betting allowed by policy)")
            meets_edge = conservative_edge >= config.MIN_CONSERVATIVE_EDGE
            meets_ev = ev >= config.MIN_EV
            if meets_edge and meets_ev:
                action, reason = "BET", ""
            else:
                action = "PASS"
                missing = []
                if not meets_edge:
                    missing.append(f"conservative edge {conservative_edge:+.1%} < "
                                    f"required {config.MIN_CONSERVATIVE_EDGE:+.1%}")
                if not meets_ev:
                    missing.append(f"EV {ev:+.1%} < required {config.MIN_EV:+.1%}")
                reason = "; ".join(missing)

        kelly = 0.0
        if action == "BET":
            full_kelly = odds_math.kelly_fraction(conservative_prob, market_price)
            kelly = min(full_kelly * config.KELLY_FRACTION_MULTIPLIER, bankroll_fraction_cap)

        reports.append(BetReport(
            game_label=game_label, market="MONEYLINE", selection=selection,
            model_true_probability=model_prob, model_conservative_probability=conservative_prob,
            market_implied_probability=market_implied, market_no_vig_probability=no_vig_selection,
            model_fair_price=model_fair_price, conservative_fair_price=conservative_fair_price,
            current_draftkings_price=market_price, maximum_acceptable_draftkings_price=max_price,
            raw_edge=raw_edge, conservative_edge=conservative_edge, expected_value=ev,
            zone=zone, action=action, action_reason=reason, kelly_stake_fraction=kelly,
            odds_snapshot_id_selection=own_snap["id"], odds_snapshot_id_opponent=opp_snap["id"],
            notes=notes,
        ))
    return reports
