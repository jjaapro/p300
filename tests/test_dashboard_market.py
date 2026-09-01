"""dashboard/market.py — flow / positioning / profile over the synthetic
fixture, plus byte-equivalence pins to the sleeves' own math so the
dashboard can never disagree with what the bots see."""
from __future__ import annotations

import sqlite3
import sys
import time as _t
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from dashboard import market, queries

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _dashboard_fixture as fx  # noqa: E402

TFS = ("15m", "1h", "4h", "1d")


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    p = fx.build_fixture_db(tmp_path / "prod.db")
    monkeypatch.setattr("strategies.support.db.PROD_DB", p)
    # sleeve loaders / funding.py open TRADER_DB (a separate module name)
    monkeypatch.setattr("strategies.support.db.TRADER_DB", p)
    market._thr_cache.clear()
    market._ssq_cache.clear()
    return p


def _by_time(payload: dict) -> dict[int, dict]:
    return {b["time"]: b for b in payload["bars"]}


def _fixture_hour(p) -> int:
    con = sqlite3.connect(str(p))
    try:
        return int(con.execute("SELECT MAX(timestamp) FROM cd_futures_ohlcv")
                   .fetchone()[0])
    finally:
        con.close()


# ─── alignment with /api/candles ──────────────────────────────────────────────

@pytest.mark.parametrize("asset,tf", [(a, tf) for a in ("BTC", "ETH") for tf in TFS])
def test_flow_times_equal_candle_times(fixture_db, asset, tf):
    cd, fd = queries.candles(asset, tf), market.flow(asset, tf)
    assert [b["time"] for b in fd["bars"]] == [b["time"] for b in cd["bars"]]
    assert fd["last_time"] == cd["last_time"]
    assert fd["source"] == cd["source"]
    cd10, fd10 = queries.candles(asset, tf, bars=10), market.flow(asset, tf, bars=10)
    assert [b["time"] for b in fd10["bars"]] == [b["time"] for b in cd10["bars"]]
    if cd["last_time"]:
        cda = queries.candles(asset, tf, after=cd["last_time"])
        fda = market.flow(asset, tf, after=cd["last_time"])
        assert [b["time"] for b in fda["bars"]] == [b["time"] for b in cda["bars"]]


def test_flow_after_mode_reproduces_last_bar(fixture_db):
    fd = market.flow("BTC", "1h")
    fa = market.flow("BTC", "1h", after=fd["last_time"])
    # the incremental fetch must carry enough history to recompute the
    # last bar's OI delta / carried values identically
    assert fa["bars"][0] == fd["bars"][-1]
    assert fa["bars"][0]["oi_delta_pct"] is not None


def test_flow_bad_params(fixture_db):
    with pytest.raises(ValueError):
        market.flow("BTC", "5m")
    with pytest.raises(ValueError):
        market.flow("DOGE", "1h")


# ─── values ───────────────────────────────────────────────────────────────────

def test_flow_native_15m_values(fixture_db):
    fd = market.flow("BTC", "15m")
    by = _by_time(fd)
    q15 = max(by)                                   # k=0 bar of the fixture
    hour = (q15 // 3600) * 3600
    for k in (0, 1, 3, 10, 11, 17, 40):
        b = by[q15 - k * 900]
        px, vb, vs = fx.perp15(k)
        assert b["perp_cvd"] == pytest.approx(vb - vs)
        s = fx.spot15(k)
        if s is None:                                # every 10th spot row missing
            assert b["spot_cvd"] is None
            assert b["divergence"] is None
            assert b["basis_bp"] is None
        else:
            sc, sb, ss = s
            assert b["spot_cvd"] == pytest.approx(sb - ss)
            assert b["divergence"] == pytest.approx((sb - ss) - (vb - vs))
            perp_close, spot_close = px + 10, sc + 10
            assert b["basis_bp"] == pytest.approx(
                (perp_close - spot_close) / spot_close * 1e4, abs=0.01)
    # old-style rows without taker columns (SJ-3 era) -> null CVD, still present
    assert any(b["perp_cvd"] is None for b in fd["bars"])
    # funding: newest bar carries the latest settlement (j=0 -> 1e-4)
    assert by[q15]["funding"] == pytest.approx(fx.funding_rate(0))
    # OI: hourly row carried across the 15m bars of its hour ...
    assert by[q15]["oi_close"] == fx.oi_close(0)
    assert by[hour - 7 * 3600 + 900]["oi_close"] == fx.oi_close(7)
    # ... and the i=5 gap: carried 4 bars from the i=6 row, then null
    assert by[hour - 5 * 3600]["oi_close"] == fx.oi_close(6)
    assert by[hour - 5 * 3600 + 900]["oi_close"] is None
    assert by[hour - 4 * 3600]["oi_close"] == fx.oi_close(4)
    assert by[hour - 4 * 3600]["oi_delta_pct"] is None      # previous bar unknown
    # close-to-close delta at an hourly row: (1070 - 1080) / 1080
    assert by[hour - 7 * 3600]["oi_delta_pct"] == pytest.approx(
        (fx.oi_close(7) - fx.oi_close(8)) / fx.oi_close(8) * 100, abs=1e-4)
    assert by[hour - 7 * 3600 + 900]["oi_delta_pct"] == 0.0   # carried -> 0


def test_flow_bucketed_4h(fixture_db):
    fd = market.flow("BTC", "4h")
    hour = _fixture_hour(fixture_db)
    assert fd["bars"]
    assert fd["bars"][-1]["time"] + 14400 <= _t.time()        # in-progress dropped
    prev = None
    for bar in fd["bars"]:
        members = [i for i in range(30)
                   if ((hour - i * 3600) // 14400) * 14400 == bar["time"]]
        assert members
        c, vb, vs = zip(*(fx.hourly_perp(i) for i in members))
        assert bar["perp_cvd"] == pytest.approx(sum(vb) - sum(vs))
        assert bar["spot_cvd"] == pytest.approx(5.0 * len(members))   # (50+i)-(45+i)
        i0 = min(members)                                     # last row in bucket
        pc, sc = fx.hourly_perp(i0)[0], fx.hourly_spot_close(i0)
        assert bar["basis_bp"] == pytest.approx((pc - sc) / sc * 1e4, abs=0.01)
        oi_members = [i for i in members if i != 5]
        if oi_members:
            assert bar["oi_close"] == fx.oi_close(min(oi_members))
        if prev and prev["oi_close"] and bar["oi_close"]:
            assert bar["oi_delta_pct"] == pytest.approx(
                (bar["oi_close"] - prev["oi_close"]) / prev["oi_close"] * 100,
                abs=1e-3)
        # settlements only (the 0.5 hourly rows are filtered), always carried
        assert bar["funding"] in (pytest.approx(1e-4), pytest.approx(2e-4),
                                  pytest.approx(3e-4))
        prev = bar


def test_flow_eth_degrades(fixture_db):
    fd = market.flow("ETH", "15m")
    assert fd["spot_source"] is None and fd["oi_source"] is None
    assert fd["ssq"] is None
    assert fd["bars"]
    for b in fd["bars"]:
        for k in ("spot_cvd", "divergence", "oi_close", "oi_delta_pct",
                  "basis_bp", "label"):
            assert b[k] is None, k
        assert b["perp_cvd"] is not None
    by = _by_time(fd)
    q15 = max(by)
    _, vb, vs = fx.eth15(0)
    assert by[q15]["perp_cvd"] == pytest.approx(vb - vs)
    assert by[q15]["funding"] == pytest.approx(fx.funding_rate(0))
    tape = fd["tape"]["24h"]
    assert tape["oi_pct"] is None and tape["spot_cvd"] is None
    assert tape["perp_cvd"] is not None and "perp CVD" in tape["text"]
    assert "OI" not in tape["text"] and "spot" not in tape["text"]
    assert "funding" in tape["text"]


def test_tape_btc_has_all_parts(fixture_db):
    fd = market.flow("BTC", "1h")
    for w in ("4h", "24h"):
        d = fd["tape"][w]
        assert d["window_h"] == int(w[:-1])
        assert d["price_pct"] is not None and d["perp_cvd"] is not None
        assert d["spot_cvd"] is not None and d["oi_pct"] is not None
        assert d["funding"] == pytest.approx(fx.funding_rate(0))
        assert d["text"].startswith(f"{w}: price ")
        assert "OI" in d["text"] and "spot CVD" in d["text"]


def test_label_rule():
    L = market._label
    assert L(5, 1, 1, 0.5) == "longs_opening"
    assert L(5, -1, 1, 0.5) == "short_covering"
    assert L(-5, 1, 1, 0.5) == "shorts_opening"
    assert L(-5, -1, 1, 0.5) == "longs_closing"
    assert L(0.5, 1, 1, 0.5) is None          # |cvd| below threshold
    assert L(5, 0.2, 1, 0.5) is None          # |doi| below threshold
    assert L(0, 1, 1, 0.5) is None
    assert L(5, 0, 1, 0.5) is None
    assert L(None, 1, 1, 0.5) is None
    assert L(5, None, 1, 0.5) is None
    assert L(5, 1, None, 0.5) is None         # no pool yet -> no labels
    assert L(5, 1, 0.0, 0.0) == "longs_opening"   # sign-only (tape read)


# ─── byte-equivalence with the sleeves ───────────────────────────────────────

def test_pct_rank_matches_short_squeeze_math():
    from strategies.sleeves.short_squeeze import math as ssq_math
    dist = np.array([-3.0, -1.0, 0.0, 0.0, 2.0, 5.0])
    for v in (-4.0, -1.0, 0.0, 0.5, 5.0, 9.0):
        assert market._pct_rank_incl(v, dist) == ssq_math.percentile_rank(v, dist)
    empty = np.array([])
    assert market._pct_rank_incl(1.0, empty) == ssq_math.percentile_rank(1.0, empty)


def test_ssq_pool_matches_sleeve_loader(fixture_db):
    from strategies.sleeves.short_squeeze import math as ssq_math
    from strategies.sleeves.short_squeeze import signal as ssq_signal
    from strategies.sleeves.short_squeeze.config import WINDOW_DAYS
    now = datetime.now(timezone.utc)
    con = queries._ro_con()
    try:
        perp, div = market._ssq_pool(con, now)
        latest = market._ssq_latest(con, now)
    finally:
        con.close()
    bars = ssq_signal._load_recent_15m_bars(now, WINDOW_DAYS)
    assert len(bars) == len(perp) > 0
    assert list(perp) == [b["perp_cvd"] for b in bars]
    assert list(div) == [b["divergence"] for b in bars]
    sl = ssq_signal._load_latest_15m_bar(now)
    assert (latest is None) == (sl is None)
    if sl is not None:
        assert latest == {"ts": sl["ts"], "perp_cvd": sl["perp_cvd"],
                          "divergence": sl["divergence"]}
        g = market.flow("BTC", "15m")["ssq"]
        assert g["pool_n"] == len(bars)
        assert g["perp_cvd_pct"] == round(
            ssq_math.percentile_rank(sl["perp_cvd"], perp), 3)
        assert g["divergence_pct"] == round(
            ssq_math.percentile_rank(sl["divergence"], div), 3)


def test_basis_and_oi_match_chento_limit_bid(fixture_db):
    pytest.importorskip("pandas")
    from strategies.sleeves.chento_limit_bid import signal as clb
    now = datetime.now(timezone.utc)
    df = clb._load_15m_enriched(now, hours_back=26)
    assert len(df) > 50
    by = _by_time(market.flow("BTC", "15m"))
    checked, oi_gap = 0, 0
    for idx, row in df.iterrows():
        ts = int(idx.timestamp()) if hasattr(idx, "timestamp") else int(idx)
        b = by.get(ts)
        if b is None:
            continue
        assert b["basis_bp"] == pytest.approx(row["basis_bp"], abs=0.01), ts
        if b["oi_close"] is None:
            oi_gap += 1                     # positional vs bar-count ffill at the gap
        else:
            assert b["oi_close"] == pytest.approx(row["oi"]), ts
        checked += 1
    assert checked > 50 and oi_gap <= 1
    # funding is deliberately NOT compared: the sleeve reads raw rows while
    # the dashboard keeps settlements only (strategies/support/funding.py).


def test_funding_daily_means_replica(fixture_db):
    from strategies.support import funding
    now_s = int(_t.time())
    con = queries._ro_con()
    try:
        mine = market._funding_daily_means(con, "BTC", now_s)
    finally:
        con.close()
    assert mine == funding.daily_means_rate("BTC", now_s)
    assert len(mine) >= 9
    assert all(v < 0.01 for v in mine.values())      # 0.5 hourly rows filtered


def test_cpr_gate_replica():
    from strategies.sleeves.timing_anomalies.internal.cpr.config import (
        PCTILE_THRESHOLD, PCTILE_WINDOW)
    panel = date(2026, 8, 31)
    dates = [(panel - timedelta(days=k)).isoformat() for k in range(220)]
    lsr = {d: 1.0 + ((i * 7) % 50) / 100 for i, d in enumerate(dates)}
    fund = {d: 1e-4 * ((i * 3) % 11 - 5) for i, d in enumerate(dates)}
    g = market._cpr_gate(lsr, fund, panel)
    assert g["reason"] is None
    win = [(panel - timedelta(days=k)).isoformat() for k in range(PCTILE_WINDOW, 0, -1)]
    ls_p = np.percentile([lsr[d] for d in win], PCTILE_THRESHOLD * 100)
    assert g["ls_p20"] == pytest.approx(ls_p, abs=1e-4)
    assert g["ls_ok"] == (lsr[panel.isoformat()] <= ls_p)
    keys = sorted(fund)
    i = keys.index(panel.isoformat())
    assert g["fund_3d"] == pytest.approx(
        np.mean([fund[k] for k in keys[i - 2:i + 1]]), abs=1e-8)
    thin = market._cpr_gate(lsr, {d: fund[d] for d in dates[:10]}, panel)
    assert thin["reason"] == "pctile_window_too_thin"
    assert market._cpr_gate({}, fund, panel)["reason"] == "missing_data"


def test_ls_circuit_breaker_matches_regime_jplus():
    from strategies.support import regime_jplus as regime
    n = 120
    dates = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]
    bc = [100.0 * (1 + 0.001 * i) for i in range(n)]        # strong_bull throughout
    ls_d = {d: (55.0 if i < 63 else 30.0) for i, d in enumerate(dates)}  # 25pt drop
    modes = regime.classify_series(dates, bc, ls_d)
    active = [d for d in dates[60:100]
              if market._ls_circuit_breaker(ls_d, d)["active"]]
    assert active == [d for d in dates[60:100] if modes[d] == "uncertain"]
    assert len(active) >= 7
    quiet = {d: 50.0 + (i % 5) for i, d in enumerate(dates)}
    assert market._ls_circuit_breaker(quiet, dates[-1])["active"] is False


# ─── positioning + profile ────────────────────────────────────────────────────

def test_positioning_rank_and_scoreboard(fixture_db):
    pz = market.positioning("BTC")
    ratios = [fx.lsr_ratio("BTC", k) for k in range(400)]     # k=0 newest
    exp = sum(1 for v in ratios[1:366] if v < ratios[0]) / 365
    assert pz["pct_rank_365"] == pytest.approx(exp, abs=1e-4)
    assert pz["decile"] == min(10, int(exp * 10) + 1)
    assert pz["decile_label"] == f"D{pz['decile']}"
    assert pz["latest"]["ratio"] == pytest.approx(ratios[0])
    assert pz["latest"]["stale"] is False
    ns = [d["n"] for d in pz["decile_stats"]]
    assert len(ns) == 10 and sum(ns) == pz["uncond"]["n"] > 0
    assert pz["cpr"]["reason"] == "pctile_window_too_thin"   # 10 days of funding
    assert pz["regime_cb"]["active"] is False
    assert pz["regime_cb"]["shift"] is not None
    eth = market.positioning("ETH")
    assert eth["regime_cb"] is None
    assert eth["latest"]["ratio"] == pytest.approx(fx.lsr_ratio("ETH", 0))
    with pytest.raises(ValueError):
        market.positioning("DOGE")


def test_profile_conserves_delta(fixture_db):
    pf = market.profile("BTC", hours=24, buckets=24)
    assert len(pf["buckets"]) == 24
    los = [b["lo"] for b in pf["buckets"]]
    assert los == sorted(los, reverse=True)                  # high -> low
    assert sum(b["delta"] for b in pf["buckets"]) == pytest.approx(
        pf["total_delta"], abs=1e-3)
    exp_total = sum(fx.perp15(k)[1] - fx.perp15(k)[2] for k in range(pf["n_bars"]))
    assert pf["total_delta"] == pytest.approx(exp_total)
    assert pf["max_abs"] == pytest.approx(
        max(abs(b["delta"]) for b in pf["buckets"]), abs=1e-3)
    assert pf["price_lo"] <= pf["last_price"] <= pf["price_hi"]
    with pytest.raises(ValueError):
        market.profile("BTC", buckets=1)
    with pytest.raises(ValueError):
        market.profile("BTC", hours=0)


def test_profile_zero_range_bar_lands_in_one_bucket(tmp_path, monkeypatch):
    p = tmp_path / "flat.db"
    con = sqlite3.connect(str(p))
    con.execute(f"CREATE TABLE cd_futures_15m ({fx._TAKER_SCHEMA})")
    q15 = int(_t.time() // 900) * 900
    con.execute("INSERT INTO cd_futures_15m VALUES (?,?,?,?,?,?,?,?)",
                (q15, 100, 110, 90, 105, 10, 7, 3))            # delta +4 over 90..110
    con.execute("INSERT INTO cd_futures_15m VALUES (?,?,?,?,?,?,?,?)",
                (q15 - 900, 100, 100, 100, 100, 10, 2, 5))     # flat bar, delta -3
    con.commit()
    con.close()
    monkeypatch.setattr("strategies.support.db.PROD_DB", p)
    pf = market.profile("BTC", hours=1, buckets=4)
    deltas = {round(b["lo"], 2): b["delta"] for b in pf["buckets"]}
    # +4 spread evenly over four 5-wide buckets, -3 entirely in the bucket
    # holding 100 (its lower edge): [100, 105)
    assert deltas[90.0] == pytest.approx(1.0)
    assert deltas[95.0] == pytest.approx(1.0)
    assert deltas[100.0] == pytest.approx(1.0 - 3.0)
    assert deltas[105.0] == pytest.approx(1.0)
    assert pf["total_delta"] == pytest.approx(1.0)
