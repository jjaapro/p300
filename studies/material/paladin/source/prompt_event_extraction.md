# Trade-event extraction from a crypto trader's Discord channel

You are turning the messages of a crypto futures trader ("Paladin", the channel owner) into a structured event log. Downstream this becomes a timeline of every trade he ran: what he called, at what entry, with what stop and targets, how he managed it, and how it ended. Precision and traceability matter more than interpretation.

## Input

A text file of messages in chronological order. Each message looks like:

```
--- MSG 211 | 2026-05-23 11:59:29
Still in JCT BE didn't hit, moving TP to 0.00375 (1R)
    [IMAGE 073_....png: pnl_share_card | SUIUSDT long 100x entry 1.0014 mark 1.0165 ROI 150.91% (open) | description]
```

- `MSG n` is the message number — always cite it.
- Lines in `[IMAGE ...]` are data already extracted from the screenshot he attached. Treat them as facts he posted.
- `(replying to: "...")` shows the message he replied to.
- A `[POSTED BY <name>, not Paladin]` marker means someone else wrote it — record it only if it changes what Paladin's trade did, and set `by_other: true`.
- The file may start with a `CONTEXT ONLY` section. Read it to understand positions that are already open, but **do not create events for those messages**. Only create events for messages in the `YOUR CHUNK` section.

## What to produce

A JSON array of events. One message can produce several events (e.g. "closed BTC, opening ZEC long" → two events). A message with no trading content produces one `chat` event or, if it is pure noise (an emoji, "gm", a thank-you), no event at all.

```json
{
  "msg": 211,
  "timestamp": "2026-05-23 11:59:29",
  "event_type": "tp_move",
  "symbol": "JCTUSDT",
  "side": "short",
  "entry_price": null,
  "dca_price": null,
  "take_profits": [0.00375],
  "stop_loss": null,
  "portion_pct": null,
  "leverage": null,
  "r_multiple": 1.0,
  "roi_pct": null,
  "price_ref": null,
  "risk_note": null,
  "rationale": "moving TP to 1R because BE did not hit",
  "indicators_mentioned": [],
  "market_context": null,
  "by_other": false,
  "quote": "Still in JCT BE didn't hit, moving TP to 0.00375 (1R)",
  "confidence": "high"
}
```

### event_type — use exactly one of these

| type | when |
|---|---|
| `call` | he announces a new setup with levels, usually "Long X / Entry: … / TP: … / SL: …" |
| `entry` | he states he has entered / is in / scaled in |
| `dca` | he adds to an existing position, or states a planned DCA level |
| `sl_set` | a stop is given for the first time |
| `sl_move` | stop moved — to entry ("SL to entry", "BE"), into profit, or wider |
| `tp_set` | a target is given or changed for the first time |
| `tp_move` | an existing target is moved |
| `tp_hit` | a target was reached |
| `partial_close` | he takes a portion off ("take 20% here") |
| `close` | fully out of the position |
| `stopped_out` | stopped at a loss |
| `breakeven` | closed flat / stopped at entry |
| `liquidated` | position liquidated |
| `update` | status/PnL update with no action (e.g. "1R soon!", a share card) |
| `plan` | a conditional or waiting plan ("if BTC taps 58k I'll long") |
| `view` | market opinion/bias with no specific trade |
| `education` | teaching, rules, risk management, R explanation, psychology |
| `recap` | performance summary over a period |
| `chat` | community talk, promos, thanks, off-topic |

### Field rules

- `symbol`: normalise to the exchange pair with USDT (`BTC` → `BTCUSDT`, `sui` → `SUIUSDT`). Use `null` for `view`/`education`/`chat` events that name no asset; use the asset if the view is about one (a BTC view → `BTCUSDT`). Gold/indices etc.: use the name he uses (`GOLD`, `SPX`).
- Prices: numbers only, no `$`, no `k`. **Expand his shorthand: `58.6k` → 58600, `64550` → 64550, `0.5R` is NOT a price.** If he writes a range ("TPs: 1.05-1.11") put both numbers in `take_profits`.
- `take_profits`: always a list, `[]` if none.
- `portion_pct`: 20 for "take 20%", 50 for "half", 100 for a full close.
- `r_multiple`: his R notation (1R, 0.5R, 8R). Negative for a loss.
- `price_ref`: the market price he references when it is not an order level (e.g. "CMP", "BTC at 61.3k" → 61300).
- `risk_note`: sizing/risk instructions verbatim-ish ("half risk", "recommended margin is half", "1-2% on alts").
- `rationale`: **why** — his stated reason. This is the most valuable field. Keep it short but faithful.
- `indicators_mentioned`: anything technical he names — `EMA`, `RSI`, `funding`, `OI`, `liquidity`, `liquidation levels`, `CVD`, `order book`, `daily low`, `support/resistance`, `trendline`, `FVG`, `CPI`, `news`. Empty list if none.
- `market_context`: BTC/market backdrop he mentions ("BTC weak", "CPI today", "weekend").
- `quote`: the exact sentence(s) the event comes from, trimmed. Never invent wording.
- `confidence`: `high` when explicit, `medium` when you inferred symbol or intent from context, `low` when it is a guess. Prefer `medium`/`low` over dropping ambiguous events.

### Judgement rules

- **Never invent numbers.** If he says "SL to entry" and you do not know the entry, leave `stop_loss` null and say so in `rationale`.
- He often manages several positions in one message — split into one event per symbol.
- "@Paladin Notify" is a ping tag, not content. Ignore it.
- Discord emoji like `:KEKLaugh:` are noise. Ignore them.
- When a message updates a position opened earlier (possibly in the CONTEXT section), still create the event — downstream stitching will link it.
- Keep events in message order.

## Output

Write the JSON array to the output path given in your task with the Write tool. Valid JSON, no trailing commas, `null` (not `None`), `[]` for empty lists. Then reply with: the number of events, the message-number range covered, and anything genuinely ambiguous.
