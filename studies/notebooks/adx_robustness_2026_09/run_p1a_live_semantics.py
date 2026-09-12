"""P1(a) — the shipped ADX configuration under the harness fill model vs live semantics (README §a)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import adx_lib as al

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex, bv, hz = al.ex, al.bv, al.hz
STUDY_END = "2026-06-25"
EXPECT = dict(n=27, ret_pct=2483, max_dd=-15.1, mar=3.09)


def main() -> None:
    out = {"env": ex.env_info(), "common_window_start": al.START}
    candles = al.daily_candles(0)
    closes = [c["close"] for c in candles]
    e150 = hz.ema(closes, 150)

    def short_gate(ctx, _e=e150):
        return ctx["new_dir"] != "short" or ctx["close"] < _e[ctx["i"]]

    # ── parity gate: the adx_study Tier-2 table on its own window ──────────
    c_study = [c for c in candles if c["dt"] <= STUDY_END]
    m = hz.run(c_study, "2018-01-01", entry_gate=short_gate, exit_mode="adx_or_atr", atr_mult=4.0)
    got = dict(n=m["n"], ret_pct=m["ret_pct"], max_dd=m["max_dd"], mar=m["mar"])
    ok = got["n"] == EXPECT["n"] and abs(got["ret_pct"] - EXPECT["ret_pct"]) < 5 and abs(got["max_dd"] - EXPECT["max_dd"]) < 0.2 and abs(got["mar"] - EXPECT["mar"]) < 0.05
    out["parity"] = dict(got=got, expected=EXPECT, pass_=bool(ok))
    print("parity (harness T2, study window):", got, "->", "PASS" if ok else "FAIL")

    # ── port check: the signal machine's entries == the harness's T2 entries ──
    m_full = hz.run(candles, al.START, entry_gate=short_gate, exit_mode="adx_or_atr", atr_mult=4.0)
    harness_entries = sorted(t["entry_dt"] for t in m_full["trades"])
    sig = al.signals(candles)
    port_entries = sorted(sig["dt"][i] for i in range(len(sig["entry"])) if sig["entry"][i] != 0 and sig["ts"][i] >= int(pd.Timestamp(al.START, tz="UTC").timestamp()))
    # the harness only logs an entry when flat; the port lists every entry signal — compare the harness set as a subset
    missing = sorted(set(harness_entries) - set(port_entries))
    out["port_check"] = dict(n_harness_entries=len(harness_entries), n_port_signals=len(port_entries), harness_not_in_port=missing)
    print("port check: harness entries", len(harness_entries), "port signals", len(port_entries), "harness entries missing from port:", missing)

    # ── harness fill model, full window, for the comparison ────────────────
    tr_h = [t for t in m_full["trades"] if not t.get("still_open")]
    start_ts = int(pd.Timestamp(al.START, tz="UTC").timestamp())
    keep = np.array([c["ts"] for c in candles]) >= start_ts       # common window only
    r_h = bv.ledger_to_daily_returns(tr_h, candles, cost_bp_rt=10.0)[keep]
    r_h15 = bv.ledger_to_daily_returns(tr_h, candles, cost_bp_rt=15.0)[keep]
    out["harness_fill"] = dict(ledger=dict(n=len(tr_h), mean_net_pct=float(np.mean([t["net_pct"] for t in tr_h])),
                                           reasons=pd.Series([t["reason"] for t in tr_h]).value_counts().to_dict(),
                                           ret_pct=m_full["ret_pct"], max_dd_tradeclose=m_full["max_dd"], mar_tradeclose=m_full["mar"]),
                               curve_10bp=al.curve_stats(pd.Series(r_h)), curve_15bp=al.curve_stats(pd.Series(r_h15)))
    print("harness fill (10 bp):", out["harness_fill"]["ledger"], out["harness_fill"]["curve_10bp"])

    # ── live semantics ─────────────────────────────────────────────────────
    path = ex.load_path("btc_1m")
    tr_l = al.live_walk(candles, path)
    r_l = al.mtm_returns(tr_l, path)
    out["live"] = dict(ledger=al.ledger_stats(tr_l), curve=al.curve_stats(r_l))
    tr_l_nf = al.live_walk(candles, path, with_funding=False)
    out["live_no_funding"] = dict(ledger=al.ledger_stats(tr_l_nf), curve=al.curve_stats(al.mtm_returns(tr_l_nf, path)))
    pd.DataFrame(tr_l).to_csv(al.RESULTS / "p1a_live_ledger.csv", index=False)
    pd.DataFrame(tr_h).to_csv(al.RESULTS / "p1a_harness_ledger.csv", index=False)
    r_l.to_csv(al.RESULTS / "p1a_live_daily_returns.csv", header=["ret"])
    print("live semantics (15 bp + funding):", out["live"]["ledger"], out["live"]["curve"])
    print("live semantics, no funding:", out["live_no_funding"]["curve"])
    out["gap"] = {k: out["live"]["curve"][k] - out["harness_fill"]["curve_10bp"][k] for k in ("cagr_pct", "mtm_maxdd_pct", "sharpe", "mar")}
    print("gap (live - harness 10bp):", out["gap"])
    al.jdump(out, "p1a_live_semantics.json")


if __name__ == "__main__":
    main()
