"""Byte-equality parity: study features vs the LIVE PDO sleeve's own functions.

The repo's standing rule for ports is "assert feature values match the source
at known timestamps".  This does it at every timestamp the study actually
trades on, plus a large deterministic random sample.

Mechanics: the sleeve computes its features from ``clock.now_utc()``, so we
freeze the clock at each test timestamp and call the sleeve's real functions.
The sleeve opens ``db.TRADER_DB`` read-write; to honour the read-only rule we
swap the ``sqlite3`` handle *inside the imported module object* for a shim
that hands back a ``?mode=ro`` connection (the sleeve source file is not
touched, and the shim also caches the connection so 20k calls stay fast).

Exit code 0 = all parity assertions passed.  Non-zero = the study must be
reported INCONCLUSIVE.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from studies.notebooks.pdo_adjacents import common as C            # noqa: E402
from studies.notebooks.pdo_adjacents import question_a_regime as QA  # noqa: E402
from strategies.support import clock, db                            # noqa: E402
import studies.material.archive.pdo.signal as SLEEVE  # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

N_RANDOM = 2000
SEED = 42


class _NoCloseConn:
    """Wraps a live connection so the sleeve's ``con.close()`` is a no-op."""

    def __init__(self, con):
        self._con = con

    def execute(self, *a, **k):
        return self._con.execute(*a, **k)

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._con, name)


class _ROSqliteShim:
    """Drop-in for the ``sqlite3`` module inside the sleeve: read-only + cached."""

    def __init__(self):
        self._cache: dict[str, _NoCloseConn] = {}
        self.n_connects = 0

    def connect(self, path, *a, **k):
        key = str(path)
        if key not in self._cache:
            self._cache[key] = _NoCloseConn(
                sqlite3.connect("file:" + key + "?mode=ro", uri=True))
            self.n_connects += 1
        return self._cache[key]

    def __getattr__(self, name):
        return getattr(sqlite3, name)


def main() -> int:
    shim = _ROSqliteShim()
    SLEEVE.sqlite3 = shim

    with C.ro_conn() as con:
        last_ts = int(con.execute(
            "SELECT MIN(m) FROM (SELECT MAX(open_time)/1000 AS m FROM btc_1m "
            "UNION ALL SELECT MAX(open_time)/1000 FROM eth_1m)").fetchone()[0])
    end_ts = last_ts - (last_ts % C.HOUR)
    hours = C.hour_range(C.STUDY_START, end_ts)

    regime = C.BtcRegime()
    rng = np.random.default_rng(SEED)

    report = {"assets": {}, "n_random": N_RANDOM, "seed": SEED,
              "sleeve_db_connections": 0}
    failures: list[dict] = []
    total_checks = 0

    for asset in ("BTC", "ETH"):
        bars = C.AssetBars(asset)
        fires = QA.scan_fires(asset, bars, regime, hours)
        entry_ts: set[int] = set()
        for thr in QA.THRESHOLDS.values():
            tr = QA.simulate(asset, bars, fires, thr, C.COST_BP_RT)
            if len(tr):
                entry_ts |= {int(x) for x in tr["entry_ts"].tolist()}
        sample = rng.choice(hours, size=min(N_RANDOM, len(hours)), replace=False)
        test_ts = sorted(entry_ts | {int(x) for x in sample.tolist()})

        n_fail = 0
        for t in test_ts:
            clock.set_simulated_now(datetime.fromtimestamp(t, tz=timezone.utc))
            # --- sleeve side ---
            s_sig = SLEEVE._load_today_open_and_pdo(asset)
            s_hb = SLEEVE._get_hourly_bar_for_today(asset)
            s_reg = SLEEVE._btc_30d_return_pct()
            # --- study side ---
            v_sig = bars.pdo_cdo_at(t)
            v_hb = bars.hour_bar_closing_at(t)
            v_reg = regime.at(t)

            probs = []
            if (s_sig is None) != (v_sig is None):
                probs.append(("pdo_block_none", s_sig, v_sig))
            elif s_sig is not None:
                if s_sig["pdo"] != v_sig["pdo"]:
                    probs.append(("pdo", s_sig["pdo"], v_sig["pdo"]))
                if s_sig["today_open"] != v_sig["today_open"]:
                    probs.append(("today_open", s_sig["today_open"], v_sig["today_open"]))
                if s_sig["gap_pct"] != v_sig["gap_pct"]:
                    probs.append(("gap_pct", s_sig["gap_pct"], v_sig["gap_pct"]))
                exp_day = C.dstr(v_sig["bar_day"] * C.DAY)
                if s_sig["date"] != exp_day:
                    probs.append(("bar_day", s_sig["date"], exp_day))
            if (s_hb is None) != (v_hb is None):
                probs.append(("hour_block_none", s_hb, v_hb))
            elif s_hb is not None:
                for k, i in (("low", 0), ("high", 1), ("close", 2)):
                    if s_hb[k] != v_hb[i]:
                        probs.append((f"hour_{k}", s_hb[k], v_hb[i]))
            if (s_reg is None) != (v_reg is None):
                probs.append(("regime_none", s_reg, v_reg))
            elif s_reg is not None and s_reg != v_reg:
                probs.append(("btc_30d_pct", s_reg, v_reg))

            total_checks += 1
            if probs:
                n_fail += 1
                if len(failures) < 40:
                    failures.append({"asset": asset, "ts": t, "iso": C.iso(t),
                                     "problems": [[p[0], repr(p[1]), repr(p[2])]
                                                  for p in probs]})
        report["assets"][asset] = {
            "n_entry_timestamps": len(entry_ts),
            "n_random_timestamps": int(min(N_RANDOM, len(hours))),
            "n_tested": len(test_ts),
            "n_failed": n_fail,
        }
        print(f"{asset}: tested {len(test_ts)} timestamps "
              f"({len(entry_ts)} entry + random), failures={n_fail}")

    clock.set_simulated_now(None)
    report["sleeve_db_connections"] = shim.n_connects
    report["total_checks"] = total_checks
    report["failures_sample"] = failures
    report["passed"] = (len(failures) == 0
                        and all(v["n_failed"] == 0 for v in report["assets"].values()))
    report["db_path"] = str(db.PROD_DB)
    (RESULTS / "parity.json").write_text(json.dumps(report, indent=2, default=str))
    print("PARITY PASSED" if report["passed"] else "PARITY FAILED")
    if failures:
        print(json.dumps(failures[:5], indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
