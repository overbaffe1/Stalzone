import datetime as dt

from szmarket.snipe import analyze_ending, format_left_ru, parse_time


def test_parse_time():
    assert parse_time("2026-10-20T19:59:58Z") == dt.datetime(
        2026, 10, 20, 19, 59, 58, tzinfo=dt.timezone.utc)
    assert parse_time("nonsense") is None
    assert parse_time(None) is None
    assert parse_time("") is None


def test_format_left_ru():
    assert format_left_ru(3720) == "1 ч 02 мин"
    assert format_left_ru(12 * 60) == "12 мин"
    assert format_left_ru(45) == "45 сек"


def _summary(median=1000.0, count=50):
    return {"count": count, "median": median, "p25": 900.0, "p75": 1100.0,
            "velocity_per_day": 20.0}


def _lot(buy_unit, amount, end_iso, start=100):
    return {"unit_price": buy_unit, "amount": amount, "end_time": end_iso,
            "raw": {"startPrice": start,
                    "buyoutPrice": buy_unit * amount}}


def test_analyze_ending_math_and_sort():
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    lots = [
        _lot(800.0, 2, "2026-10-20T13:30:00Z"),   # ends in 1.5h
        _lot(700.0, 1, "2026-10-20T12:30:00Z"),   # ends in 0.5h, better margin
        _lot(990.0, 1, "2026-10-20T12:10:00Z", start=940),  # margin too small, already bid up
        _lot(500.0, 1, "2026-10-20T11:00:00Z"),   # already expired
        _lot(500.0, 1, "2026-10-21T12:00:00Z"),   # too far (>3h)
        {"unit_price": 100.0, "amount": 1},        # no end_time
    ]
    rows = analyze_ending(lots, _summary(), now=now, max_hours=3.0)
    assert [r["time_left"] for r in rows] == ["30 мин", "1 ч 30 мин"]
    # fair = 1000*0.95 = 950; buy 700 -> profit 250, margin 250/700
    assert rows[0]["profit_total"] == 250.0
    assert rows[0]["max_buy_total"] == 950.0
    assert abs(rows[0]["margin"] - 250.0 / 700.0) < 1e-9
    # second: buy 800*2=1600, max 1900 -> profit 300
    assert rows[1]["profit_total"] == 300.0


def _bid_lot(unit_buyout, amount, end_iso, start=0, current=None):
    raw = {"startPrice": start, "buyoutPrice": unit_buyout * amount}
    if current is not None:
        raw["currentPrice"] = current
    return {"unit_price": unit_buyout, "amount": amount,
            "end_time": end_iso, "raw": raw}


def test_bid_path_when_buyout_too_high():
    # median 1000 -> ceiling 950. Buyout 1200 (bad), current bid 500 ->
    # next bid 525 <= 950 -> bid verdict.
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    lots = [_bid_lot(1200.0, 1, "2026-10-20T12:30:00Z", start=500, current=500)]
    rows = analyze_ending(lots, _summary(), now=now, max_hours=3.0)
    assert len(rows) == 1
    r = rows[0]
    assert r["buyout_ok"] is False
    assert r["bid_ok"] is True
    assert r["min_next_bid"] == 525.0
    assert r["max_buy_total"] == 950.0
    assert r["verdict"] == "✋ СТАВЬ"
    assert r["profit_total"] == 950.0 - 525.0


def test_bid_rejected_when_next_bid_above_ceiling():
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    lots = [_bid_lot(1200.0, 1, "2026-10-20T12:30:00Z", start=920, current=920)]
    rows = analyze_ending(lots, _summary(), now=now, max_hours=3.0)
    assert rows == []  # next bid 966 > ceiling 950, buyout bad too


def test_buyout_verdict_preferred():
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    lots = [_bid_lot(700.0, 1, "2026-10-20T12:30:00Z", start=500, current=500)]
    rows = analyze_ending(lots, _summary(), now=now, max_hours=3.0)
    assert rows[0]["verdict"] == "🔥 ВЫКУПАЙ"
    assert rows[0]["buyout_ok"] and rows[0]["bid_ok"]


def test_analyze_ending_needs_liquidity():
    now = dt.datetime(2026, 10, 20, 12, 0, 0, tzinfo=dt.timezone.utc)
    lots = [_lot(100.0, 1, "2026-10-20T12:30:00Z")]
    assert analyze_ending(lots, _summary(count=2), now=now) == []
    assert analyze_ending(lots, {"count": 50, "median": 0}, now=now) == []
