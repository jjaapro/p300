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
     "strategies/sleeves/timing_anomalies/internal/r4/signal.py",
     "inner_lev = R4_INNER_LEV_UNGATED * gate.leverage_mult",
     "inner_lev = R4_INNER_LEV_GATED * gate.leverage_mult",
     "tests/test_golden_r4.py"),
    ("r4: weights fallback becomes a subscript",
     "strategies/sleeves/timing_anomalies/internal/r4/signal.py",
     'ti["weights"].get(weight_key, 0.0)', 'ti["weights"][weight_key]',
     "tests/test_golden_r4.py"),
    ("r4: bear-regime zero-weight gate removed",
     "strategies/sleeves/timing_anomalies/internal/r4/signal.py",
     "if weight <= 0:", "if False:",
     "tests/test_golden_r4.py"),
    ("carry: entry threshold sign flipped",
     "strategies/sleeves/carry/config.py",
     "FR_ENTRY_THRESHOLD = 0.0", "FR_ENTRY_THRESHOLD = 1.0",
     "tests/test_golden_carry.py"),
    ("carry: funding window 7d -> 14d",
     "strategies/sleeves/carry/config.py",
     "FR_WINDOW_DAYS = 7", "FR_WINDOW_DAYS = 14",
     "tests/test_golden_carry.py"),
    ("squeeze_bull: use_stop ignored in decide() (always writes a stop)",
     "strategies/sleeves/squeeze_bull/signal.py",
     '"_stop_price": stop if use_stop else None,',
     '"_stop_price": stop,',
     "tests/test_golden_squeeze_bull.py"),
    ("squeeze_bull: flush threshold loosened",
     "strategies/sleeves/squeeze_bull/config.py",
     "FLUSH_THRESHOLD = -0.02", "FLUSH_THRESHOLD = -0.01",
     "tests/test_golden_squeeze_bull.py"),
    ("squeeze_bull: regime gate removed",
     "strategies/sleeves/squeeze_bull/signal.py",
     'if regime != "bull_30d":', "if False:",
     "tests/test_golden_squeeze_bull.py"),
    ("adx: leverage hardcoded to 1.0 in decide() (the strip's own edit)",
     "strategies/sleeves/adx/signal.py",
     "    leverage = float(leverage)", "    leverage = 1.0",
     "tests/test_golden_adx.py"),
    ("adx: trend filter removed",
     "strategies/sleeves/adx/signal.py",
     'if sig.get("entry_blocked_by_trend"):', 'if False:',
     "tests/test_golden_adx.py"),
    ("adx: funding veto removed",
     "strategies/sleeves/adx/signal.py",
     'if fz is not None and FUNDING_VETO_Z is not None and fz > FUNDING_VETO_Z:',
     'if False:',
     "tests/test_golden_adx.py"),
    ("short_squeeze: use_stop ignored in decide()",
     "strategies/sleeves/short_squeeze/signal.py",
     '"_stop_price": stop_price if use_stop else None,',
     '"_stop_price": stop_price,',
     "tests/test_golden_short_squeeze.py"),
    ("short_squeeze: macro gate removed",
     "strategies/sleeves/short_squeeze/signal.py",
     'if not macro["is_short_macro"]:', 'if False:',
     "tests/test_golden_short_squeeze.py"),
    ("chento: OKX alignment gate removed",
     "strategies/sleeves/chento_triple_v3/signal.py",
     'if not ctm.okx_aligned(okx_z, direction, OKX_ALIGN_Z_MIN):', 'if False:',
     "tests/test_golden_chento.py"),
    # The cfg->kwargs translation, now centralized. These replace nine
    # mutations that targeted the per-sleeve adapters phase D deleted.
    ("cfg_adapter: effective-weight override lost",
     "strategies/support/cfg_adapter.py",
     '"weight_pct": float(cfg.get("_effective_weight_pct",',
     '"weight_pct": float(cfg.get("__none__",',
     "tests/test_cfg_adapter.py"),
    ("cfg_adapter: weight defaults to 100 instead of 0",
     "strategies/support/cfg_adapter.py", 'cfg.get("weight_pct", 0.0))', 'cfg.get("weight_pct", 100.0))',
     "tests/test_cfg_adapter.py"),
    ("cfg_adapter: leverage override dropped",
     "strategies/support/cfg_adapter.py", '"leverage": float(cfg.get("_effective_leverage", 1.0)),',
     '"leverage": 1.0,', "tests/test_cfg_adapter.py"),
    ("cfg_adapter: ADX params stop_loss_pct ignored",
     "strategies/support/cfg_adapter.py", 'float(params.get("stop_loss_pct", 10.0))', '10.0', "tests/test_cfg_adapter.py"),
    ("cfg_adapter: use_stop forced on",
     "strategies/support/cfg_adapter.py", '"use_stop": bool(cfg.get("use_stop", True))',
     '"use_stop": True', "tests/test_cfg_adapter.py"),
    ("cfg_adapter: count_diag conflated with use_stop",
     "strategies/support/cfg_adapter.py", '"count_diag": bool(cfg.get("count_diag", True))',
     '"count_diag": bool(cfg.get("use_stop", True))', "tests/test_cfg_adapter.py"),
    ("cfg_adapter: r4 absent weight coerced to 0.0 (kills the regime gate)",
     "strategies/support/cfg_adapter.py", '"weight_pct": cfg.get("_effective_weight_pct"),',
     '"weight_pct": float(cfg.get("_effective_weight_pct") or 0.0),', "tests/test_cfg_adapter.py"),
    ("cfg_adapter: r4 gate dropped (leverage silently reverts to the fallback)",
     "strategies/support/cfg_adapter.py", '"gate": cfg.get("_effective_gate"),',
     '"gate": None,', "tests/test_cfg_adapter.py"),
]


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                          **kw)


before_state = run(["git", "status", "--porcelain", "strategies/"]).stdout

caught = missed = skipped = 0
for label, rel, before, after, testfile in MUTATIONS:
    p = REPO / rel
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
after_state = run(["git", "status", "--porcelain", "strategies/"]).stdout
st = "" if after_state == before_state else after_state.strip()
print("restored to pre-drill state:" if not st else "!! DRILL LEFT RESIDUE:",
      st or "(yes)")
sys.exit(1 if (missed or st) else 0)
