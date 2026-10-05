"""Solid-slice 3D puzzle (the default style): structure, density, depth control, mesh dependence,
manufacturing.

`tests/data/trex_photo_mask.png` is the silhouette of a photographed T-Rex kit (diagonal body, big head,
raised foot): the reference pose used to build the style. The generator itself is generic; the other
bodies here are procedural meshes and drawn silhouettes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.api.schemas import PuzzleSettingsIn
from app.geometry.kit.structure import StructureAnalyzer
from app.geometry.normalize import normalize_silhouette
from app.geometry.types import Complexity, Orientation, PieceRole, PuzzleParameters, Style
from app.geometry.volume import MeshVolume, prepare_mesh, remesh, silhouette_mask_png
from app.services.pipeline import generate_puzzle
from app.validators.geometry_validator import validate_model
from tests.animals import ANIMALS
from tests.meshes import biped, bird, quadruped

DATA = Path(__file__).parent / "data"
BASE = dict(thickness_mm=3.0, kerf_mm=0.15, clearance_mm=0.10, target_size_mm=250.0, piece_count=60,
            complexity=Complexity.ADVANCED, orientation=Orientation.AUTO, labels=True,
            sheet_width_mm=600.0, sheet_height_mm=400.0, kit_frame=True)


def params(style=Style.SLICE, **kw) -> PuzzleParameters:
    return PuzzleParameters(style=style, title="t", **{**BASE, **kw})


def run(mask, style=Style.SLICE, mesh=None, **kw):
    return generate_puzzle(mask, params(style, **kw), "Plywood", outputs=set(), mesh=mesh)


def slices(model):
    return [p for p in model.pieces if p.role in (PieceRole.RIB, PieceRole.VERTEBRA)]


def u_extent(p) -> float:
    x0, _, x1, _ = p.polygon.bounds
    return x1 - x0


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def trex_mask():
    return np.array(Image.open(DATA / "trex_photo_mask.png"))


@pytest.fixture(scope="module")
def trex(trex_mask):
    return run(trex_mask)


# ------------------------------------------------------------------------------ product definition


def test_solid_slice_is_the_default_style():
    assert PuzzleSettingsIn().style == Style.SLICE
    assert PuzzleSettingsIn().depth_scale == 1.0
    assert {s.value for s in Style} >= {"slice", "kit", "skeleton", "solid"}  # nothing was removed


def test_depth_scale_is_validated():
    with pytest.raises(ValueError):
        PuzzleSettingsIn(depth_scale=0.1)
    with pytest.raises(ValueError):
        PuzzleSettingsIn(depth_scale=5)


def test_reference_pose_structure_is_found(trex_mask):
    """Big head inside the torso blob, tail on the other end, one leg raised mid-stride: the body axis
    runs snout to tail tip, the head is the bulge past the neck, the jaw is part of the head."""
    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    assert "head" in st.ranges and "tail" in st.ranges
    assert st.spine.length > 0.9 * 250.0  # snout to tail tip, not stopping at the tail root
    assert st.head.length_mm > 60
    assert len([lb for lb in st.limbs if lb.kind == "limb"]) == 2  # both legs, the raised one too
    assert all(lb.attach_s < st.head.s_start for lb in st.limbs)  # the open jaw is not a limb


def test_slices_are_solid_cross_sections(trex):
    """Real, solid, independent parts that reach from the belly to the top of the back, unlike the thin
    open arches of the skeleton kit."""
    model = trex.model
    sl = slices(model)
    assert len(sl) >= 12
    assert all(len(p.polygon.interiors) == 0 for p in sl)
    kit = run(np.array(Image.open(DATA / "trex_photo_mask.png")), Style.KIT).model
    arch = [p for p in kit.pieces if p.role == PieceRole.RIB]
    fill = lambda ps: float(np.median([p.polygon.area / p.polygon.convex_hull.area for p in ps]))  # noqa: E731
    assert fill(sl) > 0.8 and fill(arch) < 0.6  # solid cross-sections vs open arches
    spine = [p for p in model.pieces if p.role == PieceRole.SPINE]
    kit_spine = [p for p in kit.pieces if p.role == PieceRole.SPINE]
    # a body-shaped plate (about half the body height), not the thin bar of the skeleton kit
    assert sum(p.polygon.area for p in spine) > 1.35 * sum(p.polygon.area for p in kit_spine)
    assert model.analysis["style"] == "slice" and model.analysis["slices"]["placed"] == len(sl)


def test_tail_slices_taper_towards_the_tip(trex_mask):
    """A tail sequence of slices, larger near the body and smaller towards the tip (a thicker estimate
    gives the thin tail enough width for a slot)."""
    model = run(trex_mask, depth_scale=1.6).model
    tail = [p for p in slices(model) if p.role == PieceRole.VERTEBRA]
    assert len(tail) >= 3
    by_distance = sorted(tail, key=lambda p: p.frame.cx)  # this pose: the tail runs towards +x
    areas = [p.polygon.area for p in by_distance]
    assert areas[-1] < 0.8 * areas[0] and areas[-1] == min(areas)


def test_limbs_follow_the_image_outline(trex):
    plates = [p for p in trex.model.pieces if p.role == PieceRole.LIMB]
    legs = [p for p in plates if p.polygon.bounds[3] - p.polygon.bounds[1] > 55]
    assert len(legs) == 2 and len(plates) == 4  # two long leg plates with feet; plus the arm, mirrored
    assert all(len(p.polygon.exterior.coords) > 25 for p in legs)  # an outline, not a box


def test_head_plates_show_only_what_the_image_shows(trex):
    """Skull plates have no invented eye socket or nostril: the reference silhouette has no opening in the head."""
    skull = [p for p in trex.model.pieces if p.role == PieceRole.SKULL]
    assert len(skull) == 2 and all(len(p.polygon.interiors) == 0 for p in skull)


# ------------------------------------------------------------------------------ number of slices


def test_slice_count_follows_the_detail_setting(trex_mask):
    n = {c: len(slices(run(trex_mask, complexity=c).model)) for c in
         (Complexity.EASY, Complexity.MEDIUM, Complexity.ADVANCED)}
    assert n[Complexity.EASY] < n[Complexity.MEDIUM] < n[Complexity.ADVANCED], n


def test_slice_count_follows_the_object_length():
    small = run(ANIMALS["horse"](), target_size_mm=150.0).model
    large = run(ANIMALS["horse"](), target_size_mm=300.0).model
    assert len(slices(large)) > len(slices(small))


def test_max_pieces_caps_the_slices(trex_mask):
    capped = run(trex_mask, piece_count=22).model
    assert len(capped.pieces) <= 22 + 2
    assert len(slices(capped)) < len(slices(run(trex_mask).model))


# ------------------------------------------------------------------------------ depth control


def test_depth_scale_changes_the_estimated_body(trex_mask):
    thin, base, thick = (run(trex_mask, depth_scale=k) for k in (0.6, 1.0, 1.6))
    w = lambda r: float(np.median([u_extent(p) for p in slices(r.model)]))  # noqa: E731
    assert w(thin) < w(base) < w(thick)
    assert w(thick) > 1.25 * w(base)
    zs = lambda r: max(abs(p.position_mm) for p in r.model.pieces if p.role == PieceRole.SKULL)  # noqa: E731
    assert zs(thin) < zs(base) < zs(thick)  # the skull plates move with the depth
    jz = lambda r: sorted(round(j.z_mm, 1) for j in r.model.joints)  # noqa: E731
    assert jz(thin) != jz(base) != jz(thick)  # slot positions
    assert len({sha(r.files["svg"]) for r in (thin, base, thick)}) == 3
    for r in (thin, base, thick):
        assert validate_model(r.model).ok


def test_depth_scale_changes_a_mesh_body():
    body = prepare_mesh(remesh(quadruped()), np.eye(3), 250.0)
    mask = silhouette_mask_png(MeshVolume(body))
    flat, deep = run(mask, mesh=body, depth_scale=0.7), run(mask, mesh=body, depth_scale=1.4)
    assert np.median([u_extent(p) for p in slices(deep.model)]) > 1.5 * np.median([u_extent(p) for p in slices(flat.model)])
    assert sha(flat.files["svg"]) != sha(deep.files["svg"])


# ------------------------------------------------------------------------------ 3D mesh drives the slices


@pytest.fixture(scope="module")
def mesh_kits():
    def slim():
        m = quadruped()
        m.apply_scale([1.0, 1.0, 0.6])
        return m

    out = {}
    for name, fn in (("quadruped", quadruped), ("slim", slim), ("biped", biped), ("bird", bird)):
        body = prepare_mesh(remesh(fn()), np.eye(3), 250.0)
        out[name] = (body, MeshVolume(body), run(silhouette_mask_png(MeshVolume(body)), mesh=body))
    return out


@pytest.mark.parametrize("name", ["quadruped", "biped", "bird"])
def test_every_slice_is_cut_from_the_mesh_section(mesh_kits, name):
    _, volume, res = mesh_kits[name]
    model = res.model
    assert validate_model(model).ok
    info = model.analysis["kit_volume"]
    assert info["kind"] == "mesh" and info["rib_sources"] == {"mesh-section": len(slices(model))}
    for p in slices(model):
        sec = volume.cross_section(p.frame, -500.0, 500.0, model.params.thickness_mm)
        assert p.polygon.difference(sec.buffer(0.3)).area <= 0.03 * p.polygon.area + 5.0, p.id


@pytest.mark.parametrize("a,b", [("quadruped", "bird"), ("quadruped", "biped"), ("biped", "bird")])
def test_different_meshes_give_different_slice_kits(mesh_kits, a, b):
    (ba, _, ra), (bb, _, rb) = mesh_kits[a], mesh_kits[b]
    assert not np.allclose(ba.bounds, bb.bounds, atol=1.0)
    ma, mb = ra.model, rb.model
    assert ma.object_size_mm != mb.object_size_mm
    sec = lambda m: sorted(r["section_u_extent_mm"] for r in m.analysis["kit_volume"]["ribs"])  # noqa: E731
    assert sec(ma) != sec(mb)  # cross-sections
    spine = lambda m: sorted(round(p.polygon.area) for p in m.pieces if p.role == PieceRole.SPINE)  # noqa: E731
    assert spine(ma) != spine(mb)  # spine plate
    shape = lambda m: sorted((p.role.value, round(p.polygon.area), round(u_extent(p))) for p in m.pieces)  # noqa: E731
    assert shape(ma) != shape(mb)  # part shapes and dimensions
    slots = lambda m: sorted((round(j.x_mm, 1), round(j.y_mm, 1), round(j.z_mm, 1)) for j in m.joints)  # noqa: E731
    assert slots(ma) != slots(mb)  # slot locations
    assert sha(ra.files["svg"]) != sha(rb.files["svg"])  # final SVG


def test_same_outline_different_body_gives_different_slices(mesh_kits):
    """The side view cannot tell these two bodies apart; the slices must."""
    (_, vq, rq), (_, vs, rs) = mesh_kits["quadruped"], mesh_kits["slim"]
    assert vq.silhouette.intersection(vs.silhouette).area / vq.silhouette.union(vs.silhouette).area > 0.95
    wq = np.median([u_extent(p) for p in slices(rq.model)])
    ws = np.median([u_extent(p) for p in slices(rs.model)])
    assert 0.45 < ws / wq < 0.8, (wq, ws)
    assert sha(rq.files["svg"]) != sha(rs.files["svg"])


def test_depth_only_hand_off_is_clearly_worse_than_the_mesh(mesh_kits):
    """The pre-fix hand-off (outline + symmetric depth, no sections) must differ from the mesh-driven slices."""
    from app.geometry.kit.slice import SliceGenerator

    body, volume, res = mesh_kits["bird"]
    depth_only = SliceGenerator(params()).generate(volume.silhouette, depth=volume.depth)
    a = sorted(round(u_extent(p), 1) for p in slices(res.model))
    b = sorted(round(u_extent(p), 1) for p in slices(depth_only))
    assert a != b


# ------------------------------------------------------------------------------ manufacturing


def test_manufacturing_geometry(trex):
    model, layout = trex.model, trex.layout
    p = model.params
    assert validate_model(model).ok
    assert model.joints and all(s.width_mm == pytest.approx(p.slot_width_mm) for pc in model.pieces for s in pc.slots)
    placed = {pl.piece_id: pl.polygon for pl in layout.placements}
    assert len(placed) == len(model.pieces)  # every part nested, none lost
    by_id = {pc.id: pc for pc in model.pieces}
    for pid, poly in placed.items():  # kerf: tool path = outline offset outwards by kerf / 2
        grow = (poly.area - by_id[pid].polygon.area) / by_id[pid].polygon.length
        assert grow == pytest.approx(p.kerf_mm / 2.0, abs=0.01)
        x0, y0, x1, y1 = poly.bounds
        assert 0 <= x0 and 0 <= y0 and x1 <= layout.sheet_width_mm and y1 <= layout.sheet_height_mm
    svg = trex.files["svg"]
    assert svg.count(b'-cut"') >= len(model.pieces) and b'-label"' in svg  # CUT and ENGRAVE are separate
    assert svg.lstrip().startswith(b"<?xml") or svg.lstrip().startswith(b"<svg")


def test_slice_kit_is_valid_for_other_materials(trex_mask):
    for thickness in (4.0, 6.0):
        r = run(trex_mask, thickness_mm=thickness)
        assert validate_model(r.model).ok
        assert all(s.width_mm == pytest.approx(thickness + 0.1) for pc in r.model.pieces for s in pc.slots)


@pytest.mark.parametrize("name", ["horse", "eagle", "fish", "logo"])
def test_any_object_gives_a_valid_kit(name):
    """Animals with and without a detected head, a fish, and a logo (no anatomy at all)."""
    r = run(ANIMALS[name]())
    assert validate_model(r.model).ok
    assert len(slices(r.model)) >= 5


def test_skeleton_kit_is_still_available(trex_mask):
    r = run(trex_mask, Style.KIT)
    assert validate_model(r.model).ok and r.model.analysis["style"] == "kit"
    assert any(len(p.polygon.interiors) == 0 for p in r.model.pieces if p.role == PieceRole.RIB)


# ------------------------------------------------------------------------------ thin appendages, narrow regions, traced head


def _holey(mask, cx, cy, r):
    out = mask.copy()
    yy, xx = np.ogrid[:out.shape[0], :out.shape[1]]
    out[(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r] = 0
    return out


def test_small_arms_are_detected_only_when_asked(trex_mask):
    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    plain = StructureAnalyzer().analyze(sil)
    thin = StructureAnalyzer(thin_appendages=True).analyze(sil)
    assert [lb.kind for lb in plain.limbs] == ["limb", "limb"]  # the skeleton kit is unchanged
    arms = [lb for lb in thin.limbs if lb.kind == "appendage"]
    assert len(arms) == 1 and arms[0].length_mm > 25 and arms[0].width_mm > 4
    assert arms[0].attach_s < thin.head.s_start  # on the chest, not on the head


def test_arm_plate_follows_the_arm_outline(trex):
    arms = [p for p in trex.model.pieces if p.role == PieceRole.LIMB and p.polygon.bounds[3] - p.polygon.bounds[1] < 50]
    assert len(arms) == 2  # one visible arm stands for a mirrored pair
    assert all(len(p.polygon.exterior.coords) > 12 for p in arms)
    assert trex.model.analysis["traced"]["arms"] == 1


def test_open_mouth_jaw_and_teeth_are_traced_from_the_outline(trex, trex_mask):
    model = trex.model
    assert model.analysis["traced"]["mouth"] is True
    skull = [p for p in model.pieces if p.role == PieceRole.SKULL]
    jaw = [p for p in model.pieces if p.role == PieceRole.JAW]
    assert len(skull) == 2 and len(jaw) == 2
    # the jaw plate is the lower jaw of the image: its outline is the image outline (many vertices, teeth),
    # not the straight-edged wedge the procedural mouth cuts
    assert len(jaw[0].polygon.exterior.coords) > 40
    head_area = sum(p.polygon.area for p in skull + jaw) / 2
    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    assert head_area < 0.4 * sil.area  # the head plates are the head, not the body


def test_a_closed_beak_gets_no_invented_mouth():
    r = run(ANIMALS["eagle"]())
    assert validate_model(r.model).ok
    assert r.model.analysis["traced"]["mouth"] is False  # nothing to trace: no gap is invented
    assert not any(p.role == PieceRole.JAW for p in r.model.pieces)


def test_eye_and_nostril_openings_are_traced_not_invented(trex_mask):
    """An opening in the image outline inside the head is cut out of the skull plates; without one,
    none is added."""
    plain = run(trex_mask).model
    assert plain.analysis["traced"]["head_openings"] == 0
    assert all(len(p.polygon.interiors) == 0 for p in plain.pieces if p.role == PieceRole.SKULL)
    with_hole = run(_holey(trex_mask, 215, 105, 11)).model  # an eye socket drawn into the head
    assert with_hole.analysis["traced"]["head_openings"] == 1
    head = lambda m: sum(p.polygon.area for p in m.pieces if p.role in (PieceRole.SKULL, PieceRole.JAW))  # noqa: E731
    assert head(with_hole) < head(plain) - 40.0  # the head plates lost the opening (~40 mm2 on each side)
    assert validate_model(with_hole).ok


def test_small_openings_survive_segmentation_but_other_styles_do_not_see_them(trex_mask):
    from app.geometry.normalize import fill_small_holes
    from app.vision.segmentation import clean_mask

    holey = _holey(trex_mask, 215, 105, 11)
    kept = clean_mask(holey)
    assert (kept[105, 215] == 0)  # an eye-sized opening is no longer filled (it was below 2 % of the object)
    sil = normalize_silhouette(kept, 250.0, 2.5).polygon
    assert len(sil.interiors) >= 1
    assert len(fill_small_holes(sil).interiors) == 0  # egg-crate and skeleton kit work on the filled outline
    for style in (Style.KIT, Style.SOLID):
        r = run(kept, style, piece_count=16) if style == Style.SOLID else run(kept, style)
        assert validate_model(r.model).ok


def test_thin_regions_are_covered_by_the_plate_instead_of_forced_slots(trex_mask):
    """Where the body is too thin for a slot, no slice is attempted and the spine plate grows to the local
    height: the side silhouette of a thin tail is kept (better than a plate at body-band height)."""
    from fidelity import assembly as A
    from fidelity import metrics as M
    from app.geometry.kit.slice import SliceGenerator

    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    out = {}
    for grow in (True, False):
        class G(SliceGenerator):
            grow_plate_in_narrow = grow
        g = G(params(depth_scale=0.4))  # a thin body: a third of the axis is too narrow for slots
        model = g.generate(sil)
        side = A.project(A.model_slabs(model), "side")
        out[grow] = (M.iou(sil, side), model, g)
    assert out[True][2].slice_log["narrow_stations_skipped"] > 0 and out[True][2].slice_log["narrow_share"] > 0.25
    assert out[True][0] > out[False][0] + 0.01, (out[True][0], out[False][0])  # silhouette kept (+0.023 measured)
    assert validate_model(out[True][1]).ok
    # no slot was forced into a region narrower than a slot plus walls
    narrow_w = out[True][2]._min_slice_width()
    for p in slices(out[True][1]):
        assert u_extent(p) >= narrow_w - 0.5, (p.id, u_extent(p))


# ------------------------------------------------------------------------------ fixes: mesh narrow rule, appendage depth, mouth vs openings


def _off_centre_body():
    """A sausage-shaped body that sits on one side of the z = 0 plane (z from -2 to 14), like a reconstruction
    whose centre of mass is not the middle of its body."""
    import trimesh

    m = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    m.apply_scale((125, 35, 8))
    m.apply_translation((0, 40, 6.0))
    return m


def test_mesh_narrow_rule_measures_the_real_section_not_the_z0_proxy():
    """Mesh path: `spine.half_depth` (min of up/down around z = 0) says 3.5 mm for a body whose sections are
    12-15 mm wide because it is off the z = 0 plane. The old rule would have skipped every slice."""
    from app.geometry.anatomy import Region
    from app.geometry.kit.slice import SliceGenerator

    v = MeshVolume(_off_centre_body())
    g = SliceGenerator(params())
    model = g.generate(v.silhouette, depth=v.depth, volume=v)
    sp = g.st.spine
    mid = (sp.s > 0.2 * sp.length) & (sp.s < 0.8 * sp.length)
    old_rule = (2 * np.maximum(sp.half_depth, 0) < g._min_slice_width()) & (sp.region != int(Region.HEAD))
    assert old_rule[mid].mean() == 1.0  # the proxy flags the whole middle as too thin ...
    assert g._narrow()[mid].mean() == 0.0  # ... the real section width does not
    assert len(slices(model)) >= 12 and validate_model(model).ok
    assert all(r["section_u_extent_mm"] > 11 for r in model.analysis["kit_volume"]["ribs"][:5])


def test_estimated_path_narrow_rule_is_unchanged(trex_mask):
    """Estimated volume: 2 x depth at the spine point is the slice width, so the rule still uses it."""
    from app.geometry.anatomy import Region
    from app.geometry.kit.slice import SliceGenerator

    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    g = SliceGenerator(params(depth_scale=0.4))
    g.generate(sil)
    sp = g.st.spine
    expected = (2 * np.maximum(sp.half_depth, 0) < g._min_slice_width()) & (sp.region != int(Region.HEAD))
    assert np.array_equal(g._narrow(), expected) and expected.mean() > 0.25


def test_appendage_half_extent_is_measured_where_the_body_is():
    """Per-side half extent of the body an appendage hangs from: a mesh off the z = 0 plane has +14 / -2."""
    from types import SimpleNamespace

    from app.geometry.kit.slice import SliceGenerator

    v = MeshVolume(_off_centre_body())
    g = SliceGenerator(params())
    g.volume = v
    arm = SimpleNamespace(axis=np.array([[0.0, 40.0], [0.0, 20.0]]), kind="appendage")
    ext = g._appendage_half_extent([arm])
    assert ext[1] == pytest.approx(14.0, abs=0.7) and ext[-1] == pytest.approx(2.0, abs=0.7)
    far = SimpleNamespace(axis=np.array([[400.0, 400.0], [420.0, 400.0]]), kind="appendage")  # outside the body
    assert g._appendage_half_extent([far]) == {1: float("inf"), -1: float("inf")}  # no clamp without evidence


def test_appendage_plates_and_crossbar_stay_inside_the_local_body(trex_mask):
    """Arm plates and their crossbar are clamped to the half-depth of the body they hang from (+ a small margin),
    not placed with the legs outside the widest rib: the reference body there is 11-15 mm deep."""
    from app.geometry.depth import AnatomicalDepthModel
    from app.geometry.kit.slice import SliceGenerator

    sil = normalize_silhouette(trex_mask, 250.0, 2.5).polygon
    g = SliceGenerator(params())
    model = g.generate(sil)
    T = model.params.thickness_mm
    arms = [p for p in model.pieces if p.role == PieceRole.LIMB and p.region == "appendage"]
    legs = [p for p in model.pieces if p.role == PieceRole.LIMB and p.region != "appendage"]
    assert arms and legs
    est = AnatomicalDepthModel().estimate(sil)
    c = arms[0].polygon.centroid
    local = float(est.sample(np.array([c.x]), np.array([c.y]))[0])
    log = next(r for r in model.analysis["kit_volume"]["limb_groups"] if "appendage_limit_pos_mm" in r)
    step = T + 1.0  # plates that would overlap another plate at the same depth are pushed out by one step
    for a in arms:
        assert abs(a.position_mm) <= log["appendage_limit_pos_mm"] + step + 1e-6, (a.position_mm, log)
        assert abs(a.position_mm) <= local + 0.5 * T + step, (a.position_mm, local)  # local depth + margin (+ one step)
        assert abs(a.position_mm) < 0.7 * max(abs(p.position_mm) for p in legs)  # not at the legs' cage depth (24.2)
    bar = next(p for p in model.pieces if p.role == PieceRole.SPACER and p.region == "limb"
               and abs(p.polygon.bounds[2]) < 25)  # the arm girdle crossbar (the legs' one is wider)
    assert bar.polygon.bounds[2] <= max(abs(a.position_mm) for a in arms) + T / 2 + model.params.min_wall_mm + 0.05
    assert log["appendage_unclamped_pos_mm"] > log["appendage_limit_pos_mm"] + step  # the old rule went far further out


def _synthetic_head(hole=True):
    """Head frame (xi along the head, eta up): a block with an open mouth cut in from the front (3-8 mm gap
    around eta = 4) and, behind it, an enclosed window that is taller than the mouth gap."""
    from shapely.geometry import Polygon, box

    head = box(0, -22, 95, 30)
    mouth = Polygon([(95.5, 1.0), (95.5, 8.0), (46, 4.4), (46, 3.8)])  # a wedge: apex at xi = 46, widest at the snout
    head = head.difference(mouth)
    if hole:
        head = head.difference(box(60, 12, 67, 20.5))  # 7 x 8.5 mm window: a bigger "gap" than the mouth near its apex
    return head


def test_mouth_scan_does_not_mistake_a_head_opening_for_the_mouth():
    from app.geometry.kit.slice import SliceGenerator

    g = SliceGenerator(params())
    spine = np.stack([np.linspace(0, 95, 40), np.full(40, 12.0)], 1)  # backbone passes high in the head
    plain = g._traced_mouth(_synthetic_head(hole=False), 95.0, -30.0, spine)
    holey = g._traced_mouth(_synthetic_head(hole=True), 95.0, -30.0, spine)
    assert plain is not None and holey is not None
    up, lo, eta_m = holey
    assert len(up.interiors) == 1 and len(lo.interiors) == 0  # the window survives the jaw cut
    assert eta_m == pytest.approx(plain[2], abs=0.01) and -2 < eta_m < 8  # the seam is at the mouth, not the window
    assert up.area + lo.area == pytest.approx(_synthetic_head(hole=True).area, rel=0.01)  # nothing lost
    assert sum(p.area for p in (holey[0], holey[1])) == pytest.approx(sum(p.area for p in (plain[0], plain[1])) - 7 * 8.5, rel=0.02)


def test_head_opening_and_mouth_are_both_kept_in_the_skull_plates(trex_mask):
    """A window drawn into the head of the reference silhouette (where the photographed kit has one) and the open
    mouth: both end up in the plates - the opening in the skull, the traced mouth as skull + jaw."""
    holey = _holey(trex_mask, 143, 96, 9)
    r = run(holey)
    model = r.model
    assert validate_model(model).ok
    assert model.analysis["traced"]["head_openings"] == 1 and model.analysis["traced"]["mouth"] is True
    skull = [p for p in model.pieces if p.role == PieceRole.SKULL]
    jaw = [p for p in model.pieces if p.role == PieceRole.JAW]
    assert len(skull) == 2 and len(jaw) == 2
    assert all(len(p.polygon.interiors) == 1 for p in skull)  # the window is a closed hole in both skull plates


# ------------------------------------------------------------------------------ order sensitivity of the slice sequence


def _start_shifted(delta):
    from app.geometry.kit.slice import SliceGenerator

    class Shifted(SliceGenerator):
        def _ribs_and_vertebrae(self, plan):
            self.s_lo += delta  # moves only where the sequence of slices starts
            super()._ribs_and_vertebrae(plan)

    return Shifted


@pytest.mark.parametrize("name", ["trex", "lion"])
def test_slice_sequence_start_does_not_change_the_result_beyond_noise(name, trex_mask, capsys):
    """Slices are placed one gap after the previous one, so a different starting position gives a different
    (equally valid) set of slices. Every starting position is run and every result is kept; the test reports
    median and range per metric and checks each run on its own, so a bad start is never averaged away."""
    from app.geometry.depth import AnatomicalDepthModel
    from fidelity.run import kit_vs_own_body

    mask = trex_mask if name == "trex" else ANIMALS["lion"]()
    sil = normalize_silhouette(mask, 250.0, 2.5).polygon
    est = AnatomicalDepthModel().estimate(sil)
    starts = (-3.0, -1.5, 0.0, 1.5, 3.0)
    rows = []
    for d in starts:
        model = _start_shifted(d)(params()).generate(sil)
        m = kit_vs_own_body(model, sil, est, 1.0)
        rows.append({"start_mm": d, "pieces": len(model.pieces), "valid": validate_model(model).ok,
                     "front": m["proj_iou_front"], "top": m["proj_iou_top"], "cov6": m["coverage_6mm"]})
    lines = [f"{name}: slice sequence start -> pieces / front / top / coverage@6mm (each run, then median [min-max])"]
    lines += [f"  start {r['start_mm']:+4.1f} mm: {r['pieces']:2d} pcs  front {r['front']:.3f}  top {r['top']:.3f}  cov6 {r['cov6']:.3f}  valid {r['valid']}"
              for r in rows]
    for k in ("pieces", "front", "top", "cov6"):
        v = [r[k] for r in rows]
        lines.append(f"  {k:6s} median {np.median(v):.3f}  range [{min(v):.3f} - {max(v):.3f}]")
    report = "\n".join(lines)
    with capsys.disabled():
        print("\n" + report)
    assert all(r["valid"] for r in rows), report  # every start is a valid kit
    floors = {"trex": (0.62, 0.55, 22), "lion": (0.55, 0.30, 14)}[name]  # from the measured runs, with slack
    for r in rows:  # individual floors: a single bad start fails, whatever the median is
        assert r["front"] >= floors[0] and r["top"] >= floors[1] and r["pieces"] >= floors[2], report
    assert max(r["front"] for r in rows) - min(r["front"] for r in rows) < 0.08, report
    assert max(r["top"] for r in rows) - min(r["top"] for r in rows) < 0.20, report  # the observed spread is ~0.1
