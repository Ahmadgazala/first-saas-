"""Regression tests for the detailed structural engine (anatomy, depth, adaptive ribs,
detail plates, leg placement, thin features, slot quality and preview/export consistency)."""

import io
import json
import re
import xml.etree.ElementTree as ET

import ezdxf
import numpy as np
import pytest
from shapely.geometry import Polygon

from app.exporters.dxf import export_dxf
from app.exporters.metadata import build_model_json
from app.exporters.sheet import build_sheet
from app.exporters.svg import export_svg
from app.geometry.anatomy import Region, analyze
from app.geometry.depth import AnatomicalDepthModel
from app.geometry.nesting import nest
from app.geometry.normalize import normalize_silhouette
from app.geometry.quality import analyze_quality
from app.geometry.slots import compensate_kerf
from app.geometry.types import PieceKind, PieceRole
from app.validators.geometry_validator import validate_layout, validate_model
from tests.animals import ANIMALS
from tests.conftest import build_animal

ANIMAL_NAMES = ["horse", "lion", "wolf", "deer", "elephant", "eagle", "logo", "irregular"]


def _norm(name, size=250.0):
    return normalize_silhouette(ANIMALS[name](), size, 2.5).polygon


# ------------------------------------------------------------------------------ anatomy


@pytest.mark.parametrize("name,expected", [
    ("horse", {Region.HEAD, Region.NECK, Region.LIMB, Region.TAIL, Region.CHEST}),
    ("wolf", {Region.HEAD, Region.LIMB, Region.TAIL}),
    ("deer", {Region.HEAD, Region.NECK, Region.LIMB, Region.EXTREMITY}),
    ("lion", {Region.LIMB, Region.TAIL, Region.CHEST}),
    ("elephant", {Region.LIMB, Region.TAIL, Region.EXTREMITY}),
])
def test_anatomy_finds_regions(name, expected):
    a = analyze(_norm(name))
    present = {Region(v) for v in np.unique(a.labels) if v}
    assert expected <= present, present
    assert a.head_side != 0


@pytest.mark.parametrize("name", ["logo", "irregular"])
def test_objects_are_not_treated_as_animals(name):
    a = analyze(_norm(name))
    present = {Region(v) for v in np.unique(a.labels) if v}
    assert not present & {Region.HEAD, Region.NECK, Region.LIMB, Region.TAIL}
    assert a.head_side == 0


def test_head_is_on_the_correct_side():
    assert analyze(_norm("horse")).head_side == 1  # drawn facing right
    assert analyze(_norm("elephant")).head_side == 1  # tail on the left, trunk on the right


# ------------------------------------------------------------------------------ depth model


@pytest.fixture(scope="module")
def horse_depth():
    sil = _norm("horse")
    a = analyze(sil)
    return a, AnatomicalDepthModel().estimate(sil, a)


def test_depth_follows_anatomy(horse_depth):
    a, d = horse_depth
    def peak(kind):
        return float(d.values[a.labels == kind].max())
    assert peak(Region.CHEST) > peak(Region.BELLY)
    assert peak(Region.TORSO) > peak(Region.LIMB)
    assert peak(Region.TORSO) > peak(Region.HEAD) > 0
    assert d.values[~a.mask].max() == 0.0


def test_tail_tapers(horse_depth):
    a, d = horse_depth
    tail = a.labels == Region.TAIL
    root = tail & (a.along < 0.3)
    tip = tail & (a.along > 0.7)
    assert d.values[root].mean() > d.values[tip].mean()


def test_depth_field_is_smooth(horse_depth):
    _, d = horse_depth
    gy, gx = np.gradient(d.values, d.res)
    # No discontinuities between regions: slope stays bounded (a round tube edge is the steepest part).
    assert float(np.percentile(np.hypot(gx, gy), 99)) < 4.0


# ------------------------------------------------------------------------------ structure


@pytest.mark.parametrize("name", ANIMAL_NAMES)
def test_every_example_generates_valid_geometry(name):
    model = build_animal(name, 3.0, 16, "medium")
    report = validate_model(model)
    assert report.ok, report.errors
    assert len(model.pieces) == 16


def test_more_detail_means_more_structure():
    easy, med, adv = (build_animal("horse", 3.0, 20, cx) for cx in ("easy", "medium", "advanced"))
    joints = [len(m.joints) for m in (easy, med, adv)]
    assert joints[0] < joints[1] < joints[2], joints
    plates = [sum(1 for p in m.pieces if p.kind == PieceKind.PROFILE) for m in (easy, med, adv)]
    assert plates[0] <= plates[1] < plates[2], plates
    assert any(p.role == PieceRole.DETAIL for p in adv.pieces)
    assert not any(p.role == PieceRole.DETAIL for p in easy.pieces)


def test_piece_count_drives_structure_not_slicing():
    small, mid, large = (build_animal("horse", 3.0, n, "advanced") for n in (6, 12, 20))
    assert [len(m.pieces) for m in (small, mid, large)] == [6, 12, 20]
    plates = [sum(1 for p in m.pieces if p.role != PieceRole.CROSS) for m in (small, mid, large)]
    assert plates[0] < plates[2]  # more pieces add longitudinal/detail structure, not only slices


def test_ribs_concentrate_on_head_and_neck():
    model = build_animal("horse", 3.0, 20, "advanced")
    crosses = [p for p in model.pieces if p.kind == PieceKind.CROSS]
    regions = [p.region for p in crosses]
    assert {"head", "neck"} & set(regions)
    a = analyze(_norm("horse"))
    area = {k: a.region_area(v) for k, v in {"front": (Region.HEAD, Region.NECK),
                                               "body": (Region.TORSO, Region.CHEST, Region.BELLY)}.items()}
    front = sum(r in ("head", "neck") for r in regions) / area["front"]
    body = sum(r in ("torso", "chest", "belly") for r in regions) / area["body"]
    assert front > body  # higher rib density per area where the shape is detailed


def test_tilted_ribs_follow_necks_and_tails():
    model = build_animal("wolf", 3.0, 12, "medium")
    tilted = [p for p in model.pieces if p.kind == PieceKind.CROSS and abs(p.frame.angle_deg) > 1]
    assert tilted, "expected ribs perpendicular to the tail or neck"
    assert all(p.region in ("tail", "neck", "head", "extremity") for p in tilted)


def test_head_detail_plates():
    model = build_animal("horse", 3.0, 20, "advanced")
    details = [p for p in model.pieces if p.role == PieceRole.DETAIL]
    assert any(p.id.startswith("DETAIL-") for p in details)
    assert any(p.region in ("head", "neck") for p in details)


def test_legs_move_to_the_side_profiles():
    model = build_animal("horse", 3.0, 12, "medium")
    assert model.analysis.get("legs_on_side_profiles")
    spine = model.pieces[0]
    sides = [p for p in model.pieces if p.role == PieceRole.PROFILE]
    h = model.object_size_mm[1]
    # The spine ends at the belly; both side profiles stand on the ground with legs.
    assert spine.polygon.bounds[1] > 0.2 * h
    # Profile pieces share the canonical xy frame (cross ribs use their own local frame).
    ground = min(p.polygon.bounds[1] for p in model.pieces if p.kind == PieceKind.PROFILE)
    assert len(sides) == 2 and all(p.polygon.bounds[1] - ground < 1.0 for p in sides)
    assert {np.sign(p.position_mm) for p in sides} == {1.0, -1.0}


def test_thin_features_are_reinforced_not_lost():
    sil = normalize_silhouette(ANIMALS["deer"](), 150.0, 2.5)
    assert sil.reinforced_features >= 1
    assert any("reinforced" in w for w in sil.warnings)
    opened = sil.polygon.buffer(-1.24).buffer(1.24)
    assert opened.area == pytest.approx(sil.polygon.area, rel=0.01)  # nothing thinner than 2.5 mm left


@pytest.mark.parametrize("name", ["horse", "deer", "lion", "logo"])
def test_slot_quality(name):
    model = build_animal(name, 3.0, 20, "advanced")
    q = analyze_quality(model)
    req = model.params.min_feature_mm
    assert q["min_feature_mm"] >= req - 0.1
    assert q["min_slot_wall_mm"] is None or q["min_slot_wall_mm"] >= req - 0.05
    assert q["min_slot_gap_mm"] is None or q["min_slot_gap_mm"] > 0
    assert q["slot_count"] == sum(len(p.slots) for p in model.pieces)


@pytest.mark.parametrize("name", ["horse", "elephant", "eagle"])
def test_nested_animals_fit_without_overlap(name):
    model = build_animal(name, 3.0, 20, "advanced")
    tool = {p.id: compensate_kerf(p.polygon, model.params.kerf_mm) for p in model.pieces}
    layout = nest(tool, 1200, 600, 5, 3)
    report = validate_layout({pl.piece_id: pl.polygon for pl in layout.placements}, 1200, 600, 5, 3)
    assert report.ok, report.errors
    q = analyze_quality(model, layout)
    assert q["min_cut_distance_mm"] >= 3.0 - 1e-3
    assert 0 < q["nesting_efficiency_percent"] <= 100


# ------------------------------------------------------------------------------ preview == exports


@pytest.fixture(scope="module")
def horse_job():
    model = build_animal("horse", 3.0, 20, "advanced")
    tool = {p.id: compensate_kerf(p.polygon, model.params.kerf_mm) for p in model.pieces}
    layout = nest(tool, 600, 400, 5, 3)
    return model, layout, build_sheet(model, layout)


def test_preview_uses_the_exact_generated_pieces(horse_job):
    model, layout, _ = horse_job
    data = json.loads(json.dumps(build_model_json(model, layout)))
    assert [p["id"] for p in data["pieces"]] == [p.id for p in model.pieces]
    for entry, pc in zip(data["pieces"], model.pieces):
        assert Polygon(entry["outer"], entry["holes"]).hausdorff_distance(pc.polygon) < 1e-3
        assert entry["slots"] == len(pc.slots)
        assert set(entry["connected_to"]) == {j.cross_id if j.profile_id == pc.id else j.profile_id
                                              for j in model.joints if pc.id in (j.profile_id, j.cross_id)}
        assert entry["sheet"]["rotation_deg"] in (0, 90, 180, 270)


def test_svg_matches_preview_geometry(horse_job):
    """Each SVG cut path is the preview outline offset by kerf/2 and placed on the sheet."""
    model, layout, sheet = horse_job
    data = build_model_json(model, layout)
    placements = {pl.piece_id: pl for pl in layout.placements}
    root = ET.fromstring(export_svg(sheet))
    ns = {"s": "http://www.w3.org/2000/svg"}
    paths = {p.get("id").removesuffix("-cut"): p.get("d") for p in root.find("s:g[@id='CUT']", ns)}
    from app.geometry.nesting import place_polygon

    for entry in data["pieces"]:
        nominal = Polygon(entry["outer"], entry["holes"])
        pl = placements[entry["id"]]
        expected = place_polygon(compensate_kerf(nominal, model.params.kerf_mm), pl.rotation_deg, pl.dx, pl.dy)
        rings = [[(float(a), 400 - float(b)) for a, b in re.findall(r"(-?[\d.]+) (-?[\d.]+)", sub)]
                 for sub in re.findall(r"M[^M]+", paths[entry["id"]])]
        svg_poly = Polygon(rings[0], rings[1:])
        assert svg_poly.hausdorff_distance(expected) < 5e-3


def test_dxf_matches_svg_geometry(horse_job, tmp_path):
    _, _, sheet = horse_job
    root = ET.fromstring(export_svg(sheet))
    ns = {"s": "http://www.w3.org/2000/svg"}
    svg_rings = []
    for p in root.find("s:g[@id='CUT']", ns):
        for sub in re.findall(r"M[^M]+", p.get("d")):
            svg_rings.append(Polygon([(float(a), 400 - float(b)) for a, b in re.findall(r"(-?[\d.]+) (-?[\d.]+)", sub)]))
    path = tmp_path / "s.dxf"
    path.write_bytes(export_dxf(sheet))
    dxf_rings = [Polygon([(q[0], q[1]) for q in e.get_points("xy")])
                 for e in ezdxf.readfile(path).modelspace().query("LWPOLYLINE[layer=='CUT']")]
    assert len(dxf_rings) == len(svg_rings)
    for a in svg_rings:
        best = min(dxf_rings, key=lambda b: a.centroid.distance(b.centroid))
        assert a.hausdorff_distance(best) < 2e-3
