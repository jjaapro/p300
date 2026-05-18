"""TIMING_ANOMALIES sleeve config.

The meta-sleeve itself has no signal parameters — those live in each
sub-strategy's own config module. This file holds:
  - The canonical list of sub-strategies that belong in this bucket
  - The variant-config shape (documented for operator reference)
"""

# Sub-strategies that belong in the timing_anomalies bucket. All are
# calendar/clock-driven edges; none have a microstructure (order-flow,
# positioning) mechanism. AI_QUANT (news-reactive), ADX (price-action),
# CARRY (funding), EMA (trend regime, also kept as a gate variable for
# microstructure sleeves), ETH_DAILY (continuous), SHORT_SQUEEZE
# (microstructure) are deliberately excluded.
CANONICAL_SUBSTRATEGIES = [
    "FOMC",
    "THU_BEAR",
    "PDO_L_RF",
    "CPR",
    "R4_BTC",
    "R4_ETH",
    "R4_BTC_V2",
    "R4_ETH_V2",
]

# Variant sleeve_cfg shape (for operator reference):
#
#   {
#     "strategy_id": "TIMING_ANOMALIES",
#     "weight_pct": 35.0,             # informational sum-of-children
#     "params": {
#       "substrategies": {
#         "FOMC":      {"enabled": True, "weight_pct": 5.0, "leverage": 10.0,
#                       "params": {"stop_loss_pct": 5.0}},
#         "THU_BEAR":  {"enabled": True, "weight_pct": 6.0, "leverage": 5.0,
#                       "params": {"version": "V4_event_conditioned",
#                                  "assets": ["BTC","ETH"],
#                                  "stop_loss_pct": 5.0}},
#         "PDO_L_RF":  {"enabled": True, "weight_pct": 9.0, "leverage": 1.0,
#                       "params": {"assets": ["BTC","ETH"], "gap_pct": 2.0,
#                                  "regime_threshold_pct": -10.0}},
#         "CPR":       {"enabled": True, "weight_pct": 5.0, "leverage": 1.0,
#                       "params": {"assets": ["BTC","ETH"]}},
#         "R4_BTC":    {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
#                       "params": {"asset": "BTC"}},
#         "R4_ETH":    {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
#                       "params": {"asset": "ETH"}},
#         "R4_BTC_V2": {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
#                       "params": {"asset": "BTC"}},
#         "R4_ETH_V2": {"enabled": True, "weight_pct": 0.0, "leverage": 1.0,
#                       "params": {"asset": "ETH"}},
#       }
#     }
#   }
