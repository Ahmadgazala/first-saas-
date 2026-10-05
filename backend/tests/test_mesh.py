"""Real-3D path: mesh sections, repair, orientation, alignment and slicing a 3D model."""

import io

import numpy as np
import pytest
import trimesh

from app.core.errors import PuzzleError
from app.geometry.engine import PuzzleGenerator
from app.geometry.types import Complexity, PieceKind, PuzzleParameters, RibFrame
from app.geometry.volume import (
    AXIS_ROTATIONS,
    MeshVolume,
    align_to_silhouette,
    load_mesh_bytes,
    orient_uploaded_model,
    plane_section,
    prepare_mesh,
    remesh,
)
from app.reconstruction.service import MIN_DEPTH_RATIO, depth_ratio
from app.validators.geometry_validator import validate_model
from tests.meshes import quadruped


@pytest.fixture(scope="module")
def animal_mesh():
    return prepare_mesh(remesh(quadruped()), np.eye(3), 250.0)


def _frame_z(z):
    m = np.eye(4)
    m[2, 3] = z
    return m


def test_plane_section_of_box_is_exact():
    box = trimesh.creation.box(extents=[40, 20, 10])
    sec = plane_section(box, _frame_z(0.0))
    assert sec.area == pytest.approx(800.0, rel=1e-6)
    assert plane_section(box, _frame_z(50.0)) is None


def test_plane_section_keeps_holes_and_unions_overlaps():
    ring = trimesh.creation.annulus(r_min=5, r_max=10, height=4)
    sec = plane_section(ring, _frame_z(0.0))
    assert sec.area == pytest.approx(np.pi * (100 - 25), rel=0.02)
    assert len(sec.interiors) == 1
    a = trimesh.creation.box(extents=[20, 20, 4])
    b = trimesh.creation.box(extents=[20, 20, 4])
    b.apply_translation([10, 0, 0])
    sec = plane_section(trimesh.util.concatenate([a, b]), _frame_z(0.0))
    assert sec.area == pytest.approx(30 * 20, rel=1e-6)  # union, not even-odd cancellation


def test_remesh_makes_one_watertight_body():
    m = remesh(quadruped())
    assert m.is_watertight
    assert len(m.split(only_watertight=False)) == 1
    assert len(m.faces) <= 60_000


def test_mesh_volume_sections(animal_mesh):
    vol = MeshVolume(animal_mesh)
    lo, hi = animal_mesh.bounds
    assert max(hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx(250.0, abs=0.5)
    assert lo[1] == pytest.approx(0.0, abs=1e-6)
    spine = vol.spine_section(3.0)
    side = vol.profile_section(15.0, 3.0)
    assert spine is not None and side is not None
    # The slab section never pokes out of the body: it is inside both face sections.
    assert side.area <= vol.profile_section(15.0, 0.0).area + 1e-6
    rib = vol.cross_section(RibFrame(0.0, 0.0, 0.0, 1.0), 0.0, 200.0, 3.0)
    minx, miny, maxx, maxy = rib.bounds
    assert minx < -15 and maxx > 15  # real depth of the body in the rib plane
    assert vol.depth.max_half_depth > 10


def test_slicing_a_real_mesh_is_valid_and_keeps_the_legs(animal_mesh):
    p = PuzzleParameters(thickness_mm=3.0, piece_count=20, complexity=Complexity.ADVANCED, orientation="vertical")
    model = PuzzleGenerator(p).generate_from_mesh(animal_mesh)
    report = validate_model(model)
    assert report.ok, report.errors
    assert model.analysis["shape_source"] == "mesh"
    assert len(model.pieces) == 20
    # Legs stand away from the centre plane in a real model: the structure must still reach
    # the ground (legs come from cross ribs or side profiles cut through the real legs).
    assert model.object_size_mm[1] == pytest.approx(animal_mesh.extents[1], abs=3.0)
    assert model.object_size_mm[2] == pytest.approx(animal_mesh.extents[2], abs=4.0)
    crosses = [pc for pc in model.pieces if pc.kind == PieceKind.CROSS]
    assert any(pc.polygon.bounds[2] - pc.polygon.bounds[0] > 25 for pc in crosses)


@pytest.mark.parametrize("ext", ["stl", "obj", "glb", "ply"])
def test_load_uploaded_model_formats(ext):
    mesh = trimesh.creation.box(extents=[30, 60, 20])
    data = mesh.export(file_type=ext)
    data = data if isinstance(data, bytes) else data.encode()
    loaded = load_mesh_bytes(data, ext)
    assert len(loaded.faces) >= 12
    assert np.allclose(sorted(loaded.extents), [20, 30, 60], atol=1e-3)


def test_unreadable_model_is_rejected():
    with pytest.raises(PuzzleError):
        load_mesh_bytes(b"solid nothing\nendsolid", "stl")


def test_orientation_conventions():
    stl_like = trimesh.creation.box(extents=[20, 100, 40])  # z-up: long horizontal axis is y
    v = stl_like.vertices @ orient_uploaded_model(stl_like, "stl").T
    assert np.allclose(v.max(0) - v.min(0), [100, 40, 20], atol=1e-6)
    glb_like = trimesh.creation.box(extents=[30, 50, 120])  # y-up: long horizontal axis is z
    v = glb_like.vertices @ orient_uploaded_model(glb_like, "glb").T
    assert np.allclose(v.max(0) - v.min(0), [120, 50, 30], atol=1e-6)


def test_alignment_recovers_the_camera_view():
    """A reconstruction in an arbitrary axis convention is rotated back to the photo's view."""
    from app.vision.segmentation import mask_iou

    true = prepare_mesh(remesh(quadruped()), np.eye(3), 250.0)
    vol = MeshVolume(true)
    from app.geometry.volume import silhouette_mask_png

    photo_mask = silhouette_mask_png(vol)
    scrambled = true.copy()
    R = AXIS_ROTATIONS[7]
    T = np.eye(4)
    T[:3, :3] = R
    scrambled.apply_transform(T)
    rotation, iou = align_to_silhouette(scrambled, photo_mask)
    assert iou > 0.85
    recovered = prepare_mesh(scrambled, rotation, 250.0)
    assert mask_iou(silhouette_mask_png(MeshVolume(recovered)), photo_mask) > 0.0
    assert np.allclose(np.sort(recovered.extents), np.sort(true.extents), rtol=0.03)


def test_flat_reconstructions_are_detected():
    slab = trimesh.creation.box(extents=[200, 150, 10])
    body = trimesh.creation.box(extents=[200, 150, 80])
    assert depth_ratio(slab) < MIN_DEPTH_RATIO <= depth_ratio(body)
