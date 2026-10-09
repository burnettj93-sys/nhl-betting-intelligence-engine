"""Dependency-free pricing policy constants shared by the engine, the product state and the hosted pages."""

# Walk-forward 2025-26 (docs/validation/low_sample_calibration.json): the calibrated skater model still over-predicts with 5-39 prior
# NHL games (points>=1 predicted 26-28% vs 24% observed; shots>=2 32-34% vs 28-30%) and is calibrated from 40 (27.5% vs 27.1%).
# Below this a player gets no model-priced recommendation.
MIN_GAMES_FOR_PRICING = 40

# A price that has aged out of the freshness limit may still be SHOWN as provisional this long after the provider last set it, if it was retrieved today (Eastern). Not fresh means not
# usable: nothing can be recorded or added on it (operational/daily_tickets.revalidate_before_recording; operational/manual_orders.revalidate).
PROVISIONAL_MAX_AGE_MIN = 14 * 60.0
