"""Machine-readable state of every lesson the postmortem persists: which lessons may change tomorrow's review NOW, which are accumulating evidence toward a threshold, and which are report-only.

A lesson is not a penalty. A result becomes a decision input only when (a) the evidence file shows the signal carries information beyond the model, or (b) the settled record has cleared the sample bar
(n >= MIN_N cases and |z| >= 2). Everything else is measured prospectively with its threshold written down, so what would change a decision is explicit and nothing changes it by anecdote. A player who killed two
tickets is not penalised: that is one outcome shown through reuse, not evidence about the player.

States
  ACTIONABLE     enough evidence exists; a named consumer in the review reads it (CONSUMERS below; tests assert each actionable lesson has one).
  ACCUMULATING   persisted and measured; `threshold` says what is needed; `on_threshold` says what may then happen (an automatic caution, or a PROPOSAL to the owner where a rule or policy would change).
  REPORT_ONLY    descriptive; not predictive; changes nothing.
"""
from __future__ import annotations

ACTIONABLE, ACCUMULATING, REPORT_ONLY = "ACTIONABLE", "ACCUMULATING", "REPORT_ONLY"
MIN_N = 30                       # the engine's own bar for a finding (operational/intelligence_metrics.py MIN_N)
Z_BAR = 2.0

# lesson id -> the code that reads it, for ACTIONABLE lessons only
CONSUMERS = {
    "ROLE_CAUTION": "operational.eggy_intelligence._load_overlay_inputs -> recency_overlay (POSTMORTEM_ROLE_CAUTION flag; its measured adjustment through apply_signal_evidence)",
    "CALIBRATION_BAND_OVERCONFIDENCE": "operational.eggy_intelligence._load_overlay_inputs -> calibration_caution_flag (LIVE_CALIBRATION_OVERCONFIDENT flag on legs in that band)",
    "MONEYLINE_GAP_TRUST": "operational.eggy_intelligence._load_overlay_inputs -> review_moneyline card['market_trust'] (decision layer D)",
}


def _cell(m, *path):
    for k in path:
        m = (m or {}).get(k) if isinstance(m, dict) else None
    return m or {}


def _acc(name, lid, cell, what, on_threshold, owner_decision=True):
    n, z = int(cell.get("n") or 0), cell.get("z")
    return {"id": lid, "name": name, "state": ACCUMULATING, "evidence": {"n": n, "hit": cell.get("hit"), "rate": cell.get("rate"), "expected_rate": cell.get("expected_rate"), "z": z},
            "threshold": {"n_required": MIN_N, "abs_z_required": Z_BAR, "met": bool(n >= MIN_N and z is not None and abs(z) >= Z_BAR)}, "measures": what, "on_threshold": on_threshold,
            "can_change_decisions_automatically": False, "owner_decision_required": owner_decision}


def build(metrics: dict | None, cautions: list[dict], calibration_cautions: list[dict], signal_evidence: dict | None, trust: dict | None = None) -> list[dict]:
    """The lesson states from the measured record (`metrics`: operational/intelligence_metrics.compute), the persisted cautions and the evidence file's flag evidence."""
    m = metrics or {}
    out: list[dict] = []
    ev = (signal_evidence or {}).get("POSTMORTEM_ROLE_CAUTION") or {}
    validated = sorted(k for k, v in ev.items() if str(v.get("verdict", "")).startswith("BEYOND_MODEL_LOWER"))
    out.append({"id": "ROLE_CAUTION", "name": "a role / ice-time / power-play miss becomes a short caution on that player", "state": ACTIONABLE if validated else REPORT_ONLY,
                "evidence": {"validated_markets": validated, "active_cautions": len(cautions), "source": "docs/validation/player_signals.json flag_evidence POSTMORTEM_ROLE_CAUTION"},
                "consumed_by": CONSUMERS["ROLE_CAUTION"], "window_days": 3, "can_change_decisions_automatically": bool(validated), "owner_decision_required": False})
    bands = _cell(m, "legs", "calibration_by_market_and_band")
    live = {(c["market"], c["band"]) for c in calibration_cautions or []}
    for mk, cells in bands.items():
        for band, c in cells.items():
            if (mk, band) in live:
                out.append({"id": "CALIBRATION_BAND_OVERCONFIDENCE", "name": f"{mk} estimates in the {band} band have run too high", "state": ACTIONABLE,
                            "evidence": {"n": c["n"], "z": c["z"], "rate": c["rate"], "expected_rate": c["expected_rate"]}, "consumed_by": CONSUMERS["CALIBRATION_BAND_OVERCONFIDENCE"],
                            "can_change_decisions_automatically": True, "owner_decision_required": False})
            elif c["n"] >= 5:
                x = _acc(f"{mk} {band} calibration", "CALIBRATION_BAND_OVERCONFIDENCE", c, "observed against estimated in this band",
                         "becomes an automatic MODERATE caution on legs in this band (LIVE_CALIBRATION_OVERCONFIDENT) when n >= 30 and z <= -2", owner_decision=False)
                x["can_change_decisions_automatically"] = True
                out.append(x)
    t = trust or {}
    for name, b in (t.get("by_gap_band") or {}).items():
        st = b.get("state")
        actionable = st in ("DISAGREEMENT_HAS_SIGNAL", "DISAGREEMENT_IS_OVERCONFIDENCE")
        out.append({"id": "MONEYLINE_GAP_TRUST", "name": f"model-market disagreement: {name}", "state": ACTIONABLE if actionable else ACCUMULATING,
                    "evidence": {"games": b.get("games"), "games_needed": b.get("games_needed", 100), "state": st, "z_vs_market": b.get("z_wins_vs_market_expectation"), "z_vs_hockey": b.get("z_wins_vs_hockey_expectation")},
                    "consumed_by": CONSUMERS["MONEYLINE_GAP_TRUST"], "can_change_decisions_automatically": actionable, "owner_decision_required": False,
                    "on_threshold": "100 independent games in the band and |z| >= 2 on wins against the market's (signal) or the hockey model's (overconfidence) expectation"})
    out.append(_acc("a leg reused on two tickets against a leg used once", "REUSED_LEG_PERFORMANCE", _cell(m, "legs", "reused_vs_not", "used on 2+ tickets"),
                    "hit rate of reused legs against their estimates (do they fail more than their estimates say?)", "a PROPOSAL to change the reuse cap (a rule change: the owner decides)"))
    out.append(_acc("the weakest leg of a ticket against the others", "WEAKEST_LEG_PERFORMANCE", _cell(m, "tickets", "weakest_leg_vs_other_legs", "weakest leg"),
                    "does the weakest-leg rule pick the legs that fail?", "a PROPOSAL to change the weakest-leg rule (the owner decides)"))
    out.append(_acc("GOOD tickets against their estimates", "GOOD_TICKET_PERFORMANCE", _cell(m, "tickets", "good_vs_questionable", "GOOD"), "do GOOD tickets hit as often as their probability says?",
                    "a PROPOSAL to change the GOOD / QUESTIONABLE rule (the owner decides)"))
    out.append(_acc("QUESTIONABLE tickets against their estimates", "QUESTIONABLE_TICKET_PERFORMANCE", _cell(m, "tickets", "good_vs_questionable", "QUESTIONABLE"), "do QUESTIONABLE tickets underperform their estimates?",
                    "a PROPOSAL on QUESTIONABLE eligibility (the owner decides)"))
    fl = m.get("flagship") or {}
    out.append({"id": "FLAGSHIP_PERFORMANCE", "name": "the flagship parlay's record", "state": ACCUMULATING, "evidence": {"settled": fl.get("settled"), "hit": fl.get("hit")},
                "threshold": {"n_required": MIN_N, "met": bool((fl.get("settled") or 0) >= MIN_N)}, "on_threshold": "a PROPOSAL on flagship selection (the owner decides)", "can_change_decisions_automatically": False,
                "owner_decision_required": True})
    out.append(_acc("legs graded GOOD by the parlay-leg rubric", "LEG_RUBRIC_VALIDITY", _cell(m, "legs", "rubric", "GOOD_DECISION"), "do GOOD legs hit more than QUESTIONABLE ones?", "a PROPOSAL to refine the rubric (the owner decides)"))
    causes = _cell(m, "postmortem", "cause_counts")
    out.append({"id": "REPEATED_KILLER_LEG", "name": "a leg that lost on several tickets the same day", "state": REPORT_ONLY,
                "reason": "one outcome shown through reuse, not evidence about the player; whether reuse matters is measured by REUSED_LEG_PERFORMANCE", "can_change_decisions_automatically": False, "owner_decision_required": False})
    out.append({"id": "SAME_GAME_TICKET_RESULTS", "name": "same-game tickets' hit rate", "state": ACCUMULATING, "evidence": {"source": "by_date.same_game_tickets"},
                "threshold": {"n_required": MIN_N}, "on_threshold": "compared with the measured dependence lifts (docs/validation/dependence_lift.json); a disagreement is a PROPOSAL to re-measure", "can_change_decisions_automatically": False,
                "owner_decision_required": True})
    for c in ("NORMAL_VARIANCE", "OUTCOME_WITHIN_ESTIMATE"):
        out.append({"id": "LOSS_" + c, "name": f"{c.replace('_', ' ').lower()} losses", "state": REPORT_ONLY, "evidence": {"n": causes.get(c, 0)},
                    "reason": "the loss was within what the estimate allowed: by definition no process change", "can_change_decisions_automatically": False, "owner_decision_required": False})
    out.append({"id": "LOSS_MODEL_CALIBRATION", "name": "losses classified MODEL_CALIBRATION", "state": ACCUMULATING, "evidence": {"n": causes.get("MODEL_CALIBRATION", 0)},
                "threshold": {"n_required": MIN_N}, "on_threshold": "enters decisions only through the calibration-band rule (CALIBRATION_BAND_OVERCONFIDENCE)", "can_change_decisions_automatically": False, "owner_decision_required": False})
    out.append({"id": "LOSS_UNKNOWN", "name": "losses with no supported cause", "state": REPORT_ONLY, "evidence": {"n": causes.get("UNKNOWN", 0), "rate": _cell(m, "postmortem").get("unknown_rate")},
                "reason": "an honest 'not explained': investigated, never turned into a penalty", "can_change_decisions_automatically": False, "owner_decision_required": False})
    return out


def summary(states: list[dict]) -> dict:
    return {s: sum(1 for x in states if x["state"] == s) for s in (ACTIONABLE, ACCUMULATING, REPORT_ONLY)}
