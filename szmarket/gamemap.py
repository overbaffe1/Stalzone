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

import os
from pathlib import Path
from typing import Any

MIN_TILE_SIZE = 7 * 1024
PREFIX = "r."
SUFFIX = ".ol"

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
