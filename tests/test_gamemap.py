"""v8: поиск папки игры, слои ассетов, математика тайлов (без sc-file)."""
from __future__ import annotations

import sys

import pytest

from szmarket import gamemap


def _tile(path, x: int, z: int, size: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


def test_parse_tile_name():
    assert gamemap.parse_tile_name("r.12.-34.ol") == (12, -34)
    assert gamemap.parse_tile_name("r.1.2.png") is None
    assert gamemap.parse_tile_name("notes.txt") is None
    assert gamemap.parse_tile_name("r.a.b.ol") is None


def test_find_root_walks_up(tmp_path):
    deep = tmp_path / "STALZONE" / "modassets" / "assets" / "pda"
    deep.mkdir(parents=True)
    found = gamemap.find_game_root(deep)
    assert found == tmp_path / "STALZONE"
    assert gamemap.find_game_root(tmp_path) is None


def test_layers_order_and_maps(tmp_path):
    root = tmp_path / "STALZONE"
    (root / "modassets/assets/pda/map").mkdir(parents=True)
    (root / "modassets/assets/pda/map-new_bar").mkdir(parents=True)
    (root / "modassets/assets/_localized/ru/pda/map").mkdir(parents=True)
    (root / "bin_ru/patchassets/assets/pda/map").mkdir(parents=True)
    (root / "bin_ru/patchassets/assets/_localized/ru/pda").mkdir(
        parents=True)
    layers = gamemap.asset_layers(root, "ru")
    assert len(layers) == 4
    assert layers[0] == root / "modassets/assets"
    assert gamemap.list_maps(root, "ru")[0] == "map"


def test_scan_skips_small_and_overrides(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    _tile(a / "r.0.0.ol", 0, 0)
    _tile(a / "r.1.0.ol", 1, 0, size=100)  # мусор < 7KB
    _tile(b / "r.0.0.ol", 0, 0)  # перекрывает слой a
    tiles = gamemap.scan_tiles([a, b])
    assert set(tiles) == {(0, 0)}
    assert tiles[(0, 0)].parent == b
    bounds = gamemap.tile_bounds(tiles)
    assert bounds == {"min_x": 0, "max_x": 0, "min_z": 0,
                      "max_z": 0, "count": 1}
    assert gamemap.tile_bounds({}) is None


def test_collect_map_tiles(tmp_path):
    root = tmp_path / "STALZONE"
    m1 = root / "modassets/assets/pda/map"
    m2 = root / "bin_ru/patchassets/assets/pda/map"
    _tile(m1 / "r.0.0.ol", 0, 0)
    _tile(m2 / "r.1.0.ol", 1, 0)
    tiles = gamemap.collect_map_tiles(root, "map", "ru")
    assert set(tiles) == {(0, 0), (1, 0)}


def test_assemble_without_scfile(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "scfile", None)
    monkeypatch.setitem(sys.modules, "scfile.convert", None)
    with pytest.raises(RuntimeError, match="sc-file"):
        gamemap.assemble({(0, 0): tmp_path / "r.0.0.ol"},
                         tmp_path / "out.jpg")
