"""
SIMULATED research harness -- not part of the product.

These modules build a deterministic *simulated* betting board (synthetic prices, synthetic roster, simulated market movement) that
the combination / Game Edge Parlay research code and its tests exercise. Nothing in `dashboard/`, the published snapshot or the paper
account imports this package; the hosted app and every registered page read observed data only. It is kept so the research code
that depends on it keeps its tests, and is labelled here so nobody mistakes it for a data source.
"""
