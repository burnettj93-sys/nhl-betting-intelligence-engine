"""
2026-27 Continuous Learning + Daily Model Audit framework (owner
directive, 2026-08-30): the callable daily job.

PREDICT -> SNAPSHOT -> OBSERVE -> SETTLE all already exist (prior
sprints). This module is the next stage only: SCORE MODEL -> DIAGNOSE
ERRORS/DRIFT -> compare against SHADOW -> ACCUMULATE EVIDENCE -> surface
an EXPLICIT PROMOTION GATE recommendation for a human. It NEVER writes
to a production model file, decision_policy, or the prospective ledger's
prediction-time columns -- it is a pure READER of already-settled,
already-immutable rows, plus a WRITER of its own separate, disposable
daily-report artifacts (reports/daily/*.md) and the challenger registry
(a human-reviewed proposal list, not a production switch).

Usage (once real 2026-27 settled data exists):
    python3 -m operational.daily_model_review

Run order dependency (Part 1/52): this module assumes NHL result
ingestion and settlement have ALREADY completed for the window being
reviewed -- see operational.engine_status_evaluator.check_run_order(),
called first, every run.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path

from operational import challenger_registry as cr
from operational import engine_status_evaluator as ese
from operational import error_taxonomy as et
from operational import model_scorecard as ms
from operational import prospective_ledger as pl
from operational import rejected_research_check as rrc

REPO_ROOT = Path(__file__).resolve().parent.parent
DAILY_REPORTS_DIR = REPO_ROOT / "reports" / "daily"

# Part 4/5: the known base-vs-shadow pairs today. SOG's overlay is
# deliberately restricted to 2+/3+ -- the overlay's OWN historical
# validation covered 1+/2+/3+, but the base SOG model is only
# operationally validated at 2+/3+/4+/5+ (Preseason Operational
# Readiness Closure sprint). Comparing at 1+ would let the overlay
# smuggle in an operationally-unvalidated base threshold; comparing
# through the FULL 2-5 range keeps the comparison inside what the base
# model itself is trusted for.
SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS = ("2+", "3+")

SHADOW_PAIRS = {
    "SOG_PP_ROLE_OVERLAY": {
        "market_id_prefix": "PLAYER_SOG", "base_field": "raw_probability",
        "shadow_field": "sog_shadow_raw_probability",
        "allowed_thresholds": SOG_PP_ROLE_VALID_COMPARISON_THRESHOLDS,
    },
    "GOALS_CONTEXT_OVERLAY": {
        "market_id_prefix": "PLAYER_GOALS", "base_field": "raw_policy_input_probability",
        "shadow_field": "shadow_context_policy_probability", "allowed_thresholds": None,
    },
    "POINTS_CONTEXT_OVERLAY": {
        "market_id_prefix": "PLAYER_POINTS", "base_field": "raw_policy_input_probability",
        "shadow_field": "shadow_context_policy_probability", "allowed_thresholds": None,
    },
}


def load_settleable_rows(ledger_conn) -> list[dict]:
    """Every non-HISTORICAL_RESEARCH row -- MODEL_OBSERVATION,
    SHADOW_POLICY_OBSERVATION, and REAL_BET are all eligible for
    scoring; they are simply never AGGREGATED together for P&L
    purposes (Part 44)."""
    rows = pl.query_observations(ledger_conn)
    return [r for r in rows if r["record_type"] != "HISTORICAL_RESEARCH"]


def score_all_windows(rows: list[dict], now_utc: dt.datetime, season_start_utc: str | None = None) -> dict:
    """Part 2/3: the full scorecard for every time window, grouped by
    market_id + threshold (never pooling different thresholds of the
    same market together -- their base rates differ too much to compare
    meaningfully, matching this project's own established caution from
    PLAYER_SOG_FOUNDATION_REPORT.md Section U)."""
    by_market_threshold = defaultdict(list)
    for r in rows:
        by_market_threshold[(r.get("market_id"), r.get("threshold"))].append(r)

    out = {}
    for window in ms.TIME_WINDOWS:
        window_out = {}
        for (market_id, threshold), group_rows in by_market_threshold.items():
            windowed = ms.filter_by_window(group_rows, window, now_utc, season_start_utc)
            if not windowed:
                continue
            window_out[f"{market_id}|{threshold}"] = ms.compute_scorecard(windowed)
        out[window] = window_out
    return out


def score_shadow_pairs(rows: list[dict]) -> dict:
    """Part 4/5. Enforces the 1+ exclusion for the SOG PP-role overlay
    structurally, not just by convention -- rows at any other threshold
    are filtered out before comparison ever runs."""
    out = {}
    for name, cfg in SHADOW_PAIRS.items():
        relevant = [r for r in rows if (r.get("market_id") or "").startswith(cfg["market_id_prefix"])]
        if cfg["allowed_thresholds"] is not None:
            relevant = [r for r in relevant if r.get("threshold") in cfg["allowed_thresholds"]]
            excluded_1plus = [r for r in rows if (r.get("market_id") or "").startswith(cfg["market_id_prefix"])
                               and r.get("threshold") == "1+"]
            if excluded_1plus:
                out.setdefault("_diagnostics", {})[name] = (
                    f"{len(excluded_1plus)} row(s) at threshold 1+ deliberately excluded -- base SOG 1+ "
                    f"is not operationally validated regardless of overlay history")
        out[name] = ms.compare_base_vs_shadow(relevant, cfg["base_field"], cfg["shadow_field"])
    return out


def large_miss_review(rows: list[dict], *, top_n: int = 5, large_residual_threshold: float = 0.5) -> dict:
    """Part 23: biggest positive/negative residuals, biggest edge
    losses, biggest market disagreements, biggest +/- CLV -- and Part
    22's per-miss error-taxonomy classification."""
    scored = ms.settled_rows(rows)
    with_residual = []
    for r in scored:
        if r.get("raw_probability") is None:
            continue
        residual = (1.0 if r["result_status"] == "WIN" else 0.0) - r["raw_probability"]
        with_residual.append((residual, r))

    largest_positive = sorted(with_residual, key=lambda t: -t[0])[:top_n]
    largest_negative = sorted(with_residual, key=lambda t: t[0])[:top_n]
    classified = [et.classify_miss(r, residual, large_residual_threshold=large_residual_threshold)
                  for residual, r in with_residual]

    with_clv = [r for r in scored if r.get("clv") is not None]
    biggest_positive_clv = sorted(with_clv, key=lambda r: -r["clv"])[:top_n]
    biggest_negative_clv = sorted(with_clv, key=lambda r: r["clv"])[:top_n]

    return {
        "largest_positive_residuals": [{"prediction_id": r["prediction_id"], "residual": res}
                                        for res, r in largest_positive],
        "largest_negative_residuals": [{"prediction_id": r["prediction_id"], "residual": res}
                                        for res, r in largest_negative],
        "biggest_positive_clv": [{"prediction_id": r["prediction_id"], "clv": r["clv"]} for r in biggest_positive_clv],
        "biggest_negative_clv": [{"prediction_id": r["prediction_id"], "clv": r["clv"]} for r in biggest_negative_clv],
        "error_taxonomy_summary": et.summarize_misses(classified),
    }


def starter_and_active_status_accuracy(rows: list[dict]) -> dict:
    """Part 16/17, derived honestly from real settlement outcomes rather
    than a schema field that doesn't exist: a GOALIE_SAVES prediction
    that resolves GOALIE_DID_NOT_PLAY is direct, real evidence the
    projected starter didn't start; a player prediction that resolves
    PLAYER_DID_NOT_DRESS is direct evidence of a false-active projection.
    'False-inactive' (projected inactive, but actually played) is
    explicitly NOT_OBSERVABLE from this ledger -- no row is ever created
    for a player the frozen model projected inactive, so there is no
    record to check against."""
    goalie_rows = [r for r in rows if (r.get("market_id") or "").startswith("GOALIE_SAVES")
                   and r.get("result_status") in ("WIN", "LOSS", "UNRESOLVED")]
    starter_misses = sum(1 for r in goalie_rows if "GOALIE_DID_NOT_PLAY" in (r.get("notes") or ""))

    player_rows = [r for r in rows if r.get("result_status") in ("WIN", "LOSS", "UNRESOLVED")
                   and (r.get("market_id") or "").startswith("PLAYER_")]
    false_active = sum(1 for r in player_rows if "PLAYER_DID_NOT_DRESS" in (r.get("notes") or ""))

    return {
        "goalie_predictions_checked": len(goalie_rows),
        "starter_projection_misses": starter_misses,
        "starter_projection_miss_rate": (starter_misses / len(goalie_rows)) if goalie_rows else None,
        "player_predictions_checked": len(player_rows),
        "false_active_count": false_active,
        "false_active_rate": (false_active / len(player_rows)) if player_rows else None,
        "false_inactive": "NOT_OBSERVABLE (no record exists for a player projected inactive)",
    }


def player_and_team_health(rows: list[dict], *, min_sample: int = 15) -> dict:
    """Part 47/48: persistent bias detection with an explicit minimum-
    sample gate -- below it, a player/team is reported LOW_SAMPLE, never
    given a bias verdict."""
    scored = [r for r in ms.settled_rows(rows) if r.get("raw_probability") is not None]
    by_player = defaultdict(list)
    by_team = defaultdict(list)
    for r in scored:
        if r.get("player_id"):
            by_player[r["player_id"]].append(r)
        if r.get("team"):
            by_team[r["team"]].append(r)

    def _summarize(groups: dict) -> dict:
        out = {}
        for key, group_rows in groups.items():
            if len(group_rows) < min_sample:
                out[key] = {"n": len(group_rows), "status": "LOW_SAMPLE"}
                continue
            residuals = [(1.0 if r["result_status"] == "WIN" else 0.0) - r["raw_probability"] for r in group_rows]
            out[key] = {"n": len(group_rows), "status": "SUFFICIENT_SAMPLE",
                        "mean_residual": statistics.fmean(residuals)}
        return out

    return {"players": _summarize(by_player), "teams": _summarize(by_team)}


def edge_decay(rows: list[dict]) -> dict:
    """Part 45: whether edge at prediction time shrinks by the time a
    real closing price exists -- requires BOTH an observation-time
    no-vig probability and a real closing price; NOT_AVAILABLE
    otherwise."""
    from pricing import odds_math
    deltas = []
    for r in rows:
        obs_no_vig = r.get("market_no_vig_probability")
        model_prob = r.get("conservative_probability")
        closing_odds = r.get("closing_odds")
        if obs_no_vig is None or model_prob is None or closing_odds is None:
            continue
        opening_edge = model_prob - obs_no_vig
        closing_edge = model_prob - odds_math.american_to_prob(closing_odds)
        deltas.append(closing_edge - opening_edge)
    if not deltas:
        return {"status": "NOT_AVAILABLE", "n": 0}
    return {"status": "OK", "n": len(deltas), "mean_edge_change": statistics.fmean(deltas)}


def model_disagreement(rows: list[dict], *, min_disagreement: float = 0.10) -> list[dict]:
    """Part 46: rows where base model, shadow overlay, and market all
    meaningfully disagree -- flagged as research examples, not acted on."""
    out = []
    for r in rows:
        base = r.get("raw_probability")
        shadow = r.get("sog_shadow_raw_probability")
        market = r.get("market_no_vig_probability")
        values = [v for v in (base, shadow, market) if v is not None]
        if len(values) < 2:
            continue
        if max(values) - min(values) >= min_disagreement:
            out.append({"prediction_id": r["prediction_id"], "base": base, "shadow": shadow,
                        "market": market, "spread": max(values) - min(values)})
    return sorted(out, key=lambda d: -d["spread"])


def season_leaderboard(rows: list[dict]) -> dict:
    """Part 43: model/market observations, Brier, log loss, calibration,
    CLV, theoretical ROI, real ROI where real bets exist -- season-to-
    date and full-sample only computed here; callers slice by window
    elsewhere."""
    by_market = defaultdict(list)
    for r in rows:
        by_market[r.get("market_id")].append(r)
    out = {}
    for market_id, group_rows in by_market.items():
        scorecard = ms.compute_scorecard(group_rows)
        clv = ms.clv_summary(group_rows)
        real_bets = [r for r in group_rows if r.get("record_type") == "REAL_BET" and r.get("profit_loss") is not None]
        out[market_id] = {
            "observations": scorecard["prediction_count"], "brier": scorecard["brier_score"],
            "log_loss": scorecard["log_loss"], "calibration_error": scorecard["calibration_error"],
            "mean_clv": clv["mean_clv"],
            "real_roi": (sum(r["profit_loss"] for r in real_bets) / sum(r["stake"] for r in real_bets)
                         if real_bets and sum(r.get("stake") or 0 for r in real_bets) > 0 else None),
            "real_bet_n": len(real_bets),
        }
    return out


def improvement_queue(large_misses: dict, drift_checks: list[dict]) -> list[dict]:
    """Part 60: a ranked list built from real signals already computed
    this run -- error-taxonomy counts and active drift flags -- never a
    static or hand-authored list."""
    queue = []
    for category, count in large_misses.get("error_taxonomy_summary", {}).items():
        if category in ("RANDOM_VARIANCE", "UNKNOWN") or count == 0:
            continue
        queue.append({"issue": category, "magnitude": count, "source": "error_taxonomy"})
    for check in drift_checks:
        if check.get("status") == ese.WATCH:
            queue.append({"issue": check.get("label", "drift"), "magnitude": 1, "source": "input_drift"})
    return sorted(queue, key=lambda q: -q["magnitude"])


DAILY_RECOMMENDATIONS = ("NO_ACTION", "CONTINUE_COLLECTING", "INVESTIGATE", "CREATE_CHALLENGER",
                          "PROMOTION_REVIEW")


def daily_recommendation(engine_status: str, queue: list[dict], promotion_candidates: list[dict]) -> str:
    """Part 61/62: most days should say NO_ACTION -- that is healthy,
    not a failure of the review."""
    if promotion_candidates:
        return "PROMOTION_REVIEW"
    if engine_status in (ese.HALT, ese.INVESTIGATE):
        return "INVESTIGATE"
    if queue:
        return "CONTINUE_COLLECTING"
    return "NO_ACTION"


def run_daily_review(ledger_conn, *, now_utc: dt.datetime | None = None,
                      season_start_utc: str | None = None,
                      inputs_ready: dict | None = None) -> dict:
    """The single entry point. Never mutates a prediction row; only
    reads the ledger and (optionally, via write_daily_report()) writes a
    disposable report file."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    inputs_ready = inputs_ready if inputs_ready is not None else {"results_ingested": True, "settlement_completed": True}

    run_order = ese.check_run_order(inputs_ready)
    if run_order["status"] == ese.HALT:
        return {"engine_status": ese.HALT, "reason": run_order["reason"], "incomplete": True}

    rows = load_settleable_rows(ledger_conn)
    scores = score_all_windows(rows, now_utc, season_start_utc)
    shadow = score_shadow_pairs(rows)
    misses = large_miss_review(rows)
    starter_active = starter_and_active_status_accuracy(rows)
    health = player_and_team_health(rows)
    decay = edge_decay(rows)
    disagreement = model_disagreement(rows)
    leaderboard = season_leaderboard(rows)
    contract_status = ese.check_contract_status()
    promo_candidates = cr.promotion_candidates()
    queue = improvement_queue(misses, [])
    recommendation = daily_recommendation(contract_status["status"], queue, promo_candidates)
    engine_status = ese.combine_status([run_order["status"], contract_status["status"]])

    return {
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_status": engine_status,
        "scores_by_window": scores,
        "shadow_vs_production": shadow,
        "large_miss_review": misses,
        "starter_and_active_status_accuracy": starter_active,
        "player_and_team_health": health,
        "edge_decay": decay,
        "model_disagreement": disagreement[:10],
        "season_leaderboard": leaderboard,
        "decision_state_breakdown": ms.decision_state_breakdown(rows),
        "edge_bucket_performance": ms.edge_bucket_performance(rows),
        "market_movement": ms.market_movement_summary(rows),
        "contract_status": contract_status,
        "promotion_candidates": promo_candidates,
        "improvement_queue": queue,
        "recommendation": recommendation,
        "rejected_research_entries_on_file": len(rrc.all_rejected_entries()),
    }


def write_daily_report(result: dict, *, report_date: str, out_dir: Path = DAILY_REPORTS_DIR) -> Path:
    """Part 36-38: reports/daily/YYYY-MM-DD_MODEL_REVIEW.md. Compact by
    design -- summary numbers, not a dump of every row."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_date}_MODEL_REVIEW.md"
    lines = [f"# Daily Model Review -- {report_date}", "",
             f"**ENGINE STATUS: {result['engine_status']}**", ""]
    if result.get("incomplete"):
        lines.append(f"**INCOMPLETE RUN**: {result['reason']}")
        path.write_text("\n".join(lines) + "\n")
        return path

    lines.append(f"**Recommendation: {result['recommendation']}**")
    lines.append("")
    lines.append("## Season leaderboard")
    for market_id, row in result["season_leaderboard"].items():
        lines.append(f"- `{market_id}`: n={row['observations']}, Brier={row['brier']}, "
                      f"log_loss={row['log_loss']}, mean CLV={row['mean_clv']}")
    lines.append("")
    lines.append("## Large-miss error taxonomy")
    for category, count in result["large_miss_review"]["error_taxonomy_summary"].items():
        lines.append(f"- {category}: {count}")
    lines.append("")
    lines.append("## Starter / active-status accuracy")
    lines.append(f"- Starter projection miss rate: {result['starter_and_active_status_accuracy']['starter_projection_miss_rate']}")
    lines.append(f"- False-active rate: {result['starter_and_active_status_accuracy']['false_active_rate']}")
    lines.append("")
    lines.append("## Improvement queue")
    for item in result["improvement_queue"]:
        lines.append(f"- {item['issue']} (magnitude {item['magnitude']}, source {item['source']})")
    if not result["improvement_queue"]:
        lines.append("- (empty -- no action needed)")
    lines.append("")
    lines.append(f"## Promotion candidates: {len(result['promotion_candidates'])}")
    lines.append("")
    lines.append(f"Rejected-research entries on file (consulted this run): "
                  f"{result['rejected_research_entries_on_file']}")
    path.write_text("\n".join(lines) + "\n")
    return path


def evaluate_retraining_triggers(*, new_games_since_last_review: int, calibration_error: float | None,
                                  calibration_error_baseline: float | None,
                                  league_environment_status: str,
                                  sustained_degradation_windows: int, min_new_games: int = 50,
                                  calibration_drift_threshold: float = 0.05,
                                  sustained_degradation_window_count: int = 3) -> dict:
    """Part 30/31: evaluates whether ANY retraining trigger condition is
    met. Returns a recommendation to create a CHALLENGER VERSION --
    NEVER a production replacement (this function has no code path that
    touches a production model file; it only returns a dict a human
    reads)."""
    triggered = []
    if new_games_since_last_review >= min_new_games:
        triggered.append(f"minimum new games reached ({new_games_since_last_review} >= {min_new_games})")
    if calibration_error is not None and calibration_error_baseline is not None:
        if calibration_error - calibration_error_baseline >= calibration_drift_threshold:
            triggered.append(f"calibration drift {calibration_error - calibration_error_baseline:+.3f} "
                              f">= {calibration_drift_threshold}")
    if league_environment_status == ese.WATCH:
        triggered.append("material league-environment shift flagged")
    if sustained_degradation_windows >= sustained_degradation_window_count:
        triggered.append(f"degradation sustained across {sustained_degradation_windows} consecutive windows")

    if not triggered:
        return {"retraining_triggered": False, "reasons": [], "action": "NO_ACTION"}
    return {"retraining_triggered": True, "reasons": triggered,
            "action": "CREATE_CHALLENGER_VERSION -- never a production replacement (Part 31)"}


SAMPLE_MILESTONES = (100, 250, 500, 1000)


def sample_milestone_status(n: int, milestones: tuple[int, ...] = SAMPLE_MILESTONES) -> dict:
    """Part 42: which pre-registered sample-size milestones a market has
    crossed -- purely informational (triggers a deeper human review),
    never a promotion decision by itself."""
    crossed = [m for m in milestones if n >= m]
    next_milestone = next((m for m in milestones if n < m), None)
    return {"n": n, "crossed_milestones": crossed, "next_milestone": next_milestone}


def weekly_rollup(daily_results: list[dict]) -> dict:
    """Part 41: aggregates a week's worth of already-computed daily
    run_daily_review() results (never re-scores the ledger itself) to
    check whether a single day's issue has become a REPEATED pattern.
    An issue appearing in only one of the week's runs is NOT flagged as
    persistent -- Part 24's "one outlier does not create a pattern"
    applies here too."""
    if not daily_results:
        return {"days": 0, "persistent_issues": [], "worst_status_this_week": ese.NORMAL}
    issue_days = defaultdict(set)
    for i, result in enumerate(daily_results):
        if result.get("incomplete"):
            continue
        for item in result.get("improvement_queue", []):
            issue_days[item["issue"]].add(i)
    persistent = [issue for issue, days in issue_days.items() if len(days) >= 3]
    worst = ese.combine_status([r.get("engine_status", ese.NORMAL) for r in daily_results])
    return {"days": len(daily_results), "persistent_issues": sorted(persistent),
            "worst_status_this_week": worst}


def main() -> None:
    conn = pl.init_db()
    result = run_daily_review(conn)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    path = write_daily_report(result, report_date=today)
    print(f"ENGINE STATUS: {result['engine_status']}")
    print(f"Recommendation: {result.get('recommendation', 'N/A')}")
    print(f"Report written to {path}")


if __name__ == "__main__":
    main()
