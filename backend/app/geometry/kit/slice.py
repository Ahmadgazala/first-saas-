"""SliceGenerator: solid-slice 3D puzzle (the default product style).

Single image -> side profile -> 3D body (reconstructed mesh, or the depth estimate) -> solid
structural slices -> half-lap slots -> validated, nestable laser-cut parts.

Same structure analysis, joint rules and manufacturing checks as the skeleton kit
(`KitGenerator`), a different construction:

  spine plate   a body-shaped plate along the spine curve (z = 0), about 56 % of the body height,
                not a thin bar; segments are joined by bowtie keys when it is longer than allowed
  slices        solid cross-sections of the body perpendicular to the spine, belly to the top of the
                back, as wide as the body at that station; torso, neck and tail alike. Count and
                spacing follow the object: the pitch comes from the material (slot, walls) and the
                Detail setting and tightens where the section changes quickly
  limbs         one flat plate per limb cut from the limb's own outline in the image (feet and claws
                that are wide enough to cut survive), joined to the spine plate by a girdle crossbar
  head          skull plates with eye / nose openings, mouth and teeth where the head is large enough
                (inherited from the kit)

The 3D body comes from `volume`: a MeshVolume gives the exact section of the mesh on every slice
plane, otherwise a DepthVolume built from the silhouette and the (user-scaled) depth estimate.
Nothing here depends on the kind of object; every size derives from the geometry and the material.
"""

from __future__ import annotations

import logging

import numpy as np
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.geometry.anatomy import Region, analyze
from app.geometry.kit.builder import (
    CrossPart,
    KitGenerator,
    _region_name,
    clean,
)
from app.geometry.kit.bones import chaikin
from app.geometry.normalize import fill_small_holes, polygon_parts
from app.geometry.slots import BIG
from app.geometry.types import Complexity, PieceRole, PuzzleModel, RibFrame
from app.geometry.volume import DepthVolume, Volume

logger = logging.getLogger(__name__)

SPINE_BAND = 0.28  # half height of the spine plate as a share of the local body height
SLICE_PITCH = {Complexity.EASY: 2.6, Complexity.MEDIUM: 1.8, Complexity.ADVANCED: 1.15}  # x minimum pitch


class SliceGenerator(KitGenerator):
    traced_openings = 0
    grow_plate_in_narrow = True  # False: keep the plate at body-band height even where no slice can be cut
    _last_dir = None  # plane direction of the previously placed slice (reset per build)

    def __init__(self, params, depth_model=None):
        super().__init__(params, depth_model)
        self.analyzer.thin_appendages = True  # arms, claws and small fins become limbs too
        self.trace_head = True  # mouth, jaw and teeth come from the image outline
        self.invent_openings = False  # eyes / nostrils only where the image has an opening

    def build(self, st, volume=None):
        self._last_dir = None
        self._narrow_cache = None  # narrow regions depend on this structure and volume
        model = super().build(st, volume=volume)
        if self.trace_head and any("skull plates could not be attached" in w for w in model.warnings):
            # The traced mouth cut leaves the backbone outside the upper skull plate: use the generated mouth.
            self.trace_head = False
            try:
                model = super().build(st, volume=volume)
            finally:
                self.trace_head = True
            model.warnings.append("The mouth traced from the image could not be attached to the backbone; a generated mouth was used.")
        return model

    def generate(self, silhouette: Polygon, depth=None, skeleton: dict | None = None,
                 volume: Volume | None = None) -> PuzzleModel:
        # Structure and depth are worked out on the outline with small openings filled (the way every other
        # style sees it); the openings inside the head are put back below as traced eye / nostril cut-outs.
        openings = [Polygon(r) for r in silhouette.interiors]
        silhouette = fill_small_holes(silhouette)
        anatomy = analyze(silhouette)
        if depth is None:
            depth = self.analyzer.depth_model.estimate(silhouette, anatomy)
        if volume is None:
            # No 3D body: the estimated depth, scaled by the user's depth control, is the body.
            depth = depth.scaled(self.p.depth_scale)
        st = self.analyzer.analyze(silhouette, anatomy=anatomy, depth=depth, skeleton=skeleton)
        self._trace_openings(st, [h for h in openings if h.area <= 0.02 * silhouette.area])
        if volume is None:
            volume = DepthVolume(st.silhouette, depth)
        return self.build(st, volume=volume)

    def _trace_openings(self, st, openings: list[Polygon]) -> None:
        """Interior openings of the image outline that lie inside the head (eye sockets, nostrils, skull
        windows) are cut out of the skull plates; nothing is added that the image does not show."""
        c = self.c
        if st.head is None or not openings:
            return
        wall = c.min_wall_thickness
        keep = [h for h in openings
                if h.area >= np.pi * (1.2 * c.min_feature_size) ** 2 and st.head.polygon.buffer(-wall).contains(h.buffer(wall))]
        if keep:
            cut = clean(st.head.polygon.difference(unary_union(keep)))
            if cut is not None and len(cut.interiors) >= 1:
                st.head.polygon = cut
                self.traced_openings = len(keep)

    # ================================================================== spine plate

    def _min_slice_width(self) -> float:
        """Narrowest body width (z) that still takes a slice: a slot with a wall of the minimum
        feature size on both sides."""
        return self.c.slot_width + 2.0 * self.c.min_feature_size + 0.5

    def _narrow(self) -> np.ndarray:
        """Spine samples where the body is too thin in depth for a valid slot (thin tail, neck, tips).

        Two different measurements of "how wide is the body here", chosen by the kind of volume:

        * estimated path (DepthVolume): the body is the silhouette inflated symmetrically about z = 0, so
          2 x the depth field at the spine point IS the width of the slice that would be cut there. Unchanged.
        * mesh path (MeshVolume): `spine.half_depth` is min(up, down) of the solid around the plane z = 0.
          A real body is rarely centred on that plane (a reconstruction can sit entirely on one side), so
          the proxy collapses to a few mm where the section is tens of mm wide (measured: 5.9 mm vs 15.5 mm
          on a ground-truth dinosaur, 4.8 mm vs 64 mm on a penguin) and valid slices were skipped. Here the
          width is the real extent of the mesh section on the slice plane (`_slice_section`), the same
          section the slice itself is cut from."""
        cached = getattr(self, "_narrow_cache", None)
        if cached is not None:
            return cached
        sp = self.st.spine
        if self.volume is not None and self.volume.kind == "mesh":
            width = self._mesh_section_width()
        else:
            width = 2.0 * np.maximum(sp.half_depth, 0.0)
        self._narrow_cache = (width < self._min_slice_width()) & (sp.region != int(Region.HEAD))
        return self._narrow_cache

    def _mesh_section_width(self) -> np.ndarray:
        """Real width (z extent) of the mesh section on the perpendicular plane at every spine sample, from
        up to 64 measured stations (linear in between). 0 where the section does not contain the axis: no
        slice (which must straddle the backbone plate) can be cut there."""
        sp = self.st.spine
        n = len(sp.s)
        idx = np.unique(np.round(np.linspace(0, n - 1, min(n, 64))).astype(int))
        wide = 10.0 * self._min_slice_width()  # the head is never narrow-checked: any value above the limit
        w = np.empty(len(idx))
        for j, i in enumerate(idx):
            if sp.region[i] == int(Region.HEAD):
                w[j] = wide
                continue
            f = sp.at(float(sp.s[i]))
            sec = self._slice_section(self._frame_at(f), f)
            w[j] = 0.0 if sec is None else sec.bounds[2] - sec.bounds[0]
        return np.interp(sp.s, sp.s[idx], w)

    def _hb(self) -> np.ndarray:
        """Half height of the spine plate: about half the body height. Where the body is too thin in depth
        for any slice, the plate takes over and grows to (almost) the full local height, so the side
        silhouette and proportions of a thin tail or neck are kept without forcing impossible slots."""
        sp = self.st.spine
        H = sp.dorsal + sp.ventral
        hmin = self.c.bar_half_height_min
        thin = np.minimum(sp.dorsal, sp.ventral)
        k = np.where(sp.region == int(Region.HEAD), 0.12, SPINE_BAND)
        hb = np.clip(k * H, hmin, np.maximum(hmin, 0.6 * thin))
        narrow = self._narrow().astype(float)
        if narrow.any() and self.grow_plate_in_narrow:
            win = max(3, int(round(8.0 / max(float(np.mean(np.diff(sp.s))), 1e-6))) | 1)
            w = np.clip(np.convolve(np.pad(narrow, win // 2, mode="edge"), np.ones(win) / win, mode="valid"), 0.0, 1.0)
            w = np.where(narrow > 0, 1.0, w)
            hb = hb * (1.0 - w) + np.maximum(hb, np.maximum(hmin, 0.92 * thin)) * w
        return hb

    # ================================================================== limbs

    def _hip_plate(self, lb, f: dict, hb: float) -> Polygon | None:
        """The limb's own outline from the image (smoothed, features below the minimum removed),
        joined to the girdle strut through a blade around the first joint."""
        from shapely.geometry import LineString

        c = self.c
        pt, n = f["point"], f["normal"]
        lo = pt - n * (c.min_slot_length + c.min_wall_thickness + 1.0)
        hi = pt + n * (hb + c.min_wall_thickness)
        strut = LineString([tuple(lo), tuple(hi)]).buffer(c.crown_half_width + c.min_wall_thickness, cap_style="flat")
        joints = self._joints(lb)
        outline = lb.polygon
        if outline is None or outline.is_empty:
            return super()._hip_plate(lb, f, hb)
        body = clean(chaikin(outline, 2)) or outline
        r = 0.5 * c.min_feature_size + 0.03
        opened = clean(body.buffer(-r, join_style="round").buffer(r, join_style="round"))
        body = opened if opened is not None and opened.area > 0.6 * body.area else body
        blade = self.bones.blade(strut, joints[0], max(0.5 * lb.width_mm, 3.0 * c.min_feature_size))
        parts = [body, strut] + ([blade] if blade is not None else [])
        return clean(unary_union(parts))

    def _limb_seams(self, lb, plate: Polygon, count: int):
        return []  # one plate per limb: no bowtie seams

    # ================================================================== slices

    def _ribs_and_vertebrae(self, rib_plan: list[dict]) -> None:
        """Slices from one end of the axis to the other. Each next slice is placed one local gap beyond
        the previous *placed* slice (never closer, so shifts cannot pile up); a station that cannot be cut
        (a limb or skull plate, a section too thin for a slot) is skipped after a few forward tries."""
        sp, c = self.st.spine, self.c
        a = self.s_lo + 0.5 * c.crown_half_width
        b = self.s_hi - 0.5 * c.crown_half_width
        if self.skull_info and self.st.head:
            b = min(b, self.st.head.s_start - 0.5 * c.thickness)  # the head belongs to the skull plates
        if b - a < 2.0 * c.crown_half_width:
            return
        cap = self.p.rib_count if self.p.rib_count > 0 else max(4, self.p.piece_count - len(self.plates) - len(self.crosses))
        ss, gap = self._gap_profile(a, b, cap)
        placed = planned = skipped = 0
        s = a
        narrow = self._narrow()
        while s <= b and placed < cap:
            g = float(np.interp(s, ss, gap))
            if narrow[sp.index(s)]:  # too thin for a slot: the spine plate covers this part (see _hb)
                skipped += 1
                s += 0.5 * g
                continue
            planned += 1
            hit = None
            for off in (0.0, 0.12, 0.25, 0.4):
                if self._try_slice(s + off * g):
                    hit = s + off * g
                    break
            if hit is not None:
                placed += 1
                s = hit + g
            else:
                s += 0.5 * g
        if placed < 0.7 * planned:
            self.warnings.append(f"{placed} slices fit along the body (the rest of the stations collided with limb or "
                                 "skull plates or were too thin to cut at this material thickness).")
        self.slice_log = {"planned": planned, "placed": placed, "range_mm": [round(a, 1), round(b, 1)],
                          "narrow_stations_skipped": skipped, "narrow_share": round(float(narrow.mean()), 3)}

    def _gap_profile(self, a: float, b: float, cap: int) -> tuple[np.ndarray, np.ndarray]:
        """Desired spacing along the axis: the material-derived pitch (slot + wall + margin, times the
        Detail factor) divided by a density that rises where the section (height x width) changes
        quickly, never below the physical minimum; stretched uniformly if more than `cap` slices
        would be needed."""
        sp, c = self.st.spine, self.c
        pmin = c.slot_width + c.min_wall_thickness + 2.0
        pitch = pmin * SLICE_PITCH[self.p.complexity]
        ss = np.linspace(a, b, 240)
        size = np.array([max((f["dorsal"] + f["ventral"]) * max(f["half_depth"], 1.0), 1.0)
                         for f in (sp.at(s) for s in ss)])
        change = np.abs(np.gradient(np.log(size), ss))  # relative change of the section per mm
        rho = 1.0 + 0.4 * np.minimum(1.0, 4.0 * change * pitch)  # up to 40 % denser where the section changes
        scale = 1.0
        for _ in range(3):
            gap = np.maximum(pmin * 1.05, pitch * scale / rho)
            expected = float(np.trapezoid(1.0 / gap, ss))
            if expected <= cap:
                break
            scale *= expected / cap
        return ss, gap

    def _slice_section(self, frame: RibFrame, f: dict) -> Polygon | None:
        """Whole cross-section of the body on `frame`'s plane at the spine point: from the belly line
        (limbs left out) to the top of the back, the part of the section that contains the axis."""
        from app.geometry.kit.builder import _ray_distance

        pt = np.asarray(f["point"], float)
        d = np.array([frame.dx, frame.dy])
        self._belly(f)  # builds the limb-free body outline
        belly = _ray_distance(self._axial.boundary, pt, -d) or f["ventral"]
        top = _ray_distance(self.st.silhouette.boundary, pt, d) or f["dorsal"]
        try:
            raw = self.volume.cross_section(frame, -belly - 8.0, top + 8.0, self.c.thickness)
        except Exception:  # degenerate section: this orientation is not usable
            return None
        if raw is None or raw.is_empty:
            return None
        region = make_valid(raw).intersection(box(-BIG, -belly, BIG, top + 0.5))
        T = self.c.thickness
        parts = [q for q in polygon_parts(region) if q.area > 2.0 * T * T]
        if not parts:
            return None
        o = Point(0.0, 0.0)
        best = min(parts, key=lambda q: q.distance(o))
        return Polygon(best.exterior) if best.distance(o) <= self.c.crown_half_width else None

    def _try_slice(self, s: float) -> bool:
        """One slice at spine station `s`. Perpendicular to the spine first; if it would hit its
        neighbours (a curved spine makes perpendicular slices converge on the inside) the plane is
        tilted, or set parallel to the previous slice, until a collision-free plane is found."""
        sp, c = self.st.spine, self.c
        if not (self.s_lo < s < self.s_hi):
            return False
        f = sp.at(s)
        n = np.asarray(f["normal"], float)
        bar = self._bar_key_at(f["point"])
        tail = self.st.ranges.get("tail")
        role = PieceRole.VERTEBRA if tail and tail[0] <= s <= tail[1] else PieceRole.RIB
        dirs = [n]
        for deg in (10, -10, 20, -20, 32, -32, 45, -45):
            a = np.radians(deg)
            dirs.append(np.array([n[0] * np.cos(a) - n[1] * np.sin(a), n[0] * np.sin(a) + n[1] * np.cos(a)]))
        if self._last_dir is not None and np.dot(self._last_dir, n) > 0.3:
            dirs.insert(1, self._last_dir)
        for d in dirs:
            frame = RibFrame(float(f["point"][0]), float(f["point"][1]), float(d[0]), float(d[1]))
            sec = self._slice_section(frame, f)
            if sec is None:
                continue
            x0, _, x1, _ = sec.bounds
            lim_neg, lim_pos = self._rib_limit(frame)
            k0 = min(1.0, lim_pos / x1 if x1 > 1e-6 else 1.0, lim_neg / -x0 if x0 < -1e-6 else 1.0)
            for k in (k0, 0.85 * k0, 0.7 * k0):
                if k <= 0.25:
                    break
                poly = self.bones.slice_plate(sec, u_scale=k)
                if poly is None:
                    continue
                part = CrossPart(self._key("rib" if role == PieceRole.RIB else "vertebra"), poly, frame, 1, role,
                                 _region_name(f["region"]), [(bar, 0.0)], s)
                if self._try_cross(part):
                    self._last_dir = d
                    tilt = float(np.degrees(np.arccos(np.clip(np.dot(d, n), -1, 1))))
                    self.rib_log.append({"key": part.key, "s": round(float(s), 2), "source": f"{self.volume.kind}-section",
                                         "u_scale": round(float(k), 3), "section_u_extent_mm": round(x1 - x0, 2),
                                         "section_t_range_mm": [round(sec.bounds[1], 2), round(sec.bounds[3], 2)],
                                         "section_area_mm2": round(sec.area, 1), "tilt_deg": round(tilt, 1),
                                         "section_u_centre_mm": round(sec.centroid.x, 2)})
                    return True
        return False

    # ================================================================== output

    def _finish(self) -> PuzzleModel:
        model = super()._finish()
        model.analysis["style"] = "slice"
        model.analysis["slices"] = getattr(self, "slice_log", {"planned": 0, "placed": 0})
        model.analysis["traced"] = {"head_openings": getattr(self, "traced_openings", 0),
                                    "arms": sum(1 for lb in self.st.limbs if lb.kind == "appendage"),
                                    "mouth": any("traced from the image outline" in w for w in self.warnings)}
        model.analysis["kit"]["slices"] = sum(1 for cp in self.crosses.values() if cp.role in (PieceRole.RIB, PieceRole.VERTEBRA))
        return model
