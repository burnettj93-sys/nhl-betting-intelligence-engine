"""Daily quantitative post-mortem (Parts 54-69). Answers, every morning
after the previous day's games settle: what worked, what didn't, why,
whether it's normal variance or a systematic issue, what to investigate,
and whether there's a real software bug or a model/challenger
hypothesis worth pursuing.

Hard boundary (Part 66, enforced structurally, not just by convention):
this module never imports research.model_registry, research.player_props.
decision_policy, or any production model file as something it WRITES to
-- it only ever READS settled paper-bet outcomes (operational.
paper_bankroll) and, at most, PROPOSES a HYPOTHESIS-status entry to
operational.challenger_registry, which itself never auto-promotes
anything (see that module's own docstring). No code path here can change
a coefficient, a validated threshold, an overlay, or decision_policy.

Honest scope note: several taxonomy categories below (TOI_PROJECTION_ERROR,
TEAM_SHOT_ENVIRONMENT_ERROR, GOALIE_WORKLOAD_ERROR, IDENTITY_LINEUP_ERROR,
DATA_PIPELINE_ERROR) need real per-game diagnostic signals (actual vs.
projected TOI, actual vs. projected Team SOG, identity-match status, an
ingestion-error flag) that don't exist in the paper_bets table itself --
classify_failure() accepts an optional `context` dict a caller supplies
once that data exists (e.g. once real 2026-27 games have been played and
joined against prediction snapshots). Until then, this correctly and
honestly classifies most misses as NORMAL_VARIANCE or UNKNOWN rather than
fabricating a specific root cause it can't actually see.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from operational import challenger_registry as cr
from operational import ingestion_health
from operational import paper_bankroll as pb

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports" / "daily"

# ---------------------------------------------------------------------
# Part 58: failure taxonomy -- exactly the 13 categories requested.
# ---------------------------------------------------------------------
NORMAL_VARIANCE = "NORMAL_VARIANCE"
MODEL_CALIBRATION = "MODEL_CALIBRATION"
TOI_PROJECTION_ERROR = "TOI_PROJECTION_ERROR"
ROLE_CHANGE_MISSED = "ROLE_CHANGE_MISSED"
STARTER_ERROR = "STARTER_ERROR"
TEAM_SHOT_ENVIRONMENT_ERROR = "TEAM_SHOT_ENVIRONMENT_ERROR"
GOALIE_WORKLOAD_ERROR = "GOALIE_WORKLOAD_ERROR"
MARKET_MOVED = "MARKET_MOVED"
STALE_DATA = "STALE_DATA"
IDENTITY_LINEUP_ERROR = "IDENTITY_LINEUP_ERROR"
DATA_PIPELINE_ERROR = "DATA_PIPELINE_ERROR"
DEPENDENCE_ERROR = "DEPENDENCE_ERROR"
UNKNOWN = "UNKNOWN"

FAILURE_TAXONOMY = (
    NORMAL_VARIANCE, MODEL_CALIBRATION, TOI_PROJECTION_ERROR, ROLE_CHANGE_MISSED, STARTER_ERROR,
    TEAM_SHOT_ENVIRONMENT_ERROR, GOALIE_WORKLOAD_ERROR, MARKET_MOVED, STALE_DATA,
    IDENTITY_LINEUP_ERROR, DATA_PIPELINE_ERROR, DEPENDENCE_ERROR, UNKNOWN,
)

# Part 61: recommended-action classes.
NO_ACTION = "NO_ACTION"
WATCH = "WATCH"
INVESTIGATE = "INVESTIGATE"
BUG_FIX = "BUG_FIX"
CHALLENGER_IDEA = "CHALLENGER_IDEA"
HALT_MARKET = "HALT_MARKET"
RECOMMENDED_ACTIONS = (NO_ACTION, WATCH, INVESTIGATE, BUG_FIX, CHALLENGER_IDEA, HALT_MARKET)

# Part 63: a smaller pattern than the full challenger-evidence bar can
# still warrant a human look -- these are intentionally lower than
# challenger_registry.MIN_REPEATED_OCCURRENCES/MIN_UNIQUE_GAME_DATES.
INVESTIGATE_MIN_OCCURRENCES = 3
WATCH_MIN_OCCURRENCES = 2


def classify_failure(bet_row: dict, context: dict | None = None) -> str:
    """`bet_row`: a settled paper_bets row (LOSS/VOID/UNRESOLVED).
    `context`: optional real per-game diagnostic signals a caller
    supplies (see module docstring) -- every branch below only fires on
    a real, present signal, never a guess. Checks the most specific,
    most confidently-attributable causes first; UNKNOWN if nothing
    present explains it (an honest, legitimate outcome, not a fallback
    to hide behind)."""
    context = context or {}

    if bet_row.get("result_status") == "WIN":
        raise ValueError("classify_failure is for losing/void/unresolved bets only, not a WIN")

    residual = context.get("residual")
    if residual is not None and abs(residual) < context.get("normal_variance_threshold", 0.5):
        return NORMAL_VARIANCE

    if context.get("data_pipeline_error"):
        return DATA_PIPELINE_ERROR
    if context.get("identity_match_status") in ("AMBIGUOUS", "UNMATCHED"):
        return IDENTITY_LINEUP_ERROR
    if context.get("data_freshness_hours") is not None and context["data_freshness_hours"] > context.get(
            "stale_data_threshold_hours", 6.0):
        return STALE_DATA
    if context.get("market_price_moved_significantly"):
        return MARKET_MOVED
    if bool(bet_row.get("is_combo")) and context.get("dependence_assumption_violated"):
        return DEPENDENCE_ERROR
    if context.get("role_transition"):
        return ROLE_CHANGE_MISSED
    if context.get("starter_uncertain_at_lock"):
        return STARTER_ERROR
    if context.get("team_sog_actual_vs_projected_gap") is not None and _is_saves_market(bet_row):
        return GOALIE_WORKLOAD_ERROR
    if context.get("toi_actual_vs_projected_gap") is not None:
        return TOI_PROJECTION_ERROR
    if context.get("team_shot_environment_gap") is not None:
        return TEAM_SHOT_ENVIRONMENT_ERROR
    if bet_row.get("confidence") == "HIGH" and residual is not None and abs(residual) >= 0.5:
        return MODEL_CALIBRATION
    return UNKNOWN


def _is_saves_market(bet_row: dict) -> bool:
    family = (bet_row.get("market_family") or "").upper()
    return "SAVE" in family


def summarize_failures(classified: list[str]) -> dict:
    counts = {t: 0 for t in FAILURE_TAXONOMY}
    for c in classified:
        counts[c] = counts.get(c, 0) + 1
    return counts


def recommended_action_for_pattern(category: str, *, occurrences: int, unique_game_dates: int,
                                    explanation: str = "") -> str:
    """Part 61-63: the real evidence gate. A single bad game (occurrences
    below WATCH_MIN_OCCURRENCES) is NO_ACTION -- normal variance is
    expected, not investigated. CHALLENGER_IDEA requires the SAME real
    bar operational.challenger_registry.validate_evidence() itself
    enforces (never a separately-invented, weaker threshold that could
    let a short streak masquerade as broad evidence)."""
    if category == NORMAL_VARIANCE:
        return NO_ACTION
    if occurrences < WATCH_MIN_OCCURRENCES:
        return NO_ACTION
    try:
        cr.validate_evidence({"occurrences": occurrences, "unique_game_dates": unique_game_dates,
                               "explanation": explanation})
        challenger_evidence_clears = True
    except cr.ChallengerValidationError:
        challenger_evidence_clears = False

    if category == DATA_PIPELINE_ERROR:
        return BUG_FIX
    if category in (IDENTITY_LINEUP_ERROR, MARKET_MOVED, STALE_DATA) and occurrences >= INVESTIGATE_MIN_OCCURRENCES:
        return BUG_FIX
    if challenger_evidence_clears and category in (
            MODEL_CALIBRATION, TOI_PROJECTION_ERROR, TEAM_SHOT_ENVIRONMENT_ERROR,
            GOALIE_WORKLOAD_ERROR, DEPENDENCE_ERROR):
        return CHALLENGER_IDEA
    if occurrences >= INVESTIGATE_MIN_OCCURRENCES:
        return INVESTIGATE
    return WATCH


def generate_bug_fix_candidate(*, symptom: str, evidence: str, likely_root_cause: str,
                                files_involved: list[str], reproduction: str, suggested_fix_scope: str) -> dict:
    """Part 64. This is DATA describing a proposal -- it never edits a
    file itself. A human (or a separate, explicitly-invoked fix task)
    acts on it."""
    return {
        "type": "BUG_FIX_CANDIDATE", "symptom": symptom, "evidence": evidence,
        "likely_root_cause": likely_root_cause, "files_involved": files_involved,
        "reproduction": reproduction, "suggested_fix_scope": suggested_fix_scope,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def generate_challenger_idea_draft(*, target_model: str, hypothesis: str, affected_market: str,
                                    occurrences: int, unique_game_dates: int, mean_residual: float,
                                    explanation: str, required_minimum_sample: str,
                                    proposed_evaluation: str, promotion_criteria: str) -> dict:
    """Part 65. A DRAFT only -- does not touch challenger_registry.json.
    See submit_challenger_idea() to actually propose it (a separate,
    explicit step, never auto-called from run_daily_postmortem)."""
    return {
        "type": "CHALLENGER_IDEA_DRAFT", "target_model": target_model, "hypothesis": hypothesis,
        "affected_market": affected_market,
        "evidence": {"occurrences": occurrences, "unique_game_dates": unique_game_dates,
                     "mean_residual": mean_residual, "explanation": explanation},
        "required_minimum_sample": required_minimum_sample, "proposed_evaluation": proposed_evaluation,
        "promotion_criteria": promotion_criteria,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def submit_challenger_idea(draft: dict, *, training_window: str, validation_plan: str,
                            registry_path: Path = cr.REGISTRY_PATH) -> dict:
    """Part 62/63: the ONLY path from a draft to an actual registry
    entry -- explicit, separate, never automatic. Still gated by the
    real validate_evidence() bar inside propose_challenger() itself, so
    a draft whose evidence doesn't actually clear the bar is refused
    here too, not just at generation time. `registry_path` is forwarded
    explicitly (never relying on mock.patch of the module-level default,
    a bound-at-import-time value this codebase has already been bitten
    by more than once elsewhere)."""
    return cr.propose_challenger(
        target_model=draft["target_model"], hypothesis=draft["hypothesis"],
        evidence=draft["evidence"], training_window=training_window, validation_plan=validation_plan,
        registry_path=registry_path)


# ---------------------------------------------------------------------
# Part 56/57: daily scoreboard + parlay health, built entirely from real
# settled paper_bets rows across the three tracks.
# ---------------------------------------------------------------------

def build_daily_scoreboard(conn) -> dict:
    """Part 56: straight/parlay/SOG/Saves/other-props records, daily
    P&L, bankroll status, ROI, CLV -- reusing paper_bankroll.py's own
    real, already-tested aggregation functions rather than
    re-implementing bankroll math here."""
    tracks = {}
    for track in pb.TRACKS:
        tracks[track] = {
            "bankroll_summary": pb.bankroll_summary(conn, track),
            "windowed_performance": pb.windowed_performance(conn, track),
            "breakdowns": pb.performance_breakdowns(conn, track),
        }
    return {"generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "tracks": tracks}


def build_parlay_health(conn) -> dict:
    """Part 57/69: GAME_PARLAY_PAPER-specific health -- avg modeled
    joint P vs actual hit rate, expected vs actual hit count, calibration
    gap, avg market-implied P, avg model edge, 3-leg vs 4-leg split.
    Every field is honestly WAITING_FOR_SETTLED_DATA until real
    settlements exist (Part 60: result quality and model quality are
    tracked separately, never inferred from an empty sample)."""
    settled = [r for r in pb.query_paper_bets(conn, track="GAME_PARLAY_PAPER") if r["result_status"] != "PENDING"]
    if not settled:
        return {
            "status": "WAITING_FOR_SETTLED_DATA", "settled_parlays": 0,
            "avg_modeled_joint_probability": None, "actual_hit_rate": None,
            "expected_hit_count": None, "actual_hit_count": None, "calibration_gap": None,
            "avg_market_implied_probability": None, "avg_model_edge": None,
            "by_leg_count": {},
        }

    n = len(settled)
    wins = sum(1 for r in settled if r["result_status"] == "WIN")
    avg_joint_p = sum(r["conservative_probability"] or 0.0 for r in settled) / n
    actual_hit_rate = wins / n
    expected_hit_count = sum(r["conservative_probability"] or 0.0 for r in settled)

    import json as _json
    by_leg_count: dict[int, dict[str, int]] = {}
    for r in settled:
        legs = _json.loads(r["legs_json"]) if r["legs_json"] else []
        n_legs = len(legs) or 3
        bucket = by_leg_count.setdefault(n_legs, {"bets": 0, "wins": 0})
        bucket["bets"] += 1
        if r["result_status"] == "WIN":
            bucket["wins"] += 1

    return {
        "status": "OK", "settled_parlays": n,
        "avg_modeled_joint_probability": avg_joint_p, "actual_hit_rate": actual_hit_rate,
        "expected_hit_count": expected_hit_count, "actual_hit_count": wins,
        "calibration_gap": actual_hit_rate - avg_joint_p,
        "avg_market_implied_probability": None,  # requires the estimated-price implied prob, not stored raw
        "avg_model_edge": sum(r["edge"] or 0.0 for r in settled) / n,
        "by_leg_count": by_leg_count,
    }


def run_daily_postmortem(conn, *, classified_failures: list[dict] | None = None) -> dict:
    """Part 54/55: the main entry point. `classified_failures` is a list
    of {"category": str, "occurrences": int, "unique_game_dates": int,
    "explanation": str} summaries the caller has already aggregated
    (e.g. from a day/week of classify_failure() calls) -- this function
    itself doesn't re-derive TOI/role/etc. context (that lives upstream,
    per-game, where it's actually available); it turns already-classified
    patterns into the report's recommended actions.

    Never writes to any production model file, decision_policy, or
    research/model_registry.py -- only reads paper_bankroll and, via
    recommended_action_for_pattern(), consults (never writes)
    challenger_registry's real evidence gate."""
    classified_failures = classified_failures or []
    scoreboard = build_daily_scoreboard(conn)
    parlay_health = build_parlay_health(conn)

    issues = []
    for f in classified_failures:
        action = recommended_action_for_pattern(
            f["category"], occurrences=f["occurrences"], unique_game_dates=f["unique_game_dates"],
            explanation=f.get("explanation", ""))
        issues.append({**f, "recommended_action": action})

    any_bets_settled = any(
        t["bankroll_summary"]["bets"] > t["bankroll_summary"]["pending"]
        for t in scoreboard["tracks"].values()
    )

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "what_worked": _summarize_what_worked(scoreboard) if any_bets_settled else "WAITING_FOR_SETTLED_DATA",
        "what_didnt": _summarize_what_didnt(issues) if issues else (
            "WAITING_FOR_SETTLED_DATA" if not any_bets_settled else "nothing flagged"),
        "why": [f"{i['category']}: {i.get('explanation', '')}" for i in issues] or ["WAITING_FOR_SETTLED_DATA"],
        # Zero settled results is NO_DATA, never a "normal variance" conclusion about a model with no sample.
        "normal_variance_vs_systematic": (
            "NO_DATA (no settled recommendations yet -- no variance or model conclusion is drawn)"
            if not any_bets_settled and not issues else _variance_vs_systematic(issues)),
        "investigate": [i for i in issues if i["recommended_action"] in (INVESTIGATE, BUG_FIX, CHALLENGER_IDEA)],
        "software_bug_candidates": [i for i in issues if i["recommended_action"] == BUG_FIX],
        "challenger_ideas": [i for i in issues if i["recommended_action"] == CHALLENGER_IDEA],
        "scoreboard": scoreboard,
        "parlay_health": parlay_health,
        "failure_summary": summarize_failures([i["category"] for i in issues]),
    }
    return report


def _summarize_what_worked(scoreboard: dict) -> str:
    parts = []
    for track, data in scoreboard["tracks"].items():
        s = data["bankroll_summary"]
        if s["bets"] - s["pending"] > 0:
            parts.append(f"{track}: {s['wins']}W-{s['losses']}L, P&L {s['profit_loss']:+.2f}")
    return "; ".join(parts) if parts else "no settled bets yet"


def _summarize_what_didnt(issues: list[dict]) -> str:
    return "; ".join(f"{i['category']} x{i['occurrences']}" for i in issues) if issues else "nothing flagged"


def _variance_vs_systematic(issues: list[dict]) -> str:
    systematic = [i for i in issues if i["category"] != NORMAL_VARIANCE and i["recommended_action"] != NO_ACTION]
    if not systematic:
        return "NORMAL_VARIANCE (no pattern met the systematic-issue bar)"
    return "SYSTEMATIC: " + ", ".join(i["category"] for i in systematic)


def write_report_markdown(report: dict, *, out_dir: Path = REPORTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = out_dir / f"postmortem_{date_str}.md"
    lines = [
        f"# Daily Post-Mortem -- {date_str}", "",
        "## What worked", report["what_worked"], "",
        "## What didn't", str(report["what_didnt"]), "",
        "## Why", *[f"- {w}" for w in report["why"]], "",
        "## Normal variance vs. systematic", report["normal_variance_vs_systematic"], "",
        "## Recommended actions", *[f"- {i['category']}: {i['recommended_action']}" for i in report["investigate"]],
    ]
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    """CLI entry point (Production Readiness Audit, Phase 6): the Morning
    Review dashboard page deliberately never writes a report file (a page
    view must be a pure read, per that page's own docstring/test) -- this
    is the intentional counterpart that DOES persist one, for a scheduled
    job to run each morning after settlement completes. Never raises past
    the caller for a normal "nothing settled yet" result -- that's the
    honest, expected state until real games actually settle (see
    run_daily_postmortem()'s own WAITING_FOR_SETTLED_DATA handling)."""
    from operational import deployment_mode as dm
    if not dm.require_active_scheduler_or_exit("daily_postmortem"):
        return

    # Real Recommendation Pipeline block (2026-09-24), Part 24: never let
    # a scheduled postmortem report a misleadingly "complete" day if the
    # 07:15 settlement run hasn't actually confirmed success yet -- DEFER
    # (never a retry loop) and let the next scheduled postmortem run pick
    # it up once settlement recovers.
    settlement_ready, settlement_reason = ingestion_health.dependency_ready(
        "settlement", max_age_hours=30.0)
    if not settlement_ready:
        print(f"DEFERRED: upstream settlement not ready ({settlement_reason}) -- "
              f"skipping this postmortem run rather than reporting an incomplete day as final.")
        ingestion_health.record_run("postmortem", {"status": "DEFERRED", "reason": settlement_reason})
        return

    conn = pb.init_db()
    report = run_daily_postmortem(conn)
    path = write_report_markdown(report)
    scoreboard = report.get("scoreboard", {}).get("tracks", {})
    print(f"Daily post-mortem written to {path}")
    for track, data in scoreboard.items():
        s = data["bankroll_summary"]
        print(f"  {track}: {s['bets']} bet(s), {s['wins']}W-{s['losses']}L-{s['voids']}V, "
              f"bankroll ${s['current_bankroll']:,.2f}")
    if report["investigate"]:
        print(f"  {len(report['investigate'])} pattern(s) flagged for review.")
    if report["software_bug_candidates"]:
        print(f"  {len(report['software_bug_candidates'])} bug-fix candidate(s) generated -- "
              f"review before acting, nothing is auto-applied.")
    if report["challenger_ideas"]:
        print(f"  {len(report['challenger_ideas'])} challenger idea(s) generated -- "
              f"review before acting, nothing is auto-promoted.")
    # P0.1 (2026-09-24 hardening block): this job never raises for a
    # normal "nothing settled yet" run (see this function's own
    # docstring) -- reaching this line at all is SUCCESS for health-
    # tracking purposes; a real failure would have raised before here.
    ingestion_health.record_run("postmortem", {"status": "SUCCESS", "report_path": str(path)})
    # Cloud live-data sprint (2026-09-25): the Morning Review changed -> publish
    # (opt-in, downstream, never raises).
    from operational import cloud_publish_hook
    print("cloud snapshot:", cloud_publish_hook.publish_after("daily_postmortem"))


if __name__ == "__main__":
    main()
