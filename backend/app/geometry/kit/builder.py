"""KitGenerator: AnimalStructure -> fossil-style laser kit (PuzzleModel).

Construction (all generic, driven only by the structural analysis):

  backbone     bar along the spine curve (plane z = 0), split into segments at the
               tail/torso/neck boundaries; segments are joined by bowtie connectors that sit
               in a widened joint boss
  ribs         open arches across the torso, perpendicular to the spine, hanging from the
               backbone (half-lap slot in their crown, the backbone slot opens upwards)
  vertebrae    cross plates with neural spine and transverse processes along neck and tail,
               shrinking with the local body size
  skull        two side plates (z = ±zs) with eye socket and nostril cut through, split from
               two lower-jaw plates along a mouth line with a tapering gap and teeth; skull
               spacers tie both sides to the backbone, a jaw spacer (inserted from below)
               ties the jaw sides, a bowtie at the hinge joins each jaw to its skull plate
  limbs        one plate stack per detected limb at z = ±zL (outside the rib cage), split
               into bones (upper / lower / foot) at bowtie seams; each left/right pair hangs
               from a girdle crossbar that also locks onto the backbone

With a `volume` (the real 3D body: an AI reconstruction or an uploaded model as MeshVolume,
or the estimated DepthVolume) the body's own geometry drives the cross pieces: every rib is cut
from the measured cross-section at its station (it follows the body's width and belly and never
leaves the body), vertebrae are clipped to their section, the rib-plan widths come from the
sections, and each limb group sits just outside the measured rib cage (or at the measured spread
of the limbs when they stand wider). Without a volume, the template ribs sized from the depth
field are used (the estimated photo path).

Every cross piece is checked against every plate before it is accepted (same rules as the
final validator: slot through to the edge, walls, engagement, no interpenetration with any
plate, no crossing of another cross piece) and its slots are cut only then.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.anatomy import Region
from app.geometry.depth import DepthField, DepthModel
from app.geometry.kit.components import (
    ConnectorGenerator,
    JawGenerator,
    JointGenerator,
    LimbGenerator,
    RibGenerator,
    SkullGenerator,
    SpacerGenerator,
    SpineGenerator,
    VertebraGenerator,
    clean,
    side_of,
    split_by_line,
    split_by_segment,
)
from app.geometry.kit.bones import BoneLibrary, chaikin
from app.geometry.kit.constraints import ManufacturingConstraints
from app.geometry.kit.structure import AnimalStructure, StructureAnalyzer
from app.geometry.labels import place_label, short_code
from app.geometry.normalize import largest_polygon, polygon_parts
from app.geometry.quality import narrowest_feature, slot_walls
from app.geometry.sections import line_intervals, strip_intervals
from app.geometry.slots import BIG
from app.geometry.types import (
    Complexity,
    Joint,
    Orientation,
    Piece,
    PieceKind,
    PieceRole,
    PuzzleModel,
    PuzzleParameters,
    RibFrame,
    SlideDirection,
    Slot,
    Transform3D,
)
from app.geometry.volume import Volume

logger = logging.getLogger(__name__)
TOL = 1e-3

PREFIX = {
    PieceRole.SPINE: "SP", PieceRole.SKULL: "SK", PieceRole.JAW: "J", PieceRole.LIMB: "L",
    PieceRole.RIB: "R", PieceRole.VERTEBRA: "V", PieceRole.SPACER: "S", PieceRole.CONNECTOR: "C",
}
ORDER = [PieceRole.SPINE, PieceRole.SKULL, PieceRole.JAW, PieceRole.LIMB, PieceRole.RIB,
         PieceRole.VERTEBRA, PieceRole.SPACER, PieceRole.CONNECTOR]


@dataclass
class Plate:
    key: str
    poly: Polygon
    z: float
    role: PieceRole
    region: str
    slots: list[Slot] = field(default_factory=list)
    pockets: list[Polygon] = field(default_factory=list)
    sort: tuple = ()


@dataclass
class CrossPart:
    key: str
    poly: Polygon  # local (u = z, t)
    frame: RibFrame
    sign: int  # +1 slides in from +d (slots open at its low-t edge), -1 from -d
    role: PieceRole
    region: str
    joins: list[tuple[str, float]]  # (plate key, split t)
    station: float
    slots: list[Slot] = field(default_factory=list)


class KitGenerator:
    def __init__(self, params: PuzzleParameters, depth_model: DepthModel | None = None):
        self.p = params
        self.c = ManufacturingConstraints.from_params(params)
        self.analyzer = StructureAnalyzer(depth_model)
        self.joint = JointGenerator(self.c)
        self.ribs = RibGenerator(self.c)
        self.vert = VertebraGenerator(self.c)
        self.spacer = SpacerGenerator(self.c)
        self.conn = ConnectorGenerator(self.c)
        self.skull = SkullGenerator(self.c)
        self.jawgen = JawGenerator(self.c)
        self.limbgen = LimbGenerator(self.c)
        self.spinegen = SpineGenerator(self.c)
        self.bones = BoneLibrary(self.c)
        self.limb_mode = "measured"  # or "cage" (see _girdle_depth)
        self.trace_head = False  # solid slice: mouth / teeth / jaw traced from the outline, outline not smoothed
        self.invent_openings = True  # eye socket / nostril / window cut where the image shows none (kit only)

    # ================================================================== public

    def generate(self, silhouette: Polygon, depth: DepthField | None = None, skeleton: dict | None = None,
                 volume: Volume | None = None) -> PuzzleModel:
        """`skeleton` (mm): a hand-edited skeleton replacing the detected one. `volume`: the 3D
        body in the same frame as `silhouette` (see module docstring)."""
        st = self.analyzer.analyze(silhouette, depth=depth, skeleton=skeleton)
        return self.build(st, volume=volume)

    def build(self, st: AnimalStructure, volume: Volume | None = None) -> PuzzleModel:
        self.st = st
        self.volume = volume
        self._sections: dict[float, object] = {}
        self._axial: Polygon | None = None
        self.rib_log: list[dict] = []
        self.girdle_log: list[dict] = []
        self.plates: dict[str, Plate] = {}
        self.crosses: dict[str, CrossPart] = {}
        self.joints: list[Joint] = []
        self.connectors: list[tuple[str, str, str]] = []  # (bowtie key, host a, host b)
        self.warnings: list[str] = list(st.warnings)
        self._n = 0

        self._backbone()
        rib_plan = self.rib_plan = self._rib_plan()
        self.zL = self._limb_depth(rib_plan)
        self._skull()
        girdles = self._limbs()
        for g in girdles:
            self._place_girdle(g)
        self._skull_spacers()
        self._ribs_and_vertebrae(rib_plan)
        return self._finish()

    # ================================================================== backbone

    def _hb(self) -> np.ndarray:
        sp = self.st.spine
        H = sp.dorsal + sp.ventral
        hmin = self.c.bar_half_height_min
        thin = np.minimum(sp.dorsal, sp.ventral)
        k = np.where(sp.region == int(Region.HEAD), 0.12, 0.16)
        return np.clip(k * H, hmin, np.maximum(hmin, 0.42 * thin))

    def _backbone(self) -> None:
        sp = self.st.spine
        c = self.c
        hb = self._hb()
        hmin = c.bar_half_height_min
        ok = np.minimum(sp.dorsal, sp.ventral) >= 0.7 * hmin
        mid = sp.index(0.5 * sum(self.st.ranges["torso"]))
        if not ok[mid]:
            ok[mid] = True
        lo = mid
        while lo > 0 and ok[lo - 1]:
            lo -= 1
        hi = mid
        while hi < len(ok) - 1 and ok[hi + 1]:
            hi += 1
        if hi - lo < 4:
            raise PuzzleError(ErrorCode.INVALID_GEOMETRY,
                              "The shape is too thin along its main axis for a backbone at this material thickness. "
                              "Increase the puzzle size or use thinner material.")
        self.s_lo, self.s_hi = float(sp.s[lo]), float(sp.s[hi])
        trimmed = (self.s_lo + (sp.length - self.s_hi)) / sp.length
        if trimmed > 0.04:
            self.warnings.append(
                f"Thin tips of the axis ({trimmed * 100:.0f}% of its length, e.g. the tail tip) are thinner than "
                f"the backbone can be at {self.p.thickness_mm:g} mm and were simplified.")

        # Seams: tail/torso and torso/neck boundaries, plus extra splits for long bars.
        ww, _, bl = self.conn.dims()
        margin = 2.0 * bl
        seams = []
        for name, end in (("tail", 1), ("neck", 0)):
            if name in self.st.ranges:
                s = self.st.ranges[name][end]
                if self.s_lo + margin < s < self.s_hi - margin:
                    seams.append(s)
        max_len = 0.85 * max(self.p.sheet_width_mm, self.p.sheet_height_mm)
        bounds = [self.s_lo, *sorted(seams), self.s_hi]
        for a, b in zip(bounds[:-1], bounds[1:]):
            chord = np.linalg.norm(sp.at(b)["point"] - sp.at(a)["point"])
            pieces = int(np.ceil(chord / max_len))
            for k in range(1, pieces):
                seams.append(a + (b - a) * k / pieces)
        seams = sorted(seams)
        boss = ww / 2.0 + c.clearance + c.min_wall_thickness + 1.0
        reach = bl / 2.0 + c.min_wall_thickness + 3.0
        for s0 in seams:
            near = np.abs(sp.s - s0)
            ramp = np.clip(1.0 - (near - reach) / (2.0 * reach), 0.0, 1.0)
            hb = np.maximum(hb, hb * (1 - ramp) + boss * ramp)
        self.hb = hb
        m = (sp.s >= self.s_lo) & (sp.s <= self.s_hi)
        # Vertebral bumps along the dorsal edge (neural arches), not on the skull part and not at seams.
        pitch = max(3.0 * c.crown_half_width, 0.04 * sp.length)
        bump = 0.32 * hb * np.cos(np.pi * sp.s / pitch) ** 4
        bump[sp.region == int(Region.HEAD)] = 0.0
        for s0 in seams:
            bump[np.abs(sp.s - s0) < reach * 1.5] = 0.0
        bump[np.minimum(sp.dorsal, sp.ventral) < 1.6 * hb] = 0.0  # keep inside thin parts
        bar = self.spinegen.bar(sp.pts[m], sp.normal[m], hb[m], bump[m])
        if bar is not None and narrowest_feature(bar) < c.min_feature_size:
            r = 0.5 * c.min_feature_size + 0.05
            bar = clean(bar.buffer(-r, join_style="mitre").buffer(r, join_style="mitre"))
        if bar is None:
            raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "Could not build a valid backbone for this shape.")

        segments = [bar]
        cuts = []
        for s0 in seams:
            f = sp.at(s0)
            pt, n = f["point"], f["normal"]
            half = float(np.interp(s0, sp.s, hb)) * 2.5
            line = LineString([tuple(pt - n * half), tuple(pt + n * half)])
            host = next((g for g in segments if g.intersects(line)), None)
            if host is None:
                continue
            parts = split_by_segment(host, pt - n * half, pt + n * half)
            if len(parts) < 2:
                continue
            t = f["tangent"]
            before = clean(unary_union([q for q in parts if side_of(q.representative_point().coords[0], pt, n) * side_of(pt - t, pt, n) > 0]))
            after = clean(unary_union([q for q in parts if side_of(q.representative_point().coords[0], pt, n) * side_of(pt - t, pt, n) < 0]))
            after = _separate(before, after) if before is not None else after
            if before is None or after is None or before.area < 4 * c.thickness ** 2 or after.area < 4 * c.thickness ** 2:
                continue
            bt = self.conn.shape(pt, t)
            if not self.conn.fits(host, bt):
                continue
            pocket = self.conn.pocket(bt)
            if any(narrowest_feature(clean(g.difference(pocket)) or g) < c.min_feature_size for g in (before, after)):
                continue  # the seam or the pocket would leave a sliver: no seam here
            segments.remove(host)
            segments += [before, after]
            cuts.append((s0, bt))
        keys = []
        for seg in sorted(segments, key=lambda g: sp.project(g.centroid.coords[0])):
            k = self._key("bar")
            sc = sp.project(seg.centroid.coords[0])
            self.plates[k] = Plate(k, seg, 0.0, PieceRole.SPINE, self._region_at(sc), sort=(sc,))
            keys.append(k)
        for s0, bt in cuts:
            hosts = [k for k in keys if self.plates[k].poly.distance(bt) < 1e-6 or self.plates[k].poly.intersects(bt)]
            self._add_bowtie(bt, 0.0, hosts[:2], self._region_at(s0))

    def _bar_key_at(self, point) -> str | None:
        best, dist = None, BIG
        for k, pl in self.plates.items():
            if pl.role == PieceRole.SPINE:
                d = pl.poly.distance(_P(point))
                if d < dist:
                    best, dist = k, d
        return best

    def _add_bowtie(self, bt: Polygon, z: float, hosts: list[str], region: str) -> bool:
        if len(hosts) != 2:
            return False
        pocket = self.conn.pocket(bt)
        new = {}
        for h in hosts:
            cut = clean(self.plates[h].poly.difference(pocket))
            if cut is None or len(polygon_parts(make_valid(self.plates[h].poly.difference(pocket)))) != 1:
                return False
            if narrowest_feature(cut) < self.c.min_feature_size - 0.05:
                return False
            share = self.plates[h].poly.intersection(bt).area / bt.area
            if share < 0.3:
                return False
            new[h] = cut
        for h, cut in new.items():
            self.plates[h].poly = cut
            self.plates[h].pockets.append(pocket)
        k = self._key("bowtie")
        self.plates[k] = Plate(k, clean(bt), z, PieceRole.CONNECTOR, region,
                               sort=(self._order_of(hosts[0]),))
        self.connectors.append((k, hosts[0], hosts[1]))
        return True

    # ================================================================== skull

    def _skull(self) -> None:
        st, c, sp = self.st, self.c, self.st.spine
        self.skull_info = None
        if st.head is None:
            return
        head = st.head
        s0, s1 = head.s_start, min(head.s_end, self.s_hi)
        if head.length_mm < 6 * c.thickness or head.height_mm < 4 * c.min_slot_length:
            self.warnings.append("The head is too small for separate skull plates; it is part of the backbone.")
            return
        hc = np.asarray(head.polygon.exterior.coords)
        centre = hc.mean(axis=0)
        _, _, vt = np.linalg.svd(hc - centre, full_matrices=False)
        e1 = vt[0]
        if np.dot(centre - sp.at(s0)["point"], e1) < 0:
            e1 = -e1
        e2 = np.array([-e1[1], e1[0]])
        if np.dot(e2, sp.at(0.5 * (s0 + s1))["normal"]) < 0:
            e2 = -e2
        base = centre + e1 * float(np.min((hc - centre) @ e1))
        to_local = [e1[0], e1[1], e2[0], e2[1], -float(np.dot(e1, base)), -float(np.dot(e2, base))]
        # inverse: world = base + xi e1 + eta e2
        to_world = [e1[0], e2[0], e1[1], e2[1], float(base[0]), float(base[1])]
        H = affinity.affine_transform(chaikin(head.polygon, 0 if self.trace_head else 2), to_local)
        H = clean(H.union(affinity.affine_transform(self._bar_union().intersection(head.polygon.buffer(1.0)), to_local))) or H
        if self.trace_head and head.polygon.interiors:  # the backbone must not close the traced openings again
            cut = unary_union([affinity.affine_transform(Polygon(r), to_local) for r in head.polygon.interiors])
            H = clean(H.difference(cut)) or H
        _, hy0, Lh, hy1 = H.bounds
        inside = np.array([head.polygon.buffer(1.0).contains(_P(q)) for q in sp.pts])
        m = (sp.s >= s0) & inside
        if not m.any():
            m = sp.s >= s0
        rel = sp.pts[m] - base
        eta_curve = rel @ e2
        hb_head = self.hb[m]
        eta_bar_low = float(np.min(eta_curve - hb_head)) if len(eta_curve) else 0.0
        eta_m = eta_bar_low - c.min_gap
        depth = float(np.median(sp.half_depth[m])) if m.any() else 0.0
        zs = max(0.55 * depth, c.slot_width + c.min_wall_thickness + 0.5, c.thickness + 1.0)

        cx = self.p.complexity
        want_jaw = cx != Complexity.EASY
        upper, jaw = None, None
        traced = (self._traced_mouth(H, Lh, eta_m, np.stack([rel @ e1, rel @ e2], 1))
                  if (want_jaw and self.trace_head and len(rel)) else None)
        if traced is not None:
            upper, jaw, eta_m = traced  # the real open mouth of the outline, teeth included
            self.warnings.append("The mouth, jaw and teeth were traced from the image outline.")
        elif want_jaw:
            gap_max = max(2.5 * c.min_gap, 0.16 * (hy1 - hy0))
            for teeth, gm in ((True, gap_max), (False, gap_max), (False, 0.0)):
                upper, jaw, _ = self.skull.mouth(H, eta_m, 0.45 * Lh, Lh, gm, teeth=teeth)
                if (self.jawgen.usable(jaw) and narrowest_feature(upper) >= c.min_feature_size
                        and narrowest_feature(jaw) >= c.min_feature_size):
                    break
                upper, jaw = None, None
        if upper is None:
            upper = H

        # Spacer stations (fractions of the head length) are kept clear of the openings.
        spacer_xi = [0.2 * Lh, 0.62 * Lh]
        spacer_xi_default = list(spacer_xi)
        if self.trace_head and head.polygon.interiors:
            # Skull spacers must not cut into traced openings: move each station to the nearest free place.
            traced = [affinity.affine_transform(Polygon(r), to_local) for r in head.polygon.interiors]
            half = c.crown_half_width + 2.0
            for i, xi0 in enumerate(spacer_xi):
                for d in (0.0, 0.04, -0.04, 0.08, -0.08, 0.12, -0.12, 0.16, -0.16):
                    xi = float(np.clip(xi0 + d * Lh, 0.08 * Lh, 0.92 * Lh))
                    band = box(xi - half, -BIG, xi + half, BIG)
                    if not any(h.buffer(c.min_wall_thickness).intersects(band) for h in traced):
                        spacer_xi[i] = xi
                        break
        keep = [box(xi - c.crown_half_width - 2, -BIG, xi + c.crown_half_width + 2, BIG) for xi in spacer_xi]
        holes = []
        eye = self._best_opening(upper, Lh, eta_m, (0.25, 0.55), 0.11 * Lh, keep) if self.invent_openings else None
        if eye is not None:
            holes.append(eye)
        if self.invent_openings and (cx == Complexity.ADVANCED or (cx == Complexity.MEDIUM and holes)):
            nos = self._best_opening(upper, Lh, eta_m, (0.72, 0.92), 0.05 * Lh,
                                     keep + [h.buffer(c.min_wall_thickness) for h in holes], aspect=1.4)
            if nos is not None:
                holes.append(nos)
        if self.invent_openings and cx == Complexity.ADVANCED and holes:
            # A larger window between the eye and the nostril (lightens the skull like a real one).
            win = self._best_opening(upper, Lh, eta_m, (0.5, 0.7), 0.09 * Lh,
                                     keep + [h.buffer(c.min_wall_thickness) for h in holes], aspect=1.6)
            if win is not None:
                holes.append(win)
        if holes:
            upper = Polygon(upper.exterior.coords, [h.exterior.coords for h in holes])
            upper = clean(upper) or upper

        if jaw is not None and cx == Complexity.ADVANCED and self.invent_openings:
            jw = self._best_opening(jaw, Lh, eta_m, (0.55, 0.8), 0.06 * Lh, keep, aspect=1.8, below=True)
            if jw is not None:
                jaw = clean(Polygon(jaw.exterior.coords, [jw.exterior.coords])) or jaw
        world = lambda g: clean(affinity.affine_transform(g, to_world))  # noqa: E731
        skull_w = world(upper)
        if skull_w is None:
            return
        info = {"zs": zs, "e1": e1, "e2": e2, "base": base, "Lh": Lh, "spacer_xi": spacer_xi,
                "spacer_xi_default": spacer_xi_default if spacer_xi_default != spacer_xi else None, "skull": [], "jaw": []}
        for z in (zs, -zs):
            k = self._key("skull")
            self.plates[k] = Plate(k, skull_w, z, PieceRole.SKULL, "head", sort=(z < 0,))
            info["skull"].append(k)
        if jaw is not None:
            jaw_w = _separate(skull_w, world(jaw))
            # Hinge bowtie across the mouth line, behind the gap.
            placed = []
            if jaw_w is not None:
                # The two outlines are simplified separately: close sub-0.05 mm gaps along the seam.
                host = clean(unary_union([skull_w.buffer(0.05), jaw_w.buffer(0.05)]).buffer(-0.05))
                _, _, bl = self.conn.dims()
                for shift in (0.0, -0.12 * bl, 0.12 * bl, -0.2 * bl, 0.2 * bl):
                    for f in np.linspace(0.85, 0.15, 15):
                        centre = base + e1 * (f * 0.45 * Lh) + e2 * (eta_m + shift)
                        bt = self.conn.shape(centre, e2)
                        if host is not None and self.conn.fits(host, bt):
                            placed.append(bt)
                            break
                    if placed:
                        break
            if placed:
                for z, sk in zip((zs, -zs), info["skull"]):
                    k = self._key("jaw")
                    self.plates[k] = Plate(k, jaw_w, z, PieceRole.JAW, "head", sort=(z < 0,))
                    info["jaw"].append(k)
                    if not self._add_bowtie(placed[0], z, [sk, k], "head"):
                        del self.plates[k]
                        info["jaw"].remove(k)
            if not info["jaw"]:
                self.warnings.append("The lower jaw could not be joined reliably and was merged into the skull.")
                merged = world(clean(unary_union([upper, jaw])))
                for k in info["skull"]:
                    self.plates[k].poly = merged or self.plates[k].poly
        self.skull_info = info
        self.head_frame = (base, e1, e2, Lh, eta_m)

    def _traced_mouth(self, H: Polygon, Lh: float, eta_default: float, spine_xy: np.ndarray):
        """Upper skull and lower jaw cut along the open mouth of the head outline (head frame: xi along
        the head, eta dorsal). The mouth is where a line across the head meets two separate stretches of
        material (upper and lower jaw) with a clear gap between them; the cut follows the middle of that
        gap to the snout and continues straight back from its apex to the hinge. None when the outline
        shows no open mouth (closed jaw, beak without a gap, head too small)."""
        from shapely.ops import split

        c = self.c
        # The head may already carry traced openings (a skull window, a nostril): enclosed holes of the outline.
        # They are material for the purpose of finding the mouth - a hole between two stretches of material is
        # not a gap between jaws - so the scan runs on the head with its holes filled. The mouth is an opening
        # of the outer boundary and is unaffected. The holes themselves are cut apart or kept below.
        holes = [Polygon(r) for r in H.interiors]
        solid = Polygon(H.exterior)
        xs = np.linspace(0.25 * Lh, 0.97 * Lh, 48)
        gaps = []
        for xi in xs:
            ivs = line_intervals(solid, float(xi))
            best = None
            for (a0, a1), (b0, b1) in zip(ivs[:-1], ivs[1:]):
                if b0 - a1 >= max(c.min_gap, 1.0) and (best is None or b0 - a1 > best[1] - best[0]):
                    best = (a1, b0)
            gaps.append(best)
        run = None
        for k in range(len(xs) - 3):
            if all(g is not None for g in gaps[k:k + 4]):
                run = k
                break
        if run is None:
            return None
        apex = float(xs[run])
        line_pts = [(float(xs[k]), 0.5 * (gaps[k][0] + gaps[k][1])) for k in range(run, len(xs)) if gaps[k] is not None]
        if len(line_pts) < 4 or (Lh - apex) < 0.12 * Lh:
            return None
        eta_mouth = float(np.mean([p[1] for p in line_pts[:5]]))
        # The seam behind the mouth normally continues the mouth line. It is lowered below the backbone only
        # if the backbone would then run through the lower jaw at a skull spacer station (the spacers need the
        # upper plate to reach the spine); it joins the mouth line at its apex.
        eta_m = eta_mouth
        if eta_mouth > eta_default:
            above = [spine_xy[int(np.argmin(np.abs(spine_xy[:, 0] - xs_))), 1] >= eta_mouth + c.min_slot_length
                     for xs_ in (0.2 * Lh, 0.62 * Lh)]
            if not any(above):  # at least one skull spacer must find upper-plate material at the spine
                eta_m = eta_default
        minx, miny, maxx, maxy = H.bounds
        apex_pt = line_pts[0]

        def cut_path(seam: float) -> list:
            pts = [(minx - 1.0, seam), (apex_pt[0], seam)]
            if abs(apex_pt[1] - seam) > 1e-6:
                pts.append(apex_pt)
            return pts + line_pts[1:] + [(maxx + 1.0, line_pts[-1][1])]

        # The straight seam behind the mouth must not run through a traced opening (it would turn the hole
        # into a notch): lower it clear of the holes if needed, or give up and let the caller use the
        # generated mouth - a valid hole is never sacrificed for the jaw cut.
        guard = [h.buffer(c.min_wall_thickness) for h in holes]
        for drop in (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0):
            path = cut_path(eta_m - drop)
            if not any(LineString(path).intersects(g) for g in guard):
                eta_m -= drop
                break
        else:
            return None
        try:
            pieces = [g for g in split(H, LineString(path)).geoms if g.geom_type == "Polygon"]
        except (shapely.errors.GEOSException, ValueError):
            return None
        def below(g):
            q = g.representative_point()
            return q.y < float(np.interp(q.x, [p[0] for p in path], [p[1] for p in path]))
        up = clean(unary_union([g for g in pieces if not below(g)])) if pieces else None
        lo = clean(unary_union([g for g in pieces if below(g)])) if pieces else None
        if up is None or lo is None or not (self.jawgen.usable(lo) and narrowest_feature(up) >= c.min_feature_size
                                            and narrowest_feature(lo) >= c.min_feature_size):
            return None
        if len(up.interiors) + len(lo.interiors) != len(holes):
            return None  # a traced opening was lost in the cut: use the generated mouth instead
        return up, lo, eta_m

    def _best_opening(self, upper: Polygon, Lh: float, eta_m: float, xi_range, r_max: float, keep,
                      aspect: float = 1.0, below: bool = False) -> Polygon | None:
        """Largest round opening (eye socket / nostril) in the given band of the head that keeps
        full walls to the outline, the mouth and every joint."""
        c = self.c
        best = None
        for xi in np.linspace(xi_range[0] * Lh, xi_range[1] * Lh, 9):
            if below:
                ivs = [iv for iv in line_intervals(upper, xi) if iv[0] < eta_m]
                if not ivs:
                    continue
                lo, hi = ivs[0][0], min(ivs[-1][1], eta_m)
            else:
                ivs = [iv for iv in line_intervals(upper, xi) if iv[1] > eta_m]
                if not ivs:
                    continue
                lo, hi = max(ivs[0][0], eta_m), ivs[-1][1]
            for frac in (0.6, 0.5, 0.7):
                eta = lo + frac * (hi - lo)
                r = min(r_max, 0.5 * (hi - lo) - c.min_wall_thickness)
                while r >= 1.2 * c.min_feature_size:
                    hole = self.skull.openings(upper, (xi, eta), r, keep, aspect=aspect)
                    if hole is not None:
                        if best is None or hole.area > best.area:
                            best = hole
                        break
                    r *= 0.85
        return best

    def _skull_spacers(self) -> None:
        info = self.skull_info
        if not info:
            return
        sp, c = self.st.spine, self.c
        base, e1 = info["base"], info["e1"]
        zs = info["zs"]
        ok = 0
        # Stations kept clear of traced openings first; the default stations (a slot may then open a traced
        # hole into a notch, which is still a valid joint) only if none of those can be attached.
        tries = [info["spacer_xi"]] + ([info["spacer_xi_default"]] if info.get("spacer_xi_default") else [])
        for xis in tries:
            ok = self._place_skull_spacers(xis, info, sp, c, base, e1, zs)
            if ok:
                break
        if ok == 0:
            for k in info["skull"] + info["jaw"]:
                self._drop_plate(k)
            self.warnings.append("The skull plates could not be attached reliably; the head is part of the backbone.")
            self.skull_info = None
            return
        self._jaw_spacer(info, sp, c, zs)

    def _place_skull_spacers(self, xis, info, sp, c, base, e1, zs) -> int:
        ok = 0
        for xi in xis:
            placed = False
            for dxi in (0.0, 0.05, -0.05, 0.1, -0.1):
                s = sp.project(base + e1 * (xi + dxi * info["Lh"]))
                if not (self.s_lo < s < self.s_hi):
                    continue
                f = sp.at(s)
                frame = RibFrame(float(f["point"][0]), float(f["point"][1]), float(f["normal"][0]), float(f["normal"][1]))
                bar = self._bar_key_at(f["point"])
                plates = [(bar, 0.0)] + [(k, 0.0) for k in info["skull"]]
                tops = []
                for k, _ in plates:
                    iv = _interval_at(frame.to_local(self.plates[k].poly), 0.0)
                    if iv is None:
                        break
                    tops.append(iv[1])
                if len(tops) != len(plates):
                    continue
                top = max(max(tops), float(np.interp(s, sp.s, self.hb)) + c.min_wall_thickness)
                poly = self.spacer.make([0.0, zs, -zs], -c.min_slot_length, top)
                if poly is None:
                    continue
                part = CrossPart(self._key("spacer"), poly, frame, 1, PieceRole.SPACER, "head", plates, s)
                if self._try_cross(part):
                    placed = True
                    ok += 1
                    break
            if not placed:
                continue
        return ok

    def _jaw_spacer(self, info, sp, c, zs) -> None:
        # Jaw spacer, inserted from below between the jaw sides.
        if len(info["jaw"]) == 2:
            jaw = self.plates[info["jaw"][0]].poly
            base, e1, e2, Lh, eta_m = self.head_frame
            for f in (0.7, 0.6, 0.8, 0.5):
                centre = base + e1 * (f * Lh) + e2 * (eta_m - 0.3 * c.thickness)
                frame = RibFrame(float(centre[0]), float(centre[1]), float(e2[0]), float(e2[1]))
                iv = _interval_at(frame.to_local(jaw), -0.0, below=True)
                if iv is None:
                    continue
                jl, ju = iv
                if ju - jl < 2.0 * c.min_slot_length + 1.0:
                    continue
                split_t = jl + max(c.min_slot_length, 0.45 * (ju - jl))
                if ju - split_t < c.min_slot_length * 0.8:
                    continue
                poly = self.spacer.make([zs, -zs], jl, split_t + c.min_slot_length)
                if poly is None:
                    continue
                part = CrossPart(self._key("spacer"), poly, frame, -1, PieceRole.SPACER, "head",
                                 [(k, split_t) for k in info["jaw"]], sp.project(centre))
                if self._try_cross(part):
                    break

    # ================================================================== limbs

    def _rib_plan(self) -> list[dict]:
        sp, c = self.st.spine, self.c
        a, b = self.st.ranges["torso"]
        a, b = max(a, self.s_lo), min(b, self.s_hi)
        L = b - a
        if L <= 0:
            return []
        m = 0.06 * L
        plan = []
        stations = np.linspace(a + m, b - m, 120)
        neg, pos = self._section_widths(stations)
        for s, w_neg, w_pos in zip(stations, neg, pos):
            w_sec = max(w_neg, w_pos)
            f = sp.at(s)
            H = f["dorsal"] + f["ventral"]
            band = self.ribs.band_width(H)
            hb = float(np.interp(s, sp.s, self.hb))
            # Arch centre-line half width: from the measured section (its outer edge minus half a
            # band) when the body is known, else from the depth estimate at the axis.
            w = w_sec - band / 2.0 if np.isfinite(w_sec) else 0.92 * f["half_depth"]
            W = max(w, c.crown_half_width + band + c.min_gap)
            plan.append({"s": float(s), "W": W, "band": band, "hb": hb,
                         "bottom": -(f["ventral"] - 0.05 * H), "region": f["region"],
                         "extent": {-1: float(w_neg), 1: float(w_pos)}})  # measured outer edge per side
        return plan

    def _limb_depth(self, rib_plan: list[dict]) -> float:
        c = self.c
        widest = max((r["W"] + r["band"] / 2 for r in rib_plan), default=c.crown_half_width)
        sp = self.st.spine
        widest = max(widest, float(np.max(sp.half_depth)) * 0.6)
        return widest + c.thickness + 1.0

    def _limbs(self) -> list[dict]:
        st, c, sp = self.st, self.c, self.st.spine
        girdles = []
        groups: dict[int, list] = {}
        for lb in st.limbs:
            groups.setdefault(lb.group, []).append(lb)
        cx = self.p.complexity
        fractions = [0.5] if cx == Complexity.EASY else [0.42, 0.8]
        # Limb chains (mirrored single limbs count twice) must leave room for the rib cage.
        chains = sum(2 if (lb.side == 0 or len(groups[lb.group]) == 1) else 1 for lb in st.limbs)
        budget = 0.45 * self.p.piece_count - len(self.plates) - len(groups)
        while fractions and chains * (2 * len(fractions) + 1) > budget:
            fractions = fractions[:-1]
        for gid, members in sorted(groups.items()):
            s_a = float(np.clip(np.mean([lb.attach_s for lb in members]), self.s_lo + 2, self.s_hi - 2))
            f = sp.at(s_a)
            hb = float(np.interp(s_a, sp.s, self.hb))
            # Plates of a limb group must not overlap a plate already at the same depth.
            entries = []
            for lb in members:
                sides = (1, -1) if lb.side == 0 or len(members) == 1 else (lb.side,)
                hip = self._hip_plate(lb, f, hb)
                if hip is None:
                    continue
                entries.append((lb, sides, hip))
            if not entries:
                self.warnings.append("A detected limb could not be turned into a plate and was skipped.")
                continue
            if self.volume is None:
                zL = self.zL
                for _ in range(4):
                    clash = any(
                        pl.z * s > 0 and abs(abs(pl.z) - zL) < c.thickness + 0.5 and pl.poly.intersects(hip)
                        for _, sides, hip in entries for s in sides for pl in self.plates.values()
                    )
                    if not clash:
                        break
                    zL += c.thickness + 1.0
                depth = {1: zL, -1: zL}
            else:
                # Measured body: each side at its own depth (a body need not be centred on the spine plane).
                depth = self._girdle_depth(gid, entries)
                for side in (1, -1):
                    for _ in range(4):
                        clash = any(
                            pl.z * side > 0 and abs(abs(pl.z) - depth[side]) < c.thickness + 0.5 and pl.poly.intersects(hip)
                            for _, sides, hip in entries if side in sides for pl in self.plates.values()
                        )
                        if not clash:
                            break
                        depth[side] += c.thickness + 1.0
            girdle = {"s": s_a, "zL": max(depth.values()), "hips": [], "region": "limb"}
            for lb, sides, hip in entries:
                seams = self._limb_seams(lb, hip, len(fractions))
                bones = _split_bones(hip, seams, lb.axis[0], reach=3.0 * self._knob_radius())
                for side in sides:
                    z = side * depth[side]
                    keys = []
                    for i, bone in enumerate(bones):
                        k = self._key("limb")
                        self.plates[k] = Plate(k, bone, z, PieceRole.LIMB, lb.kind, sort=(gid, -side, i))
                        keys.append(k)
                    i = 0
                    for p, d in seams[: len(bones) - 1]:
                        if i + 1 >= len(keys):
                            break
                        bt = self.conn.shape(p, d)
                        if self._add_bowtie(bt, z, [keys[i], keys[i + 1]], lb.kind):
                            i += 1
                            continue
                        # Rejoin the two bones (they cannot be connected safely). The split left
                        # a SEAM_GAP between them (_separate): close it, or `clean` would keep only
                        # the larger bone and silently drop the other (and any bowtie it holds).
                        a_poly, b_poly = self.plates[keys[i]].poly, self.plates[keys[i + 1]].poly
                        r = SEAM_GAP
                        merged = clean(unary_union([a_poly.buffer(r, join_style="mitre"), b_poly.buffer(r, join_style="mitre")])
                                       .buffer(-r, join_style="mitre"))
                        if merged is not None and merged.area < 0.99 * (a_poly.area + b_poly.area):
                            merged = None
                        if merged is not None:
                            self.plates[keys[i]].poly = merged
                            del self.plates[keys[i + 1]]
                            keys.pop(i + 1)
                        else:
                            i += 1
                    girdle["hips"].append(keys[0])
            girdles.append(girdle)
        return girdles

    def _knob_radius(self) -> float:
        """Joint knob radius that carries a bowtie across the seam with full walls."""
        c = self.c
        ww, _, bl = self.conn.dims()
        return max(ww / 2.0, bl / 2.0) + c.clearance + c.min_wall_thickness + 1.5

    def _joints(self, lb) -> np.ndarray:
        j = lb.joints if lb.joints is not None and len(lb.joints) >= 2 else np.vstack([lb.axis[0], lb.axis[-1]])
        return np.asarray(j, dtype=float)

    def _hip_plate(self, lb, f: dict, hb: float) -> Polygon | None:
        """Designed limb plate: girdle blade (pelvis / scapula) around the crossbar strut and the
        upper joint, then a long bone per segment and a foot with toes at the end. Bone widths
        follow the limb's local thickness; seam joints get knobs big enough for a bowtie."""
        c = self.c
        pt, n = f["point"], f["normal"]
        lo = pt - n * (c.min_slot_length + c.min_wall_thickness + 1.0)
        hi = pt + n * (hb + c.min_wall_thickness)
        strut = LineString([tuple(lo), tuple(hi)]).buffer(c.crown_half_width + c.min_wall_thickness, cap_style="flat")
        joints = self._joints(lb)
        sil = self.st.silhouette
        boundary = sil.boundary
        mf = c.min_feature_size
        knob = self._knob_radius()
        local = []
        for p in joints:
            d = boundary.distance(_P(p)) if sil.contains(_P(p)) else 0.5 * lb.width_mm
            local.append(max(d, 0.25 * lb.width_mm))
        local = np.asarray(local)
        radii = np.maximum(0.9 * local, 1.6 * mf)
        radii[1:-1] = np.maximum(radii[1:-1], knob)  # seam joints carry a bowtie
        radii[0] = max(radii[0], 0.8 * knob)
        parts = [self.bones.blade(strut, joints[0], radii[0])]
        n_seg = len(joints) - 1
        for i in range(n_seg):
            a, b = joints[i], joints[i + 1]
            if i == n_seg - 1 and n_seg >= 2:
                parts.append(self.bones.foot(a, b, radii[i], toes=3 if self.p.complexity != Complexity.EASY else 2))
            else:
                end_b = radii[i + 1] if i + 1 < n_seg else max(0.7 * radii[i], 1.6 * mf)
                bend = 0.04 if i == 0 else -0.03
                parts.append(self.bones.long_bone(a, b, radii[i], end_b, bend=bend))
        plate = clean(unary_union([p for p in parts if p is not None]))
        if plate is None:
            return None
        if self.p.complexity == Complexity.ADVANCED:
            keep = [strut, *[_P(p).buffer(knob + c.min_wall_thickness) for p in joints[1:-1]]]
            # Window in the pelvis / scapula blade, between the strut and the upper joint.
            mid = 0.5 * (np.asarray(strut.centroid.coords[0]) + joints[0])
            plate = self.bones.lighten(plate, np.linspace(strut.centroid.coords[0], joints[0], 20),
                                       [strut.buffer(c.min_wall_thickness), _P(joints[0]).buffer(radii[0])], max_holes=1)
            del mid
            for i in range(min(n_seg - 1, 2) if n_seg >= 2 else n_seg):
                seg = np.linspace(joints[i], joints[i + 1], 20)
                plate = self.bones.lighten(plate, seg, keep, max_holes=1)
        return plate

    def _limb_seams(self, lb, plate: Polygon, count: int) -> list[tuple[np.ndarray, np.ndarray]]:
        """Seams at the interior joints (knee first, then ankle) where a bowtie fits."""
        joints = self._joints(lb)
        out = []
        for i in range(1, len(joints) - 1)[:count]:
            d = joints[i + 1] - joints[i - 1]
            nd = np.linalg.norm(d)
            if nd < 1e-6:
                continue
            d = d / nd
            bt = self.conn.shape(joints[i], d)
            if self.conn.fits(plate, bt):
                out.append((joints[i], d))
        return out

    def _place_girdle(self, g: dict) -> None:
        sp, c = self.st.spine, self.c
        zL = g["zL"]
        for ds in (0.0, 3.0, -3.0, 6.0, -6.0, 10.0, -10.0, 15.0, -15.0):
            s = g["s"] + ds
            if not (self.s_lo + 2 < s < self.s_hi - 2):
                continue
            f = sp.at(s)
            frame = RibFrame(float(f["point"][0]), float(f["point"][1]), float(f["normal"][0]), float(f["normal"][1]))
            hb = float(np.interp(s, sp.s, self.hb))
            bar = self._bar_key_at(f["point"])
            joins = [(bar, 0.0)] + [(k, 0.0) for k in g["hips"]]
            us = [0.0] + [self.plates[k].z for k in g["hips"]]
            poly = self.spacer.make(us, -c.min_slot_length, hb + c.min_wall_thickness)
            if poly is None:
                continue
            part = CrossPart(self._key("spacer"), poly, frame, 1, PieceRole.SPACER, "limb", joins, s)
            if self._try_cross(part):
                return
        self.warnings.append("A limb girdle could not be attached to the backbone; its limbs were left out.")
        for k in g["hips"]:
            self._drop_limb_chain(k)

    def _drop_limb_chain(self, key: str) -> None:
        z = self.plates[key].z
        chain = {key}
        changed = True
        while changed:
            changed = False
            for bk, a, b in list(self.connectors):
                if (a in chain) != (b in chain) and self.plates.get(a) and self.plates[a].z == z:
                    chain |= {a, b}
                    changed = True
        for k in list(chain):
            self._drop_plate(k)

    def _drop_plate(self, key: str) -> None:
        for bk, a, b in list(self.connectors):
            if key in (a, b):
                self.connectors.remove((bk, a, b))
                self.plates.pop(bk, None)
        self.plates.pop(key, None)

    # ================================================================== ribs & vertebrae

    def _budget(self, n_ribs_max: int) -> tuple[int, int]:
        fixed = len(self.plates) + len(self.crosses)
        target = self.p.piece_count
        left = max(0, target - fixed)
        if self.p.rib_count > 0:
            ribs = self.p.rib_count
            return ribs, max(0, left - ribs)
        ribs = int(np.clip(round(0.55 * left), 3, max(3, n_ribs_max)))
        return ribs, max(0, left - ribs)

    def _ribs_and_vertebrae(self, rib_plan: list[dict]) -> None:
        sp, c = self.st.spine, self.c
        if rib_plan:
            span = rib_plan[-1]["s"] - rib_plan[0]["s"]
            pitch_min = c.slot_width + 2 * c.crown_half_width + 2.0
            n_max = max(1, int(span / pitch_min) + 1)
        else:
            n_max = 0
        n_ribs, n_vert = self._budget(n_max)
        placed_ribs = 0
        if rib_plan and n_ribs:
            targets = np.linspace(rib_plan[0]["s"], rib_plan[-1]["s"], n_ribs) if n_ribs > 1 else [0.5 * (rib_plan[0]["s"] + rib_plan[-1]["s"])]
            step = (rib_plan[-1]["s"] - rib_plan[0]["s"]) / max(n_ribs, 1)
            for s_t in targets:
                for ds in (0.0, 0.2, -0.2, 0.4, -0.4):
                    s = s_t + ds * step
                    rp = min(rib_plan, key=lambda r: abs(r["s"] - s))
                    if self._try_rib(s, rp):
                        placed_ribs += 1
                        break
        if placed_ribs < n_ribs and self.p.rib_count > 0:
            self.warnings.append(f"Only {placed_ribs} of the requested {n_ribs} ribs fit along the body.")
        n_vert += max(0, n_ribs - placed_ribs) // 2

        # Vertebrae along neck and tail (and the rest of the axis outside the rib cage).
        spans = []
        for name in ("neck", "tail"):
            if name in self.st.ranges:
                a, b = self.st.ranges[name]
                a, b = max(a, self.s_lo + 2), min(b, self.s_hi - 2)
                if name == "neck" and self.skull_info:
                    b = min(b, self.st.head.s_start if self.st.head else b)
                if b - a > 2 * c.crown_half_width:
                    spans.append((a, b))
        if not spans and n_vert:
            a, b = self.st.ranges["torso"]
            spans.append((max(a, self.s_lo + 2), min(b, self.s_hi - 2)))
        total = sum(b - a for a, b in spans)
        placed = 0
        pitch_v = max(4.0 * c.crown_half_width, 0.035 * sp.length)
        for a, b in spans:
            n = int(round(n_vert * (b - a) / total)) if total > 0 else 0
            n = min(n, int((b - a) / pitch_v))
            if n <= 0:
                continue
            for s_t in np.linspace(a, b, n + 2)[1:-1]:
                step = (b - a) / (n + 1)
                for ds in (0.0, 0.25, -0.25, 0.45, -0.45):
                    if self._try_vertebra(s_t + ds * step):
                        placed += 1
                        break

    def _try_rib(self, s: float, rp: dict) -> bool:
        sp, c = self.st.spine, self.c
        f = sp.at(s)
        hb = float(np.interp(s, sp.s, self.hb))
        crown_top = hb + c.min_wall_thickness
        if self.volume is not None:
            sec = self._section(s, f, crown_top)
            if sec is not None and self._try_section_rib(s, f, sec, crown_top, rp):
                return True
            reason = "no usable section at the axis" if sec is None else "section rib did not fit"
        bottom = -(f["ventral"] - 0.05 * (f["dorsal"] + f["ventral"]))
        W = min(rp["W"], self.zL - c.thickness - 1.0 - rp["band"] / 2)
        body = self._section(s, f) if self.volume is not None else None
        for scale in (1.0, 0.85, 0.7):
            poly = self.bones.rib_arch(W * scale, bottom, crown_top, rp["band"], self.joint.crown(crown_top))
            if poly is None:
                poly = self.ribs.make(W * scale, bottom, crown_top, rp["band"])
            if poly is None:
                return False
            if body is not None:  # a template fallback still stays inside the measured body
                poly = _clip_to(poly, body, self.joint.crown(crown_top), c.min_feature_size)
                if poly is None or poly.bounds[2] - poly.bounds[0] < 2.0 * (c.crown_half_width + c.min_feature_size):
                    continue
            frame = RibFrame(float(f["point"][0]), float(f["point"][1]), float(f["normal"][0]), float(f["normal"][1]))
            bar = self._bar_key_at(f["point"])
            part = CrossPart(self._key("rib"), poly, frame, 1, PieceRole.RIB, _region_name(f["region"]), [(bar, 0.0)], s)
            if self._try_cross(part):
                if self.volume is not None:
                    self.rib_log.append({"key": part.key, "s": round(float(s), 2), "source": "template",
                                         "fallback_reason": reason, "template_half_width_mm": round(W * scale, 2)})
                return True
        return False

    def _try_section_rib(self, s: float, f: dict, sec: Polygon, crown_top: float, rp: dict) -> bool:
        """Rib cut from the measured section; narrowed (u scale) only as far as a plate that
        crosses this rib plane (a limb, the skull) requires, then 0.85 / 0.7 of that."""
        frame = self._frame_at(f)
        x0, _, x1, _ = sec.bounds
        lim_neg, lim_pos = self._rib_limit(frame)
        k0 = min(1.0, lim_pos / x1 if x1 > 1e-6 else 1.0, lim_neg / -x0 if x0 < -1e-6 else 1.0)
        bar = self._bar_key_at(f["point"])
        crown = self.joint.crown(crown_top)
        for k in (k0, 0.85 * k0, 0.7 * k0):
            if k <= 0.2:
                break
            poly = self.bones.section_rib(sec, crown, rp["band"], u_scale=k)
            if poly is None:
                continue
            part = CrossPart(self._key("rib"), poly, frame, 1, PieceRole.RIB, _region_name(f["region"]), [(bar, 0.0)], s)
            if self._try_cross(part):
                self.rib_log.append({"key": part.key, "s": round(float(s), 2), "source": f"{self.volume.kind}-section",
                                     "u_scale": round(float(k), 3), "section_u_extent_mm": round(x1 - x0, 2),
                                     "section_t_range_mm": [round(sec.bounds[1], 2), round(sec.bounds[3], 2)],
                                     "section_area_mm2": round(sec.area, 1),
                                     # off-centre sections: the body leaves the spine plane here
                                     "section_u_centre_mm": round(sec.centroid.x, 2)})
                return True
        return False

    def _try_vertebra(self, s: float) -> bool:
        sp, c = self.st.spine, self.c
        if not (self.s_lo + 2 < s < self.s_hi - 2):
            return False
        f = sp.at(s)
        hb = float(np.interp(s, sp.s, self.hb))
        crown_top = hb + c.min_wall_thickness
        mf = c.min_feature_size
        spike = max(0.55 * (f["dorsal"] - crown_top), 0.8 * crown_top + 2 * mf)
        wing = float(np.clip(0.45 * f["half_depth"], c.crown_half_width + 2.2 * mf, 2.6 * c.crown_half_width))
        transverse = self.p.complexity != Complexity.EASY
        frame = RibFrame(float(f["point"][0]), float(f["point"][1]), float(f["normal"][0]), float(f["normal"][1]))
        bar = self._bar_key_at(f["point"])
        sec = self._section(s, f) if self.volume is not None else None
        for k in (1.0, 0.7, 0.45, 0.0):
            crown = self.joint.crown(crown_top)
            poly = self.bones.vertebra(crown, crown_top, spike * max(k, 0.5), wing * k,
                                       transverse=transverse and k > 0)
            if poly is not None and sec is not None:
                poly = _clip_to(poly, sec, crown, c.min_feature_size)  # processes stay inside the body
            if poly is None:
                continue
            part = CrossPart(self._key("vertebra"), poly, frame, 1, PieceRole.VERTEBRA, _region_name(f["region"]),
                             [(bar, 0.0)], s)
            if self._try_cross(part):
                return True
        return False

    # ================================================================== measured body (volume)

    def _frame_at(self, f: dict) -> RibFrame:
        return RibFrame(float(f["point"][0]), float(f["point"][1]), float(f["normal"][0]), float(f["normal"][1]))

    def _belly(self, f: dict) -> float:
        """Distance from the axis to the belly line of the side view, ignoring the limbs (a leg
        hanging below the body is not part of the torso section)."""
        if self._axial is None:
            limbs = [lb.polygon for lb in self.st.limbs if lb.polygon is not None and not lb.polygon.is_empty]
            body = self.st.silhouette
            if limbs:
                body = largest_polygon(make_valid(body.difference(unary_union(limbs).buffer(0.5)))) or body
            self._axial = body
        d = _ray_distance(self._axial.boundary, f["point"], -np.asarray(f["normal"]))
        return d if d > 0 else f["ventral"]

    def _section(self, s: float, f: dict, crown_top: float | None = None) -> Polygon | None:
        """Measured body section on the cross plane at s (u = z, t): the part at the axis.
        With `crown_top` it is limited to the torso band (belly line .. crown top) of a rib."""
        if self.volume is None:
            return None
        key = round(float(s), 3)
        if key not in self._sections:
            H = f["dorsal"] + f["ventral"]
            try:
                raw = self.volume.cross_section(self._frame_at(f), -2.0 * H - 10.0, 2.0 * H + 10.0, self.c.thickness)
            except (shapely.errors.GEOSException, ValueError):
                raw = None
            self._sections[key] = make_valid(raw) if raw is not None and not raw.is_empty else None
        region = self._sections[key]
        if region is None:
            return None
        if crown_top is not None:
            region = region.intersection(box(-BIG, -self._belly(f), BIG, crown_top))
        T = self.c.thickness
        parts = [q for q in polygon_parts(region) if q.area > 2.0 * T * T]
        if not parts:
            return None
        o = Point(0.0, 0.0)
        best = min(parts, key=lambda q: q.distance(o))
        if best.distance(o) > self.c.crown_half_width:
            return None  # the body is not at the axis here (thin neck, gap in the mesh)
        return Polygon(best.exterior)

    def _section_widths(self, stations) -> tuple[np.ndarray, np.ndarray]:
        """Outer extent of the torso section on each side (-u, +u) at each station (NaN =
        unknown), measured at up to 25 stations and interpolated."""
        neg, pos = np.full(len(stations), np.nan), np.full(len(stations), np.nan)
        if self.volume is None or len(stations) == 0:
            return neg, pos
        sp, c = self.st.spine, self.c
        idx = np.unique(np.linspace(0, len(stations) - 1, min(25, len(stations))).round().astype(int))
        vals = []
        for i in idx:
            f = sp.at(stations[i])
            crown_top = float(np.interp(stations[i], sp.s, self.hb)) + c.min_wall_thickness
            sec = self._section(stations[i], f, crown_top)
            vals.append((max(-sec.bounds[0], 0.0), max(sec.bounds[2], 0.0)) if sec is not None else (np.nan, np.nan))
        vals = np.asarray(vals)
        ok = np.isfinite(vals[:, 0])
        if ok.any():
            at = np.asarray(stations)[idx][ok]
            neg = np.interp(np.asarray(stations), at, vals[ok, 0])
            pos = np.interp(np.asarray(stations), at, vals[ok, 1])
        return neg, pos

    def _rib_limit(self, frame: RibFrame) -> tuple[float, float]:
        """How far (|u|, per side) a rib on this plane may reach before it meets a plate that
        also crosses the plane (limb plates, skull plates)."""
        c = self.c
        T = c.thickness
        lim = {1: BIG, -1: BIG}
        for pl in self.plates.values():
            if abs(pl.z) < T:
                continue  # the backbone / centre plates are joined, not avoided
            if strip_intervals(frame.to_local(pl.poly), -T / 2.0 - c.clearance, T / 2.0 + c.clearance):
                side = 1 if pl.z > 0 else -1
                lim[side] = min(lim[side], abs(pl.z) - T / 2.0 - c.clearance - 0.5)
        return lim[-1], lim[1]

    def _girdle_depth(self, gid: int, entries) -> dict[int, float]:
        """Depth of a limb group's plates on each side (+z, -z) from the measured body: just
        outside the torso sections the limb plates overlap along the axis on that side, or at
        the measured spread of the limbs on that side when they stand wider."""
        sp, c = self.st.spine, self.c
        s_vals = [sp.project(q) for _, _, hip in entries for q in np.asarray(hip.exterior.coords)[::3]]
        lo, hi = min(s_vals) - 5.0, max(s_vals) + 5.0
        near = [r for r in self.rib_plan if lo <= r["s"] <= hi]
        # A spacer joins the backbone and the limb plate: room for both slots and a wall between.
        z_min = c.slot_width + c.min_wall_thickness + c.thickness
        out, log = {}, {"group": gid, "global_estimate_mm": round(self.zL, 2)}
        for side in (1, -1):
            ext = [r["extent"][side] if np.isfinite(r["extent"][side]) else r["W"] + r["band"] / 2.0 for r in near]
            cage = (max(ext) if ext else c.crown_half_width) + c.thickness + 1.0
            spread = max((self._limb_spread(lb, side) for lb, _, _ in entries), default=0.0)
            # "measured": the legs stand at their real depth (under the body); ribs near the girdle are
            # narrowed to fit between the limb plates (see _rib_limit). "cage": just outside the rib cage.
            out[side] = max(spread, z_min) if (self.limb_mode == "measured" and spread > 0) else max(cage, spread, z_min)
            key = "pos" if side > 0 else "neg"
            log.update({f"zL_{key}_mm": round(out[side], 2), f"cage_{key}_mm": round(cage, 2),
                        f"measured_limb_spread_{key}_mm": round(spread, 2)})
        if all(lb.kind == "appendage" for lb, _, _ in entries):
            # Arms, claws and small fins hang from the body at their own station. The rule above (outside the
            # widest rib near the girdle, or the legs' spread) is right for legs but puts a small appendage
            # beside a part of the body that is far wider than the body it hangs from, so the plates and the
            # crossbar stick out of the body. Clamp them to the local half-depth plus a small margin
            # (never below the depth a spacer needs to clear the backbone plate).
            ext = self._appendage_half_extent([lb for lb, _, _ in entries])
            margin = 0.5 * c.thickness
            for side in (1, -1):
                key = "pos" if side > 0 else "neg"
                limit = max(z_min, ext[side] + margin)
                log.update({f"appendage_local_half_depth_{key}_mm": None if not np.isfinite(ext[side]) else round(ext[side], 2),
                            f"appendage_limit_{key}_mm": round(limit, 2), f"appendage_unclamped_{key}_mm": round(out[side], 2)})
                out[side] = min(out[side], limit)
                log[f"zL_{key}_mm"] = round(out[side], 2)
        self.girdle_log.append(log)
        return out

    def _appendage_half_extent(self, limbs) -> dict[int, float]:
        """Half-depth of the body that an appendage hangs from, per side (+z / -z), measured on the volume
        at points along the first part of each appendage's axis (where it leaves the body); the median of
        the points that fall inside the body. inf (= no clamp) when none does.

        Estimated volume: the depth field there (symmetric about z = 0). Mesh volume: the real z extent of the
        solid along the line through the point, per side, so a body that sits off the z = 0 plane is measured
        where it really is."""
        vol = self.volume
        vals: dict[int, list[float]] = {1: [], -1: []}
        for lb in limbs:
            axis = np.asarray(lb.axis, dtype=float)
            if len(axis) < 2:
                axis = self._joints(lb)
            line = LineString(axis)
            if line.length < 1e-6:
                continue
            for frac in (0.1, 0.3, 0.5):
                q = line.interpolate(frac, normalized=True)
                if vol.kind == "mesh":
                    ivs = vol.z_intervals(float(q.x), float(q.y), half_width=1.0)
                    pos = max((b for a, b in ivs if b > 0), default=0.0)
                    neg = max((-a for a, b in ivs if a < 0), default=0.0)
                else:
                    d = float(vol.depth.sample(np.array([q.x]), np.array([q.y]))[0])
                    pos = neg = d
                if pos > 0:
                    vals[1].append(pos)
                if neg > 0:
                    vals[-1].append(neg)
        return {side: (float(np.median(v)) if v else float("inf")) for side, v in vals.items()}

    def _limb_spread(self, lb, side: int) -> float:
        """Measured |z| of the limb's centre line on one side, below the body (median over its
        lower part); 0 when the body is not a mesh or no limb is found on that side."""
        if self.volume is None or not hasattr(self.volume, "z_intervals"):
            return 0.0
        pts = self._joints(lb)
        line = LineString(pts) if len(pts) >= 2 else None
        if line is None or line.length < 1e-6:
            return 0.0
        vals = []
        for frac in (0.45, 0.6, 0.75):
            q = line.interpolate(frac, normalized=True)
            ivs = self.volume.z_intervals(q.x, q.y, half_width=max(1.0, 0.15 * lb.width_mm))
            centres = [side * 0.5 * (a + b) for a, b in ivs if side * 0.5 * (a + b) > 0]
            if centres:
                vals.append(max(centres))
        return float(np.median(vals)) if vals else 0.0

    # ================================================================== joints

    def _try_cross(self, part: CrossPart) -> bool:
        """Accept a cross piece only if every joint and every 3D relation is valid; then cut.
        A candidate whose boolean operations fail numerically is simply rejected."""
        try:
            return self._try_cross_unsafe(part)
        except (shapely.errors.GEOSException, ValueError):
            logger.debug("cross candidate %s rejected: geometry error", part.key)
            return False

    def _try_cross_unsafe(self, part: CrossPart) -> bool:
        c = self.c
        T = c.thickness
        sw = c.slot_width
        frame = part.frame
        if any(k is None or k not in self.plates for k, _ in part.joins):
            return False
        # (a) the cross piece itself, with its slots
        cutters = [self.joint.cross_cutter(self.plates[k].z, t, part.sign > 0) for k, t in part.joins]
        raw = part.poly
        cut = make_valid(raw.difference(unary_union(cutters)))
        cparts = polygon_parts(cut)
        if len(cparts) != 1:
            return False
        cross_poly = clean(cparts[0])
        if cross_poly is None or cross_poly.area < 4.0 * T * T or narrowest_feature(cross_poly) < c.min_feature_size - 0.05:
            return False
        # (b) no crossing with other cross pieces
        tr = _trace(frame, cross_poly, T / 2.0 + c.clearance / 2.0)
        for other in self.crosses.values():
            if tr.intersects(_trace(other.frame, other.poly, T / 2.0 + c.clearance / 2.0)):
                return False
        # (c) each joined plate: slot through to the edge, engagement, walls, single piece
        new_plates: dict[str, Polygon] = {}
        plate_slots: dict[str, Slot] = {}
        cross_slots: list[Slot] = []
        joints = []
        jbase = len(self.joints)
        for i, (k, t) in enumerate(part.joins):
            pl = self.plates[k]
            local = frame.to_local(pl.poly)
            iv = _interval_containing(line_intervals(local, 0.0), t)
            if iv is None:
                return False
            lo, hi = iv
            below = t - lo if part.sign > 0 else hi - t  # plate material kept against the cross
            depth = hi - t if part.sign > 0 else t - lo  # plate slot depth to its edge
            if below < c.min_slot_length - 1e-6 or depth <= 0.5:
                return False
            ch_local = box(-sw / 2, t, sw / 2, BIG) if part.sign > 0 else box(-sw / 2, -BIG, sw / 2, t)
            channel = frame.to_world(ch_local)
            # Only the part of the channel that lies in the plate matters for walls to other slots
            # and pockets (slices that fan out may converge far above the plate).
            in_plate = channel.intersection(pl.poly.buffer(0.5))
            if any(in_plate.distance(pk) < c.min_wall_thickness for pk in pl.pockets):
                return False
            for s_old in pl.slots:
                if in_plate.distance(s_old.channel()) < c.min_wall_thickness:
                    return False
            rest = make_valid(pl.poly.difference(channel))
            parts = polygon_parts(rest)
            if len(parts) != 1:
                return False
            newp = clean(parts[0])
            if newp is None or narrowest_feature(newp) < c.min_feature_size - 0.05:
                return False
            d = (frame.dx * part.sign, frame.dy * part.sign)
            origin = frame.point(t)
            jid = f"J{jbase + i:03d}"
            slot = Slot(jid, (float(origin[0]), float(origin[1])), (float(d[0]), float(d[1])), float(depth), sw)
            walls = slot_walls(newp, slot, c.min_slot_length)
            if walls and min(walls) < c.min_feature_size:
                return False
            # cross-side slot
            civ = _interval_containing(line_intervals(raw, pl.z), t)
            if civ is None:
                return False
            cdepth = t - civ[0] if part.sign > 0 else civ[1] - t
            cgrip = civ[1] - t if part.sign > 0 else t - civ[0]
            if cdepth <= 0.5 or cgrip < c.min_slot_length - 1e-6:
                return False
            cslot = Slot(jid, (pl.z, t), (0.0, -float(part.sign)), float(cdepth), sw)
            new_plates[k] = newp
            plate_slots[k] = slot
            cross_slots.append(cslot)
            joints.append(Joint(jid, k, part.key, float(origin[0]), float(origin[1]), pl.z,
                                float(min(lo, t - cdepth)), float(max(hi, t + cgrip)), t,
                                float(min(below, cdepth)), True))
        cw = slot_walls(cross_poly, cross_slots[0], c.min_slot_length) if cross_slots else []
        for cs in cross_slots:
            w = slot_walls(cross_poly, cs, c.min_slot_length)
            if w and min(w) < c.min_feature_size:
                return False
        del cw
        # (d) no interpenetration with any plate (joined plates with their new slot)
        for k, pl in self.plates.items():
            poly = new_plates.get(k, pl.poly)
            yl = strip_intervals(frame.to_local(poly), -T / 2, T / 2)
            yt = strip_intervals(cross_poly, pl.z - T / 2, pl.z + T / 2)
            if not yl or not yt:
                continue
            ok = yl[-1][1] <= yt[0][0] + TOL if part.sign > 0 else yt[-1][1] <= yl[0][0] + TOL
            if not ok:
                return False
        # commit
        for k, newp in new_plates.items():
            self.plates[k].poly = newp
            self.plates[k].slots.append(plate_slots[k])
        part.poly = cross_poly
        part.slots = cross_slots
        self.crosses[part.key] = part
        self.joints += joints
        return True

    # ================================================================== finish

    def _key(self, kind: str) -> str:
        self._n += 1
        return f"{kind}-{self._n}"

    def _order_of(self, key: str) -> tuple:
        pl = self.plates.get(key)
        return (ORDER.index(pl.role), pl.sort) if pl else (99,)

    def _region_at(self, s: float) -> str:
        return _region_name(self.st.spine.at(s)["region"])

    def _bar_union(self) -> Polygon:
        return unary_union([pl.poly for pl in self.plates.values() if pl.role == PieceRole.SPINE])

    def _finish(self) -> PuzzleModel:
        p = self.p
        # IDs: role prefix + running number, in a stable anatomical order.
        items: list[tuple] = []
        for k, pl in self.plates.items():
            items.append((ORDER.index(pl.role), pl.sort, k))
        for k, cp in self.crosses.items():
            items.append((ORDER.index(cp.role), (-cp.station,), k))
        items.sort(key=lambda it: (it[0], it[1]))
        counters: dict[PieceRole, int] = {}
        ids: dict[str, str] = {}
        for _, _, k in items:
            role = self.plates[k].role if k in self.plates else self.crosses[k].role
            counters[role] = counters.get(role, 0) + 1
            ids[k] = f"{PREFIX[role]}{counters[role]:02d}"

        pieces: list[Piece] = []
        for _, _, k in items:
            if k in self.plates:
                pl = self.plates[k]
                pieces.append(Piece(ids[k], PieceKind.PROFILE, pl.poly, position_mm=pl.z, slots=list(pl.slots),
                                    role=pl.role, region=pl.region, style="kit"))
            else:
                cp = self.crosses[k]
                pieces.append(Piece(ids[k], PieceKind.CROSS, cp.poly, slots=list(cp.slots), role=cp.role,
                                    region=cp.region, frame=cp.frame, slide_sign=cp.sign, style="kit"))
        for j in self.joints:
            j.profile_id, j.cross_id = ids[j.profile_id], ids[j.cross_id]
        for i, j in enumerate(self.joints):
            new = f"J{i + 1:02d}"
            for pc in pieces:
                for sl in pc.slots:
                    if sl.joint_id == j.id:
                        sl.joint_id = new
            j.id = new
        connectors = [{"id": ids[b], "kind": "bowtie", "pieces": [ids[a], ids[c]]} for b, a, c in self.connectors]

        params = replace(p, piece_count=len(pieces), orientation=Orientation.VERTICAL)
        _set_transforms(pieces, p.thickness_mm)
        warnings = self.warnings
        if p.labels:
            warnings += _add_labels(pieces)
        lo, hi = _bounds3d(pieces, p.thickness_mm)
        size = tuple(round(float(hi[i] - lo[i]), 2) for i in range(3))
        if len(pieces) != p.piece_count:
            warnings.append(f"The fossil kit has {len(pieces)} pieces (target {p.piece_count}): the count follows "
                            "the detected structure and what fits at this size and thickness.")
        roles: dict[str, int] = {}
        for pc in pieces:
            roles[pc.role.value] = roles.get(pc.role.value, 0) + 1
        silhouette = self.st.silhouette
        dy = lo[1]
        analysis = {
            "style": "kit",
            "regions": self.st.anatomy.summary(),
            "head_side": self.st.anatomy.head_side,
            "structure_model": self.st.to_dict(),
            "connectors": connectors,
            "component_counts": roles,
            "shape_source": "estimated",
            "kit_volume": self._volume_report(ids),
            "kit": {
                "ribs": roles.get("rib", 0), "vertebrae": roles.get("vertebra", 0),
                "spine_segments": roles.get("spine", 0), "skull": roles.get("skull", 0) > 0,
                "jaw": roles.get("jaw", 0) > 0, "limb_bones": roles.get("limb", 0),
                "connectors": roles.get("connector", 0), "spacers": roles.get("spacer", 0),
            },
        }
        return PuzzleModel(
            params=params,
            orientation=Orientation.VERTICAL,
            slide=SlideDirection.CROSS_FROM_ABOVE,
            pieces=pieces,
            joints=self.joints,
            silhouette=affinity.translate(silhouette, 0.0, -dy),
            object_size_mm=size,
            warnings=warnings,
            analysis=analysis,
        )


    def _volume_report(self, ids: dict[str, str]) -> dict:
        """Which body drove the cross pieces, per rib: section-based or template fallback."""
        sources: dict[str, int] = {}
        ribs = []
        for r in self.rib_log:
            sources[r["source"]] = sources.get(r["source"], 0) + 1
            if r["key"] in ids:
                ribs.append({"id": ids[r["key"]], **{k: v for k, v in r.items() if k != "key"}})
        if self.volume is None:
            n = sum(1 for cp in self.crosses.values() if cp.role == PieceRole.RIB)
            sources = {"template": n} if n else {}
        return {"kind": self.volume.kind if self.volume is not None else None, "rib_sources": sources,
                "ribs": ribs, "limb_groups": self.girdle_log}


# ---------------------------------------------------------------------------- helpers


def _ray_distance(boundary, p, d, far: float = 1e4) -> float:
    hit = boundary.intersection(LineString([tuple(p), tuple(np.asarray(p) + np.asarray(d) * far)]))
    if hit.is_empty:
        return 0.0
    origin = Point(float(p[0]), float(p[1]))
    return float(min(origin.distance(g) for g in getattr(hit, "geoms", [hit]) if not g.is_empty))


def _clip_to(poly: Polygon, region: Polygon, crown: Polygon, min_feature: float) -> Polygon | None:
    """Part of a cross piece inside the body section, the crown (slot block) always kept."""
    out = make_valid(unary_union([poly.intersection(region), crown]))
    r = 0.5 * min_feature + 0.03
    out = make_valid(unary_union([out.buffer(-r, quad_segs=6).buffer(r, quad_segs=6), crown]))
    parts = [q for q in polygon_parts(out) if q.intersects(crown)]
    return clean(max(parts, key=lambda q: q.area)) if parts else None


def _P(xy):
    from shapely.geometry import Point

    return Point(float(xy[0]), float(xy[1]))


def _region_name(region) -> str:
    r = Region(int(region))
    if r in (Region.CHEST, Region.BELLY, Region.TORSO):
        return "torso"
    return r.label


def _interval_containing(intervals, t: float):
    for lo, hi in intervals:
        if lo - 1e-6 <= t <= hi + 1e-6:
            return lo, hi
    return None


def _interval_at(local: Polygon, t: float, below: bool = False):
    ivs = line_intervals(local, 0.0)
    iv = _interval_containing(ivs, t)
    if iv is None and below:
        lower = [v for v in ivs if v[1] <= t + 1e-6]
        iv = lower[-1] if lower else None
    return iv


def _trace(frame: RibFrame, poly: Polygon, half: float) -> Polygon:
    _, tmin, _, tmax = poly.bounds
    return LineString([frame.point(tmin), frame.point(tmax)]).buffer(half, cap_style="flat")


def _split_bones(plate: Polygon, seams, attach, reach: float = 1e4) -> list[Polygon]:
    bones: list[Polygon] = []
    rest = plate
    for p, d in seams:
        perp = np.array([-d[1], d[0]])
        parts = split_by_segment(rest, p - perp * reach, p + perp * reach)
        near = [q for q in parts if side_of(q.representative_point().coords[0], p, perp) * side_of(attach, p, perp) > 0]
        far = [q for q in parts if side_of(q.representative_point().coords[0], p, perp) * side_of(attach, p, perp) < 0]
        if len(near) != 1 or len(far) != 1:
            break
        a = clean(near[0])
        b = _separate(a, clean(far[0])) if a is not None else None
        if a is None or b is None:
            break
        bones.append(a)
        rest = b
    bones.append(clean(rest) or rest)
    return bones


SEAM_GAP = 0.02


def _separate(a: Polygon | None, b: Polygon | None, gap: float = SEAM_GAP) -> Polygon | None:
    """Two parts of one split, simplified independently, can overlap by a few microns along the
    seam: keep `b` a hair away from `a` so coplanar pieces never overlap."""
    if a is None or b is None:
        return b
    return clean(b.difference(a.buffer(gap))) or b


def _set_transforms(pieces: list[Piece], thickness: float) -> None:
    for pc in pieces:
        if pc.kind == PieceKind.PROFILE:
            origin, u, v = (0.0, 0.0, pc.position_mm), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        else:
            f = pc.frame
            origin, u, v = (f.cx, f.cy, 0.0), (0.0, 0.0, 1.0), (f.dx, f.dy, 0.0)
        n = tuple(float(x) for x in np.cross(u, v))
        pc.transform = Transform3D(origin, u, v, n)
    lo, hi = _bounds3d(pieces, thickness)
    shift = (-(lo[0] + hi[0]) / 2.0, -lo[1], -(lo[2] + hi[2]) / 2.0)
    for pc in pieces:
        o = pc.transform.origin
        pc.transform.origin = (o[0] + shift[0], o[1] + shift[1], o[2] + shift[2])


def _bounds3d(pieces: list[Piece], thickness: float):
    pts = []
    for pc in pieces:
        coords = np.asarray(pc.polygon.exterior.coords)
        for w in (-thickness / 2.0, thickness / 2.0):
            pts.extend(pc.transform.apply(u, v, w) for u, v in coords)
    arr = np.asarray(pts)
    return arr.min(axis=0), arr.max(axis=0)


def _add_labels(pieces: list[Piece]) -> list[str]:
    warnings = []
    for pc in pieces:
        if pc.role == PieceRole.CONNECTOR:
            # Bowtie keys are interchangeable and too small for an ID: not engraved.
            pc.engrave, pc.label_height_mm, pc.label = [], 0.0, ""
            continue
        keep_out = [s.channel(length=s.depth_mm + 2.0, inset=-1.0, extra_width=1.0) for s in pc.slots]
        strokes, height, text = [], 0.0, pc.id
        for text in (pc.id, short_code(pc.id)):
            strokes, height = place_label(pc.polygon, text, keep_out)
            if not strokes:
                strokes, height = place_label(pc.polygon, text, [], margin=1.0, min_height=2.0)
            if strokes:
                break
        if not strokes:
            warnings.append(f"Piece {pc.id} is too small for an engraved label.")
            text = ""
        pc.engrave, pc.label_height_mm, pc.label = strokes, height, text
    return warnings
