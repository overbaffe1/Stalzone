"""SQLite storage for auction snapshots (stdlib only).

Design notes (borrowed from Chiiivvas/stalzone-auction-monitor):
- EAPI lots carry no stable lot id, so we dedupe by SHA-256 fingerprint of
  immutable fields. `currentPrice`-like mutable fields are excluded.
- Raw JSON of every record is kept, so if EXBO changes field names we can
  re-extract without refetching.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
  fingerprint TEXT PRIMARY KEY,
  item_id     TEXT NOT NULL,
  region      TEXT NOT NULL,
  fetched_at  INTEGER NOT NULL,
  amount      INTEGER NOT NULL DEFAULT 1,
  total_price REAL,
  unit_price  REAL,
  start_time  TEXT,
  end_time    TEXT,
  raw         TEXT
);
CREATE INDEX IF NOT EXISTS idx_lots_item ON lots(item_id, region, fetched_at);

CREATE TABLE IF NOT EXISTS sales (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id     TEXT NOT NULL,
  region      TEXT NOT NULL,
  fetched_at  INTEGER NOT NULL,
  price_raw   REAL NOT NULL,
  amount      INTEGER NOT NULL DEFAULT 1,
  unit_price  REAL NOT NULL,
  sold_at     TEXT,
  raw         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_item ON sales(item_id, region, fetched_at);
"""


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def lot_fingerprint(item_id: str, lot: dict[str, Any]) -> str:
    stable = {
        "itemId": item_id,
        "amount": _pick(lot, "amount", default=1),
        "startPrice": _pick(lot, "startPrice", "start_price"),
        "buyoutPrice": _pick(lot, "buyoutPrice", "buyout_price"),
        "startTime": _pick(lot, "startTime", "start_time"),
        "endTime": _pick(lot, "endTime", "end_time"),
        "additional": _pick(lot, "additional"),
    }
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def extract_lot_numbers(lot: dict[str, Any]) -> tuple[int, float | None, float | None]:
    amount = int(_pick(lot, "amount", default=1) or 1)
    total = _pick(
        lot,
        "buyoutPrice", "buyout_price",
        "startPrice", "start_price",
        "currentPrice", "current_price",
        "price",
        default=None,
    )
    if total is None:
        return amount, None, None
    total_f = float(total)
    return amount, total_f, total_f / max(amount, 1)


class MarketStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- lots ----------------------------------------------------------
    def save_lots(self, region: str, item_id: str, lots: list[dict[str, Any]]) -> int:
        now = int(time.time())
        rows = []
        for lot in lots:
            amount, total, unit = extract_lot_numbers(lot)
            rows.append(
                (
                    lot_fingerprint(item_id, lot),
                    item_id, region, now, amount, total, unit,
                    _pick(lot, "startTime", "start_time", default=""),
                    _pick(lot, "endTime", "end_time", default=""),
                    json.dumps(lot, ensure_ascii=False),
                )
            )
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO lots "
            "(fingerprint, item_id, region, fetched_at, amount, total_price,"
            " unit_price, start_time, end_time, raw) VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0

    def latest_lots(self, item_id: str, region: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT amount, total_price, unit_price, start_time, end_time, raw FROM lots "
            "WHERE item_id=? AND region=? AND fetched_at=("
            "  SELECT MAX(fetched_at) FROM lots WHERE item_id=? AND region=?)",
            (item_id, region, item_id, region),
        )
        out = []
        for amount, total, unit, st, en, raw in cur.fetchall():
            out.append(
                {
                    "amount": amount, "total_price": total, "unit_price": unit,
                    "start_time": st, "end_time": en, "raw": json.loads(raw),
                }
            )
        return out

    # -- sales history -------------------------------------------------
    def save_sales(
        self,
        region: str,
        item_id: str,
        sales: list[dict[str, Any]],
        price_is_total: bool = True,
    ) -> int:
        """Store sales. `price_is_total=True` means price covers the whole stack.

        VERIFIED on production 2026-10-21: a 96430 sale of
        {amount: 5, price: 25} matches active lots of {amount: 5, buyout: 25},
        so history `price` is the TOTAL stack price (unit = price / amount).
        """
        now = int(time.time())
        rows = []
        for s in sales:
            price_raw = float(_pick(s, "price", default=0.0) or 0.0)
            amount = int(_pick(s, "amount", default=1) or 1)
            unit = price_raw / max(amount, 1) if price_is_total else price_raw
            rows.append(
                (
                    item_id, region, now, price_raw, amount, unit,
                    _pick(s, "soldAt", "sold_at", "time", "date", default=""),
                    json.dumps(s, ensure_ascii=False),
                )
            )
        cur = self.conn.executemany(
            "INSERT INTO sales (item_id, region, fetched_at, price_raw, amount,"
            " unit_price, sold_at, raw) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0

    def recent_sales(
        self, item_id: str, region: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT unit_price, amount, sold_at, fetched_at FROM sales "
            "WHERE item_id=? AND region=? ORDER BY fetched_at DESC, id DESC LIMIT ?",
            (item_id, region, limit),
        )
        return [
            {"unit_price": u, "amount": a, "sold_at": s, "fetched_at": t}
            for u, a, s, t in cur.fetchall()
        ]

    def tracked_items(self, region: str) -> list[str]:
        cur = self.conn.execute(
            "SELECT DISTINCT item_id FROM ("
            " SELECT item_id FROM lots WHERE region=?"
            " UNION SELECT item_id FROM sales WHERE region=?) ORDER BY 1",
            (region, region),
        )
        return [r[0] for r in cur.fetchall()]
