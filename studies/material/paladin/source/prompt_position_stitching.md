# Stitching a trader's event log into position lifecycles

You are turning a chronological event log into **positions** — one record per trade he actually ran, from the moment he called or entered it to the moment he was out. This becomes the timeline the user reads, so each record must tell the whole story of that trade with numbers and message references.

## Input

A text file of events already extracted from his Discord messages. Each event looks like:

```
MSG 224 | 2026-05-24 02:13:11 | call | BTCUSDT | long
   entry_price=75200 stop_loss=72200 tp=[77000, 78000] risk=half risk
   why: expects a bounce from the daily low
   ctx: BTC weak
   "Long BTC / Entry: 75.2k CMP / TP: 77-78k / SL: 72.2k"
```

Sections you may see:
- `EARLIER CONTEXT` — positions already open before your window. Use them to understand references, but **do not output a position whose first call/entry is in that section**.
- `YOUR WINDOW` — output positions whose **first call or entry** is here.
- `LATER CONTEXT` — how your positions ended. Use it, but do not output positions that start there.
- For per-symbol files (no window markers) output every position in the file.

## What counts as one position

One position = one directional trade in one asset, from first call/entry to final exit. Keep together: the call, the entry, DCAs/adds, stop moves, partial closes, the final exit.

Start a **new** position when he closes out and later enters the same asset again, or when he flips direction. He often runs the same idea on several exchanges/accounts at once (a 150x Blofin card and a 250x MEXC card of the same BTC long) — that is **one** position; note the extra account in `notes`, do not duplicate.

A `call` he never entered, or a limit that never filled, is still a position with `outcome: "not_taken"` or `"never_filled"`.

## Output — a JSON array of position objects

```json
{
  "position_id": "BTCUSDT-2026-05-24-long",
  "symbol": "BTCUSDT",
  "side": "long",
  "first_seen": "2026-05-24 02:13:11",
  "entry_time": "2026-05-24 02:13:11",
  "exit_time": "2026-05-24 19:41:02",
  "duration_hours": 17.5,
  "planned_entry": 75200,
  "planned_entry_note": "CMP, 50% size, 50% saved for DCA",
  "planned_dca": [74000],
  "planned_stop": 72200,
  "planned_take_profits": [77000, 78000],
  "planned_r": null,
  "risk_note": "half risk",
  "leverage": 150,
  "avg_entry": 75197.2,
  "dca_fills": [ {"time": "2026-05-24 08:02:00", "price": 74050, "note": "added 50%", "msg": 231} ],
  "stop_moves": [ {"time": "2026-05-24 10:11:00", "to": 75200, "type": "to_entry", "msg": 239} ],
  "tp_changes": [ {"time": "...", "to": [76800], "msg": 244} ],
  "partials": [ {"time": "...", "price": 76600, "portion_pct": 25, "msg": 246} ],
  "exit_price": 77906.3,
  "exit_type": "take_profit | manual_close | stop_loss | breakeven | liquidation | never_filled | not_taken | unknown",
  "outcome": "win | loss | breakeven | unknown | not_taken",
  "r_multiple": 1.0,
  "roi_pct": 331.94,
  "roi_note": "ROI is the exchange card's leveraged figure, not account return",
  "thesis": "why he took it, in his terms — 1-2 sentences",
  "indicators": ["daily low", "4h close", "EMA"],
  "market_context": "post-CPI chop, BTC weak",
  "management_story": "3-4 sentences: what he actually did as it played out — the human-readable story of this trade, in order.",
  "still_open_at_end": false,
  "msgs": [224, 231, 239, 246, 252],
  "confidence": "high | medium | low",
  "notes": "ambiguities, duplicate accounts, conflicting numbers, anything a reader should distrust"
}
```

### Rules

- **Never invent a number.** If he never stated the exit price, `exit_price` is null and `management_story` says how you know it ended. Prices already in the events are fine to carry over.
- `planned_*` = what he published up front. `avg_entry`, `exit_price`, `partials` etc. = what actually happened. Keep the two apart — the difference between plan and execution is the point of this exercise.
- `outcome`: `win` if he ended in profit (a partial-profit exit still counts as a win), `loss` if stopped or closed at a loss, `breakeven` for BE/flat exits, `unknown` when the events never say.
- `r_multiple`: only if he stated it, or it is unambiguous from his own words. Do not compute it from prices.
- `duration_hours`: compute from the timestamps, one decimal. Null if either end is unknown.
- `msgs`: every message number belonging to this position, ascending.
- `still_open_at_end`: true if the log ends with it open.
- `confidence`: `medium`/`low` if you had to infer the link between events, the symbol, or the outcome. Say why in `notes`.
- Where he tells followers to do something different from what he does himself, record **his own** action in the fields and mention the follower guidance in `management_story`.

Sort positions by `first_seen`. Write the JSON array with the Write tool to the output path in your task, then reply with: how many positions, the date range, the win/loss/BE/unknown counts, and anything genuinely ambiguous.
