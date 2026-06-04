# chento journal — reverse-engineering a multi-year discretionary track record

Standalone analysis of the trader **chento** (Discord user `chentotrades`,
ID `978925049945919499`), who runs the ScalpX community's `🐐｜chento`
journal channel. His track record:

- Started a $200 challenge in June 2024
- Grew it through multiple challenges over ~23 months
- Documented every entry/adjustment/close as Discord posts (text + chart)

This folder is separate from [../swing_base_limit_bid/](../swing_base_limit_bid/)
which models *the pattern*; here we model *his behaviour*. They
cross-reference but the workflows are different.

## Data

| File | Purpose |
|---|---|
| `studies/material/ScalpX...chento.html` | Raw Discord export (5.3 MB, 1,991 msgs) |
| `studies/material/chento/messages.parquet` | Parsed message frame |
| `studies/material/chento/trades.parquet` | Extracted trade lifecycle |
| `studies/material/chento/images_index.csv` | Image URL ↔ message_id |

## Notebooks

| Notebook | Stage |
|---|---|
| `01_parse.ipynb` | HTML → `messages.parquet`. Strict, idempotent, no judgement calls. |
| `02_classify.ipynb` | Text classifier: ENTRY / ADD / TRIM / CLOSE / SL_MOVE / COMMENTARY / ACCOUNT_SCREEN. Regex first; LLM/vision only for ambiguous cases. |
| `03_cross_reference.ipynb` | Extracted trades × MTF cell map from `swing_base_limit_bid` discovery. Tests the hypothesis: does his trade book cluster in the green cells? |

## Open questions

1. **Coverage** — what fraction of his 1,991 messages contain machine-parseable
   trade data? (text-only entries vs. chart-only entries vs. ambient
   commentary). Drives whether we need vision/OCR.
2. **Lifecycle reconstruction** — do entries always carry SL/TP in the text,
   or does he post structure-on-chart and update via separate messages?
3. **Cross-asset** — is this BTC-only or does he trade ETH/alts/indices/FX
   inside the same journal? If multi-asset, MTF cross-ref is BTC-specific
   subset.
4. **Performance attribution** — at what point do "challenges" reset the
   bankroll? Need to identify challenge boundaries to compute true compounded
   return vs. challenge-by-challenge.

## Status (2026-05-19)

- HTML inspected: 1,991 msgs over 2024-06-09 → 2026-05-19, ~1.86 attachments/msg
- Parser pending
- Classification pending
- Cross-reference pending
