"""Flat Maximum / Parameter Sensitivity Test.

Ported verbatim from the trader repo root module ``flat_max_test.py``
(``flat_max_1d``, ``flat_max_report``).

Reference: Pardo, R. (2008). "The Evaluation and Optimization of Trading
           Strategies." Wiley. Section on robustness.

Core idea: plot strategy performance (Sharpe, CAGR, etc.) across a range
of parameter values around the chosen operating point. A ROBUST strategy
has a FLAT performance surface -- small parameter changes produce small
performance changes. An OVERFIT strategy has a SHARP PEAK -- the chosen
parameter is on a knife-edge and performance drops sharply with any
perturbation.

Quantitative criterion (Pardo):
    flat_max_score = (local_mean - global_max) / local_std
    where local = performance in a neighborhood around the chosen parameter.

    flat_max_score > -0.5  -->  ROBUST (small drop from peak vs local noise)
    flat_max_score < -1.5  -->  SHARP PEAK (overfit risk)

A simpler sufficient statistic:
    relative_drop = (performance_neighbors_mean - performance_at_chosen) / performance_at_chosen

    |relative_drop| < 0.10  -->  flat (10% perf change for param perturbation)
    |relative_drop| > 0.25  -->  sharp peak

Note on the score: with a 5-point neighbourhood the flat-max score is a
shape statistic (nearly scale-free), so "FLAT" requires most local points
to sit at the top; a smooth hump of any amplitude grades "OK".

USAGE:
    from studies.lib.validation.flat_max import flat_max_1d, flat_max_report

    results = flat_max_1d(
        param_values=[0.3, 0.4, 0.5, 0.6, 0.7],
        chosen=0.5,
        score_fn=lambda p: backtest_with_param(p),
    )
    flat_max_report(results, param_name="vt50_threshold")
"""
from __future__ import annotations

import math
from typing import Any, Callable, Sequence


def flat_max_1d(param_values: Sequence[float],
                chosen: float,
                score_fn: Callable[[float], float],
                neighborhood_size: int = 2) -> dict[str, Any]:
    """1D parameter flat-max test.

    param_values: list of param values to test (sorted).
    chosen: the currently-chosen value (should be in param_values).
    score_fn: f(param) -> scalar score (Sharpe, CAGR, etc.).
    neighborhood_size: how many values to each side of `chosen` count as "local".

    Returns dict with scores, chosen_score, peak_score, flat_max metrics.
    """
    if chosen not in param_values:
        raise ValueError(f"chosen={chosen} not in param_values={param_values}")
    scores = {p: score_fn(p) for p in param_values}
    sorted_params = sorted(param_values)
    chosen_idx = sorted_params.index(chosen)
    chosen_score = scores[chosen]

    peak_score = max(scores.values())
    peak_param = max(scores, key=scores.get)

    # Local neighborhood
    lo = max(0, chosen_idx - neighborhood_size)
    hi = min(len(sorted_params), chosen_idx + neighborhood_size + 1)
    local_params = sorted_params[lo:hi]
    local_scores = [scores[p] for p in local_params]
    local_mean = sum(local_scores) / len(local_scores)
    local_std = math.sqrt(sum((s - local_mean) ** 2 for s in local_scores) / max(len(local_scores) - 1, 1))

    # Flat max score
    flat_max_score = (local_mean - peak_score) / local_std if local_std > 1e-9 else 0.0

    # Relative drop from chosen to neighbors
    other_local = [s for p, s in zip(local_params, local_scores) if p != chosen]
    other_local_mean = sum(other_local) / len(other_local) if other_local else chosen_score
    relative_drop = (other_local_mean - chosen_score) / abs(chosen_score) if abs(chosen_score) > 1e-9 else 0.0

    # Verdict
    if abs(relative_drop) < 0.10 and flat_max_score > -0.5:
        verdict = "FLAT"
    elif abs(relative_drop) < 0.25 and flat_max_score > -1.5:
        verdict = "OK"
    elif flat_max_score < -1.5 or abs(relative_drop) > 0.40:
        verdict = "SHARP_PEAK"
    else:
        verdict = "MARGINAL"

    return {
        "param_values": sorted_params,
        "scores": scores,
        "chosen": chosen,
        "chosen_score": chosen_score,
        "peak_param": peak_param,
        "peak_score": peak_score,
        "chosen_is_peak": peak_param == chosen,
        "local_params": local_params,
        "local_mean": local_mean,
        "local_std": local_std,
        "flat_max_score": flat_max_score,
        "relative_drop": relative_drop,
        "verdict": verdict,
    }


def flat_max_report(result: dict[str, Any], param_name: str) -> None:
    """Print the score surface and verdict of a ``flat_max_1d`` result."""
    print(f"\n  Parameter: {param_name}")
    print(f"  Chosen value: {result['chosen']}  -->  score {result['chosen_score']:+.3f}")
    print(f"  Peak value:   {result['peak_param']}  -->  score {result['peak_score']:+.3f}  "
          f"{'(chosen is peak)' if result['chosen_is_peak'] else '(chosen is not peak)'}")
    print(f"\n  Score surface:")
    for p in result["param_values"]:
        s = result["scores"][p]
        marker = " <-- chosen" if p == result["chosen"] else (" <-- peak" if p == result["peak_param"] else "")
        bar_len = int(max(0, s) * 10) if s > 0 else 0
        print(f"    {p:>8.3f}  {s:>+7.3f}  {'#' * bar_len}{marker}")
    print(f"\n  Local (neighborhood of {len(result['local_params'])}): mean {result['local_mean']:+.3f}, "
          f"std {result['local_std']:.3f}")
    print(f"  Flat-max score: {result['flat_max_score']:+.3f} (>-0.5 flat, <-1.5 sharp)")
    print(f"  Relative drop:  {result['relative_drop']*100:+.1f}% (chosen vs local mean)")
    print(f"  VERDICT: {result['verdict']}")
