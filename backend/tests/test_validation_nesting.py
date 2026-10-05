"""Geometry validation/repair, collision detection and nesting."""

import copy

import pytest
from shapely import affinity
from shapely.geometry import Polygon, box

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.nesting import nest, place_polygon
from app.geometry.slots import compensate_kerf
from app.validators.geometry_validator import (
    repair_polygon,
    validate_assembly,
    validate_layout,
    validate_model,
    validate_polygon,
)
from tests.conftest import build_model

# ---------------------------------------------------------------- polygon validation


def test_valid_polygon_passes():
    assert validate_polygon(box(0, 0, 50, 20), "P") == []


def test_self_intersection_detected():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    errors = validate_polygon(bowtie, "P")
    assert any("invalid polygon" in e or "self-intersecting" in e for e in errors)


def test_zero_length_segment_detected():
    poly = Polygon([(0, 0), (10, 0), (10, 0.00001), (10, 10), (0, 10)])
    assert any("zero-length" in e for e in validate_polygon(poly, "P"))


def test_tiny_fragment_and_disconnected_detected():
    assert any("tiny fragment" in e for e in validate_polygon(box(0, 0, 0.5, 0.5), "P", min_area=1.0))
    multi = box(0, 0, 10, 10).union(box(20, 0, 30, 10))
    assert any("connected polygon" in e for e in validate_polygon(multi, "P"))


def test_duplicate_segment_detected():
    # Ring that walks the same edge twice (a zero-area spike).
    poly = Polygon([(0, 0), (10, 0), (10, 10), (5, 10), (5, 15), (5, 10), (0, 10)])
    errors = validate_polygon(poly, "P")
    assert errors


def test_repair_polygon_removes_duplicates_safely():
    poly = Polygon([(0, 0), (10, 0), (10, 0), (10, 10), (10.00001, 10), (0, 10)])
    fixed = repair_polygon(poly)
    assert fixed is not None and validate_polygon(fixed, "P") == []
    assert fixed.area == pytest.approx(100, rel=1e-3)
    assert repair_polygon(Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])) is None  # would change the shape


def test_obstructed_slot_detected():
    model = copy.deepcopy(build_model("animal", 3.0, 8, "easy"))
    piece = model.pieces[0]
    slot = piece.slots[0]
    piece.polygon = piece.polygon.union(slot.channel(inset=0.0, extra_width=-1.0))
    errors = validate_model(model).errors
    assert any("obstructed" in e for e in errors)
    assert any("interpenetrate" in e for e in errors)


def test_impossible_intersection_detected():
    model = copy.deepcopy(build_model("animal", 3.0, 8, "easy"))
    assert validate_assembly(model).ok
    cross = next(p for p in model.pieces if p.kind == "cross")
    cross.polygon = cross.polygon.buffer(20)  # fills its own slot: collides with the profile
    assert any("interpenetrate" in e for e in validate_assembly(model).errors)


def test_rib_collision_detected():
    from dataclasses import replace

    model = copy.deepcopy(build_model("animal", 3.0, 8, "easy"))
    crosses = [p for p in model.pieces if p.kind == "cross"]
    f = crosses[0].frame
    crosses[1].frame = replace(crosses[1].frame, cx=f.cx + 1.0, cy=f.cy)
    assert any("collide" in e for e in validate_assembly(model).errors)


def test_plate_collision_detected():
    model = copy.deepcopy(build_model("animal", 3.0, 12, "medium"))
    plates = [p for p in model.pieces if p.kind == "profile" and p.position_mm > 0]
    plates[0].position_mm = 1.0  # pushed against the spine
    assert any("collide" in e for e in validate_assembly(model).errors)


def test_label_outside_piece_detected():
    model = copy.deepcopy(build_model("animal", 3.0, 8, "easy"))
    model.pieces[0].engrave.append([(-1000.0, -1000.0), (-990.0, -1000.0)])
    assert any("engraving" in e for e in validate_model(model).errors)


# ---------------------------------------------------------------- collision / layout validation


def test_layout_overlap_and_bounds_detected():
    placed = {"A": box(10, 10, 60, 60), "B": box(50, 50, 100, 100), "C": box(590, 10, 620, 50)}
    errors = validate_layout(placed, 600, 400, 5, 3)
    assert any("overlap" in e for e in errors.errors)
    assert any("C is outside" in e for e in errors.errors)
    close = {"A": box(10, 10, 60, 60), "B": box(61, 10, 100, 60)}
    assert any("spacing" in e for e in validate_layout(close, 600, 400, 5, 3).errors)


# ---------------------------------------------------------------- nesting


@pytest.mark.parametrize("shape,t,n", [("animal", 3.0, 12), ("circle", 6.0, 6), ("concave", 4.0, 20)])
def test_nesting_is_valid(shape, t, n):
    model = build_model(shape, t, n, "advanced" if n >= 20 else "medium", 250.0)
    tool = {p.id: compensate_kerf(p.polygon, 0.15) for p in model.pieces}
    layout = nest(tool, 600, 400, 5.0, 3.0)
    assert len(layout.placements) == n
    placed = {pl.piece_id: pl.polygon for pl in layout.placements}
    report = validate_layout(placed, 600, 400, 5.0, 3.0)
    assert report.ok, report.errors
    for pl in layout.placements:
        # The placement is a rigid motion of the tool path: same area, same shape.
        again = place_polygon(tool[pl.piece_id], pl.rotation_deg, pl.dx, pl.dy)
        assert again.symmetric_difference(pl.polygon).area < 1e-6
        assert pl.rotation_deg in (0, 90, 180, 270)
    used = sum(p.area for p in tool.values())
    assert layout.used_area_mm2 == pytest.approx(used, rel=1e-6)
    assert layout.waste_percentage == pytest.approx(100 * (1 - used / (600 * 400)), abs=0.01)


def test_nesting_is_deterministic():
    model = build_model("animal", 3.0, 12, "medium")
    tool = {p.id: compensate_kerf(p.polygon, 0.15) for p in model.pieces}
    a = nest(tool, 600, 400, 5, 3)
    b = nest(tool, 600, 400, 5, 3)
    assert [(p.piece_id, p.rotation_deg, p.dx, p.dy) for p in a.placements] == [
        (p.piece_id, p.rotation_deg, p.dx, p.dy) for p in b.placements
    ]


def test_nesting_uses_rotation_to_fit():
    tall = box(0, 0, 20, 250)
    layout = nest({"A": tall}, 300, 60, 5, 3)
    assert layout.placements[0].rotation_deg in (90, 270)


def test_nesting_reports_when_pieces_do_not_fit():
    model = build_model("animal", 3.0, 12, "medium")
    tool = {p.id: compensate_kerf(p.polygon, 0.15) for p in model.pieces}
    with pytest.raises(PuzzleError) as e:
        nest(tool, 300, 300, 5, 3)
    assert e.value.code == ErrorCode.PIECES_DO_NOT_FIT
    assert e.value.message.startswith("These pieces do not fit on the selected sheet")


def test_nesting_with_holes_does_not_place_inside_hole():
    ring = box(0, 0, 100, 100).difference(box(20, 20, 80, 80))
    small = box(0, 0, 30, 30)
    layout = nest({"ring": ring, "small": small}, 140, 110, 2, 2)
    placed = {p.piece_id: p.polygon for p in layout.placements}
    assert validate_layout(placed, 140, 110, 2, 2).ok
    assert not affinity.scale(placed["ring"], 1, 1).intersects(placed["small"])
