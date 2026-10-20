"""Ending-soon lots ("sniper"): lots about to expire that can be resold higher.

For every active lot ending within `max_hours` we compute:
  max_buy   = median_sale * (1 - fee) * amount   (breakeven incl. commission)
  profit    = max_buy - buyout_total
Lots with profit above `min_margin` are worth an instant buyout now.
Sorted by time left (soonest first).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .config import AUCTION_FEE


def parse_time(value: Any) -> dt.datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        moment = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment


def format_left_ru(seconds: float) -> str:
    """'1 ч 05 мин' / '12 мин' / '45 сек'."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} ч {m:02d} мин"
    if m:
        return f"{m} мин"
    return f"{sec} сек"


def analyze_ending(
    lots: list[dict[str, Any]],
    summary: dict[str, Any],
    now: dt.datetime | None = None,
    max_hours: float = 3.0,
    fee: float = AUCTION_FEE,
    min_margin: float = 0.02,
    min_sales: int = 5,
) -> list[dict[str, Any]]:
    """Score lots ending soon against sales history. Soonest expiry first."""
    if summary.get("count", 0) < min_sales or summary.get("median", 0) <= 0:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    fair_unit = summary["median"] * (1.0 - fee)
    out: list[dict[str, Any]] = []
    for lot in lots:
        end = parse_time(lot.get("end_time"))
        if end is None:
            continue
        left = (end - now).total_seconds()
        if left <= 0 or left > max_hours * 3600:
            continue
        buy_unit = lot.get("unit_price")
        if not buy_unit or buy_unit <= 0:
            continue
        amount = int(lot.get("amount", 1) or 1)
        buy_total = float(buy_unit) * amount
        max_buy_total = fair_unit * amount
        profit = max_buy_total - buy_total
        margin = profit / buy_total if buy_total > 0 else 0.0
        if margin < min_margin:
            continue
        out.append({
            "time_left_sec": left,
            "time_left": format_left_ru(left),
            "amount": amount,
            "buy_total": buy_total,
            "unit_buy": float(buy_unit),
            "max_buy_total": max_buy_total,
            "fair_median": summary["median"],
            "profit_total": profit,
            "margin": margin,
            "lot": lot,
        })
    out.sort(key=lambda d: d["time_left_sec"])
    return out
