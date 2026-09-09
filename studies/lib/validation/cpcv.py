"""Combinatorial Purged Cross-Validation (CPCV).

Ported verbatim from the trader repo root module ``cpcv.py`` (its ``sharpe``
helper lives in ``metrics``).

Reference: Lopez de Prado, M. (2018). "Advances in Financial Machine Learning",
           Chapter 12 (purged k-fold) + extensions.

CPCV splits time-ordered data into N groups, evaluates C(N, k) combinations
where k groups are held out as test, and purges training observations within
an embargo window of any test observation to prevent autocorrelation leakage.

Critical for strategies where:
  - Daily returns are autocorrelated (holds >= 1 day overlap between train/test)
  - Features use trailing windows (60-day regression, rolling vol, etc.)
  - Trade PnL is attributed across multi-day holds (S-003 smearing)

Why this matters more than simple train/test split: a 2020-2024 train / 2025+
test split has ZERO purging -- the last training day feeds directly into the
first test day via any rolling feature.

USAGE:
    from studies.lib.validation.cpcv import cpcv_score
    from studies.lib.validation.metrics import sharpe

    # Score a strategy across all CPCV combinations:
    scores = cpcv_score(
        daily_returns=[(date, pnl), ...],
        score_fn=sharpe,
        n_groups=10,
        k_test=2,       # 2 test groups per split -> C(10,2) = 45 splits
        embargo_days=5, # purge training days within 5 days of test
    )
"""
from __future__ import annotations

import itertools
import math
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence


def parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def days_between(d1: str, d2: str) -> int:
    return abs((parse_date(d1) - parse_date(d2)).days)


def build_groups(dates: Sequence[str], n_groups: int) -> list[list[int]]:
    """Split sorted dates into n_groups equal-size contiguous index groups."""
    n = len(dates)
    size = n // n_groups
    groups = [list(range(i * size, (i + 1) * size)) for i in range(n_groups)]
    # Last group gets remainder
    groups[-1] = list(range((n_groups - 1) * size, n))
    return groups


def purge_train(train_idx: Sequence[int], test_idx: Sequence[int],
                dates: Sequence[str], embargo_days: int) -> list[int]:
    """Remove training indices whose date is within embargo_days of any test date."""
    if embargo_days <= 0:
        return list(train_idx)
    test_dates = set(dates[i] for i in test_idx)
    # Convert to calendar ranges -- for each test date, forbidden zone is +/-embargo_days
    test_date_objs = sorted({parse_date(d) for d in test_dates})

    purged = []
    for i in train_idx:
        di = parse_date(dates[i])
        # Find nearest test date
        min_dist = min(abs((di - td).days) for td in test_date_objs)
        if min_dist > embargo_days:
            purged.append(i)
    return purged


def expand_event_dates(event_dates: Sequence[str], purge_days: int) -> set[str]:
    """Expand event dates +/-purge_days into a set of forbidden ISO date strings."""
    if purge_days < 0 or not event_dates:
        return set()
    out: set[str] = set()
    for d in event_dates:
        dt = parse_date(d)
        for k in range(-purge_days, purge_days + 1):
            out.add((dt + timedelta(days=k)).strftime("%Y-%m-%d"))
    return out


def drop_event_indices(indices: Sequence[int], dates: Sequence[str],
                       forbidden: set[str]) -> list[int]:
    """Remove indices whose date is in the forbidden (event +/- purge) set."""
    if not forbidden:
        return list(indices)
    return [i for i in indices if dates[i] not in forbidden]


def cpcv_splits(dates: Sequence[str], n_groups: int, k_test: int,
                embargo_days: int,
                event_dates: Sequence[str] | None = None,
                event_purge_days: int = 0) -> list[tuple[list[int], list[int]]]:
    """Return list of (train_indices, test_indices) for all C(n_groups, k_test) splits.

    If event_dates is non-empty and event_purge_days > 0, indices whose date
    is within event_purge_days of any event are dropped from BOTH train and
    test folds. This isolates the 'ambient' (non-event-adjacent) signal.
    """
    groups = build_groups(dates, n_groups)
    all_idx = list(range(len(dates)))
    splits = []

    forbidden = expand_event_dates(event_dates or [], event_purge_days)

    for test_group_ids in itertools.combinations(range(n_groups), k_test):
        test_idx: list[int] = []
        for g in test_group_ids:
            test_idx.extend(groups[g])
        test_idx.sort()

        # Raw train = everything not in test
        test_set = set(test_idx)
        train_raw = [i for i in all_idx if i not in test_set]
        # Apply leakage-purge (embargo) on train
        train_purged = purge_train(train_raw, test_idx, dates, embargo_days)

        # Event-purge both folds
        train_purged = drop_event_indices(train_purged, dates, forbidden)
        test_purged = drop_event_indices(test_idx, dates, forbidden)

        splits.append((train_purged, test_purged))
    return splits


def cpcv_score(daily_returns: Sequence[tuple[str, float]],
               score_fn: Callable[[list[float]], float],
               n_groups: int = 10,
               k_test: int = 2,
               embargo_days: int = 5,
               event_dates: Sequence[str] | None = None,
               event_purge_days: int = 0) -> dict[str, Any]:
    """Run CPCV across all C(n_groups, k_test) splits.

    daily_returns: list of (date_string, daily_pnl_pct) sorted by date.
    score_fn: function taking a list of daily returns, returning a scalar
              (e.g., Sharpe, CAGR). Applied to both train and test halves.
    n_groups: number of contiguous time groups (default 10).
    k_test: number of groups held out as test per split (default 2).
    embargo_days: purge window around test (default 5 days).
    event_dates: optional list of event ISO dates (FOMC/CPI/NFP/OPEX).
    event_purge_days: drop train+test indices within N days of any event.
                     0 = no event purging (backwards-compatible).

    Returns:
        dict with:
          n_splits: number of CPCV splits run
          train_scores: list of train-set scores
          test_scores: list of test-set scores
          train_mean, train_std
          test_mean, test_std
          mean_drop: average (train_score - test_score)
          decay_ratio: mean test score / mean train score
          split_details: list of per-split records
    """
    dates = [d for d, _ in daily_returns]
    values = [v for _, v in daily_returns]
    splits = cpcv_splits(dates, n_groups, k_test, embargo_days,
                         event_dates=event_dates,
                         event_purge_days=event_purge_days)

    train_scores, test_scores = [], []
    detail = []
    for s_i, (train_idx, test_idx) in enumerate(splits):
        train_vals = [values[i] for i in train_idx]
        test_vals = [values[i] for i in test_idx]
        tr_score = score_fn(train_vals)
        te_score = score_fn(test_vals)
        train_scores.append(tr_score)
        test_scores.append(te_score)
        detail.append({
            "split_id": s_i,
            "train_n": len(train_idx),
            "test_n": len(test_idx),
            "train_score": tr_score,
            "test_score": te_score,
        })

    def mean(x): return sum(x) / len(x) if x else 0.0
    def stddev(x):
        n = len(x)
        if n < 2: return 0.0
        m = mean(x)
        return math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))

    tr_mean, te_mean = mean(train_scores), mean(test_scores)
    return {
        "n_splits": len(splits),
        "n_groups": n_groups,
        "k_test": k_test,
        "embargo_days": embargo_days,
        "train_scores": train_scores,
        "test_scores": test_scores,
        "train_mean": tr_mean,
        "train_std": stddev(train_scores),
        "test_mean": te_mean,
        "test_std": stddev(test_scores),
        "mean_drop": tr_mean - te_mean,
        "decay_ratio": te_mean / tr_mean if tr_mean != 0 else 0.0,
        "split_details": detail,
    }
