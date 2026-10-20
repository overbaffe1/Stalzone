from szmarket import catalog
from szmarket.craft import Recipe, evaluate_recipe


def _catalog():
    return {
        "g4lk0": {"ru": "Военная аптечка", "en": "Military First-Aid Kit",
                  "icon": "/icons/x.png", "cat": "supply/medicine"},
        "96430": {"ru": "Аптечка индивидуальная", "en": "Personal First-Aid Kit",
                  "icon": "", "cat": ""},
    }


def test_display_name_priority():
    c = _catalog()
    assert catalog.display_name("g4lk0", c) == "Военная аптечка"
    assert catalog.display_name("zzz", c) == "zzz"
    assert catalog.display_name("zzz", c, {"zzz": "Ручное имя"}) == "Ручное имя"


def test_search_items_ranking():
    c = _catalog()
    hits = catalog.search_items("аптечка", c)
    assert [h[0] for h in hits] == ["96430", "g4lk0"]  # startswith first
    assert catalog.search_items("g4lk0", c)[0][0] == "g4lk0"
    assert catalog.search_items("military", c)[0][0] == "g4lk0"
    assert catalog.search_items("", c) == []
    assert catalog.search_items("zzz", {}, {"zzz": "Ручное имя"})[0][1] == "Ручное имя"


def test_recipe_money_cost():
    r = Recipe(id="b1", product_id="P", product_count=1,
               requirements=[("M", 1)], money=300.0)
    ev = evaluate_recipe(r, {"M": 100.0}, 500.0)
    assert ev["cost"] == 400.0
    assert ev["profit"] == 500.0 * 0.95 - 400.0
    assert ev["money"] == 300.0
