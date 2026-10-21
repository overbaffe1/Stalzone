"""Trip planner over a schematic Zone map (data/map_settlements.json).

Pure logic, no UI: group evaluated crafts/barter by settlement, order
stops by greedy nearest-neighbour from a start point, aggregate totals.
Hideout crafts need no travel -> they go to the `home` group.
Off-map points (session PvP arena) are listed separately, never routed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

DEFAULT_MAP = "data/map_settlements.json"


def load_map(path: str | Path = DEFAULT_MAP) -> dict[str, dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(data.get("stations", {}))


def coords_of(station: str, m: dict[str, dict[str, Any]]
              ) -> tuple[float, float] | None:
    info = m.get(station)
    if not info or info.get("offmap"):
        return None
    x, y = info.get("x"), info.get("y")
    if x is None or y is None:
        return None
    return float(x), float(y)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def order_stops(stations: list[str], start: str,
                m: dict[str, dict[str, Any]]) -> list[str]:
    """Greedy nearest-neighbour order starting at `start`.

    Stations without coordinates (and unknown ones) keep input order and
    go last. `start` itself is first if present in `stations`.
    """
    with_xy = [s for s in stations if coords_of(s, m) is not None]
    without = [s for s in stations if coords_of(s, m) is None]
    if not with_xy:
        return list(stations)
    anchor = start if start in with_xy else with_xy[0]
    order = [anchor]
    rest = [s for s in with_xy if s != anchor]
    while rest:
        cur = coords_of(order[-1], m)
        assert cur is not None
        rest.sort(key=lambda s: _dist(cur, coords_of(s, m)))  # type: ignore[arg-type]
        order.append(rest.pop(0))
    return order + without


def build_trip(evals: list[dict[str, Any]], start: str,
               m: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Group evaluated crafts into a trip: home + ordered stops + offmap."""
    home = [e for e in evals if e.get("kind") == "hideout"]
    away = [e for e in evals if e.get("kind") != "hideout"]
    by_station: dict[str, list[dict[str, Any]]] = {}
    for e in away:
        by_station.setdefault(str(e.get("station", "?")), []).append(e)
    order = order_stops(sorted(by_station), start, m)
    stops = []
    for s in order:
        crafts = by_station[s]
        buy: dict[str, float] = {}
        for e in crafts:
            for b in e.get("breakdown", []):
                buy[b["item"]] = buy.get(b["item"], 0.0) + float(b["count"])
        stops.append({
            "station": s,
            "coords": coords_of(s, m),
            "crafts": crafts,
            "buy": buy,
            "money_npc": sum(float(e.get("money", 0) or 0) for e in crafts),
            "profit": sum(float(e.get("profit", 0) or 0) for e in crafts),
            "cost": sum(float(e.get("cost", 0) or 0) for e in crafts),
        })
    routed = [t for t in stops if t["coords"] is not None]
    offmap = [t for t in stops if t["coords"] is None]
    total_profit = (sum(float(e.get("profit", 0) or 0) for e in home)
                    + sum(t["profit"] for t in stops))
    return {"home": home, "stops": routed, "offmap": offmap,
            "total_profit": total_profit, "start": start}
