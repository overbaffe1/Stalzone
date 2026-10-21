"""Real-map helpers: URLs, calibration roundtrip, stitch (needs PIL)."""
import json

from szmarket import zonemap


def test_preview_urls_shape():
    assert zonemap.NORTH_URL.endswith("north-zone.png")
    assert zonemap.SOUTH_URL.endswith("the-zone.png")
    assert zonemap.NORTH_URL.startswith("https://")
    assert zonemap.COMBINED_NAME.endswith(".png")


def test_calibration_roundtrip_and_clamping(tmp_path):
    p = tmp_path / "cal.json"
    assert zonemap.load_calibration(p) == {}
    zonemap.save_calibration({"A": [0.2, 0.3], "B": [9.0, -1.0],
                              "junk": "x"}, p)
    back = zonemap.load_calibration(p)
    assert back["A"] == [0.2, 0.3]
    assert back["B"] == [1.0, 0.0]
    assert "junk" not in back


def test_apply_calibration_overrides_markers():
    markers = [{"station": "A", "x": 0.1, "y": 0.1},
               {"station": "B", "x": 0.5, "y": 0.5}]
    out = zonemap.apply_calibration(markers, {"B": [0.9, 0.9]})
    assert out[0]["x"] == 0.1 and out[1]["x"] == 0.9


def test_all_map_stations_have_location_hint():
    data = json.load(open("data/map_settlements.json", encoding="utf-8"))
    for st, info in data["stations"].items():
        if info.get("offmap"):
            continue
        assert info.get("location"), f"no location hint: {st}"
        assert 0.0 <= info["x"] <= 1.0 and 0.0 <= info["y"] <= 1.0


def test_ensure_background_stitches(tmp_path):
    pil = pytest.importorskip("PIL")
    from PIL import Image
    Image.new("RGB", (60, 40), "red").save(tmp_path / "north-zone.png")
    Image.new("RGB", (60, 40), "blue").save(tmp_path / "south-zone.png")
    out = zonemap.ensure_background(tmp_path)
    assert out is not None and out.exists()
    img = Image.open(out)
    assert img.size == (60, 80)
    assert img.getpixel((30, 10))[0] > 200  # north on top
    assert img.getpixel((30, 70))[2] > 200


import pytest  # noqa: E402  (after test using it at runtime is fine)
