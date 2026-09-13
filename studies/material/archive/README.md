# Archived sleeves

Eight strategies that stopped being run, moved here on 2026-09-13 by BACKLOG
step 4 of the "bot = directory = strategy" refactor. Before that they lived
under `strategies/sleeves/`, where they looked like part of the system.

**None of this code runs.** Nothing dispatches it, nothing schedules it, and
no live process may import it.

## The contract

- **Importable, but unsupported.** The packages still import, deliberately —
  see "Why importable" below. Nothing keeps them working. When a dependency
  moves under them, they break, and that is not a bug to fix on sight.
- **Nothing live imports this.** Not `bots/`, `botlib.py`, `dashboard/`,
  `feed.py`, `monitor.py`, `bootstrap.py`, `health.py`, `data/` or
  `strategies/`. Enforced by
  `tests/test_orchestrator_interface_gone.py::test_no_live_code_imports_the_archive`,
  which parses imports with `ast` rather than grepping — several live modules
  legitimately name these paths in prose — and fails the suite rather than
  letting the edge reappear. Three such edges existed on
  the day of the archive and one of them — `jplus_inputs` importing
  `ema/math.py` on the live r4 sizing path — would have failed *silently*,
  because the runner catches ImportError and writes a degraded heartbeat.
- **Re-entry is through a study and a bot of its own.** Nothing comes back by
  being imported again. It comes back the way `squeeze_bull` arrived: a
  pre-registered study, then `bots/<name>/` with its own runner and variant row.

## What is here

| directory | sleeve | why it stopped |
|---|---|---|
| `ai_quant/` | AI_QUANT | `AI_QUANT_ENABLED=false` since 2026-06. 4,006 lines — the largest by far. |
| `chento_limit_bid/` | chento_limit_bid | superseded by `bots/chento_v3/`. Pool-plan A7. |
| `ema/` | JPLUS_EMA_BTC | zero trades in the 2026-06-07 trade audit. |
| `eth_daily/` | JPLUS_ETH_DAILY | never traded live. |
| `cpr/` | CPR | TradingView re-validation never completed. |
| `fomc/` | FOMC | `mid_hold` is the standing decision in this rate environment, i.e. skip. |
| `pdo/` | PDO_RETOUCH | validated 2026-05-11, never promoted to a bot. |
| `thu_bear/` | THU_BEAR | out-of-sample question never settled. |

`TIMING_ANOMALIES.md` documents the meta-sleeve that used to dispatch the last
four. It was retired on 2026-09-13 with the legacy orchestrator path. Its
`CANONICAL_SUBSTRATEGIES` list named eight sub-strategies: `THU_BEAR`, `PDO`,
`CPR`, `FOMC` (here), `R4_BTC`, `R4_ETH`, `R4_BTC_V2`, `R4_ETH_V2` (the four R4
windows, which live on in `bots/r4/`).

## Why importable, and not just a git tag

Three research questions were open on the day of the archive and archiving did
not answer any of them: the CPR TradingView re-validation, the PDO re-check,
and the THU_BEAR out-of-sample test. The repo's standing rule for ports is
*assert feature values match the source at known timestamps* — so those studies
need the sleeve's real functions, not a transcription of its constants.

`studies/notebooks/pdo_adjacents/parity_check.py` is the working example: it
imports the sleeve **module object**, swaps `sqlite3` inside it for a
`?mode=ro` shim, and drives the real functions under a frozen clock. That is
safe to run against the live database while the fleet is up, and it is the only
thing that actually proves a study matches the sleeve. Copied constants would
not do, and a git tag would mean checking out a different commit to answer a
question about today's data.

So the archive stays importable as `studies.material.archive.<sleeve>`, and
`parity_check.py` exiting 0 is a gate on the archive commit itself.

## The ledger still remembers

`prod.db` holds **12 closed trades and zero open ones** under the archived
labels — AI_QUANT 4, CPR 4, PDO_RETOUCH 2, THU_BEAR 2. The trades are real
history and the code move does not touch them. Because of that, the archived
names survive in live code as **data, not dispatch**, and must not be tidied
away:

- `strategies/support/strategy_health.py` `KNOWN_SLEEVES` — drop a name and the
  weekly report stops accounting for those closed trades.
- `strategies/trades.py` `_STOP_PATH_STRATEGIES = {"ADX", "THU_BEAR"}` and
  `strategies/support/stop_path.py` — historical rows still resolve through
  the stop path. Pinned by `tests/test_stop_resolver_inversion.py`.
- `strategies/support/equity.py` `_NO_FUNDING` keeps `PDO_RETOUCH`.
- `botlib.py` `STATE_TABLES` keeps `fomc_observer` and `ai_quant_decisions`.
  De-classifying either trips `monitor.py`'s ghost-registry check hourly.

The `fomc_observer` DDL moved to `bootstrap.py`'s `SCHEMAS` for the same
reason: 22 rows of decision history that exist nowhere else, and a fresh
bootstrap must still create the table it classifies.
