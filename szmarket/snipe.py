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

# Game rule: every next bid must exceed the current one by >= 5%.
BID_STEP = 1.05


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


def current_bid_total(lot: dict[str, Any]) -> float:
    """Current bid (stack total): live currentPrice, else startPrice, else 0."""
    raw = lot.get("raw") if isinstance(lot.get("raw"), dict) else {}
    for key in ("currentPrice", "current_price", "startPrice", "start_price"):
        val = raw.get(key)
        if val:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def analyze_ending(
    lots: list[dict[str, Any]],
    summary: dict[str, Any],
    now: dt.datetime | None = None,
    max_hours: float = 3.0,
    fee: float = AUCTION_FEE,
    min_margin: float = 0.02,
    min_sales: int = 5,
) -> list[dict[str, Any]]:
    """Score lots ending soon against sales history. Soonest expiry first.

    Every row carries BOTH verdicts: instant buyout (buyout_total <= ceiling)
    and bidding (next valid bid = current * 1.05 <= ceiling). A lot is kept
    if at least one path clears `min_margin`.
    """
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

        profit_buyout = max_buy_total - buy_total
        margin_buyout = profit_buyout / buy_total if buy_total > 0 else 0.0
        buyout_ok = margin_buyout >= min_margin

        current = current_bid_total(lot)
        min_next_bid = (current * BID_STEP) if current > 0 else 1.0
        profit_bid = max_buy_total - min_next_bid
        margin_bid = profit_bid / min_next_bid if min_next_bid > 0 else 0.0
        bid_ok = margin_bid >= min_margin and max_buy_total > 0

        if not (buyout_ok or bid_ok):
            continue
        if buyout_ok and bid_ok:
            verdict = "🔥 ВЫКУПАЙ"
        elif buyout_ok:
            verdict = "🔥 ВЫКУПАЙ"
        else:
            verdict = "✋ СТАВЬ"
        out.append({
            "time_left_sec": left,
            "time_left": format_left_ru(left),
            "amount": amount,
            "buy_total": buy_total,
            "unit_buy": float(buy_unit),
            "max_buy_total": max_buy_total,
            "fair_median": summary["median"],
            "profit_total": profit_buyout if buyout_ok else profit_bid,
            "margin": margin_buyout if buyout_ok else margin_bid,
            "buyout_ok": buyout_ok,
            "bid_ok": bid_ok,
            "current_bid": current,
            "min_next_bid": min_next_bid,
            "profit_bid": profit_bid,
            "verdict": verdict,
            "lot": lot,
        })
    out.sort(key=lambda d: d["time_left_sec"])
    return out
