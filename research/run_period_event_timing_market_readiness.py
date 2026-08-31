"""
Part 54/56/57/78: maps this sprint's real findings back to the 142
canonical markets in research/player_props/market_registry.py, grouped
by the underlying_process tags that already exist there
(PERIOD_EVENT_TIMING, SPECIAL_TEAMS_STATE, PENALTY_PROCESS,
EMPTY_NET_STATE, GAME_SCORE_STATE) -- never a second, hand-maintained
market list.

This does NOT edit market_registry.py itself (Part 36/78: "do not change
production registry status without validation" / "do not alter
operational statuses of existing validated models") -- it is a
read-only classification pass producing a NEW, separate results file.

Per Part 55 ("data availability is not validation"): building the
manpower/penalty/special-teams DATA FOUNDATION this sprint does not by
itself VALIDATE any individual betting market. Zero markets are
promoted to VALIDATED here. The explicit, named analyses this sprint
actually ran (PP opportunity count fit, PP goal correlation, empty-net
pull timing, first-goal timing, first-team-to-score baseline, penalty
rate) are each given their own specific, evidence-backed status; every
other market in an affected process family is marked DERIVABLE (the
infrastructure to compute a real, non-fabricated baseline for it now
exists) rather than left NOT_SUPPORTED, but explicitly NOT VALIDATED.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.player_props.market_registry import CANONICAL_MARKETS

RESULTS_PATH = REPO_ROOT / "research" / "period_event_timing_market_readiness_results.json"

PROCESS_FAMILIES = (
    "PERIOD_EVENT_TIMING", "SPECIAL_TEAMS_STATE", "PENALTY_PROCESS",
    "EMPTY_NET_STATE", "GAME_SCORE_STATE",
)


def build_readiness_matrix() -> dict:
    by_family = {}
    for family in PROCESS_FAMILIES:
        markets = [m for m in CANONICAL_MARKETS if family in m.underlying_process]
        by_family[family] = {
            "total_markets": len(markets),
            "market_ids": sorted(m.market_id for m in markets),
            "existing_model_status_counts": _count_by(markets, "model_status"),
        }

    return {
        "process_family_market_counts": {k: v["total_markets"] for k, v in by_family.items()},
        "process_family_detail": by_family,
        "named_analyses_this_sprint": {
            "PP_OPPORTUNITY_COUNT_MODEL": {
                "readiness": "CANDIDATE_BUILT",
                "evidence": "Poisson variance/mean ratio 0.84 across 10,496 real team-game rows "
                            "(research/special_teams_corpus_results.json) -- Poisson is an adequate "
                            "count family; no walk-forward OOS validation run this sprint, so "
                            "CANDIDATE_BUILT, not VALIDATED.",
            },
            "PP_GOAL_CONVERSION": {
                "readiness": "DATA_READY",
                "evidence": "League PP conversion rate 19.9% (6,356 PP goals / 31,901 PP opportunities), "
                            "matches known real-world NHL league averages closely -- a real, usable "
                            "baseline rate exists; no per-team predictive model built/validated this sprint.",
            },
            "EMPTY_NET_GOALIE_PULL_TIMING": {
                "readiness": "DATA_READY",
                "evidence": "First-pull timing reconstructed and cleanly separated from delayed-penalty "
                            "pulls (2,870 genuine trailing pulls vs 2,689 delayed-penalty pulls across "
                            "the full corpus); real, monotonic score-differential-vs-time-remaining "
                            "pattern confirmed. No formal P(pull | state) regression built this sprint.",
            },
            "FIRST_GOAL_TIMING": {
                "readiness": "DATA_READY",
                "evidence": "Real survival probabilities computed (scoreless-through-5/10/15min, "
                            "end-of-P1) across all 5,248 games -- see "
                            "research/period_event_timing_core_results.json.",
            },
            "FIRST_TEAM_TO_SCORE": {
                "readiness": "CANDIDATE_BUILT",
                "evidence": "Real home-first-goal baseline rate 52.2% (2,737/5,242 decided games) -- a "
                            "usable naive candidate exists; comparing it against the existing NHL Elo "
                            "win-probability model or a purpose-built regression was NOT done this "
                            "sprint (deferred -- see report Section AU).",
            },
            "PP_POINTS": {
                "readiness": "DATA_READY",
                "evidence": "PP role only PARTIALLY predictable (rolling-PP-TOI vs next-game-PP-TOI "
                            "r~0.54-0.58 across 3/5/10/20-game windows, R^2~0.29-0.34) -- real signal, "
                            "not strong enough to clear Part 32's gate for building a candidate "
                            "probability model this sprint.",
            },
            "SH_SCORING": {
                "readiness": "INSUFFICIENT_DATA",
                "evidence": "773 shorthanded goals across 5,248 games (4 seasons) -- ~0.074 per team "
                            "per game league-wide; a player-level SH-scoring model has essentially no "
                            "real per-player support.",
            },
            "TEAM_GOALS_BY_PERIOD_REVISIT": {
                "readiness": "NOT_REVISITED_THIS_SPRINT",
                "evidence": "Part 17 only authorizes revisiting this if a genuinely new event-timing "
                            "architecture is actually built and compared against the prior rejected "
                            "candidate. That comparison was not run this sprint (time-boxed out -- see "
                            "report Section AU) -- status remains ATTEMPTED_NOT_VALIDATED, unchanged, "
                            "per Part 17's own explicit instruction not to simply rerun it.",
            },
        },
        "validated_market_count_this_sprint": 0,
        "note": "Zero markets promoted to VALIDATED status this sprint by design (Part 55: data "
                "availability is not validation). Every market in an affected process family not "
                "named above defaults to DERIVABLE: the manpower/penalty/period-timing data "
                "foundation needed to compute a real, non-fabricated baseline for it now exists, "
                "but no market-specific model was individually built or validated.",
    }


def _count_by(markets, attr) -> dict:
    out = {}
    for m in markets:
        v = getattr(m, attr)
        out[v] = out.get(v, 0) + 1
    return out


if __name__ == "__main__":
    result = build_readiness_matrix()
    with open(RESULTS_PATH, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in result.items() if k != "process_family_detail"},
                      indent=2, sort_keys=True))
