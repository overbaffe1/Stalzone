"""Resale (arbitrage) analytics: price zones, liquidity, deal scoring.

Method (same as stalzovmarket / stalzone-market-analytics):
- fair value bands from sales history: P25 / median / P75 unit prices;
- liquidity = sales velocity (sales per day) + sample size;
- a lot is a deal if buying it now and selling at median still clears
  the 5% fee with the required margin.
"""
from __future__ import annotations

from typing import Any

from .config import AUCTION_FEE, BREAKEVEN_MULT


def net_proceeds(sale_price: float, fee: float = AUCTION_FEE) -> float:
    """Money received after the auction takes its cut."""
    return sale_price * (1.0 - fee)


def required_sell_price(buy_price: float, fee: float = AUCTION_FEE) -> float:
    """Minimum sale price to break even (buy / 0.95)."""
    return buy_price / (1.0 - fee)


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100]."""
    if not sorted_values:
        return 0.0
    s = sorted(sorted_values)
    if len(s) == 1:
        return float(s[0])
    rank = (q / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def summarize_sales(
    records: list[dict[str, Any]], price_key: str = "unit_price"
) -> dict[str, Any]:
    """Aggregate sales records into fair-value bands + liquidity stats."""
    prices = [float(r[price_key]) for r in records if r.get(price_key)]
    if not prices:
        return {
            "count": 0, "median": 0.0, "p25": 0.0, "p75": 0.0,
            "min": 0.0, "max": 0.0, "velocity_per_day": 0.0,
            "volume": 0, "turnover": 0.0, "spread": 0.0,
        }
    s = sorted(prices)
    volume = sum(int(r.get("amount", 1) or 1) for r in records)
    turnover = sum(
        float(r.get(price_key, 0.0)) * int(r.get("amount", 1) or 1) for r in records
    )
    stamps = [int(r.get("fetched_at", 0) or 0) for r in records if r.get("fetched_at")]
    if len(stamps) >= 2 and max(stamps) > min(stamps):
        span_days = (max(stamps) - min(stamps)) / 86400.0
        velocity = len(records) / span_days if span_days >= 1.0 else float(len(records))
    else:
        velocity = float(len(records))
    med = median(s)
    p25 = percentile(s, 25)
    p75 = percentile(s, 75)
    return {
        "count": len(records),
        "median": med,
        "p25": p25,
        "p75": p75,
        "min": s[0],
        "max": s[-1],
        "velocity_per_day": velocity,
        "volume": volume,
        "turnover": turnover,
        "spread": (p75 - p25) / med if med > 0 else 0.0,
    }


def find_deals(
    lots: list[dict[str, Any]],
    summary: dict[str, Any],
    fee: float = AUCTION_FEE,
    min_margin: float = 0.05,
    min_sales: int = 10,
    min_velocity: float = 1.0,
) -> list[dict[str, Any]]:
    """Score active lots against the sales summary. Best total profit first.

    Assumes we sell at `median`; skips illiquid items (few sales / slow).
    """
    if summary.get("count", 0) < min_sales:
        return []
    if summary.get("velocity_per_day", 0.0) < min_velocity:
        return []
    fair = summary["median"] * (1.0 - fee)  # net proceeds per unit at median
    if fair <= 0:
        return []
    deals = []
    for lot in lots:
        buy = lot.get("unit_price")
        if not buy or buy <= 0:
            continue
        profit_unit = fair - float(buy)
        margin = profit_unit / float(buy)
        if margin < min_margin:
            continue
        amount = int(lot.get("amount", 1) or 1)
        deals.append(
            {
                "unit_buy": float(buy),
                "amount": amount,
                "fair_median": summary["median"],
                "profit_unit": profit_unit,
                "profit_total": profit_unit * amount,
                "margin": margin,
                "breakeven_sell": required_sell_price(float(buy), fee),
                "lot": lot,
            }
        )
    deals.sort(key=lambda d: d["profit_total"], reverse=True)
    return deals


__all__ = [
    "AUCTION_FEE", "BREAKEVEN_MULT",
    "net_proceeds", "required_sell_price",
    "median", "percentile", "summarize_sales", "find_deals",
]
