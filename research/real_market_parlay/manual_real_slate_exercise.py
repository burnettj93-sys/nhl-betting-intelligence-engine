"""
Manual Real-Slate Parlay Exercise (Real-Slate Parlay Certification block,
2026-09-29). A CERTIFICATION/EXERCISE runner, never a scheduled job -- no
cron, no launchd, no automatic daily invocation. Answers exactly one
question: "if we ran the merged parlay engine against today's REAL current
DraftKings MONEYLINE + PLAYER_SOG_ALTERNATE data right now, what eligible
legs and 3-4 leg paper-parlay candidates would it actually produce?"

Sources REAL data only:
  - MONEYLINE: the real, already-synced nhl.db (via
    research/real_market_parlay/real_slate_adapter.py::moneyline_candidate_legs(),
    which itself calls the real, unmodified T-35 decision engine).
  - PLAYER_SOG_ALTERNATE: real, already-retained archive files under
    operational/odds_archive/ (via operational/real_prop_orchestrator.py's
    own _recent_archive_payloads(), reused verbatim -- never a new
    sportsbook API request).

NO DEMO / SYNTHETIC FALLBACK: if either source has no real, fresh,
eligible data, this reports zero candidates -- never a substitute.

Any qualifying parlay found is recorded ONLY into an isolated, temp-file
paper_bankroll database (never operational/paper_bankroll.db) -- this
exercise must never contaminate real paper-performance history.

Run manually: `python3 -m research.real_market_parlay.manual_real_slate_exercise`
"""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from collections import Counter
from pathlib import Path

import db
from operational import paper_bankroll
from operational.real_prop_orchestrator import _recent_archive_payloads
from research.live_sog_pricing import market_parser
from research.real_market_parlay import engine as rmp
from research.real_market_parlay import real_slate_adapter as adapter


def _reason_bucket(reason: str) -> str:
    """Collapses a detailed reason string (which may carry real numeric
    diagnostics, e.g. 'STALE_PRICE (age=402.3min, allowed<=30.0min)') down
    to its category for the summary counts Step 10 asks for."""
    return reason.split("(")[0].split(":")[0].strip()


def run(now: dt.datetime | None = None, archive_max_age_hours: float = 24.0) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    conn = db.get_conn()
    try:
        moneyline_legs, moneyline_excluded = adapter.moneyline_candidate_legs(conn, now=now)
        sog_payloads = _recent_archive_payloads(market_parser.ALTERNATE_MARKET_KEY, max_age_hours=archive_max_age_hours,
                                                 now=now)
        sog_legs, sog_excluded = adapter.sog_alternate_candidate_legs(conn, sog_payloads, now=now)
    finally:
        conn.close()

    all_legs = moneyline_legs + sog_legs
    all_excluded = moneyline_excluded + sog_excluded
    parlay_result = rmp.build_real_market_parlay(all_legs)

    # Near-miss diagnostics (Step 16): every 3-leg cross-game combination
    # that failed ONLY the quality gates (not the same-game rule), ranked by
    # joint probability, reported but never bet.
    near_misses = []
    from itertools import combinations
    for group in combinations(all_legs, 3):
        combo = rmp._evaluate_combo(list(group))  # noqa: SLF001 -- diagnostic-only, read-only
        if combo is None:
            continue
        if not rmp._passes_quality_gates(combo):  # noqa: SLF001
            near_misses.append(combo)
    near_misses.sort(key=lambda c: c.joint_probability, reverse=True)

    result = {
        "run_at_utc": now.isoformat(),
        "real_moneyline_candidate_count": len(moneyline_legs) + sum(
            1 for e in moneyline_excluded if e["market_family"] == "MONEYLINE"),
        "real_sog_candidate_count": len(sog_legs) + sum(
            1 for e in sog_excluded if e["market_family"] == "PLAYER_SOG_ALTERNATE"),
        "eligible_leg_count": len(all_legs),
        "eligible_legs": [
            {"game_id": l.game_id, "market_family": l.market_family, "participant": l.participant_name,
             "threshold": l.threshold, "price": l.american_price,
             "conservative_probability": round(l.conservative_probability, 4)}
            for l in all_legs
        ],
        "excluded_count": len(all_excluded),
        "excluded_by_reason": dict(Counter(_reason_bucket(e["reason"]) for e in all_excluded)),
        "excluded_detail": all_excluded,
        "parlay_result": parlay_result["status"],
        "parlay_reason": parlay_result.get("reason"),
        "qualifying_combo": None,
        "near_misses": [
            {"legs": [f"{l.participant_name}:{l.market_family}:{l.threshold}" for l in c.legs],
             "joint_probability": round(c.joint_probability, 4), "combo_edge": round(c.combo_edge, 4),
             "reason_rejected": "below 70% floor" if c.joint_probability < rmp.MIN_JOINT_PROBABILITY
                                 else "combo edge not positive"}
            for c in near_misses[:5]
        ],
        "paper_bet_created_in_test_db": False,
    }

    if parlay_result["status"] == "QUALIFIED":
        combo = parlay_result["combo"]
        result["qualifying_combo"] = {
            "recommended_legs": parlay_result["recommended_legs"],
            "legs": [{"participant": l.participant_name, "market_family": l.market_family,
                      "threshold": l.threshold, "offered_leg_price": l.american_price} for l in combo.legs],
            "joint_probability": round(combo.joint_probability, 4),
            "fair_combo_price": round(combo.fair_combo_price, 1),
            "estimated_combo_price": round(combo.estimated_combo_price, 1),
            "offered_parlay_price": combo.offered_parlay_price,  # always None -- never fabricated
            "data_label": "REAL MARKET DATA",
        }
        # Isolated, temp-file test DB only -- never operational/paper_bankroll.db.
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        test_bankroll_conn = paper_bankroll.init_db(Path(tmp.name))
        bet_result = paper_bankroll.create_real_market_combo_paper_bet(test_bankroll_conn, parlay_result)
        result["paper_bet_created_in_test_db"] = bet_result["status"] == "INSERTED"
        result["test_db_path"] = tmp.name
        test_bankroll_conn.close()

    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
