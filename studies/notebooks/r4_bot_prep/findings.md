# R4 bot prep — findings (2026-09-06)

STATUS: CONCLUDED per pre-registration (README.md). `STOP_LOSS_PCT = None`,
`LATE_ENTRY_MAX_S = 300`. Both values go into `bots/r4/config.py`; the stop-loss deviation
from the per-sleeve SL policy is recorded in `docs/calibration/r4.md`.

Re-run: `venv\Scripts\python studies/notebooks/r4_bot_prep/sl_sweep.py` (≈2 min, read-only on
prod.db; writes `results/sl_sweep.csv`, `results/late_entry.csv`, `results/sl_fires.csv`).
Viewer: `r4_bot_prep.ipynb` (built by `build_notebook.py` with the system Python).

## Test 1 — intraday stop-loss: no level passes

Net bp per fire (15 bp RT), windows as shipped, fills at 1m opens, stop filled at the stop level.

| window | era | n | none | 5% (Δmean / worst shrink) | 3% | 2% |
|---|---|---|---|---|---|---|
| R4_BTC | post-ETF | 62 | +58.1 | −18.5 / −7% | −23.1 / +35% | −36.9 / +55% |
| R4_BTC | pre-ETF | 98 | +6.7 | −0.8 / +25% | +1.3 / +54% | +2.5 / +69% |
| R4_BTC_V2 | post-ETF | 127 | +24.1 | −0.1 / −2% | −0.9 / +38% | −3.8 / +57% |
| R4_BTC_V2 | pre-ETF | 194 | +31.7 | −11.6 / +17% | −15.3 / +49% | −26.9 / +65% |
| R4_ETH | post-ETF | 63 | +123.8 | −69.0 / +12% | −51.7 / +46% | −62.9 / +63% |
| R4_ETH | pre-ETF | 97 | +89.0 | −17.6 / +50% | −49.4 / +69% | −47.1 / +79% |
| R4_ETH_V2 | post-ETF | 127 | +31.1 | +0.4 / +24% | +0.1 / +54% | +1.7 / +68% |
| R4_ETH_V2 | pre-ETF | 194 | +45.0 | −11.8 / +32% | −14.8 / +59% | −27.3 / +72% |

Rule: adopt a level only if in BOTH eras Δmean ≥ −5 bp AND worst-loss shrink ≥ 30 % on every
window. No level satisfies this on any window in both eras (the closest, R4_ETH_V2, fails
pre-ETF on expectancy). Stop-hit rates run 10–50 % at 2 %, which is why expectancy collapses:
the 10–24 h windows routinely dip 2–3 % before the drift completes, the same mechanism the
trader repo found for PDO. Worst single fires without a stop are −4.8 % (BTC) to −10.2 % (ETH
pre-ETF, 24 h hold) gross; at the bot's ≤ 1.5× capital per position (`VARIANT_WEIGHT` 0.20 ×
`LEV_CAP` 7.5) that is a −7.2 % to −15.3 % day on the variant, bounded by the scheduled exit.
This is the accepted deviation from the SL policy.

> **Correction and added aggregate measurement (2026-09-09, after independent verification).**
> The original text said "≤ 1.9× capital" (untraceable; the config gives 1.5×) and "the 2×/day
> maximum concurrent windows bound the aggregate". **The maximum is three, not two**, and the
> aggregate was asserted rather than measured. R4_ETH V1 runs Tue 20:00 → Wed 20:00 and so
> overlaps *both* V2 fires (Wed 04:00 → 14:00); `bots/r4/config.py` documents this itself.
> Measured over the 962 no-stop fires, mapping each to its own window:
>
> | | worst overlapping cluster | no stop | 2 % stop | 3 % stop | 5 % stop |
> |---|---|---|---|---|---|
> | pre-ETF | 2022-11-08, 3 legs (FTX week) | **−21.0 %** | −6.5 % | −9.5 % | −15.5 % |
> | post-ETF | 2026-06-05, 2 legs | **−15.2 %** | −6.5 % | −9.5 % | −12.7 % |
>
> (percent of variant capital, after the 3.0× `GROSS_MAX_X` cap scales the legs down; 97
> pre-ETF and 63 post-ETF three-leg clusters occur in the sample.)
>
> The pre-registered rule was written on the **worst single fire** and is silent on clusters,
> so this does not change the verdict it produced — but it does mean the no-stop decision
> carries a measured ~15 % single-day variant drawdown in the era the bot actually trades,
> against ~9.5 % under a 3 % stop. That is a risk-appetite call for the operator, not a
> re-reading of the sweep. `STOP_LOSS_PCT` was left at `None` as calibrated; changing it needs
> an explicit go-ahead and a new pre-registered study.

## Test 2 — late entry: 5-minute grace

Post-ETF loss versus entering at the open (net bp): at 5 min BTC −1.8 (a gain), BTC_V2 +1.6,
ETH +4.3, ETH_V2 +1.6 → all ≤ 5 bp. At 10 min ETH loses 11.3 bp and at 15 min 8.9 bp, so the
rule stops at 5 minutes → `LATE_ENTRY_MAX_S = 300` (also the rule's floor). The curve is
noisy (R4_BTC post-ETF is *better* at +30 min; pre-ETF R4_BTC loses ~6 bp at +30/60 min), so
this is not evidence that lateness is costly in itself; it is evidence that there is nothing
to gain by tolerating it. With a 60 s tick a healthy bot enters within ~1 min; a restart more
than 5 min into the window logs `missed_window` and waits for the next day.

## DSR ledger

10 trials against the R4 family (4 stop levels + 6 lateness points), tagged calibration.
No parameter was changed after seeing results.

---

## Addendum (2026-09-09) — stops make the drawdown worse, not just the expectancy

The pre-registered rule tested expectancy and the worst *single* fire. Re-reading
the same `results/sl_fires.csv` on portfolio metrics answers the obvious follow-up
("would a stop at least cut the drawdown?") and the answer is no.

Post-ETF, all four windows, 380 fires:

| stop | win % | profit factor | mean %/fire | ann % | max DD % | MAR | fires stopped | worst fire |
|---|---|---|---|---|---|---|---|---|
| **none (shipped)** | 56.8 | **1.76** | **+0.482** | 69.1 | **31.6** | **2.19** | 0 % | −6.78 % |
| 5 % | 55.8 | 1.47 | +0.339 | 48.5 | 46.1 | 1.05 | 3.9 % | −5.15 % |
| 3 % | 54.7 | 1.52 | +0.356 | 51.0 | 46.2 | 1.11 | 10.8 % | −3.15 % |
| 2 % | 52.6 | 1.45 | +0.311 | 44.6 | 48.1 | 0.93 | 25.3 % | −2.15 % |

Full sample is the same shape: no stop reaches MAR 0.89 against 0.51 to 0.71 for
every stop level.

A stop does exactly what it is supposed to do to the tail — the worst fire goes
from −6.78 % to −2.15 % at a 2 % stop — and still leaves the book *deeper* in
drawdown, by about 15 percentage points post-ETF. The mechanism is that these are
scheduled-exit drift harvests on 10 to 24 hour windows: the price routinely dips
through the stop and recovers before the window closes, so the stop converts
recoverable excursions into realised losses and forfeits the recovery. Stop-hit
rates of 25 % at the 2 % level against a 57 % win rate say the same thing.

This does not reopen the pre-registered decision, which already said no stop. It
removes the one argument that could have overturned it, and it means the
overlapping-cluster exposure recorded above cannot be managed with a per-fire
stop. If that exposure needs managing, the dials are the sizing ones
(`VARIANT_WEIGHT`, `LEV_CAP`, `GROSS_MAX_X`), not a stop.
