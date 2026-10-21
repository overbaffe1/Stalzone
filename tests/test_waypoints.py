"""Waypoint export: affine fit, markers, merge/strip, cfg roundtrip."""
from szmarket import waypoints as wp


def test_affine_fit_and_apply():
    aff = wp.fit_affine((0.0, 0.0), {"x": 100.0, "y": 70.0, "z": 200.0},
                        (1.0, 1.0), {"x": 300.0, "y": 80.0, "z": 600.0})
    assert aff is not None
    assert wp.apply_affine(aff, 0.0, 0.0) == (100.0, 200.0)
    assert wp.apply_affine(aff, 1.0, 1.0) == (300.0, 600.0)
    assert wp.apply_affine(aff, 0.5, 0.5) == (200.0, 400.0)
    assert wp.fit_affine((0.5, 0.5), {"x": 1, "y": 1, "z": 1},
                         (0.5, 0.5), {"x": 2, "y": 2, "z": 2}) is None


def test_marker_shape_and_route_build():
    aff = {"ax": 10.0, "bx": 0.0, "az": 20.0, "bz": 5.0, "ya": 70.0,
           "yb": 90.0}
    marks = wp.build_route_marks([("Start", 0.0, 0.0), ("Bar +1M", 1.0, 1.0)],
                                 aff)
    assert [m["name"] for m in marks] == ["SZ_S Start", "SZ_1 Bar +1M"]
    assert marks[0]["pos"] == {"x": 0.0, "y": 70.0, "z": 5.0}
    assert marks[1]["pos"]["x"] == 10.0
    assert marks[0]["color"] == wp.COLOR_START
    assert marks[1]["color"] == wp.COLOR_ROUTE
    assert marks[0]["type"] == "manual"


def test_merge_and_strip_keep_user_and_anchors():
    mine = [{"name": "my base"}, {"name": "SZ_A"}, {"name": "SZ_1 old"}]
    merged = wp.merge_marks(mine, [{"name": "SZ_1 new"}])
    assert [m["name"] for m in merged] == ["my base", "SZ_A", "SZ_1 new"]
    assert [m["name"] for m in wp.strip_marks(mine)] == ["my base", "SZ_A"]


def test_find_anchor_and_cfg_roundtrip(tmp_path):
    p = tmp_path / "waypoints.cfg"
    marks = [wp.make_marker("SZ_A", 1.0, 2.0, 3.0),
             {"name": "x", "pos": {}}]
    assert wp.save_waypoints(p, marks, backup=False) is None
    back = wp.load_waypoints(p)
    assert wp.find_anchor(back, "SZ_A") == {"x": 1.0, "y": 2.0, "z": 3.0}
    assert wp.find_anchor(back, "SZ_B") is None
    assert wp.load_waypoints(tmp_path / "nope.cfg") == []


def test_candidates_include_exbo_default():
    cands = [str(p) for p in wp.default_cfg_candidates()]
    assert any("EXBO" in c and c.endswith("waypoints.cfg") for c in cands)
