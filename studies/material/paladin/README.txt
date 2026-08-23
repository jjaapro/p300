paladin — Discord export, extracted trade record, and the analysis layer
=======================================================================
Source: DiscordChatExporter JSON of #paladin (ScalpX), 2026-05-06 -> 2026-08-21,
1,005 messages, 320 screenshots. Built 2026-08-21/22.

>>> READ THIS BEFORE JOINING ANYTHING TO PRICE DATA <<<
The Discord export stamps every message Europe/Helsinki (+03:00). The reading
deliverables below (positions_timeline.csv, events_timeline.csv, trade_timeline.json,
the xlsx files and both HTML pages) carry LOCAL time with the offset stripped -
they are silently 3 hours ahead of UTC. Everything in analysis\ is proper UTC.
Use analysis\ for any price join. Use the rest for reading.


READ / BROWSE
  paladin_timeline.html        The timeline. Every position he ran: what he published
                               before the trade, what he actually did, how it ended,
                               every message on that trade, searchable and filterable.
  paladin_image_browser.html   Every screenshot beside the trading data read off it.
  paladin_vs_benchmarks.html   Did he beat the S&P / buy-and-hold, and why his own
                               return can only be bounded.
  paladin_method_analysis.md   ~2,500 words: what he watches, how he structures and
                               manages a trade, how the win rate is actually produced.
  The two browsers load pictures from images\ - keep them in this folder.

SPREADSHEETS / FLAT DATA  (local time - for reading, not for price joins)
  paladin_trade_timeline.xlsx  Summary / Positions (217) / Events (1,242) /
                               What he watches / His scoreboard.
  positions_timeline.csv       One row per trade.
  events_timeline.csv          One row per action, with his exact words + Discord link.
  trade_timeline.json          Everything above, nested.
  paladin_image_analysis.xlsx  Per-image extraction (320 images -> 335 trade rows).
  trades_from_images.csv       335 trade rows read off screenshots.
  images_analysis.csv          320 rows, one per image, full transcriptions.
  image_analysis.json          Same, nested.
  manifest.csv / manifest.json Provenance of each image.

analysis\   MACHINE-READABLE, UTC - use this for backtesting
  SCHEMA.md                    Column dictionary, join recipe, known limits. Start here.
  hypotheses.json              15 testable claims + H0, the test that decides whether
                               his edge is automatable.
  load.py                      Reference loader; replay_plan() and excursions() are
                               written, only load_ohlcv() is a stub for your data.
  positions.*                  217 rows x 55 cols. is_backtestable = 173.
  actions.*                    1,153 rows, long format - one row per thing he did.
  events.*                     1,242 message-level actions incl. views and education.
  price_observations.*         981 timestamped prices he quoted - use these to measure
                               your feed's venue offset before trusting any join.
  unresolved_positions.*       35 trades that vanish; resolve them from OHLCV.
  Each table ships as .parquet, .csv and .jsonl (identical content).
  paladin_analysis_pack.zip in the root is the same folder, zipped.

source\     THE INTERMEDIATE RECORD - regenerate or audit anything from here
  messages_full.txt            All 1,005 messages in reading order, with the screenshot
                               contents merged in. The corpus to re-read or re-mine.
  events_all.json              1,242 structured events (the layer under events.csv).
  positions_all.json           217 stitched positions (the layer under positions.csv).
  image_extraction_raw.json    Per-image extraction, nested, with chart notes.
  playbook_final.json          Signals, risk rules, entry style, management tactics,
                               reconciliation and the graded win mechanics, each claim
                               carrying message-numbered verbatim quotes.
  playbook_audit.json          The adversarial audit of the playbook draft - what was
                               refuted, softened or dropped, and why.
  nosymbol_resolved.json       His own performance claims, market views and teaching.
  prompt_*.md                  The three extraction specs used, so the pipeline can be
                               re-run on a fresh export and produce comparable output.

scripts\    the build scripts, in pipeline order
  download_images.py           export JSON -> images\ + manifest
  build_xlsx.py                image extraction -> paladin_image_analysis.xlsx
  build_browser.py             image extraction -> paladin_image_browser.html
  build_timeline.py            positions -> paladin_timeline.html
  build_timeline_xlsx.py       positions + events -> paladin_trade_timeline.xlsx
  build_analysis_pack.py       positions + events -> analysis\ (this is the UTC fix)

_to_delete\                    Transfer zips from the first pass. Safe to delete (125 MB).


HOW THE DATA WAS MADE
  Every one of the 320 images was viewed, not OCR'd blindly. Every message was read and
  turned into structured events; the events were stitched back into positions. Numbers
  are copied from his messages and screenshots - nothing is calculated or assumed on his
  behalf, and a blank means he never published it. Positions marked medium/low confidence
  had part of the story inferred; the Trust notes column says which part. Independent
  audit passes re-checked 36 images and 24 positions against the source (no misread
  digits) and fact-checked every playbook claim; the corrections are applied.

THE HEADLINE
  217 positions catalogued, 200 taken, 165 with a knowable outcome: 120 W / 35 L / 10 BE
  = 77% win rate excluding breakevens, 73% including. He publicly claimed 85% (May) and
  89% (July), and never posted a June summary. Only 45 of his 120 wins reached a target
  he had published; 62 positions were closed by hand, of which 61 were wins and none were
  losses; all 35 losses came through a stop. Over the same window BTC bought and held
  returned -6.8% and the S&P 500 about +5%.
