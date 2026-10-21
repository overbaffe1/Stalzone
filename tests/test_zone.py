"""v7: emission/profile endpoints, MAD outlier filter, mirror stub guard."""
from __future__ import annotations

import pytest

from szmarket import arbitrage as arb
from szmarket.eapi import EapiClient
from szmarket.mirror import looks_like_stub


def _rec(price: float, n: int = 0) -> dict:
    return {"unit_price": price, "amount": 1, "fetched_at": 1000 + n}


SPREAD = [95.0, 97.0, 98.0, 99.0, 99.5, 100.0, 100.0, 100.5,
          101.0, 102.0, 96.0, 98.5, 99.0, 100.0, 101.5, 103.0,
          97.5, 99.0, 100.0, 102.5]


def test_mad_removes_fat_finger():
    flags = arb.mad_outliers(SPREAD + [10000.0])
    assert flags[-1] is True
    assert sum(flags) == 1


def test_mad_keeps_clean_data():
    prices = [95.0, 98.0, 100.0, 102.0, 105.0, 99.0, 101.0]
    assert not any(arb.mad_outliers(prices))


def test_mad_skips_small_samples():
    assert arb.mad_outliers([10.0, 11.0, 9999.0]) == [False] * 3


def test_summarize_rejects_outlier_median():
    recs = [_rec(p, n) for n, p in enumerate(SPREAD)] + [_rec(10000.0, 21)]
    s = arb.summarize_sales(recs)
    assert s["median"] == pytest.approx(99.75, abs=1.0)
    assert s["rejected"] == 1


def test_summarize_mad_off_keeps_all():
    recs = [_rec(100.0, n) for n in range(20)] + [_rec(10000.0, 21)]
    s = arb.summarize_sales(recs, mad=False)
    assert s["count"] == 21
    assert s["rejected"] == 0


def _stub_client() -> tuple[EapiClient, list]:
    c = EapiClient("https://x.test", "id", "secret", region="RU")
    calls: list = []
    c._get = lambda path, params=None: calls.append(path) or {}  # type: ignore
    return c, calls


def test_emission_url():
    c, calls = _stub_client()
    c.emission()
    assert calls == ["/RU/emission"]


def test_profile_url_quotes_name():
    c, calls = _stub_client()
    c.profile("Vasya Pupkin")
    assert calls == ["/RU/character/by-name/Vasya%20Pupkin/profile"]


def _hist(n: int, ordered: bool, detailed: bool) -> list[dict]:
    out = []
    for i in range(n):
        t = f"2026-10-21T10:{i:02d}:00Z"
        out.append({"time": t, "additional": {"qlt": 1} if detailed else {}})
    return list(reversed(out)) if ordered else out


def test_stub_detected():
    assert looks_like_stub(_hist(8, ordered=False, detailed=False))


def test_real_page_passes():
    assert not looks_like_stub(_hist(8, ordered=True, detailed=True))


def test_thin_page_passes():
    assert not looks_like_stub(_hist(3, ordered=False, detailed=False))
