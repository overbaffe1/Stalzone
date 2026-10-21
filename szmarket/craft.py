"""Crafting economics: recipe evaluation with the 5% auction fee.

Recipe JSON format (built by tools/build_data.py from official EXBO dumps):
  {"recipes": [ {"id": ..., "kind": "hideout"|"barter", "station": ...,
    "skill": ..., "skill_level": ..., "productId": ..., "productCount": ...,
    "money": 0, "energy": 0,
    "requirements": [ {"itemId": ..., "count": ...}, ... ] } ]}
A bare list [...] is also accepted (konti1ks/stalzone-main compatible).
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
    money: float = 0.0  # extra cash cost (barter offers / NPC vendors)
    energy: float = 0.0  # hideout energy per craft (lower = more repeatable)
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
                money=float(r.get("money", 0) or 0),
                energy=float(r.get("energy", 0) or 0),
                kind=str(r.get("kind", "")),
                category=str(r.get("category", "")),
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


def check_coverage(recipe: Recipe, sales_counts: dict[str, int],
                   min_sales: int) -> list[str]:
    """Item ids whose price is built on fewer than `min_sales` sales."""
    thin = [i for i, _ in recipe.requirements
            if sales_counts.get(i, 0) < min_sales]
    if sales_counts.get(recipe.product_id, 0) < min_sales:
        thin.append(recipe.product_id)
    return thin


def find_swaps(recipes: list[Recipe]) -> list[dict[str, Any]]:
    """1:1 money-free barter pairs convertible BOTH ways (see-saw trades).

    Returns [{a, b, a_to_b: [stations], b_to_a: [stations]}] with a < b.
    """
    fwd: dict[tuple[str, str], list[str]] = {}
    for r in recipes:
        if r.kind != "barter" or len(r.requirements) != 1:
            continue
        if (r.money or 0) > 0 or (r.product_count or 1) != 1:
            continue
        i, n = r.requirements[0]
        if n != 1 or i == r.product_id:
            continue
        fwd.setdefault((i, r.product_id), []).append(r.station)
    out = []
    for (a, b), ab in sorted(fwd.items()):
        ba = fwd.get((b, a))
        if ba and a < b:
            out.append({"a": a, "b": b, "a_to_b": ab, "b_to_a": ba})
    return out


def chain_unit_costs(
    recipes: list[Recipe], buy_prices: dict[str, float], max_depth: int = 2
) -> tuple[dict[str, float], dict[str, str]]:
    """Cheapest unit cost per item: buy now OR craft via intermediates.

    Returns (costs, sources): sources[item] is the '<station>' of the
    cheapest crafting recipe (one level shown; deeper levels fold into
    the cost recursively), absent for plain market buys. Cycle-guarded.
    """
    by_product: dict[str, list[Recipe]] = {}
    for r in recipes:
        by_product.setdefault(r.product_id, []).append(r)

    def best(item: str, depth: int, stack: frozenset[str]
             ) -> tuple[float | None, str]:
        price = buy_prices.get(item)
        src = ""
        if depth >= max_depth or item in stack:
            return price, src
        for r in by_product.get(item, []):
            total = float(r.money or 0.0)
            ok = True
            for i, n in r.requirements:
                c, _ = best(i, depth + 1, stack | {item})
                if c is None:
                    ok = False
                    break
                total += c * n
            if not ok:
                continue
            unit = total / max(r.product_count or 1, 1)
            if price is None or unit < price:
                price, src = unit, r.station
        return price, src

    costs: dict[str, float] = {}
    sources: dict[str, str] = {}
    for item in set(buy_prices) | set(by_product):
        price, src = best(item, 0, frozenset())
        if price is not None:
            costs[item] = price
            if src:
                sources[item] = src
    return costs, sources


def check_coverage_chain(recipe: Recipe, sales_counts: dict[str, int],
                         min_sales: int, recipes: list[Recipe],
                         max_depth: int = 2) -> list[str]:
    """Like check_coverage, but inputs may be covered via craftable raws.

    An input is covered if it has enough sales itself OR at least one
    recipe makes it from (recursively) covered raws.
    """
    by_product: dict[str, list[Recipe]] = {}
    for r in recipes:
        by_product.setdefault(r.product_id, []).append(r)

    def covered(item: str, depth: int, stack: frozenset[str]) -> bool:
        if sales_counts.get(item, 0) >= min_sales:
            return True
        if depth <= 0 or item in stack:
            return False
        return any(
            all(covered(i, depth - 1, stack | {item})
                for i, _ in r.requirements)
            for r in by_product.get(item, [])
        )

    thin = [i for i, _ in recipe.requirements
            if not covered(i, max_depth, frozenset())]
    if sales_counts.get(recipe.product_id, 0) < min_sales:
        thin.append(recipe.product_id)
    return thin


def evaluate_recipe(
    recipe: Recipe,
    buy_prices: dict[str, float],
    sell_price: float,
    fee: float = AUCTION_FEE,
    yield_mult: float = 1.0,
    sales_counts: dict[str, int] | None = None,
    min_sales: int = 0,
    via: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Profit of one craft, or None if prices are missing or too thin.

    `sales_counts` maps item id -> number of sales the median is built on;
    with min_sales > 0, recipes priced on thin data are rejected (this kills
    fake 3000%+ ROI rows coming from 1-2 junk/transfer sales).
    """
    cost, missing = recipe_cost(recipe, buy_prices)
    if missing:
        return None
    if sales_counts is not None and min_sales > 0:
        if check_coverage(recipe, sales_counts, min_sales):
            return None
    units = recipe.product_count * yield_mult
    revenue = units * sell_price * (1.0 - fee)
    profit = revenue - cost
    breakdown = []
    for i, n in recipe.requirements:
        row: dict[str, Any] = {"item": i, "count": n, "unit": buy_prices[i],
                               "total": buy_prices[i] * n}
        if via and via.get(i):
            row["via"] = via[i]
        breakdown.append(row)
    return {
        "recipe_id": recipe.id,
        "kind": recipe.kind,
        "product_id": recipe.product_id,
        "station": recipe.station,
        "skill": recipe.skill,
        "skill_level": recipe.skill_level,
        "energy": recipe.energy,
        "units": units,
        "cost": cost,
        "money": float(recipe.money or 0.0),
        "breakdown": breakdown,
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
    sales_counts: dict[str, int] | None = None,
    min_sales: int = 0,
    chain_depth: int = 0,
) -> list[dict[str, Any]]:
    """Evaluate every recipe that has full price coverage, best profit first.

    With chain_depth > 0, intermediates are costed at min(market, craft)
    recursively, and coverage may come from craftable raws (marked ⛓ via).
    """
    costs, via = buy_prices, None
    if chain_depth > 0:
        costs, via = chain_unit_costs(recipes, buy_prices, chain_depth)
    results = []
    for r in recipes:
        if r.product_id not in sell_prices:
            continue
        if chain_depth > 0 and sales_counts is not None and min_sales > 0:
            if check_coverage_chain(r, sales_counts, min_sales, recipes,
                                    chain_depth):
                continue
            cov, ms = None, 0
        else:
            cov, ms = sales_counts, min_sales
        ev = evaluate_recipe(r, costs, sell_prices[r.product_id], fee,
                             yield_mult, cov, ms, via)
        if ev is not None:
            results.append(ev)
    results.sort(key=lambda x: x["profit"], reverse=True)
    return results


def build_chain(ev: dict[str, Any], name_of: Any,
                fmt_money: Any) -> list[dict[str, Any]]:
    """Пошаговая цепочка профита: [{icon, text}].

    Первая строка начинается со ``*`` (итог), шаги — с ``-``:
    удобно читать и копировать в Discord/чат игры.
    """
    prod = ev["product_id"]
    units = float(ev.get("units") or 0)
    roi = float(ev.get("roi") or 0) * 100
    steps = [{
        "icon": prod,
        "text": (f"* {name_of(prod)} ×{units:g} → "
                 f"+{fmt_money(ev.get('profit', 0))} (ROI {roi:.0f}%)"),
    }]
    n = 0
    for b in ev.get("breakdown", []):
        n += 1
        iid = b["item"]
        cnt = float(b.get("count") or 0)
        total = fmt_money(b.get("total", 0))
        if b.get("via"):
            steps.append({
                "icon": iid,
                "text": (f"- {n}. ⛓ Скрафтить самому: {name_of(iid)} ×{cnt:g} "
                         f"на {b['via']} = {total}"),
            })
        else:
            steps.append({
                "icon": iid,
                "text": f"- {n}. Купить: {name_of(iid)} ×{cnt:g} = {total}",
            })
    if ev.get("money"):
        n += 1
        steps.append({
            "icon": None,
            "text": f"- {n}. 💵 NPC-деньги: {fmt_money(ev['money'])}",
        })
    n += 1
    extras = []
    if ev.get("energy"):
        extras.append(f"⚡{float(ev['energy']):g}")
    if ev.get("skill"):
        extras.append(f"🎓{ev['skill']} {ev.get('skill_level', '')}".rstrip())
    tail = f" ({', '.join(extras)})" if extras else ""
    verb = {"craft": "Скрафтить", "hideout": "Скрафтить",
            "barter": "Обменять", "shop": "Выкупить"}.get(
                str(ev.get("kind", "craft")), "Получить")
    steps.append({
        "icon": prod,
        "text": (f"- {n}. {verb}: {ev.get('station', '?')}{tail} → "
                 f"{name_of(prod)} ×{units:g}"),
    })
    n += 1
    steps.append({
        "icon": prod,
        "text": (f"- {n}. Продать: {name_of(prod)} ×{units:g} = "
                 f"{fmt_money(ev.get('revenue', 0))} (комиссия 5% учтена)"),
    })
    return steps
