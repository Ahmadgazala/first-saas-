"""Geometry quality analysis: measured manufacturing metrics of a generated puzzle.

All values are measured on the final geometry (not derived from settings):

  min_feature_mm        narrowest part of any piece (largest r whose mitred opening is lossless, x2)
  min_slot_wall_mm      thinnest material left beside any slot, measured perpendicular to the slot
  min_slot_gap_mm       closest approach of two slots on the same piece
  min_cut_distance_mm   smallest distance between the cut paths of two different pieces on the sheet
"""

from __future__ import annotations

import shapely
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

from app.geometry.nesting import Layout
from app.geometry.types import PieceKind, PuzzleModel


def narrowest_feature(poly: Polygon, hi: float = 30.0, tol: float = 0.003) -> float:
    """Approximate narrowest width of a polygon (mm)."""
    area = poly.area
    lo_r, hi_r = 0.05, hi / 2.0
    if _opening_loss(poly, lo_r) > tol:
        return 2.0 * lo_r
    for _ in range(9):
        mid = 0.5 * (lo_r + hi_r)
        if _opening_loss(poly, mid) <= tol:
            lo_r = mid
        else:
            hi_r = mid
    return round(2.0 * lo_r, 2) if area > 0 else 0.0


def _opening_loss(poly: Polygon, r: float) -> float:
    opened = poly.buffer(-r, join_style="mitre").buffer(r, join_style="mitre")
    try:
        kept = opened.intersection(poly).area
    except shapely.errors.GEOSException:
        # Mitred offsets can leave zero-width spikes that defeat the overlay: clean, then snap-round.
        a, b = shapely.make_valid(opened).buffer(0), shapely.make_valid(poly).buffer(0)
        try:
            kept = a.intersection(b).area
        except shapely.errors.GEOSException:
            kept = shapely.intersection(a, b, grid_size=0.001).area
    return 1.0 - kept / poly.area


def slot_walls(poly: Polygon, slot, grip_mm: float, samples: int = 4, reach: float = 60.0) -> list[float]:
    """Material thickness on both sides of a slot, measured perpendicular to the slot at a few
    depths within the gripping zone (the first `grip_mm` from the slot bottom). Walls that
    taper towards the slot mouth are not structural and are not measured."""
    dx, dy = slot.direction
    nx, ny = -dy, dx
    ox, oy = slot.origin
    half = slot.width_mm / 2.0
    walls = []
    span = min(slot.depth_mm, grip_mm)
    for k in range(samples):
        a = span * (k + 0.5) / samples
        qx, qy = ox + dx * a, oy + dy * a
        for sgn in (1.0, -1.0):
            start = (qx + sgn * nx * (half + 1e-3), qy + sgn * ny * (half + 1e-3))
            end = (qx + sgn * nx * (half + reach), qy + sgn * ny * (half + reach))
            hit = poly.intersection(LineString([start, end]))
            parts = [g for g in getattr(hit, "geoms", [hit]) if not g.is_empty and g.length > 0]
            first = next((g for g in parts if g.distance(_pt(start)) < 1e-2), None)
            if first is not None:
                walls.append(first.length)
    return walls


def _pt(xy):
    from shapely.geometry import Point

    return Point(xy)


def analyze_quality(model: PuzzleModel, layout: Layout | None = None) -> dict:
    p = model.params
    pieces = model.pieces
    feature = {pc.id: narrowest_feature(pc.polygon) for pc in pieces}
    walls: list[float] = []
    gaps: list[float] = []
    bearing = {j.id for j in model.joints if j.load_bearing}
    for pc in pieces:
        for slot in pc.slots:
            if slot.joint_id in bearing:
                walls += slot_walls(pc.polygon, slot, p.min_engagement_mm)
        channels = [s.channel() for s in pc.slots]
        for i in range(len(channels)):
            for j in range(i + 1, len(channels)):
                gaps.append(channels[i].distance(channels[j]))
    out = {
        "piece_count": len(pieces),
        "profile_pieces": sum(1 for pc in pieces if pc.kind == PieceKind.PROFILE),
        "cross_pieces": sum(1 for pc in pieces if pc.kind == PieceKind.CROSS),
        "slot_count": sum(len(pc.slots) for pc in pieces),
        "intersection_count": len(model.joints),
        "load_bearing_joints": sum(1 for j in model.joints if j.load_bearing),
        "min_feature_mm": round(min(feature.values()), 2),
        "min_feature_piece": min(feature, key=feature.get),
        "required_min_feature_mm": round(p.min_feature_mm, 2),
        "min_slot_wall_mm": round(min(walls), 2) if walls else None,
        "required_min_wall_mm": round(p.min_feature_mm, 2),
        "min_slot_gap_mm": round(min(gaps), 2) if gaps else None,
        "object_width_mm": model.object_size_mm[0],
        "object_height_mm": model.object_size_mm[1],
        "object_depth_mm": model.object_size_mm[2],
        "material_area_mm2": round(sum(pc.polygon.area for pc in pieces), 1),
        "tilted_ribs": model.analysis.get("tilted_ribs", 0),
    }
    if layout is not None:
        geoms = [pl.polygon for pl in layout.placements]
        tree = STRtree(geoms)
        dmin = float("inf")
        for i, g in enumerate(geoms):
            for j in tree.query(g.buffer(p.part_spacing_mm * 4)):
                if j > i:
                    dmin = min(dmin, g.distance(geoms[j]))
        sheet_area = layout.sheet_width_mm * layout.sheet_height_mm
        band = layout.sheet_width_mm * max(layout.utilized_height_mm, 1e-9)
        out.update({
            "min_cut_distance_mm": round(dmin, 2) if dmin != float("inf") else None,
            "sheet_area_mm2": round(sheet_area, 1),
            "used_area_mm2": layout.used_area_mm2,
            "nesting_efficiency_percent": round(100.0 * layout.used_area_mm2 / band, 2),
            "waste_percentage": layout.waste_percentage,
        })
    return out
