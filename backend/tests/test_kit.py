"""Fossil-kit generator: structure analysis, every component generator, joints, connectors,
spacers, frame and the manufacturing validation of complete kits."""

from functools import lru_cache

import numpy as np
import pytest
from shapely.geometry import Point, Polygon, box

from app.geometry.kit.builder import KitGenerator
from app.geometry.kit.components import (
    ConnectorGenerator,
    JointGenerator,
    LimbGenerator,
    RibGenerator,
    SkullGenerator,
    SpacerGenerator,
    SpineGenerator,
    VertebraGenerator,
)
from app.geometry.kit.constraints import ManufacturingConstraints
from app.geometry.kit.frame import FrameGenerator
from app.geometry.kit.structure import StructureAnalyzer
from app.geometry.normalize import normalize_silhouette
from app.geometry.quality import narrowest_feature
from app.geometry.sections import line_intervals
from app.geometry.types import Complexity, PieceKind, PieceRole, PuzzleParameters, Style
from app.validators.geometry_validator import validate_connectivity, validate_connectors, validate_model
from tests.animals import ANIMALS

KIT_SHAPES = ["horse", "lion", "wolf", "deer", "elephant", "eagle", "logo", "irregular", "trex", "fish", "thin_legs"]


def params(thickness=3.0, pieces=30, complexity="advanced", size=300.0, **kw) -> PuzzleParameters:
    return PuzzleParameters(thickness_mm=thickness, piece_count=pieces, complexity=Complexity(complexity),
                            target_size_mm=size, style=Style.KIT, **kw)


@lru_cache(maxsize=None)
def kit(name: str, thickness: float = 3.0, pieces: int = 30, complexity: str = "advanced", size: float = 300.0):
    p = params(thickness, pieces, complexity, size)
    return KitGenerator(p).generate(normalize_silhouette(ANIMALS[name](), size, p.min_feature_mm).polygon)


def constraints(thickness=3.0) -> ManufacturingConstraints:
    return ManufacturingConstraints.from_params(params(thickness))


def roles(model) -> dict[str, int]:
    out: dict[str, int] = {}
    for pc in model.pieces:
        out[pc.role.value] = out.get(pc.role.value, 0) + 1
    return out


# ------------------------------------------------------------------------------ structure


@pytest.mark.parametrize("name,head,limbs", [
    ("horse", True, 3), ("trex", True, 2), ("deer", True, 4), ("wolf", True, 2), ("logo", False, 0),
])
def test_structure_analyzer_finds_axis_head_and_limbs(name, head, limbs):
    sil = normalize_silhouette(ANIMALS[name](), 300, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    assert st.spine.length > 150
    assert (st.head is not None) == head
    assert len(st.limbs) >= limbs
    # The axis runs from the tail end to the head end; the dorsal normal points up in the torso.
    t0, t1 = st.ranges["torso"]
    mid = st.spine.at(0.5 * (t0 + t1))
    assert mid["normal"][1] > 0.5
    assert 0.0 <= st.confidence["structure"] <= 1.0
    d = st.to_dict()
    assert d["confidence_kind"] == "estimated" and d["limb_count"] == len(st.limbs)


def test_structure_has_head_neck_torso_tail_order():
    sil = normalize_silhouette(ANIMALS["trex"](), 300, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    r = st.ranges
    assert r["tail"][0] == 0 and r["tail"][1] <= r["torso"][0] + 1e-6
    assert r["torso"][1] <= r["neck"][0] + 1e-6 and r["neck"][1] <= r["head"][0] + 1e-6
    # The head polygon includes the lower jaw of the drawing (not just the skull blob).
    assert st.head.polygon.area > 1500


def test_depth_model_is_pluggable():
    from app.geometry.depth import DepthField, DepthModel

    class Flat(DepthModel):
        def estimate(self, silhouette, anatomy=None):
            return DepthField(np.full((4, 4), 7.0, np.float32), -1e4, -1e4, 5e3)

    sil = normalize_silhouette(ANIMALS["horse"](), 300, 2.5).polygon
    st = StructureAnalyzer(Flat()).analyze(sil)
    assert np.allclose(st.spine.half_depth, 7.0)


# ------------------------------------------------------------------------------ generators


@pytest.mark.parametrize("thickness", [3.0, 4.0, 6.0])
def test_rib_is_an_open_arch(thickness):
    c = constraints(thickness)
    rib = RibGenerator(c).make(half_width=40.0, bottom=-60.0, crown_top=12.0, band=RibGenerator(c).band_width(100))
    assert rib is not None and rib.is_valid
    assert len(rib.interiors) == 0  # an arch, not a disc with a hole
    # Below the crown, the centre line u = 0 is empty: the bottom is open.
    below = [iv for iv in line_intervals(rib, 0.0) if iv[1] < -c.min_slot_length - 1]
    assert below == []
    # Two legs reach down to the bottom on both sides.
    left, right = rib.intersection(box(-60, -62, -20, -45)), rib.intersection(box(20, -62, 60, -45))
    assert left.area > 0 and right.area > 0
    assert narrowest_feature(rib) >= c.min_feature_size - 0.1


def test_rib_size_follows_the_body():
    c = constraints()
    gen = RibGenerator(c)
    small = gen.make(20.0, -25.0, 10.0, gen.band_width(50))
    large = gen.make(45.0, -70.0, 10.0, gen.band_width(120))
    assert large.bounds[2] - large.bounds[0] > 1.8 * (small.bounds[2] - small.bounds[0])
    assert large.bounds[3] - large.bounds[1] > 1.8 * (small.bounds[3] - small.bounds[1])


def test_vertebra_has_spine_wings_and_slot_block():
    c = constraints()
    v = VertebraGenerator(c).make(crown_top=10.0, spike=12.0, wing=14.0)
    _, _, _, top = v.bounds
    assert top >= 21.5  # neural spine above the block
    assert v.bounds[2] - v.bounds[0] >= 27.0  # transverse processes
    assert narrowest_feature(v) >= c.min_feature_size - 0.1
    plain = VertebraGenerator(c).make(crown_top=10.0, spike=0.0, wing=0.0, transverse=False)
    assert plain.area < v.area


@pytest.mark.parametrize("thickness,clearance", [(3.0, 0.1), (4.0, 0.15), (6.0, 0.2)])
def test_joint_and_connector_dimensions_follow_material(thickness, clearance):
    p = params(thickness, clearance_mm=clearance)
    c = ManufacturingConstraints.from_params(p)
    assert c.slot_width == pytest.approx(thickness + clearance)
    cut = JointGenerator(c).cross_cutter(5.0, 0.0, from_above=True)
    assert cut.bounds[2] - cut.bounds[0] == pytest.approx(thickness + clearance)
    conn = ConnectorGenerator(c)
    bt = conn.shape((0, 0), (1, 0))
    ww, nw, length = conn.dims()
    assert ww >= max(2.2 * thickness, 6.0) - 1e-9 and nw >= 1.25 * c.min_feature_size - 1e-9
    assert bt.bounds[2] - bt.bounds[0] == pytest.approx(length)
    pocket = conn.pocket(bt)
    assert pocket.contains(bt) and pocket.exterior.distance(bt) == pytest.approx(clearance / 2, abs=1e-6)
    assert narrowest_feature(bt) >= c.min_feature_size - 0.1


def test_spacer_spans_all_plates_with_walls():
    c = constraints()
    sp = SpacerGenerator(c).make([0.0, 12.0, -12.0], -3.0, 10.0)
    assert sp.bounds[0] <= -12.0 - c.thickness / 2 - c.min_wall_thickness + 1e-6
    assert SpacerGenerator(c).make([0.0], 0.0, 1.0) is None  # too short to engage


def test_spine_bar_follows_curve():
    c = constraints()
    t = np.linspace(0, np.pi, 50)
    pts = np.stack([100 * t, 30 * np.sin(t)], 1)
    tang = np.gradient(pts, axis=0)
    tang /= np.linalg.norm(tang, axis=1, keepdims=True)
    nrm = np.stack([-tang[:, 1], tang[:, 0]], 1)
    bar = SpineGenerator(c).bar(pts, nrm, np.full(50, 5.0))
    assert bar.is_valid and bar.contains(Point(pts[25]))
    assert 9.5 <= bar.area / (np.sum(np.hypot(*np.diff(pts, axis=0).T))) <= 10.6


def test_skull_openings_are_real_holes_with_walls():
    c = constraints()
    head = box(0, -20, 80, 30)
    sk = SkullGenerator(c)
    hole = sk.openings(head, (30, 15), 6.0, [])
    assert hole is not None and head.buffer(-c.min_wall_thickness).contains(hole)
    assert sk.openings(head, (30, 27), 6.0, []) is None  # would break through the outline
    assert sk.openings(head, (30, 15), 0.5, []) is None  # below the minimum feature
    upper, jaw, gap = sk.mouth(head, 0.0, 30.0, 80.0, 10.0, teeth=True)
    assert upper is not None and jaw is not None and gap is not None
    assert upper.intersection(jaw).area < 1e-6
    assert upper.distance(jaw) >= 0.0


def test_limb_seams_only_where_a_connector_fits():
    c = constraints()
    plate = box(-10, 0, 10, 120)
    axis = np.array([[0, 120], [0, 0]], float)
    seams = LimbGenerator(c).seams(plate, axis, [0.42, 0.8])
    assert len(seams) == 2
    thin = box(-3, 0, 3, 120)
    assert LimbGenerator(c).seams(thin, axis, [0.5]) == []


def test_frame_never_touches_pieces():
    f = FrameGenerator(3.0, 3.0, 5.0, kerf_mm=0.15)
    frame = f.around((50, 50, 300, 200), 600, 400, "Horse")
    assert frame is not None and len(frame.polygon.interiors) == 1
    inner = Polygon(frame.polygon.interiors[0])
    assert inner.buffer(-3.0).contains(box(50, 50, 300, 200).buffer(-0.01))
    assert frame.engrave and all(frame.polygon.buffer(1e-6).contains(Point(x, y)) for s in frame.engrave for x, y in s)
    assert f.around((1, 1, 599, 399), 600, 400, "X") is None  # no room for the band


# ------------------------------------------------------------------------------ complete kits


@pytest.mark.parametrize("name", KIT_SHAPES)
def test_kits_validate(name):
    model = kit(name)
    report = validate_model(model)
    assert report.ok, report.errors[:5]
    r = roles(model)
    assert r.get("spine", 0) >= 1 and r.get("rib", 0) + r.get("vertebra", 0) >= 3
    assert all(pc.style == "kit" for pc in model.pieces)
    assert model.analysis["style"] == "kit"


@pytest.mark.parametrize("thickness", [3.0, 4.0, 6.0])
@pytest.mark.parametrize("name", ["horse", "trex"])
def test_kits_validate_for_material_thickness(name, thickness):
    model = kit(name, thickness=thickness, size=250.0 if thickness < 6 else 400.0)
    rep = validate_model(model)
    assert rep.ok, rep.errors[:5]
    for pc in model.pieces:
        for s in pc.slots:
            assert s.width_mm == pytest.approx(thickness + 0.1)


@pytest.mark.parametrize("complexity", ["easy", "medium", "advanced"])
def test_kit_complexity_levels(complexity):
    model = kit("trex", complexity=complexity)
    assert validate_model(model).ok
    if complexity == "easy":
        assert roles(model).get("jaw", 0) == 0


def test_trex_is_a_fossil_kit():
    model = kit("trex", pieces=40)
    r = roles(model)
    assert r["spine"] >= 2  # segmented backbone joined by bowties
    assert r["rib"] >= 5 and r["vertebra"] >= 4
    assert r["skull"] == 2 and r["jaw"] == 2  # left/right skull plates and lower jaws
    assert r["limb"] >= 4 and r["connector"] >= 4 and r["spacer"] >= 2
    skull = next(pc for pc in model.pieces if pc.role == PieceRole.SKULL)
    assert len(skull.polygon.interiors) >= 1  # eye socket / nostril cut through
    # Skull plates sit on both sides of the backbone, limbs outside the rib cage.
    zs = sorted(pc.position_mm for pc in model.pieces if pc.role == PieceRole.SKULL)
    assert zs[0] < 0 < zs[1]
    widest_rib = max((pc.polygon.bounds[2] for pc in model.pieces if pc.role == PieceRole.RIB))
    limb_z = min(abs(pc.position_mm) for pc in model.pieces if pc.role == PieceRole.LIMB)
    assert limb_z > widest_rib


def test_ribs_are_arches_hanging_from_the_backbone():
    model = kit("horse")
    for rib in (pc for pc in model.pieces if pc.role == PieceRole.RIB):
        assert rib.kind == PieceKind.CROSS and rib.slide_sign == 1
        assert not rib.polygon.interiors
        # Every rib has exactly one slot, at its crown (u = 0), opening downwards.
        assert len(rib.slots) == 1 and rib.slots[0].origin[0] == pytest.approx(0.0)
        assert rib.slots[0].direction == (0.0, -1.0)
        assert rib.polygon.bounds[1] < -10.0  # legs reach well below the backbone


def test_tail_vertebrae_shrink_towards_the_tip():
    model = kit("trex", pieces=40)
    tail = [pc for pc in model.pieces if pc.role == PieceRole.VERTEBRA and pc.region == "tail"]
    assert len(tail) >= 3
    tail.sort(key=lambda pc: pc.frame.cx)  # tail tip is at -x in the fixture
    heights = [pc.polygon.bounds[3] for pc in tail]
    assert heights[0] <= heights[-1]


def test_connectors_are_real_pieces_with_verified_fit():
    model = kit("trex", pieces=40)
    conns = model.analysis["connectors"]
    ids = {pc.id for pc in model.pieces}
    assert conns and all(c["id"] in ids and set(c["pieces"]) <= ids for c in conns)
    assert all(c["id"].startswith("C") for c in conns)
    rep = validate_connectors(model)
    assert rep.ok and rep.checks["connectors_checked"] == len(conns)


def test_connector_validation_catches_a_missing_pocket():
    import copy

    model = copy.deepcopy(kit("trex", pieces=40))
    conn = model.analysis["connectors"][0]
    host = model.piece(conn["pieces"][0])
    key = model.piece(conn["id"])
    host.polygon = host.polygon.union(key.polygon.buffer(0.5))  # pocket filled in
    assert not validate_connectors(model).ok


def test_connectivity_validation_catches_a_loose_piece():
    import copy

    model = copy.deepcopy(kit("horse"))
    victim = next(pc for pc in model.pieces if pc.role == PieceRole.RIB)
    model.joints = [j for j in model.joints if victim.id not in (j.cross_id, j.profile_id)]
    rep = validate_connectivity(model)
    assert not rep.ok and victim.id in rep.errors[0]


def test_ids_follow_the_kit_scheme():
    model = kit("trex", pieces=40)
    prefix = {"spine": "SP", "skull": "SK", "jaw": "J", "limb": "L", "rib": "R", "vertebra": "V",
              "spacer": "S", "connector": "C"}
    for pc in model.pieces:
        assert pc.id.startswith(prefix[pc.role.value]) and pc.id[len(prefix[pc.role.value]):].isdigit()


def test_rib_count_setting():
    p = params(rib_count=5)
    model = KitGenerator(p).generate(normalize_silhouette(ANIMALS["horse"](), 300, p.min_feature_mm).polygon)
    assert roles(model)["rib"] == 5
    assert validate_model(model).ok


def test_thin_tail_is_simplified_with_a_warning():
    model = kit("trex", thickness=6.0, size=300.0)
    assert validate_model(model).ok
    assert any("simplified" in w for w in model.warnings)


def test_too_thin_shape_is_reported_not_exported():
    from app.core.errors import PuzzleError

    m = np.zeros((768, 1024), np.uint8)
    m[380:388, 100:900] = 255  # a 8 px stick
    p = params(6.0, size=120.0)
    with pytest.raises(PuzzleError) as exc:
        KitGenerator(p).generate(normalize_silhouette(m, 120, p.min_feature_mm).polygon)
    assert exc.value.code in ("INVALID_GEOMETRY", "TOO_MANY_PIECES")


# ------------------------------------------------------------------------------ bone library


def bones(thickness=3.0):
    from app.geometry.kit.bones import BoneLibrary

    return BoneLibrary(constraints(thickness))


def test_long_bone_has_joint_knobs_and_a_narrower_shaft():
    from shapely.geometry import LineString as LS

    b = bones().long_bone((0, 0), (0, 100), 10.0, 9.0)
    assert b.is_valid and not b.interiors

    def chord(y):
        return b.intersection(LS([(-50, y), (50, y)])).length

    assert chord(50) < 0.75 * chord(2)  # shaft narrower than the knobs
    assert chord(50) < 0.75 * chord(98)
    assert narrowest_feature(b) >= constraints().min_feature_size - 0.1


def test_foot_has_toes():
    from shapely.geometry import LineString as LS

    foot = bones().foot((0, 0), (80, 0), 9.0, toes=3)
    assert foot.is_valid
    # Across the toes (near the tip) the outline is cut into several fingers.
    hits = foot.intersection(LS([(70, -40), (70, 40)]))
    assert len(getattr(hits, "geoms", [hits])) == 3


def test_rib_arch_tapers_and_curls():
    from shapely.geometry import LineString as LS

    gen = bones()
    crown = JointGenerator(constraints()).crown(12.0)
    rib = gen.rib_arch(40.0, -60.0, 12.0, 6.0, crown)
    assert rib.is_valid and not rib.interiors
    assert [iv for iv in line_intervals(rib, 0.0) if iv[1] < -5] == []  # open bottom
    # The left leg is narrower near its tip than half-way up.
    def leg_width(t):
        return rib.intersection(LS([(-60, t), (-5, t)])).length

    assert 0 < leg_width(-50) < leg_width(-15)
    # Tips curl inwards: the lowest point of the left leg is right of its widest point.
    left = rib.intersection(box(-60, -70, 0, 100))
    assert left.bounds[0] < min(x for x, y in left.exterior.coords if y < -55)


def test_lightening_holes_keep_full_walls():
    import numpy as np

    lib = bones()
    plate = box(0, 0, 200, 40)
    out = lib.lighten(plate, np.array([[0, 20], [200, 20]]), [])
    assert len(out.interiors) >= 1
    c = constraints()
    for hole in out.interiors:
        assert Polygon(hole).distance(plate.exterior) >= c.min_wall_thickness - 1e-6
    blocked = lib.lighten(plate, np.array([[0, 20], [200, 20]]), [box(0, 0, 200, 40)])
    assert not blocked.interiors  # never inside a keep-out (joints, slots, pockets)


def test_kit_limbs_are_designed_bones():
    model = kit("horse")
    limbs = [pc for pc in model.pieces if pc.role == PieceRole.LIMB]
    assert limbs
    # Designed bones are smooth (many vertices) and narrower in the shaft than at the knuckles.
    assert all(len(pc.polygon.exterior.coords) > 40 for pc in limbs)


# ------------------------------------------------------------------------------ editable skeleton


def test_skeleton_round_trips_through_image_fractions():
    import numpy as np

    from app.geometry.kit.structure import ImageFrame

    mask = ANIMALS["horse"]()
    sil = normalize_silhouette(mask, 300, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    sk = st.to_skeleton()
    assert len(sk["spine"]) == 12 and sk["head_start"] is not None
    assert all(len(lb["joints"]) == 4 for lb in sk["limbs"])  # hip, knee, ankle, toe
    frame = ImageFrame(mask, sil)
    frac = frame.skeleton_to_fractions(sk)
    assert all(-0.05 <= v <= 1.05 for p in frac["spine"] for v in p)
    back = frame.skeleton_to_mm(frac)
    assert np.allclose(back["spine"], sk["spine"], atol=0.05)


def test_edited_skeleton_drives_the_kit():
    sil = normalize_silhouette(ANIMALS["horse"](), 300, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    sk = st.to_skeleton()
    p = params()
    # No limbs and no head: the kit follows exactly what the user kept.
    edited = dict(sk, limbs=[], head_start=None)
    model = KitGenerator(p).generate(sil, skeleton=edited)
    assert validate_model(model).ok
    r = roles(model)
    assert r.get("limb", 0) == 0 and r.get("skull", 0) == 0
    assert any("edited by hand" in w for w in model.warnings)
    # Keep only the front pair of legs.
    front = sorted(sk["limbs"], key=lambda lb: -lb["joints"][0][0])[:2]
    model = KitGenerator(p).generate(sil, skeleton=dict(sk, limbs=front))
    assert validate_model(model).ok and roles(model)["limb"] >= 2


def test_spine_reaches_the_snout_not_an_ear():
    sil = normalize_silhouette(ANIMALS["horse"](), 300, 2.5).polygon
    st = StructureAnalyzer().analyze(sil)
    end = st.spine.pts[-1]
    # The horse fixture's muzzle points right/down from the head; ears point up.
    head_box = st.head.polygon.bounds
    assert end[0] > head_box[0] + 0.6 * (head_box[2] - head_box[0])
    assert end[1] < head_box[3] - 0.25 * (head_box[3] - head_box[1])
