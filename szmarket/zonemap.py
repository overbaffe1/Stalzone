"""Real Zone map background: STALZONE Wiki previews + user calibration.

The wiki publishes whole-zone preview renders which the app downloads
ONCE at runtime (user's PC has full internet) and stitches into one
image (north zone on top, south zone below):

  NORTH_URL = .../map/previews/north-zone.png
  SOUTH_URL = .../map/previews/the-zone.png

Settlement markers live in 0..1 coordinates of the stitched image.
Defaults come from data/map_settlements.json; the user drags markers
to their true spots in calibration mode, saved to
data/map_calibration.json (gitignored, per-PC).

PIL is optional here: without it the app falls back to the schematic
Canvas map. No network happens at import time.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

NORTH_URL = "https://cdn3.stalzone.wiki/map/previews/north-zone.png"
SOUTH_URL = "https://cdn3.stalzone.wiki/map/previews/the-zone.png"
COMBINED_NAME = "zone_combined.png"
CALIBRATION_NAME = "map_calibration.json"


def _fetch(url: str, dest: Path, timeout: float = 30.0) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "szmarket/0.5"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            blob = resp.read()
        if len(blob) < 5_000:
            return False
        dest.write_bytes(blob)
        return True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def ensure_background(cache_dir: str | Path = "data/maps",
                      timeout: float = 30.0) -> Path | None:
    """Download + stitch the real map. Returns combined PNG or None."""
    try:
        from PIL import Image
    except ImportError:
        return None
    d = Path(cache_dir)
    north, south = d / "north-zone.png", d / "south-zone.png"
    combined = d / COMBINED_NAME
    if combined.exists() and combined.stat().st_size > 5_000:
        return combined
    ok_n = north.exists() or _fetch(NORTH_URL, north, timeout)
    ok_s = south.exists() or _fetch(SOUTH_URL, south, timeout)
    if not (ok_n and ok_s):
        return None
    try:
        a = Image.open(north).convert("RGB")
        b = Image.open(south).convert("RGB")
        w = max(a.width, b.width)
        a = a.resize((w, int(a.height * w / a.width)))
        b = b.resize((w, int(b.height * w / b.width)))
        out = Image.new("RGB", (w, a.height + b.height))
        out.paste(a, (0, 0))
        out.paste(b, (0, a.height))
        out.save(combined)
        return combined
    except (OSError, ValueError):
        try:
            combined.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def load_calibration(path: str | Path = "data/map_calibration.json"
                     ) -> dict[str, list[float]]:
    """station -> [x, y] user overrides (empty dict if none)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        out = {}
        for k, v in (data or {}).items():
            if (isinstance(v, (list, tuple)) and len(v) == 2
                    and all(isinstance(n, (int, float)) for n in v)):
                out[k] = [min(max(float(v[0]), 0.0), 1.0),
                          min(max(float(v[1]), 0.0), 1.0)]
        return out
    except (OSError, ValueError):
        return {}


def save_calibration(calib: dict[str, list[float]],
                     path: str | Path = "data/map_calibration.json") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(calib, ensure_ascii=False, indent=1),
                 encoding="utf-8")


def apply_calibration(markers: list[dict[str, Any]],
                      calib: dict[str, list[float]]) -> list[dict[str, Any]]:
    """Override marker x/y in place from calibration; returns markers."""
    for m in markers:
        if m.get("station") in calib:
            m["x"], m["y"] = calib[m["station"]]
    return markers
