"""Step-7 mutation drill: break each sleeve deliberately, confirm its golden
goes red. A golden that survives a real behaviour change is decorative.

Every mutation below is one a plausible "simplifying" refactor could make.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"C:\Source\Repos\p300")
PY = REPO / "venv" / "Scripts" / "python.exe"

MUTATIONS = [
    # (label, file, before, after, test file)
    ("r4: gate arm uses GATED not UNGATED",
     "bots/r4/strategy/signal.py",
     "inner_lev = R4_INNER_LEV_UNGATED * gate.leverage_mult",
     "inner_lev = R4_INNER_LEV_GATED * gate.leverage_mult",
     "tests/test_golden_r4.py"),
    ("r4: weights fallback becomes a subscript",
     "bots/r4/strategy/signal.py",
     'ti["weights"].get(weight_key, 0.0)', 'ti["weights"][weight_key]',
     "tests/test_golden_r4.py"),
    ("r4: bear-regime zero-weight gate removed",
     "bots/r4/strategy/signal.py",
     "if weight <= 0:", "if False:",
     "tests/test_golden_r4.py"),
    ("carry: entry threshold sign flipped",
     "bots/carry/strategy/config.py",
     "FR_ENTRY_THRESHOLD = 0.0", "FR_ENTRY_THRESHOLD = 1.0",
     "tests/test_golden_carry.py"),
    ("carry: funding window 7d -> 14d",
     "bots/carry/strategy/config.py",
     "FR_WINDOW_DAYS = 7", "FR_WINDOW_DAYS = 14",
     "tests/test_golden_carry.py"),
    ("squeeze_bull: use_stop ignored in decide() (always writes a stop)",
     "bots/squeeze_bull/strategy/signal.py",
     '"_stop_price": stop if use_stop else None,',
     '"_stop_price": stop,',
     "tests/test_golden_squeeze_bull.py"),
    ("squeeze_bull: flush threshold loosened",
     "bots/squeeze_bull/strategy/config.py",
     "FLUSH_THRESHOLD = -0.02", "FLUSH_THRESHOLD = -0.01",
     "tests/test_golden_squeeze_bull.py"),
    ("squeeze_bull: regime gate removed",
     "bots/squeeze_bull/strategy/signal.py",
     'if regime != "bull_30d":', "if False:",
     "tests/test_golden_squeeze_bull.py"),
    ("adx: leverage hardcoded to 1.0 in decide() (the strip's own edit)",
     "bots/adx/strategy/signal.py",
     "    leverage = float(leverage)", "    leverage = 1.0",
     "tests/test_golden_adx.py"),
    ("adx: trend filter removed",
     "bots/adx/strategy/signal.py",
     'if sig.get("entry_blocked_by_trend"):', 'if False:',
     "tests/test_golden_adx.py"),
    ("adx: funding veto removed",
     "bots/adx/strategy/signal.py",
     'if fz is not None and FUNDING_VETO_Z is not None and fz > FUNDING_VETO_Z:',
     'if False:',
     "tests/test_golden_adx.py"),
    ("short_squeeze: use_stop ignored in decide()",
     "bots/short_squeeze/strategy/signal.py",
     '"_stop_price": stop_price if use_stop else None,',
     '"_stop_price": stop_price,',
     "tests/test_golden_short_squeeze.py"),
    ("short_squeeze: macro gate removed",
     "bots/short_squeeze/strategy/signal.py",
     'if not macro["is_short_macro"]:', 'if False:',
     "tests/test_golden_short_squeeze.py"),
    ("chento: OKX alignment gate removed",
     "bots/chento_v3/strategy/signal.py",
     'if not ctm.okx_aligned(okx_z, direction, OKX_ALIGN_Z_MIN):', 'if False:',
     "tests/test_golden_chento.py"),
    # The per-variant flags that separated the live paper twins moved from
    # the cfg dict to literal runner keywords when the sleeves were repointed,
    # so the mutation moves with them.
    ("squeeze_bull runner: use_stop not threaded per variant",
     "bots/squeeze_bull/runner.py",
     'per[v["row"]["id"]] = tick(v["row"], use_stop=bool(v["use_stop"]),',
     'per[v["row"]["id"]] = tick(v["row"], use_stop=True,',
     "tests/test_squeeze_bull_bot.py"),
    ("short_squeeze runner: count_diag not once-per-tick",
     "bots/short_squeeze/runner.py",
     'count_diag=(k == 0))', 'count_diag=True)',
     "tests/test_short_squeeze_bot.py"),

    # ─── Look-ahead (BACKLOG step 6, 2026-09-13) ────────────────────────────
    # Charged to a specific NODE, not a whole file. A file-level CAUGHT can
    # hide an arm that has gone decorative, which is exactly what happened to
    # three successive drafts of a squeeze_bull agreement arm — each passed
    # its file while catching nothing. Every entry below was run and confirmed
    # red before being added.
    ("lookahead: ADX candle loader reads 10d past the clock",
     "bots/adx/strategy/signal.py",
     "upper_ts = clock.now_ts()", "upper_ts = clock.now_ts() + 864000",
     "tests/test_jplus_lookahead.py::test_adx_candle_loader_no_lookahead"),
    ("lookahead: ADX candle loader drops its upper bound entirely",
     "bots/adx/strategy/signal.py",
     "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
     "WHERE timestamp >= ? AND ? > 0 ORDER BY timestamp",
     "tests/test_jplus_lookahead.py::test_adx_candle_loader_no_lookahead"),
    ("lookahead: carry funding upper_ts pushed 30d past the clock",
     "bots/carry/strategy/signal.py",
     "    upper_ts = clock.now_ts()", "    upper_ts = clock.now_ts() + 2592000",
     "tests/test_jplus_lookahead.py::test_carry_funding_no_lookahead"),
    # Binding-preserving on purpose: replacing "?" outright raises
    # sqlite3.ProgrammingError (wrong parameter count) and the drill would
    # report a FALSE catch — the test fails on the error, not the look-ahead.
    ("lookahead: jplus btc_hourly upper bound pushed past the clock",
     "data/loaders.py",
     "AND timestamp <= ? ", "AND timestamp <= ?+8640000 ",
     # Was charged to test_common_dates_identical_across_clocks and went
     # MISSED when the 7a fix landed: _run_decision_loop now drops dates
     # >= the clock, which hides an unbounded loader from anything that
     # reads its output. The drill is what caught the regression.
     "tests/test_jplus_lookahead.py::test_jplus_loaders_are_clock_bounded"),
    ("lookahead: squeeze_bull loader re-anchored to MAX(timestamp)",
     "bots/squeeze_bull/strategy/signal.py",
     "WHERE p.timestamp >= ? AND p.timestamp < ?",
     "WHERE p.timestamp >= ? AND p.timestamp < "
     "(SELECT MAX(timestamp)+1 FROM cd_futures_ohlcv) AND ?>0",
     "tests/test_bot_lookahead.py::test_squeeze_bull_loader_is_clock_bounded"),
    ("lookahead: squeeze_bull loader bound made inclusive (forming hour)",
     "bots/squeeze_bull/strategy/signal.py",
     "AND p.timestamp < ?", "AND p.timestamp <= ?",
     "tests/test_bot_lookahead.py::test_squeeze_bull_loader_is_clock_bounded"),
    ("lookahead: squeeze_bull REGIME_SHIFT_DAYS = 0 (config says never)",
     "bots/squeeze_bull/strategy/config.py",
     "REGIME_SHIFT_DAYS = 1", "REGIME_SHIFT_DAYS = 0",
     "tests/test_bot_lookahead.py::"
     "test_squeeze_bull_regime_never_reads_its_own_day_or_later"),
    ("lookahead: short_squeeze percentile pool reaches past the clock",
     "bots/short_squeeze/strategy/signal.py",
     "WHERE p.timestamp >= ? AND p.timestamp <= ?",
     "WHERE p.timestamp >= ? AND p.timestamp <= ?+8640000",
     "tests/test_bot_lookahead.py::"
     "test_short_squeeze_percentile_pool_is_clock_bounded"),
    # BACKLOG 7a. Reverting the partial-day drop is the exact bug that
    # bypassed r4's bear-regime kill switch on 2026-03-03 and 2026-03-31.
    ("lookahead: r4 sizing reads today's partial daily bar again",
     "strategies/support/jplus_inputs.py",
     "dates = [d for d in sorted(set(btc_d.keys())) if d < clock_date]",
     "dates = sorted(set(btc_d.keys()))",
     "tests/test_jplus_lookahead.py::"
     "test_bear_kill_switch_is_not_bypassed_by_the_partial_bar"),
    ("lookahead: short_squeeze percentile pool is no longer rolling",
     "bots/short_squeeze/strategy/signal.py",
     "WHERE p.timestamp >= ? AND p.timestamp <= ?",
     "WHERE p.timestamp >= ?-864000000 AND p.timestamp <= ?",
     "tests/test_bot_lookahead.py::"
     "test_short_squeeze_percentile_pool_is_clock_bounded"),
]


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          **kw)


def _restore(p: pathlib.Path, orig_bytes: bytes) -> None:
    """Byte-exact restore, AND drop the bytecode compiled from the mutation.

    Writing the original bytes back is not enough. CPython invalidates a
    .pyc on (mtime, size), and a mutation that keeps the file the same size —
    `REGIME_SHIFT_DAYS = 1` -> `= 0` — restored within the filesystem's mtime
    granularity leaves a STALE .pyc that Python keeps using. Observed
    2026-09-13: `git diff` clean, `git status` clean, and
    `config.REGIME_SHIFT_DAYS` importing as 0, which failed nine unrelated
    squeeze_bull tests and would have been a nightmare to attribute.

    That is worse than a test-suite annoyance. The fleet is running, and a
    bot restarted in that window would load the mutated bytecode from a repo
    that reports itself clean. So the .pyc goes, unconditionally.
    """
    p.write_bytes(orig_bytes)
    cache = p.parent / "__pycache__"
    if cache.is_dir():
        for pyc in cache.glob(f"{p.stem}.*.pyc"):
            pyc.unlink(missing_ok=True)


before_state = run(["git", "status", "--porcelain", "strategies/", "bots/", "data/"]).stdout

caught = missed = skipped = 0
for label, rel, before, after, testfile in MUTATIONS:
    p = REPO / rel
    if not p.exists():
        print(f"  [SKIP ] {label}\n           {rel} no longer exists")
        skipped += 1
        continue
    orig_bytes = p.read_bytes()
    orig = orig_bytes.decode("utf-8")
    if before not in orig:
        print(f"  [SKIP ] {label}\n           pattern not found in {rel}")
        skipped += 1
        continue
    p.write_bytes(orig.replace(before, after, 1).encode("utf-8"))
    try:
        r = run([str(PY), "-m", "pytest", testfile, "-q", "-p",
                 "no:cacheprovider"], timeout=900)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
        if r.returncode != 0:
            print(f"  [CAUGHT] {label}\n           {tail}")
            caught += 1
        else:
            print(f"  [MISSED] {label}\n           {tail}  <-- golden is "
                  f"decorative here")
            missed += 1
    finally:
        _restore(p, orig_bytes)

print(f"\ncaught {caught}  missed {missed}  skipped {skipped}")
after_state = run(["git", "status", "--porcelain", "strategies/", "bots/", "data/"]).stdout
st = "" if after_state == before_state else after_state.strip()
print("restored to pre-drill state:" if not st else "!! DRILL LEFT RESIDUE:",
      st or "(yes)")
sys.exit(1 if (missed or st) else 0)
