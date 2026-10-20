from szmarket.arbitrage import (
    find_deals,
    median,
    net_proceeds,
    percentile,
    required_sell_price,
    summarize_sales,
)


def test_fee_math():
    assert net_proceeds(100.0) == 95.0
    assert abs(required_sell_price(95.0) - 100.0) < 1e-9
    assert abs(required_sell_price(100.0) - 105.26315789473685) < 1e-9


def test_median_and_percentile():
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) == 0.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 25) == 1.75
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([], 50) == 0.0


def _sales(n=20, price=100.0):
    return [
        {"unit_price": price + (i % 5), "amount": 1, "fetched_at": 1_700_000_000 + i}
        for i in range(n)
    ]


def test_summarize_sales():
    s = summarize_sales(_sales())
    assert s["count"] == 20
    assert s["median"] == 102.0
    assert s["p25"] < s["median"] < s["p75"]
    assert s["velocity_per_day"] == 20.0  # intraday window -> count
    assert summarize_sales([])["count"] == 0


def test_find_deals_filters_and_sorts():
    summary = summarize_sales(_sales())
    lots = [
        {"unit_price": 80.0, "amount": 10},   # deep deal
        {"unit_price": 99.0, "amount": 1},    # below median but margin too small
        {"unit_price": 120.0, "amount": 5},   # overpriced
        {"unit_price": None, "amount": 3},    # broken record
    ]
    deals = find_deals(lots, summary, min_margin=0.05, min_sales=5, min_velocity=1.0)
    assert len(deals) == 1
    assert deals[0]["unit_buy"] == 80.0
    assert deals[0]["profit_total"] == deals[0]["profit_unit"] * 10
    # illiquid item -> no deals
    assert find_deals(lots, summarize_sales(_sales(3)), min_sales=10) == []
