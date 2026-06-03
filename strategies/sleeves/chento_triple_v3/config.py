"""CHENTO_TRIPLE_V3 sleeve configuration.

All parameters derived from the validation work in
[studies/notebooks/chento_journal/](../../../studies/notebooks/chento_journal/).
Key memories:
  - project_chento_triple_optimized_config.md  (the full stack)
  - project_chento_a4_ladder_finding.md         (A4 ladder tiers)
  - project_cross_exchange_okx_gate.md          (OKX delta gate)
  - project_chento_adaptive_hybrid.md           (H_B classifier)
  - project_chento_regime_filter.md             (asymmetric up_30d filter)

Do NOT tune these without re-running validation. Per [[wider-tp-same-stop-is-better]]
and [[tif-72h-optimal]], several were counter-intuitive.
"""

SLEEVE_NAME = "CHENTO_TRIPLE_V3"

# ─── Asset ─────────────────────────────────────────────────────────────────
ASSET = "BTC"

# ─── Math layer ────────────────────────────────────────────────────────────
ATR_PERIOD = 14                  # 14-bar ATR on 15m
ATR_STOP_MULT = 5.0              # initial stop = entry ± 5×ATR
TARGET_R = 6.0                   # 6R fixed target — see [[wider-tp-same-stop-is-better]]
TIF_HOURS = 72                   # 72h hold — see [[tif-72h-optimal]]
COST_BP_RT = 18.0                # 18bp round-trip, scaled by stop_distance in math
SLIPPAGE_BP_RT = 0.0             # included in COST_BP_RT

# ─── B1 money-flow divergence ──────────────────────────────────────────────
B1_CVD_WINDOW_BARS = 4 * 24 * 30   # 30d at 15m
B1_VEL_WINDOW_BARS = 4              # 1h velocity
B1_CVD_Z_THRESHOLD = 0.5            # |cvd_z| > 0.5 needed
B1_VEL_Z_MAX = 1.0                  # |vel_z| < 1.0 (price not yet reacting)

# ─── B5 LSR extremes ───────────────────────────────────────────────────────
B5_ROLLING_DAYS = 30                # 30d percentile window
B5_LO_PCTILE = 10                   # bottom 10% = oversold longs → SHORT trigger
B5_HI_PCTILE = 90                   # top 10% = euphoric longs → no trigger

# ─── B7 multi-TF CVD alignment ─────────────────────────────────────────────
B7_TIMEFRAMES = ("1h", "4h", "1d", "3d")
B7_Z_THRESHOLD = 2.0                # median |z| > threshold w/ all 4 TFs same sign

# ─── Triple intersection window ────────────────────────────────────────────
# Research's validation_B_composite.intersect_triggers uses WINDOW_HOURS=24
# (bidirectional ±24h). Production replays the same idea backward-only:
# at each bar, the triple is "complete" if B1, B5, AND B7 all fired
# same-direction within the trailing TRIPLE_WINDOW_HOURS. Fixed 2026-05-31
# after diag revealed 0h tolerance was the dominant trigger-divergence bug.
TRIPLE_WINDOW_HOURS = 24

# ─── Filter gates ──────────────────────────────────────────────────────────
# Filter 1: no-tilt (consec_losses_before == 0)
FILTER_NO_TILT = True

# Filter 2: no_resist_OB_within_2R
FILTER_NO_RESIST_OB = True
SMC_PIVOT_N = 5                     # 5-bar pivot detection
SMC_OB_WITHIN_R = 2.0               # skip if opposite OB within 2R

# Filter 3: OKX-Binance perp delta z-score aligned with direction
FILTER_OKX_ALIGNED = True
OKX_DELTA_WINDOW_HOURS = 24 * 7     # rolling 7d window for z-score
OKX_ALIGN_Z_MIN = 0.0               # require z ≥ 0 (or ≤ 0 for shorts)

# Filter 4: asymmetric regime filter — skip ONLY shorts in up_30d
# 2026-06-03 RE-ENABLED after Stage 1 verification of the disable attempt:
# the disable hypothesis (from a faulty audit) was that the filter blocked
# research's Jul 23 04:15 SHORT (+2.59R). Direct cache inspection showed
# B5's backward-window short was False at Jul 23 04:15 — production's
# B1-anchored triple never fires there REGARDLESS of the filter. Disabling
# the filter only unblocked Jul 26 SHORT losers (−1.57% / −0.85%) that
# fire when B1+B7 align in up_30d regimes. Stage 1 6mo backtest: cum
# +$783 (post-disable) vs +$904 (pre-disable) → net −$121, WR 50% vs
# 62.5%. The filter is empirically net-POSITIVE in current regime; keep
# it ON. See memory/project_chento_regime_filter.md for full re-eval.
FILTER_SKIP_UP_30D_SHORTS = True
UP_30D_THRESHOLD = 0.10             # BTC 30d return > +10% = "up_30d" regime

# ─── A4 ladder (adaptive sizing via H_B) ───────────────────────────────────
LADDER_ENABLED = True
LADDER_ADV_TRIGGER_R = 0.3          # ladder fires at -0.3R adverse excursion
LADDER_T1_SIZE_FRAC = 0.5           # 50% add for outside-VA (T1)
LADDER_T3_SIZE_FRAC = 1.5           # 150% add for inside-VA (T3)
LADDER_POST_STOP_R = 1.5            # combined stop widens to -1.5R from original entry

# ─── C6 volume profile classifier (the H_B VA classifier) ──────────────────
VP_WINDOW_DAYS = 7                  # 7d rolling — see [[chento-adaptive-hybrid]]
VP_N_BINS = 50
VP_VALUE_AREA_PCT = 0.70            # contiguous bins covering 70% of volume

# ─── Cooldown ──────────────────────────────────────────────────────────────
# Research's validation_B1_moneyflow_divergence.b1_triggers applies
# cooldown_bars=4*6=24 (6h) at the B1 level. With B1-anchored triple,
# matching this cooldown at the sleeve level prevents B1 re-fires within
# the same confluence from emitting duplicate trades.
COOLDOWN_HOURS = 6                  # minimum gap between triggers (was 4 pre-B1-anchor)

# ─── Diagnostic flags ──────────────────────────────────────────────────────
# Trigger-window gate (UTC). Optional restriction; mean-reversion isn't strongly
# session-dependent, but Asia hours have higher expectancy per loser_profile.
TRIGGER_HOUR_MIN = 0
TRIGGER_HOUR_MAX = 23
TRIGGER_WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)   # all days
