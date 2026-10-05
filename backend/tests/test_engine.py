"""Procedural puzzle engine: pieces, slots, joints and assembly validity on real geometry."""

import re

import numpy as np
import pytest
from shapely.geometry import LineString

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.engine import PuzzleGenerator
from app.geometry.labels import short_code
from app.geometry.normalize import normalize_silhouette
from app.geometry.sections import strip_intervals
from app.geometry.types import Complexity, Orientation, PieceKind, PieceRole, PuzzleParameters
from app.validators.geometry_validator import validate_model
from tests.conftest import build_model
from tests.fixtures import ALL_MASKS, animal_mask

SHAPES = ["circle", "rectangle", "animal", "irregular", "thin_parts", "concave"]
CONFIGS = [(3.0, 6, "easy", 250.0), (3.0, 12, "medium", 250.0), (3.0, 20, "advanced", 250.0),
           (4.0, 12, "medium", 250.0), (6.0, 6, "easy", 250.0), (6.0, 12, "medium", 300.0),
           (4.0, 20, "advanced", 300.0), (6.0, 20, "advanced", 400.0)]
ID_PATTERN = re.compile(r"^(LONG|CROSS|DETAIL-[A-Z]+)-\d{2}$")


def slot_gap(poly, slot, a):
    """Measured empty width across a slot at distance `a` from its bottom (None if no walls)."""
    dx, dy = slot.direction
    nx, ny = -dy, dx
    ox, oy = slot.origin[0] + dx * a, slot.origin[1] + dy * a
    span = slot.width_mm * 4
    line = LineString([(ox - nx * span, oy - ny * span), (ox + nx * span, oy + ny * span)])
    inside = poly.intersection(line)
    ts = []
    for g in getattr(inside, "geoms", [inside]):
        if g.is_empty:
            continue
        coords = np.asarray(g.coords)
        proj = (coords[:, 0] - ox) * nx + (coords[:, 1] - oy) * ny
        ts.append((proj.min(), proj.max()))
    left = [b for a_, b in ts if b <= 1e-9]
    right = [a_ for a_, b in ts if a_ >= -1e-9]
    if not left or not right:
        return None
    return min(right) - max(left)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("t,n,cx,size", CONFIGS)
def test_generated_puzzle_is_valid(shape, t, n, cx, size):
    model = build_model(shape, t, n, cx, size)
    report = validate_model(model)
    assert report.ok, report.errors
    assert len(model.pieces) == n
    ids = [p.id for p in model.pieces]
    assert len(set(ids)) == n and all(ID_PATTERN.match(i) for i in ids), ids
    assert model.pieces[0].id == "LONG-01" and model.pieces[0].role == PieceRole.SPINE
    crosses = [p for p in model.pieces if p.kind == PieceKind.CROSS]
    plates = [p for p in model.pieces if p.kind == PieceKind.PROFILE and p.role != PieceRole.SPINE]
    assert len(plates) % 2 == 0 and len(crosses) >= 2
    assert report.checks["intersections_checked"] >= len(crosses)
    for pc in model.pieces:
        assert pc.polygon.is_valid and pc.polygon.geom_type == "Polygon"
        assert pc.slots, f"{pc.id} has no slots"


@pytest.mark.parametrize("shape", ["animal", "circle", "concave"])
@pytest.mark.parametrize("t", [3.0, 4.0, 6.0])
def test_slot_widths_are_exactly_nominal(shape, t):
    model = build_model(shape, t, 12, "medium", 300.0)
    nominal = t + model.params.clearance_mm
    bearing = {j.id for j in model.joints if j.load_bearing}
    measured = []
    for pc in model.pieces:
        for slot in pc.slots:
            assert slot.width_mm == pytest.approx(nominal)
            if slot.joint_id not in bearing:
                continue
            gap = slot_gap(pc.polygon, slot, min(model.params.min_engagement_mm / 2, slot.depth_mm / 4))
            if gap is not None:
                measured.append(gap)
    assert len(measured) >= 0.9 * 2 * len(bearing)
    assert np.allclose(measured, nominal, atol=3e-3), (min(measured), max(measured))


def test_slots_meet_halfway_and_open_towards_each_other():
    model = build_model("animal", 3.0, 12, "medium")
    for j in model.joints:
        cross = model.piece(j.cross_id)
        prof_slot = next((s for s in model.piece(j.profile_id).slots if s.joint_id == j.id), None)
        cross_slot = next((s for s in cross.slots if s.joint_id == j.id), None)
        assert j.t_low < j.t_split < j.t_high
        if prof_slot is None or cross_slot is None:
            assert not j.load_bearing  # clearance notch whose sliver was removed
            continue
        px, py = cross.frame.point(j.t_split)
        assert prof_slot.origin == pytest.approx((px, py), abs=1e-6)
        assert cross_slot.origin == pytest.approx((j.z_mm, j.t_split), abs=1e-6)
        # Profile slot opens along +-d towards the insertion side; the rib's slot the other way.
        s = cross.slide_sign
        assert prof_slot.direction == pytest.approx((s * cross.frame.dx, s * cross.frame.dy))
        assert cross_slot.direction == pytest.approx((0.0, -s))


def test_pieces_never_interpenetrate_in_3d():
    """Direct check inside every intersection box (independent of the validator)."""
    model = build_model("animal", 4.0, 12, "medium")
    t = model.params.thickness_mm
    for cross in (p for p in model.pieces if p.kind == PieceKind.CROSS):
        for prof in (p for p in model.pieces if p.kind == PieceKind.PROFILE):
            yl = strip_intervals(cross.frame.to_local(prof.polygon), -t / 2, t / 2)
            yt = strip_intervals(cross.polygon, prof.position_mm - t / 2, prof.position_mm + t / 2)
            for a in yl:
                for b in yt:
                    assert min(a[1], b[1]) - max(a[0], b[0]) <= 1e-3, (prof.id, cross.id)


def test_spine_is_the_silhouette_backbone():
    params = PuzzleParameters(thickness_mm=3.0, piece_count=12)
    sil = normalize_silhouette(animal_mask(), params.target_size_mm, params.min_feature_mm).polygon
    model = PuzzleGenerator(params).generate(sil)
    spine = model.pieces[0]
    assert spine.role == PieceRole.SPINE and spine.position_mm == 0
    assert sil.buffer(0.01).contains(spine.polygon)
    assert spine.polygon.area > 0.5 * sil.area


def test_side_plates_come_in_mirrored_pairs():
    model = build_model("animal", 3.0, 20, "advanced")
    plates = [p for p in model.pieces if p.kind == PieceKind.PROFILE and p.role != PieceRole.SPINE]
    assert plates
    zs = sorted(p.position_mm for p in plates)
    assert zs == pytest.approx(sorted(-z for z in zs))
    for p in plates:
        assert abs(p.position_mm) >= model.params.slot_width_mm + model.params.min_wall_mm - 1e-6


def test_cross_ribs_follow_depth_and_never_collide():
    model = build_model("animal", 3.0, 12, "medium")
    crosses = [p for p in model.pieces if p.kind == PieceKind.CROSS]
    depth = model.object_size_mm[2]
    for pc in crosses:
        minx, _, maxx, _ = pc.polygon.bounds
        assert minx == pytest.approx(-maxx, abs=0.6)  # symmetric about z = 0
        assert maxx - minx <= depth + 1e-6
    traces = []
    for pc in crosses:
        _, t0, _, t1 = pc.polygon.bounds
        traces.append(LineString([pc.frame.point(t0), pc.frame.point(t1)]))
    for i in range(len(traces)):
        for j in range(i + 1, len(traces)):
            assert traces[i].distance(traces[j]) >= model.params.slot_width_mm + model.params.min_wall_mm - 1e-6


def test_transforms_place_pieces_in_their_planes():
    model = build_model("animal", 3.0, 12, "medium")
    for pc in model.pieces:
        t = pc.transform
        assert np.dot(t.u_axis, t.v_axis) == pytest.approx(0.0)
        assert np.linalg.norm(t.u_axis) == pytest.approx(1.0) and np.linalg.norm(t.v_axis) == pytest.approx(1.0)
        assert np.allclose(np.cross(t.u_axis, t.v_axis), t.normal)
    w, h, d = model.object_size_mm
    assert h > 0 and w > 0 and d > 0
    assert max(w, h) == pytest.approx(250.0, abs=1.0)


def test_orientation_override():
    params = PuzzleParameters(piece_count=8, orientation=Orientation.HORIZONTAL)
    sil = normalize_silhouette(ALL_MASKS["animal"](), 250.0, params.min_feature_mm).polygon
    model = PuzzleGenerator(params).generate(sil)
    assert model.orientation == Orientation.HORIZONTAL
    assert validate_model(model).ok


def test_generation_is_deterministic():
    params = PuzzleParameters(piece_count=14, complexity=Complexity.ADVANCED)
    sil = normalize_silhouette(animal_mask(), 250.0, params.min_feature_mm).polygon
    a = PuzzleGenerator(params).generate(sil)
    b = PuzzleGenerator(params).generate(sil)
    assert [p.id for p in a.pieces] == [p.id for p in b.pieces]
    assert [p.polygon.wkt for p in a.pieces] == [p.polygon.wkt for p in b.pieces]


def test_too_many_pieces_reports_clear_error():
    params = PuzzleParameters(thickness_mm=6.0, piece_count=40, target_size_mm=120.0, complexity=Complexity.EASY)
    sil = normalize_silhouette(animal_mask(), 120.0, params.min_feature_mm).polygon
    with pytest.raises(PuzzleError) as e:
        PuzzleGenerator(params).generate(sil)
    assert e.value.code == ErrorCode.TOO_MANY_PIECES
    assert "at most" in e.value.message


@pytest.mark.parametrize(
    "kwargs",
    [{"thickness_mm": 0.1}, {"kerf_mm": 5.0}, {"piece_count": 2}, {"target_size_mm": 10.0},
     {"clearance_mm": -1.0}, {"part_spacing_mm": 0.01, "kerf_mm": 0.2}],
)
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(PuzzleError) as e:
        PuzzleGenerator(PuzzleParameters(**kwargs))
    assert e.value.code == ErrorCode.INVALID_SETTINGS


def test_labels_are_engraving_inside_pieces():
    model = build_model("animal", 3.0, 12, "medium")
    labelled = [p for p in model.pieces if p.engrave]
    assert len(labelled) >= 0.75 * len(model.pieces)
    for pc in labelled:
        assert pc.label in (pc.id, short_code(pc.id))
        assert 2.0 <= pc.label_height_mm <= 8.0
        for stroke in pc.engrave:
            assert pc.polygon.contains(LineString(stroke))


def test_labels_can_be_disabled():
    params = PuzzleParameters(piece_count=8, labels=False)
    sil = normalize_silhouette(animal_mask(), 250.0, params.min_feature_mm).polygon
    assert all(not p.engrave for p in PuzzleGenerator(params).generate(sil).pieces)


def test_short_codes():
    assert short_code("LONG-01") == "L01"
    assert short_code("CROSS-07") == "C07"
    assert short_code("DETAIL-HEAD-01") == "DH01"
