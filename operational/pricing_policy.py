"""Dependency-free pricing policy constants shared by the engine, the product state and the hosted pages."""

# Walk-forward 2025-26 (docs/validation/low_sample_calibration.json): the calibrated skater model still over-predicts with 5-39 prior
# NHL games (points>=1 predicted 26-28% vs 24% observed; shots>=2 32-34% vs 28-30%) and is calibrated from 40 (27.5% vs 27.1%).
# Below this a player gets no model-priced recommendation.
MIN_GAMES_FOR_PRICING = 40

# A morning/midday price may be SHOWN as provisional this long after the provider last set it, if it was retrieved today (Eastern). It is never recordable
# (operational/daily_tickets.revalidate_before_recording): a ticket is recorded only on a price retrieved inside its game's pregame window.
PROVISIONAL_MAX_AGE_MIN = 14 * 60.0
