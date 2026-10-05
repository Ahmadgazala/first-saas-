"""Volumes: the 3D body the slicer cuts pieces from.

Two implementations share one interface:

* DepthVolume - the stylized body estimated from a silhouette (region-aware inflation).
  Symmetric about z = 0; sections are derived from the depth field.
* MeshVolume  - a real triangle mesh (AI reconstruction from a photo, or an uploaded STL /
  OBJ / GLB / PLY model). Sections are exact plane cuts through the mesh.

Canonical frame (millimetres): x along the body, y up, z depth; the spine plane is z = 0.
A plate at depth z occupies the slab [z - T/2, z + T/2]; its outline is the intersection of
the two faces' sections so the slab never pokes out of the body. Cross ribs lie in planes
that contain the z axis (see RibFrame); their local coordinates are (u = z, v = t).
"""

from __future__ import annotations

import io
import logging
from abc import ABC, abstractmethod

import cv2
import numpy as np
import shapely
import trimesh
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import linemerge
from shapely.validation import make_valid

from app.core.errors import ErrorCode, PuzzleError
from app.geometry.depth import DepthField, raster_to_geometry
from app.geometry.normalize import largest_polygon, polygon_parts
from app.geometry.types import RibFrame

logger = logging.getLogger(__name__)

MAX_INPUT_FACES = 3_000_000
TARGET_FACES = 60_000


# ------------------------------------------------------------------------------ plane sections


def _bodies(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Connected shells of a mesh (cached). Overlapping shells are combined by union.

    The cache lives in `mesh.metadata`, which trimesh copies along with `mesh.copy()`. It is
    therefore keyed by the vertex data (trimesh's cached array hash), so a copied and then
    transformed mesh never reuses the shells of its untransformed source."""
    key = (hash(mesh.vertices), len(mesh.faces))
    cached = mesh.metadata.get("_bodies")
    if not (isinstance(cached, tuple) and len(cached) == 2 and cached[0] == key):
        parts = mesh.split(only_watertight=False) if len(mesh.faces) else []
        cached = (key, [b for b in parts if len(b.faces) >= 4] or [mesh])
        mesh.metadata["_bodies"] = cached
    return cached[1]


def _shell_section(body: trimesh.Trimesh, frame: np.ndarray, inv: np.ndarray):
    """Section of one closed shell: rings from the intersection segments, even-odd filled."""
    segs = trimesh.intersections.mesh_plane(body, frame[:3, 2], frame[:3, 3])
    if len(segs) == 0:
        return None
    pts = segs.reshape(-1, 3) @ inv[:3, :3].T + inv[:3, 3]
    pts2 = np.round(pts[:, :2], 5).reshape(-1, 2, 2)
    keep = np.linalg.norm(pts2[:, 0] - pts2[:, 1], axis=1) > 1e-9
    if not keep.any():
        return None
    lines = MultiLineString([LineString(seg) for seg in pts2[keep]])
    merged = linemerge(shapely.set_precision(lines, 1e-4))
    region = None
    for line in getattr(merged, "geoms", [merged]):
        coords = list(line.coords)
        if len(coords) < 4:
            continue
        if not line.is_ring:
            # Tiny numerical gaps: close rings whose ends nearly meet, ignore open debris.
            if np.hypot(coords[0][0] - coords[-1][0], coords[0][1] - coords[-1][1]) > 0.05:
                continue
            coords = coords + [coords[0]]
        poly = make_valid(Polygon(coords))
        if poly.is_empty or poly.area < 1e-6:
            continue
        region = poly if region is None else region.symmetric_difference(poly)
    return region


def plane_section(mesh: trimesh.Trimesh, frame: np.ndarray):
    """Section of a mesh by the plane local z = 0 of `frame` (4x4, local -> world).

    Returns the enclosed region in local (u, v) coordinates as a (Multi)Polygon, or None.
    Each connected shell is filled with the even-odd rule (nested outlines become holes and
    islands) and shells are combined by union, so overlapping parts of a model (an eye
    pushed into a head, overlapping primitives) do not cancel out.
    """
    inv = np.linalg.inv(frame)
    regions = []
    for body in _bodies(mesh):
        lo, hi = body.bounds
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        side = (corners - frame[:3, 3]) @ frame[:3, 2]
        if side.min() > 0 or side.max() < 0:
            continue  # the plane misses this shell
        region = _shell_section(body, frame, inv)
        if region is not None and not region.is_empty:
            regions.append(region)
    if not regions:
        return None
    merged = make_valid(shapely.union_all(regions))
    return None if merged.is_empty else merged


def _frame(origin, u, v) -> np.ndarray:
    u = np.asarray(u, float)
    v = np.asarray(v, float)
    n = np.cross(u, v)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = u, v, n, origin
    return m


# ------------------------------------------------------------------------------ interface


class Volume(ABC):
    """A body in the canonical frame. `silhouette` is its projection on the xy plane,
    `depth` its half-thickness around the spine plane (used for fast estimates)."""

    kind: str
    silhouette: Polygon
    depth: DepthField

    @abstractmethod
    def spine_section(self, thickness: float):
        """Region of the spine slab |z| <= T/2."""

    @abstractmethod
    def profile_section(self, z: float, thickness: float):
        """Region of the plate slab [z - T/2, z + T/2] that lies inside the body."""

    @abstractmethod
    def cross_section(self, frame: RibFrame, a: float, b: float, thickness: float):
        """Region of a cross-rib slab on `frame`, in (u = z, v = t) coordinates."""

    @abstractmethod
    def z_extent(self) -> tuple[float, float]: ...


class DepthVolume(Volume):
    kind = "estimated"

    def __init__(self, silhouette: Polygon, depth: DepthField):
        self.silhouette = silhouette
        self.depth = depth

    def spine_section(self, thickness: float):
        return self.silhouette

    def profile_section(self, z: float, thickness: float):
        level = self.depth.level_polygon(abs(z) + thickness / 2.0)
        if level is None:
            return None
        return make_valid(level.intersection(self.silhouette))

    def cross_section(self, frame: RibFrame, a: float, b: float, thickness: float):
        n = int(np.clip((b - a) / 0.5, 40, 400))
        ts = np.linspace(a, b, n)
        d = self.depth.sample(frame.cx + ts * frame.dx, frame.cy + ts * frame.dy)
        d[0] = d[-1] = 0.0
        d = np.maximum(d, 0.0)
        right = list(zip(d.tolist(), ts.tolist()))
        left = list(zip((-d[1:-1]).tolist(), ts[1:-1].tolist()))[::-1]
        return make_valid(Polygon(right + left))

    def z_extent(self) -> tuple[float, float]:
        m = self.depth.max_half_depth
        return -m, m

    def z_intervals(self, x: float, y: float, half_width: float = 1.0) -> list[tuple[float, float]]:
        """Depth intervals where the body is solid along the z line through (x, y)."""
        d = float(self.depth.sample(np.array([x]), np.array([y]))[0])
        return [(-d, d)] if d > 0 else []


class MeshVolume(Volume):
    """A real mesh, already in the canonical frame and scale (see `prepare_mesh`)."""

    kind = "mesh"

    def __init__(self, mesh: trimesh.Trimesh, res: float | None = None):
        self.mesh = mesh
        lo, hi = mesh.bounds
        size = float(max(hi[0] - lo[0], hi[1] - lo[1]))
        self.res = res or max(size / 500.0, 0.25)
        self.silhouette, self.depth = _projection_and_depth(mesh, self.res)

    def _slab(self, frame_at, thickness: float):
        a = plane_section(self.mesh, frame_at(-thickness / 2.0))
        b = plane_section(self.mesh, frame_at(thickness / 2.0))
        if a is None or b is None:
            return None
        region = make_valid(a.intersection(b))
        return None if region.is_empty else region

    def spine_section(self, thickness: float):
        return self.profile_section(0.0, thickness)

    def profile_section(self, z: float, thickness: float):
        return self._slab(lambda off: _frame((0.0, 0.0, z + off), (1, 0, 0), (0, 1, 0)), thickness)

    def cross_section(self, frame: RibFrame, a: float, b: float, thickness: float):
        n = np.array([frame.n[0], frame.n[1], 0.0])  # plane normal (u x v points along -n)
        u, v = (0.0, 0.0, 1.0), (frame.dx, frame.dy, 0.0)

        def at(off: float):
            # u x v = -n, so moving the origin along -n shifts the plane along its normal.
            return _frame((frame.cx - n[0] * off, frame.cy - n[1] * off, 0.0), u, v)

        return self._slab(at, thickness)

    def z_extent(self) -> tuple[float, float]:
        lo, hi = self.mesh.bounds
        return float(lo[2]), float(hi[2])

    def z_intervals(self, x: float, y: float, half_width: float = 1.0) -> list[tuple[float, float]]:
        """Depth intervals (z) of the solid parts of the mesh near the z line through (x, y):
        a horizontal section at height y, cut to the strip |x' - x| <= half_width. Two legs of a
        pair standing side by side give two intervals, one each side of the spine plane."""
        region = plane_section(self.mesh, _frame((0.0, y, 0.0), (1, 0, 0), (0, 0, 1)))  # local (u, v) = (x, z)
        if region is None:
            return []
        strip = make_valid(region).intersection(shapely.box(x - half_width, -1e5, x + half_width, 1e5))
        return sorted((float(q.bounds[1]), float(q.bounds[3])) for q in polygon_parts(strip) if q.area > 1e-3)


# ------------------------------------------------------------------------------ mesh preparation


def _projection_and_depth(mesh: trimesh.Trimesh, res: float) -> tuple[Polygon, DepthField]:
    """Silhouette on the xy plane and half-thickness around z = 0, from the occupancy grid."""
    occ, x0, y0, z0, pitch = occupancy(mesh, res)
    proj = occ.any(axis=0)
    geom = raster_to_geometry(proj, x0, y0, pitch)
    silhouette = largest_polygon(make_valid(geom)) if geom is not None else None
    if silhouette is None:
        raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "The 3D model is empty.", "no projection")
    # Half-thickness around the spine plane: distance from z = 0 to the nearest empty voxel
    # in the run that contains z = 0 (0 where the spine plane is empty).
    k0 = int(round((0.0 - z0) / pitch))
    k0 = int(np.clip(k0, 0, occ.shape[0] - 1))
    nz = occ.shape[0]
    filled = occ[k0]
    up = np.zeros(filled.shape, np.float32)
    down = np.zeros(filled.shape, np.float32)
    alive = filled.copy()
    for k in range(k0 + 1, nz):
        alive &= occ[k]
        up += alive
    alive = filled.copy()
    for k in range(k0 - 1, -1, -1):
        alive &= occ[k]
        down += alive
    half = np.where(filled, (np.minimum(up, down) + 0.5) * pitch, 0.0).astype(np.float32)
    half = cv2.GaussianBlur(half, (0, 0), 1.0) * filled
    return silhouette, DepthField(half, x0, y0, pitch)


def occupancy(mesh: trimesh.Trimesh, res: float):
    """Solid occupancy grid [z][y][x] from z-slices of the mesh (works for closed meshes)."""
    lo, hi = mesh.bounds
    pad = 2 * res
    x0, y0, z0 = lo[0] - pad, lo[1] - pad, lo[2] - pad
    nx = int(np.ceil((hi[0] + pad - x0) / res)) + 1
    ny = int(np.ceil((hi[1] + pad - y0) / res)) + 1
    nz = int(np.ceil((hi[2] + pad - z0) / res)) + 1
    occ = np.zeros((nz, ny, nx), dtype=bool)
    for k in range(nz):
        z = z0 + k * res
        if not (lo[2] < z < hi[2]):
            continue
        region = plane_section(mesh, _frame((0.0, 0.0, z), (1, 0, 0), (0, 1, 0)))
        if region is None:
            continue
        layer = np.zeros((ny, nx), np.uint8)
        for part in polygon_parts(region):
            ext = np.round((np.asarray(part.exterior.coords) - (x0, y0)) / res * 16).astype(np.int32)
            cv2.fillPoly(layer, [ext], 1, lineType=cv2.LINE_8, shift=4)
            for hole in part.interiors:
                h = np.round((np.asarray(hole.coords) - (x0, y0)) / res * 16).astype(np.int32)
                cv2.fillPoly(layer, [h], 0, lineType=cv2.LINE_8, shift=4)
        occ[k] = layer > 0
    return occ, x0, y0, z0, res


def load_mesh_bytes(data: bytes, extension: str) -> trimesh.Trimesh:
    """Safely parse an uploaded 3D model (no scripts, no external references)."""
    try:
        loaded = trimesh.load(io.BytesIO(data), file_type=extension, force="mesh", process=True)
    except Exception as exc:
        raise PuzzleError(ErrorCode.INVALID_FILE, "The 3D model could not be read.", repr(exc)) from exc
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise PuzzleError(ErrorCode.INVALID_FILE, "The 3D model contains no surface.")
    if len(loaded.faces) > MAX_INPUT_FACES:
        raise PuzzleError(ErrorCode.INVALID_FILE, "The 3D model is too detailed (more than 3 million faces).")
    return loaded


def remesh(mesh: trimesh.Trimesh, resolution: int = 140) -> trimesh.Trimesh:
    """Watertight, single-body, smooth version of any mesh (voxelise + marching cubes).

    Repairs open or self-intersecting models, merges overlapping parts and removes
    internal faces, so plane sections are always closed outlines.
    """
    from scipy import ndimage
    from skimage.measure import marching_cubes

    size = float(max(mesh.extents))
    if size <= 0:
        raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "The 3D model is empty.")
    occ, x0, y0, z0, pitch = occupancy(mesh, size / resolution)
    if not occ.any():
        raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "The 3D model is not a closed solid and cannot be sliced.")
    labels, n = ndimage.label(occ)
    if n > 1:
        sizes = ndimage.sum(occ, labels, range(1, n + 1))
        occ = labels == (int(np.argmax(sizes)) + 1)
    # Smooth enough to remove texture-induced bumps (stripes, fur) from AI reconstructions.
    field = ndimage.gaussian_filter(occ.astype(np.float32), 1.3)
    verts, faces, _, _ = marching_cubes(np.pad(field, 1), level=0.5)
    verts = verts - 1.0
    # (k, j, i) index order -> (x, y, z) mm
    xyz = np.stack([x0 + verts[:, 2] * pitch, y0 + verts[:, 1] * pitch, z0 + verts[:, 0] * pitch], axis=1)
    out = trimesh.Trimesh(xyz, faces[:, ::-1], process=True)
    trimesh.smoothing.filter_taubin(out, iterations=10)
    if len(out.faces) > TARGET_FACES:
        try:
            out = out.simplify_quadric_decimation(face_count=TARGET_FACES)
        except Exception:  # decimation is an optimisation only
            logger.warning("mesh decimation failed", exc_info=True)
    out.fix_normals()
    return out


def prepare_mesh(mesh: trimesh.Trimesh, rotation: np.ndarray, target_size_mm: float) -> trimesh.Trimesh:
    """Rotate into the canonical frame, scale so the projected silhouette's longest side is
    the puzzle size, rest on y = 0, centre x, and put the spine plane (z centre of mass) at 0."""
    m = mesh.copy()
    T = np.eye(4)
    T[:3, :3] = rotation
    m.apply_transform(T)
    lo, hi = m.bounds
    size = float(max(hi[0] - lo[0], hi[1] - lo[1]))
    m.apply_scale(target_size_mm / size)
    lo, hi = m.bounds
    zc = float(m.center_mass[2]) if m.is_volume else float((lo[2] + hi[2]) / 2.0)
    m.apply_translation([-(lo[0] + hi[0]) / 2.0, -lo[1], -zc])
    return m


def scale_depth(mesh: trimesh.Trimesh, factor: float) -> trimesh.Trimesh:
    """Copy of a prepared mesh with its depth (z) multiplied by `factor`, about the spine plane."""
    if abs(factor - 1.0) < 1e-9:
        return mesh
    m = mesh.copy()
    m.apply_transform(np.diag([1.0, 1.0, float(factor), 1.0]))
    return m


def orient_uploaded_model(mesh: trimesh.Trimesh, extension: str) -> np.ndarray:
    """Rotation that brings an uploaded model into the canonical frame.

    glTF/GLB and OBJ are Y-up by convention, STL and PLY are usually Z-up (CAD/3D-printing).
    After making Y the up axis, the model is turned about Y so that its longest horizontal
    direction runs along X (a side view of an animal or object).
    """
    if extension in ("stl", "ply"):
        up = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])  # z-up -> y-up
    else:
        up = np.eye(3)
    v = mesh.vertices @ up.T
    xz = v[:, [0, 2]] - v[:, [0, 2]].mean(axis=0)
    _, vecs = np.linalg.eigh(xz.T @ xz)
    major = vecs[:, -1]  # (x, z) direction of the largest horizontal spread
    ang = np.arctan2(major[1], major[0])
    c, s_ = np.cos(ang), np.sin(ang)
    turn = np.array([[c, 0.0, s_], [0.0, 1.0, 0.0], [-s_, 0.0, c]])  # turn about y: major axis -> +x
    return turn @ up


AXIS_ROTATIONS: list[np.ndarray] = []
for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
    for signs in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
        R = np.zeros((3, 3))
        for row, (col, sgn) in enumerate(zip(perm, signs)):
            R[row, col] = sgn
        if np.isclose(np.linalg.det(R), 1.0):
            AXIS_ROTATIONS.append(R)


def align_to_silhouette(mesh: trimesh.Trimesh, mask: np.ndarray) -> tuple[np.ndarray, float]:
    """Find the axis-aligned rotation whose front projection best matches the image mask.

    Reconstruction models use their own axis conventions; the photo tells us what the
    object looks like from the camera, so the rotation with the highest silhouette IoU
    puts the camera view on the xy plane (x right, y up, z towards the viewer).
    """
    target = _normalised_mask(mask)
    best, best_iou = AXIS_ROTATIONS[0], -1.0
    v = mesh.vertices
    f = mesh.faces
    for R in AXIS_ROTATIONS:
        p = v @ R.T
        proj = _project_mask(p, f, target.shape)
        inter = np.logical_and(proj, target).sum()
        union = np.logical_or(proj, target).sum()
        iou = inter / union if union else 0.0
        if iou > best_iou:
            best, best_iou = R, float(iou)
    return best, best_iou


def _normalised_mask(mask: np.ndarray, size: int = 128) -> np.ndarray:
    ys, xs = np.nonzero(mask > 0)
    crop = (mask[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1] > 0).astype(np.uint8)
    h, w = crop.shape
    s = (size - 4) / max(h, w)
    small = cv2.resize(crop, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_NEAREST)
    out = np.zeros((size, size), np.uint8)
    oy, ox = (size - small.shape[0]) // 2, (size - small.shape[1]) // 2
    out[oy: oy + small.shape[0], ox: ox + small.shape[1]] = small
    return out > 0


def _project_mask(points: np.ndarray, faces: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    size = shape[0]
    xy = points[:, :2]
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    s = (size - 4) / max(hi - lo)
    px = (xy - lo) * s
    px[:, 1] = (hi[1] - lo[1]) * s - px[:, 1]  # image rows grow downwards
    off = ((size - (hi - lo) * s) / 2.0)
    px += off
    img = np.zeros(shape, np.uint8)
    tris = np.round(px[faces] * 4).astype(np.int32)
    cv2.fillPoly(img, list(tris), 1, lineType=cv2.LINE_8, shift=2)
    return img > 0


def silhouette_mask_png(volume: MeshVolume) -> np.ndarray:
    """White-on-black image of the model's projection (shown to the user like a photo mask)."""
    poly = volume.silhouette
    minx, miny, maxx, maxy = poly.bounds
    scale = 900.0 / max(maxx - minx, maxy - miny)
    w, h = int((maxx - minx) * scale) + 40, int((maxy - miny) * scale) + 40
    img = np.zeros((h, w), np.uint8)
    ext = np.round(np.column_stack([(np.asarray(poly.exterior.coords)[:, 0] - minx) * scale + 20,
                                    h - ((np.asarray(poly.exterior.coords)[:, 1] - miny) * scale + 20)]) * 16)
    cv2.fillPoly(img, [ext.astype(np.int32)], 255, lineType=cv2.LINE_AA, shift=4)
    return img


__all__ = [
    "DepthVolume", "MeshVolume", "Volume", "align_to_silhouette", "load_mesh_bytes", "orient_uploaded_model",
    "occupancy", "plane_section", "prepare_mesh", "remesh", "silhouette_mask_png",
]
