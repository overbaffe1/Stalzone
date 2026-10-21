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


def test_build_chain_star_and_dashes():
    from szmarket.craft import build_chain

    ev = {
        "recipe_id": "r1", "kind": "hideout", "product_id": "PROD",
        "station": "Верстак", "skill": "Оружейник", "skill_level": 3,
        "energy": 500.0, "units": 1.0, "cost": 10000.0, "money": 500.0,
        "breakdown": [
            {"item": "MAT_A", "count": 10, "unit": 800.0, "total": 8000.0},
            {"item": "MAT_B", "count": 5, "unit": 400.0, "total": 2000.0,
             "via": "Лаборатория"},
        ],
        "revenue": 22500.0, "profit": 12000.0, "roi": 1.2,
    }
    steps = build_chain(ev, lambda i: f"[{i}]",
                        lambda x: f"{x:,.0f}P".replace(",", " "))
    assert steps[0]["text"].startswith("* [PROD]")
    assert "ROI 120%" in steps[0]["text"]
    bodies = [s["text"] for s in steps[1:]]
    assert all(t.startswith("- ") for t in bodies)
    assert bodies[0].startswith("- 1. Купить: [MAT_A]")
    assert bodies[1].startswith("- 2.") and "⛓" in bodies[1]
    assert bodies[2].startswith("- 3.") and "NPC" in bodies[2]
    assert "Верстак" in bodies[3] and "⚡500" in bodies[3]
    assert bodies[4].startswith("- 5. Продать:")
    assert steps[0]["icon"] == "PROD"
    assert steps[3]["icon"] is None
