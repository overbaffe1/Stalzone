"""Lazy item-icon downloads with a local disk cache.

Icon relative paths come from data/catalog.json (EXBO listing.json),
e.g. "/icons/supply/medicine/9mmq.png". Files are fetched from
raw.githubusercontent.com ONCE and stored under data/icons/{id}.png.

Network/PIL failures always degrade to None (UI shows text only).
"""
from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ICON_BASE = ("https://raw.githubusercontent.com/EXBO-Studio/stalzone-database"
             "/main/ru")


def icon_file(item_id: str, catalog: dict[str, dict[str, Any]],
              cache_dir: str | Path = "data/icons") -> Path | None:
    """Local PNG path for an item (downloads on first use), or None."""
    info = catalog.get(item_id)
    if not info or not info.get("icon"):
        return None
    dest = Path(cache_dir) / f"{item_id}.png"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = ICON_BASE + str(info["icon"])
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "szmarket/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            blob = resp.read()
        if len(blob) < 100:
            return None
        dest.write_bytes(blob)
        return dest
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def load_icon_image(item_id: str, catalog: dict[str, dict[str, Any]],
                    size: int = 44,
                    cache_dir: str | Path = "data/icons") -> Any | None:
    """PIL thumbnail (RGBA, max `size` px) or None if unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return None
    path = icon_file(item_id, catalog, cache_dir)
    if path is None:
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGBA")
            img.thumbnail((size, size))
            return img.copy()
    except (OSError, ValueError):
        return None
