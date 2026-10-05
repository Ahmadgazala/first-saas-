"""Silhouette mask -> clean, normalized vector polygon in millimetres."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid

from app.core.errors import ErrorCode, PuzzleError


@dataclass
class NormalizedSilhouette:
    polygon: Polygon  # y-up, centred on x = 0, resting on y = 0, longest side = target size
    width_mm: float
    height_mm: float
    removed_area_fraction: float
    warnings: list[str] = field(default_factory=list)
    reinforced_features: int = 0


def largest_polygon(geom) -> Polygon | None:
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon) and not g.is_empty]
    if not polys:
        nested = [largest_polygon(g) for g in getattr(geom, "geoms", [])]
        polys = [p for p in nested if p is not None]
    return max(polys, key=lambda p: p.area) if polys else None


def polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [p for g in getattr(geom, "geoms", []) for p in polygon_parts(g)]


def mask_to_geometry(mask: np.ndarray):
    """All connected parts of a binary mask (WHITE = object) as a valid (Multi)Polygon in
    pixel units with y pointing up, or None when the mask is empty."""
    binary = (mask > 0).astype(np.uint8)
    h = binary.shape[0]
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        return None
    hierarchy = hierarchy[0]
    polys: list[Polygon] = []
    for i, contour in enumerate(contours):
        if hierarchy[i][3] != -1 or len(contour) < 3:
            continue
        shell = [(float(x) + 0.5, h - float(y) - 0.5) for x, y in contour.reshape(-1, 2)]
        holes = []
        child = hierarchy[i][2]
        while child != -1:
            hc = contours[child]
            if len(hc) >= 3:
                holes.append([(float(x) + 0.5, h - float(y) - 0.5) for x, y in hc.reshape(-1, 2)])
            child = hierarchy[child][0]
        polys.append(Polygon(shell, holes))
    if not polys:
        return None
    return make_valid(MultiPolygon(polys)) if len(polys) > 1 else make_valid(polys[0])


def mask_to_polygon(mask: np.ndarray) -> Polygon:
    """Convert a binary mask (WHITE = object) into a polygon in pixel units with y pointing up."""
    binary = (mask > 0).astype(np.uint8)
    h = binary.shape[0]
    contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours or hierarchy is None:
        raise PuzzleError(ErrorCode.OBJECT_NOT_DETECTED, "Unable to detect the object.", "no contours")
    hierarchy = hierarchy[0]
    polys: list[Polygon] = []
    for i, contour in enumerate(contours):
        if hierarchy[i][3] != -1 or len(contour) < 3:
            continue  # holes are attached to their parent below
        shell = [(float(x) + 0.5, h - float(y) - 0.5) for x, y in contour.reshape(-1, 2)]
        holes = []
        child = hierarchy[i][2]
        while child != -1:
            hc = contours[child]
            if len(hc) >= 3:
                holes.append([(float(x) + 0.5, h - float(y) - 0.5) for x, y in hc.reshape(-1, 2)])
            child = hierarchy[child][0]
        if len(shell) >= 3:
            polys.append(Polygon(shell, holes))
    poly = largest_polygon(make_valid(MultiPolygon(polys)) if len(polys) > 1 else make_valid(polys[0]) if polys else None)
    if poly is None or poly.area < 25:
        raise PuzzleError(ErrorCode.OBJECT_NOT_DETECTED, "Unable to detect the object.", "empty polygon")
    return poly


def _fit(poly: Polygon, target_size_mm: float) -> Polygon:
    minx, miny, maxx, maxy = poly.bounds
    scale = target_size_mm / max(maxx - minx, maxy - miny)
    poly = affinity.scale(poly, scale, scale, origin=(0, 0))
    minx, miny, maxx, maxy = poly.bounds
    return affinity.translate(poly, -(minx + maxx) / 2.0, -miny)


def normalize_silhouette(mask: np.ndarray, target_size_mm: float, min_feature_mm: float) -> NormalizedSilhouette:
    if target_size_mm <= 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "Puzzle size must be positive.")
    raw = mask_to_polygon(mask)
    poly = _fit(raw, target_size_mm)
    # Contour smoothing/simplification that preserves features larger than ~0.15% of the size.
    poly = poly.simplify(target_size_mm * 0.0015, preserve_topology=True)
    poly = largest_polygon(make_valid(poly))
    base_area = poly.area

    # Remove features thinner than the minimum manufacturable width (morphological opening).
    r = min_feature_mm / 2.0
    opened = largest_polygon(poly.buffer(-r, quad_segs=8).buffer(r, quad_segs=8))
    if opened is None or opened.area < 0.2 * base_area:
        raise PuzzleError(
            ErrorCode.INVALID_GEOMETRY,
            "The object is too thin to be cut at this size. Increase the puzzle size or use a bolder image.",
            f"opened area {0 if opened is None else opened.area:.1f} of {base_area:.1f}",
        )
    opened, reinforced, dropped = _reinforce_thin_features(poly, opened, min_feature_mm)
    removed = max(0.0, 1.0 - opened.area / base_area)
    poly = _fit(opened.simplify(target_size_mm * 0.001, preserve_topology=True), target_size_mm)
    poly = shapely.set_precision(poly, 0.001)
    poly = largest_polygon(make_valid(poly))
    if poly is None or not poly.is_valid:
        raise PuzzleError(ErrorCode.INVALID_GEOMETRY, "Generated geometry is invalid.", "normalized polygon invalid")
    poly = shapely.geometry.polygon.orient(poly, 1.0)

    warnings = []
    if reinforced:
        warnings.append(
            f"{reinforced} thin feature(s) (tails, antennae, thin legs) were reinforced to the minimum cuttable "
            f"width of {min_feature_mm:.1f} mm so they do not break."
        )
    if dropped and removed > 0.02:
        warnings.append(
            f"{removed * 100:.0f}% of the silhouette (hairline details thinner than {0.35 * min_feature_mm:.1f} mm "
            "or very short spikes) was removed. Increase the puzzle size to keep more detail."
        )
    minx, miny, maxx, maxy = poly.bounds
    return NormalizedSilhouette(poly, maxx - minx, maxy - miny, removed, warnings, reinforced)


def _reinforce_thin_features(original: Polygon, opened: Polygon, min_feature_mm: float):
    """Thin parts removed by the opening are either reinforced or dropped.

    A feature that is long enough to matter (>= 3x the minimum width) and not a hairline
    (>= 35% of the minimum width) is rebuilt along its medial axis as a stroke of the
    minimum cuttable width, so tails, antennae, antlers or thin legs keep their shape.
    Shorter or hairline features are dropped. Returns (polygon, reinforced, dropped).
    """
    from skimage.morphology import skeletonize

    from app.geometry.depth import rasterize

    r = min_feature_mm / 2.0
    thin = make_valid(original.difference(opened))
    reinforced, dropped, strokes = 0, 0, []
    for part in polygon_parts(thin):
        if part.area < 0.25 * min_feature_mm**2:
            continue
        width = 2.0 * part.area / max(part.length, 1e-9)
        length = part.length / 2.0
        if length < 3.0 * min_feature_mm or width < 0.35 * min_feature_mm or part.distance(opened) > r:
            dropped += 1
            continue
        res = max(min_feature_mm / 6.0, 0.05)
        raster, x0, y0 = rasterize(part.buffer(0.5 * res), res, pad=2)
        rows, cols = np.nonzero(skeletonize(raster > 0))
        if len(rows) < 3:
            dropped += 1
            continue
        pts = np.stack([x0 + cols * res, y0 + rows * res], axis=1)
        strokes.append(shapely.MultiPoint(pts).buffer(1.1 * r, quad_segs=6))
        reinforced += 1
    if not strokes:
        return opened, 0, dropped
    merged = largest_polygon(make_valid(shapely.union_all([opened] + strokes)))
    if merged is None or merged.area < opened.area:
        return opened, 0, dropped + reinforced
    return merged, reinforced, dropped


def fill_small_holes(poly, max_fraction: float = 0.02):
    """The polygon without interior holes smaller than `max_fraction` of its area (holes the size of an
    eye socket or a nostril are dropped; a handle or a window is kept). Segmentation keeps openings from
    0.1 % of the object, which is finer than every style except the solid slice can use."""
    from shapely.geometry import Polygon as _Polygon

    if getattr(poly, "geom_type", "") != "Polygon" or not poly.interiors:
        return poly
    limit = max_fraction * _Polygon(poly.exterior).area
    keep = [r for r in poly.interiors if _Polygon(r).area > limit]
    return _Polygon(poly.exterior, keep)
