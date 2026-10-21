"""Карты из папки игры (PDA-тайлы STALZONE/STALCRAFT).

По исследованию onejeuu/sc-file: плоские тайлы ``r.<x>.<z>.ol`` лежат
в ``pda/<карта>`` внутри слоёв ассетов и склеиваются в порядке загрузки:
  modassets/assets
  modassets/assets/_localized/<регион>
  bin_<realm>/patchassets/assets (+ _localized/<регион>)
Сетка тайлов линейна в мировых координатах — идеальный фон для рейса.

Сборка картинки требует pip-пакет ``sc-file`` (импортируется лениво),
а поиск папок и математика сетки работают без него.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MIN_TILE_SIZE = 7 * 1024
PREFIX = "r."
SUFFIX = ".ol"

# v9: мировой тайл/регион — 512x512 м (подтверждено совпадением
# map-new_bar/r.-3.-10.ol с mapcache reg.-3.-10 из сейва Бара).
REGION_M = 512

GAME_DIR_NAMES = ("STALZONE", "STALCRAFT")
ASSETS = Path("modassets/assets")
PATCHASSETS = Path("patchassets/assets")


def default_hints() -> list[Path]:
    """Вероятные корни игры (для стартовой папки диалога)."""
    out: list[Path] = []
    progs = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for prog in progs:
        for name in GAME_DIR_NAMES:
            p = Path(prog) / "Steam/steamapps/common" / name
            if p.is_dir():
                out.append(p)
    home = Path(os.environ.get("USERPROFILE", Path.home()))
    for sub in ("AppData/Roaming/EXBO/runtime/stalcraft",
                "EXBO/runtime/stalcraft"):
        p = home / sub
        if (p / ASSETS).is_dir():
            out.append(p)
    return out


def find_game_root(start: Path | str | None = None) -> Path | None:
    """Подняться от `start` (или cwd) до папки с modassets/assets."""
    cur = Path(start or Path.cwd()).resolve()
    for cand in (cur, *cur.parents):
        if (cand / ASSETS).is_dir():
            return cand
    return None


def asset_layers(root: Path | str, region: str = "ru") -> list[Path]:
    """Слои ассетов в порядке загрузки игрой (поздние перекрывают)."""
    root = Path(root)
    realm = "ru" if str(region).lower() == "ru" else "global"
    patch = root / f"bin_{realm}" / PATCHASSETS
    layers = [root / ASSETS,
              root / ASSETS / "_localized" / region,
              patch,
              patch / "_localized" / region]
    return [p for p in layers if p.is_dir()]


def list_maps(root: Path | str, region: str = "ru") -> list[str]:
    """Имена PDA-карт (папки pda/map*, первая — обычно весь мир)."""
    names: set[str] = set()
    for layer in asset_layers(root, region):
        pda = layer / "pda"
        if not pda.is_dir():
            continue
        for sub in pda.iterdir():
            if sub.is_dir() and sub.name.startswith("map"):
                names.add(sub.name)
    order = sorted(names)
    order.sort(key=lambda n: (n != "map", n))  # "map" (весь мир) первая
    return order


def parse_tile_name(name: str) -> tuple[int, int] | None:
    """r.<x>.<z>.ol -> (x, z)."""
    if not name.startswith(PREFIX) or not name.endswith(SUFFIX):
        return None
    try:
        x, z = name[len(PREFIX):-len(SUFFIX)].split(".")
        return int(x), int(z)
    except ValueError:
        return None


def scan_tiles(folders: list[Path | str]) -> dict[tuple[int, int], Path]:
    """Собрать тайлы из папок (поздние папки перекрывают ранние)."""
    tiles: dict[tuple[int, int], Path] = {}
    for folder in folders:
        try:
            entries = sorted(Path(folder).iterdir())
        except OSError:
            continue
        for p in entries:
            if not p.is_file() or p.suffix.lower() != SUFFIX:
                continue
            try:
                if p.stat().st_size < MIN_TILE_SIZE:
                    continue
            except OSError:
                continue
            key = parse_tile_name(p.name)
            if key is not None:
                tiles[key] = p
    return tiles


def collect_map_tiles(root: Path | str, map_name: str,
                      region: str = "ru") -> dict[tuple[int, int], Path]:
    """Тайлы карты из всех слоёв в порядке загрузки игрой."""
    folders = []
    for layer in asset_layers(root, region):
        pda = layer / "pda" / map_name
        if pda.is_dir():
            folders.append(pda)
    return scan_tiles(folders)


def tile_bounds(tiles: dict[tuple[int, int], Path]
                ) -> dict[str, int] | None:
    """Границы сетки тайлов: min/max x/z + число."""
    if not tiles:
        return None
    xs = [k[0] for k in tiles]
    zs = [k[1] for k in tiles]
    return {"min_x": min(xs), "max_x": max(xs),
            "min_z": min(zs), "max_z": max(zs), "count": len(tiles)}


def assemble(tiles: dict[tuple[int, int], Path], out: Path | str,
             image_format: str = "JPEG") -> dict[str, Any]:
    """Склеить тайлы в одну картинку (нужен pip-пакет sc-file)."""
    if not tiles:
        raise ValueError("Нет тайлов для сборки.")
    try:
        from scfile.convert import maptiles as maps
        from scfile.convert.regions import Region
    except ImportError as e:
        raise RuntimeError(
            "Для карты из игры нужен пакет sc-file: "
            "pip install sc-file") from e
    conv = {Region(x, z): p for (x, z), p in tiles.items()}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save: dict[str, Any] = ({"format": "JPEG", "quality": 88}
                            if image_format.upper() != "PNG"
                            else {"format": "PNG", "compress_level": 6})
    result = maps.render(conv, out, save=save)
    meta = tile_bounds(tiles) or {}
    meta.update({"image": str(result.output), "tiles": result.tiles})
    return meta


# -- v9: встроенные карты из PDA-архива -----------------------------------

def shipped_dir() -> Path:
    """Папка со встроенными фонами (assets/maps в корне репозитория)."""
    return Path(__file__).resolve().parent.parent / "assets" / "maps"


def load_shipped() -> list[dict[str, Any]]:
    """Метаданные встроенных карт (файл game_maps.json)."""
    path = shipped_dir() / "game_maps.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    maps = data.get("maps", []) if isinstance(data, dict) else []
    out = []
    for m in maps:
        if not isinstance(m, dict) or "id" not in m or "file" not in m:
            continue
        m = dict(m)
        m["path"] = str(shipped_dir() / m["file"])
        out.append(m)
    return out


def _grid(meta: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        gx = (int(meta["min_x"]), int(meta["max_x"]))
        gz = (int(meta["min_z"]), int(meta["max_z"]))
    except (KeyError, TypeError, ValueError):
        return None
    if gx[1] < gx[0] or gz[1] < gz[0]:
        return None
    return gx[0], gx[1], gz[0], gz[1]


def is_world(meta: dict[str, Any]) -> bool:
    """True, если у карты мировая привязка (якоря не нужны)."""
    return bool(meta.get("world")) and _grid(meta) is not None


def world_to_frac(meta: dict[str, Any], x: float, z: float
                  ) -> tuple[float, float] | None:
    """Мировые метры -> доли 0..1 встроенного фона (None для локальных)."""
    g = _grid(meta) if is_world(meta) else None
    if g is None:
        return None
    min_x, max_x, min_z, max_z = g
    fx = (float(x) - min_x * REGION_M) / ((max_x - min_x + 1) * REGION_M)
    fz = (float(z) - min_z * REGION_M) / ((max_z - min_z + 1) * REGION_M)
    return fx, fz


def frac_to_world(meta: dict[str, Any], fx: float, fy: float
                  ) -> tuple[float, float] | None:
    """Доли 0..1 фона -> мировые метры (x, z). None для локальных."""
    g = _grid(meta) if is_world(meta) else None
    if g is None:
        return None
    min_x, max_x, min_z, max_z = g
    x = min_x * REGION_M + float(fx) * (max_x - min_x + 1) * REGION_M
    z = min_z * REGION_M + float(fy) * (max_z - min_z + 1) * REGION_M
    return x, z


def meters_per_pixel(meta: dict[str, Any]) -> float | None:
    """Сколько метров в пикселе встроенного JPG (None для локальных)."""
    g = _grid(meta) if is_world(meta) else None
    if g is None:
        return None
    try:
        w = float(meta["img_w"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0:
        return None
    min_x, max_x, _min_z, _max_z = g
    return (max_x - min_x + 1) * REGION_M / w


def describe(meta: dict[str, Any]) -> str:
    """Однострочное RU-описание карты для интерфейса."""
    title = str(meta.get("title", meta.get("id", "?")))
    if is_world(meta):
        mpp = meters_per_pixel(meta)
        g = _grid(meta) or (0, 0, 0, 0)
        scale = f"{mpp:.2f} м/пиксель".replace(".", ",") if mpp else "?"
        return (f"{title}: мировая привязка ✅ ({scale}; "
                f"x {g[0] * REGION_M}…{(g[1] + 1) * REGION_M}, "
                f"z {g[2] * REGION_M}…{(g[3] + 1) * REGION_M} м)")
    return f"{title}: нужны якоря SZ_A/SZ_B 🎯 ({meta.get('note', '')})"
