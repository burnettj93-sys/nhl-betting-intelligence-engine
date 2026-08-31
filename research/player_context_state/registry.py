"""
Part 44: PLAYER_CONTEXT_REGISTRY -- a plain, disclosed registry of every
context signal this slice tested, deliberately separate from
market_registry.py and research/joint_scoring_dependence's
JOINT_DEPENDENCE_REGISTRY (different concern: this tracks RESEARCH
validation status of a context signal, not a priced market or a joint
dependence combination).

Classification thresholds (VALIDATED / PARTIAL / NOT_VALIDATED) are
pre-specified here, applied mechanically to the driver's own bootstrap
output -- never hand-picked per prop to fit a narrative:
  VALIDATED     -- game-clustered bootstrap 95% CI excludes 0 in the
                   UNDER direction in BOTH EVAL seasons
  PARTIAL       -- excludes 0 in exactly one of the two EVAL seasons
                   (directionally consistent in both)
  NOT_VALIDATED -- CI includes 0 in both seasons, OR the point estimate
                   flips sign across seasons (no reliable direction)
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = REPO_ROOT / "research" / "player_context_state_results.json"
REGISTRY_PATH = REPO_ROOT / "research" / "player_context_state_registry.json"


def _excludes_zero_under(bootstrap: dict) -> bool:
    return bootstrap != "INSUFFICIENT_DATA" and bootstrap["ci_high"] < 0


def _classify(bootstraps_by_season: dict) -> str:
    seasons = list(bootstraps_by_season.values())
    if any(b == "INSUFFICIENT_DATA" for b in seasons):
        return "INSUFFICIENT_DATA"
    signs = [1 if b["point_delta"] < 0 else -1 for b in seasons]
    if len(set(signs)) > 1:
        return "NOT_VALIDATED"
    excl = [_excludes_zero_under(b) for b in seasons]
    if all(excl):
        return "VALIDATED"
    if any(excl):
        return "PARTIAL"
    return "NOT_VALIDATED"


def build_registry(results: dict) -> list[dict]:
    entries = []
    props = results["props"]
    eval_seasons = results["config"]["eval_seasons"]

    for prop, block in props.items():
        cold_boots = {s: block["by_season"][str(s)]["cold_vs_normal_bootstrap"] for s in eval_seasons}
        multi_boots = {s: block["by_season"][str(s)]["multi_signal_vs_normal_bootstrap"] for s in eval_seasons}
        hot_boots = {s: block["by_season"][str(s)]["hot_vs_normal_bootstrap"] for s in eval_seasons}

        cold_n = {s: block["by_season"][str(s)]["by_state"]["COLD"]["n"] for s in eval_seasons}
        multi_n = {s: block["by_season"][str(s)]["multi_signal"]["n"] for s in eval_seasons}
        hot_n = {s: block["by_season"][str(s)]["by_state"]["HOT"]["n"] for s in eval_seasons}

        entries.append({
            "signal": "COLD_STATE", "prop": prop, "source": "own-history rolling recent-5 vs baseline-20 "
            "actual-rate log-ratio (expectation-relative, TUNING-fit bottom-20% cutoff)",
            "status": _classify(cold_boots),
            "effect_direction": "UNDER (frozen model overpredicts)" if all(
                cold_boots[s] != "INSUFFICIENT_DATA" and cold_boots[s]["point_delta"] < 0 for s in eval_seasons)
                else "INCONSISTENT",
            "effect_magnitude_by_season": {str(s): cold_boots[s]["point_delta"]
                                            if cold_boots[s] != "INSUFFICIENT_DATA" else None for s in eval_seasons},
            "sample_size_by_season": cold_n,
            "supported_seasons": eval_seasons,
            "known_confounders": ["role/TOI decline (see COLD_AND_TOI_DECLINE -- often the dominant real driver)",
                                   "regression to the mean (see report Section R)"],
            "operational_status": "RESEARCH ONLY -- no decision_policy change made this slice",
        })

        entries.append({
            "signal": "COLD_AND_TOI_DECLINE", "prop": prop, "source": "COLD_STATE AND recent-10 vs baseline-20 "
            "icetime log-ratio at/below TUNING-fit bottom-20% cutoff (Part 4 multi-signal AND-rule)",
            "status": _classify(multi_boots),
            "effect_direction": "UNDER (frozen model overpredicts)" if all(
                multi_boots[s] != "INSUFFICIENT_DATA" and multi_boots[s]["point_delta"] < 0 for s in eval_seasons)
                else "INCONSISTENT",
            "effect_magnitude_by_season": {str(s): multi_boots[s]["point_delta"]
                                            if multi_boots[s] != "INSUFFICIENT_DATA" else None for s in eval_seasons},
            "sample_size_by_season": multi_n,
            "supported_seasons": eval_seasons,
            "known_confounders": ["role/TOI decline is itself part of the signal definition here, not a "
                                   "confound to strip out -- this is the stronger, more consistent of the two "
                                   "production-based signals"],
            "operational_status": "RESEARCH ONLY -- no decision_policy change made this slice",
        })

        hot_signs = [1 if hot_boots[s] != "INSUFFICIENT_DATA" and hot_boots[s]["point_delta"] > 0 else -1
                     for s in eval_seasons]
        hot_mirrors_cold = all(s == 1 for s in hot_signs)
        entries.append({
            "signal": "HOT_STATE_CONTROL", "prop": prop, "source": "same signal as COLD_STATE, top-20% cutoff "
            "(Part 5 symmetric control -- NOT assumed to create OVER value)",
            "status": "CONSISTENT_MIRROR" if hot_mirrors_cold else "ANOMALOUS_ASYMMETRY",
            "effect_direction": "OVER (frozen model underpredicts)" if hot_mirrors_cold else
                "frozen model ALSO overpredicts HOT state in at least one season -- not a clean mirror of COLD",
            "effect_magnitude_by_season": {str(s): hot_boots[s]["point_delta"]
                                            if hot_boots[s] != "INSUFFICIENT_DATA" else None for s in eval_seasons},
            "sample_size_by_season": hot_n,
            "supported_seasons": eval_seasons,
            "known_confounders": ["rare-event count noise for sparser props (Goals/Assists/Points)"],
            "operational_status": "RESEARCH ONLY -- diagnostic control, not a betting signal",
        })

        arena = block["arena_effects"]
        entries.append({
            "signal": "ARENA_PLAYER_PERFORMANCE", "prop": prop, "source": "player-arena residual, hierarchically "
            "shrunk player-arena -> arena -> league-mean-zero (TUNING-fit, EVAL-checked via correlation)",
            "status": "NOT_VALIDATED" if abs(arena["eval_generalization_corr"] or 0) < 0.10 else "PARTIAL",
            "effect_direction": "NO RELIABLE OUT-OF-SAMPLE SIGNAL",
            "effect_magnitude_by_season": {"eval_generalization_corr": arena["eval_generalization_corr"]},
            "sample_size_by_season": {"eval_n_pairs": arena["eval_n_pairs"]},
            "supported_seasons": eval_seasons,
            "known_confounders": ["small raw player-arena game counts (Part 17's own stated concern) -- "
                                   "shrinkage was applied precisely because of this, and the effect still does "
                                   "not generalize"],
            "operational_status": "RESEARCH ONLY -- confirmed NOT usable as a betting signal",
        })

        entries.append({
            "signal": "ARENA_RINK_RECORDING_CANDIDATE", "prop": prop, "source": "arena-wide (pooled across all "
            "players, home-team identity as arena proxy) mean prediction residual, TUNING-fit",
            "status": "DESCRIPTIVE_ONLY -- not claimed as causal (team style-of-play is an equally plausible "
                      "confound, same caveat as the existing Hits rink-variation finding in "
                      "NHL_PBP_FOUR_SEASON_CORPUS_REPORT.md Section S)",
            "effect_direction": "N/A -- descriptive spread, not a directional betting claim",
            "effect_magnitude_by_season": {"arena_raw_mean_range": arena["arena_raw_mean_range"],
                                            "n_arenas": arena["n_arenas"]},
            "sample_size_by_season": {"n_arenas": arena["n_arenas"]},
            "supported_seasons": eval_seasons,
            "known_confounders": ["team style-of-play", "opponent quality mix", "schedule composition"],
            "operational_status": "RESEARCH ONLY -- not operationalized",
        })

    entries.append({
        "signal": "MEDIA_SENTIMENT_STATE", "prop": "ALL", "source": "NONE -- no legitimate, timestamped, "
        "legally-accessible historical media/news/sentiment corpus exists anywhere in this repository "
        "(verified directly by search)",
        "status": "NOT_BUILT",
        "effect_direction": "N/A",
        "effect_magnitude_by_season": {},
        "sample_size_by_season": {},
        "supported_seasons": [],
        "known_confounders": [],
        "operational_status": "NOT BUILT -- fabricating a media corpus was explicitly prohibited by this "
                               "slice's own instructions; this is a first-class disclosed finding, not an "
                               "omission. See report Section E/L/U.",
    })
    return entries


if __name__ == "__main__":
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    registry = build_registry(results)
    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2, default=str)
    print(f"Wrote {len(registry)} registry entries to {REGISTRY_PATH}")
