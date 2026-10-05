"""Assembled-kit geometry in the reference frame: exact view projections and voxelization.

Frame recovery
--------------
Generators place pieces with transforms in a *display* frame = canonical frame + shift
(`_set_transforms` in engine.py / kit/builder.py). The spine (role SPINE, canonical origin
(0, 0, 0)) therefore carries the shift in its transform origin, so
    p_reference = transform.apply(u, v, w) - spine.transform.origin
puts every piece back into the frame of the silhouette / mesh the generator was given.

Views (orthographic, reference camera = side view):
  side  (x, y)   front (z, y)   top (x, z)
A piece is a planar slab: its projection is the swept union of the projected face along the
projected thickness vector (exact); when the face projects edge-on, the projection is the
convex hull of both faces' projected vertices (exact for a connected planar polygon).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from shapely.geometry import MultiPoint, Polygon
from shapely.ops import unary_union

from app.geometry.types import PieceRole, PuzzleModel

VIEWS = {"side": (0, 1), "front": (2, 1), "top": (0, 2)}


@dataclass
class Slab:
    origin: np.ndarray  # reference frame
    a: np.ndarray  # local u axis
    b: np.ndarray  # local v axis
    n: np.ndarray  # normal
    polygon: Polygon  # local (u, v)
    thickness: float
    role: str = ""
    region: str = ""
    pid: str = ""


def model_shift(model: PuzzleModel) -> np.ndarray:
    spine = next((pc for pc in model.pieces if pc.role == PieceRole.SPINE), None)
    if spine is None:
        raise ValueError("model has no spine piece to recover the frame from")
    o = np.asarray(spine.transform.origin, float)
    return o - np.array([0.0, 0.0, spine.position_mm])


def model_slabs(model: PuzzleModel) -> list[Slab]:
    shift = model_shift(model)
    T = model.params.thickness_mm
    out = []
    for pc in model.pieces:
        t = pc.transform
        out.append(Slab(np.asarray(t.origin, float) - shift, np.asarray(t.u_axis, float), np.asarray(t.v_axis, float),
                        np.asarray(t.normal, float), pc.polygon, T, pc.role.value, pc.region, pc.id))
    return out


def plate_slab(polygon: Polygon, z: float, T: float, role: str = "") -> Slab:
    return Slab(np.array([0.0, 0.0, z]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]),
                np.array([0.0, 0.0, 1.0]), polygon, T, role)


def cross_slab(polygon: Polygon, frame, T: float, role: str = "") -> Slab:
    a = np.array([0.0, 0.0, 1.0])
    b = np.array([frame.dx, frame.dy, 0.0])
    return Slab(np.array([frame.cx, frame.cy, 0.0]), a, b, np.cross(a, b), polygon, T, role)


def _rings(p: Polygon) -> list[np.ndarray]:
    return [np.asarray(p.exterior.coords)] + [np.asarray(r.coords) for r in p.interiors]


def project_slab(s: Slab, view: str) -> Polygon:
    i, j = VIEWS[view]
    P = np.zeros((2, 3))
    P[0, i] = 1.0
    P[1, j] = 1.0
    A, B, N = P @ s.a, P @ s.b, P @ s.n
    o = P @ s.origin
    half = s.thickness / 2.0

    def lift(ring: np.ndarray, w: float) -> np.ndarray:
        return o + ring[:, :1] * A + ring[:, 1:2] * B + w * N

    det = abs(A[0] * B[1] - A[1] * B[0])
    rings = _rings(s.polygon)
    if det < 1e-9:
        pts = np.vstack([lift(r, w) for r in rings for w in (-half, half)])
        hull = MultiPoint([tuple(p) for p in pts]).convex_hull
        if not isinstance(hull, Polygon):  # a line: give it the projected thickness
            hull = hull.buffer(max(1e-3, half * float(np.linalg.norm(N))), cap_style="flat")
        return hull
    faces = []
    for w in (-half, half):
        lr = [lift(r, w) for r in rings]
        faces.append(Polygon(lr[0], lr[1:]))
    if np.linalg.norm(N) * s.thickness < 1e-6:
        return faces[0]
    sweep = [faces[0], faces[1]]
    for r in rings:
        lo, hi = lift(r, -half), lift(r, half)
        for k in range(len(r) - 1):
            q = Polygon([lo[k], lo[k + 1], hi[k + 1], hi[k]])
            if q.area > 1e-9:
                sweep.append(q)
    return unary_union(sweep)


def project(slabs: list[Slab], view: str):
    return unary_union([project_slab(s, view) for s in slabs])


# ------------------------------------------------------------------------------ voxels


@dataclass
class Grid:
    x0: float
    y0: float
    z0: float
    res: float
    shape: tuple[int, int, int]  # (nz, ny, nx)

    def centers(self, axis: int) -> np.ndarray:
        n = self.shape[2 - axis]
        o = (self.x0, self.y0, self.z0)[axis]
        return o + (np.arange(n) + 0.5) * self.res


def grid_for(bounds_list, res: float, pad: float = 4.0) -> Grid:
    lo = np.min([b[0] for b in bounds_list], axis=0) - pad
    hi = np.max([b[1] for b in bounds_list], axis=0) + pad
    shape = tuple(int(np.ceil((hi[k] - lo[k]) / res)) for k in (2, 1, 0))
    return Grid(float(lo[0]), float(lo[1]), float(lo[2]), res, shape)


def slab_bounds(s: Slab) -> tuple[np.ndarray, np.ndarray]:
    ring = np.asarray(s.polygon.exterior.coords)
    pts = [s.origin + ring[:, :1] * s.a + ring[:, 1:2] * s.b + w * s.n for w in (-s.thickness / 2, s.thickness / 2)]
    pts = np.vstack(pts)
    return pts.min(axis=0), pts.max(axis=0)


def voxelize(slabs: list[Slab], g: Grid) -> np.ndarray:
    occ = np.zeros(g.shape, bool)
    xs, ys, zs = g.centers(0), g.centers(1), g.centers(2)
    for s in slabs:
        lo, hi = slab_bounds(s)
        ix = np.nonzero((xs >= lo[0] - g.res) & (xs <= hi[0] + g.res))[0]
        iy = np.nonzero((ys >= lo[1] - g.res) & (ys <= hi[1] + g.res))[0]
        iz = np.nonzero((zs >= lo[2] - g.res) & (zs <= hi[2] + g.res))[0]
        if not len(ix) or not len(iy) or not len(iz):
            continue
        Z, Y, X = np.meshgrid(zs[iz], ys[iy], xs[ix], indexing="ij")
        d = np.stack([X - s.origin[0], Y - s.origin[1], Z - s.origin[2]], axis=-1)
        u, v, w = d @ s.a, d @ s.b, d @ s.n
        near = np.abs(w) <= s.thickness / 2.0
        if not near.any():
            continue
        # point-in-polygon through a fine local raster of the piece outline
        minx, miny, maxx, maxy = s.polygon.bounds
        r = g.res / 2.0
        shape = (int(np.ceil((maxy - miny) / r)) + 3, int(np.ceil((maxx - minx) / r)) + 3)
        mask = np.zeros(shape, np.uint8)
        for k, ring in enumerate(_rings(s.polygon)):
            pts = np.round((ring - (minx - r, miny - r)) / r * 16).astype(np.int32)
            cv2.fillPoly(mask, [pts], 1 if k == 0 else 0, lineType=cv2.LINE_8, shift=4)
        ci = np.round((u - (minx - r)) / r).astype(int)
        ri = np.round((v - (miny - r)) / r).astype(int)
        ok = near & (ci >= 0) & (ri >= 0) & (ci < shape[1]) & (ri < shape[0])
        inside = np.zeros_like(near)
        inside[ok] = mask[ri[ok], ci[ok]] > 0
        sub = occ[np.ix_(iz, iy, ix)]
        occ[np.ix_(iz, iy, ix)] = sub | inside
    return occ


def mesh_occupancy(mesh, g: Grid) -> np.ndarray:
    """Solid occupancy of a closed mesh on grid g (z-slices via the production plane_section)."""
    from app.geometry.normalize import polygon_parts
    from app.geometry.volume import _frame, plane_section

    occ = np.zeros(g.shape, bool)
    lo, hi = mesh.bounds
    for k, z in enumerate(g.centers(2)):
        if not (lo[2] < z < hi[2]):
            continue
        region = plane_section(mesh, _frame((0.0, 0.0, z), (1, 0, 0), (0, 1, 0)))
        if region is None:
            continue
        layer = np.zeros(g.shape[1:], np.uint8)
        for part in polygon_parts(region):
            ext = np.round((np.asarray(part.exterior.coords) - (g.x0, g.y0)) / g.res * 16 - 8).astype(np.int32)
            cv2.fillPoly(layer, [ext], 1, lineType=cv2.LINE_8, shift=4)
            for h in part.interiors:
                hh = np.round((np.asarray(h.coords) - (g.x0, g.y0)) / g.res * 16 - 8).astype(np.int32)
                cv2.fillPoly(layer, [hh], 0, lineType=cv2.LINE_8, shift=4)
        occ[k] = layer > 0
    return occ


def depth_occupancy(silhouette: Polygon, depth, g: Grid) -> np.ndarray:
    """The 2.5D body a generator sees: inside the silhouette and |z| <= half-depth(x, y)."""
    from fidelity.metrics import rasterize

    xs, ys, zs = g.centers(0), g.centers(1), g.centers(2)
    inside = rasterize(silhouette, g.x0 + g.res / 2, g.y0 + g.res / 2, g.res, g.shape[1:])  # voxel centres
    X, Y = np.meshgrid(xs, ys)
    D = depth.sample(X, Y).reshape(X.shape) * inside
    return np.abs(zs)[:, None, None] <= D[None, :, :]
