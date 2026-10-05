"""Correctness of the fidelity harness itself (metrics on known shapes, frame recovery, and
proof that instrumentation does not change production output).

    .venv/bin/python -m pytest fidelity/tests -q -p no:warnings
"""

import numpy as np
import pytest
from shapely.geometry import box

from fidelity import assembly as A
from fidelity import metrics as M


def test_iou_and_area_metrics():
    a = box(0, 0, 10, 10)
    assert M.iou(a, a) == pytest.approx(1.0)
    assert M.iou(a, box(5, 0, 15, 10)) == pytest.approx(50 / 150)
    m = M.silhouette_metrics(a, box(0, 0, 10, 5), size_mm=100)
    assert m["recall_area"] == pytest.approx(0.5) and m["precision_area"] == pytest.approx(1.0)


def test_boundary_f_and_distances():
    a = box(0, 0, 100, 50)
    assert M.boundary_f(a, a, tau=2.0)["f"] == pytest.approx(1.0)
    shifted = box(10, 0, 110, 50)
    low = M.boundary_f(a, shifted, tau=2.0)["f"]
    assert low < 0.75
    d = M.boundary_dist(a, shifted, step=0.25)
    assert d["hausdorff_mm"] == pytest.approx(10.0, abs=0.3)


def test_envelope_bridges_gaps_only():
    comb = M.as_area(box(0, 0, 100, 4).union(box(0, 0, 3, 40)).union(box(10, 0, 13, 40)))
    env = M.envelope(comb, 6.0)
    assert env.area > comb.area  # the 7 mm gap between the teeth is bridged
    assert M.envelope(box(0, 0, 10, 10), 6.0).area == pytest.approx(100, rel=1e-3)


def test_plate_and_cross_slab_projections():
    poly = box(0, 0, 40, 20)
    plate = A.plate_slab(poly, z=5.0, T=3.0)
    assert M.iou(A.project_slab(plate, "side"), poly) == pytest.approx(1.0)
    front = A.project_slab(plate, "front")  # edge-on: z in [3.5, 6.5], y in [0, 20]
    assert front.bounds == pytest.approx((3.5, 0.0, 6.5, 20.0))
    from app.geometry.types import RibFrame

    cross = A.cross_slab(box(-15, -10, 15, 30), RibFrame(50.0, 0.0, 0.0, 1.0), 3.0)
    side = A.project_slab(cross, "side")  # a vertical strip of width T at x = 50
    assert side.bounds == pytest.approx((48.5, -10.0, 51.5, 30.0))
    assert A.project_slab(cross, "front").area == pytest.approx(30 * 40)


def test_voxelize_and_surface_distances():
    plate = A.plate_slab(box(0, 0, 60, 40), z=0.0, T=4.0)
    g = A.grid_for([A.slab_bounds(plate)], res=1.0)
    occ = A.voxelize([plate], g)
    assert occ.sum() == pytest.approx(60 * 40 * 4, rel=0.08)
    d = M.surface_distances(occ, occ, 1.0)
    assert d["completeness_mean_mm"] == 0 and d["coverage_3mm"] == 1.0 and d["chamfer_mm"] == 0
    assert M.volume_iou(occ, occ) == 1.0


def test_traced_normalization_matches_production():
    from fidelity.stages import traced_normalize
    from tests.animals import ANIMALS

    for name in ("horse", "trex", "deer"):
        tr = traced_normalize(ANIMALS[name](), 250.0, 2.5)
        assert tr.production_equal
        # px -> mm maps the kept component onto the final polygon's frame
        mapped = tr.px_to_mm.apply(tr.largest_px)
        assert M.iou(mapped, tr.final) > 0.97


def test_instrumented_kit_is_identical_to_production():
    from app.geometry.kit.builder import KitGenerator
    from app.geometry.normalize import normalize_silhouette
    from app.geometry.types import Complexity, PuzzleParameters, Style
    from fidelity.instrument import InstrumentedKit
    from tests.animals import ANIMALS

    p = PuzzleParameters(thickness_mm=3, piece_count=30, complexity=Complexity.ADVANCED, target_size_mm=250,
                         style=Style.KIT)
    sil = normalize_silhouette(ANIMALS["trex"](), 250, p.min_feature_mm).polygon
    a = KitGenerator(p).generate(sil)
    gen = InstrumentedKit(p)
    b = gen.generate(sil)
    assert [pc.id for pc in a.pieces] == [pc.id for pc in b.pieces]
    for x, y in zip(a.pieces, b.pieces):
        assert x.polygon.equals(y.polygon) and x.transform == y.transform
    deg = gen.degradation()
    assert deg["ribs_placed"] == sum(pc.role.value == "rib" for pc in b.pieces)
    assert deg["rib_attempts"] >= deg["ribs_placed"]


def test_frame_recovery_puts_the_kit_on_its_input_silhouette():
    from app.geometry.kit.builder import KitGenerator
    from app.geometry.normalize import normalize_silhouette
    from app.geometry.types import Complexity, PuzzleParameters, Style
    from tests.animals import ANIMALS

    p = PuzzleParameters(thickness_mm=3, piece_count=30, complexity=Complexity.ADVANCED, target_size_mm=250,
                         style=Style.KIT)
    sil = normalize_silhouette(ANIMALS["horse"](), 250, p.min_feature_mm).polygon
    model = KitGenerator(p).generate(sil)
    spine = [s for s in A.model_slabs(model) if s.role == "spine"]
    side = A.project(spine, "side")
    # the backbone lies (almost) entirely inside the silhouette it was built from
    assert M.as_area(side).intersection(sil).area / M.as_area(side).area > 0.95


def test_upload_ground_truth_is_in_the_generator_frame():
    """Regression: a stale shell cache once put the GT mesh in the wrong frame (IoU 0.30)."""
    from fidelity.cases import gt_meshes
    from fidelity.run import mesh_vs_mesh, upload_path

    gen, gt = upload_path(gt_meshes()[0])
    r = mesh_vs_mesh(gt, gen, 1.0)
    assert r["volume_iou"] > 0.9 and r["chamfer_mm"] < 1.0


def test_parts_recurses_into_nested_collections():
    """Regression: a GeometryCollection holding a MultiPolygon (deer mask) became empty."""
    from shapely.geometry import GeometryCollection, LineString, MultiPolygon

    gc = GeometryCollection([MultiPolygon([box(0, 0, 1, 1), box(2, 0, 3, 1)]), LineString([(0, 0), (1, 1)])])
    assert len(M.parts(gc)) == 2 and M.as_area(gc).area == pytest.approx(2.0)
