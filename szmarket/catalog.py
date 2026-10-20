"""Item catalog loader for the official EXBO-Studio/stalzone-database layout.

realm/items/<category>/<subcat>/<itemId>.json, e.g.
  ru/items/weapon/pistol/0n9q.json
Only names + category are extracted (small memory footprint).
Docs: https://eapi.stalcraft.net/items.html
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_item_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name", {})
    lines = name.get("lines", {}) if isinstance(name, dict) else {}
    return {
        "id": str(data.get("id", path.stem)),
        "name_ru": str(lines.get("ru", "")) if isinstance(lines, dict) else "",
        "name_en": str(lines.get("en", "")) if isinstance(lines, dict) else "",
        "category": str(data.get("category", "")),
    }


def load_catalog(items_root: str | Path) -> dict[str, dict[str, Any]]:
    """Walk an `items/` directory -> {item_id: info}."""
    out: dict[str, dict[str, Any]] = {}
    for fp in Path(items_root).rglob("*.json"):
        info = parse_item_file(fp)
        if info:
            out[info["id"]] = info
    return out
