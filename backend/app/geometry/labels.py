"""Piece labels as single-stroke engraving polylines (never cut geometry).

A minimal stroke font (cap height 1, glyph width 0.6) covers the characters used in piece
IDs. Labels are placed at the pole of inaccessibility of the piece region that stays
visible after assembly (strips where other ribs cross are excluded) and scaled to fit.
"""

from __future__ import annotations

import math

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import polylabel, unary_union

from app.geometry.normalize import polygon_parts

Polyline = list[tuple[float, float]]

GLYPHS: dict[str, list[Polyline]] = {
    "0": [[(0.1, 0), (0.5, 0), (0.6, 0.1), (0.6, 0.9), (0.5, 1), (0.1, 1), (0, 0.9), (0, 0.1), (0.1, 0)]],
    "1": [[(0.12, 0.8), (0.32, 1), (0.32, 0)], [(0.1, 0), (0.55, 0)]],
    "2": [[(0, 0.85), (0.1, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.62), (0, 0), (0.6, 0)]],
    "3": [
        [(0, 0.9), (0.1, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.6), (0.5, 0.5), (0.2, 0.5)],
        [(0.5, 0.5), (0.6, 0.4), (0.6, 0.1), (0.5, 0), (0.1, 0), (0, 0.1)],
    ],
    "4": [[(0.45, 0), (0.45, 1), (0, 0.3), (0.6, 0.3)]],
    "5": [[(0.6, 1), (0, 1), (0, 0.55), (0.45, 0.55), (0.6, 0.45), (0.6, 0.1), (0.5, 0), (0.1, 0), (0, 0.1)]],
    "6": [[(0.55, 1), (0.2, 1), (0, 0.8), (0, 0.1), (0.1, 0), (0.5, 0), (0.6, 0.1), (0.6, 0.45), (0.5, 0.55), (0, 0.55)]],
    "7": [[(0, 1), (0.6, 1), (0.2, 0)]],
    "8": [
        [(0.1, 0.5), (0, 0.6), (0, 0.9), (0.1, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.6), (0.5, 0.5), (0.1, 0.5)],
        [(0.1, 0.5), (0, 0.4), (0, 0.1), (0.1, 0), (0.5, 0), (0.6, 0.1), (0.6, 0.4), (0.5, 0.5)],
    ],
    "9": [[(0.6, 0.45), (0.1, 0.45), (0, 0.55), (0, 0.9), (0.1, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.2), (0.4, 0), (0.05, 0)]],
    "P": [[(0, 0), (0, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.6), (0.5, 0.5), (0, 0.5)]],
    "A": [[(0, 0), (0.3, 1), (0.6, 0)], [(0.12, 0.4), (0.48, 0.4)]],
    "B": [[(0, 0), (0, 1), (0.45, 1), (0.58, 0.88), (0.58, 0.62), (0.45, 0.52), (0, 0.52)],
          [(0.45, 0.52), (0.6, 0.4), (0.6, 0.12), (0.48, 0), (0, 0)]],
    "C": [[(0.6, 0.85), (0.45, 1), (0.15, 1), (0, 0.85), (0, 0.15), (0.15, 0), (0.45, 0), (0.6, 0.15)]],
    "D": [[(0, 0), (0, 1), (0.35, 1), (0.6, 0.75), (0.6, 0.25), (0.35, 0), (0, 0)]],
    "E": [[(0.6, 1), (0, 1), (0, 0), (0.6, 0)], [(0, 0.5), (0.45, 0.5)]],
    "G": [[(0.6, 0.85), (0.45, 1), (0.15, 1), (0, 0.85), (0, 0.15), (0.15, 0), (0.45, 0), (0.6, 0.15),
           (0.6, 0.45), (0.35, 0.45)]],
    "H": [[(0, 0), (0, 1)], [(0.6, 0), (0.6, 1)], [(0, 0.5), (0.6, 0.5)]],
    "I": [[(0.3, 0), (0.3, 1)], [(0.12, 0), (0.48, 0)], [(0.12, 1), (0.48, 1)]],
    "K": [[(0, 0), (0, 1)], [(0.6, 1), (0, 0.4)], [(0.18, 0.58), (0.6, 0)]],
    "L": [[(0, 1), (0, 0), (0.55, 0)]],
    "N": [[(0, 0), (0, 1), (0.6, 0), (0.6, 1)]],
    "O": [[(0.15, 0), (0.45, 0), (0.6, 0.15), (0.6, 0.85), (0.45, 1), (0.15, 1), (0, 0.85), (0, 0.15), (0.15, 0)]],
    "R": [[(0, 0), (0, 1), (0.5, 1), (0.6, 0.9), (0.6, 0.6), (0.5, 0.5), (0, 0.5)], [(0.3, 0.5), (0.6, 0)]],
    "S": [[(0.6, 0.85), (0.45, 1), (0.15, 1), (0, 0.85), (0, 0.62), (0.15, 0.5), (0.45, 0.5), (0.6, 0.38),
           (0.6, 0.15), (0.45, 0), (0.15, 0), (0, 0.15)]],
    "T": [[(0, 1), (0.6, 1)], [(0.3, 1), (0.3, 0)]],
    "X": [[(0, 0), (0.6, 1)], [(0, 1), (0.6, 0)]],
    "Y": [[(0, 1), (0.3, 0.5), (0.6, 1)], [(0.3, 0.5), (0.3, 0)]],
    "-": [[(0.1, 0.5), (0.5, 0.5)]],
    "F": [[(0.6, 1), (0, 1), (0, 0)], [(0, 0.5), (0.45, 0.5)]],
    "J": [[(0.15, 1), (0.6, 1)], [(0.45, 1), (0.45, 0.15), (0.3, 0), (0.12, 0), (0, 0.15)]],
    "M": [[(0, 0), (0, 1), (0.3, 0.45), (0.6, 1), (0.6, 0)]],
    "Q": [[(0.15, 0), (0.45, 0), (0.6, 0.15), (0.6, 0.85), (0.45, 1), (0.15, 1), (0, 0.85), (0, 0.15), (0.15, 0)],
          [(0.35, 0.25), (0.62, -0.05)]],
    "U": [[(0, 1), (0, 0.15), (0.15, 0), (0.45, 0), (0.6, 0.15), (0.6, 1)]],
    "V": [[(0, 1), (0.3, 0), (0.6, 1)]],
    "W": [[(0, 1), (0.12, 0), (0.3, 0.6), (0.48, 0), (0.6, 1)]],
    "Z": [[(0, 1), (0.6, 1), (0, 0), (0.6, 0)]],
    " ": [],
}
ADVANCE = 0.8
GLYPH_WIDTH = 0.6


def short_code(piece_id: str) -> str:
    """Compact engraving code for small pieces: LONG-01 -> L01, CROSS-07 -> C07,
    DETAIL-HEAD-01 -> DH01."""
    parts = piece_id.split("-")
    if len(parts) < 2:
        return piece_id
    return "".join(p[0] for p in parts[:-1]) + parts[-1]


def text_strokes(text: str, height: float) -> list[Polyline]:
    """Stroke polylines for `text` with the baseline-left corner at the origin."""
    strokes: list[Polyline] = []
    for i, ch in enumerate(text):
        glyph = GLYPHS.get(ch)
        if glyph is None:
            raise ValueError(f"no glyph for {ch!r}")
        for line in glyph:
            strokes.append([((i * ADVANCE + x) * height, y * height) for x, y in line])
    return strokes


def text_size(text: str, height: float) -> tuple[float, float]:
    return ((len(text) - 1) * ADVANCE + GLYPH_WIDTH) * height, height


def place_label(
    piece: Polygon,
    text: str,
    keep_out: list[Polygon],
    margin: float = 1.5,
    min_height: float = 2.5,
    max_height: float = 8.0,
) -> tuple[list[Polyline], float]:
    """Return engraving strokes (local coordinates) and the text height, or ([], 0) if no fit."""
    region = piece.buffer(-margin)
    if keep_out:
        region = region.difference(unary_union(keep_out))
    parts = [p for p in polygon_parts(region) if p.area > 1.0]
    best: tuple[float, list[Polyline]] | None = None
    for part in sorted(parts, key=lambda p: -p.area)[:4]:
        try:
            centre = polylabel(part, tolerance=0.2)
        except Exception:
            centre = part.representative_point()
        for angle in (0.0, 90.0):
            h = _largest_fit(part, text, centre.x, centre.y, angle, min_height, max_height)
            if h and (best is None or h > best[0]):
                best = (h, _strokes_at(text, h, centre.x, centre.y, angle))
    if best is None:
        return [], 0.0
    return best[1], best[0]


def _label_box(text: str, h: float, cx: float, cy: float, angle: float) -> Polygon:
    w, hh = text_size(text, h)
    rect = box(cx - w / 2 - 0.3 * h, cy - hh / 2 - 0.3 * h, cx + w / 2 + 0.3 * h, cy + hh / 2 + 0.3 * h)
    return affinity.rotate(rect, angle, origin=(cx, cy)) if angle else rect


def _largest_fit(part: Polygon, text: str, cx: float, cy: float, angle: float, lo: float, hi: float) -> float:
    if not part.contains(_label_box(text, lo, cx, cy, angle)):
        return 0.0
    if part.contains(_label_box(text, hi, cx, cy, angle)):
        return hi
    for _ in range(12):
        mid = (lo + hi) / 2
        if part.contains(_label_box(text, mid, cx, cy, angle)):
            lo = mid
        else:
            hi = mid
    return math.floor(lo * 10) / 10


def _strokes_at(text: str, h: float, cx: float, cy: float, angle: float) -> list[Polyline]:
    w, hh = text_size(text, h)
    lines = MultiLineString([LineString(s) for s in text_strokes(text, h)])
    lines = affinity.translate(lines, cx - w / 2, cy - hh / 2)
    if angle:
        lines = affinity.rotate(lines, angle, origin=(cx, cy))
    return [[(round(x, 4), round(y, 4)) for x, y in g.coords] for g in lines.geoms]
