"""Deterministic 2D nesting of piece tool paths onto a single sheet.

Algorithm (raster bottom-left fill, exact verification):
  1. Pieces are sorted by area (largest first, ties by ID).
  2. The usable sheet area (sheet minus margin) is rasterized into an occupancy grid.
     Placed pieces are written into the grid dilated by the part spacing.
  3. For every allowed rotation (0/90/180/270°) the piece is rasterized conservatively
     (buffered by one grid cell) and correlated against the occupancy grid
     (cv2.matchTemplate). Zero correlation = collision-free position.
  4. The position nearest the top-left corner wins (highest bottom edge, then lowest x),
     matching the top-left origin convention of most laser software.
  5. Every placement is verified exactly with Shapely (spacing and sheet bounds); a
     violation rejects that position and the next best is tried.

The sheet frame is y-up with the origin at the bottom-left corner of the sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, box

from app.core.errors import ErrorCode, PuzzleError

ROTATIONS = (0, 90, 180, 270)
MAX_GRID_CELLS = 1_200_000
NO_FIT_MESSAGE = (
    "These pieces do not fit on the selected sheet. Increase the sheet size or reduce the puzzle size."
)


@dataclass
class Placement:
    piece_id: str
    rotation_deg: int
    dx: float  # translation applied after rotation about the origin
    dy: float
    polygon: Polygon  # placed tool path in sheet coordinates


@dataclass
class Layout:
    sheet_width_mm: float
    sheet_height_mm: float
    placements: list[Placement]
    used_area_mm2: float
    waste_percentage: float
    utilized_height_mm: float  # height of the occupied band measured from the top edge

    def transform_point(self, piece_id: str, x: float, y: float) -> tuple[float, float]:
        pl = next(p for p in self.placements if p.piece_id == piece_id)
        return place_point(x, y, pl.rotation_deg, pl.dx, pl.dy)


def place_point(x: float, y: float, rotation_deg: int, dx: float, dy: float) -> tuple[float, float]:
    r = np.deg2rad(rotation_deg)
    c, s = round(float(np.cos(r)), 12), round(float(np.sin(r)), 12)
    return (c * x - s * y + dx, s * x + c * y + dy)


def place_polygon(poly: Polygon, rotation_deg: int, dx: float, dy: float) -> Polygon:
    rotated = affinity.rotate(poly, rotation_deg, origin=(0.0, 0.0)) if rotation_deg else poly
    return affinity.translate(rotated, dx, dy)


def _raster(poly: Polygon, res: float, shape: tuple[int, int], ox: float, oy: float) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)

    def px(coords):
        pts = np.asarray(coords, dtype=np.float64)
        return np.round(np.stack([(pts[:, 0] - ox) / res, (pts[:, 1] - oy) / res], axis=1) * 16).astype(np.int32)

    cv2.fillPoly(mask, [px(poly.exterior.coords)], 1, lineType=cv2.LINE_8, shift=4)
    for hole in poly.interiors:
        cv2.fillPoly(mask, [px(hole.coords)], 0, lineType=cv2.LINE_8, shift=4)
    return mask


def nest(
    toolpaths: dict[str, Polygon],
    sheet_w: float,
    sheet_h: float,
    margin: float,
    spacing: float,
) -> Layout:
    usable_w, usable_h = sheet_w - 2 * margin, sheet_h - 2 * margin
    if usable_w <= 0 or usable_h <= 0:
        raise PuzzleError(ErrorCode.INVALID_SETTINGS, "The sheet is smaller than its margins.")
    total_area = sum(p.area for p in toolpaths.values())
    if total_area > usable_w * usable_h:
        raise PuzzleError(ErrorCode.PIECES_DO_NOT_FIT, NO_FIT_MESSAGE, "total area exceeds sheet")

    res = max(0.25, float(np.sqrt(usable_w * usable_h / MAX_GRID_CELLS)))
    gw, gh = int(np.floor(usable_w / res)), int(np.floor(usable_h / res))
    occupancy = np.zeros((gh, gw), dtype=np.float32)
    usable = box(margin, margin, sheet_w - margin, sheet_h - margin)

    # Large outlines first (by bounding box: open arches need their whole span), then by area.
    def footprint(k: str) -> float:
        x0, y0, x1, y1 = toolpaths[k].bounds
        return max(toolpaths[k].area, 0.5 * (x1 - x0) * (y1 - y0))

    order = sorted(toolpaths, key=lambda k: (-round(footprint(k), 3), k))
    placements: list[Placement] = []
    for pid in order:
        placement = _place_one(pid, toolpaths[pid], occupancy, res, margin, spacing, usable, placements)
        if placement is None:
            raise PuzzleError(ErrorCode.PIECES_DO_NOT_FIT, NO_FIT_MESSAGE, f"piece {pid} could not be placed")
        placements.append(placement)
        grown = placement.polygon.buffer(spacing + res, quad_segs=4)
        occupancy = np.maximum(occupancy, _raster(grown, res, occupancy.shape, margin, margin).astype(np.float32))

    used = sum(pl.polygon.area for pl in placements)
    lowest = min(pl.polygon.bounds[1] for pl in placements) if placements else sheet_h
    waste = 100.0 * (1.0 - used / (sheet_w * sheet_h))
    return Layout(sheet_w, sheet_h, placements, round(used, 2), round(waste, 2), round(sheet_h - lowest, 2))


def _place_one(pid, poly, occupancy, res, margin, spacing, usable, placed) -> Placement | None:
    """Top-left-first placement; a broader search over every free row is the fallback when the
    first candidates fail the exact (vector) checks."""
    for rows_n, cols_n, limit in ((8, 8, 200), (400, 40, 4000)):
        found = _search(poly, occupancy, res, margin, spacing, usable, placed, rows_n, cols_n, limit)
        if found is not None:
            return Placement(pid, *found)
    return None


def _search(poly, occupancy, res, margin, spacing, usable, placed, rows_n, cols_n, limit):
    gh, gw = occupancy.shape
    options = []
    for rot in ROTATIONS:
        rp = affinity.rotate(poly, rot, origin=(0.0, 0.0)) if rot else poly
        minx, miny, maxx, maxy = rp.bounds
        # Template covers the piece buffered by one cell (conservative rasterization).
        tw = int(np.ceil((maxx - minx) / res)) + 3
        th = int(np.ceil((maxy - miny) / res)) + 3
        if tw > gw or th > gh:
            continue
        tmpl = _raster(rp.buffer(res, quad_segs=4), res, (th, tw), minx - res, miny - res).astype(np.float32)
        corr = cv2.matchTemplate(occupancy, tmpl, cv2.TM_CCORR)
        free = corr < 0.5  # collision-free template origins
        rows = np.flatnonzero(free.any(axis=1))[::-1][:rows_n]
        if rows.size == 0:
            continue
        # Highest rows (closest to the top edge) first, leftmost columns within each row.
        best = [(int(r), int(c)) for r in rows for c in np.flatnonzero(free[r])[:cols_n]]
        options.append((rot, minx, miny, th, best))
    candidates = []
    for rot, minx, miny, th, best in options:
        for row, col in best:
            candidates.append((-row, col, rot, minx, miny, row))
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    for _, col, rot, minx, miny, row in candidates[:limit]:
        dx = round(margin + (col + 1) * res - minx, 6)
        dy = round(margin + (row + 1) * res - miny, 6)
        placed_poly = place_polygon(poly, rot, dx, dy)
        if not usable.contains(placed_poly):
            continue
        if any(placed_poly.distance(other.polygon) < spacing - 1e-6 for other in placed):
            continue
        return rot, dx, dy, placed_poly
    return None
