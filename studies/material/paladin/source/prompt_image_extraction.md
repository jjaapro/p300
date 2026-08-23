# Image extraction instructions (crypto trader Discord channel)

You are extracting trading information from screenshots posted by a crypto futures trader ("Paladin") in his Discord channel. The goal is a precise, per-image record of everything trading-related that is visible: the pair, long/short, leverage, entry price, exit/mark/close price, ROI %, PnL, position size/margin, stop loss, take profits, liquidation price, timeframe, indicators and drawings on charts, and any written reasoning. Later these records are used to reconstruct what he was looking at when he made decisions, so details matter.

## Workflow

1. Read your input file (path given in your task). It is a JSON list. Each item has: `filename`, `path` (absolute path of the image), `timestamp` (when the Discord message was posted, Europe/Helsinki), `message` (the text he posted together with the image, may be empty), and `ocr` (rough OCR text of the image — an AID ONLY; it contains garbage and misreads digits, so never copy a number from OCR without seeing it in the image).
2. For EVERY item, use the Read tool on `path` to actually view the image. Do not skip any image and do not rely on the OCR text or the filename alone.
3. Build one record per image following the schema below.
4. Write the full list of records as a JSON array to your output file (path given in your task) using the Write tool. Valid JSON only, no comments, no trailing commas. Use `null` for unknown values (not empty strings, not 0).
5. Reply with only: the number of records written, and any images you could not read.

## Rules for numbers

- Copy numbers exactly as displayed (e.g. `67119.9`, not a rounded value). Strip thousands separators. Do not compute or infer values that are not shown.
- `leverage` is a number (150 for "150X").
- `roi_pct` is the ROI / PnL percentage shown on the card (331.94 for "+331.94%"); negative for losses.
- A share card that shows "Close Price" means the trade was closed → `status: "closed"`, `exit_price_type: "close"`. One that shows "Mark Price" / "Last Price" means it was still open → `status: "open"`, `exit_price_type: "mark"`.
- If a number is partially cut off or too blurry to read with certainty, set it to null and mention it in `issues`.

## Image types (pick the best one)

`pnl_share_card` (exchange share card with ROI %, entry/close price, e.g. Blofin "WOW War of Whales", Yubit, Binance), `open_positions_screenshot` (exchange positions tab: size, entry, mark, liq price, PnL, TP/SL), `order_screenshot` (order form / open orders / TP-SL setup), `closed_pnl_list` (exchange trade history list), `pnl_summary` (cumulative PnL / win-rate statistics card), `chart` (TradingView or exchange chart), `discord_message_screenshot` (screenshot of Discord messages — his own recap/plan or someone else's), `tweet_screenshot`, `liquidation_heatmap_or_orderflow` (liquidation map, order book, open interest, funding, CVD, etc.), `news_or_calendar`, `other`, `non_trading` (memes, personal photos, promo graphics with no trade data).

## Record schema (one per image)

```json
{
  "filename": "string (exactly as in input)",
  "trading_related": true,
  "image_type": "pnl_share_card",
  "platform": "Blofin | Yubit | Binance | Bybit | OKX | Bitget | TradingView | Discord | X/Twitter | Coinglass | other | unknown",
  "timestamp_on_image": "any date/time printed in the image, written as shown, else null",
  "trades": [
    {
      "symbol": "BTCUSDT",
      "side": "long | short | null",
      "leverage": 150,
      "margin_mode": "cross | isolated | null",
      "entry_price": 67119.9,
      "exit_price": 65634.5,
      "exit_price_type": "close | mark | last | liquidation | null",
      "roi_pct": 331.94,
      "pnl_usd": null,
      "position_size": "as shown, e.g. '0.5 BTC' or '12,000 USDT', else null",
      "margin_usd": null,
      "stop_loss": null,
      "take_profits": [],
      "liquidation_price": null,
      "status": "open | closed | planned | unknown",
      "notes": "anything else about this trade visible in the image"
    }
  ],
  "chart": {
    "symbol": "BTCUSDT or null",
    "timeframe": "e.g. 15m, 1H, 4H, 1D or null",
    "indicators": ["EMA 21", "VWAP", "RSI", "volume"],
    "drawings_and_levels": ["horizontal level ~ 80,500 labeled 'TP'", "descending trendline", "supply zone 82.1k-82.6k", "FVG", "liquidity sweep arrow"],
    "annotations_text": "any text written on the chart, verbatim",
    "price_shown": "current/last price visible on the chart, if any",
    "what_it_shows": "1-2 sentences on the setup: structure, what he seems to be watching"
  },
  "visible_text": "Verbatim transcription of the important text in the image (trade calls, updates, recaps, rules, numbers, tweet text). For Discord screenshots transcribe the whole message(s) including author names and times if visible. Empty string if none.",
  "description": "1-3 sentences: what the image shows and anything that reveals his decision process (what he was watching, why he entered/exited).",
  "confidence": "high | medium | low",
  "issues": "unreadable parts, ambiguities, or null"
}
```

- `trades` is an empty list `[]` if the image contains no trade with at least a symbol. If an image shows several positions/trades (a positions tab, a history list, a recap listing many trades), create one trade entry per trade.
- `chart` is `null` unless the image is or contains a price chart.
- Keep `visible_text` faithful: this is the trader's own language and is valuable. Do not summarize it away.
- Use the `message` field only as context (for example to know which pair a chart is about); do not copy it into `visible_text`.
