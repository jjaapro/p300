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
]


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          **kw)


before_state = run(["git", "status", "--porcelain", "strategies/", "bots/"]).stdout

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
        p.write_bytes(orig_bytes)      # byte-exact restore

print(f"\ncaught {caught}  missed {missed}  skipped {skipped}")
after_state = run(["git", "status", "--porcelain", "strategies/", "bots/"]).stdout
st = "" if after_state == before_state else after_state.strip()
print("restored to pre-drill state:" if not st else "!! DRILL LEFT RESIDUE:",
      st or "(yes)")
sys.exit(1 if (missed or st) else 0)
