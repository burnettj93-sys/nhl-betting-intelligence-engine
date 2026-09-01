"""Fantasy projection layer: converts the existing NHL Intelligence
Engine's real model outputs into league-specific fantasy category
projections. Never uses betting decision_policy/EV/no-vig probability
as fantasy value -- fantasy has its own value layer (see
recommendations/)."""
