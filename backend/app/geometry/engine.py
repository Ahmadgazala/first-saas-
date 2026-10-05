"""Procedural slot-together puzzle generator (structural engine).

Pieces
------
* SPINE (LONG-01): the backbone. The full silhouette at z = 0; it follows the centreline
  head -> neck -> torso -> tail and carries the legs.
* PROFILE plates (LONG-02...): torso side profiles at ±z, the regions where the stylized body
  is thicker than |z| + T/2, so their outer faces stay inside the body envelope.
* DETAIL plates (DETAIL-HEAD-01, DETAIL-BODY-01, ...): localized plates at ±z for regions
  that have their own volume (cheeks of a head, a haunch, a chest) - real pieces taken from
  region-specific level sets of the depth field.
* CROSS ribs (CROSS-01...): body cross-sections. Each lies in a plane that contains the
  depth axis and is either vertical (torso, head) or perpendicular to the local skeleton
  axis (necks, tails, legs, trunks). Its outline is the silhouette section on that plane
  widened to ±D by the region-aware depth model.

Placement is adaptive: candidate ribs carry an importance weight (region, outline
curvature, rate of change of the section) and are chosen by weighted farthest-point
sampling, so ribs concentrate on heads, necks, transitions and curvy outlines while flat
parts of the torso get fewer. Hard constraints keep every pair of rib traces far enough
apart for slot walls and prevent non-parallel ribs from crossing.

Joints
------
Every profile/plate at z and every cross rib (frame c, d) meet along the line
{c + t d, z}. Where both slabs have material on that line, a half-lap joint splits the
shared interval [p, q] at m. The plate loses the strip |s| <= W/2 from m through the edge
on the side the rib is inserted from, the rib loses |u - z| <= W/2 from m through its edge on
the other side. Inside every intersection box each piece therefore has material on one
side of m only: nothing can interpenetrate, and each rib slides in along its own trace.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import cv2
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.validation import make_valid

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.anatomy import TORSO_LIKE, Anatomy, Region, analyze
from app.geometry.depth import AnatomicalDepthModel, DepthField, DepthModel
from app.geometry.labels import place_label, short_code
from app.geometry.normalize import largest_polygon, polygon_parts
from app.geometry.sections import core_intervals, intersect, line_intervals, measure, strip_intervals
from app.geometry.slots import BIG, slot_cutter, slot_split
from app.geometry.volume import DepthVolume, MeshVolume, Volume
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
    Style,
    Transform3D,
)

logger = logging.getLogger(__name__)

# All piece coordinates are snapped to this grid (mm). Slot positions are rounded to 0.001 mm
# and slot half-widths are multiples of 0.0005 mm, so slot edges survive snapping exactly.
GRID = 0.0005

# Importance of a cross section by region (where a designer would spend pieces).
REGION_WEIGHT = {
    Region.HEAD: 2.2,
    Region.NECK: 1.6,
    Region.CHEST: 1.25,
    Region.TORSO: 1.0,
    Region.BELLY: 1.0,
    Region.LIMB: 1.2,
    Region.TAIL: 1.3,
    Region.EXTREMITY: 1.5,
}


@dataclass(frozen=True)
class DetailProfile:
    """How a complexity level distributes structure.

    curvature/change: influence of outline curvature and section change on rib density.
    focus: exponent of the importance weight in farthest-point sampling (0 = uniform).
    torso_levels: area fractions of the torso covered by successive side-profile pairs.
    head_plates / extra_plates: allow head plates / other localized plates.
    tilted: allow ribs perpendicular to necks, tails and legs.
    """

    curvature: float
    change: float
    focus: float
    torso_levels: tuple[float, ...]
    head_plates: bool
    extra_plates: bool
    tilted: bool


DETAIL = {
    Complexity.EASY: DetailProfile(0.15, 0.15, 0.25, (0.5,), False, False, False),
    Complexity.MEDIUM: DetailProfile(0.8, 0.6, 1.0, (0.5,), True, False, True),
    Complexity.ADVANCED: DetailProfile(1.4, 1.0, 1.6, (0.62, 0.3), True, True, True),
}


class ConfigurationFailed(Exception):
    """A particular configuration is not manufacturable; try another one."""

    def __init__(self, code: ErrorCode, message: str, plate: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.plate = plate  # index of the plate pair that caused the failure, if any


@dataclass
class PlateSpec:
    z: float  # positive; the pair is placed at ±z
    polygon: Polygon  # plate at +z
    region: Region
    role: PieceRole
    priority: float
    polygon_minus: Polygon | None = None  # plate at -z when the body is not symmetric (real meshes)

    @property
    def minus(self) -> Polygon:
        return self.polygon_minus if self.polygon_minus is not None else self.polygon


@dataclass
class RibCandidate:
    frame: RibFrame
    a: float  # rib extent along t (the silhouette section through c)
    b: float
    up: bool  # inserted from the +d side
    engagement: float
    region: Region
    weight: float = 1.0
    tilted: bool = False
    trace: LineString | None = None
    footprint: Polygon | None = None
    meta: dict = field(default_factory=dict)


def canonical_silhouette(silhouette: Polygon, orientation: Orientation) -> Polygon:
    """Display frame -> canonical frame (x = default slicing axis)."""
    if orientation == Orientation.HORIZONTAL:
        return affinity.rotate(silhouette, -90.0, origin=(0.0, 0.0))
    return silhouette


def _display_vec(v: tuple[float, float, float], orientation: Orientation) -> tuple[float, float, float]:
    """Canonical -> display rotation (inverse of canonical_silhouette)."""
    if orientation == Orientation.HORIZONTAL:
        return (-v[1], v[0], v[2])
    return v


class PuzzleGenerator:
    def __init__(self, params: PuzzleParameters, depth_model: DepthModel | None = None):
        self.p = params
        self.depth_model = depth_model or AnatomicalDepthModel()
        self.detail = DETAIL[params.complexity]
        self._validate_params()
        self._analysis_cache: dict[Orientation, tuple[Polygon, Anatomy, DepthField, Volume]] = {}
        self._volume: Volume | None = None
        self._chosen_footprints: list[Polygon] = []

    def _analyse(self, source, orientation: Orientation) -> tuple[Polygon, Anatomy, DepthField, Volume]:
        """Canonical silhouette, anatomy, depth estimate and volume for one slicing orientation.

        `source` is either a silhouette polygon (the body is estimated from it) or a
        trimesh mesh already in the display frame (sliced exactly)."""
        if orientation not in self._analysis_cache:
            if isinstance(source, Polygon):
                canon = canonical_silhouette(source, orientation)
                anatomy = analyze(canon)
                depth = self.depth_model.estimate(canon, anatomy)
                volume: Volume = DepthVolume(canon, depth)
            else:
                mesh = source.copy()
                if orientation == Orientation.HORIZONTAL:
                    rot = np.eye(4)
                    rot[:2, :2] = [[0.0, 1.0], [-1.0, 0.0]]  # same as rotating the silhouette by -90°
                    mesh.apply_transform(rot)
                volume = MeshVolume(mesh)
                canon = volume.silhouette
                anatomy = analyze(canon)
                depth = volume.depth
            self._analysis_cache[orientation] = (canon, anatomy, depth, volume)
        return self._analysis_cache[orientation]

    # ------------------------------------------------------------------ public API

    def generate(self, silhouette: Polygon) -> PuzzleModel:
        """Puzzle from a silhouette: the 3D body is estimated (region-aware inflation)."""
        return self._checked(lambda: self._generate(silhouette, silhouette))

    def generate_from_mesh(self, mesh) -> PuzzleModel:
        """Puzzle from a real 3D mesh in the display frame (x right, y up, z depth, mm),
        e.g. from `volume.prepare_mesh`. Pieces are exact plane sections of the mesh."""
        display_silhouette = MeshVolume(mesh).silhouette
        return self._checked(lambda: self._generate(mesh, display_silhouette))

    def _checked(self, build) -> PuzzleModel:
        """Skeleton style cuts material away after the joints are planned. Should an opening
        ever weaken a joint, step back (backbone cut first, then the windows) instead of
        shipping a model that fails validation."""
        from app.validators.geometry_validator import validate_model

        self._skeleton_steps = {"backbone", "windows"}
        model = build()
        if self.p.style == Style.SKELETON:
            for drop in ("backbone", "windows"):
                if validate_model(model).ok:
                    break
                logger.info("skeleton %s weakened a joint: retrying without it", drop)
                self._skeleton_steps.discard(drop)
                model = build()
            model.analysis["skeleton"] = sorted(self._skeleton_steps)
        model.analysis["style"] = self.p.style.value
        return model

    def _generate(self, source, display_silhouette: Polygon) -> PuzzleModel:
        orientations = (
            self._rank_orientations(source) if self.p.orientation == Orientation.AUTO else [self.p.orientation]
        )
        failures: list[ConfigurationFailed] = []
        for orientation in orientations:
            canon, anatomy, depth, volume = self._analyse(source, orientation)
            self._volume = volume
            plates = self._plate_candidates(canon, anatomy, depth, volume)
            candidate_cache: dict[bool, list[RibCandidate]] = {}
            plan = self._initial_plan(plates)
            for _ in range(12):
                spine, plate_polys, legs_moved = self._structure(canon, anatomy, plan, volume)
                self._body = canon
                if self.p.style == Style.SKELETON and "backbone" in self._skeleton_steps:
                    spine = self._backbone(spine, anatomy, legs_moved)
                if legs_moved not in candidate_cache:
                    skip = legs_moved or volume.kind == "mesh"
                    candidate_cache[legs_moved] = self._rib_candidates(spine, anatomy, depth, skip_limbs=skip)
                cands = candidate_cache[legs_moved]
                if not cands:
                    failures.append(ConfigurationFailed(
                        ErrorCode.INVALID_GEOMETRY,
                        "The shape has no region thick enough for interlocking slots. "
                        "Increase the puzzle size or use thinner material.",
                    ))
                    break
                vertical_only = [c for c in cands if not c.tilted]
                build = (display_silhouette, canon, spine, plate_polys, anatomy, depth, orientation, plan)
                try:
                    try:
                        model = self._build(*build, cands)
                    except ConfigurationFailed as exc:
                        if exc.code != ErrorCode.TOO_MANY_PIECES or len(vertical_only) == len(cands):
                            raise
                        # Ribs across necks/tails/legs use space; parallel ribs pack tighter.
                        model = self._build(*build, vertical_only)
                    if legs_moved:
                        model.analysis["legs_on_side_profiles"] = True
                    model.analysis["shape_source"] = volume.kind
                except ConfigurationFailed as exc:
                    logger.info("configuration %s/%d plates rejected: %s", orientation, len(plan), exc.message)
                    failures.append(exc)
                    if not plan:
                        break
                    # Drop the failing plate pair (or the least important one) and retry.
                    drop = exc.plate if exc.plate is not None and exc.plate < len(plan) else len(plan) - 1
                    plan = plan[:drop] + plan[drop + 1:]
                    continue
                return model
        failures.sort(key=lambda f: 0 if f.code == ErrorCode.TOO_MANY_PIECES else 1)
        first = failures[0] if failures else ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, "No configuration")
        raise PuzzleError(first.code, first.message, "; ".join(f.message for f in failures[:8]))

    # ------------------------------------------------------------------ configuration

    def _validate_params(self) -> None:
        p = self.p
        checks = [
            (0.5 <= p.thickness_mm <= 25.0, "Material thickness must be between 0.5 and 25 mm."),
            (0.0 <= p.kerf_mm <= 1.0, "Kerf must be between 0 and 1 mm."),
            (0.0 <= p.clearance_mm <= 1.0, "Clearance must be between 0 and 1 mm."),
            (p.kerf_mm < p.slot_width_mm, "Kerf must be smaller than the slot width."),
            (3 <= p.piece_count <= 60, "Number of pieces must be between 3 and 60."),
            (40.0 <= p.target_size_mm <= 2000.0, "Puzzle size must be between 40 and 2000 mm."),
            (p.target_size_mm >= 12 * p.thickness_mm, "Puzzle size is too small for this material thickness."),
            (p.sheet_width_mm > 0 and p.sheet_height_mm > 0, "Sheet dimensions must be positive."),
            (p.part_spacing_mm >= p.kerf_mm, "Part spacing must be at least the kerf width."),
        ]
        for ok, message in checks:
            if not ok:
                raise PuzzleError(ErrorCode.INVALID_SETTINGS, message)

    def _rank_orientations(self, source) -> list[Orientation]:
        scores = {}
        for orientation in (Orientation.VERTICAL, Orientation.HORIZONTAL):
            canon, anatomy, depth, _ = self._analyse(source, orientation)
            cands = self._sweep_candidates(canon, anatomy, depth)
            span = (max(c.frame.cx for c in cands) - min(c.frame.cx for c in cands)) if cands else 0.0
            # Animals read best when sliced across the body axis (legs down).
            bonus = 1.25 if anatomy.head_side and orientation == Orientation.VERTICAL else 1.0
            scores[orientation] = span * (1.0 + len(cands) / 1000.0) * bonus
        return sorted(scores, key=lambda o: (-scores[o], o != Orientation.VERTICAL))

    def _min_cross_spacing(self) -> float:
        p = self.p
        return max(p.slot_width_mm + p.min_wall_mm, 2.0 * p.thickness_mm, 8.0)

    def _min_level_gap(self) -> float:
        return self.p.slot_width_mm + self.p.min_wall_mm

    # ------------------------------------------------------------------ plates

    def _level_z(self, values: np.ndarray, frac: float, floor: float) -> float | None:
        """Smallest z whose plate (D >= z + T/2) still covers `frac` of the given depth samples."""
        if values.size == 0:
            return None
        vals = np.sort(values)[::-1]
        k = min(len(vals) - 1, int(frac * len(vals)))
        z = float(vals[k]) - self.p.thickness_mm / 2.0
        return round(max(z, floor), 3)

    def _plate_candidates(
        self, canon: Polygon, anatomy: Anatomy, depth: DepthField, volume: Volume
    ) -> list[PlateSpec]:
        p, det = self.p, self.detail
        gap = self._min_level_gap()
        torso_vals = _depth_in(depth, anatomy, TORSO_LIKE)
        if torso_vals.size == 0:
            torso_vals = depth.values[depth.values > 0]
        levels: list[tuple[float, str]] = []
        prev = 0.0
        torso_levels = det.torso_levels[:1] if self.p.style == Style.SKELETON else det.torso_levels
        for frac in torso_levels:
            z = self._level_z(torso_vals, frac, prev + gap)
            if z is None or z + p.thickness_mm / 2.0 >= float(torso_vals.max()) - 0.5:
                break
            levels.append((z, "torso"))
            prev = z
        if det.head_plates and anatomy.region_area((Region.HEAD,)) > 0:
            head_vals = _depth_in(depth, anatomy, (Region.HEAD, Region.NECK))
            zh = self._level_z(head_vals, 0.4, gap)
            if head_vals.size and zh is not None and zh + p.thickness_mm / 2.0 < float(head_vals.max()):
                near = [z for z, _ in levels if abs(z - zh) < gap]
                if near:
                    zh = near[0]  # share the torso level: its head component is the head plate
                if all(abs(z - zh) >= gap or z == zh for z, _ in levels) and zh not in [z for z, _ in levels]:
                    levels.append((zh, "head"))
        if volume.kind == "mesh":
            zl = self._mesh_leg_level(volume, anatomy, gap)
            if zl is not None:
                # In a real model the legs sit beside the spine: put the first side profile there.
                near = [i for i, (z, _) in enumerate(levels) if abs(z - zl) < gap]
                for i in sorted(near, reverse=True):
                    levels.pop(i)
                levels.insert(0, (zl, "torso"))
                levels.sort(key=lambda lv: (lv[1] != "torso", lv[0]))
        min_area = max(6.0 * p.thickness_mm**2, 0.004 * canon.area)
        head_zone = None
        if any(purpose == "head" for _, purpose in levels):
            from app.geometry.depth import raster_to_geometry

            zone = anatomy.region_mask((Region.HEAD, Region.NECK))
            # Reach a little into the chest so the plate can share ribs with the body.
            grow = max(1, int(round(4.0 * self._min_level_gap() / anatomy.res)))
            zone = cv2.dilate(zone.astype(np.uint8), np.ones((grow, grow), np.uint8)) > 0
            head_zone = raster_to_geometry(zone, anatomy.x0, anatomy.y0, anatomy.res)
        specs: list[PlateSpec] = []
        for li, (z, purpose) in enumerate(levels):
            sides = []
            for zz in (z, -z):
                region = volume.profile_section(zz, p.thickness_mm)
                if region is None:
                    break
                region = make_valid(region.intersection(canon))
                if purpose == "head":
                    if head_zone is None:
                        break
                    region = make_valid(region.intersection(head_zone))
                r = p.min_feature_mm / 2.0
                sides.append([pp for pp in polygon_parts(region.buffer(-r).buffer(r)) if pp.area >= min_area])
            if len(sides) < 2 or not sides[0] or not sides[1]:
                continue
            parts, minus_parts = sides
            largest_area = max(pp.area for pp in parts)
            for raw_part in parts:
                is_largest = raw_part.area == largest_area
                part = _inside(raw_part.simplify(0.1), canon)
                if part is None:
                    continue
                reg = _majority_region(anatomy, part)
                if is_largest and purpose == "torso":
                    role, prio = PieceRole.PROFILE, 100.0 - li
                elif purpose == "head" or reg in (Region.HEAD, Region.NECK):
                    reg = Region.HEAD if reg in TORSO_LIKE else reg
                    if not det.head_plates:
                        continue
                    role, prio = PieceRole.DETAIL, 50.0 + part.area / canon.area
                else:
                    if not det.extra_plates or self.p.style == Style.SKELETON:
                        continue
                    role, prio = PieceRole.DETAIL, 10.0 + part.area / canon.area
                partner = _matching_part(raw_part, minus_parts)
                if partner is None:
                    continue  # no counterpart on the other side: the pair would be lopsided
                minus = None if volume.kind != "mesh" else _inside(partner.simplify(0.1), canon)
                if volume.kind == "mesh" and minus is None:
                    continue
                specs.append(PlateSpec(z, orient(part, 1.0), reg, role, prio,
                                       orient(minus, 1.0) if minus is not None else None))
        specs.sort(key=lambda s: -s.priority)
        return specs

    def _mesh_leg_level(self, volume: Volume, anatomy: Anatomy, gap: float) -> float | None:
        """Depth at which a real model's legs stand: the level with the most leg material."""
        from app.geometry.depth import raster_to_geometry

        legs = anatomy.region_mask((Region.LIMB,))
        if not legs.any():
            return None
        zone = raster_to_geometry(legs, anatomy.x0, anatomy.y0, anatomy.res)
        lo, hi = volume.z_extent()
        best, best_area = None, 0.0
        t = self.p.thickness_mm
        for z in np.arange(gap, min(-lo, hi) - t / 2.0, max(0.5, t / 2.0)):
            area = 0.0
            for zz in (z, -z):
                sec = volume.profile_section(float(zz), t)
                if sec is not None:
                    area += make_valid(sec.intersection(zone)).area
            if area > best_area:
                best, best_area = round(float(z), 3), area
        return best

    def _initial_plan(self, specs: list[PlateSpec]) -> list[PlateSpec]:
        n = self.p.piece_count
        min_cross = max(2, math.ceil(0.45 * n), 3 if n >= 6 else 2)
        budget = max(0, (n - 1 - min_cross) // 2)
        if self.p.complexity == Complexity.EASY:
            budget = min(budget, 1 if n >= 12 else 0)
        return specs[:budget]

    # ------------------------------------------------------------------ legs

    def _structure(
        self, canon: Polygon, anatomy: Anatomy, plan: list[PlateSpec], volume: Volume | None = None
    ) -> tuple[Polygon, dict[int, tuple[Polygon, Polygon]], bool]:
        """Decide which piece carries the legs.

        With torso side profiles available, legs move from the spine to the innermost pair of
        side profiles, like the side panels of a real animal kit: one leg of each front/back
        pair per side (a single drawn leg goes on both sides). Each leg is bridged up into
        its profile through the silhouette above it. The spine then ends at the belly.
        Falls back to legs on the spine whenever a leg cannot be attached cleanly.
        """
        from app.geometry.depth import raster_to_geometry

        plates = {k: (spec.polygon, spec.minus) for k, spec in enumerate(plan)}
        if volume is not None and volume.kind == "mesh":
            # A real model already has its legs where they are: the spine is the centre section.
            section = volume.spine_section(self.p.thickness_mm)
            r = self.p.min_feature_mm / 2.0
            spine = largest_polygon(make_valid(section.buffer(-r).buffer(r))) if section is not None else None
            if spine is None:
                raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "The 3D model has no material on its centre plane.")
            return _inside(spine.simplify(0.05), canon), plates, False
        legs = [a for a in anatomy.appendages if a.kind == Region.LIMB]
        profile_ids = [k for k, spec in enumerate(plan) if spec.role == PieceRole.PROFILE]
        if len(legs) < 2 or not profile_ids:
            return canon, plates, False
        k = min(profile_ids, key=lambda i: plan[i].z)
        base = plan[k].polygon
        hw = self.p.slot_width_mm / 2.0 + self.p.min_wall_mm
        r = self.p.min_feature_mm / 2.0

        polys = []
        for leg in legs:
            g = raster_to_geometry(leg.mask, anatomy.x0, anatomy.y0, anatomy.res)
            if g is None:
                return canon, plates, False
            poly = largest_polygon(make_valid(g.buffer(0.75 * anatomy.res).intersection(canon)))
            if poly is None:
                return canon, plates, False
            polys.append((leg, poly))

        # Front/back groups by attachment; alternate near/far legs between the two sides.
        centre = float(np.mean([leg.attach[0] for leg, _ in polys]))
        sides: dict[int, list[Polygon]] = {1: [], -1: []}
        for front in (True, False):
            group = sorted((lp for lp in polys if (lp[0].attach[0] >= centre) == front), key=lambda lp: lp[0].attach[0])
            for i, (_, poly) in enumerate(group):
                targets = (1, -1) if len(group) == 1 else ((1,) if i % 2 == 0 else (-1,))
                for t in targets:
                    sides[t].append(poly)

        if self.p.style == Style.SKELETON:
            # Only a hip/shoulder pad of the side profile is kept around each leg, so the ribs
            # between the legs stay open (like the leg plates of a fossil kit).
            pads = [shapely.box(p.bounds[0] - 3.0 * hw, -BIG, p.bounds[2] + 3.0 * hw, BIG) for _, p in polys]
            base = largest_polygon(make_valid(base.intersection(shapely.union_all(pads)))) or base
        result = []
        for side in (1, -1):
            parts = [base]
            for poly in sides[side]:
                lx0, ly0, lx1, ly1 = poly.bounds
                ivs = strip_intervals(base, lx0, lx1)
                if not ivs:
                    return canon, plates, False
                top = max(max(iv[0] for iv in ivs) + 2.0 * hw + 2.0, ly1)
                bridge = canon.intersection(shapely.box(lx0, ly1 - max(2.0, 0.15 * (ly1 - ly0)), lx1, top))
                parts += [poly, bridge]
            merged = make_valid(shapely.union_all(parts)).buffer(0.25).buffer(-0.25).buffer(-r).buffer(r)
            plate = largest_polygon(make_valid(merged))
            if plate is None or plate.area < 0.97 * make_valid(merged).area:
                return canon, plates, False
            result.append(_inside(plate.simplify(0.1), canon))

        all_legs = shapely.union_all([poly for _, poly in polys]).buffer(0.3)
        spine = make_valid(canon.difference(all_legs)).buffer(-r).buffer(r)
        spine = largest_polygon(make_valid(spine))
        if spine is None or spine.area < 0.45 * canon.area:
            return canon, plates, False
        plates[k] = (result[0], result[1])
        return _inside(spine.simplify(0.05), canon), plates, True

    # ------------------------------------------------------------------ rib candidates

    def _evaluate(self, canon: Polygon, depth: DepthField, frame: RibFrame, through_origin: bool) -> RibCandidate | None:
        """Can a cross rib on this frame form a secure joint with the spine?

        Requires (a) spine material across the slot plus walls on both sides, (b) a rib wide
        enough (half-depth D) for its own slot walls around z = 0 over at least the minimum
        engagement on each side of the split, and (c) the slot cut to run out of the spine
        without crossing any other part of it on the insertion side.
        """
        p = self.p
        w = p.slot_width_mm
        hw = w / 2.0 + p.min_wall_mm
        local = frame.to_local(canon)
        core = core_intervals(local, 0.0, hw)
        if not core:
            return None
        if through_origin:
            best = next((iv for iv in core if iv[0] <= 0.0 <= iv[1]), None)
            if best is None:
                return None
        else:
            best = max(core, key=lambda iv: iv[1] - iv[0])
        if best[1] - best[0] < p.min_joint_length_mm:
            return None
        comps = strip_intervals(local, -w / 2.0, w / 2.0)
        chosen = next((c for c in comps if c[0] <= best[0] + 1e-9 and c[1] >= best[1] - 1e-9), None)
        if chosen is None:
            return None
        others = [c for c in comps if c != chosen]
        up_ok = not any(c[0] >= chosen[1] for c in others)
        down_ok = not any(c[1] <= chosen[0] for c in others)
        if not (up_ok or down_ok):
            return None
        mid = slot_split(chosen[0], chosen[1])
        ts = np.linspace(best[0], best[1], max(8, int((best[1] - best[0]) / 0.5)))
        xs = frame.cx + ts * frame.dx
        ys = frame.cy + ts * frame.dy
        wide = depth.sample(xs, ys) >= hw
        dt = (best[1] - best[0]) / (len(ts) - 1)
        engagement = min(float(wide[ts < mid].sum()) * dt, float(wide[ts >= mid].sum()) * dt)
        if engagement < p.min_engagement_mm:
            return None
        column = line_intervals(local, 0.0)
        a, b = next((iv for iv in column if iv[0] <= best[0] + 1e-6 and iv[1] >= best[1] - 1e-6), best)
        return RibCandidate(frame=frame, a=a, b=b, up=up_ok, engagement=engagement, region=Region.TORSO,
                            meta={"best": best, "strip": chosen})

    def _sweep_candidates(self, canon: Polygon, anatomy: Anatomy, depth: DepthField) -> list[RibCandidate]:
        hw = self.p.slot_width_mm / 2.0 + self.p.min_wall_mm
        minx, _, maxx, _ = canon.bounds
        step = max(0.25, (maxx - minx) / 600.0)
        out = []
        for x in np.arange(minx + hw, maxx - hw, step):
            cand = self._evaluate(canon, depth, RibFrame(round(float(x), 3), 0.0, 0.0, 1.0), through_origin=False)
            if cand is not None:
                out.append(cand)
        return out

    def _skeleton_candidates(
        self, canon: Polygon, anatomy: Anatomy, depth: DepthField, skip_limbs: bool = False
    ) -> list[RibCandidate]:
        """Ribs perpendicular to the local skeleton axis in necks, heads, legs, tails, trunks."""
        kinds = (Region.NECK, Region.HEAD, Region.TAIL, Region.EXTREMITY) + (() if skip_limbs else (Region.LIMB,))
        rows, cols = np.nonzero(anatomy.skeleton & np.isin(anatomy.labels, kinds))
        seen: set[tuple[int, int, int]] = set()
        out = []
        for r, c in zip(rows, cols):
            ax, ay = anatomy.axis[r, c]
            if not np.isfinite(ax):
                continue
            dx, dy = -ay, ax  # rib runs across the local axis
            if dy < 0 or (abs(dy) < 1e-9 and dx < 0):
                dx, dy = -dx, -dy
            tilt = math.degrees(math.atan2(-dx, dy))
            tilt = 5.0 * round(tilt / 5.0)  # quantised: neighbouring ribs come out parallel
            if abs(tilt) < 15.0:
                continue  # practically vertical: covered by the sweep
            x, y = anatomy.x0 + c * anatomy.res, anatomy.y0 + r * anatomy.res
            key = (round(x), round(y), int(tilt))
            if key in seen:
                continue
            seen.add(key)
            rad = math.radians(tilt)
            frame = RibFrame(round(x, 3), round(y, 3), round(-math.sin(rad), 9), round(math.cos(rad), 9))
            cand = self._evaluate(canon, depth, frame, through_origin=True)
            if cand is not None:
                cand.tilted = True
                out.append(cand)
        return out

    def _rib_candidates(
        self, canon: Polygon, anatomy: Anatomy, depth: DepthField, skip_limbs: bool = False
    ) -> list[RibCandidate]:
        det = self.detail
        w = self.p.slot_width_mm
        sweep = self._sweep_candidates(canon, anatomy, depth)
        tilted = self._skeleton_candidates(canon, anatomy, depth, skip_limbs) if det.tilted else []
        cands = sweep + tilted
        if not cands:
            return []
        minx, miny, maxx, maxy = canon.bounds
        size = max(maxx - minx, maxy - miny)
        # Section change along the sweep (fast width/height changes deserve more ribs).
        lengths = np.array([c.b - c.a for c in sweep])
        peaks = np.array([float(depth.sample(*_mid(c)).item()) for c in sweep])
        change = np.zeros(len(sweep))
        if len(sweep) > 2:
            xs = np.array([c.frame.cx for c in sweep])
            g = np.abs(np.gradient(lengths, xs)) + 2.0 * np.abs(np.gradient(peaks, xs))
            change = np.clip(g / (np.percentile(g, 95) + 1e-9), 0, 2)
        for i, c in enumerate(cands):
            pts = _trace_points(c, 16)
            c.region = anatomy.region_of_points(pts)
            if anatomy.region_fraction(pts, (Region.LIMB,)) > 0.35 and c.region in TORSO_LIKE:
                c.region = Region.LIMB  # body + leg rib: supports the leg
            kappa = anatomy.curvature_near(np.array([pts[0], pts[-1]]), 0.04 * size)
            ch = change[i] if i < len(sweep) else 0.5
            c.weight = REGION_WEIGHT.get(c.region, 1.0) * (1.0 + det.curvature * kappa + det.change * ch)
            # In necks, tails and trunks a section perpendicular to the axis describes the shape;
            # a vertical slice through an inclined appendage does not.
            if c.region in (Region.NECK, Region.TAIL, Region.EXTREMITY):
                c.weight *= 1.3 if c.tilted else 0.7
            c.trace = LineString([c.frame.point(c.a), c.frame.point(c.b)])
            # The slot strip in the spine can reach further than the centre line where the
            # outline is slanted; spacing must be measured on everything the cuts touch.
            s0, s1 = c.meta["strip"]
            c.footprint = LineString([c.frame.point(min(c.a, s0)), c.frame.point(max(c.b, s1))]).buffer(
                w / 2.0, cap_style="flat")
        return cands

    # ------------------------------------------------------------------ build

    def _build(
        self,
        silhouette: Polygon,
        canon: Polygon,
        spine_poly: Polygon,
        plate_polys: dict[int, tuple[Polygon, Polygon]],
        anatomy: Anatomy,
        depth: DepthField,
        orientation: Orientation,
        plan: list[PlateSpec],
        candidates: list[RibCandidate],
    ) -> PuzzleModel:
        p = self.p
        n_cross = p.piece_count - 1 - 2 * len(plan)
        if n_cross < 2:
            raise ConfigurationFailed(ErrorCode.INVALID_SETTINGS, "At least two cross ribs are required.")

        spine = Piece(id="S", kind=PieceKind.PROFILE, polygon=orient(spine_poly, 1.0), position_mm=0.0,
                      role=PieceRole.SPINE, region="spine")
        plates: list[Piece] = []
        for k, spec in enumerate(plan):
            for sign, poly in zip((1.0, -1.0), plate_polys[k]):
                plates.append(Piece(id=f"P{k}{'+' if sign > 0 else '-'}", kind=PieceKind.PROFILE,
                                    polygon=orient(poly, 1.0), position_mm=round(sign * spec.z, 3), role=spec.role,
                                    region=spec.region.label))
        plate_checks = [(spec.z, plate_polys[k][i]) for k, spec in enumerate(plan) for i in range(2)]
        crosses = self._select_ribs(spine_poly, depth, candidates, n_cross, plan, plate_checks)
        profiles = [spine] + plates

        joints = self._cut_joints(profiles, crosses)
        for piece in profiles + crosses:
            if piece.role == PieceRole.CROSS:
                max_loss = 0.12
            elif piece.role == PieceRole.SPINE:
                max_loss = 0.04
            else:
                max_loss = 0.15
            piece.polygon = self._cleanup(piece.polygon, max_loss=max_loss, name=piece.id, plan_index=_plan_index(piece))

        self._drop_orphan_slots(profiles + crosses, joints)
        if p.style == Style.SKELETON and "windows" in self._skeleton_steps:
            for piece in profiles + crosses:
                piece.polygon = self._hollow(piece)
        self._check_connectivity(profiles, crosses, joints)
        pieces = self._assign_ids(spine, plates, crosses, joints)
        self._set_transforms(pieces, orientation)
        warnings = self._add_labels(pieces) if p.labels else []
        size = self._object_size(pieces)
        slide = SlideDirection.CROSS_FROM_ABOVE if sum(c.slide_sign for c in crosses) >= 0 else SlideDirection.CROSS_FROM_BELOW
        analysis = {
            "regions": anatomy.summary(),
            "head_side": anatomy.head_side,
            "tilted_ribs": sum(1 for c in crosses if abs(c.frame.angle_deg) > 1e-6),
            "plates": [{"z": s.z, "region": s.region.label, "role": s.role.value} for s in plan],
        }
        return PuzzleModel(
            params=p,
            orientation=orientation,
            slide=slide,
            pieces=pieces,
            joints=joints,
            silhouette=silhouette,
            object_size_mm=size,
            warnings=warnings,
            analysis=analysis,
        )

    # ------------------------------------------------------------------ rib selection

    def _select_ribs(
        self,
        canon: Polygon,
        depth: DepthField,
        cands: list[RibCandidate],
        n: int,
        plan: list[PlateSpec],
        plate_checks: list[tuple[float, Polygon]] | None = None,
    ) -> list[Piece]:
        """Weighted farthest-point sampling with hard spacing/crossing constraints.

        Each pick maximises weight^focus x distance to the ribs chosen so far, so density
        follows importance. Picked ribs are verified against the real geometry (joint grip
        on the spine, slot cuts that would sever the spine or the rib); failures are
        discarded and the next best candidate is tried.
        """
        p = self.p
        w = p.slot_width_mm
        spacing = self._min_cross_spacing()
        weights = np.array([c.weight for c in cands])
        # Plates need ribs through them: favour candidates that cross planned plates.
        for spec in plan:
            hits = shapely.intersects(np.array([c.trace for c in cands], dtype=object), spec.polygon)
            weights = np.where(hits, weights * 1.25, weights)
        traces = np.array([c.trace for c in cands], dtype=object)
        footprints = np.array([c.footprint for c in cands], dtype=object)
        minx, miny, maxx, maxy = canon.bounds
        cap = 0.35 * max(maxx - minx, maxy - miny)
        dmin = np.full(len(cands), np.inf)
        alive = np.ones(len(cands), dtype=bool)
        focus = self.detail.focus
        chosen: list[Piece] = []
        cutters: list[Polygon] = []
        self._chosen_footprints = []
        while len(chosen) < n:
            if not chosen:
                score = np.array([
                    (c.b - c.a) if c.region in TORSO_LIKE and not c.tilted else -1.0 for c in cands
                ])
                if score.max() <= 0:
                    score = weights.copy()
            else:
                score = np.power(weights, focus) * np.minimum(dmin, cap) + 1e-6 * weights
            score = np.where(alive, score, -np.inf)
            i = int(np.argmax(score))
            if not np.isfinite(score[i]):
                break
            cand = cands[i]
            piece = self._try_rib(canon, depth, cand, cutters, len(chosen), plate_checks or [])
            if piece is None:
                alive[i] = False
                near = shapely.distance(traces, cand.trace) < 0.75
                alive &= ~(near & np.array([abs(c.frame.angle_deg - cand.frame.angle_deg) < 1e-6 for c in cands]))
                continue
            chosen.append(piece)
            d_new = shapely.distance(traces, cand.trace)
            dmin = np.minimum(dmin, d_new)
            alive &= d_new >= spacing
            alive &= shapely.distance(footprints, cand.footprint) >= max(p.min_wall_mm, spacing - w)
        if len(chosen) < n:
            total = len(chosen) + 1 + 2 * len(plan)
            raise ConfigurationFailed(
                ErrorCode.TOO_MANY_PIECES,
                f"Too many pieces for this size: at most {total} pieces fit with {p.thickness_mm:g} mm material. "
                "Reduce the number of pieces, use thinner material or increase the puzzle size.",
            )
        return chosen

    def _try_rib(
        self,
        canon: Polygon,
        depth: DepthField,
        cand: RibCandidate,
        cutters: list[Polygon],
        idx: int,
        plate_checks: list[tuple[float, Polygon]],
    ):
        frame = cand.frame
        poly = self._cross_rib(depth, cand)
        if poly is None:
            return None
        # The real rib may reach beyond the spine section (legs of a real model): it must
        # keep clear of every rib already chosen.
        _, t0, _, t1 = poly.bounds
        footprint = LineString([frame.point(t0), frame.point(t1)]).buffer(self.p.slot_width_mm / 2.0, cap_style="flat")
        if any(footprint.distance(f) < self.p.min_wall_mm for f in self._chosen_footprints):
            return None
        # Every plate this rib crosses must be cut cleanly: no unrelated part of the plate may
        # lie further along the insertion path, and the cut must not split the plate.
        for z, plate in plate_checks:
            local = frame.to_local(plate)
            geo = self._joint_geometry(local, poly, z)
            if geo is None:
                continue
            if _blocks_insertion(geo[0], geo[2], geo[3], cand.up):
                return None
            cut = frame.to_world(slot_cutter(0.0, self.p.slot_width_mm, geo[4], opens_up=cand.up))
            if self._split_loss(plate, cut) > 0.1:
                return None
        local = frame.to_local(canon)
        geo = self._joint_geometry(local, poly, 0.0)
        if geo is None or geo[5] < self.p.min_engagement_mm:
            return None
        m = geo[4]
        w = self.p.slot_width_mm
        spine_cut = frame.to_world(slot_cutter(0.0, w, m, opens_up=cand.up))
        if self._split_loss(canon, spine_cut) > 0.01:
            return None
        if self._split_loss(poly, slot_cutter(0.0, w, m, opens_up=not cand.up)) > 0.08:
            return None
        # Cuts that are harmless alone can still enclose and sever a region together.
        if cutters and self._split_loss(canon, shapely.union_all(cutters + [spine_cut])) > 0.01:
            return None
        cutters.append(spine_cut)
        self._chosen_footprints.append(footprint)
        return Piece(id=f"C{idx}", kind=PieceKind.CROSS, polygon=poly, role=PieceRole.CROSS,
                     region=cand.region.label, frame=frame, slide_sign=1 if cand.up else -1)

    def _cross_rib(self, depth: DepthField, cand: RibCandidate) -> Polygon | None:
        """The body section on the rib's plane (only the part connected to the spine joint)."""
        p = self.p
        volume = self._volume
        a, b = cand.a, cand.b
        if p.style == Style.SKELETON and getattr(self, "_body", None) is not None:
            # The spine is only a backbone band: the rib still spans the whole body section
            # (it hangs from the backbone like a real rib).
            column = line_intervals(cand.frame.to_local(self._body), 0.0)
            a, b = next(((lo, hi) for lo, hi in column if lo <= cand.a + 1e-6 and hi >= cand.b - 1e-6), (a, b))
        geom = volume.cross_section(cand.frame, a, b, p.thickness_mm)
        if geom is None or geom.is_empty:
            return None
        w = p.slot_width_mm
        core = shapely.box(-w / 2.0, cand.a, w / 2.0, cand.b)
        parts = polygon_parts(make_valid(geom))
        if not parts:
            return None
        poly = max(parts, key=lambda pp: pp.intersection(core).area)
        if poly.intersection(core).area <= 0:
            return None
        r = p.min_feature_mm / 2.0
        parts = [pp for pp in polygon_parts(poly.buffer(-r).buffer(r)) if pp.intersection(core).area > 0]
        if not parts:
            return None
        poly = max(parts, key=lambda pp: pp.intersection(core).area)
        return orient(largest_polygon(make_valid(poly.simplify(0.05))), 1.0)

    # ------------------------------------------------------------------ joints

    def _joint_geometry(self, lp_local: Polygon, tp: Polygon, z: float):
        """Shared material of a profile (already in the rib frame, s/t) at depth z and a cross
        rib polygon (u = z, v = t) on their common line s = 0.

        Returns (profile intervals, rib intervals, t_low, t_high, split, engagement) or None.
        Engagement is the length, on each side of the split, over which both pieces keep
        full slot walls (min of the two sides).
        """
        p = self.p
        w = p.slot_width_mm
        hw = w / 2.0 + p.min_wall_mm
        yl = strip_intervals(lp_local, -w / 2, w / 2)
        yt = strip_intervals(tp, z - w / 2, z + w / 2)
        overlap = intersect(yl, yt)
        if not overlap:
            return None
        t_low, t_high = overlap[0][0], overlap[-1][1]
        if t_high - t_low < 1e-3:
            return None
        m = round(slot_split(t_low, t_high), 3)
        grip = intersect(intersect(core_intervals(lp_local, 0.0, hw), core_intervals(tp, z, hw)), [(t_low, t_high)])
        # Only grip that starts right at the split counts: the slot bottoms carry the load, so
        # both pieces need full walls from m outwards (no thin wall next to a slot bottom).
        at_m = next((iv for iv in grip if iv[0] - 1e-6 <= m <= iv[1] + 1e-6), None)
        engagement = 0.0 if at_m is None else min(m - at_m[0], at_m[1] - m)
        return yl, yt, t_low, t_high, m, engagement

    def _cut_joints(self, profiles: list[Piece], crosses: list[Piece]) -> list[Joint]:
        p = self.p
        w = p.slot_width_mm
        originals = {pc.id: pc.polygon for pc in profiles + crosses}
        joints: list[Joint] = []
        cutters: dict[str, list[Polygon]] = {pc.id: [] for pc in profiles + crosses}
        for cross in crosses:
            frame, up = cross.frame, cross.slide_sign > 0
            tp = originals[cross.id]
            for prof in profiles:
                z = prof.position_mm
                local = frame.to_local(originals[prof.id])
                geo = self._joint_geometry(local, tp, z)
                if geo is None:
                    continue
                yl, yt, t_low, t_high, m, engagement = geo
                if _blocks_insertion(yl, t_low, t_high, up) or _blocks_insertion(yt, t_low, t_high, not up):
                    raise ConfigurationFailed(
                        ErrorCode.INVALID_GEOMETRY,
                        f"Rib {cross.id} would have to pass through an unrelated part of {prof.id}.",
                        _plan_index(prof),
                    )
                px, py = frame.point(m)
                joint = Joint(
                    id=f"J{len(joints) + 1:02d}",
                    profile_id=prof.id,
                    cross_id=cross.id,
                    x_mm=round(px, 3),
                    y_mm=round(py, 3),
                    z_mm=z,
                    t_low=t_low,
                    t_high=t_high,
                    t_split=m,
                    engagement_mm=round(engagement, 3),
                    load_bearing=engagement >= p.min_engagement_mm,
                )
                joints.append(joint)
                # The profile's slot opens towards the side the rib is inserted from.
                cutters[prof.id].append(frame.to_world(slot_cutter(0.0, w, m, opens_up=up)))
                cutters[cross.id].append(slot_cutter(z, w, m, opens_up=not up))
                prof_end = yl[-1][1] if up else yl[0][0]
                cross_end = yt[0][0] if up else yt[-1][1]
                sgn = 1.0 if up else -1.0
                prof.slots.append(Slot(joint.id, (px, py), (sgn * frame.dx, sgn * frame.dy), abs(prof_end - m), w))
                cross.slots.append(Slot(joint.id, (z, m), (0.0, -sgn), abs(m - cross_end), w))
        for piece in profiles + crosses:
            if cutters[piece.id]:
                cut = shapely.union_all(cutters[piece.id])
                piece.polygon = make_valid(piece.polygon.difference(cut))
        return joints

    def _split_loss(self, poly: Polygon, cutter: Polygon) -> float:
        """Material a cut would cost after cleanup: fragments plus regions hanging on necks
        thinner than the minimum feature width (as a fraction of the remaining material)."""
        rest = make_valid(poly.difference(cutter))
        total = rest.area
        if total <= 0:
            return 1.0
        r = self.p.min_feature_mm / 2.0
        opened = largest_polygon(make_valid(rest.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")))
        return 1.0 - (opened.intersection(rest).area if opened is not None else 0.0) / total

    def _cleanup(self, geom, max_loss: float, name: str, plan_index: int | None = None) -> Polygon:
        parts = polygon_parts(make_valid(geom))
        if not parts:
            raise ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, f"Piece {name} vanished after slotting.", plan_index)
        total = sum(pp.area for pp in parts)
        main = max(parts, key=lambda pp: pp.area)
        r = self.p.min_feature_mm / 2.0
        opened = main.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")
        opened = largest_polygon(make_valid(opened.intersection(main)))
        if opened is None:
            raise ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, f"Piece {name} is too thin after slotting.", plan_index)
        loss = 1.0 - opened.area / total
        if loss > max_loss:
            raise ConfigurationFailed(
                ErrorCode.INVALID_GEOMETRY, f"Slots would split piece {name} ({loss * 100:.0f}% material lost).",
                plan_index,
            )
        cleaned = shapely.set_precision(opened.simplify(GRID), GRID)
        cleaned = largest_polygon(make_valid(cleaned))
        if cleaned is None or not cleaned.is_valid:
            raise ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, f"Piece {name} is invalid after cleanup.", plan_index)
        return orient(cleaned, 1.0)

    def _backbone(self, spine: Polygon, anatomy: Anatomy, legs_moved: bool) -> Polygon:
        """Skeleton style: the spine keeps only a backbone band under the top of the torso
        (plus head, neck and tail); the belly is opened so the ribs hang from it as arches."""
        from app.geometry.depth import raster_to_geometry

        torso = raster_to_geometry(anatomy.region_mask(TORSO_LIKE), anatomy.x0, anatomy.y0, anatomy.res)
        if torso is None:
            return spine
        torso = make_valid(torso.intersection(spine))
        if torso.is_empty:
            return spine
        minx, miny, maxx, maxy = torso.bounds
        width = max(0.26 * (maxy - miny), 3.0 * (self.p.slot_width_mm / 2 + self.p.min_wall_mm), 10.0)
        xs = np.linspace(minx, maxx, 160)
        upper = []
        for x in xs:
            ivs = line_intervals(spine, float(x))
            if ivs:
                upper.append((float(x), max(iv[1] for iv in ivs) - width))
        if len(upper) < 3:
            return spine
        below = Polygon(upper + [(maxx, miny - BIG / 100), (minx, miny - BIG / 100)])
        r = self.p.min_feature_mm
        window = make_valid(torso.buffer(2 * r).intersection(below))
        if not legs_moved:
            legs = [raster_to_geometry(a.mask, anatomy.x0, anatomy.y0, anatomy.res)
                    for a in anatomy.appendages if a.kind == Region.LIMB]
            legs = [g for g in legs if g is not None]
            if legs:  # legs still hang from the spine: keep solid columns above them
                cols = [shapely.box(g.bounds[0] - r, -BIG, g.bounds[2] + r, BIG) for g in legs]
                window = make_valid(window.difference(shapely.union_all(cols)))
        window = window.buffer(-2 * r).buffer(2 * r)
        if window.is_empty:
            return spine
        result = make_valid(spine.difference(window))
        main = largest_polygon(result)
        if main is None or main.area < 0.95 * result.area or main.area < 0.3 * spine.area:
            return spine
        return orient(largest_polygon(make_valid(main.buffer(-r / 2).buffer(r / 2))), 1.0)

    def _hollow(self, piece: Piece) -> Polygon:
        """Skeleton style: turn a filled piece into a frame.

        The outline is kept as a band; the interior is opened as rounded windows. Material
        around every slot (the slot plus its gripping walls and a band beyond the slot bottom)
        is protected, so joints keep exactly the same grip. A cross rib whose spine slot
        enters from below becomes an open arch - a rib of a rib cage.
        """
        p = self.p
        poly = piece.polygon
        minx, miny, maxx, maxy = poly.bounds
        if piece.role == PieceRole.CROSS:
            # Ribs are small: a band relative to the rib itself, so they become open hoops.
            band = max(2.0 * p.min_feature_mm, 0.11 * min(maxx - minx, maxy - miny))
            spread, min_area = 0.3, 1.5
        else:
            band = max(2.4 * p.min_feature_mm, 0.026 * max(maxx - minx, maxy - miny), 5.0)
            spread, min_area = 0.6, 3.0
        inner = poly.buffer(-band)
        if inner.is_empty:
            return poly
        keep = [s.channel(length=s.depth_mm + band, inset=-band, extra_width=p.min_wall_mm + band * spread)
                for s in piece.slots]
        window = make_valid(inner.difference(shapely.union_all(keep))) if keep else inner
        r = 0.6 * band
        window = window.buffer(-r).buffer(r)  # rounded corners; slits (e.g. along a leg) vanish
        holes = [w for w in polygon_parts(window) if w.area >= min_area * band * band]
        if not holes:
            return poly
        frame = make_valid(poly.difference(shapely.union_all(holes)))
        parts = polygon_parts(frame)
        main = max(parts, key=lambda q: q.area)
        if main.area < 0.97 * frame.area:
            return poly  # hollowing would split the piece: keep it solid
        cleaned = largest_polygon(make_valid(shapely.set_precision(main.simplify(GRID), GRID)))
        return orient(cleaned, 1.0) if cleaned is not None and cleaned.is_valid else poly

    def _drop_orphan_slots(self, pieces: list[Piece], joints: list[Joint]) -> None:
        """A clearance slot whose surrounding material was removed by cleanup (a sliver that
        only grazed the other rib) no longer exists on the piece; forget it."""
        orphaned: set[str] = set()
        for pc in pieces:
            keep = []
            for slot in pc.slots:
                bottom = slot.channel(length=0.05, inset=-0.05, extra_width=0.5)
                if pc.polygon.buffer(1e-3).intersects(bottom):
                    keep.append(slot)
                else:
                    orphaned.add(slot.joint_id)
            pc.slots = keep
        for j in joints:
            if j.id in orphaned:
                j.load_bearing = False

    def _check_connectivity(self, profiles: list[Piece], crosses: list[Piece], joints: list[Joint]) -> None:
        bearing = [j for j in joints if j.load_bearing]
        count = {pc.id: 0 for pc in profiles + crosses}
        for j in bearing:
            count[j.profile_id] += 1
            count[j.cross_id] += 1
        for cross in crosses:
            if count[cross.id] < 1:
                raise ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, f"Cross rib {cross.id} has no secure joint.")
        for prof in profiles:
            if count[prof.id] < 2:
                raise ConfigurationFailed(
                    ErrorCode.INVALID_GEOMETRY, f"Plate {prof.id} has fewer than 2 secure joints.", _plan_index(prof)
                )
        parent = {k: k for k in count}

        def find(a: str) -> str:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for j in bearing:
            parent[find(j.profile_id)] = find(j.cross_id)
        if len({find(k) for k in count}) != 1:
            raise ConfigurationFailed(ErrorCode.INVALID_GEOMETRY, "Pieces do not form a single connected assembly.")

    # ------------------------------------------------------------------ finishing

    def _assign_ids(self, spine: Piece, plates: list[Piece], crosses: list[Piece], joints: list[Joint]) -> list[Piece]:
        mapping = {spine.id: "LONG-01"}
        profiles = sorted((pc for pc in plates if pc.role == PieceRole.PROFILE),
                          key=lambda pc: (abs(pc.position_mm), -pc.position_mm))
        for i, pc in enumerate(profiles, start=2):
            mapping[pc.id] = f"LONG-{i:02d}"
        details = sorted((pc for pc in plates if pc.role == PieceRole.DETAIL),
                         key=lambda pc: (Region[pc.region.upper()].group, abs(pc.position_mm), -pc.position_mm,
                                         pc.polygon.centroid.x))
        counters: dict[str, int] = {}
        for pc in details:
            group = Region[pc.region.upper()].group
            counters[group] = counters.get(group, 0) + 1
            mapping[pc.id] = f"DETAIL-{group}-{counters[group]:02d}"
        ordered_crosses = sorted(crosses, key=_cross_order)
        for i, pc in enumerate(ordered_crosses, start=1):
            mapping[pc.id] = f"CROSS-{i:02d}"
        for pc in [spine] + plates + crosses:
            pc.id = mapping[pc.id]
        for j in joints:
            j.profile_id = mapping[j.profile_id]
            j.cross_id = mapping[j.cross_id]
        return [spine] + profiles + details + ordered_crosses

    def _set_transforms(self, pieces: list[Piece], orientation: Orientation) -> None:
        for pc in pieces:
            if pc.kind == PieceKind.PROFILE:
                origin, u, v = (0.0, 0.0, pc.position_mm), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
            else:
                f = pc.frame
                origin, u, v = (f.cx, f.cy, 0.0), (0.0, 0.0, 1.0), (f.dx, f.dy, 0.0)
            u, v, origin = _display_vec(u, orientation), _display_vec(v, orientation), _display_vec(origin, orientation)
            n = tuple(float(c) for c in np.cross(u, v))
            pc.transform = Transform3D(origin, u, v, n)
        lo, hi = _bounds3d(pieces, self.p.thickness_mm)
        shift = (-(lo[0] + hi[0]) / 2.0, -lo[1], -(lo[2] + hi[2]) / 2.0)
        for pc in pieces:
            o = pc.transform.origin
            pc.transform.origin = (o[0] + shift[0], o[1] + shift[1], o[2] + shift[2])

    def _object_size(self, pieces: list[Piece]) -> tuple[float, float, float]:
        lo, hi = _bounds3d(pieces, self.p.thickness_mm)
        return tuple(round(float(hi[i] - lo[i]), 2) for i in range(3))

    def _add_labels(self, pieces: list[Piece]) -> list[str]:
        warnings = []
        for pc in pieces:
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


def _inside(poly: Polygon, envelope: Polygon) -> Polygon | None:
    """Clip to the silhouette envelope (simplification may move vertices slightly outwards)."""
    return largest_polygon(make_valid(make_valid(poly).intersection(envelope)))


def _blocks_insertion(intervals: list[tuple[float, float]], lo: float, hi: float, towards_plus: bool) -> bool:
    """True if a piece has material on the joint line beyond the joint, on the side the slot
    must open to (the slot would cut through an unrelated part of the piece)."""
    if towards_plus:
        return any(iv[0] > hi + 1e-6 for iv in intervals)
    return any(iv[1] < lo - 1e-6 for iv in intervals)


def _depth_in(depth: DepthField, anatomy: Anatomy, kinds: tuple[Region, ...]) -> np.ndarray:
    """Depth values over the given anatomical regions (the two rasters may differ)."""
    rows, cols = np.nonzero(anatomy.region_mask(kinds))
    if rows.size == 0:
        return np.zeros(0)
    vals = depth.sample(anatomy.x0 + cols * anatomy.res, anatomy.y0 + rows * anatomy.res)
    return vals[vals > 0]


def _matching_part(part: Polygon, candidates: list[Polygon]) -> Polygon | None:
    """The part on the other side of the spine that corresponds to `part` (largest overlap
    in the xy projection)."""
    best, best_overlap = None, 0.0
    for c in candidates:
        ov = part.intersection(c).area
        if ov > best_overlap:
            best, best_overlap = c, ov
    if best is None or best_overlap < 0.3 * min(part.area, best.area):
        return None
    return best


def _plan_index(piece: Piece) -> int | None:
    if piece.role in (PieceRole.PROFILE, PieceRole.DETAIL) and piece.id.startswith("P"):
        try:
            return int(piece.id[1:-1])
        except ValueError:
            return None
    return None


def _cross_order(pc: Piece) -> tuple[float, float]:
    """Order cross ribs along the body by the midpoint of their trace."""
    c = pc.polygon.centroid  # (z, t): t centre along the trace
    x, y = pc.frame.point(c.y)
    return (round(x, 3), round(y, 3))


def _majority_region(anatomy: Anatomy, poly: Polygon) -> Region:
    minx, miny, maxx, maxy = poly.bounds
    xs, ys = np.meshgrid(np.linspace(minx, maxx, 24), np.linspace(miny, maxy, 24))
    inside = shapely.contains_xy(poly, xs.ravel(), ys.ravel())
    pts = np.stack([xs.ravel()[inside], ys.ravel()[inside]], axis=1)
    if len(pts) == 0:
        c = poly.representative_point()
        return anatomy.region_at(c.x, c.y)
    reg = anatomy.region_of_points(pts)
    return Region.TORSO if reg in TORSO_LIKE else reg


def _mid(c: RibCandidate) -> tuple[float, float]:
    return c.frame.point(0.5 * (c.a + c.b))


def _trace_points(c: RibCandidate, n: int) -> np.ndarray:
    ts = np.linspace(c.a, c.b, n)
    return np.stack([c.frame.cx + ts * c.frame.dx, c.frame.cy + ts * c.frame.dy], axis=1)


def _bounds3d(pieces: list[Piece], thickness: float) -> tuple[np.ndarray, np.ndarray]:
    pts = []
    for pc in pieces:
        coords = np.asarray(pc.polygon.exterior.coords)
        for w in (-thickness / 2.0, thickness / 2.0):
            pts.extend(pc.transform.apply(u, v, w) for u, v in coords)
    arr = np.asarray(pts)
    return arr.min(axis=0), arr.max(axis=0)
