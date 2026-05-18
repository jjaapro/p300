# S-103 FOMC — Long BTC into FOMC announcement, filtered

LONG BTC from T-10h before each FOMC announcement to T+0.5h after, conditional
on a composite filter over Fed phase, F&G sentiment, and Polymarket-implied
cut probability.

## Trade window

- **Entry**: T-10h before announcement (announcement = 14:00 ET on the FOMC date).
- **Exit**: T+0.5h after announcement.

## Composite filter

Derived from 52 historical FOMC events 2020-2026:

| Phase × Action | Win% | Mean |
|---|---|---|
| peak_hold | 100% | +1.69% |
| hiking | 83% | +1.69% |
| zirp_hold | 79% | +1.17% |
| cutting | 70% | +1.26% |
| mid_hold | 25% | -0.70% |

| F&G at FOMC | Win% | Mean |
|---|---|---|
| Extreme Fear (≤25) | 100% | +2.40% |
| Extreme Greed (>75) | 40% | +1.18% |

Composite rule:
- HARD SKIP if `expected_action == 'cut_25'` (20% historical win rate).
- HARD SKIP if F&G == `extreme_greed` (40% win rate).
- HARD TRADE if F&G == `extreme_fear` AND phase ≠ `mid_hold` (8/8 wins).
- SKIP if `phase == 'mid_hold'` (25% win rate).
- TRADE otherwise.

## Inputs

- **Fed phase** (`zirp_hold / hiking / peak_hold / cutting / mid_hold`):
  classified from NY Fed target rate XML via `services/fed_funds_service.py`.
- **F&G**: alternative.me Fear & Greed via `services/sentiment_index_service.py`.
- **Expected action**: implied per-meeting cut probability from the
  Polymarket "How many Fed rate cuts in 2026?" market via
  `services/polymarket_service.py`.

(All three helper services will move to `strategies/support/` in restructure
step 6.)

## Audit trail

Every FOMC date writes a row to `fomc_observer` in `trader.db` with the
decision + reason + inputs, even when the decision is SKIP. Useful for
post-hoc verification: did the filter weed out the regimes where this
fails?

## Edge thesis

Short-window event trade. Drift up into the announcement, partial fade
after. Filter weeds out the regimes where this fails. Caveat: filter was
tuned on the same 52-event historical cohort the in-sample backtest is
drawn from — going-forward edge unproven.

## Files

- [signal.py](signal.py) — decision logic + observer tick + trade dispatch
- [config.py](config.py) — entry/exit offsets, window tolerance, cost, slippage
- `__init__.py` — package marker

No .pine reference: FOMC is event-driven, not chart-pattern based.
