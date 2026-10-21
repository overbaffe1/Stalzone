"""Quality-tier (qlt) pricing: extraction, summaries, tier-aware scoring."""
import datetime as dt

from szmarket.arbitrage import (
    extract_qlt,
    find_deals,
    has_bonus,
    record_qlt,
    summarize_by_quality,
)
from szmarket.snipe import analyze_ending


def _sale(unit, qlt=None, bonus=False, amount=1):
    raw = {}
    if qlt is not None or bonus:
        add = {}
        if qlt is not None:
            add["qlt"] = qlt
        if bonus:
            add["bonus_properties"] = {"x": 1}
        raw = {"additional": add}
    return {"unit_price": unit, "amount": amount, "raw": raw,
            "fetched_at": 1_000}


def test_extract_and_bonus():
    assert extract_qlt({"additional": {"qlt": 3}}) == 3
    assert extract_qlt({"additional": {"qlt": 9}}) is None
    assert extract_qlt({"additional": {}}) is None
    assert extract_qlt({}) is None
    assert extract_qlt(None) is None
    assert has_bonus({"additional": {"bonus_properties": {}}}) is True
    assert has_bonus({"additional": {"qlt": 1}}) is False
    assert record_qlt({"qlt": 2}) == 2
    assert record_qlt(_sale(100, qlt=4)) == 4


def test_summarize_by_quality_segments_and_drops_bonus():
    recs = [_sale(100 + i, qlt=0) for i in range(6)]
    recs += [_sale(1000 + i, qlt=3) for i in range(6)]
    recs += [_sale(999999, qlt=3, bonus=True)]
    out = summarize_by_quality(recs)
    assert out["mix"]["count"] == 12  # bonus excluded
    assert out["qlt"][0]["median"] < 200
    assert out["qlt"][3]["median"] > 900
    assert out["qlt"][3]["count"] == 6


def test_snipe_scores_lot_against_own_tier():
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    recs = [_sale(100, qlt=0) for _ in range(10)]
    recs += [_sale(1000, qlt=3) for _ in range(10)]
    qb = summarize_by_quality(recs)
    mix = qb["mix"]  # median ~550
    # qlt0 lot at 400: mixed median says profit, tier median (100) says no.
    lot = {"unit_price": 400.0, "amount": 1,
           "end_time": "2026-10-20T12:30:00Z",
           "raw": {"startPrice": 100, "buyoutPrice": 400,
                   "additional": {"qlt": 0}}}
    rows_mix = analyze_ending([lot], mix, now=now, min_sales=5)
    assert len(rows_mix) == 1  # mixed view: looks like a deal
    rows_tier = analyze_ending([lot], mix, now=now, min_sales=5,
                               qlt_summaries=qb)
    assert rows_tier == []  # tier view: fairly priced, no deal
    assert rows_mix[0]["qlt"] == 0


def test_find_deals_uses_tier_median():
    recs = [_sale(100, qlt=0) for _ in range(10)]
    recs += [_sale(1000, qlt=3) for _ in range(10)]
    qb = summarize_by_quality(recs)
    lot = {"unit_price": 400.0, "amount": 1,
           "raw": {"additional": {"qlt": 0}}}
    assert len(find_deals([lot], qb["mix"], min_sales=5,
                          min_velocity=0)) == 1
    assert find_deals([lot], qb["mix"], min_sales=5, min_velocity=0,
                      qlt_summaries=qb) == []
