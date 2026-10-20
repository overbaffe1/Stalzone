from szmarket.store import MarketStore, extract_lot_numbers, lot_fingerprint


def _lot(**kw):
    base = {"amount": 10, "buyoutPrice": 1000, "startPrice": 900,
            "startTime": "t0", "endTime": "t1", "currentPrice": 950}
    base.update(kw)
    return base


def test_fingerprint_ignores_mutable_price():
    a = lot_fingerprint("x", _lot(currentPrice=1))
    b = lot_fingerprint("x", _lot(currentPrice=999))
    assert a == b
    assert lot_fingerprint("x", _lot(amount=5)) != a


def test_extract_numbers_variants():
    assert extract_lot_numbers(_lot()) == (10, 1000.0, 100.0)
    assert extract_lot_numbers({"price": 50}) == (1, 50.0, 50.0)
    assert extract_lot_numbers({}) == (1, None, None)


def test_save_lots_dedupes(tmp_path):
    st = MarketStore(tmp_path / "m.db")
    try:
        assert st.save_lots("RU", "it1", [_lot(), _lot(amount=5)]) == 2
        assert st.save_lots("RU", "it1", [_lot(), _lot(amount=5)]) == 0  # dupes
        lots = st.latest_lots("it1", "RU")
        assert len(lots) == 2
        assert sorted(l["unit_price"] for l in lots) == [100.0, 200.0]
        assert st.tracked_items("RU") == ["it1"]
    finally:
        st.close()


def test_save_and_read_sales(tmp_path):
    st = MarketStore(tmp_path / "m.db")
    try:
        st.save_sales("RU", "it1", [{"price": 1000, "amount": 10},
                                    {"price": 50, "amount": 1}])
        rows = st.recent_sales("it1", "RU")
        assert [r["unit_price"] for r in rows] == [50.0, 100.0]
    finally:
        st.close()
