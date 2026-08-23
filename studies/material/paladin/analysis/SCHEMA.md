# Paladin analysis pack — schema and join recipe

Everything here is derived from one DiscordChatExporter JSON of `#paladin` (ScalpX),
1,005 messages, 2026-05-06 → 2026-08-21, plus the 320 screenshots he posted.
Five tables, each in **`.parquet` / `.csv` / `.jsonl`** (identical content).

## ⚠️ Read this first: the timestamp fix

The Discord export stamps everything **Europe/Helsinki (+03:00)**. The earlier
deliverables in this project (`positions_timeline.csv`, `events_timeline.csv`,
`trade_timeline.json`) carry **local time with the offset stripped** — they are
silently 3 hours ahead of UTC. Joining those directly to OHLCV puts every signal
three hours late.

Every timestamp in **this** pack is UTC, recomputed from the original offsets:
`*_time_utc` is ISO-8601 with a trailing `Z`, `*_ms` is epoch milliseconds.
Use this pack for anything that touches price data, and the earlier files only
for reading.

Verified: message 5 is `2026-05-07T02:34:43.759+03:00` → `signal_time_utc = 2026-05-06T23:34:43Z`.

---

## `positions.csv` — 217 rows, 55 columns

One row per trade, from the call to the exit.

**Identity and timing**

| column | notes |
|---|---|
| `position_id` | unique; two colliding ids in the source were suffixed `-2` |
| `symbol` `base` `quote` `asset_class` | `asset_class` is `crypto` (208), `metal` (8, GOLD/XAU), `commodity` (1, OIL) |
| `side` `side_sign` | `side_sign` is +1 long / −1 short — multiply by it and stop writing branches |
| `signal_time_utc` `signal_ms` | **when he posted it.** The only timestamp a follower could have acted on — use this for any tradability test |
| `entry_time_utc` `entry_ms` | when he says he was in; equals signal for market entries, null on 31 rows |
| `exit_time_utc` `exit_ms` | null on 66 rows — he often never posts the moment |
| `signal_hour_utc` `signal_dow` `signal_date_utc` | precomputed for seasonality work |
| `duration_hours` | null when either end is unknown |

**The published plan** (what he committed to before the trade)

`planned_entry`, `planned_entry_note`, `entry_style` (`market` 153 / `limit` 4),
`planned_stop`, `planned_tp1`, `planned_tps` (pipe-joined), `n_planned_tps`,
`planned_dca` (pipe-joined), `leverage`, `risk_note`.

**Derived geometry** — computed here, not claimed by him

`entry_ref` (avg_entry, else planned_entry — the price to backtest from),
`risk_dist_abs`, `risk_dist_pct` (median **4.04%**), `tp1_dist_pct`,
`planned_rr` (median **0.93** — his published plans are roughly 1:1).

**What happened**

`avg_entry`, `exit_price`, `exit_type`, `outcome`, `r_stated` (his own words, 31 rows),
`r_from_prices` (computed from entry/stop/exit — agrees with `r_stated` to a mean
absolute 0.17R on the 20 rows where both exist, which is a useful internal check),
`roi_card_pct`, `n_dca_fills`, `n_stop_moves`, `n_tp_changes`, `n_partials`,
`still_open_at_end`.

**Quality gates**

| column | use |
|---|---|
| `is_backtestable` | **173 rows true.** crypto + a signal time + `entry_ref` + `planned_stop` + a side, minus rows with an implausible or wrong-sided stop |
| `data_quality_flags` | `stop_distance_implausible` (his own decimal typos, recorded verbatim, 5 rows) · `stop_on_wrong_side` (4) · `target_on_wrong_side` (1) · `low_confidence_reconstruction` (26, left in `is_backtestable`) |
| `confidence` `confidence_rank` | 3 high / 2 medium / 1 low. Filter `confidence_rank >= 2` for a stricter run |

**Text** — `thesis`, `management_story`, `notes`, `indicators`, `market_context`,
`msgs`, `first_msg_url`.

## `actions.csv` — 1,153 rows

The long-format table, one row per thing he did. This is what you join to bars.

`position_id`, `symbol`, `side`, `action`, `time_utc`, `time_ms`, `price`,
`portion_pct`, `msg`, `note`, `msg_url`.

`action` ∈ `signal` 217 · `stop_set` 187 · `entry` 186 · `target_set` 177 ·
`exit` 152 · `target_move` 98 · `stop_move` 54 · `partial_close` 47 · `dca_fill` 35.

## `events.csv` — 1,242 rows

Every message-level action including the ones that never became a position —
market views, plans, education, recaps. Carries the verbatim `quote` and his
stated `rationale`. Use it for text mining; use `actions` for price joins.

## `price_observations.csv` — 981 rows

Every price he quoted, with a UTC timestamp: prices he called live (`price_ref`),
stated entries, and mark prices read off exchange share cards (`card_mark_price` —
the tightest timestamp/price pairs available).

**Use this before you trust any join.** He traded Blofin, MEXC, Bybit, Yubit and
Binance; your feed is one venue. Join these observations to your bars and measure
the offset — expect 0.05–0.3%. If it is larger, your symbol mapping or your
timestamps are wrong.

## `unresolved_positions.csv` — 35 rows

The positions that simply stop being mentioned. Each has a signal time, an entry
and a stop. Scan forward for the first touch of stop or TP1 and you convert the
77% *reconstructed* win rate into a *measured* one — the cheapest high-value fix
in the dataset (hypothesis H12).

---

## Join recipe

```python
from load import load_pack, replay_plan, excursions
d   = load_pack('parquet')
pos = d['positions']
bt  = pos[pos.is_backtestable]                    # 173 rows

for _, r in bt.iterrows():
    bars = load_ohlcv(r.symbol, r.signal_time_utc,
                      r.signal_time_utc + pd.Timedelta(hours=168), '1h')
    plan = replay_plan(bars, r.entry_ref, r.planned_stop,
                       r.planned_tp1, int(r.side_sign))
    exc  = excursions(bars, r.entry_ref, r.planned_stop, int(r.side_sign))
```

`load.py` runs standalone (`python load.py`) for a sanity report, and
`python load.py --replay` runs the full mechanical comparison once you have
implemented `load_ohlcv`.

## Where to start

`hypotheses.json` lists 16 testable claims with feature definitions and the data
each needs. **H0 is the one that decides your project**: trade his published plan
mechanically and compare it to his actual exits. If the mechanical version wins,
the edge is in the setup and is automatable. If it loses, the edge is in his
discretionary exits and it is not.

Two findings already visible without any price data:

- **His published R:R is a median 0.93** — he plans roughly 1:1, which is what a ~77%
  hit rate needs to be profitable and confirms the small-target reading.
- **The entry price is not the decision — the timing is.** 168 of 217 positions are
  entered at CMP/market, and 33% of his call messages contain nothing but the order
  block. See `entry_selection_findings.md` for the full measurement, including a
  correction to an earlier round-number claim that was overstated (the effect is a
  modest ~2× at hundreds granularity, and nothing at thousands).

## Known limits

- No account size and no position sizes anywhere in the source. Returns can be
  expressed in R, never in currency.
- 35 positions have no recorded outcome; 66 have no exit timestamp.
- He runs the same idea across several venues and an $89 novelty account. One
  idea can appear as three different cards — the positions table already merges
  those, but the prices come from whichever venue he screenshotted.
- Concurrent positions are common and BTC-correlated. They are not independent
  bets for portfolio simulation.
- Decimal typos in his own posts are preserved verbatim and flagged rather than
  silently corrected.
