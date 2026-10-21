"""Trip planner: map coverage + ordering + grouping."""
import json

from szmarket.route import build_trip, coords_of, load_map, order_stops


def _stations():
    recs = json.load(open("data/recipes.json", encoding="utf-8"))["recipes"]
    return sorted({r["station"] for r in recs if r["kind"] == "barter"})


def test_map_covers_all_barter_stations():
    m = load_map()
    for s in _stations():
        assert s in m, f"no map entry: {s}"


def test_coords_in_range_and_offmap_flagged():
    m = load_map()
    n_xy = 0
    for s, info in m.items():
        c = coords_of(s, m)
        if info.get("offmap"):
            assert c is None
        else:
            assert c is not None, s
            assert 0.0 <= c[0] <= 1.0 and 0.0 <= c[1] <= 1.0, s
            n_xy += 1
    assert n_xy >= 10


def test_order_stops_starts_at_start_and_keeps_all():
    m = load_map()
    stations = _stations()
    start = "Бартер · Бар"
    order = order_stops(stations, start, m)
    assert sorted(order) == sorted(stations)
    assert order[0] == start  # Бар на карте
    # неизвестная точка старта -> просто все на месте
    order2 = order_stops(stations, "Бартер · Неведомая хрень", m)
    assert sorted(order2) == sorted(stations)


def _ev(pid, station, profit, kind="barter", breakdown=None, money=0.0,
        cost=100.0):
    return {"recipe_id": pid, "product_id": pid, "station": station,
            "kind": kind, "profit": profit, "cost": cost, "money": money,
            "units": 1, "sell_price": 200.0, "revenue": profit + cost,
            "roi": 0.5, "energy": 0, "breakdown": breakdown or []}


def test_build_trip_groups_and_sums():
    m = load_map()
    evals = [
        _ev("a", "Бартер · Бар", 1000.0,
            breakdown=[{"item": "x", "count": 2, "total": 500.0}]),
        _ev("b", "Бартер · Бар", 500.0,
            breakdown=[{"item": "x", "count": 1, "total": 250.0}]),
        _ev("c", "Верстак", 300.0, kind="hideout"),
        _ev("d", "Бартер · Сессионные бои", 100.0),
    ]
    trip = build_trip(evals, "Бартер · Бар", m)
    assert len(trip["home"]) == 1
    assert len(trip["stops"]) == 1
    assert trip["stops"][0]["station"] == "Бартер · Бар"
    assert trip["stops"][0]["buy"] == {"x": 3.0}
    assert len(trip["offmap"]) == 1  # арена вне карты
    assert trip["total_profit"] == 1900.0
    assert trip["start"] == "Бартер · Бар"
