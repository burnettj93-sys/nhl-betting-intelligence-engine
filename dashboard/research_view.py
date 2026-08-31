"""
Research Lab view logic: parses the four completed experiments' result
JSON files (research/*_comparison_results.json) into one normalized
structure the UI can render generically. Every number displayed comes
from these files -- nothing is hand-typed here (Part: DATA ACCESS).

STATUS_MAP below is the one place this module editorializes at all, and
it does so only to attach the same REJECTED / INCONCLUSIVE / PROMISING
BUT NOT ADOPTED / ADOPTED label each experiment's own report already
used in its own words -- see the inline citation on each entry. Nothing
here reinterprets a report's conclusion; every experiment's final
recommendation was, and remains, KEEP CURRENT MODEL / KEEP CURRENT ELO.
"""
from __future__ import annotations

# (experiment display name, candidate letter prefix) -> (status, why -- a
# short paraphrase of that candidate's own report section, not a new
# judgment). Letter prefix is stable across reruns even though the exact
# window suffix in a candidate's label (e.g. "_10" vs "_25") depends on
# which window tuning selected.
STATUS_MAP = {
    ("Result-Quality / MOV Elo", "B"): (
        "INCONCLUSIVE",
        "Improved both metrics but the 95% bootstrap interval crosses zero on both "
        "(report's own words: 'statistically inconclusive... not adoption-worthy either').",
    ),
    ("Result-Quality / MOV Elo", "C"): (
        "REJECTED",
        "Worse on every season and every metric; 0% of bootstrap resamples showed improvement.",
    ),
    ("Result-Quality / MOV Elo", "D"): (
        "REJECTED",
        "Combined candidate inherits Candidate C's harm; worse than baseline.",
    ),
    ("Simple Team xG", "B"): (
        "PROMISING BUT NOT ADOPTED",
        "Weak positive signal, encouraging bootstrap direction (91%/88% of resamples), "
        "but tiny effect size, season-inconsistent, and calibration slightly worsened.",
    ),
    ("Simple Team xG", "C"): ("REJECTED", "Worse than baseline on every season."),
    ("Simple Team xG", "D"): ("REJECTED", "Worse than baseline, inconsistent across seasons."),
    ("Simple Team xG", "E"): ("REJECTED", "Combining two weak/harmful signals did not help."),
    ("Special Teams", "B"): ("REJECTED", "Worse than baseline in both evaluation seasons (PP only)."),
    ("Special Teams", "C"): ("REJECTED", "Worse than baseline in both evaluation seasons (PK only)."),
    ("Special Teams", "D"): (
        "INCONCLUSIVE",
        "Fitted coefficient essentially zero; effect statistically indistinguishable from a no-op.",
    ),
    ("Special Teams", "E"): ("REJECTED", "Decisively worse -- only 0.3-0.35% of resamples favored it."),
    ("Offense / Defense Shot Quality", "B"): (
        "REJECTED",
        "Technically the smallest positive delta of any candidate, but explicitly failed the "
        "effect-size and novelty (redundant-with-Elo/5v5-xG) adoption gates outright.",
    ),
    ("Offense / Defense Shot Quality", "C"): (
        "REJECTED",
        "Promising in tuning but the effect vanished on true evaluation and flipped sign "
        "between the two evaluation seasons.",
    ),
    ("Offense / Defense Shot Quality", "D"): ("REJECTED", "Worse than baseline; inherited defense's overfit."),
    ("Offense / Defense Shot Quality", "E"): (
        "REJECTED",
        "Worst result of any candidate across all four experiments (99.7% of resamples worse).",
    ),
}

FINAL_DECISIONS = {
    "Result-Quality / MOV Elo": "KEEP CURRENT ELO",
    "Simple Team xG": "KEEP CURRENT MODEL",
    "Special Teams": "KEEP CURRENT MODEL",
    "Offense / Defense Shot Quality": "KEEP CURRENT MODEL",
}


def _letter(label: str) -> str:
    return label.split("_")[0]


def normalize_elo_experiment(raw: dict) -> dict:
    baseline_label = "A_baseline"
    baseline = raw["final_candidates"][baseline_label]
    candidates = {}
    for label, data in raw["final_candidates"].items():
        if label == baseline_label:
            continue
        delta = raw["deltas_vs_baseline_true_eval"].get(label, {})
        season_deltas = {}
        for season, sd in data.get("season_breakdown", {}).items():
            base_sd = baseline.get("season_breakdown", {}).get(season, {})
            if "brier" in sd and "brier" in base_sd:
                season_deltas[season] = sd["brier"] - base_sd["brier"]
        candidates[label] = {
            "letter": _letter(label),
            "brier": data.get("true_eval_brier"), "log_loss": data.get("true_eval_log_loss"),
            "calibration_error": data.get("true_eval_calibration_error"),
            "n": data.get("true_eval_n"),
            "brier_delta_abs": delta.get("brier_abs_delta"), "brier_delta_rel_pct": delta.get("brier_rel_delta_pct"),
            "log_loss_delta_abs": delta.get("log_loss_abs_delta"),
            "log_loss_delta_rel_pct": delta.get("log_loss_rel_delta_pct"),
            "brier_ci": (delta.get("brier_bootstrap") or {}).get("ci_low"),
            "brier_ci_high": (delta.get("brier_bootstrap") or {}).get("ci_high"),
            "frac_improved_brier": (delta.get("brier_bootstrap") or {}).get("frac_resamples_improved"),
            "frac_improved_ll": (delta.get("log_loss_bootstrap") or {}).get("frac_resamples_improved"),
            "season_deltas": season_deltas,
        }
    return {
        "baseline": {"brier": baseline.get("true_eval_brier"), "log_loss": baseline.get("true_eval_log_loss"),
                     "n": baseline.get("true_eval_n")},
        "candidates": candidates,
    }


def normalize_standard_experiment(raw: dict) -> dict:
    baseline_label = "A_baseline"
    baseline = raw["metrics"][baseline_label]
    candidates = {}
    for label, data in raw["metrics"].items():
        if label == baseline_label:
            continue
        delta = raw["deltas_vs_baseline"].get(label, {})
        season_deltas = {}
        for season, sd in raw.get("season_consistency", {}).get(label, {}).items():
            season_deltas[season] = sd.get("brier_delta")
        candidates[label] = {
            "letter": _letter(label),
            "brier": data.get("brier"), "log_loss": data.get("log_loss"),
            "calibration_error": data.get("calibration_error"), "n": data.get("n"),
            "brier_delta_abs": delta.get("brier_abs_delta"), "brier_delta_rel_pct": delta.get("brier_rel_delta_pct"),
            "log_loss_delta_abs": delta.get("log_loss_abs_delta"),
            "log_loss_delta_rel_pct": delta.get("log_loss_rel_delta_pct"),
            "brier_ci": (delta.get("brier_bootstrap") or {}).get("ci_low"),
            "brier_ci_high": (delta.get("brier_bootstrap") or {}).get("ci_high"),
            "frac_improved_brier": (delta.get("brier_bootstrap") or {}).get("frac_resamples_improved"),
            "frac_improved_ll": (delta.get("log_loss_bootstrap") or {}).get("frac_resamples_improved"),
            "season_deltas": season_deltas,
        }
    return {
        "baseline": {"brier": baseline.get("brier"), "log_loss": baseline.get("log_loss"), "n": baseline.get("n")},
        "candidates": candidates,
    }


def build_experiment_summary(name: str, raw: dict | None) -> dict | None:
    if raw is None:
        return None
    normalized = normalize_elo_experiment(raw) if name == "Result-Quality / MOV Elo" else normalize_standard_experiment(raw)
    for label, cand in normalized["candidates"].items():
        status, why = STATUS_MAP.get((name, cand["letter"]), ("INCONCLUSIVE", "Not individually classified."))
        cand["status"] = status
        cand["status_reason"] = why
    normalized["final_decision"] = FINAL_DECISIONS.get(name, "KEEP CURRENT MODEL")
    normalized["test_suite_returncode"] = raw.get("test_suite_returncode")
    return normalized


def build_all_summaries(experiment_results: dict[str, dict | None]) -> dict[str, dict | None]:
    return {name: build_experiment_summary(name, raw) for name, raw in experiment_results.items()}
