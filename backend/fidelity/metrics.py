"""Fidelity metrics (2D silhouettes and 3D voxel volumes).

2D (shapely geometries in mm, same frame):
  iou               exact vector area IoU
  boundary_f        Boundary F-score at tolerance tau (Martin et al. / DAVIS style), computed on a
                    raster of `res` mm: precision = share of the candidate boundary within tau of
                    the reference boundary, recall = share of the reference boundary within tau
                    of the candidate boundary
  boundary_dist     Hausdorff distance, 95th-percentile (HD95) and mean symmetric surface
                    distance (ASSD) between the boundaries (all rings), from points sampled
                    every `step` mm
3D (boolean occupancy grids on one common grid, pitch `res` mm):
  volume_iou        |A n B| / |A u B|
  surface_distances completeness (reference surface -> nearest candidate material) and accuracy
                    (candidate material surface -> nearest reference surface), Chamfer = mean of
                    both means; coverage@d = share of the reference surface within d of material
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from shapely.geometry import MultiPolygon, Polygon
from shapely.validation import make_valid


def parts(geom) -> list[Polygon]:
    """All polygons in a geometry, recursing into Multi* and GeometryCollections."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    out: list[Polygon] = []
    for g in getattr(geom, "geoms", []):
        out.extend(parts(g))
    return out


def as_area(geom):
    """Valid polygonal part of a geometry (drops lines / points)."""
    g = make_valid(geom) if geom is not None else None
    ps = parts(g) if g is not None else []
    if not ps:
        return Polygon()
    return ps[0] if len(ps) == 1 else MultiPolygon(ps)


def iou(a, b) -> float:
    a, b = as_area(a), as_area(b)
    u = a.union(b).area
    return float(a.intersection(b).area / u) if u > 0 else float("nan")


def rasterize(geom, x0: float, y0: float, res: float, shape: tuple[int, int]) -> np.ndarray:
    """Boolean raster (rows = increasing y) of a polygonal geometry, holes respected."""
    img = np.zeros(shape, np.uint8)
    for p in parts(as_area(geom)):
        ext = np.round((np.asarray(p.exterior.coords) - (x0, y0)) / res * 16).astype(np.int32)
        cv2.fillPoly(img, [ext], 1, lineType=cv2.LINE_8, shift=4)
        for h in p.interiors:
            hh = np.round((np.asarray(h.coords) - (x0, y0)) / res * 16).astype(np.int32)
            cv2.fillPoly(img, [hh], 0, lineType=cv2.LINE_8, shift=4)
    return img > 0


def common_grid(geoms, res: float, pad: float = 5.0):
    b = [as_area(g).bounds for g in geoms if not as_area(g).is_empty]
    x0 = min(v[0] for v in b) - pad
    y0 = min(v[1] for v in b) - pad
    x1 = max(v[2] for v in b) + pad
    y1 = max(v[3] for v in b) + pad
    shape = (int(np.ceil((y1 - y0) / res)) + 1, int(np.ceil((x1 - x0) / res)) + 1)
    return x0, y0, shape


def _boundary(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask, structure=np.ones((3, 3), bool), border_value=0)


def boundary_f(reference, candidate, tau: float, res: float = 0.5) -> dict:
    x0, y0, shape = common_grid([reference, candidate], res)
    R = rasterize(reference, x0, y0, res, shape)
    C = rasterize(candidate, x0, y0, res, shape)
    bR, bC = _boundary(R), _boundary(C)
    if not bR.any() or not bC.any():
        return {"precision": float("nan"), "recall": float("nan"), "f": float("nan"), "tau_mm": tau}
    dR = ndimage.distance_transform_edt(~bR) * res
    dC = ndimage.distance_transform_edt(~bC) * res
    precision = float(np.mean(dR[bC] <= tau))
    recall = float(np.mean(dC[bR] <= tau))
    f = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return {"precision": precision, "recall": recall, "f": f, "tau_mm": tau}


def _ring_points(geom, step: float) -> np.ndarray:
    pts = []
    for p in parts(as_area(geom)):
        for ring in [p.exterior, *p.interiors]:
            n = max(4, int(np.ceil(ring.length / step)))
            pts.extend(ring.interpolate(i / n, normalized=True).coords[0] for i in range(n))
    return np.asarray(pts, dtype=float)


def boundary_dist(reference, candidate, step: float = 0.5) -> dict:
    a, b = _ring_points(reference, step), _ring_points(candidate, step)
    if len(a) == 0 or len(b) == 0:
        return {"hausdorff_mm": float("nan"), "hd95_mm": float("nan"), "assd_mm": float("nan")}
    dab = cKDTree(b).query(a)[0]
    dba = cKDTree(a).query(b)[0]
    return {
        "hausdorff_mm": float(max(dab.max(), dba.max())),
        "hd95_mm": float(max(np.percentile(dab, 95), np.percentile(dba, 95))),
        "assd_mm": float(np.mean(np.concatenate([dab, dba]))),
    }


def silhouette_metrics(reference, candidate, size_mm: float, res: float = 0.5) -> dict:
    """All 2D metrics; tau = 2 % of the object size (and 1 % reported as f_tight)."""
    tau = 0.02 * size_mm
    bf = boundary_f(reference, candidate, tau, res)
    bf_tight = boundary_f(reference, candidate, 0.01 * size_mm, res)
    bd = boundary_dist(reference, candidate)
    ref, cand = as_area(reference), as_area(candidate)
    return {
        "iou": iou(ref, cand),
        "bf": bf["f"], "bf_precision": bf["precision"], "bf_recall": bf["recall"], "bf_tau_mm": tau,
        "bf_tight": bf_tight["f"],
        **bd,
        "hausdorff_pct": bd["hausdorff_mm"] / size_mm * 100.0,
        "area_ratio": cand.area / ref.area if ref.area > 0 else float("nan"),
        "recall_area": ref.intersection(cand).area / ref.area if ref.area > 0 else float("nan"),
        "precision_area": ref.intersection(cand).area / cand.area if cand.area > 0 else float("nan"),
    }


def envelope(geom, radius: float):
    """Morphological closing (gap bridging) - the outline a viewer perceives for a sparse
    skeleton of thin pieces. Reported separately and always labelled with its radius."""
    g = as_area(geom)
    if g.is_empty:
        return g
    return as_area(g.buffer(radius, quad_segs=8).buffer(-radius, quad_segs=8))


# ------------------------------------------------------------------------------ 3D


def volume_iou(A: np.ndarray, B: np.ndarray) -> float:
    u = np.logical_or(A, B).sum()
    return float(np.logical_and(A, B).sum() / u) if u else float("nan")


def surface(occ: np.ndarray) -> np.ndarray:
    return occ & ~ndimage.binary_erosion(occ, structure=np.ones((3, 3, 3), bool), border_value=0)


def surface_distances(reference: np.ndarray, candidate: np.ndarray, res: float) -> dict:
    """Reference = ground-truth (or reconstructed) solid, candidate = kit material."""
    if not reference.any() or not candidate.any():
        return {}
    sR = surface(reference)
    sC = surface(candidate)
    d_to_C = ndimage.distance_transform_edt(~candidate) * res  # distance to nearest kit material
    d_to_sR = ndimage.distance_transform_edt(~sR) * res  # distance to the reference surface
    comp = d_to_C[sR]
    acc = d_to_sR[sC]
    return {
        "completeness_mean_mm": float(comp.mean()),
        "completeness_p95_mm": float(np.percentile(comp, 95)),
        "accuracy_mean_mm": float(acc.mean()),
        "accuracy_p95_mm": float(np.percentile(acc, 95)),
        "chamfer_mm": float(0.5 * (comp.mean() + acc.mean())),
        "coverage_3mm": float(np.mean(comp <= 3.0)),
        "coverage_6mm": float(np.mean(comp <= 6.0)),
        "material_outside_ref": float(np.mean(~reference[candidate])),
    }


def projection_iou(A: np.ndarray, B: np.ndarray, axis: int) -> float:
    return volume_iou(A.any(axis=axis), B.any(axis=axis))
