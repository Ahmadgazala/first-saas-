"""FrameGenerator: optional kit frame around the nested pieces (like a commercial model-kit
sheet). The frame is its own closed cut contour (a band with the pieces' area as a hole),
never touching any piece: pieces are nested inside the frame's inner edge with the normal
part spacing. The kit name is engraved in the bottom band."""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon, box

from app.geometry.labels import GLYPHS, text_size, text_strokes

Polyline = list[tuple[float, float]]


@dataclass
class KitFrame:
    polygon: Polygon  # nominal frame outline (band with one hole), sheet coordinates
    engrave: list[Polyline]
    inner_margin: float  # nesting margin that keeps every piece inside the frame window


class FrameGenerator:
    def __init__(self, thickness_mm: float, part_spacing_mm: float, sheet_margin_mm: float, kerf_mm: float = 0.0):
        self.band = max(8.0, 2.5 * thickness_mm)
        # Tool paths are offset by kerf/2 outwards from the material: keep the full part spacing.
        self.spacing = part_spacing_mm + kerf_mm / 2.0 + 0.01
        self.margin = sheet_margin_mm

    def make(self, sheet_w: float, sheet_h: float, title: str) -> KitFrame | None:
        m, b = self.margin, self.band
        if sheet_w - 2 * (m + b) < 40 or sheet_h - 2 * (m + b) < 40:
            return None
        outer = box(m, m, sheet_w - m, sheet_h - m)
        inner = box(m + b, m + b, sheet_w - m - b, sheet_h - m - b)
        frame = Polygon(outer.exterior.coords, [inner.exterior.coords[::-1]])
        text = "".join(ch for ch in title.upper() if ch in GLYPHS).strip()[:40]
        strokes: list[Polyline] = []
        if text:
            h = 0.5 * b
            w, _ = text_size(text, h)
            avail = sheet_w - 2 * (m + b) - 10
            if w > avail:
                h *= avail / w
                w = avail
            x0 = m + b + 5
            y0 = m + (b - h) / 2
            strokes = [[(x0 + x, y0 + y) for x, y in s] for s in text_strokes(text, h)]
        return KitFrame(frame, strokes, m + b + self.spacing)

    def around(self, used_bounds, sheet_w: float, sheet_h: float, title: str) -> KitFrame | None:
        """Frame hugging the nested pieces (their bounds), inside the sheet margins."""
        m, b, sp = self.margin, self.band, self.spacing
        x0, y0, x1, y1 = used_bounds
        ix0, iy0, ix1, iy1 = x0 - sp, y0 - sp, x1 + sp, y1 + sp
        ox0, oy0 = max(m, ix0 - b), max(m, iy0 - b)
        ox1, oy1 = min(sheet_w - m, ix1 + b), min(sheet_h - m, iy1 + b)
        if ix0 - ox0 < b - 1e-6 or iy0 - oy0 < b - 1e-6 or ox1 - ix1 < b - 1e-6 or oy1 - iy1 < b - 1e-6:
            return None
        frame = Polygon(box(ox0, oy0, ox1, oy1).exterior.coords, [box(ix0, iy0, ix1, iy1).exterior.coords[::-1]])
        text = "".join(ch for ch in title.upper() if ch in GLYPHS).strip()[:40]
        strokes: list[Polyline] = []
        if text:
            h = 0.5 * (iy0 - oy0)
            w, _ = text_size(text, h)
            avail = (ix1 - ix0) - 10
            if w > avail:
                h *= avail / w
            x = ix0 + 5
            y = oy0 + ((iy0 - oy0) - h) / 2
            strokes = [[(x + px, y + py) for px, py in s] for s in text_strokes(text, h)]
        return KitFrame(frame, strokes, m + b + sp)
