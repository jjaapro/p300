# chento journal — findings log

Running list of insights from reverse-engineering the chento Discord journal.

## Phase 1 — full Q2/Q3 2024 ledger (2024-06-09 → 2024-09-27)

156 image-bearing posts → 48 message clusters → **29 unique trade lifecycles**.
Saved to [../../material/chento/phase1_trades.jsonl](../../material/chento/phase1_trades.jsonl)
(52 records: 35 position cards, 4 TV charts, 3 account balances, 4 social embeds,
1 IRL photo, 5 skip-marked low-priority misc).

### Asset breakdown

| Asset | Unique trades | Notes |
|---|---|---|
| **OPUSDT** | 23 (79%) | Bootstrap asset, dominant June–August |
| **BTCUSDT** | 6 (21%) | Starts 2024-08-15, **100% shorts** |

Confirms the "alts to bootstrap" hypothesis emphatically. He didn't touch BTC
for the first 9 weeks of the journey; OP was the entire bankroll engine.

### Leverage distribution

| Leverage | Count |
|---|---|
| 10x | 3 |
| **15x** | **1** (the multi-day BTC swing short) |
| **20x** | **16** (default — most common) |
| 30x | 3 |
| 49x | 1 |
| 50x | 3 |
| 75x | 1 |
| **125x** | **1** (the "Pray for me" BTC short) |

Pattern: 20x is his default, 10x when explicitly cautious ("Not risking much
here max 20"), 30–50x on high-conviction OP setups, **75x and 125x on BTC
shorts** by late September 2024 — increasing aggression as bankroll grew.

### Direction bias

Counted from the unique-trades table:

- **OP**: 8 longs + 15 shorts → **65% short-biased on alts** during this window
- **BTC**: 0 longs + 6 shorts → **100% short-biased on BTC** Aug–Sept 2024

(BTC was rangebound 55-65k during this period; he was structurally bearish.
This will be testable later when we cross-reference vs the MTF cell map.)

### Take-profit horizons (by trade)

Visible TPs in the ledger:

| Range from entry | Count | Behaviour |
|---|---|---|
| < −10% (shallow / scalp) | 4 | Tighter TPs on counter-trend bounces |
| −10% to −25% (mid swing) | 4 | Mostly his BTC shorts |
| > −25% (deep swing) | 6 | OP shorts targeting major dumps (TP 0.9, 1.0, 1.1, 1.2) |
| > +25% (deep long) | 2 | OP long bottom-fish (TP 1.8 from 1.14 = +58%) |
| Not set / "manual" | rest | Discretionary close |

Roughly half his trades target moves > 20%. Confirms the **position-trader
profile** + cross-validates the "trader holds for weeks" memory.

### Hold-time observation (the BTC short anatomy)

Only one trade in the sample had enough screenshots to reconstruct the
lifecycle, but it's exemplary:

- **08-12 09:07**: open BTC short @ 58,437.3 / 15x / TP 43,000 (caption *"200$ where we left.... 43k will finish"*)
- **08-12 17:06**: +7.64% (*"Why panic?"*)
- **08-14 17:12**: **−10.65%** (*"I hold till the damn end..... get out now weak heart[s]"*) ← drawdown
- **08-15 09:20**: +6.61% (*"Haha"*)
- **08-15 21:13**: +31.03% (*"Gg"*)
- **08-19 12:55**: +11.93%, **TP lowered 43k → 38k**, partial trim of $0.05 realized

So: **held through −10.65% drawdown for 7+ days**, took partial profit,
lowered TP target as conviction softened. Textbook position trade.

### Account-balance trajectory

| Date | Total assets | Δ |
|---|---|---|
| 2024-06-09 | ~$200 (start) | — |
| 2024-06-11 | ~$130 (text caption "Port 130") | **−35%** initial drawdown |
| 2024-06-14 | **$578** | +189% from $200 |
| 2024-06-18 | + $521 single-trade win | massive single hit |
| 2024-06-19 00:34 | **$3,382** | +485% in 5 days |
| 2024-06-19 11:20 | **$4,124** | +22% in 11h |

The June 14 → June 19 leg is the bankroll being made — driven entirely by
OPUSDT leveraged positions on the alt's high volatility. The "DROPS THE FKING
MIC" OP short on 06-18 added $521 to the account on a single trade.

### Multi-exchange

| Exchange | First seen | Margin mode |
|---|---|---|
| MEXC | 2024-06-10 (start) | Cross |
| Bybit | 2024-09-03 | Isolated |

Bybit appears just once in the Q2/Q3 sample (the 2024-09-03 BTC short). Most
of this era is MEXC.

### TradingView analysis posts

4 of 35 trade-relevant posts in the Q2/Q3 window are TradingView chart
commentary (not entries):

- 2024-07-07 (BTC): SMC orderblocks + reversal projection
- 2024-07-15 (BTC): Fib retracement + Buy/Sell SMC markers
- 2024-07-25 (BTC): Multiple daily/4h OBs stacked overhead
- 2024-07-29 (BTC): Pink OB resistance zones

All are **BTC-focused** analysis posts even when his book is dominantly OP.
He thinks BTC structure, trades OP structure.

### Skip-marked clusters (worth re-reading later)

5 clusters were skip-marked in this pass (caption-only assessment):

| Cluster | Date | Caption | Why skipped |
|---|---|---|---|
| C36 | 2024-08-26 | "How to Easily Win the $100..." | Promotional/community post |
| C39 | 2024-09-02 | "Fking discord messing" | Caption suggests technical issue, likely position card |
| C41 | 2024-09-04 | "TRICKY" | Brief, content unknown |
| C42 | 2024-09-05 | "Fml" | Brief, likely a losing position |
| C43 | 2024-09-06 | "Aged well" | Likely a winning chart in retrospect |

Worth re-reading in a follow-up if the late-Sept window matters for
cross-reference work (the 75,485 hypothesis is much later, so these may not).

## Implications for the cross-reference work

1. **OP data is in place** — `op_perp_1m` table backfilled 2022-08 → 2026-05.
   Cross-validation of the 2024-06-18 OP short confirmed the pipeline works
   end-to-end (−13.31% spot move, +133% theoretical margin return at 10x).
2. **BTC subset of his Q2/Q3 trades is small (6)** but he's 100% short. Will
   be useful for cross-checking the **short-side MTF cells** from
   `swing_base_limit_bid/discovery.ipynb`.
3. **More alt data needed** — for late-2024 onwards he diversifies. SOL,
   AVAX, DOGE, ARB, INJ, WIF backfills already prepped in
   `studies/notebooks/chento_journal/fetch_alts.py`.
4. **Phase 2 (chart-era) extraction still pending** — the Q4 2025 → present
   period uses TradingView screenshots, not position cards. The 75,485
   hypothesis is in that era. Different vision schema required.

## Next steps

- [ ] Run cross-reference on each of the 6 BTC shorts in this phase — was each
      entry near a swing high in our base-detector terms? Which MTF cell?
- [ ] Backfill SOL, AVAX, DOGE (next-tier alts he likely trades)
- [ ] Re-read the 5 skip-marked clusters if late-Sept BTC analysis matters
- [ ] Move to phase 2 (chart-era, 2025-Q4 → present) when bandwidth allows
