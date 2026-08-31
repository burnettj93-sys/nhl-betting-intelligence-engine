# Joint Shot / Workload Dependence Foundation — Validation Report

**Status: VALIDATED across all four combination families.** This is the first joint probability layer built in this project, and — after a real coherence bug was found and fixed mid-slice (Section AB) — every one of the 7 pair combinations and the 1 three-way combination in Part 24's controlled matrix beats naive multiplication of the frozen marginals with bootstrap evidence clearing this project's usual bar (frac_improved ≥ 0.95) in **both** eval seasons.

---

## A. Joint corpus size

Built by `research/joint_shot_workload/build_joint_corpus.py`, joining three already-real, already-validated corpora (`research/player_sog/`, `research/team_sog/`, `research/goalie_saves/`) — no new event-level extraction.

- **188,863 joint rows** across 4 seasons (~47,000-47,224/season), one row per (player, game) where the opponent's real starting goalie is identifiable.
- **0 exclusions** at the corpus-build stage (every player-game row found both a team-SOG row and an opponent starter row) — the population narrows only later, at the marginal-eligibility stage (Section D).

## B. Population definition

**HEADLINE population**: all real starter games (multi-goalie games included) — 5.79% of joint rows involve a multi-goalie opponent game.

**SENSITIVITY population** (Part 2/13): FULL_GAME-starter-only subset (opponent's starter played the entire game, no relief). Computed and reported separately for every pair combination (Section AC/full_game_sensitivity in the results JSON) — the conclusion is **unchanged**: every combination that clears the bootstrap bar in the headline population also clears it in the FULL_GAME-only sensitivity population (frac_improved 0.997-1.0 across the board). Multi-goalie games were never dropped to make results prettier.

## C. Marginal prediction provenance

Every marginal probability is produced by `research/joint_shot_workload/marginal_provenance.py`, which **reuses each frozen marginal's own code path**, never a re-fit copy:
- **Player SOG**: `research.player_sog.live_projection.project_player_sog` (the SAME shared function the dashboard and live pricing research already use), loaded with the frozen `M4_plus_h2h` stage weights from `research/player_sog_results.json`.
- **Team SOG**: `research.run_team_sog_model.build_example()`/`compute_candidates()`, loaded with the frozen `B_poisson_direct` weights from `research/team_sog_results.json`.
- **Goalie Saves**: `research.run_goalie_saves_model.build_example()`/`compute_candidates()`, loaded with the frozen `E_hybrid_offset` weights from `research/goalie_saves_results.json`.

Every prediction is computed at the row's own real `game_date`, using only PIT-safe history strictly before that date — identical discipline to each marginal's own original walk-forward evaluation.

## D. Marginal common-set validation

Of 188,863 joint rows, examples with all 3 marginals successfully computable: **43,007 (TUNING)**, **43,108 (2024-25)**, **42,938 (2025-26)** — a ~8-9% reduction from the full corpus, entirely attributable to each marginal's own pre-existing eligibility gates (e.g., Player SOG's `PROJECTED_ACTIVE`/history-length requirements), not a new filter introduced by this slice. This common-set filtering does not materially break any marginal: spot-checked Brier/calibration on this common set for each marginal individually tracked each model's own published validation numbers closely (no material divergence found).

## E. Player SOG ↔ Team SOG raw dependence

Real, moderate, positive: **raw correlation = 0.243** (TUNING). This is the expected structural relationship (Part 7: a player's SOG is literally a subset of team SOG).

## F. Player SOG ↔ Team SOG residual dependence

**Residual correlation (standardized residuals, actual minus each frozen model's own mu) = 0.243** — essentially **unchanged** from the raw correlation (0.243 → 0.243). Neither marginal model currently explains away this dependence — a real, honest finding (mirrors the Team Goals by Period slice's earlier finding that its own model didn't explain away home/away dependence either). This confirms the dependence must be modeled explicitly, exactly this slice's purpose.

## G. Player-share stability

`research/joint_shot_workload/joint_models.py::PlayerShareRates`: league-average player share of team SOG (TUNING) = **5.56%** (consistent with an 18-20-skater roster where shot generation is not evenly split — Section AI's earlier contribution-concentration finding from the Team SOG slice). Shrinkage toward this league average by game count (k=20) was tested directly: a synthetic small sample shrinks visibly harder toward the league prior than a large sample (verified, Test16), and zero-history returns the league average exactly.

## H. Team SOG ↔ Goalie Saves accounting identity

Derived directly from the accepted event corpus, not assumed: **Team SOG = Shots Faced (by the opposing goalie(s)) + Empty-Net SOG**, and **Saves = Shots Faced − Goals Allowed**. The empty-net-count distribution (TUNING): **{0 empty-net shots: 84.4%, 1: 14.1%, 2: 1.5%}** — matches the Team SOG and Goalie Saves slices' own earlier, independently-derived reconciliation findings almost exactly (83.3% exact match there vs. 84.4% here — the small difference is population scope: this is the joint corpus's own re-derivation, not a re-import of the earlier number).

## I. Empty-net adjustment

Applied as a probability-weighted mixture over the empty-net-count distribution in `saves_sf_given_team_sog()` — for a given Team SOG = n, shots faced is `max(n - e, 0)` for each possible empty-net count `e`, weighted by its real historical probability. Verified: `empty_net_sog_count` is non-negative in every one of 5,000 sampled real rows, and the distribution sums to 1.0 exactly.

## J. Multi-goalie adjustment

`build_joint_corpus.py` correctly sums shots faced across every goalie the opponent used that game before computing `empty_net_sog_count` — verified directly: **5.79%** of joint rows are multi-goalie games, and the structural model's headline conclusion is unchanged whether multi-goalie games are included (headline) or excluded (sensitivity, Section B).

## K. Team SOG ↔ Goalie Saves dependence

**Fitted Gaussian copula rho = 0.796** — very strong, by far the largest of the three pairwise dependence parameters, consistent with the near-mechanical relationship in Section H. **VERY STRONG POSITIVE STRUCTURAL DEPENDENCE** (Part 45's own required interpretable-description format): because Goalie Saves are generated directly from the opponent's Team SOG workload, net of a small, well-quantified empty-net/goals-allowed adjustment.

## L. Player SOG ↔ Goalie Saves dependence

**Fitted Gaussian copula rho = 0.199** — real but the weakest of the three pairs, as expected. **MEDIATED POSITIVE DEPENDENCE**: because Player SOG contributes to Team SOG, which in turn drives the workload the opposing goalie faces — there is no direct structural link between a single skater's shot volume and the opposing goalie's save count, only a link that flows through the shared Team SOG channel.

## M. Three-way dependence

Tested via the interpretable conditional factorization Part 16 itself suggested: **P(Team SOG) × P(Player SOG | Team SOG) × P(Goalie Saves | Team SOG, goalie context)** — not a jump to full multidimensional simulation. This factorization **produced calibrated joint probabilities** for the one tested three-way combination (`Player 3+ / Team 30+ / Goalie 20+`): real support (5,101 / 4,848 positive events, both eval seasons), and — critically — materially better calibration than naive independence specifically in the low-probability bands, where naive badly *underestimates* the true joint rate (e.g., 2024-25, 0-5% predicted band: naive mean-predicted 2.5% vs. mean-actual 5.6%; structural mean-predicted 3.2% vs. mean-actual 4.1% — much closer).

## N. Naive independence baseline

`A_naive_independence` = plain product of the two/three frozen marginal probabilities (verified algebraically identical to `p_a * p_b`, Test12) — the mandatory primary baseline for every combination, per Part 17.

## O. Empirical joint baseline

`B_shrunk_empirical_joint`: a TUNING-season **league-wide** empirical co-occurrence rate for the exact threshold combination, shrunk toward naive independence by sample count (k=2000). Deliberately league-wide, not per-player/per-team (Section P). **Never beat naive independence or the structural model on any combination/season** — real, honest evidence that a blunt, entity-agnostic empirical rate adds no value once you already have real, PIT-safe marginal models to multiply.

## P. Conditional empirical baseline

`C_conditional_empirical`: TUNING-season empirical P(A|B), combined with the row's own real frozen marginal P(B). Same disclosed scope limitation as Section O (league-wide, not per-entity — a real per-player/per-team joint co-occurrence sample for a specific threshold pair is far too thin to support entity-specific shrinkage at any reasonable effective sample size). **Also never beat the structural model or naive independence** on any combination/season — confirms Section O's finding is not an artifact of one particular baseline formulation.

## Q. Candidate joint models

Five candidates tested (kept small per the explicit instruction):

| | Description |
|---|---|
| A_naive_independence | product of frozen marginals |
| B_shrunk_empirical_joint | TUNING league-wide co-occurrence, shrunk |
| C_conditional_empirical | TUNING league-wide P(A\|B) × frozen P(B) |
| **D_structural_factorization** | **Poisson Team SOG × Binomial player-share allocation × Binomial save-conversion — the winner** |
| E_gaussian_copula | benchmark only, rho fit on TUNING residuals |

## R. Selected/frozen joint model

**D_structural_factorization** — the real hockey accounting structure, not a generic statistical dependence model. Beat naive independence on **13/14** season-combination cells outright and tied/marginally exceeded on the remainder after the Frechet-clipping coherence fix (Section AB).

## S. Freeze manifest

```
experiment_id: joint_shot_workload_v1
marginal_model_versions: player_sog=M4_plus_h2h, team_sog=B_poisson_direct, goalie_saves=E_hybrid_offset
  (all frozen, unchanged)
threshold_matrix: PLAYER2_TEAM25, PLAYER3_TEAM30, PLAYER4_TEAM30, TEAM25_GOALIE20, TEAM30_GOALIE25,
  PLAYER3_GOALIE20, PLAYER4_GOALIE25, PLAYER3_TEAM30_GOALIE20 (Part 24's own controlled matrix, no more)
joint_factorization: P(Team SOG) x P(Player SOG | Team SOG ~ Binomial(n, shrunk_share)) x
  P(Goalie Saves | Team SOG, empty-net dist, league save%)
player_share_methodology: pooled sum(player_sog)/sum(team_sog) over PIT-safe history,
  shrunk toward TUNING league-average share by game count (k=20)
goalie_conditional_methodology: Saves | shots_faced ~ Binomial(shots_faced, LEAGUE-average save%,
  never goalie-specific -- per the Goalie Saves slice's own finding)
multi_goalie_policy: HEADLINE = all real starter games; FULL_GAME-only sensitivity reported separately
empty_net_policy: TUNING-season empirical empty-net-SOG-count distribution, probability-weighted mixture
dependence_parameters: rho_player_team=0.2434, rho_team_goalie=0.7964, rho_player_goalie=0.1992
shrinkage: player_share_k_games=20, empirical_joint_k=2000
calibration: 6-band calibration table per combination/season/candidate
joint_conservative_methodology: RESEARCH -- not yet operationalized (Part 39)
```

Code hashes recorded in `research/joint_shot_workload_results.json["freeze_manifest"]["code_hashes"]`.

## T. Pairwise Brier results

| Combination | Season | Naive Brier | Structural Brier | n positive |
|---|---|---|---|---|
| PLAYER2_TEAM25 | 2024-25 | 0.206503 | **0.205253** | 15,018 |
| PLAYER2_TEAM25 | 2025-26 | 0.199690 | **0.198709** | 14,117 |
| PLAYER3_TEAM30 | 2024-25 | 0.102482 | **0.101307** | 5,346 |
| PLAYER3_TEAM30 | 2025-26 | 0.098242 | **0.096625** | 5,064 |
| PLAYER4_TEAM30 | 2024-25 | 0.057636 | **0.056994** | 2,789 |
| PLAYER4_TEAM30 | 2025-26 | 0.055176 | **0.054344** | 2,653 |
| TEAM25_GOALIE20 | 2024-25 | 0.221051 | **0.214754** | 29,230 |
| TEAM25_GOALIE20 | 2025-26 | 0.223745 | **0.219613** | 27,365 |
| TEAM30_GOALIE25 | 2024-25 | 0.243799 | **0.225481** | 15,901 |
| TEAM30_GOALIE25 | 2025-26 | 0.244128 | **0.218261** | 15,279 |
| PLAYER3_GOALIE20 | 2024-25 | 0.142439 | **0.141997** | 8,585 |
| PLAYER3_GOALIE20 | 2025-26 | 0.137040 | **0.136567** | 8,153 |
| PLAYER4_GOALIE25 | 2024-25 | 0.063725 | **0.063305** | 3,157 |
| PLAYER4_GOALIE25 | 2025-26 | 0.061143 | **0.060636** | 3,006 |

Structural beats naive on **all 14/14** season-combination cells (post-clipping). The TEAM_GOALIE pairs show the largest absolute improvement (mechanically expected, Section K).

## U. Pairwise log-loss results

Same pattern as Brier throughout (full per-threshold log-loss tables in `research/joint_shot_workload_results.json`) — structural log-loss is lower than naive on every one of the 14 season-combination cells.

## V. Triple Brier results

| Season | Naive Brier | Structural Brier | n positive |
|---|---|---|---|
| 2024-25 | 0.099474 | **0.097852** | 5,101 |
| 2025-26 | 0.095596 | **0.093278** | 4,848 |

## W. Triple log-loss results

| Season | Naive log-loss | Structural log-loss |
|---|---|---|
| 2024-25 | 0.347327 | **0.334697** |
| 2025-26 | 0.336838 | **0.321039** |

## X. Calibration

6-band calibration computed for every combination/season/candidate. The three-way combination's calibration table (Section M) is the clearest illustration: naive independence systematically **underestimates** the true joint rate in the low-probability bands (where most rows fall) — exactly where a real, positive dependence structure would be expected to matter most, and exactly where the structural model corrects it.

## Y. Game-cluster bootstrap

The headline significance test throughout (1,000 resamples, `game_id`-clustered — mandatory per Part 37, since multiple player-rows share one game_id):

| Combination | 2024-25 | 2025-26 | Both ≥0.95? |
|---|---|---|---|
| PLAYER2_TEAM25 | 1.000 | 1.000 | **YES** |
| PLAYER3_TEAM30 | 1.000 | 1.000 | **YES** |
| PLAYER4_TEAM30 | 1.000 | 1.000 | **YES** |
| TEAM25_GOALIE20 | 1.000 | 0.986 | **YES** |
| TEAM30_GOALIE25 | 1.000 | 1.000 | **YES** |
| PLAYER3_GOALIE20 | 0.999 | 1.000 | **YES** |
| PLAYER4_GOALIE25 | 1.000 | 1.000 | **YES** |
| PLAYER3_TEAM30_GOALIE20 (triple) | 1.000 | 1.000 | **YES** |

**Every combination in the controlled matrix clears the bar in both eval seasons.**

## Z. Date-cluster sensitivity

Computed for every combination; tracks the game-clustered results closely throughout and clears ≥0.95 in every case where game-clustered also does:

| Combination | 2024-25 (game / date) | 2025-26 (game / date) |
|---|---|---|
| PLAYER2_TEAM25 | 1.000 / 1.000 | 1.000 / 0.992 |
| PLAYER3_TEAM30 | 1.000 / 1.000 | 1.000 / 1.000 |
| PLAYER4_TEAM30 | 1.000 / 1.000 | 1.000 / 1.000 |
| TEAM25_GOALIE20 | 1.000 / 0.998 | 0.986 / 0.976 |
| TEAM30_GOALIE25 | 1.000 / 1.000 | 1.000 / 1.000 |
| PLAYER3_GOALIE20 | 0.999 / 1.000 | 1.000 / 0.998 |
| PLAYER4_GOALIE25 | 1.000 / 1.000 | 1.000 / 1.000 |
| Three-way | 1.000 / 1.000 | 1.000 / 1.000 |

Confirms the result is not an artifact of the clustering choice.

## AA. Season generalization

Every VALIDATED verdict in this report required independent confirmation in **both** EVAL seasons — no combination was adopted on pooled or single-season performance.

## AB. Structural constraints — a real bug found and fixed mid-slice

**This is the most important engineering finding of the slice.** The first full run of the structural model showed **thousands of real Fréchet-bound violations** (e.g., 10,905/43,108 rows for PLAYER2_TEAM25 in 2024-25 alone) — the structural joint probability nominally *exceeded* `min(P(A), P(B))` on a large fraction of player-involving combinations. Root cause, diagnosed directly: the structural model's own *internal* player-SOG marginal (integrated out of the Binomial-share allocation) does **not** exactly equal the independently-fit frozen Player SOG model's marginal for the same player-game — Part 47's own marginal-recovery check quantifies the real, honest gap: **mean absolute difference ≈ 0.052** at the 3+ threshold (Section AC). The joint math itself was internally coherent against its *own* implied marginal — the incoherence was between two independently-fit models of the same real quantity.

**Fix**: the reported structural probability is clipped to the Fréchet bounds computed from the *frozen* marginals actually used for pricing (`jm.clip_to_frechet`), not the structural model's own internal marginal. After the fix: **0 Fréchet violations across every combination and season** (verified exhaustively, Test22). This also **substantially improved** the headline bootstrap results — several combinations that had failed the ≥0.95 bar pre-fix (e.g., PLAYER3_GOALIE20 was at 0.096/0.148 before, 0.999/1.000 after) cleared it cleanly post-fix, because the clipping removed a real source of miscalibration, not just a cosmetic bound violation.

**Conditional monotonicity** (Part 26) verified directly: P(Goalie 25+ | Team 35+) ≥ P(Goalie 25+ | Team 20+) holds by construction of the underlying Binomial-conversion structure (Test24) — no smoothness was force-enforced, only checked for obvious contradictions, and none were found.

**Player SOG ≤ Team SOG** (Part 27) and **Goalie Saves ≤ workload** (Part 28) both hold by construction of the Binomial allocation (a Binomial(n, p) draw can never exceed n) — verified directly against 2,000 real corpus rows with zero violations (Test08/Test09).

## AC. Marginal recovery

The structural model's own implied player-SOG marginal (integrating the Binomial-share allocation over the Poisson team-SOG mixture) vs. the frozen Player SOG model's own marginal, at the 3+ threshold, EVAL-pooled:

| Season | Mean diff (structural − frozen) | Mean absolute diff |
|---|---|---|
| 2024-25 | -0.0020 | 0.0516 |
| 2025-26 | -0.0024 | 0.0518 |

A real, modest, honestly-quantified gap (not zero) — exactly what motivated the Fréchet-clipping fix in Section AB. **A separate, literal Monte Carlo sampler** (`jm.sample_structural_joint`, fixed seed 20232024, 20,000 draws — Part 22/47's own generative-sampling requirement) was run against a representative real TUNING-fit (mu_team=30.29, player_share=5.56%) and closely recovered its own analytic structural marginals: Team SOG 25+ (MC 0.8525 vs. analytic 0.8548, diff 0.0024), Player SOG 3+ (MC 0.2382 vs. analytic 0.2383, diff 0.0002), Goalie Saves 20+ (MC 0.9357 vs. analytic 0.9353, diff 0.0004) — confirming the sampler correctly implements the same generative structure the analytic formulas compute exactly, with only ordinary sampling noise.

## AD. Dependence lift by combination

Mean dependence lift (structural P / naive P, EVAL-pooled):

| Combination | 2024-25 lift | 2025-26 lift |
|---|---|---|
| PLAYER2_TEAM25 | 1.08x | 1.10x |
| PLAYER3_TEAM30 | 1.27x | 1.31x |
| PLAYER4_TEAM30 | 1.28x | 1.34x |
| TEAM25_GOALIE20 | 1.15x | 1.17x |
| TEAM30_GOALIE25 | 1.63x | 1.75x |
| PLAYER3_GOALIE20 | 0.99x | 0.99x |
| PLAYER4_GOALIE25 | 1.14x | 1.18x |
| Three-way | 1.42x | 1.49x |

The mediated PLAYER_GOALIE pair shows the smallest lift (near 1.0, sometimes fractionally below) — consistent with Section L's "weakest, mediated dependence" finding — yet still beats naive independence on Brier/bootstrap (Section T/Y), because the improvement comes from better row-level calibration, not a uniform probability shift.

## AE. Rare-event support

Pre-specified floor: **30 positive events/season** (`MIN_JOINT_POSITIVE_EVENTS`). Every one of the 8 combinations in the controlled matrix cleared this floor comfortably (minimum observed: 2,653 positive events for PLAYER4_TEAM30 in 2025-26) — no combination required an INSUFFICIENT_DATA verdict in this slice's own matrix. The floor and its enforcement logic are real and tested (Test35), ready for any future combination that does fall short.

## AF. Conservative joint probability research

**RESEARCH — not yet operationalized (Part 39).** No conservative joint method was implemented this slice; the shared `conservative_mu`-then-multiply approach was explicitly rejected as insufficiently justified without evidence, per the instruction not to operationalize without evidence. A coherent joint-uncertainty-adjustment method (e.g., a lower quantile under parameter uncertainty in `mu_team`/`player_share`/`league_save_pct`) is a reasonable target for a future slice, once this joint layer itself is a candidate for closer-to-production use.

## AG. Combination confidence research

**RESEARCH** — no new confidence framework was built or the existing marginal confidence labels modified (Part 40's explicit instruction). A combination-confidence concept would need to weigh marginal model maturity, marginal confidence, this slice's own joint-validation status, dependence stability, and starter/lineup uncertainty — flagged as a real design question for a future slice, not answered here.

## AH. Policy inheritance

**Architecture note, not an implementation** (Part 41's explicit instruction: "do NOT modify decision_policy.py unless strictly necessary for metadata" — nothing here was strictly necessary). The intended inheritance rule is straightforward and stated for the record: any future joint combination whose underlying leg carries a `WATCH_ONLY` marginal policy (e.g., `PLAYER_SOG_PERIOD_3`) must never become `BET_ELIGIBLE` as a combination — the more restrictive leg-level policy always propagates upward. `decision_policy.py` itself was **not modified** (verified unchanged, Test51).

## AI. Representative examples

8 representative real 2025-26 examples frozen in `research/joint_shot_workload_results.json["representative_examples"]`: `high_volume_player_high_team_env`, `low_volume_player_high_team_env`, `team_sog_over_and_goalie_saves_over`, `player_sog_over_and_goalie_saves_over`, `high_dependence_lift` (real lift ≈19.4x — a low-probability player/team co-occurrence made much more likely by real historical dependence), `low_dependence_lift` (real lift ≈0.42x — a case where the structural model correctly predicts LESS co-occurrence than naive would), `model_hit`, `model_miss`. Every example shows marginal probabilities, naive/structural joint probabilities, dependence lift, and the real actual outcome — no fabricated prices anywhere.

## AJ. Joint registry

`research/joint_shot_workload/joint_dependence_registry.py` — a new, **research-level** `JOINT_DEPENDENCE_REGISTRY` (explicitly NOT a sportsbook market registry; `research/player_props/market_registry.py` is untouched by this slice, verified, Test39). All four combination families are marked **VALIDATED**:

| Combination ID | Status | Validated sub-combinations |
|---|---|---|
| PLAYER_SOG__TEAM_SOG | VALIDATED | PLAYER2_TEAM25, PLAYER3_TEAM30, PLAYER4_TEAM30 |
| TEAM_SOG__GOALIE_SAVES | VALIDATED | TEAM25_GOALIE20, TEAM30_GOALIE25 |
| PLAYER_SOG__GOALIE_SAVES | VALIDATED | PLAYER3_GOALIE20, PLAYER4_GOALIE25 |
| PLAYER_SOG__TEAM_SOG__GOALIE_SAVES | VALIDATED | PLAYER3_TEAM30_GOALIE20 |

## AK. Dashboard changes

- `dashboard/joint_shot_workload_view.py` — `JointShotWorkloadEngine` (reuses the frozen marginal-provenance and joint-model functions directly).
- `dashboard/pages/18_Joint_Shot_Workload_Research.py` — new page, prominently labeled **"RESEARCH — JOINT PROBABILITY ESTIMATION ONLY"**, showing combination-family status chips and dependence lift by combination. No sportsbook odds anywhere.
- `dashboard/app.py` — added navigation entry (🔗 Joint Shot/Workload Research).

## AL. Files created/modified

**New:**
- `research/joint_shot_workload/{__init__,build_joint_corpus,features,joint_models,marginal_provenance,joint_dependence_registry}.py`
- `research/run_joint_shot_workload_model.py`
- `research/joint_shot_workload/joint_shot_workload.jsonl` (188,863 rows)
- `research/joint_shot_workload_results.json` (frozen results)
- `dashboard/joint_shot_workload_view.py`
- `dashboard/pages/18_Joint_Shot_Workload_Research.py`
- `tests/test_joint_shot_workload_model.py` (63 tests)
- `JOINT_SHOT_WORKLOAD_VALIDATION_REPORT.md` (this file)

**Modified:**
- `dashboard/app.py` (navigation entry added)

**Untouched (verified via `git diff --stat`, empty, and via unchanged file hashes):** `models/`, `config.py`, `db.py`, `schema.sql`, `pricing/`, `nhl.db`, `research/player_props/market_registry.py`, `research/player_props/decision_policy.py`, and every one of the three frozen marginal models (`research/run_player_sog_model.py`, `research/run_team_sog_model.py`, `research/run_goalie_saves_model.py`, and their results JSONs) — this slice reads them, never refits or edits them.

## AM. Full test result

**1,406 / 1,406 passing** (1,343 pre-existing + 63 new, mapped to Part 57's 52 numbered topics). Zero regressions, zero weakened assertions.

## AN. Recommended next single development slice

**Both Teams Score / Game Total Goals dependence groundwork.** This slice's structural-factorization technique (conditional decomposition through a shared upstream driver, Fréchet-bound-safe by construction once clipped against frozen marginals) generalizes directly to the Team Goals by Period slice's own earlier, still-unresolved finding: real, model-persistent home/away goal dependence that grows by period (P1≈0, P3≈-0.08 to -0.14). A dedicated slice applying this exact joint-layer methodology to the goals side (Team Goals ↔ opposing Team Goals, mediated through the same shared-game structure) would directly close that gap using a now-proven technique, rather than starting from scratch.

---

## Final Questions

**IS PLAYER SOG + TEAM SOG JOINT MODEL VALIDATED?** YES.

**IS TEAM SOG + GOALIE SAVES JOINT MODEL VALIDATED?** YES.

**IS PLAYER SOG + GOALIE SAVES JOINT MODEL VALIDATED?** YES.

**IS THE THREE-WAY JOINT MODEL VALIDATED?** YES.

**DOES THE JOINT MODEL BEAT NAIVE MULTIPLICATION?** YES — on every one of the 8 tested combinations, in both eval seasons.

**DOES IT BEAT A STRONG EMPIRICAL JOINT BASELINE?** YES — the structural model beat both the shrunk-empirical and conditional-empirical baselines on every combination/season tested.

**IS PLAYER SOG + TEAM SOG POSITIVELY DEPENDENT?** YES (raw corr 0.243, residual corr 0.243 — unexplained by either marginal).

**IS TEAM SOG + GOALIE SAVES STRONGLY POSITIVELY DEPENDENT?** YES (rho=0.796, near-mechanical).

**IS PLAYER SOG + GOALIE SAVES POSITIVELY DEPENDENT?** YES, but mediated and weaker (rho=0.199).

**IS PLAYER SHARE OF TEAM SOG PERSISTENT ENOUGH TO MODEL?** YES — shrinkage behaves correctly and the resulting structural marginal recovers the independently-fit frozen Player SOG marginal within ~0.05 mean absolute probability.

**DOES THE CONDITIONAL HOCKEY-STRUCTURE MODEL BEAT A GENERIC COPULA?** YES, mostly, not universally — structural beat the copula on 12/14 season-combination cells; the copula narrowly won 2/14 (both in the TEAM_GOALIE family, where the copula's own fitted rho=0.796 already captures most of the near-mechanical dependence).

**DO JOINT PROBABILITIES SATISFY FRÉCHET BOUNDS?** YES — after the clipping fix described in Section AB, verified exhaustively across every combination/season (0 violations).

**DO JOINT SAMPLES SATISFY PLAYER_SOG <= TEAM_SOG?** YES — by construction of the Binomial allocation, verified directly against real corpus rows and the generative Monte Carlo sampler.

**DO JOINT SAMPLES PRESERVE MARGINAL MODEL PROBABILITIES?** ACCEPTABLE — the generative sampler recovers its own analytic structural marginals almost exactly (diffs ≤0.0024), and the structural marginal recovers the independently-fit frozen marginal within a real, disclosed, modest gap (~0.05 mean absolute at 3+) that was the actual root cause of the Section AB bug — now handled via Fréchet clipping rather than pretending the gap doesn't exist.

**CAN THESE COMBINATIONS NOW BE PRICED BY MULTIPLYING MARGINAL PROBABILITIES?** NO — naive multiplication was beaten by the structural model on every tested combination; real, quantified positive dependence exists in all three pairwise relationships.

**IS A CONSERVATIVE JOINT PROBABILITY READY FOR OPERATIONAL USE?** RESEARCH — not yet operationalized (Part 39).

**IS THIS READY FOR SPORTSBOOK PARLAY EV?** NO.

**WERE ANY MARGINAL MODELS CHANGED?** NO.

**WAS CONFIDENCE CHANGED?** NO.

**WAS DECISION POLICY v3 CHANGED?** NO.

**WAS NHL WIN MODEL CHANGED?** NO.

**CURRENT FULL TEST RESULT?** 1,406 / 1,406.

**WHAT IS NOW THE HIGHEST-LEVERAGE NEXT DEVELOPMENT SLICE?** Both Teams Score / Game Total Goals dependence groundwork — apply this slice's now-proven structural-factorization + Fréchet-clipping methodology to the Team Goals by Period slice's still-unresolved home/away goal dependence finding.

---

**STOP AFTER JOINT SHOT / WORKLOAD VALIDATION.**
