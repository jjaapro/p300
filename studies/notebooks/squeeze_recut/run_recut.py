"""Paired re-cut of the squeeze twins — CLI and report.

    python studies/notebooks/squeeze_recut/run_recut.py --sleeve both
    python studies/notebooks/squeeze_recut/run_recut.py --sleeve squeeze_bull --n-gate 30

Read-only against prod.db. Prints the pre-registered clause table and writes
results/recut_<sleeve>_<as_of>.json plus the union and paired CSVs.

Exit code 0 = CONTINUE or NOT_DUE, 1 = a DISABLE / RETIRE verdict, so this
can be run from a scheduled task without anyone reading the output.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import recut_lib as R  # noqa: E402

W = 78


def _fmt(v, nd=3) -> str:
    if v is None:
        return "    —"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:+.{nd}f}" if f == f else "    —"


def report(cfg: R.SleeveCfg, live, union, rep, div, verdict) -> str:
    L = ["=" * W,
         f"  SQUEEZE TWIN RE-CUT — {cfg.sleeve}".ljust(W - 26)
         + f"as of {verdict.as_of[:19]}Z",
         f"  pre-registration: {cfg.doc}  (written 2026-09-12, before any fire)",
         f"  gate: n = {verdict.n_gate} paired fires     "
         f"DSR trial count: {R.N_TRIALS} (fixed)",
         "=" * W, ""]

    n_stop = int((live.side == "stop").sum()) if len(live) else 0
    n_nost = int((live.side == "nostop").sum()) if len(live) else 0
    n_open = int(live.still_open.sum()) if len(live) else 0
    L += [f"  LIVE LEDGER      stop variant {n_stop:>3}      "
          f"no-stop variant {n_nost:>3}      still open {n_open:>3}",
          f"  UNION OF FIRES   {len(union):>3} trigger bars"
          + (f"   ({int((union.klass == 'BOTH').sum())} BOTH, "
             f"{int((union.klass == 'STOP_ONLY').sum())} stop-only, "
             f"{int((union.klass == 'NOSTOP_ONLY').sum())} nostop-only, "
             f"{int((union.klass == 'NEITHER').sum())} neither)"
             if len(union) else ""),
          f"  PAIRED FIRES     {verdict.n_paired:>3}   (both variants, same bar, both closed)",
          ""]

    if len(union) and "unexplained" in set(union.stop_block) | set(union.nostop_block):
        bad = union[(union.stop_block == "unexplained")
                    | (union.nostop_block == "unexplained")]
        L += ["  !! UNEXPLAINED SKIPS — a fire neither variant took and no open",
              "     position explains. This is an alarm, not a note:"]
        L += [f"       {r.bar_iso}  stop={r.stop_block} nostop={r.nostop_block}"
              for r in bad.head(10).itertuples()]
        L.append("")

    if len(live) and live.backstop_exit.any():
        n = int(live.backstop_exit.sum())
        L += [f"  !! {n} trade(s) closed by botlib.close_due_trades, which books the",
              "     15 bp default rather than this sleeve's measured cost. That is a",
              "     fidelity defect in the ledger, not a policy outcome (BACKLOG 4.4).",
              ""]

    if len(live) and live.legacy_ref_stop.any():
        L += [f"  note: {int(live.legacy_ref_stop.sum())} row(s) predate "
              "`_reference_stop_price`; R is measured against the sleeve's",
              "        flat 2% reference distance, which is exact for that period.", ""]

    if not div.empty:
        L += ["  DIVERGENCE — live vs the sleeve's own replay of the same fires", ""]
        L.append("    trade      side     R live   R replay    diff   funding  "
                 "backstop  RESIDUAL")
        for d in div.itertuples():
            L.append(f"    {d.id:<10} {d.side:<7} {_fmt(d.r_live)}  "
                     f"{_fmt(d.r_replay)}  {_fmt(d.diff_R)}  {_fmt(d.funding_R)}  "
                     f"{_fmt(d.backstop_cost_R)}  {_fmt(d.residual_R)}")
        mism = div[~div.exit_matches]
        if len(mism):
            L.append(f"    {len(mism)} exit-kind mismatch(es) live vs replay: "
                     + ", ".join(f"{m.id} {m.live_exit}->{m.replay_exit}"
                                 for m in mism.head(5).itertuples()))
        L.append("")

    L += ["  CLAUSES (transcribed from the calibration log; not editable here)", ""]
    for c in verdict.clauses:
        L.append(f"    [{c.verdict:^4}] {c.name}")
        L.append(f"           {c.text}")
        L.append(f"           measured {_fmt(c.value)}   threshold {c.threshold}"
                 + (f"   -> {c.action}" if c.verdict == "FAIL" else ""))
    L.append("")

    for n in verdict.notes:
        L.append(f"  {n}")
    if verdict.notes:
        L.append("")

    L += ["-" * W, f"  VERDICT: {verdict.outcome}", "-" * W]
    if verdict.outcome.startswith(("DISABLE", "RETIRE")):
        L += ["  Act by setting the variant's enabled = 0 AND removing it from the",
              "  bot's VARIANTS list, then restarting that bot. enabled = 0 alone",
              "  does NOT stop a bot runner — the runners do not read the column.",
              "  Do not edit thresholds."]
    return "\n".join(L)


def run_one(key: str, as_of: datetime, n_gate: int | None,
            rebuild_cache: bool) -> tuple[str, R.Verdict]:
    cfg = R.SLEEVES[key]
    live = R.load_live(cfg, as_of)
    t0 = (datetime.fromisoformat(live.entry_time.min())
          if len(live) and live.entry_time.notna().any()
          else datetime(2026, 9, 12, tzinfo=timezone.utc))
    try:
        fires = R.reconstruct_fires(cfg, t0, as_of)
    except Exception as e:  # noqa: BLE001
        print(f"  ! fire reconstruction failed for {cfg.sleeve}: {e!r}",
              file=sys.stderr)
        fires = []
    union = R.build_union(live, fires, cfg, as_of)
    try:
        rep = R.replay(cfg, union, as_of, rebuild_cache=rebuild_cache)
    except Exception as e:  # noqa: BLE001
        print(f"  ! replay failed for {cfg.sleeve}: {e!r}", file=sys.stderr)
        import pandas as pd
        rep = pd.DataFrame()
    div = R.divergence(cfg, live, rep)
    gate = n_gate or (20 if len(R.paired_frame(live, union)) < 30 else 30)
    v = R.decide(cfg, live, union, div, as_of, gate)

    stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    R.RESULTS.joinpath(f"recut_{key}_{stamp}.json").write_text(json.dumps(
        dict(sleeve=cfg.sleeve, as_of=v.as_of, n_gate=v.n_gate,
             n_paired=v.n_paired, outcome=v.outcome, notes=v.notes,
             clauses=[c.__dict__ for c in v.clauses],
             n_live_stop=int((live.side == "stop").sum()) if len(live) else 0,
             n_live_nostop=int((live.side == "nostop").sum()) if len(live) else 0,
             n_union=len(union), n_reconstructed_fires=len(fires)),
        indent=1, default=str), encoding="utf-8")
    if len(union):
        union.to_csv(R.RESULTS / f"union_{key}_{stamp}.csv", index=False)
    if not div.empty:
        div.to_csv(R.RESULTS / f"divergence_{key}_{stamp}.csv", index=False)
    return report(cfg, live, union, rep, div, v), v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sleeve", choices=["squeeze_bull", "short_squeeze", "both"],
                    default="both")
    ap.add_argument("--as-of", default=None,
                    help="ISO instant; default now. Fixes the truncation point.")
    ap.add_argument("--n-gate", type=int, choices=[20, 30], default=None,
                    help="Which pre-registered re-cut point. Default: auto.")
    ap.add_argument("--rebuild-cache", action="store_true",
                    help="Force exec_lib to rebuild the 1m price cache.")
    a = ap.parse_args(argv)

    as_of = (datetime.fromisoformat(a.as_of) if a.as_of
             else datetime.now(timezone.utc))
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    keys = (["squeeze_bull", "short_squeeze"] if a.sleeve == "both"
            else [a.sleeve])
    bad = False
    for k in keys:
        text, v = run_one(k, as_of, a.n_gate, a.rebuild_cache)
        print(text)
        print()
        bad = bad or v.outcome.startswith(("DISABLE", "RETIRE"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
