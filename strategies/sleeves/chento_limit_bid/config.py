"""S-106 CHENTO_LIMIT_BID signal parameters.

Defaults are taken directly from
[studies/notebooks/swing_base_limit_bid/discovery.ipynb] where the +1/conf=3
MTF cell delivered +1.53R per signal (n=9) and the --+++ signature delivered
+0.53R (n=9). The numbers below should be re-validated by
chento_limit_bid_v1_backtest.ipynb before any live deployment.

DO NOT re-tune these without a sensitivity sweep + walk-forward — the
discovery notebook's findings sit on small samples and tuning into a peak
will overfit. Errors of judgement should bias toward conservative (looser)
thresholds, not aggressive (tighter) ones.
"""

# ─── Swing-base detection (mirror of BASE in discovery.ipynb) ──────────────

# Window in hours over which we search for the low cluster.
BASE_WINDOW_HOURS = 36

# Fraction of the window's bars that must sit within CLUSTER_BAND_PCT of the
# window low to qualify as a "base" rather than a one-touch wick.
BASE_CLUSTER_PCT = 0.40

# How close to the base low does a bar's low have to be to count as
# clustered? 1.2% of price.
BASE_CLUSTER_BAND_PCT = 0.012

# Forward-expansion gate: price must move at least this far up from the
# base low within EXPANSION_DAYS to confirm the base.
BASE_EXPANSION_PCT = 0.04
BASE_EXPANSION_DAYS = 3

# How far above the base low can the trigger bar still be and we'll fire?
# 1.2% — anything further is "past the base", not "approaching".
BASE_APPROACH_BAND_PCT = 0.012


# ─── Confluence score (mirror of SCORE in discovery.ipynb) ─────────────────

# Threshold for confluence_score (sum of 4 boolean flags) below which we
# don't trade. The discovery notebook showed monotonic expectancy with
# score, with the meaningful inflection at >= 3.
CONF_SCORE_MIN = 3

# Individual leg thresholds — each must fire for its bit to count.
BASIS_BP_MAX = -2.0        # futures discount: mean basis ≤ -2 bp during base window
FUNDING_MAX  = 0.0          # funding negative: mean funding < 0
OI_FLUSH_PCT = 0.015        # OI dropped by ≥ 1.5% during base window
# absorption: spot CVD > 0 — implicit in the score function (no threshold)


# ─── MTF bias gate ─────────────────────────────────────────────────────────

# Accept signals from the four most-profitable cells per
# studies/notebooks/swing_base_limit_bid/discovery.ipynb (long side):
# - mtf_net ∈ {+1, +2}: the moderate-up-stack sweet spot (+1.53R historical)
# - mtf_net ∈ {-3, -2}: capitulation-bounce in bearish HTF (+0.74R historical)
# Plus the exact --+++ signature as a special case.
#
# Cells we explicitly REJECT: +3, +4, +5 — these had NEGATIVE expectancy.
# "Everything bullish, buy the dip" is a losing trade on average.
# Refined after v1 backtest (2026-05-20):
# - net_-3 (deep counter-trend / capitulation): mean R +0.64 net, 43% TP rate
# - net_+1 (moderate up-stack): mean R +0.38 net, 36% TP rate
# - net_-2: mean R -0.02 (0% TP rate — never reaches 3R) — DROP
# - net_+2: mean R -0.49 (worst cell unexpectedly) — DROP
MTF_NET_ACCEPT = (-3, 1)          # only the two reliably profitable cells
MTF_NET_REJECT = (3, 4, 5)        # explicit rejects (overrides everything)
MTF_CAPITULATION_SIG = "--+++"    # always accepted regardless of net

# Multi-timeframe SMA periods and slope-lookbacks. Match discovery.ipynb.
MTF_DEFS = {
    "M":  {"period": 12, "slope": 3},
    "W":  {"period": 20, "slope": 4},
    "D":  {"period": 50, "slope": 5},
    "H4": {"period": 50, "slope": 12},
    "H1": {"period": 50, "slope": 24},
}


# ─── Time-of-day + day-of-week filter ──────────────────────────────────────

# UTC hour range when we accept new triggers. Widened from the original
# 12-17 NY-overlap window to 6-22 — the discovery notebook didn't bake a
# session filter in, and tightening to just 12-17 cut signal count by
# ~83%. Most chento entries land in the 12-20 UTC band; 6-22 keeps that
# without nuking the trade count.
TRIGGER_HOUR_MIN = 6   # inclusive
TRIGGER_HOUR_MAX = 22  # inclusive

# Days-of-week we accept new triggers. Mon=0 through Sun=6. Chento posts
# most heavily Mon/Tue/Wed (per text mining 2026-05-20) but DOES trade
# Thu/Fri/Sun — Sat is the only consistently dead day. So we exclude
# only Saturday.
TRIGGER_WEEKDAYS = (0, 1, 2, 3, 4, 6)  # Mon-Fri + Sun


# ─── Cooldown ──────────────────────────────────────────────────────────────

# Minimum elapsed wall-clock minutes between two consecutive triggers on
# the same variant. 24h prevents stacking near a base that's still active.
COOLDOWN_MIN = 60 * 24


# ─── Trade management ──────────────────────────────────────────────────────

# Stop placed below the base low.
STOP_OFFSET_PCT = 0.020  # 2% below base_low

# v2: staged take-profit replacing the v1 single 3R exit.
# Tier 1: small win takes pressure off — converts what would have been
# a stop-out (64% of v1 trades) into a small positive R when price visits
# 1R first. Mirrors chento's "Partial profits allowed only after 1R" rule.
T1_R = 1.0              # tier 1 trigger price = entry + 1R
T1_CLOSE_PCT = 0.333    # close 33% of original qty at T1

# Tier 2: the original 3R target. Closes ~50% of remaining (= 33% of original).
T2_R = 3.0
T2_CLOSE_PCT = 0.500    # close 50% of remaining qty at T2

# Runner trail: armed once T1 hits. Exits the final ~34% on a high-water
# trail. Chento's framework lets a fraction of the position run for the
# tail. 5% from peak matches his observed trail behavior on the
# 2026-04-23 mega-short ("Added some margin for liq" then ride).
TRAIL_PCT = 0.05        # trail 5% under running high-water mark
RUNNER_ARMED_AFTER = "T1"  # arm trail after T1 hits (alternative: after T2)

# Time stop: close at market if neither full TP path nor stop hits within
# N days. Widened from v1 (14d) because chento holds for weeks.
TIF_DAYS = 21


# ─── Execution costs (matches DEFAULT_* in strategies.trades) ──────────────

# 5 bp entry + 5 bp exit on BTC perps (taker).
COST_BP_RT = 10.0

# Round-trip slippage. Bumped vs default because we don't actually use
# limit-bid in v1 — we market-buy when the swing base is approached, which
# eats more spread than a true limit order would.
SLIPPAGE_BP_RT = 8.0


# ─── Tick model ────────────────────────────────────────────────────────────

# Only do real work at 15m bar boundaries — same approach as SHORT_SQUEEZE.
# Sweep loop still runs every tick (1m) so exits don't wait for 15m.
EVAL_AT_15M_BOUNDARY = True
