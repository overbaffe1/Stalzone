import time

import pytest

from szmarket.craft import Recipe, check_coverage, evaluate_all, evaluate_recipe
from szmarket.eapi import EapiClient, EapiError, TokenBucket


def _recipe():
    return Recipe(id="q1", product_id="PROD", product_count=1,
                  requirements=[("A", 2), ("B", 1)], energy=500.0)


def test_thin_data_rejected():
    buy = {"A": 10.0, "B": 5.0}
    counts = {"A": 50, "B": 2, "PROD": 100}
    assert check_coverage(_recipe(), counts, 10) == ["B"]
    assert evaluate_recipe(_recipe(), buy, 100.0,
                           sales_counts=counts, min_sales=10) is None
    # без фильтра — считается как раньше
    ev = evaluate_recipe(_recipe(), buy, 100.0)
    assert ev["profit"] == 95.0 - 25.0


def test_breakdown_and_energy_in_result():
    ev = evaluate_recipe(_recipe(), {"A": 10.0, "B": 5.0}, 100.0)
    assert ev["energy"] == 500.0
    assert ev["breakdown"] == [
        {"item": "A", "count": 2, "unit": 10.0, "total": 20.0},
        {"item": "B", "count": 1, "unit": 5.0, "total": 5.0},
    ]


def test_evaluate_all_quality_filter():
    r = _recipe()
    buy = {"A": 10.0, "B": 5.0}
    counts = {"A": 50, "B": 50, "PROD": 3}
    assert evaluate_all([r], buy, {"PROD": 100.0},
                        sales_counts=counts, min_sales=10) == []
    counts["PROD"] = 50
    assert len(evaluate_all([r], buy, {"PROD": 100.0},
                            sales_counts=counts, min_sales=10)) == 1


def test_bucket_allows_burst_then_limits():
    b = TokenBucket(per_minute=60.0)  # 1 token/sec, capacity 60
    assert b.take(60.0) == 0.0  # full burst, no wait
    t0 = time.monotonic()
    with pytest.raises(EapiError):
        b.take(60.0, max_wait=0.05)  # empty + tiny budget -> error
    assert time.monotonic() - t0 < 2.0


def test_bucket_refills_over_time(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: (slept.append(s),
                                                  now.__setitem__(0, now[0] + s)))
    b = TokenBucket(per_minute=60.0)
    b.take(60.0)
    b.take(1.0, max_wait=30.0)  # waits ~1s for 1 token
    assert 0.9 < sum(slept) < 1.5


def test_client_shares_bucket_and_ts_lock():
    c = EapiClient("https://eapi.test", "ID", "S", min_gap=0)
    assert isinstance(c.bucket, TokenBucket)
    assert c.request_cost == 2.0
