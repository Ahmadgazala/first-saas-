"""Flattened manufacturing geometry for one laser sheet, shared by all file exporters.

Coordinates are millimetres in the sheet frame: origin at the bottom-left corner, y up.
CUT rings are the kerf-compensated tool paths (closed). ENGRAVE paths are open polylines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geometry.nesting import Layout, place_point
from app.geometry.types import PuzzleModel

Point = tuple[float, float]


@dataclass
class SheetPiece:
    id: str
    cut_rings: list[list[Point]]  # exterior first, then holes; each ring closed (first == last)
    engrave: list[list[Point]] = field(default_factory=list)
    label_anchor: Point | None = None


@dataclass
class SheetGeometry:
    width_mm: float
    height_mm: float
    pieces: list[SheetPiece]

    def cut_ring_count(self) -> int:
        return sum(len(p.cut_rings) for p in self.pieces)

    def engrave_path_count(self) -> int:
        return sum(len(p.engrave) for p in self.pieces)


def build_sheet(model: PuzzleModel, layout: Layout, frame=None) -> SheetGeometry:
    """`frame` (optional KitFrame) is added as one more cut contour with its engraved name;
    its tool path is kerf-compensated like every other contour."""
    by_id = {pc.id: pc for pc in model.pieces}
    pieces: list[SheetPiece] = []
    for pl in sorted(layout.placements, key=lambda p: p.piece_id):
        poly = pl.polygon
        rings = [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]
        rings = [[(float(x), float(y)) for x, y in ring] for ring in rings]
        engrave = [
            [place_point(x, y, pl.rotation_deg, pl.dx, pl.dy) for x, y in stroke]
            for stroke in by_id[pl.piece_id].engrave
        ]
        c = poly.representative_point()
        pieces.append(SheetPiece(pl.piece_id, rings, engrave, (c.x, c.y)))
    if frame is not None:
        from app.geometry.slots import compensate_kerf

        poly = compensate_kerf(frame.polygon, model.params.kerf_mm)
        rings = [list(poly.exterior.coords)] + [list(r.coords) for r in poly.interiors]
        rings = [[(float(x), float(y)) for x, y in ring] for ring in rings]
        pieces.append(SheetPiece("FRAME", rings, [list(st) for st in frame.engrave], None))
    return SheetGeometry(layout.sheet_width_mm, layout.sheet_height_mm, pieces)
