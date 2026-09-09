"""studies.lib.validation.cpcv -- synthetic daily series."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from studies.lib.validation import cpcv, metrics


def _dates(n: int, start: date = date(2020, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_split_count_is_binomial_and_partitions_without_embargo():
    dates = _dates(400)
    splits = cpcv.cpcv_splits(dates, n_groups=10, k_test=2, embargo_days=0)
    assert len(splits) == 45                      # C(10, 2)
    for train, test in splits:
        assert len(test) == 80
        assert sorted(train + test) == list(range(400))
    groups = cpcv.build_groups(dates, 10)
    assert [len(g) for g in groups] == [40] * 10
    assert [len(g) for g in cpcv.build_groups(_dates(403), 10)] == [40] * 9 + [43]


def test_embargo_excludes_k_days_around_test():
    dates = _dates(400)
    k = 5
    splits = cpcv.cpcv_splits(dates, 10, 2, embargo_days=k)
    for train, test in splits:
        test_d = [date.fromisoformat(dates[i]) for i in test]
        for i in train:
            d = date.fromisoformat(dates[i])
            assert min(abs((d - t).days) for t in test_d) > k
    # exact boundaries: test groups (0, 1) -> test = 0..79, train starts at 85
    train0, test0 = splits[0]
    assert test0 == list(range(80))
    assert train0[0] == 85 and len(train0) == 400 - 80 - 5
    # test groups (0, 9): the embargo bites on both sides of the middle block
    train9, test9 = splits[8]
    assert test9 == list(range(40)) + list(range(360, 400))
    assert train9 == list(range(45, 355))
    # purge_train is a no-op for embargo <= 0
    assert cpcv.purge_train([1, 2, 3], [0], dates, 0) == [1, 2, 3]


def test_event_purge_drops_window_from_both_folds():
    dates = _dates(400)
    events = [dates[100], dates[300]]
    d = 3
    forbidden = cpcv.expand_event_dates(events, d)
    assert len(forbidden) == 2 * (2 * d + 1)
    assert dates[97] in forbidden and dates[103] in forbidden
    assert dates[96] not in forbidden and dates[104] not in forbidden
    splits = cpcv.cpcv_splits(dates, 10, 2, embargo_days=0,
                              event_dates=events, event_purge_days=d)
    for train, test in splits:
        for i in train + test:
            assert dates[i] not in forbidden
        assert len(train) + len(test) == 400 - 14
    assert cpcv.expand_event_dates([], 3) == set()
    assert cpcv.drop_event_indices([1, 2], dates, set()) == [1, 2]


def test_cpcv_score_reports_train_and_test_stats():
    rng = np.random.default_rng(0)
    dates = _dates(400)
    daily = [(dt, 0.05 + float(x)) for dt, x in zip(dates, rng.standard_normal(400))]
    res = cpcv.cpcv_score(daily, metrics.sharpe, n_groups=10, k_test=2, embargo_days=5)
    assert res["n_splits"] == 45
    assert len(res["train_scores"]) == 45 == len(res["test_scores"])
    assert res["mean_drop"] == pytest.approx(res["train_mean"] - res["test_mean"])
    assert res["decay_ratio"] == pytest.approx(res["test_mean"] / res["train_mean"])
    assert res["train_std"] >= 0 and res["test_std"] > 0
    for rec in res["split_details"]:
        assert rec["train_n"] > rec["test_n"] == 80
        assert rec["train_n"] <= 400 - 80 - 5
    # the same series without purging keeps every observation
    res0 = cpcv.cpcv_score(daily, metrics.sharpe, n_groups=10, k_test=2, embargo_days=0)
    assert all(r["train_n"] == 320 for r in res0["split_details"])
