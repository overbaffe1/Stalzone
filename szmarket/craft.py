"""Crafting economics: recipe evaluation with the 5% auction fee.

Recipe JSON format (compatible with konti1ks/stalzone-main `recipes.json`):
  [ { "id": ..., "station": ..., "skill": ..., "skill_level": ...,
      "productId": "4npn", "productCount": 10,
      "requirements": [ {"itemId": "l0ryo", "count": 2}, ... ] } ]
A wrapper object {"recipes": [...]} is also accepted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import AUCTION_FEE


@dataclass
class Recipe:
    id: str
    product_id: str
    product_count: int = 1
    requirements: list[tuple[str, int]] = field(default_factory=list)
    station: str = ""
    skill: str = ""
    skill_level: int = 0
    money: float = 0.0  # extra cash cost (barter offers)
    kind: str = ""  # "hideout" | "barter" | ""
    category: str = ""


def load_recipes(path: str | Path) -> list[Recipe]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("recipes", []) if isinstance(data, dict) else data
    out: list[Recipe] = []
    for r in items:
        if not isinstance(r, dict) or "productId" not in r:
            continue
        reqs = [
            (q["itemId"], int(q.get("count", 1)))
            for q in r.get("requirements", [])
            if isinstance(q, dict) and "itemId" in q
        ]
        out.append(
            Recipe(
                id=str(r.get("id", r.get("productId"))),
                product_id=str(r["productId"]),
                product_count=int(r.get("productCount", 1)),
                requirements=reqs,
                station=str(r.get("station", "")),
                skill=str(r.get("skill", "")),
                skill_level=int(r.get("skill_level", 0)),
            )
        )
    return out


def recipe_cost(
    recipe: Recipe, buy_prices: dict[str, float]
) -> tuple[float, list[str]]:
    """Total material cost at given unit buy prices + missing item ids."""
    cost = float(recipe.money or 0.0)
    missing: list[str] = []
    for item_id, count in recipe.requirements:
        if item_id not in buy_prices:
            missing.append(item_id)
            continue
        cost += buy_prices[item_id] * count
    return cost, missing


def evaluate_recipe(
    recipe: Recipe,
    buy_prices: dict[str, float],
    sell_price: float,
    fee: float = AUCTION_FEE,
    yield_mult: float = 1.0,
) -> dict[str, Any] | None:
    """Profit of one craft, or None if some material price is unknown."""
    cost, missing = recipe_cost(recipe, buy_prices)
    if missing:
        return None
    units = recipe.product_count * yield_mult
    revenue = units * sell_price * (1.0 - fee)
    profit = revenue - cost
    return {
        "recipe_id": recipe.id,
        "kind": recipe.kind,
        "product_id": recipe.product_id,
        "station": recipe.station,
        "units": units,
        "cost": cost,
        "money": float(recipe.money or 0.0),
        "sell_price": sell_price,
        "revenue": revenue,
        "profit": profit,
        "roi": (profit / cost) if cost > 0 else 0.0,
    }


def evaluate_all(
    recipes: list[Recipe],
    buy_prices: dict[str, float],
    sell_prices: dict[str, float],
    fee: float = AUCTION_FEE,
    yield_mult: float = 1.0,
) -> list[dict[str, Any]]:
    """Evaluate every recipe that has full price coverage, best profit first."""
    results = []
    for r in recipes:
        if r.product_id not in sell_prices:
            continue
        ev = evaluate_recipe(r, buy_prices, sell_prices[r.product_id], fee, yield_mult)
        if ev is not None:
            results.append(ev)
    results.sort(key=lambda x: x["profit"], reverse=True)
    return results
