"""Manufacturing geometry validation and safe repair.

Nothing is exported unless every check here passes. Repairs are limited to operations that
cannot change a part's intended shape beyond tolerance (duplicate-vertex removal, snap
rounding, zero-width buffer). Anything else is reported as an error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LinearRing, LineString, Polygon, box
from shapely.strtree import STRtree
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid

from app.geometry.normalize import largest_polygon
from app.geometry.sections import strip_intervals
from app.geometry.slots import BIG
from app.geometry.types import PieceKind, PuzzleModel, SlideDirection

MIN_SEGMENT_MM = 1e-4
ASSEMBLY_TOL = 1e-3
TOL = 1e-6


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    checks: dict[str, int] = field(default_factory=dict)

    def count(self, name: str, n: int = 1) -> None:
        self.checks[name] = self.checks.get(name, 0) + n

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "ValidationReport") -> "ValidationReport":
        self.errors.extend(other.errors)
        for k, v in other.checks.items():
            self.count(k, v)
        return self


def _ring_problems(ring: LinearRing, label: str) -> list[str]:
    coords = list(ring.coords)
    problems = []
    if len(coords) < 4:
        problems.append(f"{label}: ring has fewer than 3 distinct vertices")
        return problems
    if coords[0] != coords[-1]:
        problems.append(f"{label}: open contour")
    seen = set()
    for a, b in zip(coords[:-1], coords[1:]):
        if math.dist(a, b) < MIN_SEGMENT_MM:
            problems.append(f"{label}: zero-length segment at {a}")
            break
        key = (min(a, b), max(a, b))
        if key in seen:
            problems.append(f"{label}: duplicate segment {key}")
            break
        seen.add(key)
    if not ring.is_simple:
        problems.append(f"{label}: self-intersecting contour")
    return problems


def repair_polygon(poly: Polygon) -> Polygon | None:
    """Safe repair: snap rounding, duplicate/zero-length vertex removal, make_valid."""
    try:
        fixed = largest_polygon(make_valid(shapely.set_precision(make_valid(poly), 0.0005)))
        if fixed is None:
            return None
        fixed = fixed.simplify(0.0, preserve_topology=True)  # drops duplicate vertices only
    except shapely.errors.GEOSException:
        return None
    if not fixed.is_valid or abs(fixed.area - poly.area) > 0.002 * max(poly.area, 1.0):
        return None
    return shapely.geometry.polygon.orient(fixed, 1.0)


def validate_polygon(poly, label: str, min_area: float = 1.0) -> list[str]:
    if not isinstance(poly, Polygon):
        return [f"{label}: geometry is {getattr(poly, 'geom_type', type(poly).__name__)}, expected one connected polygon"]
    if poly.is_empty:
        return [f"{label}: empty geometry"]
    errors = []
    if not poly.is_valid:
        errors.append(f"{label}: invalid polygon ({explain_validity(poly)})")
    if poly.area < min_area:
        errors.append(f"{label}: tiny fragment (area {poly.area:.2f} mm²)")
    minx, miny, maxx, maxy = poly.bounds
    if maxx - minx <= 0 or maxy - miny <= 0:
        errors.append(f"{label}: non-positive dimensions")
    errors += _ring_problems(poly.exterior, f"{label} outer")
    for i, hole in enumerate(poly.interiors):
        errors += _ring_problems(hole, f"{label} hole {i}")
    return errors


def validate_model(model: PuzzleModel) -> ValidationReport:
    """Validate every piece, every slot and the 3D assembly itself."""
    rep = ValidationReport()
    p = model.params
    ids = [pc.id for pc in model.pieces]
    if len(set(ids)) != len(ids):
        rep.errors.append("duplicate piece IDs")
    if len(model.pieces) != p.piece_count:
        rep.errors.append(f"piece count {len(model.pieces)} != requested {p.piece_count}")

    min_area = 4.0 * p.thickness_mm**2
    for pc in model.pieces:
        rep.errors += validate_polygon(pc.polygon, pc.id, min_area)
        rep.count("pieces")
        for slot in pc.slots:
            rep.count("slots")
            rep.errors += _validate_slot(pc.polygon, slot, pc.id)
        for i, stroke in enumerate(pc.engrave):
            rep.count("engrave_paths")
            line = LineString(stroke)
            if len(stroke) < 2 or line.length <= 0:
                rep.errors.append(f"{pc.id}: degenerate engraving path {i}")
            elif not pc.polygon.buffer(-0.2).contains(line):
                rep.errors.append(f"{pc.id}: engraving path {i} leaves the piece")

    rep.merge(validate_slot_quality(model))
    rep.merge(validate_assembly(model))
    rep.merge(validate_connectors(model))
    rep.merge(validate_connectivity(model))
    return rep


def validate_connectors(model: PuzzleModel) -> ValidationReport:
    """Bowtie connectors: same plane as both hosts, no overlap, the designed clearance all
    around, and a pocket wall in each host (the key really bridges the seam)."""
    rep = ValidationReport()
    by_id = {pc.id: pc for pc in model.pieces}
    c = model.params.clearance_mm
    for conn in model.analysis.get("connectors", []):
        rep.count("connectors_checked")
        key = by_id.get(conn["id"])
        hosts = [by_id.get(h) for h in conn.get("pieces", [])]
        if key is None or len(hosts) != 2 or any(h is None for h in hosts):
            rep.errors.append(f"connector {conn.get('id')}: missing pieces")
            continue
        if any(abs(h.position_mm - key.position_mm) > TOL or h.kind != PieceKind.PROFILE for h in hosts):
            rep.errors.append(f"connector {key.id}: not in the plane of {hosts[0].id} / {hosts[1].id}")
            continue
        band = key.polygon.buffer(c / 2.0 + 0.4, join_style="mitre").difference(
            key.polygon.buffer(c / 2.0 + 0.02, join_style="mitre"))
        for h in hosts:
            if h.polygon.intersection(key.polygon).area > 1e-6:
                rep.errors.append(f"connector {key.id} overlaps {h.id}")
            gap = h.polygon.distance(key.polygon)
            if gap < c / 2.0 - 0.01:
                rep.errors.append(f"connector {key.id}: fit gap to {h.id} is {gap:.3f} mm (expected {c / 2:.3f})")
            if h.polygon.intersection(band).area < 0.2 * band.area:
                rep.errors.append(f"connector {key.id} does not grip {h.id}")
        covered = unary_union([h.polygon for h in hosts]).intersection(band).area
        if covered < 0.85 * band.area:
            rep.errors.append(f"connector {key.id}: pocket walls are incomplete")
    return rep


def validate_connectivity(model: PuzzleModel) -> ValidationReport:
    """Every piece must be held: the joint + connector graph is one connected assembly."""
    rep = ValidationReport()
    ids = [pc.id for pc in model.pieces]
    if not ids:
        return rep
    adj: dict[str, set[str]] = {i: set() for i in ids}
    for j in model.joints:
        if j.profile_id in adj and j.cross_id in adj:
            adj[j.profile_id].add(j.cross_id)
            adj[j.cross_id].add(j.profile_id)
    for conn in model.analysis.get("connectors", []):
        for h in conn.get("pieces", []):
            if h in adj and conn["id"] in adj:
                adj[h].add(conn["id"])
                adj[conn["id"]].add(h)
    seen = {ids[0]}
    stack = [ids[0]]
    while stack:
        for nb in adj[stack.pop()]:
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    rep.count("connectivity_checks")
    loose = [i for i in ids if i not in seen]
    if loose:
        rep.errors.append(f"pieces not connected to the assembly: {', '.join(loose[:10])}")
    return rep


def validate_slot_quality(model: PuzzleModel) -> ValidationReport:
    """Manufacturability: narrowest feature, walls beside load-bearing slots, slot overlaps."""
    from app.geometry.quality import narrowest_feature, slot_walls

    rep = ValidationReport()
    p = model.params
    required = p.min_feature_mm
    bearing = {j.id for j in model.joints if j.load_bearing}
    for pc in model.pieces:
        rep.count("feature_checks")
        try:
            nf = narrowest_feature(pc.polygon)
        except shapely.errors.GEOSException as exc:
            rep.errors.append(f"{pc.id}: geometry cannot be measured ({exc})")
            continue
        if nf < required - 0.1:
            rep.errors.append(f"{pc.id}: narrowest feature {nf:.2f} mm is below the {required:.2f} mm minimum")
        for slot in pc.slots:
            if slot.joint_id not in bearing:
                continue
            walls = slot_walls(pc.polygon, slot, p.min_engagement_mm)
            rep.count("slot_wall_checks")
            if walls and min(walls) < required - 0.05:
                rep.errors.append(
                    f"{pc.id}: slot {slot.joint_id} leaves a {min(walls):.2f} mm wall (minimum {required:.2f} mm)"
                )
        channels = [s.channel() for s in pc.slots]
        for i in range(len(channels)):
            for j in range(i + 1, len(channels)):
                if channels[i].distance(channels[j]) < 0.01:
                    rep.errors.append(f"{pc.id}: slots {pc.slots[i].joint_id} and {pc.slots[j].joint_id} overlap")
    return rep


def _validate_slot(poly: Polygon, slot, label: str) -> list[str]:
    """The slot channel must be free of material from its closed end through the piece edge,
    and its closed end must lie on the piece."""
    errors = []
    if slot.width_mm <= 0 or not math.isfinite(slot.depth_mm) or slot.depth_mm <= 0:
        return [f"{label}: slot {slot.joint_id} has invalid dimensions"]
    channel = slot.channel(length=BIG, inset=1e-3, extra_width=-1e-3)
    leftover = poly.intersection(channel).area
    if leftover > 1e-4:
        errors.append(f"{label}: slot {slot.joint_id} is obstructed ({leftover:.4f} mm² of material)")
    bottom = slot.channel(length=0.05, inset=-0.05, extra_width=0.5)
    if not poly.buffer(1e-3).intersects(bottom):
        errors.append(f"{label}: slot {slot.joint_id} lies outside the piece")
    return errors


def _rib_trace(piece, thickness: float) -> Polygon:
    """Footprint of a cross rib in the canonical xy plane (its trace thickened by T)."""
    from shapely.geometry import LineString

    _, tmin, _, tmax = piece.polygon.bounds
    f = piece.frame
    return LineString([f.point(tmin), f.point(tmax)]).buffer(thickness / 2.0, cap_style="flat")


def validate_assembly(model: PuzzleModel) -> ValidationReport:
    """3D checks: no two pieces interpenetrate and every cross rib can slide into place."""
    rep = ValidationReport()
    t = model.params.thickness_mm
    profiles = [pc for pc in model.pieces if pc.kind == PieceKind.PROFILE]
    crosses = [pc for pc in model.pieces if pc.kind == PieceKind.CROSS]
    for cross in crosses:
        if cross.frame is None:
            rep.errors.append(f"{cross.id}: cross rib without a frame")
            continue
        up = cross.slide_sign > 0
        for prof in profiles:
            z = prof.position_mm
            local = cross.frame.to_local(prof.polygon)
            yl = strip_intervals(local, -t / 2, t / 2)
            yt = strip_intervals(cross.polygon, z - t / 2, z + t / 2)
            if not yl or not yt:
                continue
            rep.count("intersections_checked")
            # 1 µm tolerance: coordinates are snapped to a 0.5 µm grid, which moves diagonal
            # slot edges by a fraction of a micron.
            ok = yl[-1][1] <= yt[0][0] + ASSEMBLY_TOL if up else yt[-1][1] <= yl[0][0] + ASSEMBLY_TOL
            if not ok:
                rep.errors.append(f"impossible intersection: {prof.id} and {cross.id} interpenetrate")
    # Cross ribs are not parallel in general: their thickened traces must never touch.
    traces = [_rib_trace(c, t) for c in crosses if c.frame is not None]
    for i in range(len(traces)):
        for j in range(i + 1, len(traces)):
            rep.count("rib_pairs_checked")
            if traces[i].intersects(traces[j]):
                rep.errors.append(f"cross ribs {crosses[i].id} and {crosses[j].id} collide")
    # Profile plates are parallel: overlapping outlines need at least one thickness between them.
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            a, b = profiles[i], profiles[j]
            if abs(a.position_mm - b.position_mm) < t + TOL and a.polygon.intersection(b.polygon).area > 1e-6:
                rep.errors.append(f"plates {a.id} and {b.id} collide")
    return rep


def validate_toolpaths(toolpaths: dict[str, Polygon]) -> ValidationReport:
    rep = ValidationReport()
    for pid, poly in toolpaths.items():
        rep.count("toolpaths")
        rep.errors += validate_polygon(poly, f"{pid} toolpath")
    return rep


def validate_layout(
    placed: dict[str, Polygon], sheet_w: float, sheet_h: float, margin: float, spacing: float
) -> ValidationReport:
    rep = ValidationReport()
    usable = box(margin - TOL, margin - TOL, sheet_w - margin + TOL, sheet_h - margin + TOL)
    ids = list(placed)
    geoms = [placed[i] for i in ids]
    for pid, g in zip(ids, geoms):
        rep.count("placed")
        if not usable.contains(g):
            rep.errors.append(f"{pid} is outside the sheet boundary")
    tree = STRtree(geoms)
    for i, g in enumerate(geoms):
        for j in tree.query(g.buffer(spacing)):
            if j <= i:
                continue
            rep.count("pairs_checked")
            d = g.distance(geoms[j])
            if d < spacing - 1e-4:
                kind = "overlap" if g.intersects(geoms[j]) else "spacing violation"
                rep.errors.append(f"{kind}: {ids[i]} / {ids[j]} (distance {d:.3f} mm)")
    return rep
