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


def load_compact_catalog(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load data/catalog.json ({_meta, items: {id: {ru, en, icon, cat}}})."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("items"), dict):
        return data["items"]
    return data if isinstance(data, dict) else {}


def load_overrides(path: str | Path) -> dict[str, str]:
    """Manual RU names for items missing from the official DB ({id: name})."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = data.get("items", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in items.items()} if isinstance(items, dict) else {}


def display_name(item_id: str, catalog: dict[str, dict[str, Any]],
                 overrides: dict[str, str] | None = None) -> str:
    """Best Russian name for an item (override > catalog > raw id)."""
    if overrides and item_id in overrides:
        return overrides[item_id]
    info = catalog.get(item_id)
    if info and info.get("ru"):
        return str(info["ru"])
    return item_id


def search_items(query: str, catalog: dict[str, dict[str, Any]],
                 overrides: dict[str, str] | None = None,
                 limit: int = 10) -> list[tuple[str, str, str]]:
    """Substring search over RU/EN names and ids -> [(id, ru, en)]."""
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, str, str, str]] = []
    for item_id, info in catalog.items():
        ru = str(info.get("ru", ""))
        en = str(info.get("en", ""))
        hay = f"{ru}\n{en}\n{item_id}".lower()
        if q not in hay:
            continue
        if ru.lower().startswith(q) or item_id.lower() == q:
            score = 0
        elif q in ru.lower():
            score = 1
        elif q in en.lower():
            score = 2
        else:
            score = 3
        scored.append((score, item_id, ru, en))
    if overrides:
        for item_id, ru in overrides.items():
            if item_id in catalog or q not in f"{ru}\n{item_id}".lower():
                continue
            scored.append((1, item_id, ru, ""))
    scored.sort(key=lambda t: (t[0], t[2] or t[1]))
    return [(i, ru, en) for _, i, ru, en in scored[:limit]]
