"""Craft chains (cheap intermediates) and barter see-saw pairs."""
from szmarket.craft import (
    Recipe,
    chain_unit_costs,
    check_coverage_chain,
    evaluate_all,
    find_swaps,
)


def _r(rid, prod, reqs, station="Верстак", money=0.0, kind="hideout",
       count=1):
    return Recipe(id=rid, product_id=prod, product_count=count,
                  requirements=list(reqs), station=station, money=money,
                  kind=kind)


def test_find_swaps_both_ways_only():
    recs = [
        _r("1", "B", [("A", 1)], station="S1", kind="barter"),
        _r("2", "A", [("B", 1)], station="S2", kind="barter"),
        _r("3", "D", [("C", 1)], station="S1", kind="barter"),  # one-way
        _r("4", "F", [("E", 1)], station="S1", kind="barter", money=50),
        _r("5", "E", [("F", 1)], station="S1", kind="barter", money=50),
    ]
    swaps = find_swaps(recs)
    assert len(swaps) == 1
    assert swaps[0]["a"] == "A" and swaps[0]["b"] == "B"
    assert swaps[0]["a_to_b"] == ["S1"]
    assert swaps[0]["b_to_a"] == ["S2"]


def test_chain_unit_costs_prefers_craft_and_stops_cycles():
    recs = [
        _r("1", "B", [("A", 2)], station="Верстак"),   # 2*100=200 < buy 300
        _r("2", "X", [("Y", 1)], station="S"),          # cycle X<->Y
        _r("3", "Y", [("X", 1)], station="S"),
    ]
    costs, src = chain_unit_costs(recs, {"A": 100.0, "B": 300.0, "X": 50.0})
    assert costs["B"] == 200.0
    assert src["B"] == "Верстак"
    assert costs["A"] == 100.0 and "A" not in src
    assert costs["X"] == 50.0  # cycle falls back to buy price


def test_check_coverage_chain_allows_craftable_input():
    recs = [_r("1", "B", [("A", 2)], station="Верстак")]
    main = _r("m", "P", [("B", 1)], station="S2")
    counts = {"A": 50, "P": 50}  # B itself never sold
    assert check_coverage_chain(main, counts, 10, recs) == []
    counts2 = {"P": 50}  # raw A uncovered too
    assert check_coverage_chain(main, counts2, 10, recs) == ["B"]


def test_evaluate_all_chain_depth_unlocks_hidden_profit():
    recs = [
        _r("1", "B", [("A", 2)], station="Верстак"),
        _r("m", "P", [("B", 1)], station="S2"),
    ]
    buy = {"A": 100.0, "B": 1000.0}  # B absurdly overpriced on market
    sell = {"P": 500.0}
    counts = {"A": 50, "B": 50, "P": 50}
    plain = evaluate_all(recs, buy, sell, sales_counts=counts, min_sales=10)
    assert all(e["profit"] <= 0 for e in plain)  # 1000 cost vs 475 revenue
    chained = evaluate_all(recs, buy, sell, sales_counts=counts,
                           min_sales=10, chain_depth=2)
    win = [e for e in chained if e["product_id"] == "P"][0]
    assert win["cost"] == 200.0
    assert win["profit"] > 0
    assert win["breakdown"][0]["via"] == "Верстак"
