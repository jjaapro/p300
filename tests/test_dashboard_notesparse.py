"""dashboard/notesparse.py — real-shaped reason blobs from every sleeve."""
from __future__ import annotations

import json

from dashboard import notesparse

CHENTO = json.dumps({
    "trigger": "chento_triple_v3", "variant_id": "bot_chento_v3_v1",
    "sleeve": "CHENTO_TRIPLE_V3", "bar_ts": "2026-08-21T19:30:00+00:00",
    "_entry_price": 77105.4, "_stop_price": 75041.15,
    "_target_price": 89490.9, "_atr_at_entry": 412.85, "_risk": 2064.25,
    "_inside_va": False, "_ladder_size_frac": 0.5,
    "_time_stop_iso": "2026-08-24T19:45:12.165476+00:00",
    "_filter_diag": {"no_tilt": "pass", "resist_ob_dist_R": 99.0,
                     "okx_delta_z": 0.1437},
    "_state": {"entry_price": 77105.4, "ladder_added": False,
               "last_walked_ts": "2026-08-24T15:00:00+00:00"},
})

ADX = json.dumps({
    "adx": 25.31, "ema50": 65428.7, "trend_ema": 64000.1,
    "trend_ema_len": 150, "close": 78338.03,
    "direction_rule": "close>ema50", "stop_loss_pct": 10.0,
    "funding_z": 0.4, "_atr_at_entry": 2124.3, "_stop_price": 70495.2,
})

CARRY = json.dumps({
    "fr_7d_avg_pct": 0.012, "threshold": 0.0, "fr_window_days": 7,
    "structure": "long_spot_short_perp_delta_neutral",
})

SSQ = json.dumps({
    "trigger": "short_squeeze", "variant_id": "bot_short_squeeze_v1",
    "sleeve": "SHORT_SQUEEZE", "bar_ts_utc": "2026-08-24T09:15:00+00:00",
    "bar_low": 61000.0, "bar_close": 61400.0, "perp_cvd": -512.3,
    "perp_cvd_pct": 0.05, "divergence": 2.1, "divergence_pct": 0.82,
    "close_in_range": 0.4, "prior_low_24x15m": 61010.0,
    "_stop_price": 60939.0, "_target_price": 62783.0,
    "_time_stop_iso": "2026-08-24T15:15:00+00:00",
})


def test_chento_plan_and_filters():
    p = notesparse.parse(CHENTO)
    assert p["error"] is None
    assert p["plan"]["stop_price"] == 75041.15
    assert p["plan"]["target_price"] == 89490.9
    assert p["plan"]["time_stop"].startswith("2026-08-24T19:45")
    assert p["plan"]["risk_price"] == 2064.25
    assert p["plan"]["inside_va"] is False
    assert p["decision"]["trigger"] == "chento_triple_v3"
    assert p["decision"]["filters"]["okx_delta_z"] == 0.1437
    assert p["decision"]["inputs"] == {}          # chento has no bare scalars
    # _state must never leak (it is large and mutates while open)
    assert "state" not in (p["plan"].get("other") or {})
    assert json.dumps(p).count("last_walked_ts") == 0


def test_adx_generic_inputs_no_target():
    p = notesparse.parse(ADX)
    assert p["plan"]["stop_price"] == 70495.2
    assert "target_price" not in p["plan"]
    assert p["decision"]["inputs"]["adx"] == 25.31
    assert p["decision"]["inputs"]["direction_rule"] == "close>ema50"
    assert p["decision"]["trigger"] is None


def test_carry_no_plan_at_all():
    p = notesparse.parse(CARRY)
    assert p["plan"] is None
    assert p["decision"]["inputs"]["structure"].startswith("long_spot")


def test_short_squeeze_shape():
    p = notesparse.parse(SSQ)
    assert p["plan"]["stop_price"] == 60939.0
    assert p["decision"]["inputs"]["divergence_pct"] == 0.82
    assert p["decision"]["inputs"]["bar_ts_utc"]     # not a _META key


def test_exit_lines_appended_after_newline():
    notes = CHENTO + "\nCHENTO_TRIPLE_V3_EXIT: stop_hit; fees=18bp RT, slip=0bp RT\n"
    p = notesparse.parse(notes)
    assert p["error"] is None
    assert p["exit_lines"] == [
        "CHENTO_TRIPLE_V3_EXIT: stop_hit; fees=18bp RT, slip=0bp RT"]


def test_malformed_json_never_raises():
    p = notesparse.parse("not json at all {")
    assert p["error"] is not None and "raw" in p["error"]
    assert p["plan"] is None and p["decision"] is None


def test_json_but_not_dict():
    p = notesparse.parse("[1, 2, 3]")
    assert p["error"] is not None


def test_none_and_empty():
    assert notesparse.parse(None)["error"] is None
    assert notesparse.parse("")["plan"] is None


def test_unknown_underscore_scalars_kept_in_other():
    p = notesparse.parse(json.dumps({"_stop_price": 1.0, "_new_dial": 7,
                                     "_nested": {"x": 1}}))
    assert p["plan"]["other"] == {"new_dial": 7}   # nested non-scalar dropped
