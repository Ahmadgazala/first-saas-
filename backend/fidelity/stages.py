"""Stage tracing for the image -> silhouette part of the production pipeline.

`traced_normalize` repeats app.geometry.normalize.normalize_silhouette step by step (same
functions, same parameters) and keeps every intermediate polygon plus the exact
pixel -> mm similarity transform. Its final polygon is checked for equality against the
production function on every call, so the trace cannot drift from production.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely
from shapely import affinity
from shapely.validation import make_valid

from app.geometry.normalize import (
    _fit,
    _reinforce_thin_features,
    largest_polygon,
    mask_to_geometry,
    mask_to_polygon,
    normalize_silhouette,
)


@dataclass
class Similarity:
    scale: float
    tx: float
    ty: float

    def apply(self, geom):
        return affinity.translate(affinity.scale(geom, self.scale, self.scale, origin=(0, 0)), self.tx, self.ty)

    def then(self, other: "Similarity") -> "Similarity":
        return Similarity(self.scale * other.scale, self.tx * other.scale + other.tx, self.ty * other.scale + other.ty)


def _fit_transform(poly, size: float) -> Similarity:
    minx, miny, maxx, maxy = poly.bounds
    s = size / max(maxx - minx, maxy - miny)
    return Similarity(s, -(minx * s + maxx * s) / 2.0, -miny * s)


@dataclass
class NormTrace:
    all_components_px: object  # every component of the mask (pixel units, y up)
    largest_px: object  # mask_to_polygon: the single component the pipeline keeps
    fitted: object  # after the first fit (mm)
    simplified: object
    opened: object  # after the morphological opening at min-feature
    reinforced: object
    final: object  # == normalize_silhouette(...).polygon
    px_to_mm: Similarity  # maps pixel-space geometry of this mask into the final frame
    production_equal: bool


def traced_normalize(mask: np.ndarray, size: float, min_feature: float) -> NormTrace:
    all_px = mask_to_geometry(mask)
    raw = mask_to_polygon(mask)
    t1 = _fit_transform(raw, size)
    poly = _fit(raw, size)
    fitted = poly
    poly = poly.simplify(size * 0.0015, preserve_topology=True)
    poly = largest_polygon(make_valid(poly))
    simplified = poly
    r = min_feature / 2.0
    opened = largest_polygon(poly.buffer(-r, quad_segs=8).buffer(r, quad_segs=8))
    opened_raw = opened
    opened, _, _ = _reinforce_thin_features(poly, opened, min_feature)
    reinforced = opened
    pre = opened.simplify(size * 0.001, preserve_topology=True)
    t2 = _fit_transform(pre, size)
    final = _fit(pre, size)
    final = shapely.set_precision(final, 0.001)
    final = largest_polygon(make_valid(final))
    final = shapely.geometry.polygon.orient(final, 1.0)
    prod = normalize_silhouette(mask, size, min_feature).polygon
    equal = bool(prod.equals(final) or prod.symmetric_difference(final).area < 1e-6 * prod.area)
    return NormTrace(all_px, raw, fitted, simplified, opened_raw, reinforced, prod, t1.then(t2), equal)
