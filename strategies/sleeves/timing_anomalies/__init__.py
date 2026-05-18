"""TIMING_ANOMALIES — meta-sleeve consolidating calendar/clock edges.

Single sleeve at the orchestrator level that internally dispatches to
multiple sub-strategies (FOMC, R4, THU_BEAR, PDO, CPR) on every tick.
Each sub-strategy retains its own signal logic; the meta-sleeve provides
unified allocation budget + reconciliation + composition.

See README.md for the architectural rationale.
"""
