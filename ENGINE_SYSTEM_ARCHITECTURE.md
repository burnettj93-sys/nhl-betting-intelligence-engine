# Engine System Architecture

Living architecture document for the NHL Betting Intelligence Engine, current as of the Preseason Master Consolidation sprint.

## Current pipeline

```
DATA SOURCES
  NHL API (schedule, PBP, boxscore) -- research/real_nhl_pbp/, research/real_nhl_results/
  MoneyPuck (skater/team/goalie game logs) -- research/moneypuck_ingestion/, per-prop raw/ dirs
  The Odds API (SOG only, live) -- research/live_sog_pricing/
    |
    v
NORMALIZATION
  Per-prop corpus builders (build_*_corpus.py) -- one per family, each producing a
  normalized .jsonl with a consistent {player_id, team, opponent, game_date, season,
  home_or_away, <stat>} row shape.
    |
    v
PIT FEATURE STORE
  PlayerHistoryIndex.history_as_of(player_id, date) per package -- strict `<` on
  game_date, never `<=`. Rolling means (5/10/20-game windows), opponent-allowed
  environments, H2H shrinkage -- reimplemented per package by convention (never a
  shared cross-import) but always the same strict-chronology contract.
    |
    v
MARGINAL MODELS
  Player SOG, Player SOG by Period, Goals, Assists, Points (empirical baseline),
  Blocked Shots, Team SOG, Goalie Saves, Team Goals by Period (not validated).
  Each: frozen weights in research/<prop>_results.json, never refit outside its own
  validation slice.
    |
    v
CONTEXT OVERLAY   <-- NEW this sprint, shadow-integrated
  research/context_overlay/prediction_stack.py::ShadowContextStack
  Applies ONLY to Goals 1+ and Points 1+, ONLY in COLD_AND_TOI_DECLINE state.
  logit(p_adj) = logit(p_raw) + offset (Goals) / p_adj = p_raw + shift (Points).
  Identity elsewhere. Tagged SHADOW_VALIDATED, never FULL_BET_POLICY.
    |
    v
LOGICAL COHERENCE LAYER
  Non-destructive Frechet/implication clipping (research/joint_scoring_dependence/
  logical_implication_registry.py + joint_models.py::clip_to_frechet). Enforces
  P(Goal>=1) <= P(Point>=1), P(Assist>=1) <= P(Point>=1), etc. Applied post-overlay
  too (verified 0 violations remain, both eval seasons).
    |
    v
JOINT DEPENDENCE LAYER  (only for multi-leg combinations)
  research/joint_shot_workload/ (SOG x Team SOG x Goalie Saves)
  research/joint_scoring_dependence/ (SOG x Goals x Assists x Points)
  Winner-per-combination architecture (naive / structural / empirical / copula) --
  never one dependence model forced onto every family.
    |
    v
CONSERVATIVE PROBABILITY   <-- documented, NOT wired into ShadowContextStack yet
  research/player_sog/count_models.py::conservative_mu -- one-sided normal-
  approximation haircut on the count-scale mu. Applies cleanly to props with a mu
  (SOG, Goals, Blocks, Team SOG, Goalie Saves). Points has no mu under the empirical-
  baseline champion -- a probability-domain conservative treatment for Points
  remains an open design question (CONTEXT_STATE_PROBABILITY_OVERLAY_REPORT.md
  Section AE).
    |
    v
MARKET PRICING   (live only for SOG today)
  pricing/odds_math.py -- american_to_prob, no_vig_two_way, expected_value,
  kelly_fraction, max_acceptable_price. research/live_sog_pricing/ -- the only
  family with a real, tested DraftKings/Odds-API payload contract.
    |
    v
DECISION POLICY
  research/player_props/decision_policy.py -- v3, unchanged. Narrows BET/WATCH
  downward for LOW-confidence Goals/Points/Assists/PLAYER_SOG_PERIOD_3. Never
  touches PASS/WAIT/DATA_UNAVAILABLE. Never imported by ShadowContextStack.
    |
    v
LEDGER   <-- NOT YET BUILT
  Four record types defined and demonstrated in dashboard_prototype/ only:
  REAL_BET, MODEL_OBSERVATION, HISTORICAL_RESEARCH, SHADOW_POLICY_OBSERVATION.
  No real persisted schema exists yet -- see PROSPECTIVE_VALIDATION_PROTOCOL.md.
    |
    v
DASHBOARD
  Streamlit, dashboard/pages/1-20. 17 RESEARCH, 2 infrastructure/ops, 1 OPERATIONAL
  (Live SOG Markets). A parallel static HTML prototype (dashboard_prototype/)
  demonstrates the target consolidated operational IA for review before a real
  Streamlit rebuild is scoped.
```

## Future / non-operational (explicitly out of scope this sprint)

```
FULL GAME SIMULATOR      -- NOT_BUILT. Readiness matrix in
                            PRESEASON_ENGINE_READINESS_REPORT.md Section AW.
GENERALIZED PARLAY ENGINE -- NOT_BUILT. Readiness requirements only (Part 112),
                            no optimizer code exists or was written.
FANTASY LAYER            -- NOT_BUILT, not requested, not planned.
```

## Design invariants (never violate)

1. A marginal's frozen weights file is read-only outside its own validation slice.
2. Every joint/context/overlay package reimplements its own thin marginal-provenance wrapper rather than importing a sibling's — deliberate, not an oversight.
3. PIT history uses strict `<` on `game_date`, never `<=`.
4. Fréchet/logical-coherence clipping is always non-destructive: it edits the *reported combination*, never a raw marginal file.
5. `decision_policy` only ever narrows an already-computed action; it never computes probability, edge, or EV itself.
6. `ShadowContextStack` never imports `decision_policy` — probability plumbing and policy are architecturally separate layers.
