"""
Every tunable threshold in one place. None of these values are "correct" —
the spec is explicit (sections 37/50/56/57) that thresholds and feature
weights must be optimized through walk-forward backtesting, not set once
and trusted. Treat everything here as a starting guess to be recalibrated
once you have real DraftKings odds history to backtest against.
"""

# v2.1.2 (spec item 9): bumped from the old "v2-temporal-2026.08" marker
# -- that single identifier no longer distinguished the several
# materially different architecture/correctness revisions since (v2.1,
# v2.1.1, v2.1.1a, v2.1.2), which matters for auditability: a persisted
# prediction's model_version/feature_version is what lets a later
# reader/reproduce() call know exactly which code produced it. No model
# semantics were changed to force this bump -- it's purely an identifier
# update; see tests/test_reproducibility.py, which reads this
# dynamically via config.MODEL_VERSION rather than a hardcoded string,
# so this bump requires no test changes.
# v2.1.2a: bumped again for the same reason -- this slice's fixes
# (real boxscore SOG-field mapping, current-vs-season roster separation,
# per-response live-observation timestamping, live contract hardening)
# are ingestion/validation-layer changes, not model-probability-math
# changes, but the identifier still needs to distinguish "predictions
# made under the v2.1.2 ingestion code" from "predictions made under the
# v2.1.2a ingestion code" for the same auditability reason.
MODEL_VERSION = "v2.1.2a"
FEATURE_VERSION = "v2.1.2a"

# --- Reference sportsbook (see features/point_in_time.py, pricing/engine.py) ---
# DraftKings is the engine's exclusive EXECUTION/REFERENCE sportsbook —
# the only book any BET/WAIT/PASS decision is ever priced against.
REFERENCE_SPORTSBOOK = "DraftKings"
# v2.1: placeholder for a future, explicitly NOT-yet-implemented distinction
# between the execution/reference book above and other licensed books used
# only as MARKET-INTELLIGENCE signals (consensus, lead/lag, sharp
# movement, DraftKings-staleness-relative-to-market). Kept empty and
# unused this slice — see schema.sql's odds_snapshots comment. Do not
# start using these for pricing without a deliberate, separate decision.
MARKET_INTELLIGENCE_SPORTSBOOKS: list[str] = []

# a DK price older than this vs. prediction_time is stale and rejected.
# MAX_ODDS_STALENESS_MINUTES is now used only as an explicit CALLER
# OVERRIDE (pass max_staleness_minutes= directly) or as a synthetic-data /
# ad hoc fallback; the actual default policy used by
# pricing/engine.py::evaluate_moneyline_for_game is the DYNAMIC,
# time-to-puck-drop-sensitive ODDS_STALENESS_TIERS below (v2.1) — a quote
# that's fine a day out is dangerously stale 10 minutes before puck drop,
# and a single static window can't express that.
MAX_ODDS_STALENESS_MINUTES = 180
# (lower_bound_hours_to_puck_drop, max_allowed_quote_age_minutes), checked
# top-down; the first tier whose lower bound the current hours-to-puck-drop
# meets or exceeds wins. See pricing/odds_math.py::dynamic_max_staleness_minutes.
ODDS_STALENESS_TIERS = [
    (6.0, 60.0),     # > 6 hours out: up to 60 min old
    (2.0, 30.0),     # 2-6 hours out: up to 30 min old
    (0.5, 10.0),     # 30 min - 2 hours out: up to 10 min old
    (10.0 / 60.0, 3.0),   # 10-30 min out: up to 3 min old
    (0.0, 1.0),      # < 10 min out: up to 1 min old
]
# Missing/stale/suspended DraftKings data => DATA_UNAVAILABLE / WAIT. This
# must stay False unless a human deliberately re-configures it — see
# pricing/engine.py's ALLOW_SPORTSBOOK_FALLBACK docstring.
ALLOW_SPORTSBOOK_FALLBACK = False

# --- Elo team-strength model (models/elo_model.py) ---
ELO_START = 1500.0
ELO_K_FACTOR = 20.0         # rating points transferred per game result
ELO_HOME_ADVANTAGE = 35.0   # rating-point bump for the home team
ELO_SEASON_REGRESSION = 0.30  # fraction of distance-to-mean reverted at season start
# Deliberate choice (spec ask, item 7): Elo updates on the BASE Elo
# expectation (home rating vs away rating + home ice only), NOT the fully
# adjusted pregame probability (which also folds in player availability,
# goalie, and rest). Rationale: Elo is meant to track durable team
# strength over a season; updating it on a probability that already
# absorbed "our backup goalie played" would double-count that information
# every time it recurs and make the team rating itself goalie-dependent.
# See models/elo_model.py:update() and tests/test_elo_update_rule.py.
ELO_UPDATES_ON_BASE_EXPECTATION = True

# --- Rest / fatigue adjustment (features/point_in_time.py) ---
# Rating-point penalties. Sec.12: "don't assume fatigue automatically
# matters — test it." These are placeholders pending that test.
BACK_TO_BACK_PENALTY = 12.0
THREE_IN_FOUR_PENALTY = 6.0
FOUR_IN_SIX_PENALTY = 8.0

# --- Player-availability adjustment (models/player_model.py) ---
# Converts a player's rolling rating (points-per-game EWMA vs. league
# average) into Elo-equivalent rating points. This is a v1 heuristic
# comparative-quality signal, NOT RAPM/GAR/xGAR or a complete player-
# impact model — see models/player_model.py's docstring.
PLAYER_RATING_EWMA_ALPHA = 0.10
# 1 PPG above league-average skater ≈ this many Elo pts. Lowered from an
# initial guess of 55 to 20 after backtest.py's baseline comparison showed
# 55 made elo_plus_player calibrate WORSE than elo_only (Brier 0.2313 vs
# 0.2248) — the per-player EWMA rating is noisy on a small-sample synthetic
# league, and at too high a weight that noise swamped real signal. At 20
# the two are roughly on par (~0.225 either way). This is exactly the kind
# of thing spec sec.56's Feature Value Report is for — re-tune this against
# real data rather than trusting either number.
POINTS_PER_GAME_TO_ELO = 20.0
MIN_GAMES_FOR_FULL_PLAYER_WEIGHT = 10  # below this, shrink toward 0 impact

# --- Goalie adjustment (models/goalie_model.py) ---
SAVE_PCT_TO_ELO = 2500.0        # 1.0% above league-average save% ≈ this many Elo pts
GOALIE_SHRINKAGE_STARTS = 15    # shrinkage denominator; more starts = less regression
UNCONFIRMED_GOALIE_UNCERTAINTY_WIDENING = 1.4  # multiplies CI width if goalie unconfirmed
# Spec item 4: by default the engine will NOT bet on an EXPECTED (not yet
# CONFIRMED) starter — it returns WAIT. Set True only as a deliberate,
# explicit policy choice (and expect wider uncertainty applied either way).
ALLOW_BETTING_ON_EXPECTED_STARTER = False

# --- Uncertainty / conservative probability (models/combined_model.py) ---
# IMPORTANT (v2.1 rename + disclaimer, spec item 13): this is a HEURISTIC
# maturity-based uncertainty band, NOT a statistically validated confidence
# interval. It narrows as both teams accumulate games this SEASON (reset
# at each season boundary — see CombinedMoneylineModel._maybe_new_season)
# and has never been calibrated against real forecast error. Do not
# present ci_low/ci_high (kept as those names on GamePrediction/schema.sql
# for API/schema compatibility — see models/combined_model.py's docstring)
# as a real 95% CI in any user-facing report. TODO (deferred, not this
# slice): replace this with empirical out-of-sample uncertainty once real
# NHL + DraftKings historical data exists — ideally as a function of
# historical forecast error by probability bucket, season maturity, goalie
# certainty, lineup certainty, player-data maturity, and model/market
# disagreement. See BetReport.format()'s output note.
BASE_UNCERTAINTY_BAND_HALF_WIDTH = 0.09
MIN_UNCERTAINTY_BAND_HALF_WIDTH = 0.02
UNCERTAINTY_BAND_GAMES_TO_MATURITY = 40   # games played this season before the band hits its floor

# --- Betting zones (pricing/engine.py) — spec section 50, informational labeling only ---
EDGE_GREEN = 0.05
EDGE_LIGHT_GREEN = 0.03
EDGE_YELLOW = 0.01
# below EDGE_YELLOW (or negative) => RED

# --- Distinct betting-decision thresholds (spec item 9) ---
# These are DIFFERENT quantities and must not be conflated:
#   conservative_edge  = conservative_probability - market_no_vig_probability
#                        (a probability-point gap)
#   expected_value     = conservative_probability * decimal_odds - 1
#                        (a % return at the actually-offered price)
# BET requires BOTH to clear their own minimum — a big edge at a price with
# thin EV (or vice versa) is not enough on its own.
MIN_CONSERVATIVE_EDGE = EDGE_LIGHT_GREEN   # 3.0 probability points
MIN_EV = 0.02                              # 2.0% expected return

# --- Staking (pricing/engine.py) — spec section 59/61 ---
KELLY_FRACTION_MULTIPLIER = 0.25   # 25% Kelly, never full Kelly
MAX_SINGLE_BET_BANKROLL_PCT = 0.02
