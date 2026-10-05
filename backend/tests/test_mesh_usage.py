"""The fossil kit is driven by the 3D body itself, not only by its outline.

Regression guard for the root cause found in Phase 2 (see PUZZLECUT_CONTEXT.md): the mesh path
handed the kit generator nothing but the mesh's side outline and a symmetric depth estimate, so
the ribs were templates and two different 3D bodies with the same outline gave the same kit.

Every model goes through the production upload path (remesh -> prepare_mesh -> MeshVolume ->
generate_puzzle with the mesh).
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from shapely.geometry import box

from app.geometry.kit.builder import KitGenerator
from app.geometry.normalize import polygon_parts
from app.geometry.types import Complexity, Orientation, PieceRole, PuzzleParameters, Style
from app.geometry.volume import MeshVolume, prepare_mesh, remesh, silhouette_mask_png
from app.services.pipeline import generate_puzzle
from app.validators.geometry_validator import validate_model
from tests.meshes import biped, bird, quadruped

# Web-app defaults (frontend DEFAULT_SETTINGS, medium size).
SETTINGS = dict(style=Style.KIT, thickness_mm=3.0, kerf_mm=0.15, clearance_mm=0.10, target_size_mm=250.0,
                piece_count=30, complexity=Complexity.ADVANCED, orientation=Orientation.AUTO, labels=True,
                sheet_width_mm=600.0, sheet_height_mm=400.0, kit_frame=True)


def _slim_quadruped():
    """Same side outline as `quadruped` (z only is scaled), a body 40 % narrower."""
    m = quadruped()
    m.apply_scale([1.0, 1.0, 0.6])
    return m


MESHES = {"quadruped": quadruped, "slim_quadruped": _slim_quadruped, "biped": biped, "bird": bird}


@pytest.fixture(scope="module")
def kit():
    cache: dict = {}

    def get(name: str):
        if name not in cache:
            body = prepare_mesh(remesh(MESHES[name]()), np.eye(3), 250.0)
            volume = MeshVolume(body)
            params = PuzzleParameters(title=name, **SETTINGS)
            res = generate_puzzle(silhouette_mask_png(volume), params, "Plywood", outputs=set(), mesh=body)
            cache[name] = (body, volume, res)
        return cache[name]

    return get


def _ribs(model):
    return [pc for pc in model.pieces if pc.role == PieceRole.RIB]


def _width(poly) -> float:
    x0, _, x1, _ = poly.bounds
    return x1 - x0


def _section(volume, pc, T):
    return volume.cross_section(pc.frame, -500.0, 500.0, T)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.mark.parametrize("name", list(MESHES))
def test_ribs_are_cut_from_the_mesh_sections(kit, name):
    _, volume, res = kit(name)
    model = res.model
    assert validate_model(model).ok
    info = model.analysis["kit_volume"]
    assert model.analysis["shape_source"] == "mesh" and info["kind"] == "mesh"
    ribs = _ribs(model)
    assert ribs
    from_mesh = info["rib_sources"].get("mesh-section", 0)
    assert from_mesh >= 0.75 * len(ribs), info["rib_sources"]
    T = model.params.thickness_mm
    logged = {r["id"]: r for r in info["ribs"]}
    for pc in ribs:
        sec = _section(volume, pc, T)
        assert sec is not None
        # Inside the real body (the slot block may touch the outline; nothing pokes out).
        outside = pc.polygon.difference(sec.buffer(0.3)).area
        assert outside <= 0.02 * pc.polygon.area + 5.0, (pc.id, outside)
        r = logged.get(pc.id)
        if r and r["source"] == "mesh-section" and r["u_scale"] == 1.0:
            # As wide as the measured torso section on its own plane.
            assert _width(pc.polygon) >= 0.95 * r["section_u_extent_mm"], (pc.id, _width(pc.polygon), r)


def test_same_outline_different_body_gives_different_ribs(kit):
    """The side outline alone cannot tell these two bodies apart; the kit must."""
    _, vq, rq = kit("quadruped")
    _, vs, rs = kit("slim_quadruped")
    inter = vq.silhouette.intersection(vs.silhouette).area
    # Same side view (the raw meshes project identically; remeshing smooths the thinner legs).
    assert inter / vq.silhouette.union(vs.silhouette).area > 0.95
    wq = np.median([_width(pc.polygon) for pc in _ribs(rq.model)])
    ws = np.median([_width(pc.polygon) for pc in _ribs(rs.model)])
    assert 0.45 < ws / wq < 0.8, (wq, ws)  # the body is 0.6x as wide
    zq = max(abs(pc.position_mm) for pc in rq.model.pieces if pc.role == PieceRole.LIMB)
    zs = max(abs(pc.position_mm) for pc in rs.model.pieces if pc.role == PieceRole.LIMB)
    assert zs < zq  # limbs sit against the narrower rib cage
    assert _sha(rq.files["svg"]) != _sha(rs.files["svg"])


@pytest.mark.parametrize("a,b", [("quadruped", "bird"), ("quadruped", "biped"), ("biped", "bird")])
def test_different_meshes_give_different_kits(kit, a, b):
    ba, va, ra = kit(a)
    bb, vb, rb = kit(b)
    assert not np.allclose(ba.bounds, bb.bounds, atol=1.0)  # bounding boxes
    ma, mb = ra.model, rb.model
    assert ma.object_size_mm != mb.object_size_mm
    sa = sorted(r["section_u_extent_mm"] for r in ma.analysis["kit_volume"]["ribs"] if "section_u_extent_mm" in r)
    sb = sorted(r["section_u_extent_mm"] for r in mb.analysis["kit_volume"]["ribs"] if "section_u_extent_mm" in r)
    assert sa != sb  # structural sections
    parts_a = sorted((pc.role.value, round(pc.polygon.area)) for pc in ma.pieces)
    parts_b = sorted((pc.role.value, round(pc.polygon.area)) for pc in mb.pieces)
    assert parts_a != parts_b  # parts
    slots_a = sorted((round(j.x_mm, 1), round(j.y_mm, 1), round(j.z_mm, 1)) for j in ma.joints)
    slots_b = sorted((round(j.x_mm, 1), round(j.y_mm, 1), round(j.z_mm, 1)) for j in mb.joints)
    assert slots_a != slots_b  # slot positions
    assert _sha(ra.files["svg"]) != _sha(rb.files["svg"])  # final SVG


@pytest.mark.parametrize("name", ["quadruped", "bird"])
def test_mesh_kit_is_closer_to_the_body_than_the_depth_only_kit(kit, name):
    """Fidelity regression (fidelity-harness metrics, 1 mm voxels): the same mesh, generated with
    its sections vs with only its outline and symmetric depth (the pre-fix hand-off).
    Measured when this test was written (mesh vs depth-only):
      quadruped  front 0.450 / 0.437  top 0.410 / 0.358  cover@6 0.455 / 0.369  outside 0.408 / 0.465
      bird       front 0.361 / 0.258  top 0.542 / 0.395  cover@6 0.406 / 0.212  outside 0.299 / 0.418"""
    from fidelity import assembly as A
    from fidelity.run import voxel_block

    body, volume, res = kit(name)
    mesh = voxel_block(body, A.model_slabs(res.model), None, 1.0, "mesh")
    depth_only = KitGenerator(PuzzleParameters(title="depth-only", **SETTINGS)).generate(volume.silhouette, depth=volume.depth)
    est = voxel_block(body, A.model_slabs(depth_only), None, 1.0, "mesh")
    assert mesh["coverage_6mm"] > est["coverage_6mm"] + 0.05, (mesh, est)
    assert mesh["proj_iou_top"] > est["proj_iou_top"] + 0.03, (mesh, est)
    assert mesh["material_outside_ref"] < est["material_outside_ref"] - 0.03, (mesh, est)
    assert mesh["proj_iou_front"] >= est["proj_iou_front"], (mesh, est)
    if name == "quadruped":
        assert mesh["proj_iou_front"] > 0.426  # Phase-2 baseline (kit_upload3d, quadruped)


def test_legs_stand_where_the_mesh_legs_are(kit):
    """Limb plates sit at the measured depth of the legs (quadruped: legs at |z| ~ 15.5 mm), not outside
    the rib cage (~29 mm), so the front view shows legs under the body."""
    _, volume, res = kit("quadruped")
    z = {abs(round(pc.position_mm, 1)) for pc in res.model.pieces if pc.role == PieceRole.LIMB}
    assert z and max(z) < 22.0, z
    x0, _, x1, _ = volume.silhouette.bounds
    centres = [abs(0.5 * (a + b)) for x in np.linspace(x0, x1, 60)
               for a, b in volume.z_intervals(float(x), 20.0, half_width=2.0)]  # horizontal cut through the legs
    assert centres, "the test mesh has legs reaching the ground"
    leg_z = float(np.median(centres))
    assert min(abs(v - leg_z) for v in z) < 4.0, (z, leg_z)


def test_ribs_follow_an_off_centre_body():
    """A body that is not centred on the spine plane: the ribs and the limb depths follow it (the
    depth estimate is symmetric by construction and cannot)."""
    import trimesh

    lump = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    lump.apply_scale((60, 28, 30))
    lump.apply_translation((0, 105, 28))  # one-sided bulge of the barrel (+z)
    body = prepare_mesh(remesh(trimesh.util.concatenate([quadruped(), lump])), np.eye(3), 250.0)
    volume = MeshVolume(body)
    params = PuzzleParameters(title="bulge", **SETTINGS)
    model = KitGenerator(params).generate(volume.silhouette, depth=volume.depth, volume=volume)
    assert validate_model(model).ok
    # Ribs over the bulge whose plane is (nearly) perpendicular to the body axis.
    # (ribs squeezed between the limb plates are narrowed on purpose: u_scale < 1)
    full = {r["id"] for r in model.analysis["kit_volume"]["ribs"] if r.get("u_scale") == 1.0}
    mid = [pc for pc in _ribs(model) if pc.id in full and abs(pc.frame.cx) < 40 and abs(pc.frame.dx) < 0.2]
    assert mid
    for pc in mid:
        x0, y0, x1, y1 = pc.polygon.bounds
        band = _section(volume, pc, 3.0).intersection(box(-1e4, y0, 1e4, y1))
        s0, _, s1, _ = max(polygon_parts(band), key=lambda q: q.area).bounds
        assert abs(x0 - s0) < 1.0 and abs(x1 - s1) < 1.0, (pc.id, (x0, x1), (s0, s1))
        assert x1 > -x0 + 5.0  # asymmetric like the body
    # Limb plates stand at the legs' measured depth on each side - not symmetric about the spine plane,
    # because the legs of this body are not (the bulge shifts the centre of mass the mesh is centred on).
    logged = model.analysis["kit_volume"]["limb_groups"]
    assert any(abs(g["zL_pos_mm"] - g["zL_neg_mm"]) > 3.0 for g in logged), logged
    limb_z = [pc.position_mm for pc in model.pieces if pc.role == PieceRole.LIMB]
    assert abs(max(limb_z) + min(limb_z)) > 3.0, sorted(set(limb_z))
    depth_only = KitGenerator(params).generate(volume.silhouette, depth=volume.depth)
    for pc in _ribs(depth_only):
        x0, _, x1, _ = pc.polygon.bounds
        assert abs(x0 + x1) < 1.0  # the estimate can only make symmetric ribs
