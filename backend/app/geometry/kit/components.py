"""Parameterised component generators for fossil-style kits.

Every generator is a pure function of dimensions and ManufacturingConstraints; none of them
knows which animal it is building. Cross pieces (ribs, vertebrae, spacers) are returned in
their local (u, t) frame: u = depth (z), t = along the piece's trace, the backbone joint
split at t = 0. Plates (spine, skull, jaw, limbs, connectors) are returned in the side
view (x, y).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import split, unary_union
from shapely.validation import make_valid

from app.geometry.kit.constraints import ManufacturingConstraints
from app.geometry.normalize import largest_polygon, polygon_parts

GRID = 0.0005


def clean(geom) -> Polygon | None:
    """Largest valid polygon, snapped to the 0.5 µm grid (stable for later boolean ops)."""
    try:
        poly = largest_polygon(make_valid(geom))
        if poly is None or poly.is_empty:
            return None
        poly = largest_polygon(make_valid(shapely.set_precision(poly.simplify(0.02), GRID)))
        if poly is None or not poly.is_valid:
            return None
        poly = largest_polygon(poly.buffer(0))  # drops zero-width spikes left by snapping
        if poly is None:
            return None
        # Round-trip through WKB-equivalent coordinates so GEOS sees a clean ring structure.
        poly = Polygon(poly.exterior.coords, [r.coords for r in poly.interiors])
        if not poly.is_valid:
            poly = largest_polygon(make_valid(poly))
        return shapely.geometry.polygon.orient(poly, 1.0) if poly is not None and poly.is_valid else None
    except shapely.errors.GEOSException:
        return None


def rounded_box(x0: float, y0: float, x1: float, y1: float, r: float) -> Polygon:
    r = min(r, 0.49 * (x1 - x0), 0.49 * (y1 - y0))
    if r <= 0:
        return box(x0, y0, x1, y1)
    return box(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, quad_segs=6)


# ------------------------------------------------------------------------------ joints


class JointGenerator:
    """Half-lap slot geometry from material thickness, clearance and engagement."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def crown(self, top: float, bottom: float | None = None) -> Polygon:
        """Solid block around a backbone slot of a cross piece: from below the split (prongs of
        `min_slot_length`) to `top`."""
        cw = self.c.crown_half_width
        lo = -self.c.min_slot_length if bottom is None else bottom
        return rounded_box(-cw, lo, cw, top, 0.35 * self.c.min_wall_thickness)

    def cross_cutter(self, u: float, split_t: float, from_above: bool) -> Polygon:
        """Channel removed from a cross piece at depth u: from the split through its edge."""
        h = self.c.slot_width / 2.0
        return box(u - h, -1e5, u + h, split_t) if from_above else box(u - h, split_t, u + h, 1e5)


# ------------------------------------------------------------------------------ ribs


class RibGenerator:
    """Open arch ribs: two legs hanging from a crown that straddles the backbone.

    The arch centreline is a superellipse t = t_bottom + H (1 - |u/W|^q)^(1/q), offset by
    half the rib width; the bottom stays open. The crown block carries the backbone slot.
    """

    def __init__(self, c: ManufacturingConstraints, curvature: float = 2.4):
        self.c = c
        self.q = curvature
        self.joint = JointGenerator(c)

    def band_width(self, body_height: float) -> float:
        mf = self.c.min_feature_size
        return float(np.clip(0.05 * body_height, 1.6 * mf, 3.2 * mf))

    def make(self, half_width: float, bottom: float, crown_top: float, band: float) -> Polygon | None:
        c = self.c
        cw = c.crown_half_width
        W = max(half_width, cw + band + c.min_gap)
        t_arch = crown_top - band / 2.0
        if t_arch - bottom < 2.0 * band:
            return None
        us = np.linspace(-W, W, 61)
        ts = bottom + (t_arch - bottom) * np.power(np.clip(1.0 - np.abs(us / W) ** self.q, 0.0, 1.0), 1.0 / self.q)
        arch = LineString(np.stack([us, ts], 1)).buffer(band / 2.0, quad_segs=6)
        rib = unary_union([arch, self.joint.crown(crown_top)])
        return clean(rib)


# ------------------------------------------------------------------------------ vertebrae


class VertebraGenerator:
    """Vertebra plate: centrum block with the backbone slot, a neural spine (spike) above and
    optional transverse processes. All sizes scale with the local body."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c
        self.joint = JointGenerator(c)

    def make(self, crown_top: float, spike: float, wing: float, lean: float = 0.0,
             transverse: bool = True) -> Polygon | None:
        c = self.c
        cw = c.crown_half_width
        mf = c.min_feature_size
        parts = [self.joint.crown(crown_top)]
        if spike >= 1.5 * mf:
            tip = max(1.2 * mf, 0.35 * cw)
            parts.append(Polygon([(-0.85 * cw, crown_top - 0.5), (0.85 * cw, crown_top - 0.5),
                                  (lean + tip / 2, crown_top + spike), (lean - tip / 2, crown_top + spike)]))
        if transverse and wing > cw + 1.5 * mf:
            th = max(1.4 * mf, 0.22 * crown_top)
            t0 = crown_top - th
            parts.append(rounded_box(-wing, t0, wing, crown_top, 0.45 * th))
        return clean(unary_union(parts))


# ------------------------------------------------------------------------------ spacers


class SpacerGenerator:
    """Beam joining parallel plates (skull sides, jaw sides, limb girdles) through half-lap
    slots. `u_positions` are the plate depths; the beam spans them plus walls."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def make(self, u_positions: list[float], bottom: float, top: float) -> Polygon | None:
        c = self.c
        # From the outermost plate on one side to the outermost on the other (plates need not be
        # symmetric: a measured body can sit off the spine plane).
        margin = c.thickness / 2.0 + c.min_wall_thickness
        lo, hi = min(min(u_positions), 0.0) - margin, max(max(u_positions), 0.0) + margin
        if top - bottom < 2.0 * c.min_slot_length:
            return None
        return clean(rounded_box(lo, bottom, hi, top, 0.3 * c.min_wall_thickness))


# ------------------------------------------------------------------------------ connectors


class ConnectorGenerator:
    """Bowtie (butterfly key) connector joining two coplanar plates across a seam.

    The bowtie is a real cut piece; each host plate gets a pocket equal to the bowtie grown by
    clearance/2 on every side (same fit play as the slots)."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def dims(self, scale: float = 1.0) -> tuple[float, float, float]:
        ww = self.c.min_connector_width * scale
        nw = max(1.25 * self.c.min_feature_size, 0.55 * ww)
        return ww, nw, 2.0 * ww  # wide end, neck, length

    def shape(self, center, direction, scale: float = 1.0) -> Polygon:
        """Bowtie whose length runs along `direction` (across the seam)."""
        ww, nw, length = self.dims(scale)
        h = length / 2.0
        local = Polygon([(-h, -ww / 2), (0, -nw / 2), (h, -ww / 2), (h, ww / 2), (0, nw / 2), (-h, ww / 2)])
        ang = math.degrees(math.atan2(direction[1], direction[0]))
        return affinity.translate(affinity.rotate(local, ang, origin=(0, 0)), center[0], center[1])

    def pocket(self, bowtie: Polygon) -> Polygon:
        return bowtie.buffer(self.c.clearance / 2.0, join_style="mitre", mitre_limit=4.0)

    def boss(self, center, direction, scale: float = 1.0) -> Polygon:
        """Material needed around a bowtie so the pocket keeps its walls."""
        ww, _, length = self.dims(scale)
        w = self.c.min_wall_thickness + self.c.clearance
        d = np.asarray(direction, dtype=float)
        d /= np.linalg.norm(d) or 1.0
        local = rounded_box(-length / 2 - w, -ww / 2 - w, length / 2 + w, ww / 2 + w, w)
        ang = math.degrees(math.atan2(d[1], d[0]))
        return affinity.translate(affinity.rotate(local, ang, origin=(0, 0)), center[0], center[1])

    def fits(self, host_union: Polygon, bowtie: Polygon) -> bool:
        wall = self.c.min_wall_thickness
        return host_union.buffer(1e-6).contains(self.pocket(bowtie).buffer(wall * 0.95))


# ------------------------------------------------------------------------------ backbone


class SpineGenerator:
    """Backbone bar along the body axis with joint bosses at segment seams."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def bar(self, pts: np.ndarray, normals: np.ndarray, half_heights: np.ndarray,
            dorsal_extra: np.ndarray | None = None) -> Polygon | None:
        """Band around the axis; `dorsal_extra` raises the dorsal edge (vertebral bumps)."""
        up = half_heights + (dorsal_extra if dorsal_extra is not None else 0.0)
        left = pts + normals * up[:, None]
        right = pts - normals * half_heights[:, None]
        ring = np.vstack([left, right[::-1]])
        poly = make_valid(Polygon(ring))
        caps = [Point(pts[0]).buffer(half_heights[0], quad_segs=8), Point(pts[-1]).buffer(half_heights[-1], quad_segs=8)]
        return clean(unary_union([poly, *caps]))


def split_by_line(poly: Polygon, a, b) -> list[Polygon]:
    """Split a polygon with the infinite line through a and b."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    d /= np.linalg.norm(d) or 1.0
    line = LineString([tuple(a - d * 1e4), tuple(a + d * 1e4)])
    return [g for g in polygon_parts(split(poly, line)) if g.area > 1e-6]


def split_by_segment(poly: Polygon, a, b) -> list[Polygon]:
    """Split a polygon with the finite segment a-b (only where the segment crosses it)."""
    return [g for g in polygon_parts(split(poly, LineString([tuple(a), tuple(b)]))) if g.area > 1e-6]


def side_of(point, a, d) -> float:
    return float((point[0] - a[0]) * d[1] - (point[1] - a[1]) * d[0])


# ------------------------------------------------------------------------------ skull


class SkullGenerator:
    """Side plates of a skull: outline with a real eye socket and nostril, split from the
    lower jaw along a mouth line, with a tapering mouth gap and teeth. Works in a local head
    frame (xi along the head from its base to the tip, eta dorsal)."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def openings(self, upper: Polygon, center, radius: float, keep_out: list[Polygon],
                 aspect: float = 1.0) -> Polygon | None:
        """A round (or oval, `aspect` = length / height) opening if it keeps a full wall to the
        outline and to joints."""
        c = self.c
        if radius < 1.2 * c.min_feature_size:
            return None
        hole = Point(center).buffer(radius, quad_segs=10)
        if aspect != 1.0:
            hole = affinity.scale(hole, aspect, 1.0, origin=tuple(center))
        if not upper.buffer(-c.min_wall_thickness).contains(hole):
            return None
        if any(hole.buffer(c.min_wall_thickness).intersects(k) for k in keep_out):
            return None
        return hole

    def mouth(self, head: Polygon, eta_m: float, xi_gap: float, xi_tip: float, gap_max: float,
              teeth: bool) -> tuple[Polygon | None, Polygon | None, Polygon | None]:
        """Upper skull, lower jaw and the removed mouth gap (all in the head frame)."""
        c = self.c
        minx, miny, maxx, maxy = head.bounds
        upper = clean(head.intersection(box(minx - 1, eta_m, maxx + 1, maxy + 1)))
        jaw = clean(head.intersection(box(minx - 1, miny - 1, maxx + 1, eta_m)))
        if upper is None or jaw is None:
            return None, None, None
        if xi_tip - xi_gap < 3 * c.min_gap or gap_max < c.min_gap:
            return upper, jaw, None
        top = [(xi_gap, eta_m), (xi_tip + 5, eta_m + gap_max / 2 + 1)]
        bottom = [(xi_tip + 5, eta_m - gap_max / 2 - 1)]
        gap = Polygon(top + bottom)
        upper_g = clean(upper.difference(gap))
        jaw_g = clean(jaw.difference(gap))
        if upper_g is None or jaw_g is None:
            return upper, jaw, None
        if teeth:
            pitch = max(3.0 * c.min_feature_size, 2.0 * c.thickness)
            span = xi_tip - xi_gap
            n = int(span / pitch)
            up_teeth, low_teeth = [], []
            for k in range(1, n):
                xi = xi_gap + k * pitch
                g = gap_max * (xi - xi_gap) / max(span, 1e-6)
                if g < 2.4 * c.min_gap:
                    continue
                h = 0.42 * g
                base = 0.7 * pitch
                eu = eta_m + g / 2.0
                el = eta_m - g / 2.0
                up_teeth.append(Polygon([(xi - base / 2, eu + 0.3), (xi + base / 2, eu + 0.3), (xi, eu - h)]))
                xl = xi + pitch / 2.0
                gl = gap_max * (xl - xi_gap) / max(span, 1e-6)
                if xl < xi_tip - pitch / 2 and gl >= 2.4 * c.min_gap:
                    ell = eta_m - gl / 2.0
                    low_teeth.append(Polygon([(xl - base / 2, ell - 0.3), (xl + base / 2, ell - 0.3), (xl, ell + 0.42 * gl)]))
            if up_teeth:
                cand = clean(unary_union([upper_g, *[t.intersection(head) for t in up_teeth]]))
                upper_g = cand or upper_g
            if low_teeth:
                cand = clean(unary_union([jaw_g, *[t.intersection(head) for t in low_teeth]]))
                jaw_g = cand or jaw_g
        return upper_g, jaw_g, gap


class JawGenerator:
    """The lower jaw is cut by SkullGenerator.mouth; this checks it is a usable piece."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    def usable(self, jaw: Polygon | None) -> bool:
        if jaw is None:
            return False
        minx, miny, maxx, maxy = jaw.minimum_rotated_rectangle.bounds
        return jaw.area >= 12.0 * self.c.thickness ** 2 and min(maxx - minx, maxy - miny) >= 2.5 * self.c.min_slot_length


# ------------------------------------------------------------------------------ limbs


class LimbGenerator:
    """Splits a limb plate into bone segments (upper limb, lower limb, foot) along its axis.

    Seams are straight cuts perpendicular to the local axis at given fractions of its length;
    a seam is only used when a bowtie connector fits across it with full walls."""

    def __init__(self, c: ManufacturingConstraints):
        self.c = c
        self.conn = ConnectorGenerator(c)

    def seams(self, plate: Polygon, axis: np.ndarray, fractions: list[float]) -> list[tuple[np.ndarray, np.ndarray]]:
        seg = np.hypot(*np.diff(axis, axis=0).T)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        out = []
        for f in fractions:
            for df in (0.0, 0.06, -0.06, 0.12, -0.12):
                ff = float(np.clip(f + df, 0.15, 0.9))
                at = ff * s[-1]
                i = int(np.clip(np.searchsorted(s, at) - 1, 0, len(axis) - 2))
                w = (at - s[i]) / max(s[i + 1] - s[i], 1e-9)
                p = axis[i] * (1 - w) + axis[i + 1] * w
                lo, hi = max(0, i - 3), min(len(axis) - 1, i + 4)
                d = axis[hi] - axis[lo]
                d = d / (np.linalg.norm(d) or 1.0)
                bt = self.conn.shape(p, d)
                if self.conn.fits(plate, bt) and len(split_by_line(plate, p, p + np.array([-d[1], d[0]]))) >= 2:
                    out.append((p, d))
                    break
        return out


class TailGenerator:
    """Tail vertebrae follow the axis and shrink with the local tail size (see builder)."""

    shrink = 0.92  # each tail vertebra at most this fraction of the previous one


class WingGenerator(LimbGenerator):
    """Wings and fins are segmented exactly like limbs (inner / outer panel)."""
