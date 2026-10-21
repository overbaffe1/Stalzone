"""Export trip routes as in-game waypoint markers (waypoints.cfg).

Format (BuildersSC/waypoint_image_print + HelloGames Waypoint-Editor):
  [{"time": ms, "name": str, "pos": {"x","y","z"}, "color": ARGB-decimal,
    "icon_index": int, "can_position_float": bool, "type": "manual"}]

Default file: %APPDATA%/EXBO/runtime/stalcraft/config/waypoints.cfg.

World coordinates are unknown to us, so the user drops two anchors in
game (SZ_A / SZ_B at two known settlements); we read them back and fit
an affine map our-0..1 -> world x/z. Markers are named SZ_1..N (+ SZ_S
for start) so they can be wiped without touching user waypoints.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PREFIX = "SZ_"
ANCHOR_A = "SZ_A"
ANCHOR_B = "SZ_B"
COLOR_ROUTE = 0xFFFFC857  # золото
COLOR_START = 0xFF4CAF50  # зелёный
ICON_DEFAULT = 1


def default_cfg_candidates() -> list[Path]:
    home = Path.home()
    cands = [
        home / "AppData" / "Roaming" / "EXBO" / "runtime" / "stalcraft"
        / "config" / "waypoints.cfg",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(Path(appdata) / "EXBO" / "runtime" / "stalcraft"
                     / "config" / "waypoints.cfg")
    for drive in ("C:", "D:", "E:"):
        for base in ("Program Files (x86)/Steam/steamapps/common",
                     "Steam/steamapps/common", "Games"):
            cands.append(Path(f"{drive}/{base}/STALCRAFT/config/waypoints.cfg"))
            cands.append(Path(f"{drive}/{base}/STALZONE/config/waypoints.cfg"))
    return cands


def find_waypoints_cfg() -> Path | None:
    for p in default_cfg_candidates():
        if p.exists() and p.is_file():
            return p
    return None


def load_waypoints(path: str | Path) -> list[dict[str, Any]]:
    """Parse waypoints.cfg (JSON array). Empty list if missing/broken."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [m for m in data if isinstance(m, dict)] if isinstance(
            data, list) else []
    except (OSError, ValueError):
        return []


def save_waypoints(path: str | Path, marks: list[dict[str, Any]],
                   backup: bool = True) -> Path | None:
    """Write markers back; returns backup path (or None if no backup)."""
    p = Path(path)
    bak = None
    if backup and p.exists():
        bak = p.with_suffix(f".szbak{p.suffix}")
        try:
            bak.write_bytes(p.read_bytes())
        except OSError:
            bak = None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(marks, ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return bak


def find_anchor(marks: list[dict[str, Any]],
                name: str) -> dict[str, float] | None:
    for m in marks:
        if m.get("name") == name and isinstance(m.get("pos"), dict):
            try:
                return {"x": float(m["pos"].get("x", 0.0)),
                        "y": float(m["pos"].get("y", 0.0)),
                        "z": float(m["pos"].get("z", 0.0))}
            except (TypeError, ValueError):
                return None
    return None


def fit_affine(our_a: tuple[float, float], world_a: dict[str, float],
               our_b: tuple[float, float], world_b: dict[str, float]
               ) -> dict[str, float] | None:
    """world_x = ax*ox + bx ; world_z = az*oy + bz. None if degenerate."""
    (ax0, ay0), (bx0, by0) = our_a, our_b
    if ax0 == bx0 or ay0 == by0:
        return None
    ax = (world_b["x"] - world_a["x"]) / (bx0 - ax0)
    bx = world_a["x"] - ax * ax0
    az = (world_b["z"] - world_a["z"]) / (by0 - ay0)
    bz = world_a["z"] - az * ay0
    return {"ax": ax, "bx": bx, "az": az, "bz": bz,
            "ya": world_a["y"], "yb": world_b["y"]}


def apply_affine(aff: dict[str, float], ox: float, oy: float
                 ) -> tuple[float, float]:
    return aff["ax"] * ox + aff["bx"], aff["az"] * oy + aff["bz"]


def make_marker(name: str, x: float, y: float, z: float,
                color: int = COLOR_ROUTE,
                icon: int = ICON_DEFAULT) -> dict[str, Any]:
    return {"time": int(time.time() * 1000), "name": name,
            "pos": {"x": float(x), "y": float(y), "z": float(z)},
            "color": int(color), "icon_index": int(icon),
            "can_position_float": False, "type": "manual"}


def merge_marks(existing: list[dict[str, Any]],
                new: list[dict[str, Any]],
                keep_anchors: bool = True) -> list[dict[str, Any]]:
    """Drop our old SZ_* markers (optionally keeping anchors), append new."""
    keep = []
    for m in existing:
        nm = str(m.get("name", ""))
        if not nm.startswith(PREFIX):
            keep.append(m)
        elif keep_anchors and nm in (ANCHOR_A, ANCHOR_B):
            keep.append(m)
    return keep + new


def strip_marks(existing: list[dict[str, Any]],
                keep_anchors: bool = True) -> list[dict[str, Any]]:
    return merge_marks(existing, [], keep_anchors)


def build_route_marks(order: list[tuple[str, float, float]],
                      aff: dict[str, float],
                      y_default: float = 70.0) -> list[dict[str, Any]]:
    """order: [(label, ox, oy)] -> in-game markers SZ_S/SZ_1....

    First label is the start (green), rest are numbered gold stops.
    """
    out = []
    for k, (label, ox, oy) in enumerate(order):
        x, z = apply_affine(aff, ox, oy)
        ya, yb = aff.get("ya", y_default), aff.get("yb", y_default)
        y = ya if k == 0 else (ya + yb) / 2.0
        if k == 0:
            out.append(make_marker(f"{PREFIX}S {label}", x, y, z,
                                   COLOR_START))
        else:
            out.append(make_marker(f"{PREFIX}{k} {label}", x, y, z,
                                   COLOR_ROUTE))
    return out
