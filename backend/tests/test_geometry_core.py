"""Normalization, depth model, slot sizing and kerf compensation."""

import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.depth import HeuristicDepthModel
from app.geometry.normalize import mask_to_polygon, normalize_silhouette
from app.geometry.sections import core_intervals, intersect, line_intervals, measure, strip_intervals
from app.geometry.slots import (
    compensate_kerf,
    nominal_slot_width,
    slot_cutter,
    slot_split,
    toolpath_slot_width,
)
from app.geometry.types import PuzzleParameters
from tests.fixtures import ALL_MASKS, animal_mask, circle_mask, concave_mask, rectangle_mask, thin_parts_mask

# ---------------------------------------------------------------- normalization


def test_mask_to_polygon_rectangle_dimensions():
    poly = mask_to_polygon(rectangle_mask())
    minx, miny, maxx, maxy = poly.bounds
    assert maxx - minx == pytest.approx(392, abs=1.5)
    assert maxy - miny == pytest.approx(192, abs=1.5)
    assert poly.area == pytest.approx(393 * 193, rel=0.01)


def test_mask_to_polygon_flips_y_up():
    m = np.zeros((100, 100), np.uint8)
    m[0:20, 40:60] = 255  # object at the TOP of the image
    poly = mask_to_polygon(m)
    assert poly.centroid.y > 50


def test_mask_to_polygon_keeps_holes():
    import cv2

    m = circle_mask().copy()
    cv2.circle(m, (256, 256), 60, 0, -1)
    assert len(mask_to_polygon(m).interiors) == 1


def test_empty_mask_raises():
    with pytest.raises(PuzzleError) as e:
        mask_to_polygon(np.zeros((50, 50), np.uint8))
    assert e.value.code == ErrorCode.OBJECT_NOT_DETECTED


@pytest.mark.parametrize("name", list(ALL_MASKS))
@pytest.mark.parametrize("size", [150.0, 400.0])
def test_normalization_frame(name, size):
    sil = normalize_silhouette(ALL_MASKS[name](), size, 2.5)
    minx, miny, maxx, maxy = sil.polygon.bounds
    assert max(maxx - minx, maxy - miny) == pytest.approx(size, abs=0.01)
    assert miny == pytest.approx(0.0, abs=0.01)
    assert (minx + maxx) / 2 == pytest.approx(0.0, abs=0.01)
    assert sil.polygon.is_valid and sil.polygon.geom_type == "Polygon"
    assert sil.polygon.exterior.is_ccw


def test_aspect_ratio_preserved():
    sil = normalize_silhouette(rectangle_mask(), 250.0, 2.5)
    assert sil.width_mm / sil.height_mm == pytest.approx(393 / 193, rel=0.02)


def test_thin_parts_are_reinforced_with_warning():
    sil = normalize_silhouette(thin_parts_mask(), 250.0, 2.5)
    # The antennae (≈1 mm wide) are kept but rebuilt at the 2.5 mm minimum cuttable width.
    assert sil.reinforced_features >= 1
    assert any("reinforced" in w for w in sil.warnings)
    assert sil.polygon.bounds[3] > 0.8 * 250 * (512 - 40) / 512 * 0.5  # antennae still reach upwards
    opened = sil.polygon.buffer(-1.24).buffer(1.24)
    assert opened.area == pytest.approx(sil.polygon.area, rel=0.01)  # nothing thinner than 2.5 mm


def test_hairlines_are_dropped():
    m = rectangle_mask().copy()
    import cv2

    cv2.line(m, (100, 150), (100, 60), 255, 1)  # 1 px hairline ≈ 0.5 mm: below 35% of the minimum
    sil = normalize_silhouette(m, 250.0, 2.5)
    assert sil.reinforced_features == 0
    assert sil.polygon.bounds[3] == pytest.approx(sil.height_mm, abs=0.01)


def test_contour_is_simplified_but_faithful():
    raw = mask_to_polygon(circle_mask())
    sil = normalize_silhouette(circle_mask(), 250.0, 2.5)
    assert len(sil.polygon.exterior.coords) < len(raw.exterior.coords) / 2
    expected = np.pi * 125.0**2
    assert sil.polygon.area == pytest.approx(expected, rel=0.01)


def test_too_thin_object_rejected():
    m = np.zeros((512, 512), np.uint8)
    m[250:253, 10:500] = 255  # a hairline
    with pytest.raises(PuzzleError) as e:
        normalize_silhouette(m, 100.0, 4.0)
    assert e.value.code == ErrorCode.INVALID_GEOMETRY


# ---------------------------------------------------------------- depth model


@pytest.fixture(scope="module")
def circle_depth():
    sil = normalize_silhouette(circle_mask(), 200.0, 2.5).polygon
    return sil, HeuristicDepthModel().estimate(sil)


def test_depth_zero_outside_positive_inside(circle_depth):
    sil, depth = circle_depth
    assert depth.sample(0.0, 100.0) > 10.0
    assert depth.sample(150.0, 100.0) == 0.0
    assert depth.sample(0.0, -20.0) == 0.0
    assert depth.values.min() >= 0.0


def test_depth_peaks_at_centre_and_is_symmetric(circle_depth):
    _, depth = circle_depth
    centre = float(depth.sample(0.0, 100.0))
    assert centre == pytest.approx(depth.max_half_depth, rel=0.05)
    for dx in (20.0, 50.0, 80.0):
        assert depth.sample(-dx, 100.0) == pytest.approx(depth.sample(dx, 100.0), rel=0.05, abs=0.3)
        assert depth.sample(dx, 100.0) < centre


def test_depth_capped_by_ratio(circle_depth):
    _, depth = circle_depth
    assert 2 * depth.max_half_depth <= 0.42 * 200.0 + 1e-6


def test_wide_body_is_thicker_than_legs():
    sil = normalize_silhouette(animal_mask(), 250.0, 2.5).polygon
    depth = HeuristicDepthModel().estimate(sil)
    minx, miny, maxx, maxy = sil.bounds
    body = float(depth.values.max())
    # Sample along a leg: the lowest 15% of the silhouette.
    ys = np.linspace(miny + 2, miny + 0.15 * (maxy - miny), 20)
    xs = np.linspace(minx, maxx, 400)
    leg = max(float(depth.sample(np.full_like(ys, x), ys).max()) for x in xs)
    assert 0 < leg < 0.6 * body


def test_depth_level_polygon_is_inside_silhouette(circle_depth):
    sil, depth = circle_depth
    region = depth.level_polygon(depth.max_half_depth * 0.5)
    assert region is not None and region.area < sil.area
    assert sil.buffer(1.0).contains(region)


# ---------------------------------------------------------------- slots and kerf


@pytest.mark.parametrize("t,c,expected", [(3.0, 0.1, 3.1), (4.0, 0.1, 4.1), (6.0, 0.1, 6.1), (3.0, 0.0, 3.0),
                                          (9.0, 0.25, 9.25), (3.175, 0.05, 3.225)])
def test_nominal_slot_width(t, c, expected):
    assert nominal_slot_width(t, c) == pytest.approx(expected)
    assert PuzzleParameters(thickness_mm=t, clearance_mm=c).slot_width_mm == pytest.approx(expected)


@pytest.mark.parametrize("t,c,k,expected", [(3.0, 0.1, 0.15, 2.95), (6.0, 0.1, 0.2, 5.9), (4.0, 0.0, 0.0, 4.0)])
def test_toolpath_slot_width(t, c, k, expected):
    assert toolpath_slot_width(t, c, k) == pytest.approx(expected)


def test_invalid_slot_parameters():
    with pytest.raises(PuzzleError):
        nominal_slot_width(0, 0.1)
    with pytest.raises(PuzzleError):
        nominal_slot_width(3, -0.1)
    with pytest.raises(PuzzleError):
        toolpath_slot_width(3, 0.1, 3.2)
    with pytest.raises(PuzzleError):
        compensate_kerf(box(0, 0, 10, 10), -0.1)


def test_slot_split_and_cutter():
    assert slot_split(10.0, 30.0) == 20.0
    with pytest.raises(ValueError):
        slot_split(5.0, 5.0)
    up = slot_cutter(50.0, 3.1, 20.0, opens_up=True)
    assert up.bounds[0] == pytest.approx(48.45) and up.bounds[2] == pytest.approx(51.55)
    assert up.bounds[1] == 20.0 and up.bounds[3] > 1000
    down = slot_cutter(0.0, 3.1, 20.0, opens_up=False)
    assert down.bounds[3] == 20.0 and down.bounds[1] < -1000


def _gap_width(poly: Polygon, x: float, y: float, span: float = 20.0) -> float:
    """Width of the empty gap around x on the horizontal line at height y."""
    line = LineString([(x - span, y), (x + span, y)])
    inside = poly.intersection(line)
    segs = sorted((g.bounds[0], g.bounds[2]) for g in getattr(inside, "geoms", [inside]) if not g.is_empty)
    left = max(b for a, b in segs if b <= x)
    right = min(a for a, b in segs if a >= x)
    return right - left


@pytest.mark.parametrize("kerf", [0.0, 0.1, 0.15, 0.3])
def test_kerf_compensation_is_exact(kerf):
    """A 100x50 plate with a 3.1 mm slot: the tool path must grow outward and narrow the slot by kerf."""
    plate = box(0, 0, 100, 50).difference(slot_cutter(50.0, 3.1, 25.0, opens_up=True))
    tool = compensate_kerf(plate, kerf)
    minx, miny, maxx, maxy = tool.bounds
    assert maxx - minx == pytest.approx(100 + kerf, abs=1e-6)
    assert maxy - miny == pytest.approx(50 + kerf, abs=1e-6)
    assert _gap_width(plate, 50.0, 40.0) == pytest.approx(3.1, abs=1e-6)
    assert _gap_width(tool, 50.0, 40.0) == pytest.approx(3.1 - kerf, abs=1e-6)
    # Slot bottom moves up by kerf/2 (the laser removes it again).
    bottom = min(y for x, y in tool.exterior.coords if 48 < x < 52)
    assert bottom == pytest.approx(25.0 + kerf / 2, abs=1e-6)
    # Simulate the cut: removing kerf/2 of material everywhere restores the nominal part.
    cut = tool.buffer(-kerf / 2, join_style="mitre")
    assert cut.symmetric_difference(plate).area < 1e-6


# ---------------------------------------------------------------- sections


def test_section_helpers():
    shape = box(0, 0, 10, 10).union(box(0, 20, 10, 30))
    assert line_intervals(shape, 5.0) == [(0.0, 10.0), (20.0, 30.0)]
    assert strip_intervals(shape, 4.0, 6.0) == [(0.0, 10.0), (20.0, 30.0)]
    assert intersect([(0, 10), (20, 30)], [(5, 25)]) == [(5, 10), (20, 25)]
    assert measure([(0, 10), (20, 30)], 5, 25) == 10
    notch = box(0, 0, 10, 10).difference(box(4, 5, 6, 20))
    assert core_intervals(notch, 5.0, 2.0) == [(0.0, 5.0)]
    assert line_intervals(shape, 50.0) == []
