"""BoneLibrary: designed, parametric bone outlines (the "design language" of the kit).

Instead of cutting bones out of the silhouette or drawing boxes, every bone type has a
designed profile that is fitted to the measured skeleton (joint positions and local widths):

  long_bone     femur / humerus / tibia: rounded joint knobs (condyles) at both ends, a
                shaft that narrows smoothly in the middle, a slight natural bend
  foot          metatarsal shaft with splayed toes ending in rounded claws
  blade         pelvis / scapula: a smooth rounded blade around the girdle joint and the
                femur head
  rib_arch      arch rib tapering from the crown to the tips, tips curled slightly inwards
  section_rib   rib following a measured body cross-section (3D mesh / volume), open at the belly
  slice_plate   solid slice: the whole cross-section, belly to top of the back (solid-slice style)
  vertebra      rounded centrum, tapered neural spine with a round tip, tapered transverse
                processes angled upwards
  lighten       oval lightening holes along wide bones (real cut openings, full walls kept)

All shapes are templates per *bone type*, never per animal. Every size passes through the
ManufacturingConstraints (minimum feature, walls), and every result is a valid polygon.
"""

from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

from app.geometry.kit.constraints import ManufacturingConstraints
from app.geometry.normalize import largest_polygon


def chaikin(poly: Polygon, iterations: int = 2) -> Polygon:
    """Corner-cutting smoothing of every ring (keeps the topology, rounds hard corners)."""

    def ring(coords: np.ndarray) -> np.ndarray:
        pts = coords[:-1]
        for _ in range(iterations):
            nxt = np.roll(pts, -1, axis=0)
            q = 0.75 * pts + 0.25 * nxt
            r = 0.25 * pts + 0.75 * nxt
            pts = np.empty((2 * len(pts), 2))
            pts[0::2], pts[1::2] = q, r
        return np.vstack([pts, pts[:1]])

    out = Polygon(ring(np.asarray(poly.exterior.coords)), [ring(np.asarray(h.coords)) for h in poly.interiors])
    out = largest_polygon(make_valid(out))
    return out if out is not None and out.area > 0.9 * poly.area else poly


def variable_band(points: np.ndarray, half_widths: np.ndarray, round_ends: bool = True) -> Polygon | None:
    """Band of varying half-width along a polyline (smooth outline, optionally round caps)."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        return None
    tang = np.gradient(pts, axis=0)
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-9)
    nrm = np.stack([-tang[:, 1], tang[:, 0]], 1)
    hw = np.asarray(half_widths, dtype=float)[:, None]
    ring = np.vstack([pts + nrm * hw, (pts - nrm * hw)[::-1]])
    parts = [make_valid(Polygon(ring))]
    if round_ends:
        parts += [Point(pts[0]).buffer(float(hw[0, 0]), quad_segs=10), Point(pts[-1]).buffer(float(hw[-1, 0]), quad_segs=10)]
    return largest_polygon(make_valid(unary_union(parts)))


def _curve(a, b, bend: float, n: int = 40) -> np.ndarray:
    """Quadratic curve from a to b, bulging sideways by `bend` x length."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    L = float(np.linalg.norm(d)) or 1.0
    n_ = np.array([-d[1], d[0]]) / L
    ctrl = (a + b) / 2 + n_ * bend * L
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * a + 2 * (1 - t) * t * ctrl + t ** 2 * b


class BoneLibrary:
    def __init__(self, c: ManufacturingConstraints):
        self.c = c

    # ------------------------------------------------------------------ limbs

    def long_bone(self, a, b, end_a: float, end_b: float, shaft_ratio: float = 0.55, bend: float = 0.03) -> Polygon | None:
        """Long bone between joint centres a and b. `end_a` / `end_b` are the joint-knob radii."""
        mf = self.c.min_feature_size
        pts = _curve(a, b, bend)
        t = np.linspace(0, 1, len(pts))
        shaft = max(shaft_ratio * min(end_a, end_b), 1.3 * mf)
        ends = end_a * (1 - t) + end_b * t
        flare = np.exp(-(t / 0.16) ** 2) + np.exp(-((1 - t) / 0.16) ** 2)
        hw = shaft + (0.82 * ends - shaft) * np.clip(flare, 0, 1)
        band = variable_band(pts, hw, round_ends=False)
        knobs = [Point(a).buffer(end_a, quad_segs=12), Point(b).buffer(end_b, quad_segs=12)]
        out = largest_polygon(make_valid(unary_union([band, *knobs]))) if band is not None else None
        return chaikin(out, 1) if out is not None else None

    def foot(self, ankle, toe, end_a: float, toes: int = 3, splay_deg: float = 22.0) -> Polygon | None:
        """Foot: short metatarsal shaft from the ankle knob, then `toes` tapered toes with claws."""
        mf = self.c.min_feature_size
        a, b = np.asarray(ankle, float), np.asarray(toe, float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 3 * end_a:
            return self.long_bone(a, b, end_a, max(0.6 * end_a, 1.5 * mf), shaft_ratio=0.7, bend=0.0)
        u = d / L
        ball = a + u * 0.55 * L
        meta = self.long_bone(a, ball, end_a, max(0.75 * end_a, 1.6 * mf), shaft_ratio=0.7, bend=0.0)
        parts = [meta]
        toe_w = max(0.42 * end_a, 1.1 * mf)
        reach = 0.5 * L
        angles = np.linspace(-splay_deg, splay_deg, toes) if toes > 1 else [0.0]
        for ang in angles:
            r = math.radians(ang)
            dir_ = np.array([u[0] * math.cos(r) - u[1] * math.sin(r), u[0] * math.sin(r) + u[1] * math.cos(r)])
            tip = ball + dir_ * reach * (1.0 - 0.15 * abs(ang) / max(splay_deg, 1e-6))
            pts = np.linspace(ball, tip, 12)
            hw = np.linspace(toe_w, max(0.62 * toe_w, 0.6 * mf), 12)
            parts.append(variable_band(pts, hw))
        out = largest_polygon(make_valid(unary_union([p for p in parts if p is not None])))
        return chaikin(out, 1) if out is not None else None

    def blade(self, strut: Polygon, joint, radius: float) -> Polygon | None:
        """Pelvis / scapula blade: rounded hull of the girdle strut and the femur-head disc."""
        disc = Point(joint).buffer(radius * 1.25, quad_segs=12)
        hull = unary_union([strut, disc]).convex_hull
        r = 0.35 * radius
        out = largest_polygon(make_valid(hull.buffer(-r).buffer(r * 1.6).buffer(-0.6 * r)))
        return chaikin(out, 1) if out is not None else None

    # ------------------------------------------------------------------ ribs & vertebrae

    def rib_arch(self, half_width: float, bottom: float, crown_top: float, band: float, crown: Polygon,
                 curl: float = 0.12, q: float = 2.4) -> Polygon | None:
        """Open arch rib tapering from the crown to the tips; tips curl slightly inwards."""
        c = self.c
        W = max(half_width, c.crown_half_width + band + c.min_gap)
        t_arch = crown_top - band / 2.0
        H = t_arch - bottom
        if H < 2.0 * band:
            return None
        us = np.linspace(-W, W, 81)
        ts = bottom + H * np.power(np.clip(1.0 - np.abs(us / W) ** q, 0.0, 1.0), 1.0 / q)
        # Curl: the lowest part of each leg turns inwards (towards u = 0).
        low = np.clip((bottom + 0.25 * H - ts) / (0.25 * H), 0.0, 1.0)
        us = us - np.sign(us) * curl * W * low ** 2
        k = np.abs(np.linspace(-1, 1, len(us)))
        hw = band / 2.0 * (1.0 - 0.4 * k ** 1.5)
        hw = np.maximum(hw, 0.6 * c.min_feature_size)
        arch = variable_band(np.stack([us, ts], 1), hw)
        if arch is None:
            return None
        return largest_polygon(make_valid(unary_union([arch, crown])))

    def section_rib(self, section: Polygon, crown: Polygon, band: float, u_scale: float = 1.0,
                    gap_ratio: float = 0.3) -> Polygon | None:
        """Open rib that follows a measured body cross-section (local u = z, t).

        `section` is the body's torso section on the rib plane, already limited to the band
        between the belly line and the crown top. The rib is a band of width `band` just
        inside its outline (so it never leaves the body), running from the crown down both
        flanks and under the belly, open across the middle of the belly (|u - u_c| < gap).
        A section too small for an open band (narrow end of the torso) gives a solid piece of
        the section's shape instead. `u_scale` < 1 narrows the section towards u = 0 (used
        when a plate is in the way)."""
        c = self.c
        mf = c.min_feature_size
        S = Polygon(section.exterior)  # internal cavities of a reconstruction are not ribs
        if u_scale != 1.0:
            S = affinity.scale(S, xfact=u_scale, yfact=1.0, origin=(0.0, 0.0))
        S = largest_polygon(make_valid(S.buffer(-0.5 * mf, quad_segs=6).buffer(0.5 * mf, quad_segs=6)))
        if S is None:
            return None
        x0, y0, x1, y1 = S.bounds
        if y1 - y0 < 1.5 * band:
            return None
        cx0, cy0, cx1, _ = crown.bounds
        inner = S.buffer(-band, quad_segs=8)
        if x1 - x0 >= 2.0 * (c.crown_half_width + band) and not inner.is_empty:
            ring = S.difference(inner)
            uc = S.centroid.x
            gap = max(gap_ratio * 0.5 * (x1 - x0), c.crown_half_width + c.min_gap)
            # Belly opening, centred on the section (which need not be centred on the spine plane).
            ring = ring.difference(box(uc - gap, y0 - 1.0, uc + gap, y0 + 0.5 * (y1 - y0)))
        else:
            ring = S
        # The backbone slot runs down the axis below the crown: nothing may cross u = 0 there or
        # the slot would cut the rib in two.
        ring = ring.difference(box(cx0 - c.min_gap, y0 - 1.0, cx1 + c.min_gap, cy0))
        rib = make_valid(unary_union([ring, crown]))
        r = 0.5 * mf + 0.03  # opening: no sliver or tip thinner than the minimum feature
        rib = make_valid(rib.buffer(-r, quad_segs=6).buffer(r, quad_segs=6))
        rib = make_valid(unary_union([rib, crown]))
        parts = [p for p in getattr(rib, "geoms", [rib]) if isinstance(p, Polygon) and p.intersects(crown)]
        if not parts:
            return None
        rib = max(parts, key=lambda p: p.area)
        if rib.bounds[2] - rib.bounds[0] < cx1 - cx0 + 2.0 * mf:
            return None  # nothing but the slot block: the body is too thin here for a rib
        return rib

    def slice_plate(self, section: Polygon, u_scale: float = 1.0) -> Polygon | None:
        """Solid slice: the body's whole cross-section (belly to top of the back) as one plate.
        Internal cavities of a reconstruction are filled, features thinner than the minimum are
        removed, and the outline is lightly simplified. `u_scale` < 1 narrows it towards u = 0
        (used when a limb or skull plate stands in the way)."""
        c = self.c
        S = Polygon(section.exterior)
        if u_scale != 1.0:
            # narrowed towards u = 0 but never outside the body (an off-centre section shifts when scaled)
            S = make_valid(affinity.scale(S, xfact=u_scale, yfact=1.0, origin=(0.0, 0.0))).intersection(S)
        r = 0.5 * c.min_feature_size + 0.03
        S = largest_polygon(make_valid(S.buffer(-r, quad_segs=6).buffer(r, quad_segs=6)))
        if S is None:
            return None
        S = largest_polygon(make_valid(S.simplify(0.12, preserve_topology=True)))
        return S if S is not None and S.area > 4.0 * c.thickness ** 2 else None

    def vertebra(self, crown: Polygon, crown_top: float, spike: float, wing: float, transverse: bool) -> Polygon | None:
        """Centrum (crown) + tapered neural spine with a round tip + tapered transverse processes."""
        c = self.c
        mf = c.min_feature_size
        cw = c.crown_half_width
        parts = [crown, Point(0, crown_top * 0.35).buffer(cw * 1.05, quad_segs=10).intersection(box(-BIGV, -c.min_slot_length * 0.2, BIGV, BIGV))]
        if spike >= 1.5 * mf:
            tip_r = max(0.65 * mf, 0.22 * cw)
            pts = np.array([[0, crown_top - 0.5], [0, crown_top + spike - tip_r]])
            hw = np.array([0.8 * cw, tip_r])
            parts.append(variable_band(np.linspace(pts[0], pts[1], 10), np.linspace(hw[0], hw[1], 10)))
        if transverse and wing > cw + 1.5 * mf:
            th = max(1.3 * mf, 0.2 * crown_top)
            for sgn in (1, -1):
                root = np.array([0.0, crown_top - th * 0.8])
                tip = np.array([sgn * wing, crown_top - th * 0.8 + 0.25 * wing])
                parts.append(variable_band(np.linspace(root, tip, 10), np.linspace(th * 0.75, max(0.55 * th, 0.6 * mf), 10)))
        out = largest_polygon(make_valid(unary_union([p for p in parts if p is not None])))
        return out

    # ------------------------------------------------------------------ openings

    def lighten(self, plate: Polygon, axis_pts: np.ndarray, keep_out: list[Polygon], max_holes: int = 3) -> Polygon:
        """Oval lightening holes along a bone axis where the bone is wide enough to keep full
        walls (>= min wall on every side, and away from every joint / slot / pocket)."""
        c = self.c
        wall = max(c.min_wall_thickness, 1.2 * c.min_feature_size)
        inner = plate.buffer(-wall)
        if inner.is_empty:
            return plate
        blocked = unary_union([k.buffer(wall) for k in keep_out]) if keep_out else None
        holes: list[Polygon] = []
        axis = LineString(axis_pts) if len(axis_pts) >= 2 else None
        if axis is None or axis.length <= 0:
            return plate
        for f in np.linspace(0.2, 0.8, 7):
            if len(holes) >= max_holes:
                break
            p = axis.interpolate(f, normalized=True)
            q = axis.interpolate(min(1.0, f + 0.02), normalized=True)
            ang = math.degrees(math.atan2(q.y - p.y, q.x - p.x))
            for r in (0.35, 0.28, 0.22):
                rx, ry = r * axis.length * 0.35, None
                # semi-minor axis from the local inner width
                width = inner.intersection(LineString([(p.x - 1e3 * math.sin(math.radians(ang)), p.y + 1e3 * math.cos(math.radians(ang))),
                                                       (p.x + 1e3 * math.sin(math.radians(ang)), p.y - 1e3 * math.cos(math.radians(ang)))])).length
                ry = 0.42 * width
                if ry < 1.1 * c.min_feature_size or rx < 1.5 * ry:
                    continue
                hole = affinity.rotate(affinity.scale(Point(p.x, p.y).buffer(1.0, quad_segs=12), rx, ry), ang, origin=(p.x, p.y))
                if not inner.contains(hole):
                    continue
                if blocked is not None and hole.intersects(blocked):
                    continue
                if any(hole.distance(h) < wall for h in holes):
                    continue
                holes.append(hole)
                break
        if not holes:
            return plate
        out = largest_polygon(make_valid(plate.difference(unary_union(holes))))
        return out if out is not None and out.area > 0.5 * plate.area else plate


BIGV = 1.0e4
