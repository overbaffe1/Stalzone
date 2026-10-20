from szmarket.craft import Recipe, evaluate_all, evaluate_recipe, recipe_cost


def _recipe():
    return Recipe(
        id="test1", product_id="PROD", product_count=10,
        requirements=[("MAT_A", 2), ("MAT_B", 3)],
        station="Lab", skill="Test", skill_level=1,
    )


def test_recipe_cost_and_missing():
    cost, missing = recipe_cost(_recipe(), {"MAT_A": 100.0, "MAT_B": 50.0})
    assert cost == 2 * 100.0 + 3 * 50.0 == 350.0
    assert missing == []
    _, missing2 = recipe_cost(_recipe(), {"MAT_A": 100.0})
    assert missing2 == ["MAT_B"]


def test_evaluate_recipe_profit_with_fee():
    # cost 350; 10 units * 50 sell * 0.95 = 475 revenue -> profit 125
    ev = evaluate_recipe(_recipe(), {"MAT_A": 100.0, "MAT_B": 50.0}, 50.0)
    assert ev is not None
    assert ev["cost"] == 350.0
    assert ev["revenue"] == 475.0
    assert ev["profit"] == 125.0
    assert abs(ev["roi"] - 125.0 / 350.0) < 1e-9


def test_evaluate_recipe_none_on_missing_price():
    assert evaluate_recipe(_recipe(), {"MAT_A": 100.0}, 50.0) is None


def test_evaluate_all_sorted_best_first():
    r1 = _recipe()
    r2 = Recipe(id="test2", product_id="PROD2", product_count=1,
                requirements=[("MAT_A", 1)])
    res = evaluate_all(
        [r1, r2],
        {"MAT_A": 100.0, "MAT_B": 50.0},
        {"PROD": 50.0, "PROD2": 500.0},
    )
    assert [r["recipe_id"] for r in res] == ["test2", "test1"]
    assert res[0]["profit"] == 500.0 * 0.95 - 100.0
