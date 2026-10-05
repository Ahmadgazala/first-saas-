"""Depth models: estimate the object's half-thickness D(x, y) from its silhouette.

A single image carries no true depth. The default HeuristicDepthModel "inflates" the
silhouette deterministically: every region is treated as a rounded tube whose thickness
follows its local width (wide bodies become thick, legs and tails taper). The model is
symmetric about z = 0. Any future model (AI depth estimation, user-painted depth, multi-view
reconstruction) only has to implement DepthModel.estimate and return a DepthField.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import cv2
import numpy as np
from shapely.geometry import Polygon



@dataclass
class DepthField:
    """Half-depth raster in mm. Pixel (row, col) centre maps to (x0 + col*res, y0 + row*res)."""

    values: np.ndarray  # float32, rows indexed by increasing y
    x0: float
    y0: float
    res: float

    @property
    def max_half_depth(self) -> float:
        return float(self.values.max())

    def sample(self, x: np.ndarray | float, y: np.ndarray | float) -> np.ndarray:
        """Bilinear sampling; zero outside the raster."""
        xs = (np.asarray(x, dtype=np.float64) - self.x0) / self.res
        ys = (np.asarray(y, dtype=np.float64) - self.y0) / self.res
        h, w = self.values.shape
        x0 = np.floor(xs).astype(int)
        y0 = np.floor(ys).astype(int)
        fx, fy = xs - x0, ys - y0

        def at(r, c):
            valid = (r >= 0) & (r < h) & (c >= 0) & (c < w)
            out = np.zeros(np.broadcast(r, c).shape, dtype=np.float64)
            out[valid] = self.values[r[valid], c[valid]]
            return out

        v = (
            at(y0, x0) * (1 - fx) * (1 - fy)
            + at(y0, x0 + 1) * fx * (1 - fy)
            + at(y0 + 1, x0) * (1 - fx) * fy
            + at(y0 + 1, x0 + 1) * fx * fy
        )
        return v

    def scaled(self, factor: float) -> "DepthField":
        """The same field with every half-depth multiplied by `factor` (user depth control)."""
        return DepthField((self.values * float(factor)).astype(np.float32), self.x0, self.y0, self.res)

    def level_polygon(self, level: float):
        """Region where half-depth >= level, in mm. May have several parts (MultiPolygon)."""
        mask = self.values >= level
        if not mask.any():
            return None
        return raster_to_geometry(mask, self.x0, self.y0, self.res)


def raster_to_geometry(mask: np.ndarray, x0: float, y0: float, res: float):
    """Vectorise a raster whose rows grow with y (pixel (r, c) centre = (x0 + c*res, y0 + r*res))."""
    from shapely import affinity

    from app.geometry.normalize import mask_to_geometry

    img = np.flipud(mask.astype(np.uint8) * 255).copy()  # mask_to_geometry expects image rows (y down)
    geom = mask_to_geometry(img)
    if geom is None:
        return None
    # pixel (col+0.5, h-row-0.5) in image space -> mm (x0 + col*res, y0 + row*res) in raster space
    return affinity.affine_transform(geom, [res, 0, 0, res, x0 - 0.5 * res, y0 - 0.5 * res])


def rasterize(polygon: Polygon, res: float, pad: int = 2) -> tuple[np.ndarray, float, float]:
    """Rasterize a polygon (mm) into a uint8 mask with rows of increasing y."""
    minx, miny, maxx, maxy = polygon.bounds
    x0, y0 = minx - pad * res, miny - pad * res
    w = int(np.ceil((maxx - x0) / res)) + pad + 1
    h = int(np.ceil((maxy - y0) / res)) + pad + 1
    mask = np.zeros((h, w), dtype=np.uint8)

    def ring_px(coords):
        pts = np.asarray(coords, dtype=np.float64)
        return np.round(np.stack([(pts[:, 0] - x0) / res, (pts[:, 1] - y0) / res], axis=1) * 16).astype(np.int32)

    cv2.fillPoly(mask, [ring_px(polygon.exterior.coords)], 255, lineType=cv2.LINE_8, shift=4)
    for hole in polygon.interiors:
        cv2.fillPoly(mask, [ring_px(hole.coords)], 0, lineType=cv2.LINE_8, shift=4)
    return mask, x0, y0


class DepthModel(ABC):
    @abstractmethod
    def estimate(self, silhouette: Polygon, anatomy=None) -> DepthField: ...


def _inflate(mask: np.ndarray, d: np.ndarray, size: float, res: float) -> tuple[np.ndarray, np.ndarray]:
    """Circular-tube inflation: D0 = sqrt(d (2R - d)) with R the local tube radius."""
    window = max(3, int(round(0.12 * size / res)) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (window, window))
    r_local = cv2.dilate(d, kernel)
    dd = np.minimum(d, r_local)
    return np.sqrt(np.clip(dd * (2.0 * r_local - dd), 0.0, None)), r_local


def _finish(depth: np.ndarray, mask: np.ndarray, d: np.ndarray, r_local: np.ndarray, size: float, res: float,
            scale_cap: np.ndarray | float, max_ratio: float) -> np.ndarray:
    sigma = max(1.0, 0.012 * size / res)
    depth = cv2.GaussianBlur(depth.astype(np.float32), (0, 0), sigma)
    depth[mask == 0] = 0.0
    # Exactly zero on the outline and never steeper than a round tube near the edge.
    depth = np.minimum(depth, np.sqrt(np.clip(d * 2.0 * r_local, 0, None)) * scale_cap)
    cap = max_ratio * size / 2.0
    peak = float(depth.max())
    if peak > cap > 0:
        depth *= cap / peak
    return depth.astype(np.float32)


class HeuristicDepthModel(DepthModel):
    """Uniform distance-transform inflation (no anatomy).

    d(p)   distance from p to the silhouette boundary
    R(p)   local tube radius: max of d in a neighbourhood (approximates the medial radius)
    D(p) = k * sqrt(d * (2R - d))  for d <= R  (circular cross-section of a tube of radius R)

    The field is Gaussian-smoothed and capped so the object's total depth never exceeds
    `max_depth_ratio` of its largest silhouette dimension.
    """

    def __init__(self, depth_scale: float = 0.75, max_depth_ratio: float = 0.42, max_raster_side: int = 700):
        self.depth_scale = depth_scale
        self.max_depth_ratio = max_depth_ratio
        self.max_raster_side = max_raster_side

    def estimate(self, silhouette: Polygon, anatomy=None) -> DepthField:
        minx, miny, maxx, maxy = silhouette.bounds
        size = max(maxx - minx, maxy - miny)
        res = max(size / self.max_raster_side, 0.2)
        mask, x0, y0 = rasterize(silhouette, res)
        d = cv2.distanceTransform(mask, cv2.DIST_L2, 5).astype(np.float32) * res
        d0, r_local = _inflate(mask, d, size, res)
        depth = _finish(self.depth_scale * d0, mask, d, r_local, size, res, self.depth_scale, self.max_depth_ratio)
        return DepthField(depth, x0, y0, res)


# Relative thickness of each anatomical region (multiplies the tube inflation).
REGION_DEPTH = {
    "torso": 0.85,
    "chest": 0.97,
    "belly": 0.72,
    "head": 0.82,
    "neck": 0.72,
    "limb": 0.62,
    "tail": 0.6,
    "extremity": 0.45,
}


class AnatomicalDepthModel(DepthModel):
    """Region-aware 2.5D depth field.

    Starts from the tube inflation (local width -> local thickness) and modulates it with a
    smooth per-region factor field:

        chest      fullest volume (ribcage / shoulders)
        torso      large volume
        belly      slightly reduced underneath
        neck       transition between head and torso
        head       skull full, muzzle/snout tapering towards the tip
        limbs      narrow, tapering towards paws/hooves
        tail       progressively tapered from root to tip
        extremity  thin (ears, horns, antlers, trunk, fins)

    The factor field is blended with a normalised Gaussian (no discontinuities between
    regions) and the result is smoothed, kept at zero on the outline and capped.
    """

    def __init__(self, max_depth_ratio: float = 0.42, blend: float = 0.035):
        self.max_depth_ratio = max_depth_ratio
        self.blend = blend

    def estimate(self, silhouette: Polygon, anatomy=None) -> DepthField:
        from app.geometry.anatomy import Region, analyze

        if anatomy is None:
            anatomy = analyze(silhouette)
        minx, miny, maxx, maxy = silhouette.bounds
        size = max(maxx - minx, maxy - miny)
        res = anatomy.res
        mask = anatomy.mask.astype(np.uint8)
        d = anatomy.dist.astype(np.float32)
        d0, r_local = _inflate(mask, d, size, res)

        labels, along = anatomy.labels, anatomy.along
        k = np.zeros(mask.shape, np.float32)
        for region in Region:
            if region == Region.BACKGROUND:
                continue
            sel = labels == region
            if not sel.any():
                continue
            base = REGION_DEPTH[region.label]
            t = along[sel]
            if region == Region.HEAD:  # skull full, muzzle narrower towards the tip
                k[sel] = base * (1.0 - 0.35 * _smoothstep(0.55, 1.0, t))
            elif region == Region.LIMB:  # taper towards the paw / hoof
                k[sel] = base * (1.0 - 0.2 * t)
            elif region == Region.TAIL:  # progressive taper root -> tip
                k[sel] = base * (1.0 - 0.5 * _smoothstep(0.0, 1.0, t))
            elif region == Region.NECK:  # blend from torso volume into the head
                k[sel] = base * (1.08 - 0.12 * t)
            else:
                k[sel] = base
        sigma = max(1.0, self.blend * size / res)
        num = cv2.GaussianBlur(k * mask, (0, 0), sigma)
        den = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), sigma)
        k_smooth = np.where(den > 1e-4, num / np.maximum(den, 1e-4), 0.0).astype(np.float32)
        depth = _finish(k_smooth * d0, mask, d, r_local, size, res, np.maximum(k_smooth, 0.3), self.max_depth_ratio)
        return DepthField(depth, anatomy.x0, anatomy.y0, res)


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)
